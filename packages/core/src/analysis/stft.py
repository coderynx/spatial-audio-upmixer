import numpy as np
from scipy.signal.windows import get_window

from upmixer.config import UpmixConfig


def _compute_synthesis_window(window: np.ndarray, hop_size: int) -> np.ndarray:
    """Compute synthesis window for perfect reconstruction via WOLA."""
    fft_size = len(window)
    ola_sum = np.zeros(fft_size)
    n_overlaps = fft_size // hop_size
    for i in range(n_overlaps):
        ola_sum += np.roll(window**2, i * hop_size)
    ola_sum = np.maximum(ola_sum, 1e-10)
    return window / ola_sum


class STFTAnalyzer:
    """Batch offline STFT/ISTFT for processing full signals."""

    def __init__(self, config: UpmixConfig, sample_rate: int):
        fft_size, hop_size = config.resolve_fft_params(sample_rate)
        self._fft_size = fft_size
        self._hop_size = hop_size
        self._sample_rate = sample_rate
        self._window = get_window(config.window_type, fft_size).astype(np.float64)
        self._synth_window = _compute_synthesis_window(self._window, hop_size)

    def forward(self, signal: np.ndarray) -> np.ndarray:
        """Compute STFT. Returns complex array of shape (n_freq_bins, n_frames)."""
        fft, hop = self._fft_size, self._hop_size
        padded = np.pad(signal, (fft - hop, fft))
        n_frames = (len(padded) - fft) // hop + 1
        out = np.zeros((self.n_freq_bins, n_frames), dtype=np.complex128)
        for i in range(n_frames):
            s = i * hop
            out[:, i] = np.fft.rfft(padded[s : s + fft] * self._window)
        return out

    def inverse(self, spectrogram: np.ndarray, length: int) -> np.ndarray:
        """Compute ISTFT. Returns 1D float array of given length."""
        fft, hop = self._fft_size, self._hop_size
        n_frames = spectrogram.shape[1]
        buf = np.zeros((n_frames - 1) * hop + fft, dtype=np.float64)
        for i in range(n_frames):
            frame = np.fft.irfft(spectrogram[:, i], n=fft) * self._synth_window
            buf[i * hop : i * hop + fft] += frame
        return buf[fft - hop : fft - hop + length]

    @property
    def n_freq_bins(self) -> int:
        return self._fft_size // 2 + 1

    @property
    def fft_size(self) -> int:
        return self._fft_size

    @property
    def hop_size(self) -> int:
        return self._hop_size

    @property
    def freq_bins(self) -> np.ndarray:
        return np.fft.rfftfreq(self._fft_size, d=1.0 / self._sample_rate)
