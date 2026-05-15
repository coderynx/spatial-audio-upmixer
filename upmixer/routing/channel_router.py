import numpy as np

from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixBatchResult, SoftMatrixResult
from upmixer.formats import FORMAT_MAP
from upmixer.routing.lfe import LFEExtractor


class HeightFilter:
    """Psychoacoustic elevation shaping filter.

    Two-section spectral curve:
      1. Sub-bass rolloff below low_rolloff_hz  (height speakers don't couple to room bass)
      2. High-frequency lift above crossover_hz  (elevation HRTF cues, air, shimmer)
    Midrange is preserved at unity — channels have body, not just thin air.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._gain = config.height_gain
        self._mask = self._build_elevation_mask(
            sample_rate=sample_rate,
            n_freq_bins=n_freq_bins,
            low_rolloff_hz=config.height_low_rolloff_hz,
            low_rolloff_gain=config.height_low_rolloff_gain,
            high_shelf_hz=config.height_crossover_hz,
            high_shelf_gain=config.height_high_shelf_gain,
            transition_width_hz=config.height_transition_width_hz,
        )

    @staticmethod
    def _build_elevation_mask(
        sample_rate: int,
        n_freq_bins: int,
        low_rolloff_hz: float,
        low_rolloff_gain: float,
        high_shelf_hz: float,
        high_shelf_gain: float,
        transition_width_hz: float,
    ) -> np.ndarray:
        freqs = np.arange(n_freq_bins) * sample_rate / ((n_freq_bins - 1) * 2)

        # Section 1: sub-bass rolloff (low_rolloff_gain → 1.0)
        low_scale = low_rolloff_hz / 4.0
        bass_mask = low_rolloff_gain + (1.0 - low_rolloff_gain) / (
            1.0 + np.exp(-(freqs - low_rolloff_hz) / low_scale)
        )

        # Section 2: high-frequency lift (1.0 → high_shelf_gain)
        high_scale = transition_width_hz / 4.0
        shelf_mask = 1.0 + (high_shelf_gain - 1.0) / (
            1.0 + np.exp(-(freqs - high_shelf_hz) / high_scale)
        )

        return bass_mask * shelf_mask

    @property
    def mask(self) -> np.ndarray:
        return self._mask

    def apply_frame(self, frame: np.ndarray) -> np.ndarray:
        return self._gain * self._mask * frame

    def apply(self, spectrogram: np.ndarray) -> np.ndarray:
        return self._gain * self._mask[:, np.newaxis] * spectrogram


class ChannelRouter:
    """Maps soft-matrix decomposed signals to output channel spectrograms.

    No decorrelation, no delays — clean gain-based remixing.
    Surround = M-S side signal (natural stereo width).
    Height = raw L/R through high-shelf (captures air frequencies).
    Height back = ambient side through high-shelf (more diffuse, appropriate for rear).
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._config = config
        self._format = FORMAT_MAP[config.output_format]
        self._lfe = LFEExtractor(config, sample_rate, n_freq_bins)

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

        channels["FL"] = d.front_L
        channels["FR"] = d.front_R
        channels["C"] = cfg.center_gain * d.center
        channels["LFE"] = self._lfe.extract_frame(mid_frame)

        # Surround: M-S side signal — natural stereo width, no artifacts
        channels["SL"] = cfg.surround_gain * d.ambient_L
        channels["SR"] = cfg.surround_gain * d.ambient_R

        if self._format.has_back:
            channels["BL"] = cfg.back_gain * d.ambient_L
            channels["BR"] = cfg.back_gain * d.ambient_R

        if self._height_filter is not None:
            # Front height: raw L/R air — instrument shimmer and room reflection
            channels["TFL"] = self._height_filter.apply_frame(d.signal_L)
            channels["TFR"] = self._height_filter.apply_frame(d.signal_R)

            if self._format.n_height_channels == 4:
                # Back height: side signal air — more diffuse, natural for rear dome
                channels["TBL"] = self._height_filter.apply_frame(d.ambient_L)
                channels["TBR"] = self._height_filter.apply_frame(d.ambient_R)

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
            "SL": cfg.surround_gain * d.ambient_L,
            "SR": cfg.surround_gain * d.ambient_R,
        }

        if self._format.has_back:
            channels["BL"] = cfg.back_gain * d.ambient_L
            channels["BR"] = cfg.back_gain * d.ambient_R

        if self._height_filter is not None:
            channels["TFL"] = self._height_filter.apply(d.signal_L)
            channels["TFR"] = self._height_filter.apply(d.signal_R)

            if self._format.n_height_channels == 4:
                channels["TBL"] = self._height_filter.apply(d.ambient_L)
                channels["TBR"] = self._height_filter.apply(d.ambient_R)

        return channels
