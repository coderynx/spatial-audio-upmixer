"""Objective measurement kit for the stem-mixing path (mixing phase 0).

Skipped by default. Run with:
    uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s

The ``-s`` run prints markdown tables for docs/plans/mixing/phase0_report.md.
Five measurements: send frequency response, downmix fold-down comb / height
loss, LFE energy and crossover phase, per-zone channel energy accounting,
per-stem loudness offset after routing.

Every chain measured here is LTI (no spatial plan, no signal-dependent stage
except ``StemRouter.route``'s scalar renormalization), so transfer functions
come from an impulse response rather than noise or a sweep: same measurement,
exact per bin, no estimator variance.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pytest
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP, ChannelLabel
from upmixer.loudness import CHANNEL_WEIGHT
from upmixer.separation.stem_placement import BALANCED_PLACEMENTS, STEM_ROUTING_PRESET_NAMES
from upmixer.separation.stem_router import StemRouter, build_stem_routing
from upmixer.upmix.multichannel import MultichannelUpmixer
from upmixer.utils import (
    HEIGHT_VELVET_SEED,
    ITU_CENTER_COEFF,
    SURROUND_VELVET_SEED,
    itu_downmix_stereo,
    velvet_send,
)

pytestmark = pytest.mark.perf

_SR = 48000
_N = 2 * _SR
_SEED = 20260817

_STEMS: tuple[str, ...] = tuple(BALANCED_PLACEMENTS)
_LAYOUTS: tuple[str, ...] = ("stereo", "5.1", "7.1.4")

_FRONT = ("FL", "FR", "C")
_SURROUND = ("SL", "SR", "BL", "BR")
_HEIGHT = ("TFL", "TFR", "TBL", "TBR")

_NOTCH_LO_HZ = 300.0
_NOTCH_HI_HZ = 16000.0


def _print_table(title: str, header: tuple[str, ...], rows: list[tuple]) -> None:
    print(f"\n### {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def _impulse() -> np.ndarray:
    signal = np.zeros(_N, dtype=np.float64)
    signal[0] = 1.0
    return signal


def _pink(n: int = _N, seed: int = _SEED) -> np.ndarray:
    """Unit-RMS pink noise by 1/sqrt(f) shaping of white noise."""
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    scale = np.ones(len(spectrum), dtype=np.float64)
    scale[1:] = 1.0 / np.sqrt(np.arange(1, len(spectrum)))
    signal = np.fft.irfft(spectrum * scale, n)
    return signal / float(np.sqrt(np.mean(signal**2)))


def _freqs(n: int = _N) -> np.ndarray:
    return np.fft.rfftfreq(n, 1.0 / _SR)


def _tf_db(impulse_response: np.ndarray) -> np.ndarray:
    magnitude = np.abs(np.fft.rfft(impulse_response))
    return 20.0 * np.log10(np.maximum(magnitude, 1e-12))


def _third_octave_bands(
    lo_hz: float = 25.0, hi_hz: float = 16000.0
) -> list[tuple[float, float, float]]:
    """(center, low_edge, high_edge) per third-octave band."""
    step = 2.0 ** (1.0 / 3.0)
    edge = 2.0 ** (1.0 / 6.0)
    bands = []
    center = lo_hz
    while center <= hi_hz:
        bands.append((center, center / edge, center * edge))
        center *= step
    return bands


def _band_means_db(tf_db: np.ndarray) -> list[tuple[float, float]]:
    """Power-mean level per third-octave band, in dB."""
    freqs = _freqs()
    power = 10.0 ** (tf_db / 10.0)
    out = []
    for center, low, high in _third_octave_bands():
        mask = (freqs >= low) & (freqs < high)
        if mask.any():
            out.append((center, 10.0 * math.log10(float(power[mask].mean()))))
    return out


def _band_energy(signal: np.ndarray, lo_hz: float, hi_hz: float) -> float:
    freqs = _freqs(len(signal))
    spectrum = np.fft.rfft(signal)
    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    return float(np.sum(np.abs(spectrum[mask]) ** 2))


def _dip_count(tf_db: np.ndarray) -> int:
    """Crossings below −10 dB in the search band.

    The metric that separates a comb from a sparse aperiodic filter: a comb
    produces hundreds of evenly spaced dips, velvet a few dozen scattered
    ones. Third-octave bands are wider than a comb's notch spacing and hide
    the difference entirely (phase 0 §1b).
    """
    freqs = _freqs()
    band = tf_db[(freqs >= _NOTCH_LO_HZ) & (freqs <= _NOTCH_HI_HZ)]
    return int(np.sum((band[:-1] >= -10.0) & (band[1:] < -10.0)))


def _worst_notch_db(tf_db: np.ndarray) -> tuple[float, float]:
    """(depth_db, frequency_hz) of the deepest dip in the comb search band."""
    freqs = _freqs()
    mask = (freqs >= _NOTCH_LO_HZ) & (freqs <= _NOTCH_HI_HZ)
    band = tf_db[mask]
    index = int(np.argmin(band))
    return float(band[index]), float(freqs[mask][index])


def _router(layout: str, preset: str) -> StemRouter:
    fmt = FORMAT_MAP[layout]
    return StemRouter(
        UpmixConfig(), fmt, _SR, build_stem_routing(list(_STEMS), fmt, preset)
    )


def _route_stem(
    layout: str, preset: str, stem: str, signal: np.ndarray
) -> dict[str, np.ndarray]:
    stereo = np.stack([signal, signal], axis=1)
    return _router(layout, preset).route({stem: stereo}, len(signal))


@lru_cache(maxsize=None)
def _accounting(layout: str, preset: str) -> dict[str, dict[str, float]]:
    """Per-stem fraction of input energy landing in each zone after routing."""
    signal = _pink()
    input_energy = 2.0 * float(np.dot(signal, signal))
    table: dict[str, dict[str, float]] = {}
    for stem in _STEMS:
        channels = _route_stem(layout, preset, stem, signal)
        zones = {
            "front": _FRONT,
            "surround": _SURROUND,
            "height": _HEIGHT,
            "LFE": ("LFE",),
        }
        table[stem] = {
            zone: sum(
                float(np.dot(channels[name], channels[name]))
                for name in names
                if name in channels
            )
            / input_energy
            for zone, names in zones.items()
        }
    return table


def test_send_frequency_response() -> None:
    """Measurement 1 — surround/height send chains and derived-channel chains."""
    router = _router("7.1.4", "balanced")
    impulse = _impulse()

    chains = {
        "surround L (HP250 → velvet L)": (
            router._surround_send(impulse),
            "left",
            SURROUND_VELVET_SEED,
        ),
        "surround R (HP250 → velvet R)": (
            router._surround_send(impulse),
            "right",
            SURROUND_VELVET_SEED,
        ),
        "height L (elev EQ → velvet L)": (
            router._height_send(impulse),
            "left",
            HEIGHT_VELVET_SEED,
        ),
        "height R (elev EQ → velvet R)": (
            router._height_send(impulse),
            "right",
            HEIGHT_VELVET_SEED,
        ),
    }

    eq_only = {name: _tf_db(eq) for name, (eq, _, _) in chains.items()}
    full = {
        name: _tf_db(velvet_send(eq, _SR, side, seed))
        for name, (eq, side, seed) in chains.items()
    }
    decorrelator = {name: full[name] - eq_only[name] for name in chains}

    names = tuple(chains)
    band_levels = {name: dict(_band_means_db(full[name])) for name in names}
    _print_table(
        "1a. Send chain response, third-octave band level (dB, full chain)",
        ("Band Hz",) + names,
        [
            (f"{center:.0f}",)
            + tuple(f"{band_levels[name][center]:+.2f}" for name in names)
            for center in sorted(band_levels[names[0]])
        ],
    )

    rows = []
    for name, (eq, side, seed) in chains.items():
        depth, freq = _worst_notch_db(decorrelator[name])
        eq_depth, eq_freq = _worst_notch_db(full[name])
        band = [level for _, level in _band_means_db(decorrelator[name])]
        gain = 10.0 * math.log10(
            float(np.dot(velvet_send(eq, _SR, side, seed), velvet_send(eq, _SR, side, seed)))
            / float(np.dot(eq, eq))
        )
        rows.append(
            (
                name,
                f"{depth:.2f} @ {freq:.0f} Hz",
                _dip_count(decorrelator[name]),
                f"{max(band) - min(band):.2f}",
                f"{gain:+.2f}",
                f"{eq_depth:.2f} @ {eq_freq:.0f} Hz",
            )
        )
    _print_table(
        "1b. Decorrelator response, isolated from the send EQ",
        (
            "Chain",
            "worst notch (dB)",
            "dips < −10 dB",
            "band ripple p-p (dB)",
            "broadband gain (dB)",
            "worst notch, full chain",
        ),
        rows,
    )

    upmixer = MultichannelUpmixer(
        UpmixConfig(), INPUT_FORMAT_MAP["stereo"], FORMAT_MAP["7.1.4"], _SR
    )
    derived = upmixer.process(
        {ChannelLabel.FL: impulse.copy(), ChannelLabel.FR: impulse.copy()}
    )
    rows = []
    source_pair = 2.0 * float(np.dot(impulse, impulse))
    front_pair = float(np.dot(derived["FL"], derived["FL"])) + float(
        np.dot(derived["FR"], derived["FR"])
    )
    center = float(np.dot(derived["C"], derived["C"]))
    fold_error = derived["FL"] + ITU_CENTER_COEFF * derived["C"] - impulse
    rows.append(
        (
            "C build-up (FL+FR+C vs input pair)",
            f"{10.0 * math.log10((front_pair + center) / source_pair):+.2f}",
            "-",
            "-",
        )
    )
    rows.append(
        (
            "C fold-down error (FL+0.707C vs input FL)",
            f"{10.0 * math.log10(max(float(np.dot(fold_error, fold_error)), 1e-30) / float(np.dot(impulse, impulse))):+.2f}",
            "-",
            "-",
        )
    )
    for name, signal in derived.items():
        # The derived LFE is the butter lowpass measured in §3; its stopband
        # would dominate the ripple and notch columns with nothing audible.
        if name in ("FL", "FR", "LFE"):
            continue
        tf = _tf_db(signal)
        band = [level for _, level in _band_means_db(tf)]
        depth, freq = _worst_notch_db(tf)
        broadband = 10.0 * math.log10(max(float(np.dot(signal, signal)), 1e-30))
        rows.append(
            (
                name,
                f"{broadband:+.2f}",
                f"{max(band) - min(band):.2f}",
                f"{depth:.2f} @ {freq:.0f} Hz",
            )
        )
    _print_table(
        "1c. MultichannelUpmixer derived channels, stereo (FL=FR=impulse) in",
        ("Channel", "broadband gain (dB)", "band ripple p-p (dB)", "worst notch (dB)"),
        rows,
    )

    pairs = {
        "StemRouter surround (velvet L/R)": (
            velvet_send(router._surround_send(impulse), _SR, "left", SURROUND_VELVET_SEED),
            velvet_send(router._surround_send(impulse), _SR, "right", SURROUND_VELVET_SEED),
        ),
        "StemRouter height (velvet L/R)": (
            velvet_send(router._height_send(impulse), _SR, "left", HEIGHT_VELVET_SEED),
            velvet_send(router._height_send(impulse), _SR, "right", HEIGHT_VELVET_SEED),
        ),
        "MultichannelUpmixer SL+SR": (derived["SL"], derived["SR"]),
        "MultichannelUpmixer BL+BR": (derived["BL"], derived["BR"]),
        "MultichannelUpmixer TFL+TFR": (derived["TFL"], derived["TFR"]),
        "MultichannelUpmixer TBL+TBR": (derived["TBL"], derived["TBR"]),
    }
    fine = (_freqs() >= _NOTCH_LO_HZ) & (_freqs() <= _NOTCH_HI_HZ)
    rows = []
    for name, (left, right) in pairs.items():
        summed = left + right
        loss = 10.0 * math.log10(
            float(np.dot(summed, summed))
            / (float(np.dot(left, left)) + float(np.dot(right, right)))
        )
        tf = _tf_db(summed)
        band = [level for _, level in _band_means_db(tf)]
        depth, freq = _worst_notch_db(tf)
        rows.append(
            (
                name,
                f"{loss:+.2f}",
                f"{depth - max(band):.2f} @ {freq:.0f} Hz",
                f"{float(np.std(tf[fine])):.2f}",
            )
        )
    _print_table(
        "1d. Mono fold-down of each decorrelated pair (L+R)",
        (
            "Pair",
            "sum vs power sum (dB)",
            "worst notch rel. (dB)",
            "per-bin ripple σ (dB)",
        ),
        rows,
    )

    for name in chains:
        dips = _dip_count(decorrelator[name])
        assert dips < 120, f"{name} has {dips} dips, comb-like"
    for name, (left, right) in pairs.items():
        summed = left + right
        loss = 10.0 * math.log10(
            float(np.dot(summed, summed))
            / (float(np.dot(left, left)) + float(np.dot(right, right)))
        )
        assert abs(loss) < 0.5, f"{name} fold-down lost {loss:.2f} dB"
    assert set(_SURROUND + _HEIGHT).issubset(derived)


def _downmix_ratio_db(
    preset: str, stem: str, signal: np.ndarray, height_coeff: float
) -> np.ndarray:
    """Transfer function of the stereo downmix over the direct stereo render."""
    immersive = _route_stem("7.1.4", preset, stem, signal)
    down_l, down_r = itu_downmix_stereo(immersive, height_coeff=height_coeff)
    folded = _route_stem("stereo", preset, stem, signal)
    return _tf_db(down_l + down_r) - _tf_db(folded["FL"] + folded["FR"])


def _height_delivery(
    preset: str, stem: str, signal: np.ndarray, height_coeff: float
) -> float:
    """Fraction of a stem's routed height energy the stereo downmix carries."""
    bed = _route_stem("7.1.4", preset, stem, signal)
    heights = {name: bed[name] for name in _HEIGHT if name in bed}
    in_bed = sum(float(np.dot(x, x)) for x in heights.values())
    if in_bed <= 0.0:
        return 0.0
    left, right = itu_downmix_stereo(heights, height_coeff=height_coeff)
    return (float(np.dot(left, left)) + float(np.dot(right, right))) / in_bed


