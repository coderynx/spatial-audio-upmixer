#!/usr/bin/env python3
"""Generates the 2x2 crosstalk-cancellation (XTC) FIR filter sets.

Dev-only tool — not imported by production code. For each transaural profile,
synthesizes the speaker-to-ear acoustic transfer matrix C from the same
parametric spherical-head model the binaural HRIR decode uses
(``upmixer.binaural.head_model.synth_hrir``), then computes a
frequency-dependent Tikhonov-regularized inverse H = C^H (C C^H + beta(f)
I)^-1 — the standard crosstalk-canceller design (Atal-Schroeder /
Cooper-Bauck shuffler lineage), with beta(f) set per bin to Choueiri's
optimal frequency-dependent prescription: the least regularization that holds
spectral coloration at the speakers under the profile's budget. Cancellation
blends to identity outside the profile's active band. See
``docs/standards/transaural_speakers.md`` §4. Writes each profile's 4 FIR
filters (H_LL, H_LR, H_RL, H_RR) as one 4-channel WAV file and copies the
result into ``apps/web/public/xtc/`` so the browser preview uses
byte-identical filters.

Usage:
    uv run python scripts/build_crosstalk_filters.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from upmixer.binaural.head_model import synth_hrir
from upmixer.crosstalk.geometry import speaker_azimuths_rad
from upmixer.crosstalk.profiles import XTC_FILTER_SET, XTC_PARAMS, XtcParams

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 48_000
HRIR_TAPS = 256
N_FFT = 4096

CORE_OUT_DIR = ROOT / "packages" / "core" / "src" / "crosstalk" / "xtc"
WEB_OUT_DIR = ROOT / "apps" / "web" / "public" / "xtc"


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


def _gamma_capped_beta(c: np.ndarray, gamma_db: float) -> np.ndarray:
    """Smallest per-bin regularization holding ``||H(f)|| <= 10^(gamma_db/20)``.

    Choueiri's frequency-dependent prescription: rather than a fixed
    regularization floor, spend exactly enough per bin to cap the coloration
    envelope at the desired level and leave the rest of the spectrum
    unregularized (the "perfect filter" branch). For a singular value ``s`` of
    ``C``, the regularized filter's gain along that axis is ``s / (s^2 +
    beta)``, so capping it at ``gamma`` needs ``beta >= s/gamma - s^2``. Both
    singular values must clear the cap, not just the smaller one.
    """
    gamma = 10.0 ** (gamma_db / 20.0)
    sv = np.linalg.svd(c, compute_uv=False)
    return np.max(np.maximum(sv / gamma - sv**2, 0.0), axis=-1)


def _xtc_band_weight(freqs_hz: np.ndarray, params: XtcParams) -> np.ndarray:
    """Raised-cosine weight fading cancellation in over the active band.

    Outside the band the filter blends to identity: below ``xtc_lo_hz`` there
    are no usable localization cues to protect, above ``xtc_hi_hz`` the head
    already separates the ears and cancellation only shrinks the sweet spot
    (docs/standards/transaural_speakers.md §4.3).
    """
    weight = np.ones_like(freqs_hz)
    lo_ramp = (freqs_hz >= params.xtc_lo_hz) & (freqs_hz < 2.0 * params.xtc_lo_hz)
    weight[freqs_hz < params.xtc_lo_hz] = 0.0
    weight[lo_ramp] = 0.5 * (
        1.0 - np.cos(np.pi * (freqs_hz[lo_ramp] - params.xtc_lo_hz) / params.xtc_lo_hz)
    )
    hi_stop = 1.5 * params.xtc_hi_hz
    hi_ramp = (freqs_hz > params.xtc_hi_hz) & (freqs_hz <= hi_stop)
    weight[freqs_hz > hi_stop] = 0.0
    weight[hi_ramp] = 0.5 * (
        1.0 + np.cos(np.pi * (freqs_hz[hi_ramp] - params.xtc_hi_hz) / (hi_stop - params.xtc_hi_hz))
    )
    return weight


def build_filter_set(params: XtcParams) -> np.ndarray:
    """Return the (params.taps, 4) XTC filter matrix: [H_LL, H_LR, H_RL, H_RR]."""
    c_time = _speaker_to_ear_matrix(params)
    c_freq = np.fft.rfft(c_time, n=N_FFT, axis=-1)  # (2, 2, n_bins)
    n_bins = c_freq.shape[-1]
    freqs_hz = np.fft.rfftfreq(N_FFT, d=1.0 / SAMPLE_RATE)

    c = np.moveaxis(c_freq, -1, 0)  # (n_bins, 2, 2), row=ear, col=speaker
    c_h = np.conjugate(np.transpose(c, (0, 2, 1)))  # (n_bins, 2, 2), row=speaker, col=ear
    beta = _gamma_capped_beta(c, params.gamma_db)
    regularized = c @ c_h + beta[:, None, None] * np.eye(2)[None, :, :]
    # H = C^H (C C^H + beta I)^-1 — regularized inverse, row=speaker, col=ear.
    h = c_h @ np.linalg.inv(regularized)

    weight = _xtc_band_weight(freqs_hz, params)[:, None, None]
    h = weight * h + (1.0 - weight) * np.eye(2)[None, :, :]

    # Bulk delay so the (generally non-causal) inverse filter's main energy
    # lands inside a causal, finite window rather than wrapping at n=0. Both
    # blend branches share this one delay, so the crossover cannot comb.
    delay_samples = N_FFT // 2
    phase = np.exp(-2j * np.pi * np.arange(n_bins) * delay_samples / N_FFT)
    h_delayed = h * phase[:, None, None]

    h_time = np.fft.irfft(np.moveaxis(h_delayed, 0, -1), n=N_FFT, axis=-1)  # (2, 2, N_FFT)

    start = delay_samples - params.taps // 2
    window = _edge_taper(params.taps)
    windowed = h_time[:, :, start:start + params.taps] * window

    out = np.zeros((params.taps, 4), dtype=np.float64)
    out[:, 0] = windowed[0, 0]  # H_LL
    out[:, 1] = windowed[0, 1]  # H_LR
    out[:, 2] = windowed[1, 0]  # H_RL
    out[:, 3] = windowed[1, 1]  # H_RR

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
        print(f"Building {name} (span={params.azimuth_left_deg - params.azimuth_right_deg:.0f}deg, gamma={params.gamma_db}dB)...")
        matrix = build_filter_set(params)
        write_filter_set(name, matrix, CORE_OUT_DIR)

    WEB_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for wav in CORE_OUT_DIR.glob("*.wav"):
        shutil.copyfile(wav, WEB_OUT_DIR / wav.name)
        print(f"  copied -> {(WEB_OUT_DIR / wav.name).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
