import numpy as np

from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixBatchResult, SoftMatrixResult
from upmixer.formats import FORMAT_MAP
from upmixer.routing.lfe import LFEExtractor
from upmixer.manifest import register_block as _rb

_rb("routing", {
    "center_gain":            ("config", "center_gain"),
    "surround_gain":          ("config", "surround_gain"),
    "back_gain":              ("config", "back_gain"),
    "height_gain":            ("config", "height_gain"),
    "lfe_gain":               ("config", "lfe_gain"),
    "lfe_cutoff":             ("config", "lfe_cutoff"),
    "center_extraction_gain": ("config", "center_extraction_gain"),
    "center_attenuation":     ("config", "center_attenuation"),
    "height_low_rolloff_gain":("config", "height_low_rolloff_gain"),
    "height_high_shelf_gain": ("config", "height_high_shelf_gain"),
    "content_mix_strength":   ("config", "content_mix_strength"),
})
del _rb


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

        low_scale = low_rolloff_hz / 4.0
        bass_mask = low_rolloff_gain + (1.0 - low_rolloff_gain) / (
            1.0 + np.exp(-(freqs - low_rolloff_hz) / low_scale)
        )

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
    """Perceptual spectral router: maps decomposed signals to output channels.

    Routing is driven by three per-frame signals from the decomposer:

    width(f) = 1 - coherence(f)
        High where L/R are diffuse/uncorrelated (room reverb, wide panned instruments).
        Only diffuse content routes to surrounds — direct/coherent signal stays in front.

    surround_freq_mask(f)
        Sigmoid above surround_bass_cutoff_hz. Prevents low-frequency energy from
        leaking into surround channels (no muddy bass in SL/SR).

    transient_gate (scalar, per frame)
        Derived from decomposition.transient_score (spectral flux).
        On transients (attack of drums, plucks): gate closes → energy anchored in front.
        On sustained/decaying content: gate open → energy spreads to surround field.
        This makes percussion stay upfront while reverb tails wrap around the listener.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq_bins: int):
        self._config = config
        self._format = FORMAT_MAP[config.output_format]
        self._lfe = LFEExtractor(config, sample_rate, n_freq_bins)
        self._transient_gate_min = config.transient_gate_min

        freqs = np.arange(n_freq_bins) * sample_rate / ((n_freq_bins - 1) * 2)
        cutoff = config.surround_bass_cutoff_hz
        self._surround_freq_mask = 1.0 / (1.0 + np.exp(-(freqs - cutoff) / (cutoff / 4.0)))

        self._height_filter: HeightFilter | None = None
        if self._format.has_height:
            self._height_filter = HeightFilter(config, sample_rate, n_freq_bins)

    def route_frame(
        self,
        decomposition: SoftMatrixResult,
        mid_frame: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """Route one frame to output channels.

        Surrounds receive the M-S side signal bass-filtered above surround_bass_cutoff_hz.
        No per-bin spectral masking — M-S decomposition already ensures centered/coherent
        content has near-zero side signal. Masking causes underwater spectral artifacts.
        """
        cfg = self._config
        d = decomposition

        side_L = d.ambient_L * self._surround_freq_mask
        side_R = d.ambient_R * self._surround_freq_mask

        channels: dict[str, np.ndarray] = {}

        channels["FL"] = d.front_L
        channels["FR"] = d.front_R
        channels["C"] = cfg.center_gain * d.center
        channels["LFE"] = self._lfe.extract_frame(mid_frame)

        channels["SL"] = cfg.surround_gain * side_L
        channels["SR"] = cfg.surround_gain * side_R

        if self._format.has_back:
            channels["BL"] = cfg.back_gain * side_L
            channels["BR"] = cfg.back_gain * side_R

        if self._height_filter is not None:
            channels["TFL"] = self._height_filter.apply_frame(d.signal_L)
            channels["TFR"] = self._height_filter.apply_frame(d.signal_R)

            if self._format.n_height_channels == 4:
                channels["TBL"] = self._height_filter.apply_frame(side_L)
                channels["TBR"] = self._height_filter.apply_frame(side_R)

        return channels

    def route(
        self,
        mid: np.ndarray,
        decomposition: SoftMatrixBatchResult,
    ) -> dict[str, np.ndarray]:
        """Batch routing. Transient gate derived from per-frame transient_score."""
        cfg = self._config
        d = decomposition

        gate = self._transient_gate_min + (1.0 - self._transient_gate_min) * (
            1.0 - d.transient_score[np.newaxis, :]
        )
        surround_w = d.width * self._surround_freq_mask[:, np.newaxis]

        channels: dict[str, np.ndarray] = {
            "FL": d.front_L,
            "FR": d.front_R,
            "C": cfg.center_gain * d.center,
            "LFE": self._lfe.extract(mid),
            "SL": cfg.surround_gain * d.ambient_L * surround_w * gate,
            "SR": cfg.surround_gain * d.ambient_R * surround_w * gate,
        }

        if self._format.has_back:
            channels["BL"] = cfg.back_gain * d.ambient_L * surround_w * gate
            channels["BR"] = cfg.back_gain * d.ambient_R * surround_w * gate

        if self._height_filter is not None:
            channels["TFL"] = self._height_filter.apply(d.signal_L)
            channels["TFR"] = self._height_filter.apply(d.signal_R)

            if self._format.n_height_channels == 4:
                channels["TBL"] = self._height_filter.apply(
                    d.ambient_L * surround_w
                ) * gate
                channels["TBR"] = self._height_filter.apply(
                    d.ambient_R * surround_w
                ) * gate

        return channels
