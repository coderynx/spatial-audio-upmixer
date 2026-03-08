import numpy as np

from upmixer.config import UpmixConfig


class Decorrelator:
    """Generates and applies allpass decorrelation filters (phase randomization)."""

    def __init__(self, config: UpmixConfig, n_freq_bins: int):
        self._n_filters = config.decorr_n_filters
        self._filters = self._generate_allpass_filters(
            n_filters=self._n_filters,
            n_freq_bins=n_freq_bins,
            seed=config.decorr_seed,
        )

    def apply_frame(self, frame: np.ndarray, filter_index: int) -> np.ndarray:
        """Apply decorrelation to a single frame (n_freq,)."""
        return frame * self._filters[filter_index]

    def apply(self, spectrogram: np.ndarray, filter_index: int) -> np.ndarray:
        """Apply to full spectrogram (n_freq, n_frames) — batch mode."""
        filt = self._filters[filter_index]
        return spectrogram * filt[:, np.newaxis]

    @staticmethod
    def _generate_allpass_filters(
        n_filters: int, n_freq_bins: int, seed: int
    ) -> list[np.ndarray]:
        """Generates random-phase allpass filters.

        Each filter is a complex array of shape (n_freq_bins,) with unit magnitude
        and smoothed random phase.
        """
        rng = np.random.default_rng(seed)
        filters = []

        for i in range(n_filters):
            raw_phase = rng.uniform(0, 2 * np.pi, n_freq_bins)

            kernel_size = max(3, n_freq_bins // 64)
            kernel = np.ones(kernel_size) / kernel_size
            smoothed_phase = np.convolve(raw_phase, kernel, mode="same")

            # DC bin should have zero phase shift
            smoothed_phase[0] = 0.0

            filters.append(np.exp(1j * smoothed_phase))

        return filters
