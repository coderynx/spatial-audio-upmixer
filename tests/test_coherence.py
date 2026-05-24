import numpy as np

from upmixer.analysis.coherence import CoherenceEstimator
from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig


def test_identical_signals_high_coherence(sample_rate):
    """Identical signals (L == R) should have coherence close to 1."""
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)
    coherence_est = CoherenceEstimator(config)

    t = np.arange(sample_rate) / sample_rate
    signal = np.sin(2 * np.pi * 440 * t)

    X_L = stft.forward(signal)
    X_R = stft.forward(signal)

    gamma = coherence_est.estimate(X_L, X_R)

    energy = np.abs(X_L) ** 2 + np.abs(X_R) ** 2
    active_bins = energy > np.max(energy) * 0.01
    assert np.mean(gamma[active_bins]) > 0.9


def test_uncorrelated_noise_low_coherence(sample_rate):
    """Uncorrelated noise should have lower coherence than correlated signals.

    Adaptive EMA (alpha_attack=0.25, alpha_release=0.75) produces a steady-state
    coherence of ~0.6 for uncorrelated noise — significantly below the >0.9
    seen for identical signals.
    """
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)
    coherence_est = CoherenceEstimator(config)

    rng = np.random.default_rng(42)
    left = rng.standard_normal(sample_rate * 2)
    right = rng.standard_normal(sample_rate * 2)

    X_L = stft.forward(left)
    X_R = stft.forward(right)

    gamma = coherence_est.estimate(X_L, X_R)

    n_frames = gamma.shape[1]
    later_half = gamma[:, n_frames // 2 :]
    assert np.mean(later_half) < 0.70


def test_coherence_range():
    """Coherence values should be in [0, 1]."""
    config = UpmixConfig(auto_fft_size=False)
    coherence_est = CoherenceEstimator(config)

    rng = np.random.default_rng(42)
    X_L = rng.standard_normal((100, 50)) + 1j * rng.standard_normal((100, 50))
    X_R = rng.standard_normal((100, 50)) + 1j * rng.standard_normal((100, 50))

    gamma = coherence_est.estimate(X_L, X_R)
    assert np.all(gamma >= 0.0)
    assert np.all(gamma <= 1.0)


def test_estimate_frame_matches_batch():
    """Per-frame estimation should produce same result as batch."""
    config = UpmixConfig(auto_fft_size=False)
    coherence_est = CoherenceEstimator(config)

    rng = np.random.default_rng(42)
    n_freq, n_frames = 100, 30
    X_L = rng.standard_normal((n_freq, n_frames)) + 1j * rng.standard_normal(
        (n_freq, n_frames)
    )
    X_R = rng.standard_normal((n_freq, n_frames)) + 1j * rng.standard_normal(
        (n_freq, n_frames)
    )

    # Batch
    gamma_batch = coherence_est.estimate(X_L, X_R)

    # Frame-by-frame
    state = coherence_est.create_state(n_freq)
    gamma_stream = np.zeros((n_freq, n_frames))
    for n in range(n_frames):
        gamma_stream[:, n] = coherence_est.estimate_frame(
            X_L[:, n], X_R[:, n], state
        )

    np.testing.assert_allclose(gamma_stream, gamma_batch, atol=1e-12)
