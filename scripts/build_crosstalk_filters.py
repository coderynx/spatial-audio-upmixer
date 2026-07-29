#!/usr/bin/env python3
"""Generates the 2x2 crosstalk-cancellation (XTC) FIR filter sets.

Dev-only tool — not imported by production code. For each transaural
profile (stereo / smart_speaker / car), synthesizes the speaker-to-ear
acoustic transfer matrix C from the same parametric spherical-head model the
binaural HRIR decode uses (``upmixer.binaural.head_model.synth_hrir``), then
computes a frequency-dependent Tikhonov-regularized inverse H = C^H (C C^H +
beta(f) I)^-1 — the standard crosstalk-canceller design (Atal-Schroeder /
Cooper-Bauck shuffler lineage; BACCH-style frequency-dependent regularization
trades cancellation depth for bounded spectral coloration, see
``docs/standards/transaural_speakers.md`` §4). Writes each profile's 4 FIR
filters (H_LL, H_LR, H_RL, H_RR) as one 4-channel WAV file and copies the
result into ``web/public/xtc/`` so the browser preview uses byte-identical
filters.

Usage:
    python3 scripts/build_crosstalk_filters.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upmixer.binaural.head_model import synth_hrir  # noqa: E402
from upmixer.crosstalk.geometry import speaker_azimuths_rad  # noqa: E402
from upmixer.crosstalk.profiles import XTC_FILTER_SET, XTC_PARAMS, XtcParams  # noqa: E402

SAMPLE_RATE = 48_000
HRIR_TAPS = 256
N_FFT = 4096

CORE_OUT_DIR = ROOT / "upmixer" / "crosstalk" / "xtc"
WEB_OUT_DIR = ROOT / "web" / "public" / "xtc"


def _speaker_to_ear_matrix(params: XtcParams) -> np.ndarray:
    """Return the (2, 2, HRIR_TAPS) speaker->ear impulse-response matrix.

    Row = ear (0=left, 1=right), column = speaker (0=left, 1=right) — matches
    ``upmixer.crosstalk.filters.XtcFilterSet``'s H layout transposed (H is
    speaker<-ear; C is ear<-speaker, its acoustic inverse).
    """
    az_left, az_right = speaker_azimuths_rad(params)
    c_ll, c_rl = synth_hrir(az_left, 0.0, SAMPLE_RATE, HRIR_TAPS)
    c_lr, c_rr = synth_hrir(az_right, 0.0, SAMPLE_RATE, HRIR_TAPS)
    c = np.zeros((2, 2, HRIR_TAPS), dtype=np.float64)
    c[0, 0], c[0, 1] = c_ll, c_lr
    c[1, 0], c[1, 1] = c_rl, c_rr
    return c


def _beta_curve(freqs_hz: np.ndarray, params: XtcParams) -> np.ndarray:
    """Frequency-dependent regularization floor.

    Raised at low frequency (narrow-span C is near-singular there — tiny
    interaural phase difference for a small head at long wavelengths) and
    above the head-shadow onset (~8 kHz, where the head already separates
    the ears and forcing more cancellation only adds coloration for no
    perceptual gain). Flat at ``beta_mid`` in between. A documented
    heuristic curve, not a reproduction of any specific published
    regularization formula — see ``docs/standards/transaural_speakers.md``
    §4 for the honest provenance note.
    """
    beta = np.full_like(freqs_hz, params.beta_mid)
    low = freqs_hz < params.low_boost_hz
    beta[low] *= 1.0 + (params.low_boost_factor - 1.0) * (1.0 - freqs_hz[low] / params.low_boost_hz)
    high = freqs_hz > params.high_boost_hz
    span = max(float(freqs_hz[-1]) - params.high_boost_hz, 1.0)
    ramp = np.clip((freqs_hz[high] - params.high_boost_hz) / span, 0.0, 1.0)
    beta[high] *= 1.0 + (params.high_boost_factor - 1.0) * ramp
    return beta


def build_filter_set(params: XtcParams) -> np.ndarray:
    """Return the (params.taps, 4) XTC filter matrix: [H_LL, H_LR, H_RL, H_RR]."""
    c_time = _speaker_to_ear_matrix(params)
    c_freq = np.fft.rfft(c_time, n=N_FFT, axis=-1)  # (2, 2, n_bins)
    n_bins = c_freq.shape[-1]
    freqs_hz = np.fft.rfftfreq(N_FFT, d=1.0 / SAMPLE_RATE)

    c = np.moveaxis(c_freq, -1, 0)  # (n_bins, 2, 2), row=ear, col=speaker
    c_h = np.conjugate(np.transpose(c, (0, 2, 1)))  # (n_bins, 2, 2), row=speaker, col=ear
    beta = _beta_curve(freqs_hz, params)
    regularized = c @ c_h + beta[:, None, None] * np.eye(2)[None, :, :]
    # H = C^H (C C^H + beta I)^-1 — regularized inverse, row=speaker, col=ear.
    h = c_h @ np.linalg.inv(regularized)

    # Bulk delay so the (generally non-causal) inverse filter's main energy
    # lands inside a causal, finite window rather than wrapping at n=0.
    delay_samples = N_FFT // 2
    phase = np.exp(-2j * np.pi * np.arange(n_bins) * delay_samples / N_FFT)
    h_delayed = h * phase[:, None, None]

    h_time = np.fft.irfft(np.moveaxis(h_delayed, 0, -1), n=N_FFT, axis=-1)  # (2, 2, N_FFT)

    pre_taps = params.taps // 8
    start = delay_samples - pre_taps
    window = _edge_taper(params.taps)
    windowed = h_time[:, :, start:start + params.taps] * window

    out = np.zeros((params.taps, 4), dtype=np.float64)
    out[:, 0] = windowed[0, 0]  # H_LL
    out[:, 1] = windowed[0, 1]  # H_LR
    out[:, 2] = windowed[1, 0]  # H_RL
    out[:, 3] = windowed[1, 1]  # H_RR

    peak = float(np.max(np.abs(out))) or 1.0
    out *= 0.9 / peak
    return out


def _edge_taper(n_taps: int, taper_fraction: float = 0.1) -> np.ndarray:
    """Short raised-cosine taper at both ends of an otherwise-flat window."""
    taper_len = max(int(n_taps * taper_fraction), 1)
    window = np.ones(n_taps, dtype=np.float64)
    ramp = 0.5 * (1.0 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    window[:taper_len] = ramp
    window[-taper_len:] = ramp[::-1]
    return window


def write_filter_set(name: str, matrix: np.ndarray, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.wav"
    sf.write(str(path), matrix, SAMPLE_RATE, subtype="FLOAT")
    print(f"  wrote {path.relative_to(ROOT)}  ({matrix.shape[0]} taps)")


def main() -> None:
    for profile, params in XTC_PARAMS.items():
        name = XTC_FILTER_SET[profile]
        print(f"Building {name} (span={params.azimuth_left_deg - params.azimuth_right_deg:.0f}deg, beta_mid={params.beta_mid})...")
        matrix = build_filter_set(params)
        write_filter_set(name, matrix, CORE_OUT_DIR)

    WEB_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for wav in CORE_OUT_DIR.glob("*.wav"):
        shutil.copyfile(wav, WEB_OUT_DIR / wav.name)
        print(f"  copied -> {(WEB_OUT_DIR / wav.name).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
