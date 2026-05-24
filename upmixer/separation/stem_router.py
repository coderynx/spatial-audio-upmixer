"""Spatial routing: maps separated stems to multichannel output positions.

Routing philosophy (Dolby Atmos Music best practices):
  Front bed (FL/FR/C) = song foundation — vocals, kick, snare, bass, melody.
    Center anchors: lead vocals, kick/snare transients, bass mono low-end.
    NOT vocals-only center — that sounds pasted-in and unnatural.

  LFE = effect send, not primary bass channel.
    Core low-end lives in FL/FR; LFE adds weight at specific transient moments.

  Surround (SL/SR/BL/BR) = diffuse only.
    Room reverb, ambience, crowd. Keep rhythmic core in front.
    NO dominant surround bass — muddy and disorienting.

  Heights (TFL/TFR/TBL/TBR) = space/elevation, not dry instruments.
    Reverb tails, ambient textures, pad swells, overhead mics simulation.
    Backing vocals in choruses get height for expansion.
    Wide sustained content belongs here more than transient direct sounds.

  Backing vocals ≠ lead vocals.
    Lead: center-anchored phantom (C dominant + light FL/FR).
    Backing: widened in front L/R + strong height for chorus expansion.

Zone-aware routing for multichannel input:
  Each stem tagged "StemName@zone" where zone ∈ {front, surround, back,
  height_front, height_back}. Zone stems route to their spatial home.

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
from upmixer.separation.stem_analyzer import StemFeatures

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

_FRONT_CHANNELS    = {ChannelLabel.FL, ChannelLabel.FR}
_SURROUND_CHANNELS = {ChannelLabel.SL, ChannelLabel.SR, ChannelLabel.BL, ChannelLabel.BR}
_HEIGHT_CHANNELS   = {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}

_VOCAL_STEM_NAMES: frozenset[str] = frozenset({
    "Vocals", "Lead Vocals", "Backing Vocals",
})


def _content_scale(features: StemFeatures, label: ChannelLabel) -> float:
    """Per-channel multiplicative scale driven by stem content analysis.

    Applied on top of the static routing table gain so spatial placement
    adapts to the actual audio rather than using fixed gains.

    LFE     : boosted by low-frequency energy (bass/kick).
    Center  : boosted when content is mono (vocals, bass, kick) — less for wide stereo.
    Height  : two paths — cymbal/air (HF+transient) OR reverb tail (wide+sustained).
              Takes the stronger path so both use-cases score well.
    Surround: wide + sustained (reverb, room ambience, diffuse guitars).
    Front   : stable anchor; slightly boosted for direct/percussive sounds.
    """
    w = features.stereo_width
    h = features.high_freq_ratio
    b = features.low_freq_ratio
    t = features.transient_ratio

    if label == ChannelLabel.LFE:
        return 0.4 + 0.9 * b

    if label == ChannelLabel.C:
        return 0.55 + 0.65 * (1.0 - w)

    if label in _HEIGHT_CHANNELS:
        cymbal_score  = 0.6 * h + 0.4 * t
        ambient_score = 0.5 * w + 0.5 * (1.0 - t)
        return 0.25 + 0.72 * max(cymbal_score, ambient_score)

    if label in _SURROUND_CHANNELS:
        sustain = 1.0 - t
        return 0.20 + 0.80 * (0.60 * w + 0.40 * sustain)

    return 0.60 + 0.40 * (0.55 * t + 0.45 * (1.0 - w))



ZONE_ROUTING: dict[str, dict[str, dict[str, float]]] = {
    "front": {
        "Vocals":         {"C": 0.72, "FL": 0.28, "FR": 0.28, "TFL": 0.08, "TFR": 0.08},
        "Bass":           {"FL": 0.65, "FR": 0.65, "C": 0.22, "LFE": 0.75},
        "Drums":          {"C": 0.22, "FL": 0.58, "FR": 0.58, "LFE": 0.32,
                           "TFL": 0.18, "TFR": 0.18},
        "Other":          {"FL": 0.38, "FR": 0.38, "SL": 0.18, "SR": 0.18,
                           "TFL": 0.30, "TFR": 0.30},
        "Guitar":         {"FL": 0.55, "FR": 0.55, "SL": 0.15, "SR": 0.15,
                           "TFL": 0.10, "TFR": 0.10},
        "Piano":          {"C": 0.18, "FL": 0.58, "FR": 0.58,
                           "TFL": 0.12, "TFR": 0.12},
        "Instrumental":   {"C": 0.12, "FL": 0.62, "FR": 0.62, "LFE": 0.40,
                           "TFL": 0.15, "TFR": 0.15},
        "Lead Vocals":    {"C": 0.80, "FL": 0.22, "FR": 0.22,
                           "TFL": 0.07, "TFR": 0.07},
        "Backing Vocals": {"FL": 0.48, "FR": 0.48,
                           "TFL": 0.25, "TFR": 0.25},
        "Kick":           {"C": 0.35, "FL": 0.55, "FR": 0.55, "LFE": 0.90},
        "Snare":          {"C": 0.40, "FL": 0.62, "FR": 0.62,
                           "TFL": 0.10, "TFR": 0.10},
        "Toms":           {"C": 0.15, "FL": 0.58, "FR": 0.58, "LFE": 0.22},
        "Hi-Hat":         {"FL": 0.40, "FR": 0.40, "TFL": 0.50, "TFR": 0.50},
        "Ride":           {"FL": 0.35, "FR": 0.35, "TFL": 0.55, "TFR": 0.55},
        "Crash":          {"FL": 0.32, "FR": 0.32, "TFL": 0.60, "TFR": 0.60},
        "Crowd":          {"SL": 0.30, "SR": 0.30, "TFL": 0.10, "TFR": 0.10},
    },
    "surround": {
        "Vocals":         {"SL": 0.22, "SR": 0.22, "TBL": 0.14, "TBR": 0.14},
        "Bass":           {"LFE": 0.20},
        "Drums":          {"SL": 0.52, "SR": 0.52, "BL": 0.22, "BR": 0.22,
                           "TFL": 0.15, "TFR": 0.15, "TBL": 0.22, "TBR": 0.22},
        "Other":          {"SL": 0.62, "SR": 0.62, "BL": 0.32, "BR": 0.32,
                           "TFL": 0.28, "TFR": 0.28, "TBL": 0.32, "TBR": 0.32},
        "Guitar":         {"SL": 0.58, "SR": 0.58, "BL": 0.22, "BR": 0.22,
                           "TBL": 0.12, "TBR": 0.12},
        "Piano":          {"SL": 0.38, "SR": 0.38, "TBL": 0.18, "TBR": 0.18},
        "Instrumental":   {"SL": 0.48, "SR": 0.48, "BL": 0.22, "BR": 0.22,
                           "LFE": 0.15, "TBL": 0.18, "TBR": 0.18},
        "Lead Vocals":    {"SL": 0.10, "SR": 0.10},
        "Backing Vocals": {"SL": 0.38, "SR": 0.38,
                           "TFL": 0.18, "TFR": 0.18, "TBL": 0.22, "TBR": 0.22},
        "Kick":           {"LFE": 0.25},
        "Snare":          {"SL": 0.30, "SR": 0.30},
        "Toms":           {"SL": 0.40, "SR": 0.40},
        "Hi-Hat":         {"SL": 0.22, "SR": 0.22,
                           "TFL": 0.28, "TFR": 0.28, "TBL": 0.18, "TBR": 0.18},
        "Ride":           {"SL": 0.18, "SR": 0.18, "TBL": 0.22, "TBR": 0.22},
        "Crash":          {"SL": 0.28, "SR": 0.28, "TBL": 0.30, "TBR": 0.30},
        "Crowd":          {"SL": 0.32, "SR": 0.32, "BL": 0.20, "BR": 0.20,
                           "TBL": 0.18, "TBR": 0.18},
    },
    "back": {
        "Vocals":         {"BL": 0.20, "BR": 0.20},
        "Bass":           {"LFE": 0.15},
        "Drums":          {"BL": 0.50, "BR": 0.50, "TBL": 0.28, "TBR": 0.28},
        "Other":          {"BL": 0.58, "BR": 0.58, "TBL": 0.42, "TBR": 0.42},
        "Guitar":         {"BL": 0.42, "BR": 0.42, "TBL": 0.18, "TBR": 0.18},
        "Piano":          {"BL": 0.28, "BR": 0.28, "TBL": 0.15, "TBR": 0.15},
        "Instrumental":   {"BL": 0.42, "BR": 0.42, "TBL": 0.28, "TBR": 0.28},
        "Lead Vocals":    {"BL": 0.08, "BR": 0.08},
        "Backing Vocals": {"BL": 0.32, "BR": 0.32, "TBL": 0.25, "TBR": 0.25},
        "Kick":           {"LFE": 0.18},
        "Snare":          {"BL": 0.20, "BR": 0.20},
        "Toms":           {"BL": 0.35, "BR": 0.35},
        "Hi-Hat":         {"TBL": 0.42, "TBR": 0.42},
        "Ride":           {"BL": 0.20, "BR": 0.20, "TBL": 0.40, "TBR": 0.40},
        "Crash":          {"BL": 0.28, "BR": 0.28, "TBL": 0.48, "TBR": 0.48},
        "Crowd":          {"BL": 0.30, "BR": 0.30, "TBL": 0.22, "TBR": 0.22},
    },
    "height_front": {
        "Vocals":         {"TFL": 0.32, "TFR": 0.32},
        "Bass":           {},
        "Drums":          {"TFL": 0.58, "TFR": 0.58, "TBL": 0.18, "TBR": 0.18},
        "Other":          {"TFL": 0.68, "TFR": 0.68, "TBL": 0.28, "TBR": 0.28},
        "Guitar":         {"TFL": 0.45, "TFR": 0.45, "TBL": 0.10, "TBR": 0.10},
        "Piano":          {"TFL": 0.42, "TFR": 0.42},
        "Instrumental":   {"TFL": 0.52, "TFR": 0.52, "TBL": 0.18, "TBR": 0.18},
        "Lead Vocals":    {"TFL": 0.22, "TFR": 0.22},
        "Backing Vocals": {"TFL": 0.50, "TFR": 0.50},
        "Kick":           {},
        "Snare":          {"TFL": 0.15, "TFR": 0.15},
        "Toms":           {"TFL": 0.22, "TFR": 0.22},
        "Hi-Hat":         {"TFL": 0.72, "TFR": 0.72},
        "Ride":           {"TFL": 0.68, "TFR": 0.68},
        "Crash":          {"TFL": 0.80, "TFR": 0.80, "TBL": 0.25, "TBR": 0.25},
        "Crowd":          {"TFL": 0.20, "TFR": 0.20, "TBL": 0.12, "TBR": 0.12},
    },
    "height_back": {
        "Vocals":         {"TBL": 0.25, "TBR": 0.25},
        "Bass":           {},
        "Drums":          {"TBL": 0.52, "TBR": 0.52},
        "Other":          {"TBL": 0.72, "TBR": 0.72},
        "Guitar":         {"TBL": 0.42, "TBR": 0.42},
        "Piano":          {"TBL": 0.38, "TBR": 0.38},
        "Instrumental":   {"TBL": 0.58, "TBR": 0.58},
        "Lead Vocals":    {"TBL": 0.10, "TBR": 0.10},
        "Backing Vocals": {"TBL": 0.50, "TBR": 0.50},
        "Kick":           {},
        "Snare":          {"TBL": 0.12, "TBR": 0.12},
        "Toms":           {"TBL": 0.18, "TBR": 0.18},
        "Hi-Hat":         {"TBL": 0.52, "TBR": 0.52},
        "Ride":           {"TBL": 0.62, "TBR": 0.62},
        "Crash":          {"TBL": 0.75, "TBR": 0.75},
        "Crowd":          {"TBL": 0.28, "TBR": 0.28},
    },
}


DEFAULT_ROUTING: dict[str, dict[str, float]] = {
    "Vocals": {
        "C":   0.70,
        "FL":  0.28,
        "FR":  0.28,
        "TFL": 0.12,
        "TFR": 0.12,
    },
    "Bass": {
        "FL":  0.65,
        "FR":  0.65,
        "C":   0.20,
        "LFE": 0.80,
    },
    "Drums": {
        "C":   0.20,
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.18,
        "SR":  0.18,
        "LFE": 0.32,
        "TFL": 0.20,
        "TFR": 0.20,
        "TBL": 0.08,
        "TBR": 0.08,
    },
    "Other": {
        "FL":  0.28,
        "FR":  0.28,
        "SL":  0.48,
        "SR":  0.48,
        "BL":  0.22,
        "BR":  0.22,
        "TFL": 0.42,
        "TFR": 0.42,
        "TBL": 0.28,
        "TBR": 0.28,
    },
    "Guitar": {
        "FL":  0.52,
        "FR":  0.52,
        "SL":  0.35,
        "SR":  0.35,
        "BL":  0.12,
        "BR":  0.12,
        "TFL": 0.12,
        "TFR": 0.12,
    },
    "Piano": {
        "C":   0.18,
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.22,
        "SR":  0.22,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Instrumental": {
        "C":   0.15,
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.38,
        "SR":  0.38,
        "BL":  0.18,
        "BR":  0.18,
        "LFE": 0.45,
        "TFL": 0.22,
        "TFR": 0.22,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    "Lead Vocals": {
        "C":   0.80,
        "FL":  0.20,
        "FR":  0.20,
        "TFL": 0.08,
        "TFR": 0.08,
    },
    "Backing Vocals": {
        "FL":  0.45,
        "FR":  0.45,
        "SL":  0.22,
        "SR":  0.22,
        "TFL": 0.30,
        "TFR": 0.30,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    "Kick": {
        "C":   0.30,
        "FL":  0.55,
        "FR":  0.55,
        "LFE": 0.85,
    },
    "Snare": {
        "C":   0.35,
        "FL":  0.60,
        "FR":  0.60,
        "TFL": 0.08,
        "TFR": 0.08,
    },
    "Toms": {
        "C":   0.12,
        "FL":  0.62,
        "FR":  0.62,
        "SL":  0.15,
        "SR":  0.15,
        "LFE": 0.20,
    },
    "Hi-Hat": {
        "FL":  0.42,
        "FR":  0.42,
        "TFL": 0.55,
        "TFR": 0.55,
        "TBL": 0.10,
        "TBR": 0.10,
    },
    "Ride": {
        "FL":  0.38,
        "FR":  0.38,
        "TFL": 0.60,
        "TFR": 0.60,
    },
    "Crash": {
        "FL":  0.35,
        "FR":  0.35,
        "SL":  0.20,
        "SR":  0.20,
        "TFL": 0.65,
        "TFR": 0.65,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    "Crowd": {
        "SL":  0.28,
        "SR":  0.28,
        "BL":  0.20,
        "BR":  0.20,
        "TBL": 0.15,
        "TBR": 0.15,
        "TFL": 0.06,
        "TFR": 0.06,
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
        stem_features: dict[str, StemFeatures] | None = None,
    ) -> dict[str, np.ndarray]:
        """Mix stems into output channels.

        Args:
            stems: Dict "StemName[@zone]" → ndarray (n_samples, 2) stereo float.
            n_samples: Expected output length.
            passthrough_channels: Channel names to skip (injected directly by caller).
            stem_features: Optional per-stem content analysis (from stem_analyzer).
                When provided, static routing table gains are scaled per channel type
                based on the stem's stereo width, frequency content, and transient
                density — making spatial placement adapt to the actual audio.

        Returns:
            Dict channel_name → 1D float64 array of length n_samples.
        """
        skip = passthrough_channels or set()
        channels: dict[str, np.ndarray] = {
            label.value: np.zeros(n_samples, dtype=np.float64)
            for label in self._fmt.channels
        }

        for stem_key, audio in stems.items():
            if "@" in stem_key:
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

            features = stem_features.get(stem_key) if stem_features else None

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64)
            stem_R = audio[:n, 1].astype(np.float64) if audio.shape[1] > 1 else stem_L.copy()
            stem_mono = (stem_L + stem_R) * 0.5

            c_redirect: float = 0.0
            if "C" in skip and "C" in stem_routing and stem_name in _VOCAL_STEM_NAMES:
                c_redirect = stem_routing["C"] * 0.5

            lfe_signal: np.ndarray | None = None

            for label in self._fmt.channels:
                ch = label.value
                if ch in skip or ch not in stem_routing:
                    continue

                gain = stem_routing[ch]
                if c_redirect > 0.0 and label in (ChannelLabel.FL, ChannelLabel.FR):
                    gain += c_redirect
                if features is not None:
                    gain = gain * _content_scale(features, label)

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