def _energy_loss_db(height_fraction: float, delivered: float) -> float:
    """Stem energy missing from the downmix, heights delivered at *delivered*."""
    kept = 1.0 - height_fraction * (1.0 - delivered)
    return -10.0 * math.log10(max(kept, 1e-9))


def _level_offset_db(ratio: np.ndarray) -> tuple[float, list[float]]:
    bands = [
        level
        for center, level in _band_means_db(ratio)
        if _NOTCH_LO_HZ <= center <= _NOTCH_HI_HZ
    ]
    return sum(bands) / len(bands), bands


def test_downmix_fold_comb_and_height_loss() -> None:
    """Measurement 2 — BS.775 downmix vs the stereo render of the same stem."""
    signal = _pink()
    freqs = _freqs()
    fine = (freqs >= _NOTCH_LO_HZ) & (freqs <= _NOTCH_HI_HZ)
    height_coeff = UpmixConfig().height_downmix_coeff
    rows = []
    for stem in ("Crowd", "Other", "Crash", "Hi-Hat", "Backing Vocals", "Drums"):
        dropped, _ = _level_offset_db(_downmix_ratio_db("balanced", stem, signal, 0.0))
        ratio = _downmix_ratio_db("balanced", stem, signal, height_coeff)
        offset, bands = _level_offset_db(ratio)
        depth, freq = _worst_notch_db(ratio)
        rows.append(
            (
                stem,
                f"{dropped:+.2f}",
                f"{offset:+.2f}",
                f"{max(bands) - min(bands):.2f}",
                f"{float(np.std(ratio[fine] - offset)):.2f}",
                f"{depth - offset:.2f} @ {freq:.0f} Hz",
            )
        )
    _print_table(
        "2a. Downmix (7.1.4 → BS.775 stereo) vs direct stereo render, balanced",
        (
            "Stem",
            "level offset, heights dropped (dB)",
            "level offset, heights folded (dB)",
            "band ripple p-p (dB)",
            "per-bin ripple σ (dB)",
            "worst notch rel. (dB)",
        ),
        rows,
    )

    rows = []
    for preset in STEM_ROUTING_PRESET_NAMES:
        table = _accounting("7.1.4", preset)
        dropped = {}
        folded = {}
        for stem, zones in table.items():
            dropped[stem] = _energy_loss_db(zones["height"], 0.0)
            folded[stem] = _energy_loss_db(
                zones["height"], _height_delivery(preset, stem, signal, height_coeff)
            )
        worst = max(folded, key=folded.get)
        rows.append(
            (
                preset,
                f"{sum(dropped.values()) / len(dropped):.2f}",
                f"{sum(folded.values()) / len(folded):.2f}",
                f"{worst} {folded[worst]:.2f}",
                f"{max(table[s]['height'] for s in table):.3f}",
            )
        )
        assert sum(folded.values()) < sum(dropped.values()), preset
    _print_table(
        "2b. Stem energy the stereo downmix fails to carry, per preset (7.1.4)",
        (
            "Preset",
            "mean loss, heights dropped (dB)",
            "mean loss, heights folded (dB)",
            "worst stem, folded (dB)",
            "max height fraction",
        ),
        rows,
    )

    per_stem = _accounting("7.1.4", "balanced")
    _print_table(
        "2c. Height fraction and downmix loss per stem, balanced (7.1.4)",
        ("Stem", "height fraction", "loss dropped (dB)", "loss folded (dB)"),
        [
            (
                stem,
                f"{zones['height']:.3f}",
                f"{_energy_loss_db(zones['height'], 0.0):.2f}",
                f"{_energy_loss_db(zones['height'], _height_delivery('balanced', stem, signal, height_coeff)):.2f}",
            )
            for stem, zones in per_stem.items()
        ],
    )

    assert max(zones["height"] for zones in per_stem.values()) > 0.1


