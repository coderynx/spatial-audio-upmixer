import numpy as np

from upmixer.config import UpmixConfig
from upmixer.routing.decorrelator import Decorrelator


def test_allpass_preserves_magnitude():
    """Decorrelation should preserve magnitude spectrum."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    decorr = Decorrelator(config, n_freq_bins)

    rng = np.random.default_rng(42)
    spec = rng.standard_normal((n_freq_bins, 50)) + 1j * rng.standard_normal(
        (n_freq_bins, 50)
    )

    result = decorr.apply(spec, filter_index=0)
    np.testing.assert_allclose(np.abs(result), np.abs(spec), atol=1e-12)


def test_different_filters_produce_different_output():
    """Different filter indices should produce different phase shifts."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    decorr = Decorrelator(config, n_freq_bins)

    rng = np.random.default_rng(42)
    spec = rng.standard_normal((n_freq_bins, 50)) + 1j * rng.standard_normal(
        (n_freq_bins, 50)
    )

    r0 = decorr.apply(spec, filter_index=0)
    r1 = decorr.apply(spec, filter_index=1)

    assert not np.allclose(r0, r1)
    np.testing.assert_allclose(np.abs(r0), np.abs(r1), atol=1e-12)


def test_eight_filters_available():
    """Should have 8 filters for SL, SR, BL, BR, TFL, TFR, TBL, TBR."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    decorr = Decorrelator(config, n_freq_bins)

    rng = np.random.default_rng(42)
    frame = rng.standard_normal(n_freq_bins) + 1j * rng.standard_normal(n_freq_bins)

    for i in range(8):
        result = decorr.apply_frame(frame, filter_index=i)
        np.testing.assert_allclose(np.abs(result), np.abs(frame), atol=1e-12)


def test_apply_frame_matches_batch():
    """Per-frame application should match batch."""
    config = UpmixConfig(auto_fft_size=False)
    n_freq_bins = config.fft_size // 2 + 1
    decorr = Decorrelator(config, n_freq_bins)

    rng = np.random.default_rng(42)
    spec = rng.standard_normal((n_freq_bins, 10)) + 1j * rng.standard_normal(
        (n_freq_bins, 10)
    )

    batch_result = decorr.apply(spec, filter_index=0)

    for i in range(10):
        frame_result = decorr.apply_frame(spec[:, i], filter_index=0)
        np.testing.assert_allclose(frame_result, batch_result[:, i], atol=1e-14)
