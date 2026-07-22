import numpy as np

from upmixer.analysis.coherence import CoherenceEstimator
from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixDecomposer


def test_center_signal_has_strong_center(center_panned_sine, sample_rate):
    """A center-panned signal should produce a strong center channel."""
    left, right = center_panned_sine
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    X_L = stft.forward(left)
    X_R = stft.forward(right)

    coherence = CoherenceEstimator(config).estimate(X_L, X_R)
    decomposer = SoftMatrixDecomposer(config)
    result = decomposer.decompose(X_L, X_R, coherence)

    center_energy = np.sum(np.abs(result.center) ** 2)
    mid_energy = np.sum(np.abs((X_L + X_R) * 0.5) ** 2)
    assert center_energy > 0.3 * mid_energy


def test_side_signal_has_weak_center(side_panned_signal, sample_rate):
    """A side-panned signal (L=-R) should have low center energy."""
    left, right = side_panned_signal
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    X_L = stft.forward(left)
    X_R = stft.forward(right)

    coherence = CoherenceEstimator(config).estimate(X_L, X_R)
    decomposer = SoftMatrixDecomposer(config)
    result = decomposer.decompose(X_L, X_R, coherence)

    # For L=-R, mid=0 so center should be near zero
    center_energy = np.sum(np.abs(result.center) ** 2)
    input_energy = np.sum(np.abs(X_L) ** 2) + np.sum(np.abs(X_R) ** 2)
    assert center_energy < 0.01 * input_energy


def test_side_signal_has_strong_ambient(side_panned_signal, sample_rate):
    """A side-panned signal should produce strong ambient content."""
    left, right = side_panned_signal
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    X_L = stft.forward(left)
    X_R = stft.forward(right)

    coherence = CoherenceEstimator(config).estimate(X_L, X_R)
    decomposer = SoftMatrixDecomposer(config)
    result = decomposer.decompose(X_L, X_R, coherence)

    ambient_energy = np.sum(np.abs(result.ambient_L) ** 2) + np.sum(
        np.abs(result.ambient_R) ** 2
    )
    assert ambient_energy > 0.1 * (np.sum(np.abs(X_L) ** 2) + np.sum(np.abs(X_R) ** 2))


def test_no_phase_artifacts(stereo_mix, sample_rate):
    """Soft matrix should not introduce phase discontinuities.

    The front L/R channels should be simple gain-scaled versions of
    the original — no spectral subtraction, so no phase issues.
    """
    left, right = stereo_mix
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    X_L = stft.forward(left)
    X_R = stft.forward(right)

    coherence = CoherenceEstimator(config).estimate(X_L, X_R)
    decomposer = SoftMatrixDecomposer(config)
    result = decomposer.decompose(X_L, X_R, coherence)

    # Front L should be X_L * (1 - attenuation * coherence * 0.5)
    # This means |front_L| <= |X_L| always (gain reduction only)
    assert np.all(np.abs(result.front_L) <= np.abs(X_L) + 1e-10)
    assert np.all(np.abs(result.front_R) <= np.abs(X_R) + 1e-10)


def test_quadrature_signal_does_not_leak_to_streaming_center():
    """Equal levels with a 90-degree phase offset are not center-panned."""
    config = UpmixConfig(auto_fft_size=False)
    estimator = CoherenceEstimator(config)
    state = estimator.create_state(32)
    decomposer = SoftMatrixDecomposer(config, n_freq=32)
    left = np.ones(32, dtype=np.complex128)
    right = 1j * np.ones(32, dtype=np.complex128)

    coherence = estimator.estimate_frame(left, right, state)
    result = decomposer.decompose_frame(left, right, coherence, estimator.directness_frame(state))

    assert np.max(np.abs(result.center)) < 1e-12