def test_lfe_energy_and_crossover_phase() -> None:
    """Measurement 3 — LFE vs mains in-band energy, and crossover coherence."""
    signal = _pink()
    cutoff = UpmixConfig().lfe_cutoff_hz
    playback = 10.0 ** (10.0 / 20.0)
    rows = []
    for preset in STEM_ROUTING_PRESET_NAMES:
        for stem in _STEMS:
            channels = _route_stem("7.1.4", preset, stem, signal)
            lfe = channels["LFE"] * playback
            mains = sum(
                channels[name]
                for name in channels
                if name != "LFE"
            )
            lfe_energy = _band_energy(lfe, 0.0, cutoff)
            mains_energy = _band_energy(mains, 0.0, cutoff)
            if lfe_energy <= 1e-12:
                continue

            freqs = _freqs()
            near = np.abs(freqs - cutoff) <= 10.0
            a = np.fft.rfft(lfe)[near]
            b = np.fft.rfft(mains)[near]
            coherent = float(np.sum(np.abs(a + b) ** 2))
            power = float(np.sum(np.abs(a) ** 2 + np.abs(b) ** 2))
            rows.append(
                (
                    preset,
                    stem,
                    f"{10.0 * math.log10(lfe_energy / max(mains_energy, 1e-30)):+.2f}",
                    f"{10.0 * math.log10(coherent / max(power, 1e-30)):+.2f}",
                )
            )
    _print_table(
        f"3. LFE in-band (<{cutoff:.0f} Hz) energy with +10 dB playback weighting",
        ("Preset", "Stem", "LFE vs mains (dB)", "crossover sum vs power (dB)"),
        rows,
    )
    assert rows


