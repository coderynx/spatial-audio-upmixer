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


def dump_butter_bandpass() -> None:
    for order, low, high in ((2, 0.05, 0.2), (2, 0.1234, 0.1466), (1, 0.01, 0.5),
                             (3, 0.2, 0.35)):
        sos = signal.butter(order, [low, high], btype="bandpass", output="sos")
        write_case(f"butter_bp_{order}_{str(low).replace('.', 'p')}_{str(high).replace('.', 'p')}",
                   {"order": order, "low": low, "high": high}, {"sos": sos}, 1e-14)


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
    import upmixer_dsp

    fir = upmixer_dsp.true_peak_fir()
    x = deterministic_signal(1024)
    write_case("upfirdn_truepeak_4x", {"up": 4},
               {"fir": fir, "input": x,
                "output": signal.upfirdn(fir, x, up=4)}, 1e-12)


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
    from upmixer.config import UpmixConfig
    from upmixer.mastering.bass import (
        BASS_PROFILES, DECORR_FAST_MS, DECORR_HIGH_HZ, DECORR_LOW_HZ,
        DECORR_MAX_DELAY_MS, DECORR_SECTIONS, DECORR_SLOW_MS, EXCITE_BLEND,
        EXCITE_DRIVE, MID_CUTOFF_HZ, PUNCH_FAST_MS, PUNCH_MAX_DB,
        PUNCH_SLOW_MS, SUB_CUTOFF_HZ, BassController, resolve_lf_targets,
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

    lfe_authoring_gain = UpmixConfig().lfe_gain
    # Every shipped profile leaves decorrelation off, so it needs a case of its
    # own for the cross-language check to cover the cascade at all.
    bass_cases = dict(BASS_PROFILES)
    bass_cases["decorrelate"] = {**BASS_PROFILES["deep"], "decorrelate": 0.6}
    for profile in bass_cases:
        params = bass_cases[profile]
        out = BassController(
            sample_rate=MASTERING_SR, lfe_authoring_gain=lfe_authoring_gain, **params
        ).process(dict(bed))
        targets = (
            resolve_lf_targets(
                list(MASTERING_CHANNELS), params["spread"], params["lfe_mode"],
                params["lfe_send"], lfe_authoring_gain,
            )
            if params["unify_hz"] is not None
            else []
        )
        _write_stage(
            f"bass_{profile}",
            out,
            {
                **{k: (v if v is not None else -1.0) if k == "unify_hz" else v
                   for k, v in params.items()},
                "sub_cutoff_hz": SUB_CUTOFF_HZ,
                "mid_cutoff_hz": MID_CUTOFF_HZ,
                "excite_blend": EXCITE_BLEND,
                "excite_drive": EXCITE_DRIVE,
                "punch_fast_ms": PUNCH_FAST_MS,
                "punch_slow_ms": PUNCH_SLOW_MS,
                "punch_max_db": PUNCH_MAX_DB,
                "decorr_low_hz": DECORR_LOW_HZ,
                "decorr_high_hz": DECORR_HIGH_HZ,
                "decorr_sections": DECORR_SECTIONS,
                "decorr_max_delay_ms": DECORR_MAX_DELAY_MS,
                "decorr_fast_ms": DECORR_FAST_MS,
                "decorr_slow_ms": DECORR_SLOW_MS,
                "lf_targets": [[i, w] for i, w in targets],
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


def dump_match_reference() -> None:
    from upmixer.mastering.match_reference.curve import (
        _BASS_CLAMP_DB, _BASS_CLAMP_HZ, _CLAMP_KNEE_DB, _CONFIDENCE_FLOOR_DB,
        _LOG_GRID_OCT_STEP, _MAX_FREQ_HZ, _MIN_FREQ_HZ, _NORM_HIGH_HZ, _NORM_LOW_HZ,
        _N_BREAKPOINTS, _SMOOTH_SIGMA_OCT, _TAPER_HIGH_HZ, _TAPER_LOW_HZ,
        _band_edge_taper, _confidence_taper, _log_grid, _smooth_log_grid, _soft_clamp,
        compute_reference_curve,
    )
    from upmixer.mastering.match_reference.spectrum import weighted_power_spectrum_arrays

    curve_params = {
        "min_freq_hz": _MIN_FREQ_HZ, "max_freq_hz": _MAX_FREQ_HZ,
        "grid_step_oct": _LOG_GRID_OCT_STEP, "smooth_sigma_oct": _SMOOTH_SIGMA_OCT,
        "norm_low_hz": _NORM_LOW_HZ, "norm_high_hz": _NORM_HIGH_HZ,
        "confidence_floor_db": _CONFIDENCE_FLOOR_DB,
        "taper_low": list(_TAPER_LOW_HZ), "taper_high": list(_TAPER_HIGH_HZ),
        "n_breakpoints": _N_BREAKPOINTS, "clamp_knee_db": _CLAMP_KNEE_DB,
        "bass_clamp_hz": _BASS_CLAMP_HZ, "bass_clamp_db": _BASS_CLAMP_DB,
    }

    grid = _log_grid(_MAX_FREQ_HZ)
    write_case("mr_log_grid", {"high_hz": _MAX_FREQ_HZ, **curve_params}, {"grid": grid}, 1e-9)

    ramp = np.sin(np.linspace(0.0, 9.0, len(grid))) * 4.0
    write_case("mr_smooth", curve_params,
               {"input": ramp,
                "output": _smooth_log_grid(ramp, _SMOOTH_SIGMA_OCT, _LOG_GRID_OCT_STEP)}, 1e-11)

    ref_db = np.linspace(0.0, -90.0, len(grid))
    write_case("mr_confidence_taper", curve_params,
               {"correction": ramp, "ref_power_db": ref_db,
                "output": _confidence_taper(ramp, ref_db)}, 1e-12)

    write_case("mr_band_edge_taper", curve_params,
               {"correction": ramp, "freqs": grid,
                "output": _band_edge_taper(ramp, grid)}, 1e-12)

    wide = np.linspace(-15.0, 15.0, 257)
    write_case("mr_soft_clamp", {"limit_db": 6.0, **curve_params},
               {"input": wide, "output": _soft_clamp(wide, 6.0)}, 1e-12)

    bed = _mastering_bed()
    weights = [1.0, 1.0, 1.0, 0.0]
    freqs, power = weighted_power_spectrum_arrays(
        [bed[k] for k in MASTERING_CHANNELS], weights, MASTERING_SR, 8192
    )
    write_case("mr_spectrum",
               {"channels": list(MASTERING_CHANNELS), "weights": weights,
                "n": MASTERING_N, "sample_rate": MASTERING_SR, "n_fft": 8192, "hot": False},
               {"freqs": freqs, "power": power}, 1e-11)

    reference = np.stack([
        deterministic_signal(MASTERING_N, MASTERING_SR, seed_phase=7.0) * 0.8,
        deterministic_signal(MASTERING_N, MASTERING_SR, seed_phase=9.0) * 0.8,
    ], axis=1)
    curve = compute_reference_curve(dict(bed), reference, MASTERING_SR, 8192)
    write_case("mr_curve",
               {"channels": list(MASTERING_CHANNELS), "n": MASTERING_N,
                "sample_rate": MASTERING_SR, "n_fft": 8192, "hot": False,
                "ref_seed_phases": [7.0, 9.0], "ref_scale": 0.8,
                "target_weights": [1.0, 1.0, 1.0, 0.0], "ref_weights": [1.0, 1.0],
                **curve_params},
               {"freqs": np.array([f for f, _ in curve]),
                # 1e-8, not the 1e-9 the individual curve stages hold to: the
                # bottom two octaves of the analysis sit ~7 decades below the
                # spectrum's peak, where the FFT's absolute error floor is a
                # ~1e-9 *relative* error, and 10*log10 carries that into dB.
                "gains_db": np.array([g for _, g in curve])}, 1e-8)


def dump_spatial() -> None:
    from upmixer.binaural.ambisonics import encode_gains
    from upmixer.binaural.decoder import decode_to_binaural, load_decode_filter_set
    from upmixer.binaural.profiles import DECODE_FILTER_SET, VOICING_PARAMS
    from upmixer.binaural.voicing import apply_voicing
    from upmixer.crosstalk.filters import apply_xtc, load_xtc_filter_set
    from upmixer.crosstalk.profiles import (
        VOICING_PARAMS as CROSSTALK_VOICING, XTC_FILTER_SET,
    )

    directions = [(0.0, 0.0), (0.5236, 0.0), (-2.3562, 0.0), (0.7854, 0.5236),
                  (0.0, 1.5708), (3.1416, -0.3)]
    write_case("ambi_encode", {"directions": [list(d) for d in directions]},
               {"gains": np.concatenate([encode_gains(a, e) for a, e in directions])}, 1e-14)

    sr = MASTERING_SR
    n = MASTERING_N
    left = deterministic_signal(n, sr, seed_phase=1.0) * 0.5
    right = deterministic_signal(n, sr, seed_phase=4.0) * 0.5

    for profile, params in VOICING_PARAMS.items():
        name = getattr(profile, "value", profile)
        out_l, out_r = apply_voicing(left.copy(), right.copy(), sr, params)
        write_case(
            f"voicing_{name}",
            {"n": n, "sample_rate": sr, "seed_phases": [1.0, 4.0], "scale": 0.5,
             "crossfeed_amount": params.crossfeed_amount,
             "crossfeed_cutoff_hz": params.crossfeed_cutoff_hz,
             "bass_shelf_hz": params.bass_shelf_hz,
             "bass_shelf_gain_db": params.bass_shelf_gain_db,
             "air_shelf_hz": params.air_shelf_hz,
             "air_shelf_gain_db": params.air_shelf_gain_db,
             "presence_hz": params.presence_hz,
             "presence_gain_db": params.presence_gain_db,
             "presence_q": params.presence_q,
             "stereo_widen": params.stereo_widen},
            {"left": out_l, "right": out_r},
            1e-11,
        )

    # Decode and XTC filters ship as WAV assets; the taps travel with the
    # fixture so the Rust side tests the convolution, not the file loader.
    studio = next(k for k in DECODE_FILTER_SET if getattr(k, "value", k) == "studio")
    decode = load_decode_filter_set(DECODE_FILTER_SET[studio], sr)
    hoa = np.stack([
        deterministic_signal(n, sr, seed_phase=float(i)) * (0.1 + 0.02 * i)
        for i in range(decode.taps.shape[0])
    ])
    dec_l, dec_r = decode_to_binaural(hoa, decode)
    write_case("binaural_decode",
               {"n": n, "sample_rate": sr, "n_acn": int(decode.taps.shape[0]),
                "n_taps": int(decode.taps.shape[-1])},
               {"taps": decode.taps, "left": dec_l, "right": dec_r}, 1e-9)

    stereo_xtc = next(k for k in XTC_FILTER_SET if getattr(k, "value", k) == "stereo")
    xtc = load_xtc_filter_set(XTC_FILTER_SET[stereo_xtc], sr)
    xtc_l, xtc_r = apply_xtc(left.copy(), right.copy(), xtc)
    write_case("crosstalk_xtc",
               {"n": n, "sample_rate": sr, "seed_phases": [1.0, 4.0], "scale": 0.5,
                "n_taps": int(xtc.taps.shape[-1])},
               {"taps": xtc.taps, "left": xtc_l, "right": xtc_r}, 1e-9)

    transaural = next(v for k, v in CROSSTALK_VOICING.items() if k.value == "stereo")
    tv_l, tv_r = apply_voicing(left.copy(), right.copy(), sr, transaural)
    write_case(
        "voicing_transaural_stereo",
        {"n": n, "sample_rate": sr, "seed_phases": [1.0, 4.0], "scale": 0.5,
         "crossfeed_amount": transaural.crossfeed_amount,
         "crossfeed_cutoff_hz": transaural.crossfeed_cutoff_hz,
         "bass_shelf_hz": transaural.bass_shelf_hz,
         "bass_shelf_gain_db": transaural.bass_shelf_gain_db,
         "air_shelf_hz": transaural.air_shelf_hz,
         "air_shelf_gain_db": transaural.air_shelf_gain_db,
         "presence_hz": transaural.presence_hz,
         "presence_gain_db": transaural.presence_gain_db,
         "presence_q": transaural.presence_q,
         "stereo_widen": transaural.stereo_widen},
        {"left": tv_l, "right": tv_r},
        1e-11,
    )


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in GOLDEN_DIR.glob("*"):
        stale.unlink()
    dump_generator_parity()
    dump_butter()
    dump_butter_bandpass()
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
    dump_match_reference()
    dump_spatial()
    print(f"wrote fixtures to {GOLDEN_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
