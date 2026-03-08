import numpy as np

from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixBatchResult, SoftMatrixResult
from upmixer.formats import FORMAT_MAP
from upmixer.routing.decorrelator import Decorrelator
from upmixer.routing.lfe import LFEExtractor


class HeightFilter:
    """Frequency-dependent gain for height channels.

    Vertical localization cues exist primarily above 4kHz (pinna HRTFs).
    Uses a sigmoid transition curve starting around 3kHz.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._gain = config.height_gain
        self._mask = self._build_height_mask(
            crossover_hz=config.height_crossover_hz,
            transition_width_hz=config.height_transition_width_hz,
            max_gain=config.height_max_gain,
            sample_rate=sample_rate,
            n_freq_bins=n_freq_bins,
        )

    @staticmethod
    def _build_height_mask(
        crossover_hz: float,
        transition_width_hz: float,
        max_gain: float,
        sample_rate: int,
        n_freq_bins: int,
    ) -> np.ndarray:
        """Build sigmoid height frequency mask."""
        freqs = np.arange(n_freq_bins) * sample_rate / ((n_freq_bins - 1) * 2)
        sigmoid_scale = transition_width_hz / 4.0
        mask = max_gain / (1.0 + np.exp(-(freqs - crossover_hz) / sigmoid_scale))
        return mask

    @property
    def mask(self) -> np.ndarray:
        return self._mask

    def apply_frame(self, frame: np.ndarray) -> np.ndarray:
        """Apply height frequency mask to one frame."""
        return self._gain * self._mask * frame

    def apply(self, spectrogram: np.ndarray) -> np.ndarray:
        """Apply height frequency mask to full spectrogram (batch)."""
        return self._gain * self._mask[:, np.newaxis] * spectrogram


class ChannelRouter:
    """Maps soft-matrix decomposed signals to output channel spectrograms."""

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._config = config
        self._format = FORMAT_MAP[config.output_format]
        self._lfe = LFEExtractor(config, sample_rate, n_freq_bins)
        self._decorrelator = Decorrelator(config, n_freq_bins)

        self._height_filter = None
        if self._format.has_height:
            self._height_filter = HeightFilter(config, sample_rate, n_freq_bins)

    def route_frame(
        self,
        decomposition: SoftMatrixResult,
        mid_frame: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """Route one frame to output channels."""
        cfg = self._config
        d = decomposition

        channels: dict[str, np.ndarray] = {}

        # Front Left / Right: already computed by soft matrix
        channels["FL"] = d.front_L
        channels["FR"] = d.front_R

        # Center
        channels["C"] = cfg.center_gain * d.center

        # LFE
        channels["LFE"] = self._lfe.extract_frame(mid_frame)

        # Surround Left / Right
        channels["SL"] = cfg.surround_gain * self._decorrelator.apply_frame(
            d.ambient_L, filter_index=0
        )
        channels["SR"] = cfg.surround_gain * self._decorrelator.apply_frame(
            d.ambient_R, filter_index=1
        )

        # Back channels (7.1, 7.1.2, 7.1.4)
        if self._format.has_back:
            channels["BL"] = cfg.back_gain * self._decorrelator.apply_frame(
                d.ambient_L, filter_index=2
            )
            channels["BR"] = cfg.back_gain * self._decorrelator.apply_frame(
                d.ambient_R, filter_index=3
            )

        # Height channels
        if self._height_filter is not None:
            channels["TFL"] = self._height_filter.apply_frame(
                self._decorrelator.apply_frame(d.ambient_L, filter_index=4)
            )
            channels["TFR"] = self._height_filter.apply_frame(
                self._decorrelator.apply_frame(d.ambient_R, filter_index=5)
            )

            if self._format.n_height_channels == 4:
                channels["TBL"] = self._height_filter.apply_frame(
                    self._decorrelator.apply_frame(d.ambient_L, filter_index=6)
                )
                channels["TBR"] = self._height_filter.apply_frame(
                    self._decorrelator.apply_frame(d.ambient_R, filter_index=7)
                )

        return channels

    def route(
        self,
        mid: np.ndarray,
        decomposition: SoftMatrixBatchResult,
    ) -> dict[str, np.ndarray]:
        """Batch routing."""
        cfg = self._config
        d = decomposition

        channels: dict[str, np.ndarray] = {
            "FL": d.front_L,
            "FR": d.front_R,
            "C": cfg.center_gain * d.center,
            "LFE": self._lfe.extract(mid),
            "SL": cfg.surround_gain * self._decorrelator.apply(d.ambient_L, 0),
            "SR": cfg.surround_gain * self._decorrelator.apply(d.ambient_R, 1),
        }

        if self._format.has_back:
            channels["BL"] = cfg.back_gain * self._decorrelator.apply(d.ambient_L, 2)
            channels["BR"] = cfg.back_gain * self._decorrelator.apply(d.ambient_R, 3)

        if self._height_filter is not None:
            channels["TFL"] = self._height_filter.apply(
                self._decorrelator.apply(d.ambient_L, 4)
            )
            channels["TFR"] = self._height_filter.apply(
                self._decorrelator.apply(d.ambient_R, 5)
            )
            if self._format.n_height_channels == 4:
                channels["TBL"] = self._height_filter.apply(
                    self._decorrelator.apply(d.ambient_L, 6)
                )
                channels["TBR"] = self._height_filter.apply(
                    self._decorrelator.apply(d.ambient_R, 7)
                )

        return channels