def test_channel_energy_accounting() -> None:
    """Measurement 4 — per-zone energy fractions, and preset-merge residue."""
    for layout in _LAYOUTS:
        table = _accounting(layout, "balanced")
        _print_table(
            f"4. Zone energy fraction of stem input energy — balanced, {layout}",
            ("Stem", "front", "surround", "height", "LFE", "non-LFE total"),
            [
                (
                    stem,
                    f"{zones['front']:.3f}",
                    f"{zones['surround']:.3f}",
                    f"{zones['height']:.3f}",
                    f"{zones['LFE']:.3f}",
                    f"{zones['front'] + zones['surround'] + zones['height']:.4f}",
                )
                for stem, zones in table.items()
            ],
        )
        for stem, zones in table.items():
            # Not 1.0 since phase 9: ``route_scale`` matches loudness, not raw
            # energy, so a band-limited send zone lands below its input energy.
            # Measurement 5 pins the level invariant.
            total = zones["front"] + zones["surround"] + zones["height"]
            assert 0.2 < total < 2.0, f"{layout} {stem} renormalized to {total}"

    rows = []
    for preset in STEM_ROUTING_PRESET_NAMES:
        table = _accounting("7.1.4", preset)
        rows.append(
            (
                preset,
                f"{sum(z['front'] for z in table.values()) / len(table):.3f}",
                f"{sum(z['surround'] for z in table.values()) / len(table):.3f}",
                f"{sum(z['height'] for z in table.values()) / len(table):.3f}",
                f"{sum(z['LFE'] for z in table.values()) / len(table):.3f}",
            )
        )
    _print_table(
        "4b. Zone fraction averaged over all 16 stems, per preset (7.1.4)",
        ("Preset", "front", "surround", "height", "LFE"),
        rows,
    )

    rows = []
    for layout in _LAYOUTS:
        fmt = FORMAT_MAP[layout]
        for preset in STEM_ROUTING_PRESET_NAMES:
            requested = build_stem_routing(list(_STEMS), fmt, preset)
            reachable = {label.value for label in fmt.channels}
            router = _router(layout, preset)
            worst = ("", 0.0, 0)
            for stem in _STEMS:
                effective = router.get_routing(stem) or {}
                extra = {
                    ch: gain
                    for ch, gain in effective.items()
                    if gain > 0.0 and ch not in requested[stem] and ch in reachable
                }
                if extra and max(extra.values()) > worst[1]:
                    worst = (stem, max(extra.values()), len(extra))
            if worst[0]:
                rows.append((layout, preset, worst[0], f"{worst[1]:.3f}", worst[2]))
    _print_table(
        "4c. Channels the preset does not request but the merged route keeps",
        ("Layout", "Preset", "worst stem", "max residual gain", "residual channels"),
        rows,
    )


