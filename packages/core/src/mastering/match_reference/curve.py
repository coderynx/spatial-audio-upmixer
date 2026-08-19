"""The reference-matching correction curve: a single, strength/max_db-
independent difference curve between a target bed and a reference file, plus
the cheap per-request step that scales, clamps, and realizes it as a FIR.

Algorithm (see ``docs/contracts/preview_export_parity.md`` §3 for the
contracted constants):

1. BS.1770-weighted, gated power spectra of target and reference
   (:mod:`.spectrum`).
2. Both resampled onto a shared 1/24-octave log grid, 20 Hz-20 kHz, so the
   ratio compares like-for-like frequencies regardless of FFT bin spacing.
3. ``correction_db = 10*log10(ref/target)``.
4. Mean-subtracted over 100 Hz-10 kHz *on the log grid*, so the offset is an
   equal-per-octave average rather than biased toward the top of the band by
   linear bin density.
5. Tapered to 0 dB where the reference has little energy relative to its own
   peak (protects against extrapolating a curve from noise-floor content,
   e.g. a lossy-sourced reference's brickwall) and hard-tapered to 0 dB
   below 25 Hz / above 18 kHz.

Steps 1-5 depend on no user control — the raw grid curve is what gets
persisted. :func:`build_curve_fir` owns every control (strength, smoothing
bandwidth, frequency-range masks, the two clamps), so moving one re-designs
the FIR without re-running the analysis.
"""
from __future__ import annotations

import numpy as np
import upmixer_dsp

from ..eq import _build_fir_from_breakpoints
from .spectrum import weighted_power_spectrum, weighted_power_spectrum_reference

_EPS: float = 1e-20

_MIN_FREQ_HZ: float = 20.0
_MAX_FREQ_HZ: float = 20000.0

_LOG_GRID_OCT_STEP: float = 1.0 / 24.0

SMOOTH_OCT_DEFAULT: float = 1.0 / 3.0
SMOOTH_OCT_MIN: float = 1.0 / 12.0
SMOOTH_OCT_MAX: float = 1.0
"""Realization-time smoothing bandwidth, in octaves. Coarse matches tonal
balance, fine chases the reference's own resonances. Served to the web (see
``docs/contracts/preview_export_parity.md`` §2)."""

_MASK_EASE_OCT: float = 0.5

_NORM_LOW_HZ: float = 100.0
_NORM_HIGH_HZ: float = 10000.0

_CONFIDENCE_FLOOR_DB: float = 40.0
_TAPER_LOW_HZ: tuple[float, float] = (20.0, 25.0)
_TAPER_HIGH_HZ: tuple[float, float] = (18000.0, 20000.0)

_BASS_CLAMP_HZ: float = 120.0
_BASS_CLAMP_DB: float = 2.0
_CLAMP_KNEE_DB: float = 2.0


def _as_f64(values: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64)


def _log_grid(high_hz: float) -> np.ndarray:
    return upmixer_dsp.log_grid(high_hz, _MIN_FREQ_HZ, _LOG_GRID_OCT_STEP)


def _smooth_log_grid(values: np.ndarray, sigma_oct: float, step_oct: float) -> np.ndarray:
    """Gaussian smoothing on a grid uniform in log-frequency, so ``sigma_oct``
    is the true smoothing width in octaves (unlike the linear-FFT-grid bug
    this replaces — see module docstring)."""
    return upmixer_dsp.smooth_log_grid(_as_f64(values), sigma_oct, step_oct)


def _confidence_taper(correction_db: np.ndarray, ref_power_db: np.ndarray, floor_db: float = _CONFIDENCE_FLOOR_DB) -> np.ndarray:
    """Fade correction to 0 dB where the reference sits more than ~floor_db
    below its own broadband peak — guards against extrapolating a curve from
    near-nothing (e.g. a 16 kHz-brickwalled, lossy-sourced reference)."""
    return upmixer_dsp.confidence_taper(
        _as_f64(correction_db), _as_f64(ref_power_db), floor_db
    )


