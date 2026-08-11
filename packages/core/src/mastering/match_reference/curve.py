"""The reference-matching correction curve: a single, strength/max_db-
independent difference curve between a target bed and a reference file, plus
the cheap per-request step that scales, clamps, and realizes it as a FIR.

Algorithm (see ``docs/contracts/preview_export_parity.md`` §3 for the
contracted constants):

1. BS.1770-weighted, gated power spectra of target and reference
   (:mod:`.spectrum`).
2. Both resampled onto a shared 1/24-octave log grid, 20 Hz-20 kHz, so the
   ratio compares like-for-like frequencies regardless of FFT bin spacing.
3. ``correction_db = 10*log10(ref/target)``, smoothed 1/24-oct grid at a
   real 1/3-octave Gaussian width (fixes a prior bug where the smoothing
   kernel's width was measured from a linear FFT grid and collapsed to a
   near-identity three-tap kernel).
4. Mean-subtracted over 100 Hz-10 kHz *on the log grid*, so the offset is an
   equal-per-octave average rather than biased toward the top of the band by
   linear bin density.
5. Tapered to 0 dB where the reference has little energy relative to its own
   peak (protects against extrapolating a curve from noise-floor content,
   e.g. a lossy-sourced reference's brickwall) and hard-tapered to 0 dB
   below 25 Hz / above 18 kHz.
6. Decimated to log-spaced breakpoints for FIR design.

Steps 1-6 don't depend on ``strength`` or ``max_correction_db`` — the result
is what gets persisted. :func:`build_curve_fir` applies both per request.
"""
from __future__ import annotations

import numpy as np

from ..eq import _build_fir_from_breakpoints
from .spectrum import weighted_power_spectrum, weighted_power_spectrum_reference

_EPS: float = 1e-20

_N_BREAKPOINTS: int = 64
_MIN_FREQ_HZ: float = 20.0
_MAX_FREQ_HZ: float = 20000.0

_LOG_GRID_OCT_STEP: float = 1.0 / 24.0
_SMOOTH_SIGMA_OCT: float = 1.0 / 3.0

_NORM_LOW_HZ: float = 100.0
_NORM_HIGH_HZ: float = 10000.0

_CONFIDENCE_FLOOR_DB: float = 40.0
_TAPER_LOW_HZ: tuple[float, float] = (20.0, 25.0)
_TAPER_HIGH_HZ: tuple[float, float] = (18000.0, 20000.0)

_BASS_CLAMP_HZ: float = 120.0
_BASS_CLAMP_DB: float = 2.0
_CLAMP_KNEE_DB: float = 2.0


def _log_grid(high_hz: float) -> np.ndarray:
    lo = np.log2(_MIN_FREQ_HZ)
    hi = np.log2(max(high_hz, _MIN_FREQ_HZ * 2))
    n = max(int(round((hi - lo) / _LOG_GRID_OCT_STEP)) + 1, 2)
    return 2.0 ** np.linspace(lo, hi, n)


