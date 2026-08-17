import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.routing.lfe import LFEExtractor


def test_lpf_mask_shape():
    """LPF mask should have correct shape."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    lfe = LFEExtractor(config, 44100, n_freq_bins)
    assert lfe.mask.shape == (n_freq_bins,)


def test_lpf_passband():
    """Frequencies well below cutoff should pass through."""
    config = UpmixConfig(auto_fft_size=False, lfe_cutoff_hz=120.0)
    n_freq_bins = 2049
    lfe = LFEExtractor(config, 44100, n_freq_bins)
    assert lfe.mask[0] > 0.99


def test_lpf_stopband():
    """Frequencies well above cutoff should be attenuated."""
    config = UpmixConfig(auto_fft_size=False, lfe_cutoff_hz=120.0, lfe_filter_order=4)
    n_freq_bins = 2049
    sr = 44100
    lfe = LFEExtractor(config, sr, n_freq_bins)

    freq_per_bin = sr / ((n_freq_bins - 1) * 2)
    bin_1000 = int(1000 / freq_per_bin)
    assert lfe.mask[bin_1000] < 0.01


def test_lpf_mask_is_linkwitz_riley_at_cutoff():
    """LR signature: half amplitude at cutoff, where Butterworth is 1/sqrt(2)."""
    config = UpmixConfig(auto_fft_size=False, lfe_cutoff_hz=120.0, lfe_filter_order=4)
    n_freq_bins = 2049
    sr = 44100
    lfe = LFEExtractor(config, sr, n_freq_bins)

    cutoff_bin = round(120.0 / (sr / ((n_freq_bins - 1) * 2)))
    assert lfe.mask[cutoff_bin] == pytest.approx(0.5, abs=0.02)


def test_odd_filter_order_is_rejected():
    with pytest.raises(ValueError):
        LFEExtractor(UpmixConfig(auto_fft_size=False, lfe_filter_order=3), 44100, 2049)


def test_lfe_extract_preserves_shape():
    """Extract should return same shape as input."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    lfe = LFEExtractor(config, 44100, n_freq_bins)

    mid = np.random.default_rng(42).standard_normal((n_freq_bins, 100)) + 0j
    result = lfe.extract(mid)
    assert result.shape == mid.shape


def test_extract_frame_matches_batch():
    """Per-frame extraction should match batch."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    lfe = LFEExtractor(config, 44100, n_freq_bins)

    rng = np.random.default_rng(42)
    mid = rng.standard_normal((n_freq_bins, 10)) + 0j

    batch_result = lfe.extract(mid)

    for i in range(10):
        frame_result = lfe.extract_frame(mid[:, i])
        np.testing.assert_allclose(frame_result, batch_result[:, i], atol=1e-14)
