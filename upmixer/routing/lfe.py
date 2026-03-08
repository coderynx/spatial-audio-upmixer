import numpy as np

from upmixer.config import UpmixConfig


class LFEExtractor:
    """Extracts LFE channel via frequency-domain low-pass filtering."""

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._gain = config.lfe_gain
        self._mask = self._build_lpf_mask(
            cutoff_hz=config.lfe_cutoff_hz,
            sample_rate=sample_rate,
            n_freq_bins=n_freq_bins,
            order=config.lfe_filter_order,
        )

    def extract_frame(self, mid_frame: np.ndarray) -> np.ndarray:
        """Extract LFE from a single frame (n_freq,)."""
        return self._gain * self._mask * mid_frame

    def extract(self, mid: np.ndarray) -> np.ndarray:
        """Extract from full spectrogram (batch mode)."""
        return self._gain * self._mask[:, np.newaxis] * mid

    @property
    def mask(self) -> np.ndarray:
        return self._mask

    @staticmethod
    def _build_lpf_mask(
        cutoff_hz: float, sample_rate: int, n_freq_bins: int, order: int
    ) -> np.ndarray:
        """Builds a Butterworth-shaped frequency magnitude mask."""
        freqs = np.arange(n_freq_bins) * sample_rate / ((n_freq_bins - 1) * 2)
        ratio = freqs / cutoff_hz
        mask = 1.0 / np.sqrt(1.0 + np.power(ratio, 2 * order))
        return mask
