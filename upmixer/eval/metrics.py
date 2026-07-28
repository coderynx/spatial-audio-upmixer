"""SDR, fullness, and bleedless metrics for separated stems.

SDR is the community-standard signal-to-distortion ratio (formula fixed by
convention — see ``docs/evaluation_harness.md``).  Fullness and bleedless
separate the two axes SDR conflates: how much of the true stem survives, and
how little foreign content leaks in.  Community definitions (jarredou's
metrics, the MVSEP quality checker) are conceptual only; this module commits
to a magnitude-STFT operationalization documented in
``docs/evaluation_harness.md``.  All three metrics are advisory community
metrics, not a normative standard.
"""
from __future__ import annotations

import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig

_DELTA = 1e-7
_EPS = 1e-10


def _align(reference: np.ndarray, estimate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Truncate both stems to their shared length.

    Raises:
        ValueError: If channel counts differ.
    """
    if reference.ndim != 2 or estimate.ndim != 2:
        raise ValueError("reference and estimate must be 2D (n_samples, n_channels)")
    if reference.shape[1] != estimate.shape[1]:
        raise ValueError(
            f"channel count mismatch: reference has {reference.shape[1]}, "
            f"estimate has {estimate.shape[1]}"
        )
    n = min(reference.shape[0], estimate.shape[0])
    return reference[:n], estimate[:n]


def sdr(reference: np.ndarray, estimate: np.ndarray) -> float:
    """Signal-to-distortion ratio in dB.

    Args:
        reference: True stem, shape (n_samples, n_channels).
        estimate:  Separated stem, same shape (truncated to shared length).

    Returns:
        SDR in dB. Higher is better; conflates fullness and bleed and
        under-represents HF detail and transients (energy-weighted).
    """
    ref, est = _align(reference, estimate)
    num = np.sum(np.square(ref)) + _DELTA
    den = np.sum(np.square(ref - est)) + _DELTA
    return 10.0 * np.log10(num / den)


def _magnitude_spectrogram(signal: np.ndarray, sample_rate: int) -> np.ndarray:
    """Sum-of-channels magnitude STFT, shape (n_freq_bins, n_frames)."""
    analyzer = STFTAnalyzer(UpmixConfig(), sample_rate)
    mono = signal.mean(axis=1)
    return np.abs(analyzer.forward(mono))


def fullness(reference: np.ndarray, estimate: np.ndarray, sample_rate: int) -> float:
    """Fraction of the true stem's spectral content retained in the estimate.

    ``Σ min(|R|, |E|) / Σ |R|`` over the magnitude STFT, clipped to [0, 1].
    1.0 means every bin of the reference is at least matched by the estimate;
    values below 1.0 indicate attenuated or missing target content.
    """
    ref, est = _align(reference, estimate)
    r = _magnitude_spectrogram(ref, sample_rate)
    e = _magnitude_spectrogram(est, sample_rate)
    retained = np.sum(np.minimum(r, e))
    total = np.sum(r) + _EPS
    return float(np.clip(retained / total, 0.0, 1.0))


def bleedless(reference: np.ndarray, estimate: np.ndarray, sample_rate: int) -> float:
    """Fraction of the estimate that is not foreign (bled-in) content.

    ``Σ min(|R|, |E|) / Σ |E|`` over the magnitude STFT, clipped to [0, 1].
    1.0 means the estimate contains no energy beyond what the reference has;
    values below 1.0 indicate cross-stem bleed or added noise.
    """
    ref, est = _align(reference, estimate)
    r = _magnitude_spectrogram(ref, sample_rate)
    e = _magnitude_spectrogram(est, sample_rate)
    retained = np.sum(np.minimum(r, e))
    total = np.sum(e) + _EPS
    return float(np.clip(retained / total, 0.0, 1.0))
