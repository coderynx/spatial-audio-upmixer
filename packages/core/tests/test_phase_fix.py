"""Phase-fixer DSP: magnitude preserved, phase swapped only within the band."""
from __future__ import annotations

import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig
from upmixer.separation.phase_fix import _fix_spectrum, apply_phase_fix

SR = 48_000


def _analyzer() -> STFTAnalyzer:
    return STFTAnalyzer(UpmixConfig(), SR)


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n) * 0.1


def _stereo(n: int, seed: int) -> np.ndarray:
    return np.column_stack([_noise(n, seed), _noise(n, seed + 100)])


def _wrap(delta: np.ndarray) -> np.ndarray:
    return np.angle(np.exp(1j * delta))


def test_fix_spectrum_preserves_magnitude_everywhere():
    an = _analyzer()
    spec_t = an.forward(_noise(20_000, 1))
    spec_r = an.forward(_noise(20_000, 2))
    band = (an.freq_bins >= 500) & (an.freq_bins <= 5000)

    fixed = _fix_spectrum(spec_t, spec_r, band, 0.8)

    assert np.allclose(np.abs(fixed), np.abs(spec_t))


def test_fix_spectrum_changes_phase_only_in_band():
    an = _analyzer()
    spec_t = an.forward(_noise(20_000, 3))
    spec_r = an.forward(_noise(20_000, 4))
    band = (an.freq_bins >= 500) & (an.freq_bins <= 5000)

    fixed = _fix_spectrum(spec_t, spec_r, band, 0.8)

    outside = _wrap(np.angle(fixed)[~band] - np.angle(spec_t)[~band])
    assert np.max(np.abs(outside)) < 1e-12
    inside = _wrap(np.angle(fixed)[band] - np.angle(spec_t)[band])
    assert np.max(np.abs(inside)) > 0.1


def test_fix_spectrum_scale_zero_is_noop():
    an = _analyzer()
    spec_t = an.forward(_noise(8_000, 5))
    spec_r = an.forward(_noise(8_000, 6))
    band = (an.freq_bins >= 500) & (an.freq_bins <= 5000)

    assert np.allclose(_fix_spectrum(spec_t, spec_r, band, 0.0), spec_t)


def test_fix_spectrum_scale_one_takes_reference_phase_in_band():
    an = _analyzer()
    spec_t = an.forward(_noise(8_000, 7))
    spec_r = an.forward(_noise(8_000, 8))
    band = (an.freq_bins >= 500) & (an.freq_bins <= 5000)

    fixed = _fix_spectrum(spec_t, spec_r, band, 1.0)

    mask = band[:, None] & (np.abs(spec_r) > 1e-9)
    diff = _wrap(np.angle(fixed) - np.angle(spec_r))
    assert np.max(np.abs(diff[mask])) < 1e-9


def test_apply_phase_fix_preserves_shape_and_dtype():
    tgt = _stereo(16_000, 1).astype(np.float32)
    ref = _stereo(16_000, 2).astype(np.float32)

    out = apply_phase_fix(tgt, ref, SR, 500, 5000, 0.8)

    assert out.shape == tgt.shape
    assert out.dtype == tgt.dtype
    assert not np.array_equal(out, tgt)


def test_apply_phase_fix_scale_zero_returns_target():
    tgt = _stereo(8_000, 3).astype(np.float32)
    ref = _stereo(8_000, 4).astype(np.float32)

    assert np.array_equal(apply_phase_fix(tgt, ref, SR, 500, 5000, 0.0), tgt)


def test_apply_phase_fix_difference_is_band_limited():
    n = 32_000
    tgt = _stereo(n, 5)
    ref = _stereo(n, 6)

    out = apply_phase_fix(tgt, ref, SR, 500, 5000, 1.0)

    an = _analyzer()
    diff = an.forward((tgt - out)[:, 0])
    band = (an.freq_bins >= 500) & (an.freq_bins <= 5000)
    energy_in = float(np.sum(np.abs(diff[band]) ** 2))
    energy_out = float(np.sum(np.abs(diff[~band]) ** 2))
    assert energy_in > 0.0
    assert energy_out < 0.1 * energy_in


def test_apply_phase_fix_rejects_bad_band():
    tgt = _stereo(4_000, 7)
    ref = _stereo(4_000, 8)
    for low, high in ((0.0, 5000.0), (5000.0, 500.0)):
        try:
            apply_phase_fix(tgt, ref, SR, low, high, 0.8)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for band ({low}, {high})")