def _band_edge_taper(
    correction_db: np.ndarray,
    freqs: np.ndarray,
    low_hz: tuple[float, float] = _TAPER_LOW_HZ,
    high_hz: tuple[float, float] = _TAPER_HIGH_HZ,
) -> np.ndarray:
    """Hard-taper to 0 dB outside the band the analysis trusts, regardless of
    reference content."""
    return upmixer_dsp.band_edge_taper(
        _as_f64(correction_db), _as_f64(freqs), low_hz, high_hz
    )


def _soft_clamp(db: np.ndarray, limit_db: float, knee_db: float = _CLAMP_KNEE_DB) -> np.ndarray:
    """Clamp ``|db|`` to ``limit_db`` with a soft knee starting ``knee_db``
    below the limit, so the curve doesn't develop a hard corner at the
    ceiling."""
    return upmixer_dsp.soft_clamp(_as_f64(db), limit_db, knee_db)


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
    freqs_t, power_t = weighted_power_spectrum(target_channels, sample_rate, n_fft, lfe_key)
    freqs_r, power_r = weighted_power_spectrum_reference(reference_data, sample_rate, n_fft)

    return upmixer_dsp.correction_curve(
        _as_f64(freqs_t), _as_f64(power_t), _as_f64(freqs_r), _as_f64(power_r),
        sample_rate,
        _MIN_FREQ_HZ, _MAX_FREQ_HZ, _LOG_GRID_OCT_STEP,
        _NORM_LOW_HZ, _NORM_HIGH_HZ, _CONFIDENCE_FLOOR_DB,
        _TAPER_LOW_HZ, _TAPER_HIGH_HZ,
    )


def realize_curve(
    curve: list[tuple[float, float]],
    strength: float,
    max_correction_db: float,
    smooth_octaves: float | None = None,
    low_hz: float | None = None,
    high_hz: float | None = None,
) -> np.ndarray:
    """The persisted curve's gains at one set of user controls, in dB."""
    freqs = np.array([f for f, _ in curve], dtype=np.float64)
    gains_db = np.array([g for _, g in curve], dtype=np.float64)
    smooth = SMOOTH_OCT_DEFAULT if smooth_octaves is None else float(smooth_octaves)
    return upmixer_dsp.realize_curve(
        freqs, gains_db,
        float(strength), float(max_correction_db), _CLAMP_KNEE_DB,
        float(np.clip(smooth, SMOOTH_OCT_MIN, SMOOTH_OCT_MAX)), _LOG_GRID_OCT_STEP,
        0.0 if low_hz is None else float(low_hz),
        0.0 if high_hz is None else float(high_hz),
        _MASK_EASE_OCT, _BASS_CLAMP_HZ, _BASS_CLAMP_DB,
    )


def build_curve_fir(
    curve: list[tuple[float, float]],
    sample_rate: int,
    n_taps: int,
    strength: float,
    max_correction_db: float,
    smooth_octaves: float | None = None,
    low_hz: float | None = None,
    high_hz: float | None = None,
) -> np.ndarray:
    """Design the minimum-phase correction FIR for one set of user controls
    from a persisted, control-independent ``curve`` (see
    :func:`compute_reference_curve`).

    Cheap: no spectral analysis, just the dB-domain realization
    (:func:`realize_curve`) and ``firwin2``/``minimum_phase`` (memoized by
    ``eq._build_fir_from_breakpoints``'s cache). ``strength`` scales the
    curve in the dB domain rather than crossfading an undelayed dry signal
    against a minimum-phase-delayed wet one — the latter combs at partial
    strength, since minimum-phase group delay is frequency-dependent.

    ``smooth_octaves`` is the Gaussian smoothing bandwidth (``None`` = the
    1/3-octave default); ``low_hz``/``high_hz`` restrict the range the curve
    acts on, easing to unity outside it (``None`` = full range).
    """
    if not curve:
        raise ValueError("build_curve_fir: curve is empty")
    gains_db = realize_curve(curve, strength, max_correction_db, smooth_octaves, low_hz, high_hz)
    breakpoints = [(float(f), float(g)) for (f, _), g in zip(curve, gains_db)]
    return _build_fir_from_breakpoints(breakpoints, sample_rate, n_taps)
