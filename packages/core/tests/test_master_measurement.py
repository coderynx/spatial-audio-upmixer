"""Objective measurement kit for the mastering chain (mastering phase 0).

Skipped by default. Run with:
    uv run pytest packages/core/tests/test_master_measurement.py -m perf -s

The ``-s`` run prints markdown tables for docs/plans/mastering/phase0_report.md.
Two parts: a compliance baseline (measured delivery numbers for two contrasting
programmes across three layouts at two loudness targets) and the four audits
that size later phases — the 5.1-fold loudness delta, the limiter's LFE link
duck depth, the 96 kHz true-peak oversampling factor, and the undithered
quantization floor.

Programmes are synthetic and seeded: the chain under measurement is the
mastering chain, so feeding it a constructed bed measures exactly that and
nothing upstream of it.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import (
    measure_integrated_loudness,
    measure_loudness_stats,
    measure_true_peak,
)
from upmixer.mastering.chain import MasteringChain, MasteringResult
from upmixer.mastering.limiter import _SAFETY_MARGIN_DB as _LIMITER_SAFETY_MARGIN_DB
from upmixer.mastering.limiter import LookAheadLimiter

pytestmark = pytest.mark.perf

_SR = 48_000
_DUR_S = 30
_SEED = 20260818

_LAYOUTS: tuple[str, ...] = ("stereo", "5.1", "7.1.4")
_PROGRAMMES: tuple[str, ...] = ("dense", "dynamic")

# Dolby Atmos Music delivery, the source of the config defaults. Phase 1 turns
# this row into a named preset table with tolerances.
_TARGET_LKFS = -18.0
_TARGET_TP_DBTP = -1.0
_HOT_TARGET_LKFS = -10.0

# BS.775-4 Annex D b₀ for the back-to-side fold, and the project's height
# coefficient (docs/standards/spatial_layouts_bs775_bs2051.md). Phase 1 owns
# the production implementation; this is the yardstick it has to match.
_FOLD_SURROUND = 0.7071
_FOLD_HEIGHT = 0.7071

# Level trims applied to the synthesized bed so the constructed programme has a
# plausible front-dominant balance instead of twelve equally loud channels.
_CHANNEL_TRIM: dict[str, float] = {
    "FL": 1.0, "FR": 1.0, "C": 0.7,
    "SL": 0.5, "SR": 0.5, "BL": 0.4, "BR": 0.4,
    "TFL": 0.35, "TFR": 0.35, "TBL": 0.3, "TBR": 0.3,
}


def _print_table(title: str, header: tuple[str, ...], rows: list[tuple]) -> None:
    print(f"\n### {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(v) for v in row) + " |")


def _source(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    """One decorrelated channel of the named test programme.

    ``dense`` is loud, near-constant broadband material with a low crest
    factor — the case a limiter leans on. ``dynamic`` is the same spectrum
    under a slow ±9 dB envelope with sparse transient hits, so it carries a
    real loudness range and a high crest factor.
    """
    t = np.arange(n) / _SR
    # Pink-ish noise: white noise through a one-pole tilt, plus tonal content
    # so the K-weighting has something with structure to sit on.
    white = rng.standard_normal(n)
    pink = np.convolve(white, np.exp(-np.arange(64) / 12.0), mode="same") / 3.0
    tonal = 0.3 * np.sin(2 * np.pi * (110.0 + 40.0 * rng.random()) * t + rng.random())
    signal = pink + tonal

    if kind == "dense":
        return 0.5 * signal / np.max(np.abs(signal))

    envelope = 10.0 ** (
        (-9.0 + 9.0 * np.sin(2 * np.pi * 0.06 * t + rng.random() * 6.0)) / 20.0
    )
    hits = np.zeros(n)
    for at in rng.integers(0, n - _SR // 2, size=24):
        decay = np.exp(-np.arange(_SR // 2) / (_SR * 0.04))
        hits[at:at + _SR // 2] += decay * rng.standard_normal(_SR // 2)
    shaped = signal * envelope + 0.6 * hits
    return 0.5 * shaped / np.max(np.abs(shaped))


def _bed(kind: str, layout: str) -> dict[str, np.ndarray]:
    """A decorrelated bed for *layout*, LFE lowpassed to its crossover."""
    rng = np.random.default_rng(_SEED + _PROGRAMMES.index(kind))
    fmt = FORMAT_MAP[layout]
    n = _DUR_S * _SR
    bed: dict[str, np.ndarray] = {}
    for label in fmt.channels:
        name = label.value
        if name == "LFE":
            continue
        bed[name] = _CHANNEL_TRIM[name] * _source(kind, n, rng)
    if "LFE" in {c.value for c in fmt.channels}:
        # LFE carries the bed's summed low end, the way bass control's send
        # would, at BS.775's −10 dB programme level.
        summed = np.ascontiguousarray(bed["FL"] + bed["FR"])
        bed["LFE"] = 0.316 * upmixer_dsp.lfe_lowpass(summed, _SR, 120.0, 4)
    return bed


def _master(
    bed: dict[str, np.ndarray],
    layout: str,
    target_lkfs: float = _TARGET_LKFS,
) -> tuple[dict[str, np.ndarray], MasteringResult]:
    cfg = UpmixConfig(
        output_format=layout,
        loudness_target_lkfs=target_lkfs,
        loudness_max_tp=_TARGET_TP_DBTP,
    )
    return MasteringChain(cfg).process(
        {k: v.copy() for k, v in bed.items()}, _SR, FORMAT_MAP[layout]
    )


def _compliance_table(
    title: str,
    results: list[tuple[str, MasteringResult]],
    target_lkfs: float,
) -> None:
    """Render one compliance table: measured delivery numbers vs the target row.

    The report generator phase 0 owes later phases — every audible change
    re-runs this and diffs the rows.
    """
    rows = []
    for name, r in results:
        worst_channel = max(r.per_channel_tp_dbtp, key=r.per_channel_tp_dbtp.get)
        rows.append((
            name,
            f"{r.measured_lkfs:.2f}",
            f"{r.measured_lkfs - target_lkfs:+.2f}",
            f"{r.measured_tp_dbtp:.2f}",
            f"{worst_channel} {r.per_channel_tp_dbtp[worst_channel]:.2f}",
            f"{r.lra_lu:.1f}",
            f"{r.max_momentary_lkfs:.1f}",
            f"{r.max_short_term_lkfs:.1f}",
            f"{r.plr_db:.1f}",
            f"{r.psr_db:.1f}" if r.psr_db is not None else "—",
            f"{r.limiter_gr_peak_db:.2f}",
            f"{100.0 * r.limiter_gr_duty:.1f}%",
            "PASS" if r.measured_tp_dbtp <= _TARGET_TP_DBTP + 1e-6 else "FAIL",
        ))
    _print_table(
        title,
        (
            "render", "LKFS", "Δ target", "dBTP", "worst ch dBTP", "LRA LU",
            "max M", "max S", "PLR", "PSR", "lim GR pk", "lim GR duty", "TP",
        ),
        rows,
    )


def test_compliance_baseline() -> None:
    """Measured delivery numbers at the default and at a deliberately hot target."""
    overshoots = []
    for target, label in ((_TARGET_LKFS, "default"), (_HOT_TARGET_LKFS, "hot")):
        for kind in _PROGRAMMES:
            results = []
            for layout in _LAYOUTS:
                _, result = _master(_bed(kind, layout), layout, target)
                results.append((layout, result))
                overshoots.append(result.measured_tp_dbtp - _TARGET_TP_DBTP)
            _compliance_table(
                f"Compliance — {kind} programme, target {target:.0f} LKFS ({label})",
                results,
                target,
            )
    worst = max(overshoots)
    print(f"\nWorst true-peak ceiling overshoot: {worst:+.4f} dB")
    # Not `<= ceiling`: under deep gain reduction the limiter leaks past its
    # nominal ceiling (phase0_report.md § "Ceiling overshoot" — handed to
    # phase 2). Bounded by the limiter's own internal safety margin, so a
    # later phase that makes it worse fails loudly.
    assert worst <= _LIMITER_SAFETY_MARGIN_DB, f"overshoot {worst:+.4f} dB"


def _fold_to_51(bed: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """BS.775-governed 7.1.4 → 5.1 re-render: heights onto their base-layer
    channels, the back pair onto the surround pair."""
    ks, kh = _FOLD_SURROUND, _FOLD_HEIGHT
    return {
        "FL": bed["FL"] + kh * bed["TFL"],
        "FR": bed["FR"] + kh * bed["TFR"],
        "C": bed["C"],
        "LFE": bed["LFE"],
        "SL": bed["SL"] + ks * bed["BL"] + kh * bed["TBL"],
        "SR": bed["SR"] + ks * bed["BR"] + kh * bed["TBR"],
    }


def test_audit_five_one_fold_loudness_delta() -> None:
    """Audit 1 — full-bed vs 5.1-fold integrated loudness on 7.1.4 renders.

    The error bar on every Atmos compliance claim the chain currently makes:
    the spec measures the 5.1 re-render, the chain measures the full bed.
    """
    rows = []
    for kind in _PROGRAMMES:
        mastered, result = _master(_bed(kind, "7.1.4"), "7.1.4")
        folded = _fold_to_51(mastered)
        fold_lkfs = measure_integrated_loudness(folded, _SR, FORMAT_MAP["5.1"])
        fold_stats = measure_loudness_stats(folded, _SR, FORMAT_MAP["5.1"])
        rows.append((
            kind,
            f"{result.measured_lkfs:.2f}",
            f"{fold_lkfs:.2f}",
            f"{fold_lkfs - result.measured_lkfs:+.2f}",
            f"{measure_true_peak(folded):.2f}",
            f"{result.lra_lu:.1f} → {fold_stats['lra_lu']:.1f}",
        ))

    # A height-only programme isolates the worst case the fold can produce.
    bed = _bed("dense", "7.1.4")
    height_only = {
        k: (v if k in ("TFL", "TFR", "TBL", "TBR") else np.zeros_like(v))
        for k, v in bed.items()
    }
    mastered, result = _master(height_only, "7.1.4")
    folded = _fold_to_51(mastered)
    fold_lkfs = measure_integrated_loudness(folded, _SR, FORMAT_MAP["5.1"])
    rows.append((
        "height-only",
        f"{result.measured_lkfs:.2f}",
        f"{fold_lkfs:.2f}",
        f"{fold_lkfs - result.measured_lkfs:+.2f}",
        f"{measure_true_peak(folded):.2f}",
        "—",
    ))

    _print_table(
        "Audit 1 — 5.1-fold loudness delta (7.1.4 renders)",
        ("programme", "full bed LKFS", "5.1 fold LKFS", "Δ", "fold dBTP", "LRA full → fold"),
        rows,
    )


def test_audit_lfe_link_duck_depth() -> None:
    """Audit 2 — how much gain reduction the mains take from an LFE-only peak.

    ``lookahead_limit`` maxes its envelope across every channel, LFE included,
    so an LFE that alone approaches the ceiling ducks the whole bed.
    """
    rng = np.random.default_rng(_SEED)
    n = 10 * _SR
    t = np.arange(n) / _SR
    # Mains sit well under the ceiling: on their own they never limit, so all
    # gain reduction below is the LFE's doing.
    mains = 0.25 * _source("dense", n, rng)
    # Sparse 40 Hz swells, the shape a `cinema` LFE send produces.
    swell = np.exp(-((t % 2.0) - 0.2) ** 2 / 0.02)
    lfe_shape = swell * np.sin(2 * np.pi * 40.0 * t)

    rows = []
    for lfe_peak_dbfs in (None, -3.0, 0.0, 3.0, 6.0):
        bed = {"FL": mains.copy(), "FR": mains.copy()}
        if lfe_peak_dbfs is not None:
            bed["LFE"] = 10.0 ** (lfe_peak_dbfs / 20.0) * lfe_shape
        limiter = LookAheadLimiter(_TARGET_TP_DBTP, 5.0, 50.0, _SR)
        out = limiter.process(bed)
        # The gain the mains actually received, sample by sample.
        live = np.abs(mains) > 1e-6
        gain = out["FL"][live] / mains[live]
        rows.append((
            "none" if lfe_peak_dbfs is None else f"{lfe_peak_dbfs:+.0f} dBFS",
            f"{limiter.gr_peak_db:.2f}",
            f"{100.0 * limiter.gr_duty:.1f}%",
            f"{20.0 * math.log10(float(np.min(gain))):+.2f}",
            f"{20.0 * math.log10(float(np.sqrt(np.mean(out['FL'] ** 2)) / np.sqrt(np.mean(mains ** 2)))):+.2f}",
        ))

    _print_table(
        "Audit 2 — LFE-link duck depth (mains alone never limit)",
        (
            "LFE peak", "GR peak dB", "GR duty",
            "worst mains gain dB", "mains RMS change dB",
        ),
        rows,
    )


def _fft_true_peak(signal: np.ndarray, ratio: int) -> float:
    """Reference true peak by exact band-limited FFT interpolation.

    Valid only for a signal periodic in the buffer — every fixture below uses
    an integer number of cycles, so zero-padding the spectrum reconstructs the
    continuous waveform without edge artifacts.
    """
    spectrum = np.fft.rfft(signal)
    padded = np.zeros(len(signal) * ratio // 2 + 1, dtype=complex)
    padded[: len(spectrum)] = spectrum
    return float(np.max(np.abs(np.fft.irfft(padded, len(signal) * ratio) * ratio)))


def test_audit_96k_true_peak_factor() -> None:
    """Audit 3 — the BS.1770-5 ≤48 kHz 4x kernel, run at 96 kHz.

    The standard's table asks 2x at 96 kHz; the code runs 4x at every rate.
    Higher ratios are permitted, so the question is only whether the kernel's
    passband still covers the band at 96 kHz — measured here against exact
    band-limited interpolation.
    """
    rows = []
    for sr in (48_000, 96_000):
        # Fixed physical frequencies, so the same tone can be compared across
        # rates, plus one near-Nyquist case per rate.
        seen: set[int] = set()
        for hz in (997.0, 5_000.0, 11_520.0, 21_598.0, 0.45 * sr):
            n = 4096
            cycles = round(hz * n / sr)
            if cycles < 1 or cycles in seen:
                continue
            seen.add(cycles)
            exact_hz = cycles * sr / n
            phase = math.pi / 4.0
            signal = np.sin(2 * np.pi * exact_hz * np.arange(n) / sr + phase)
            measured = upmixer_dsp.true_peak_per_channel(
                [np.ascontiguousarray(signal)]
            )[0]
            reference = 20.0 * math.log10(_fft_true_peak(signal, 32))
            rows.append((
                f"{sr // 1000} kHz",
                f"{exact_hz:.0f}",
                f"{exact_hz / sr:.3f}",
                f"{measured:+.3f}",
                f"{reference:+.3f}",
                f"{measured - reference:+.3f}",
            ))
    _print_table(
        "Audit 3 — 4x true-peak detector error vs exact interpolation",
        ("rate", "Hz", "f/fs", "measured dBTP", "exact dBTP", "error dB"),
        rows,
    )


def test_audit_quantization_floor() -> None:
    """Audit 4 — what undithered bit-depth reduction costs today.

    Measures the real writer path: float64 → libsndfile → read back, so the
    number is whatever the export actually does, not what truncation would.
    """
    mastered, _ = _master(_bed("dynamic", "5.1"), "5.1")
    programme = np.column_stack([mastered[c.value] for c in FORMAT_MAP["5.1"].channels])
    # The case undithered truncation actually hurts: a quiet fading tone, where
    # the error stops looking like noise and starts looking like distortion.
    fade = np.arange(5 * _SR) / (5 * _SR)
    quiet_tone = (
        10.0 ** (-50.0 / 20.0)
        * (1.0 - fade)
        * np.sin(2 * np.pi * 997.0 * np.arange(5 * _SR) / _SR)
    ).reshape(-1, 1)

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for label, signal in (("programme", programme), ("−50 dBFS fade", quiet_tone)):
            for subtype in ("PCM_24", "PCM_16"):
                path = Path(tmp) / f"{label}-{subtype}.wav"
                sf.write(str(path), signal, _SR, format="WAV", subtype=subtype)
                back, _ = sf.read(str(path), dtype="float64", always_2d=True)
                error = (back - signal).reshape(-1)
                error_rms = float(np.sqrt(np.mean(error ** 2)))
                signal_rms = float(np.sqrt(np.mean(signal ** 2)))
                bits = int(subtype.split("_")[1])
                lsb = 2.0 ** -(bits - 1)
                # Round-to-nearest error is uniform over ±lsb/2 (RMS lsb/√12);
                # truncation is uniform over one lsb *offset by half of it*, so
                # it reads 2x that and carries a DC term. TPDF dither reads √2x
                # and decorrelates.
                rows.append((
                    label,
                    subtype,
                    f"{20.0 * math.log10(error_rms):.1f}",
                    f"{20.0 * math.log10(error_rms / signal_rms):.1f}",
                    f"{error_rms / (lsb / math.sqrt(12.0)):.3f}",
                    f"{float(np.mean(error)) / lsb:+.3f}",
                    f"{float(np.corrcoef(error, signal.reshape(-1))[0, 1]):+.4f}",
                ))

    _print_table(
        "Audit 4 — quantization floor of the current (undithered) writer",
        (
            "signal", "subtype", "error RMS dBFS", "error vs signal dB",
            "error / round-to-nearest RMS", "DC offset (LSB)",
            "error·signal correlation",
        ),
        rows,
    )
