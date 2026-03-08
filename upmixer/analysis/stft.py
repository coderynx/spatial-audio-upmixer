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
    """Batch STFT/ISTFT using overlap-add.

    Uses the same windowing/OLA math as StreamingSTFT for equivalence.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int):
        fft_size, hop_size = config.resolve_fft_params(sample_rate)
        self._fft_size = fft_size
        self._hop_size = hop_size
        self._sample_rate = sample_rate
        self._window = get_window(config.window_type, fft_size).astype(np.float64)
        self._synth_window = _compute_synthesis_window(self._window, hop_size)

    def forward(self, signal: np.ndarray) -> np.ndarray:
        """STFT of a 1D signal. Returns (n_freq_bins, n_frames)."""
        fft = self._fft_size
        hop = self._hop_size
        # Pad signal for complete frames
        pad_left = fft - hop
        pad_right = hop - (len(signal) % hop) if len(signal) % hop != 0 else 0
        padded = np.pad(signal, (pad_left, pad_right))
        n_frames = (len(padded) - fft) // hop + 1

        result = np.zeros((self.n_freq_bins, n_frames), dtype=np.complex128)
        for i in range(n_frames):
            start = i * hop
            frame = padded[start : start + fft] * self._window
            result[:, i] = np.fft.rfft(frame)
        return result

    def inverse(self, spectrogram: np.ndarray, length: int) -> np.ndarray:
        """ISTFT via overlap-add with per-sample window normalization.

        Uses the analysis window as a synthesis window and accumulates the
        actual window-squared sum per sample.  This correctly handles
        boundaries where fewer frames overlap (unlike the circular
        pre-computed synthesis window).
        """
        fft = self._fft_size
        hop = self._hop_size
        n_frames = spectrogram.shape[1]
        output_length = (n_frames - 1) * hop + fft
        output = np.zeros(output_length)
        window_sum = np.zeros(output_length)

        for i in range(n_frames):
            frame = np.fft.irfft(spectrogram[:, i], n=fft)
            frame *= self._window
            start = i * hop
            output[start : start + fft] += frame
            window_sum[start : start + fft] += self._window ** 2

        window_sum = np.maximum(window_sum, 1e-10)
        output /= window_sum

        pad_left = fft - hop
        result = output[pad_left : pad_left + length]
        if len(result) < length:
            result = np.pad(result, (0, length - len(result)))
        return result

    @property
    def freq_bins(self) -> np.ndarray:
        return np.arange(self.n_freq_bins) * self._sample_rate / self._fft_size

    @property
    def n_freq_bins(self) -> int:
        return self._fft_size // 2 + 1

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def fft_size(self) -> int:
        return self._fft_size

    @property
    def hop_size(self) -> int:
        return self._hop_size


class StreamingSTFT:
    """Frame-by-frame STFT/ISTFT with internal overlap state.

    Feed hop_size samples at a time via analyze_frame(), process in
    frequency domain, then synthesize_frame() to get time-domain output.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int):
        fft_size, hop_size = config.resolve_fft_params(sample_rate)
        self._fft_size = fft_size
        self._hop_size = hop_size
        self._sample_rate = sample_rate

        self._window = get_window(config.window_type, fft_size).astype(np.float64)
        self._synth_window = _compute_synthesis_window(self._window, hop_size)

        self._input_buffer = np.zeros(fft_size, dtype=np.float64)
        self._input_fill = 0
        self._output_buffer = np.zeros(fft_size, dtype=np.float64)

    def analyze_frame(self, new_samples: np.ndarray) -> np.ndarray | None:
        """Feed hop_size new samples, get one frequency-domain frame.

        Returns complex array of shape (n_freq_bins,), or None if
        the initial buffer hasn't filled yet.
        """
        hop = self._hop_size
        fft = self._fft_size
        assert len(new_samples) == hop, f"Expected {hop} samples, got {len(new_samples)}"

        self._input_buffer[: fft - hop] = self._input_buffer[hop:]
        self._input_buffer[fft - hop :] = new_samples
        self._input_fill = min(self._input_fill + hop, fft)

        if self._input_fill < fft:
            return None

        windowed = self._input_buffer * self._window
        return np.fft.rfft(windowed)

    def synthesize_frame(self, spectrum: np.ndarray) -> np.ndarray:
        """Convert one frequency-domain frame back, return hop_size output samples."""
        hop = self._hop_size
        fft = self._fft_size

        frame = np.fft.irfft(spectrum, n=fft)
        frame *= self._synth_window

        self._output_buffer += frame

        output = self._output_buffer[:hop].copy()
        self._output_buffer[: fft - hop] = self._output_buffer[hop:]
        self._output_buffer[fft - hop :] = 0.0

        return output

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
    def latency_samples(self) -> int:
        return self._fft_size

    def reset(self) -> None:
        self._input_buffer[:] = 0.0
        self._output_buffer[:] = 0.0
        self._input_fill = 0
