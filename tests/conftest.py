import numpy as np
import pytest


@pytest.fixture
def sample_rate():
    return 44100


@pytest.fixture
def duration():
    return 1.0


@pytest.fixture
def center_panned_sine(sample_rate, duration):
    """A 440 Hz sine wave panned dead center (L == R)."""
    t = np.arange(int(sample_rate * duration)) / sample_rate
    signal = 0.5 * np.sin(2 * np.pi * 440 * t)
    return signal, signal.copy()


@pytest.fixture
def side_panned_signal(sample_rate, duration):
    """A signal panned full side (L == -R)."""
    t = np.arange(int(sample_rate * duration)) / sample_rate
    signal = 0.5 * np.sin(2 * np.pi * 440 * t)
    return signal, -signal



@pytest.fixture
def stereo_mix(sample_rate, duration):
    """A realistic stereo mix: center vocal + decorrelated ambience."""
    t = np.arange(int(sample_rate * duration)) / sample_rate
    rng = np.random.default_rng(456)

    # Center-panned vocal-like content (sum of harmonics)
    vocal = np.zeros_like(t)
    for harmonic in [1, 2, 3, 5]:
        vocal += (0.3 / harmonic) * np.sin(2 * np.pi * 220 * harmonic * t)

    # Decorrelated ambient content
    ambient_L = rng.standard_normal(len(t)) * 0.1
    ambient_R = rng.standard_normal(len(t)) * 0.1

    left = vocal + ambient_L
    right = vocal + ambient_R
    return left, right