_LFE_PLAYBACK_WEIGHT = 10.0 ** (10.0 / 10.0)


def _routed_lkfs(
    channels: dict[str, np.ndarray], layout: str, lfe_weight: float
) -> float:
    """BS.1770 loudness of a routed contribution, LFE weighted by *lfe_weight*."""
    weights: list[float] = []
    audio: list[np.ndarray] = []
    for label in FORMAT_MAP[layout].channels:
        weight = (
            lfe_weight if label == ChannelLabel.LFE else CHANNEL_WEIGHT.get(label, 0.0)
        )
        if weight == 0.0 or label.value not in channels:
            continue
        weights.append(weight)
        audio.append(np.ascontiguousarray(channels[label.value], dtype=np.float64))
    if not weights:
        return -70.0
    return upmixer_dsp.integrated_loudness(weights, audio, _SR)


@lru_cache(maxsize=None)
def _loudness_offsets(layout: str, preset: str) -> dict[str, tuple[float, float]]:
    """Per stem: (LU offset excluding LFE, LU offset with LFE at +10 dB)."""
    signal = _pink()
    reference = upmixer_dsp.integrated_loudness([1.0, 1.0], [signal, signal], _SR)
    offsets: dict[str, tuple[float, float]] = {}
    for stem in _STEMS:
        channels = _route_stem(layout, preset, stem, signal)
        offsets[stem] = (
            _routed_lkfs(channels, layout, 0.0) - reference,
            _routed_lkfs(channels, layout, _LFE_PLAYBACK_WEIGHT) - reference,
        )
    return offsets


