"""Objective measurement kit for the stem-mixing path (mixing phase 0).

Skipped by default. Run with:
    uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s

The ``-s`` run prints markdown tables for docs/plans/mixing/phase0_report.md.
Four measurements: send frequency response, downmix fold-down comb / height
loss, LFE energy and crossover phase, per-zone channel energy accounting.

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

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP, ChannelLabel
from upmixer.separation.stem_placement import BALANCED_PLACEMENTS, STEM_ROUTING_PRESET_NAMES
from upmixer.separation.stem_router import (
    HEIGHT_HAAS_DELAY_MS_L,
    HEIGHT_HAAS_DELAY_MS_R,
    SURROUND_HAAS_DELAY_MS_L,
    SURROUND_HAAS_DELAY_MS_R,
    StemRouter,
    build_stem_routing,
)
from upmixer.upmix.multichannel import MultichannelUpmixer
from upmixer.utils import diffuse_send, itu_downmix_stereo

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
        "surround L (HP250 → 31 ms)": (
            router._surround_send(impulse),
            SURROUND_HAAS_DELAY_MS_L,
        ),
        "surround R (HP250 → 37 ms)": (
            router._surround_send(impulse),
            SURROUND_HAAS_DELAY_MS_R,
        ),
        "height L (elev EQ → 23 ms)": (
            router._height_send(impulse),
            HEIGHT_HAAS_DELAY_MS_L,
        ),
        "height R (elev EQ → 29 ms)": (
            router._height_send(impulse),
            HEIGHT_HAAS_DELAY_MS_R,
        ),
    }

    eq_only = {name: _tf_db(eq) for name, (eq, _) in chains.items()}
    full = {
        name: _tf_db(diffuse_send(eq, _SR, delay_ms=delay))
        for name, (eq, delay) in chains.items()
    }
    comb = {name: full[name] - eq_only[name] for name in chains}

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
    for name, (_, delay) in chains.items():
        depth, freq = _worst_notch_db(comb[name])
        eq_depth, eq_freq = _worst_notch_db(full[name])
        band = [level for _, level in _band_means_db(comb[name])]
        rows.append(
            (
                name,
                f"{depth:.2f} @ {freq:.0f} Hz",
                f"{1000.0 / delay:.1f} Hz",
                f"{max(band) - min(band):.2f}",
                f"{eq_depth:.2f} @ {eq_freq:.0f} Hz",
            )
        )
    _print_table(
        "1b. Diffuse-send comb, isolated from the send EQ",
        (
            "Chain",
            "worst notch (dB)",
            "notch spacing (1/D)",
            "band ripple p-p (dB)",
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
    front_pair = float(np.dot(derived["FL"], derived["FL"])) + float(
        np.dot(derived["FR"], derived["FR"])
    )
    center = float(np.dot(derived["C"], derived["C"]))
    rows.append(
        (
            "C build-up (FL+FR+C vs FL+FR)",
            f"{10.0 * math.log10((front_pair + center) / front_pair):+.2f}",
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
        "StemRouter surround (31/37 ms)": (
            diffuse_send(router._surround_send(impulse), _SR, delay_ms=SURROUND_HAAS_DELAY_MS_L),
            diffuse_send(router._surround_send(impulse), _SR, delay_ms=SURROUND_HAAS_DELAY_MS_R),
        ),
        "StemRouter height (23/29 ms)": (
            diffuse_send(router._height_send(impulse), _SR, delay_ms=HEIGHT_HAAS_DELAY_MS_L),
            diffuse_send(router._height_send(impulse), _SR, delay_ms=HEIGHT_HAAS_DELAY_MS_R),
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
        depth, _ = _worst_notch_db(comb[name])
        assert depth < -15.0, f"{name} comb notch only {depth:.2f} dB"
    assert set(_SURROUND + _HEIGHT).issubset(derived)


def test_downmix_fold_comb_and_height_loss() -> None:
    """Measurement 2 — BS.775 downmix vs the stereo render of the same stem."""
    signal = _pink()
    freqs = _freqs()
    fine = (freqs >= _NOTCH_LO_HZ) & (freqs <= _NOTCH_HI_HZ)
    rows = []
    for stem in ("Crowd", "Other", "Crash", "Hi-Hat", "Backing Vocals", "Drums"):
        immersive = _route_stem("7.1.4", "balanced", stem, signal)
        down_l, down_r = itu_downmix_stereo(immersive)
        folded = _route_stem("stereo", "balanced", stem, signal)

        ratio = _tf_db(down_l + down_r) - _tf_db(folded["FL"] + folded["FR"])
        bands = [
            level
            for center, level in _band_means_db(ratio)
            if _NOTCH_LO_HZ <= center <= _NOTCH_HI_HZ
        ]
        offset = sum(bands) / len(bands)
        depth, freq = _worst_notch_db(ratio)
        rows.append(
            (
                stem,
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
            "level offset (dB)",
            "band ripple p-p (dB)",
            "per-bin ripple σ (dB)",
            "worst notch rel. (dB)",
        ),
        rows,
    )

    rows = []
    for preset in STEM_ROUTING_PRESET_NAMES:
        table = _accounting("7.1.4", preset)
        losses = {
            stem: -10.0 * math.log10(max(1.0 - zones["height"], 1e-9))
            for stem, zones in table.items()
        }
        worst = max(losses, key=losses.get)
        rows.append(
            (
                preset,
                f"{sum(losses.values()) / len(losses):.2f}",
                f"{worst} {losses[worst]:.2f}",
                f"{max(table[s]['height'] for s in table):.3f}",
            )
        )
    _print_table(
        "2b. Energy lost by dropping heights from the downmix, per preset (7.1.4)",
        ("Preset", "mean loss (dB)", "worst stem (dB)", "max height fraction"),
        rows,
    )

    per_stem = _accounting("7.1.4", "balanced")
    _print_table(
        "2c. Height fraction and downmix loss per stem, balanced (7.1.4)",
        ("Stem", "height fraction", "loss (dB)"),
        [
            (
                stem,
                f"{zones['height']:.3f}",
                f"{-10.0 * math.log10(max(1.0 - zones['height'], 1e-9)):.2f}",
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
            total = zones["front"] + zones["surround"] + zones["height"]
            assert abs(total - 1.0) < 1e-6, f"{layout} {stem} renormalized to {total}"

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
