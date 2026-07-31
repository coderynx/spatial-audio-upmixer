"""Phase-fixer bleed reduction (Aufr33's method).

Keeps a target instrumental's magnitude but swaps in a bleedless reference's
phase inside a limited frequency band, reducing vocal residue at the cost of a
little fullness. Pure STFT-domain DSP — no inference. The reference is the
instrumental output of a vocal-target model; producing it is the caller's job.
"""
from __future__ import annotations

import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig


def _unit(spec: np.ndarray) -> np.ndarray:
    """Unit-magnitude phasors of a spectrum; zero where the bin is silent."""
    mag = np.abs(spec)
    return np.divide(spec, mag, out=np.zeros_like(spec), where=mag > 1e-12)


def _fix_spectrum(
    spec_t: np.ndarray, spec_r: np.ndarray, band: np.ndarray, scale: float
) -> np.ndarray:
    """Target magnitude with reference phase blended in over *band* bins.

    ``np.abs(result)`` equals ``np.abs(spec_t)`` at every bin; the phase is moved
    toward the reference only where ``band`` is True.
    """
    phase = np.angle(spec_t)
    blended = (1.0 - scale) * _unit(spec_t) + scale * _unit(spec_r)
    phase[band, :] = np.angle(blended)[band, :]
    return np.abs(spec_t) * np.exp(1j * phase)


def apply_phase_fix(
    target: np.ndarray,
    reference: np.ndarray,
    sample_rate: int,
    low_hz: float,
    high_hz: float,
    scale: float,
) -> np.ndarray:
    """Blend reference phase into *target* over ``[low_hz, high_hz]``.

    The target magnitude is preserved at every bin; only the phase inside the
    band is moved toward the reference, weighted by *scale* (``0`` is a no-op,
    ``1`` takes the reference phase fully within the band). Both arrays are
    ``(n_samples, channels)`` float and are aligned at sample 0 and clipped to
    the shorter length. Returns an array of the same shape/dtype as *target*.
    """
    if not 0.0 <= scale <= 1.0:
        raise ValueError("phase-fix scale must be between 0.0 and 1.0")
    if not 0.0 < low_hz < high_hz:
        raise ValueError("phase-fix requires 0 < low_hz < high_hz")

    original = np.asarray(target)
    if original.ndim != 2 or np.asarray(reference).ndim != 2:
        raise ValueError("target and reference must be 2-D (n_samples, channels)")
    if scale == 0.0:
        return original

    tgt = original.astype(np.float64, copy=False)
    ref = np.asarray(reference, dtype=np.float64)
    n = min(len(tgt), len(ref))
    if n == 0:
        return original

    analyzer = STFTAnalyzer(UpmixConfig(), sample_rate)
    band = (analyzer.freq_bins >= low_hz) & (analyzer.freq_bins <= high_hz)

    out = tgt.copy()
    for ch in range(tgt.shape[1]):
        ref_ch = ref[:n, ch] if ch < ref.shape[1] else ref[:n, 0]
        spec_t = analyzer.forward(tgt[:n, ch])
        spec_r = analyzer.forward(ref_ch)
        fixed = _fix_spectrum(spec_t, spec_r, band, scale)
        out[:n, ch] = analyzer.inverse(fixed, n)
    return out.astype(original.dtype, copy=False)