def _smooth_log_grid(values: np.ndarray, sigma_oct: float, step_oct: float) -> np.ndarray:
    """Gaussian smoothing on a grid uniform in log-frequency, so ``sigma_oct``
    is the true smoothing width in octaves (unlike the linear-FFT-grid bug
    this replaces — see module docstring)."""
    sigma_bins = sigma_oct / step_oct
    half_w = int(3 * sigma_bins) + 1
    kernel_idx = np.arange(-half_w, half_w + 1, dtype=float)
    kernel = np.exp(-0.5 * (kernel_idx / sigma_bins) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(values, half_w, mode="reflect")
    return np.convolve(padded, kernel, mode="valid")


def _confidence_taper(correction_db: np.ndarray, ref_power_db: np.ndarray, floor_db: float = _CONFIDENCE_FLOOR_DB) -> np.ndarray:
    """Fade correction to 0 dB where the reference sits more than ~floor_db
    below its own broadband peak — guards against extrapolating a curve from
    near-nothing (e.g. a 16 kHz-brickwalled, lossy-sourced reference)."""
    peak = float(np.max(ref_power_db))
    deficit = (peak - floor_db) - ref_power_db
    confidence = np.clip(1.0 - deficit / floor_db, 0.0, 1.0)
    return correction_db * confidence


def _band_edge_taper(
    correction_db: np.ndarray,
    freqs: np.ndarray,
    low_hz: tuple[float, float] = _TAPER_LOW_HZ,
    high_hz: tuple[float, float] = _TAPER_HIGH_HZ,
) -> np.ndarray:
    """Hard-taper to 0 dB outside the band the analysis trusts, regardless of
    reference content."""
    taper = np.ones_like(correction_db)
    lo0, lo1 = low_hz
    hi0, hi1 = high_hz
    below = freqs < lo1
    taper[below] = np.clip((freqs[below] - lo0) / (lo1 - lo0), 0.0, 1.0)
    above = freqs > hi0
    taper[above] = np.clip((hi1 - freqs[above]) / (hi1 - hi0), 0.0, 1.0)
    return correction_db * taper


def _soft_clamp(db: np.ndarray, limit_db: float, knee_db: float = _CLAMP_KNEE_DB) -> np.ndarray:
    """Clamp ``|db|`` to ``limit_db`` with a soft knee starting ``knee_db``
    below the limit, so the curve doesn't develop a hard corner at the
    ceiling."""
    if limit_db <= 0:
        return np.zeros_like(db)
    knee_start = max(limit_db - knee_db, 0.0)
    knee_width = max(limit_db - knee_start, 1e-6)
    sign = np.sign(db)
    mag = np.abs(db)
    over = np.clip(mag - knee_start, 0.0, None)
    compressed = knee_start + knee_width * np.tanh(over / knee_width)
    return sign * np.where(mag > knee_start, compressed, mag)


def compute_reference_curve(
    target_channels: dict[str, np.ndarray],
    reference_data: np.ndarray,
    sample_rate: int,
    n_fft: int,
    lfe_key: str = "LFE",
) -> list[tuple[float, float]]:
    """The strength/max_db-independent reference-matching correction curve.

    ``target_channels`` should already be level-matched to the reference
    (see ``processor.ReferenceMatchProcessor.compute_curve``, which applies
    the RMS/loudness gain before calling this) — computing the ratio at
    mismatched levels would leave a residual broadband offset baked into the
    per-band curve instead of cleanly separated.

    Returns ``(freq_hz, gain_db)`` breakpoints, unclamped and unscaled.
    """
    nyquist = sample_rate / 2.0
    freqs_t, power_t = weighted_power_spectrum(target_channels, sample_rate, n_fft, lfe_key)
    freqs_r, power_r = weighted_power_spectrum_reference(reference_data, sample_rate, n_fft)

    grid = _log_grid(min(_MAX_FREQ_HZ, nyquist))
    log_grid = np.log2(grid)

    power_t_grid = np.interp(log_grid, np.log2(freqs_t), power_t)
    power_r_grid = np.interp(log_grid, np.log2(freqs_r), power_r)

    correction_db = 10.0 * np.log10((power_r_grid + _EPS) / (power_t_grid + _EPS))
    correction_db = _smooth_log_grid(correction_db, _SMOOTH_SIGMA_OCT, _LOG_GRID_OCT_STEP)

    norm_mask = (grid >= _NORM_LOW_HZ) & (grid <= _NORM_HIGH_HZ)
    if norm_mask.any():
        correction_db = correction_db - float(correction_db[norm_mask].mean())

    ref_power_db = 10.0 * np.log10(power_r_grid + _EPS)
    correction_db = _confidence_taper(correction_db, ref_power_db)
    correction_db = _band_edge_taper(correction_db, grid)

    bp_high = min(_MAX_FREQ_HZ, nyquist)
    bp_freqs = np.logspace(np.log10(_MIN_FREQ_HZ), np.log10(bp_high), num=_N_BREAKPOINTS)
    bp_gains = np.interp(np.log2(bp_freqs), log_grid, correction_db)
    return [(float(f), float(g)) for f, g in zip(bp_freqs, bp_gains)]


def build_curve_fir(
    curve: list[tuple[float, float]],
    sample_rate: int,
    n_taps: int,
    strength: float,
    max_correction_db: float,
) -> np.ndarray:
    """Design the minimum-phase correction FIR for one ``(strength,
    max_correction_db)`` pair from a persisted, strength-independent
    ``curve`` (see :func:`compute_reference_curve`).

    Cheap: no spectral analysis, just dB scaling, clamping, and
    ``firwin2``/``minimum_phase`` (memoized by
    ``eq._build_fir_from_breakpoints``'s cache). ``strength`` scales the
    curve in the dB domain rather than crossfading an undelayed dry signal
    against a minimum-phase-delayed wet one — the latter combs at partial
    strength, since minimum-phase group delay is frequency-dependent.
    """
    if not curve:
        raise ValueError("build_curve_fir: curve is empty")
    freqs = np.array([f for f, _ in curve], dtype=np.float64)
    gains_db = np.array([g for _, g in curve], dtype=np.float64) * float(strength)
    gains_db = _soft_clamp(gains_db, float(max_correction_db))
    bass = freqs < _BASS_CLAMP_HZ
    gains_db[bass] = np.clip(gains_db[bass], -_BASS_CLAMP_DB, _BASS_CLAMP_DB)
    breakpoints = [(float(f), float(g)) for f, g in zip(freqs, gains_db)]
    return _build_fir_from_breakpoints(breakpoints, sample_rate, n_taps)