def test_routing_loudness_offset() -> None:
    """Measurement 5 — per-stem loudness offset across ``route_scale``.

    ``route_scale`` equalizes raw routed energy; BS.1770 weights channels and
    K-weights the band, so a send-shaped stem can land off its input loudness.
    """
    rows = []
    for layout in _LAYOUTS:
        for preset in STEM_ROUTING_PRESET_NAMES:
            offsets = _loudness_offsets(layout, preset)
            plain = [value[0] for value in offsets.values()]
            perceptual = [value[1] for value in offsets.values()]
            worst = max(offsets, key=lambda stem: abs(offsets[stem][1]))
            rows.append(
                (
                    layout,
                    preset,
                    f"{max(plain) - min(plain):.2f}",
                    f"{max(perceptual) - min(perceptual):.2f}",
                    f"{min(plain):+.2f} / {max(plain):+.2f}",
                    f"{worst} {offsets[worst][1]:+.2f}",
                )
            )
    _print_table(
        "5a. Per-stem loudness offset after routing, spread within preset",
        (
            "Layout",
            "Preset",
            "spread, LFE excluded (LU)",
            "spread, LFE +10 dB (LU)",
            "min / max, LFE excluded (LU)",
            "worst stem, LFE +10 dB",
        ),
        rows,
    )

    for layout in ("stereo", "7.1.4"):
        offsets = _loudness_offsets(layout, "balanced")
        _print_table(
            f"5b. Per-stem loudness offset — balanced, {layout}",
            ("Stem", "offset, LFE excluded (LU)", "offset, LFE +10 dB (LU)"),
            [
                (stem, f"{plain:+.2f}", f"{perceptual:+.2f}")
                for stem, (plain, perceptual) in offsets.items()
            ],
        )

    assert all(
        abs(value[0]) < 12.0
        for layout in _LAYOUTS
        for preset in STEM_ROUTING_PRESET_NAMES
        for value in _loudness_offsets(layout, preset).values()
    )
