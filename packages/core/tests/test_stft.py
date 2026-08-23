import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig


def test_round_trip_reconstruction(sample_rate):
    """STFT -> ISTFT should reconstruct the original signal."""
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    rng = np.random.default_rng(42)
    signal = rng.standard_normal(sample_rate)

    spectrogram = stft.forward(signal)
    reconstructed = stft.inverse(spectrogram, length=len(signal))

    error = np.max(np.abs(signal - reconstructed))
    assert error < 1e-6, f"Reconstruction error too large: {error}"


def test_round_trip_sine(sample_rate):
    """Round-trip a pure sine wave."""
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    t = np.arange(sample_rate) / sample_rate
    signal = np.sin(2 * np.pi * 440 * t)

    spectrogram = stft.forward(signal)
    reconstructed = stft.inverse(spectrogram, length=len(signal))

    error = np.max(np.abs(signal - reconstructed))
    assert error < 1e-6, f"Sine reconstruction error: {error}"


def test_output_shape(sample_rate):
    """Check that STFT output has expected shape."""
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    signal = np.zeros(sample_rate)
    spectrogram = stft.forward(signal)

    assert spectrogram.shape[0] == stft.n_freq_bins
    assert spectrogram.shape[1] > 0
    assert np.iscomplexobj(spectrogram)


def test_freq_bins(sample_rate):
    """Verify frequency bin values."""
    config = UpmixConfig(auto_fft_size=False)
    stft = STFTAnalyzer(config, sample_rate)

    freqs = stft.freq_bins
    assert len(freqs) == stft.n_freq_bins
    assert freqs[0] == 0.0
    assert freqs[-1] <= sample_rate / 2


def test_auto_fft_size_192k():
    """Auto FFT size should scale up for 192kHz."""
    config = UpmixConfig(auto_fft_size=True)
    fft_size, hop_size = config.resolve_fft_params(192000)
    assert fft_size > 4096
    assert fft_size <= 16384
    assert hop_size == fft_size // 4


def test_auto_fft_size_44k():
    """Auto FFT size should be 4096 for 44.1kHz."""
    config = UpmixConfig(auto_fft_size=True)
    fft_size, hop_size = config.resolve_fft_params(44100)
    assert fft_size == 4096
    assert hop_size == 1024
