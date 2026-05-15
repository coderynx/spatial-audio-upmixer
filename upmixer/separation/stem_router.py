"""Spatial routing: maps separated stems to multichannel output positions.

Zone-aware routing for multichannel input:
  Each stem is tagged "StemName@zone" where zone ∈ {front, surround, back,
  height_front, height_back}. Zone stems route primarily to their spatial home,
  spreading only where musically appropriate.

For stereo input all stems are tagged @front; behaviour matches DEFAULT_ROUTING.

Center (C) and LFE from multichannel inputs are passed through directly and
excluded from stem routing via the passthrough_channels set.

Channel assignment within each zone:
  Left channels  (FL, SL, BL, TFL, TBL): receive stem_L
  Right channels (FR, SR, BR, TFR, TBR): receive stem_R
  C / LFE:                                receive (stem_L + stem_R) × 0.5
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.config import UpmixConfig

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

# ── Zone routing tables ────────────────────────────────────────────────────────
# front: direct/dry front-speaker content → front channels + slight height

ZONE_ROUTING: dict[str, dict[str, dict[str, float]]] = {
    "front": {
        "Vocals":         {"C": 0.85, "FL": 0.25, "FR": 0.25, "TFL": 0.12, "TFR": 0.12},
        "Bass":           {"FL": 0.55, "FR": 0.55, "LFE": 0.90},
        "Drums":          {"FL": 0.65, "FR": 0.65, "LFE": 0.40, "TFL": 0.20, "TFR": 0.20},
        "Other":          {"FL": 0.40, "FR": 0.40, "TFL": 0.12, "TFR": 0.12},
        "Guitar":         {"FL": 0.50, "FR": 0.50, "TFL": 0.10, "TFR": 0.10},
        "Piano":          {"C": 0.15, "FL": 0.55, "FR": 0.55, "TFL": 0.12, "TFR": 0.12},
        "Instrumental":   {"FL": 0.60, "FR": 0.60, "LFE": 0.45, "TFL": 0.15, "TFR": 0.15},
        "Lead Vocals":    {"C": 0.90, "FL": 0.20, "FR": 0.20},
        "Backing Vocals": {"FL": 0.40, "FR": 0.40, "TFL": 0.15, "TFR": 0.15},
    },
    # surround: room/reverb tails, wide ambience → surrounds + back + height
    "surround": {
        "Vocals":         {"SL": 0.30, "SR": 0.30, "TBL": 0.15, "TBR": 0.15},
        "Bass":           {"LFE": 0.25},
        "Drums":          {"SL": 0.60, "SR": 0.60, "BL": 0.30, "BR": 0.30, "TBL": 0.15, "TBR": 0.15},
        "Other":          {"SL": 0.70, "SR": 0.70, "BL": 0.45, "BR": 0.45, "TBL": 0.25, "TBR": 0.25},
        "Guitar":         {"SL": 0.65, "SR": 0.65, "BL": 0.30, "BR": 0.30},
        "Piano":          {"SL": 0.35, "SR": 0.35, "TBL": 0.20, "TBR": 0.20},
        "Instrumental":   {"SL": 0.55, "SR": 0.55, "BL": 0.30, "BR": 0.30, "LFE": 0.20},
        "Lead Vocals":    {"SL": 0.15, "SR": 0.15},
        "Backing Vocals": {"SL": 0.45, "SR": 0.45, "TBL": 0.20, "TBR": 0.20},
    },
    # back: deep rear energy → back channels + height_back
    "back": {
        "Vocals":         {"BL": 0.25, "BR": 0.25},
        "Bass":           {"LFE": 0.20},
        "Drums":          {"BL": 0.55, "BR": 0.55, "TBL": 0.20, "TBR": 0.20},
        "Other":          {"BL": 0.65, "BR": 0.65, "TBL": 0.35, "TBR": 0.35},
        "Guitar":         {"BL": 0.50, "BR": 0.50},
        "Piano":          {"BL": 0.30, "BR": 0.30},
        "Instrumental":   {"BL": 0.50, "BR": 0.50, "TBL": 0.25, "TBR": 0.25},
        "Lead Vocals":    {"BL": 0.15, "BR": 0.15},
        "Backing Vocals": {"BL": 0.40, "BR": 0.40},
    },
    # height_front: overhead front energy → TFL/TFR + slight TBL/TBR
    "height_front": {
        "Vocals":         {"TFL": 0.30, "TFR": 0.30},
        "Bass":           {},
        "Drums":          {"TFL": 0.55, "TFR": 0.55, "TBL": 0.15, "TBR": 0.15},
        "Other":          {"TFL": 0.60, "TFR": 0.60, "TBL": 0.20, "TBR": 0.20},
        "Guitar":         {"TFL": 0.45, "TFR": 0.45},
        "Piano":          {"TFL": 0.40, "TFR": 0.40},
        "Instrumental":   {"TFL": 0.50, "TFR": 0.50},
        "Lead Vocals":    {"TFL": 0.20, "TFR": 0.20},
        "Backing Vocals": {"TFL": 0.35, "TFR": 0.35},
    },
    # height_back: rear overhead energy → TBL/TBR primary
    "height_back": {
        "Vocals":         {"TBL": 0.25, "TBR": 0.25},
        "Bass":           {},
        "Drums":          {"TBL": 0.45, "TBR": 0.45},
        "Other":          {"TBL": 0.65, "TBR": 0.65},
        "Guitar":         {"TBL": 0.40, "TBR": 0.40},
        "Piano":          {"TBL": 0.35, "TBR": 0.35},
        "Instrumental":   {"TBL": 0.50, "TBR": 0.50},
        "Lead Vocals":    {"TBL": 0.15, "TBR": 0.15},
        "Backing Vocals": {"TBL": 0.40, "TBR": 0.40},
    },
}

# ── Default routing (stereo / unzoned fallback) ────────────────────────────────
DEFAULT_ROUTING: dict[str, dict[str, float]] = {
    "Vocals": {
        "C":   0.85,
        "FL":  0.25,
        "FR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Bass": {
        "FL":  0.50,
        "FR":  0.50,
        "LFE": 0.90,
    },
    "Drums": {
        "FL":  0.60,
        "FR":  0.60,
        "SL":  0.25,
        "SR":  0.25,
        "BL":  0.10,
        "BR":  0.10,
        "LFE": 0.40,
        "TFL": 0.20,
        "TFR": 0.20,
    },
    "Other": {
        "FL":  0.35,
        "FR":  0.35,
        "SL":  0.55,
        "SR":  0.55,
        "BL":  0.30,
        "BR":  0.30,
        "TFL": 0.20,
        "TFR": 0.20,
        "TBL": 0.15,
        "TBR": 0.15,
    },
    "Guitar": {
        "FL":  0.30,
        "FR":  0.30,
        "SL":  0.60,
        "SR":  0.60,
        "BL":  0.25,
        "BR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Piano": {
        "C":   0.20,
        "FL":  0.50,
        "FR":  0.50,
        "SL":  0.25,
        "SR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Instrumental": {
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.45,
        "SR":  0.45,
        "BL":  0.25,
        "BR":  0.25,
        "LFE": 0.50,
        "TFL": 0.15,
        "TFR": 0.15,
        "TBL": 0.10,
        "TBR": 0.10,
    },
    "Lead Vocals": {
        "C":   0.90,
        "FL":  0.20,
        "FR":  0.20,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Backing Vocals": {
        "FL":  0.40,
        "FR":  0.40,
        "SL":  0.35,
        "SR":  0.35,
        "TFL": 0.20,
        "TFR": 0.20,
    },
}


class StemRouter:
    """Mix separated stems into output channels using spatial routing tables.

    Stems keyed as "StemName@zone" are routed via ZONE_ROUTING[zone][StemName].
    Unzoned stems (no "@") fall back to DEFAULT_ROUTING or custom_routing.

    Channels listed in passthrough_channels are skipped during routing; the
    pipeline injects those channels directly from the source material.
    """

    def __init__(
        self,
        config: UpmixConfig,
        output_fmt: OutputFormat,
        sample_rate: int,
        routing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._config = config
        self._fmt = output_fmt
        self._fallback_routing = routing or DEFAULT_ROUTING
        self._sr = sample_rate
        self._lfe_sos = butter(
            config.lfe_filter_order,
            config.lfe_cutoff_hz / (sample_rate / 2.0),
            btype="low",
            output="sos",
        )
        self._lfe_gain = config.lfe_gain

    def route(
        self,
        stems: dict[str, np.ndarray],
        n_samples: int,
        passthrough_channels: set[str] | None = None,
        per_stem_routing: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, np.ndarray]:
        """Mix stems into output channels.

        Args:
            stems: Dict "StemName[@zone]" → ndarray (n_samples, 2) stereo float.
            n_samples: Expected output length.
            passthrough_channels: Channel names to skip (injected directly by caller).
            per_stem_routing: Optional content-aware per-stem routing overrides
                produced by ContentMixer. Takes precedence over ZONE_ROUTING /
                DEFAULT_ROUTING for any stem key present in the dict.

        Returns:
            Dict channel_name → 1D float64 array of length n_samples.
        """
        skip = passthrough_channels or set()
        channels: dict[str, np.ndarray] = {
            label.value: np.zeros(n_samples, dtype=np.float64)
            for label in self._fmt.channels
        }

        for stem_key, audio in stems.items():
            if per_stem_routing and stem_key in per_stem_routing:
                stem_routing = per_stem_routing[stem_key]
            elif "@" in stem_key:
                stem_name, zone = stem_key.rsplit("@", 1)
                stem_routing = (
                    ZONE_ROUTING.get(zone, {}).get(stem_name)
                    or self._fallback_routing.get(stem_name)
                )
            else:
                stem_name = stem_key
                stem_routing = self._fallback_routing.get(stem_name)

            if not stem_routing:
                continue

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64)
            stem_R = audio[:n, 1].astype(np.float64) if audio.shape[1] > 1 else stem_L.copy()
            stem_mono = (stem_L + stem_R) * 0.5

            lfe_signal: np.ndarray | None = None

            for label in self._fmt.channels:
                ch = label.value
                if ch in skip or ch not in stem_routing:
                    continue
                gain = stem_routing[ch]

                if label == ChannelLabel.LFE:
                    if lfe_signal is None:
                        lfe_signal = self._lfe_gain * sosfilt(self._lfe_sos, stem_mono)
                    channels[ch][:n] += gain * lfe_signal
                elif label in _LEFT_CHANNELS:
                    channels[ch][:n] += gain * stem_L
                elif label in _RIGHT_CHANNELS:
                    channels[ch][:n] += gain * stem_R
                elif label == ChannelLabel.C:
                    channels[ch][:n] += gain * stem_mono

        return channels

    def get_routing(self, stem_key: str) -> dict[str, float] | None:
        """Return effective routing dict for a stem key ("StemName" or "StemName@zone")."""
        if "@" in stem_key:
            stem_name, zone = stem_key.rsplit("@", 1)
            return (
                ZONE_ROUTING.get(zone, {}).get(stem_name)
                or self._fallback_routing.get(stem_name)
            )
        return self._fallback_routing.get(stem_key)
