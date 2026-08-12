"""Dump reference vectors from the Python/SciPy implementation.

The Rust core is verified against these fixtures rather than against a
re-derivation, so any divergence from the shipping export pipeline is caught
at the kernel that causes it.  Run from the repository root:

    uv run python packages/dsp/tools/dump_golden_vectors.py

Each case writes ``<name>.json`` (parameters, shapes, tolerance) plus raw
little-endian float64 ``<name>.<array>.f64`` blobs next to it.  Regenerate
only against a Python implementation that has not yet been swapped for the
Rust one; once a stage is ported the fixture is a regression pin, not a
reference.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "crates" / "dsp-core" / "tests" / "golden"


def _write_array(name: str, key: str, values: np.ndarray) -> str:
    arr = np.ascontiguousarray(np.asarray(values, dtype=np.float64).ravel())
    path = GOLDEN_DIR / f"{name}.{key}.f64"
    path.write_bytes(struct.pack(f"<{arr.size}d", *arr.tolist()))
    return path.name


def write_case(name: str, params: dict, arrays: dict[str, np.ndarray], tol: float) -> None:
    meta = {
        "name": name,
        "params": params,
        "tolerance": tol,
        "arrays": {k: {"file": _write_array(name, k, v), "len": int(np.asarray(v).size)}
                   for k, v in arrays.items()},
    }
    (GOLDEN_DIR / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n")


def deterministic_signal(n: int, sr: int = 48000, seed_phase: float = 0.0) -> np.ndarray:
    """Multi-tone test signal — deterministic across languages, unlike RNG noise.

    Reproduced in ``tests/common/mod.rs`` so bed-sized fixtures can store only
    their outputs; ``generator_parity`` pins the two implementations together.
    """
    t = np.arange(n, dtype=np.float64) / sr
    sig = np.zeros(n, dtype=np.float64)
    for freq, amp in ((55.0, 0.30), (220.0, 0.22), (1000.0, 0.18),
                      (3500.0, 0.12), (11000.0, 0.07)):
        sig += amp * np.sin(2.0 * np.pi * freq * t + seed_phase * freq / 100.0)
    envelope = 0.6 + 0.4 * np.sin(2.0 * np.pi * 0.7 * t)
    return sig * envelope


def dump_butter() -> None:
    for order, wn, btype in ((1, 0.00625, "low"), (2, 0.05, "low"), (4, 0.005, "low"),
                             (2, 0.125, "high"), (1, 0.2, "high"), (4, 0.25, "low"),
                             (2, 0.0033333333333333335, "low"), (3, 0.1, "low"),
                             (3, 0.3, "high"), (5, 0.15, "low")):
        sos = signal.butter(order, wn, btype=btype, output="sos")
        write_case(
            f"butter_{order}_{btype}_{str(wn).replace('.', 'p')}",
            {"order": order, "wn": wn, "btype": btype},
            {"sos": sos},
            1e-14,
        )


def dump_sosfilt() -> None:
    x = deterministic_signal(4096)
    for order, wn, btype in ((2, 0.05, "low"), (4, 0.005, "low"), (2, 0.125, "high")):
        sos = signal.butter(order, wn, btype=btype, output="sos")
        name = f"sosfilt_{order}_{btype}_{str(wn).replace('.', 'p')}"
        write_case(name, {"order": order, "wn": wn, "btype": btype},
                   {"sos": sos, "input": x, "output": signal.sosfilt(sos, x)}, 1e-12)
        write_case(f"{name}_zi", {"order": order, "wn": wn, "btype": btype},
                   {"sos": sos, "zi": signal.sosfilt_zi(sos)}, 1e-13)


def dump_sosfiltfilt() -> None:
    for n, order, wn in ((4096, 2, 0.05), (513, 4, 0.02), (37, 2, 0.1), (8, 2, 0.1)):
        x = deterministic_signal(n)
        sos = signal.butter(order, wn, btype="low", output="sos")
        ntaps = 2 * len(sos) + 1
        ntaps -= min(int((sos[:, 2] == 0).sum()), int((sos[:, 5] == 0).sum()))
        # Below SciPy's padlen there is no zero-phase result to compare against;
        # the pipeline falls back to a single forward pass, so pin that instead.
        y = signal.sosfiltfilt(sos, x) if n > 3 * ntaps else signal.sosfilt(sos, x)
        write_case(f"sosfiltfilt_n{n}_o{order}", {"n": n, "order": order, "wn": wn},
                   {"sos": sos, "input": x, "output": y}, 1e-12)


def dump_lfilter() -> None:
    x = deterministic_signal(2048)
    for alpha in (0.001, 0.05, 0.5):
        b = np.array([alpha])
        a = np.array([1.0, -(1.0 - alpha)])
        write_case(f"lfilter_onepole_{str(alpha).replace('.', 'p')}", {"alpha": alpha},
                   {"input": x, "output": signal.lfilter(b, a, x)}, 1e-12)


def dump_upfirdn() -> None:
    from upmixer.loudness import _TRUE_PEAK_FIR_4X

    x = deterministic_signal(1024)
    write_case("upfirdn_truepeak_4x", {"up": 4},
               {"fir": _TRUE_PEAK_FIR_4X, "input": x,
                "output": signal.upfirdn(_TRUE_PEAK_FIR_4X, x, up=4)}, 1e-12)


def dump_minimum_filter1d() -> None:
    x = np.abs(deterministic_signal(2048)) + 0.05
    for size in (3, 13, 241):
        write_case(f"minfilter_centered_{size}", {"size": size, "mode": "reflect"},
                   {"input": x, "output": ndimage.minimum_filter1d(x, size=size)}, 1e-15)
    for size in (3, 13):
        write_case(f"minfilter_nearest_{size}", {"size": size, "mode": "nearest"},
                   {"input": x,
                    "output": ndimage.minimum_filter1d(x, size=size, mode="nearest")}, 1e-15)


def dump_firwin2() -> None:
    for ntaps, freqs, gains in (
        (1023, [0.0, 0.1, 0.35, 0.7, 1.0], [1.0, 1.2, 0.9, 1.05, 1.0]),
        (511, [0.0, 0.25, 0.5, 1.0], [0.8, 1.4, 1.0, 0.6]),
        (65, [0.0, 0.5, 1.0], [1.0, 0.5, 1.0]),
    ):
        taps = signal.firwin2(ntaps, freqs, gains)
        write_case(f"firwin2_{ntaps}", {"ntaps": ntaps, "freq": freqs, "gain": gains},
                   {"freq": np.array(freqs), "gain": np.array(gains), "taps": taps}, 1e-12)


def dump_minimum_phase() -> None:
    for ntaps, freqs, gains in (
        (1023, [0.0, 0.1, 0.35, 0.7, 1.0], [1.0, 1.2, 0.9, 1.05, 1.0]),
        (511, [0.0, 0.25, 0.5, 1.0], [0.8, 1.4, 1.0, 0.6]),
    ):
        linear = signal.firwin2(ntaps, freqs, gains)
        minphase = signal.minimum_phase(linear, method="homomorphic", half=False)
        write_case(f"minimum_phase_{ntaps}", {"ntaps": ntaps},
                   {"linear": linear, "minphase": minphase}, 1e-10)


def dump_k_weighting() -> None:
    from upmixer.loudness import _k_weighting_sos

    for sr in (44100, 48000, 96000):
        write_case(f"k_weighting_{sr}", {"sample_rate": sr},
                   {"sos": _k_weighting_sos(sr)}, 1e-14)


def dump_loudness() -> None:
    from upmixer.formats import FORMAT_MAP
    from upmixer.loudness import CHANNEL_WEIGHT, measure_integrated_loudness, measure_true_peak

    fmt = FORMAT_MAP["5.1.4"]
    sr = 48000
    n = sr * 3
    channels = {
        label.value: deterministic_signal(n, sr, seed_phase=float(i)) * (0.5 + 0.1 * i)
        for i, label in enumerate(fmt.channels)
    }
    # Inputs are regenerated Rust-side; only the measurements are pinned.
    write_case(
        "loudness_514",
        {
            "sample_rate": sr,
            "n": n,
            "format": "5.1.4",
            "channels": [c.value for c in fmt.channels],
            "weights": [CHANNEL_WEIGHT.get(c, 0.0) for c in fmt.channels],
            "lkfs": measure_integrated_loudness(channels, sr, fmt),
            "true_peak_dbtp": measure_true_peak(channels, sr),
        },
        {},
        1e-10,
    )


def dump_stft() -> None:
    x = deterministic_signal(48000)
    f, t, z = signal.stft(x, fs=48000, nperseg=8192, noverlap=8192 * 3 // 4,
                          window="hann", boundary=None, padded=False)
    psd = np.mean(np.abs(z) ** 2, axis=1)
    write_case("stft_psd_8192", {"nperseg": 8192, "noverlap": 8192 * 3 // 4},
               {"input": x, "psd": psd}, 1e-12)


MASTERING_CHANNELS = ("FL", "FR", "C", "LFE")
MASTERING_N = 12_000
MASTERING_SR = 48_000


def dump_generator_parity() -> None:
    write_case("generator_parity", {"n": 4096, "sample_rate": 48000, "seed_phase": 1.5},
               {"signal": deterministic_signal(4096, 48000, 1.5)}, 1e-14)


def _mastering_bed() -> dict[str, np.ndarray]:
    """A short 4-channel bed shared by every mastering-stage fixture."""
    return {
        name: deterministic_signal(MASTERING_N, MASTERING_SR, seed_phase=float(i))
        * (0.55 + 0.12 * i)
        for i, name in enumerate(MASTERING_CHANNELS)
    }


def _write_stage(name: str, out: dict[str, np.ndarray], params: dict, tol: float) -> None:
    params = dict(params)
    params["channels"] = list(MASTERING_CHANNELS)
    params["n"] = MASTERING_N
    params["sample_rate"] = MASTERING_SR
    params["hot"] = False
    write_case(name, params, {f"ch_{k}": out[k] for k in MASTERING_CHANNELS}, tol)


def dump_mastering() -> None:
    from upmixer.mastering.bass import (
        BASS_PROFILES, EXCITE_BLEND, EXCITE_DRIVE, MID_CUTOFF_HZ, SUB_CUTOFF_HZ,
        BassController,
    )
    from upmixer.mastering.compressor import COMP_PROFILES, BusCompressor
    from upmixer.mastering.eq import EQ_PROFILES, SpectralShaper, _build_fir
    from upmixer.mastering.limiter import _SAFETY_MARGIN_DB, LookAheadLimiter

    bed = _mastering_bed()

    for profile in EQ_PROFILES:
        write_case(
            f"eq_fir_{profile.replace('-', '_')}",
            {"profile": profile, "sample_rate": MASTERING_SR, "n_taps": 1023,
             "breakpoints": [list(bp) for bp in EQ_PROFILES[profile]]},
            {"taps": _build_fir(profile, MASTERING_SR, 1023)},
            1e-10,
        )

    for profile, strength in (("atmos-streaming", 1.0), ("spatial-warm", 0.6)):
        shaped = SpectralShaper(profile, strength, MASTERING_SR).process(dict(bed))
        _write_stage(
            f"eq_apply_{profile.replace('-', '_')}_{str(strength).replace('.', 'p')}",
            shaped, {"profile": profile, "strength": strength}, 1e-9,
        )

    for profile in COMP_PROFILES:
        params = COMP_PROFILES[profile]
        out = BusCompressor(sample_rate=MASTERING_SR, **params).process(dict(bed))
        _write_stage(f"comp_{profile}", out, dict(params), 1e-12)

    for profile in BASS_PROFILES:
        params = BASS_PROFILES[profile]
        out = BassController(sample_rate=MASTERING_SR, **params).process(dict(bed))
        _write_stage(
            f"bass_{profile}",
            out,
            {
                **{k: (v if v is not None else -1.0) if k == "mono_cutoff_hz" else v
                   for k, v in params.items()},
                "sub_cutoff_hz": SUB_CUTOFF_HZ,
                "mid_cutoff_hz": MID_CUTOFF_HZ,
                "excite_blend": EXCITE_BLEND,
                "excite_drive": EXCITE_DRIVE,
                "stereo_pairs": [[0, 1]],
            },
            1e-11,
        )

    # A hot bed so the limiter actually engages.
    hot = {k: np.clip(v * 3.2, -1.5, 1.5) for k, v in bed.items()}
    limited = LookAheadLimiter(-1.0, 5.0, 50.0, MASTERING_SR).process(dict(hot))
    write_case(
        "limiter_apply",
        {"channels": list(MASTERING_CHANNELS), "n": MASTERING_N,
         "sample_rate": MASTERING_SR, "hot": True, "ceiling_dbtp": -1.0,
         "lookahead_ms": 5.0, "release_ms": 50.0,
         "safety_margin_db": _SAFETY_MARGIN_DB},
        {f"ch_{k}": limited[k] for k in MASTERING_CHANNELS},
        1e-11,
    )


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in GOLDEN_DIR.glob("*"):
        stale.unlink()
    dump_generator_parity()
    dump_butter()
    dump_sosfilt()
    dump_sosfiltfilt()
    dump_lfilter()
    dump_upfirdn()
    dump_minimum_filter1d()
    dump_firwin2()
    dump_minimum_phase()
    dump_k_weighting()
    dump_loudness()
    dump_stft()
    dump_mastering()
    print(f"wrote fixtures to {GOLDEN_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
