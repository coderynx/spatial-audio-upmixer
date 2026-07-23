"""Static spatial routing: maps separated stems to output speakers.

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

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.utils import diffuse_send

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

_FRONT_CHANNELS    = {ChannelLabel.FL, ChannelLabel.FR}
_SURROUND_CHANNELS = {ChannelLabel.SL, ChannelLabel.SR, ChannelLabel.BL, ChannelLabel.BR}
_HEIGHT_CHANNELS   = {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}

_VOCAL_STEM_NAMES: frozenset[str] = frozenset({
    "Vocals", "Lead Vocals", "Backing Vocals",
})

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
        "SL":  0.12,
        "SR":  0.12,
        "LFE": 0.32,
        "TFL": 0.12,
        "TFR": 0.12,
        "TBL": 0.04,
        "TBR": 0.04,
    },
    "Other": {
        "FL":  0.38,
        "FR":  0.38,
        "SL":  0.34,
        "SR":  0.34,
        "BL":  0.15,
        "BR":  0.15,
        "TFL": 0.22,
        "TFR": 0.22,
        "TBL": 0.14,
        "TBR": 0.14,
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
        "FL":  0.60,
        "FR":  0.60,
        "SL":  0.30,
        "SR":  0.30,
        "BL":  0.14,
        "BR":  0.14,
        "LFE": 0.45,
        "TFL": 0.15,
        "TFR": 0.15,
        "TBL": 0.08,
        "TBR": 0.08,
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
        "TFL": 0.40,
        "TFR": 0.40,
        "TBL": 0.06,
        "TBR": 0.06,
    },
    "Ride": {
        "FL":  0.38,
        "FR":  0.38,
        "TFL": 0.45,
        "TFR": 0.45,
    },
    "Crash": {
        "FL":  0.35,
        "FR":  0.35,
        "SL":  0.15,
        "SR":  0.15,
        "TFL": 0.50,
        "TFR": 0.50,
        "TBL": 0.08,
        "TBR": 0.08,
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


STEM_ROUTING_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "balanced": (1.00, 1.00, 1.00, 1.00),
    "intimate": (1.06, 0.68, 0.55, 0.68),
    "rhythmic": (1.03, 0.88, 0.72, 1.05),
    "spacious": (0.96, 1.20, 1.12, 1.18),
    "live": (0.98, 1.16, 1.22, 0.96),
    "detailed": (1.02, 1.05, 0.92, 1.12),
}
"""Static front, side, back, and height scales for stem-routing presets."""

STEM_ROUTING_PRESET_NAMES: tuple[str, ...] = tuple(STEM_ROUTING_PRESETS)


def build_stem_routing(
    stems: list[str],
    output_format: OutputFormat,
    preset: str = "balanced",
    intensity: float = 1.0,
) -> dict[str, dict[str, float]]:
    """Return explicit speaker maps for a stem-routing preset.

    Presets scale the built-in static routes before the router's existing
    constant-power normalization.  ``intensity`` interpolates from balanced
    routing to the requested preset.  LFE is deliberately unchanged.
    """
    if preset not in STEM_ROUTING_PRESETS:
        raise ValueError(
            f"Unknown stem routing preset '{preset}'. Valid: {STEM_ROUTING_PRESET_NAMES}"
        )
    amount = float(np.clip(intensity, 0.0, 1.0))
    scales = STEM_ROUTING_PRESETS[preset]
    channel_labels = {label.value: label for label in output_format.channels}
    output: dict[str, dict[str, float]] = {}
    for stem in stems:
        base = DEFAULT_ROUTING.get(stem)
        if base is None:
            continue
        route: dict[str, float] = {}
        for channel, gain in base.items():
            label = channel_labels.get(channel)
            if label is None:
                continue
            if label in _FRONT_CHANNELS or label == ChannelLabel.C:
                scale = scales[0]
            elif label in {ChannelLabel.SL, ChannelLabel.SR}:
                scale = scales[1]
            elif label in {ChannelLabel.BL, ChannelLabel.BR}:
                scale = scales[2]
            elif label in _HEIGHT_CHANNELS:
                scale = scales[3]
            else:
                scale = 1.0
            route[channel] = gain * (1.0 + amount * (scale - 1.0))
        output[stem] = route
    return output


class StemRouter:
    """Mix separated stems into output channels using spatial routing tables.

    Stems keyed as "StemName@zone" are routed via ZONE_ROUTING[zone][StemName].
    Manifest routing entries merge over built-in routes. Zone-specific keys
    (``"Stem@zone"``) take precedence over stem-name entries.

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
        self._manifest_routing = config.stem_routing or {}
        self._custom_routing = routing or {}
        self._stem_enabled = config.stem_enabled or {}
        self._sr = sample_rate
        self._lfe_sos = butter(
            config.lfe_filter_order,
            config.lfe_cutoff_hz / (sample_rate / 2.0),
            btype="low",
            output="sos",
        )
        self._lfe_gain = config.lfe_gain
        self._surround_sos = butter(
            2,
            config.surround_bass_cutoff_hz / (sample_rate / 2.0),
            btype="high",
            output="sos",
        )
        self._height_low_sos = butter(
            1,
            config.height_low_rolloff_hz / (sample_rate / 2.0),
            btype="low",
            output="sos",
        )
        self._height_high_sos = butter(
            2,
            config.height_crossover_hz / (sample_rate / 2.0),
            btype="high",
            output="sos",
        )

    def _routing_for(self, stem_key: str) -> dict[str, float] | None:
        if "@" in stem_key:
            stem_name, zone = stem_key.rsplit("@", 1)
            zone_routing = ZONE_ROUTING.get(zone, {})
            base = (
                zone_routing[stem_name]
                if stem_name in zone_routing
                else DEFAULT_ROUTING.get(stem_name)
            )
        else:
            stem_name = stem_key
            base = DEFAULT_ROUTING.get(stem_name)

        result = dict(base) if base is not None else {}
        found_override = base is not None
        for overrides in (self._manifest_routing, self._custom_routing):
            custom = (
                overrides[stem_key]
                if stem_key in overrides
                else overrides.get(stem_name)
            )
            if custom is not None:
                result.update(custom)
                found_override = True
        return result if found_override else None

    def _is_enabled(self, stem_key: str) -> bool:
        stem_name = stem_key.rsplit("@", 1)[0]
        return bool(
            self._stem_enabled[stem_key]
            if stem_key in self._stem_enabled
            else self._stem_enabled.get(stem_name, True)
        )

    def _channel_gain(self, label: ChannelLabel) -> float:
        if label == ChannelLabel.C:
            return self._config.center_gain
        if label in {ChannelLabel.BL, ChannelLabel.BR}:
            return self._config.back_gain
        if label in {ChannelLabel.SL, ChannelLabel.SR}:
            return self._config.surround_gain
        if label in _HEIGHT_CHANNELS:
            return self._config.height_gain
        return 1.0

    def _height_send(self, signal: np.ndarray) -> np.ndarray:
        low = sosfilt(self._height_low_sos, signal)
        shaped = signal - low * (1.0 - self._config.height_low_rolloff_gain)
        high = sosfilt(self._height_high_sos, shaped)
        return shaped + high * (self._config.height_high_shelf_gain - 1.0)

    def route(
        self,
        stems: dict[str, np.ndarray],
        n_samples: int,
        passthrough_channels: set[str] | None = None,
    ) -> dict[str, np.ndarray]:
        """Mix stems into output channels.

        Args:
            stems: Dict "StemName[@zone]" → ndarray (n_samples, 2) stereo float.
            n_samples: Expected output length.
            passthrough_channels: Channel names to skip (injected directly by caller).
        Returns:
            Dict channel_name → 1D float64 array of length n_samples.
        """
        skip = passthrough_channels or set()
        channels: dict[str, np.ndarray] = {
            label.value: np.zeros(n_samples, dtype=np.float64)
            for label in self._fmt.channels
        }
        lfe_bus = np.zeros(n_samples, dtype=np.float64)

        for stem_key, audio in stems.items():
            if not self._is_enabled(stem_key):
                continue
            stem_name = stem_key.rsplit("@", 1)[0]
            stem_routing = self._routing_for(stem_key)

            if not stem_routing:
                continue

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64, copy=False)
            stem_R = audio[:n, 1].astype(np.float64, copy=False) if audio.shape[1] > 1 else stem_L
            stem_mono = (stem_L + stem_R) * 0.5
            needs_surround = any(
                label in _SURROUND_CHANNELS and label.value in stem_routing
                for label in self._fmt.channels
            )
            needs_height = any(
                label in _HEIGHT_CHANNELS and label.value in stem_routing
                for label in self._fmt.channels
            )
            surround_L = (
                diffuse_send(sosfilt(self._surround_sos, stem_L), self._sr, delay_ms=31.0)
                if needs_surround else stem_L
            )
            surround_R = (
                diffuse_send(sosfilt(self._surround_sos, stem_R), self._sr, delay_ms=37.0)
                if needs_surround else stem_R
            )
            height_L = (
                diffuse_send(self._height_send(stem_L), self._sr, delay_ms=23.0)
                if needs_height else stem_L
            )
            height_R = (
                diffuse_send(self._height_send(stem_R), self._sr, delay_ms=29.0)
                if needs_height else stem_R
            )

            c_redirect: float = 0.0
            if "C" in skip and "C" in stem_routing and stem_name in _VOCAL_STEM_NAMES:
                c_redirect = stem_routing["C"] * 0.5

            route_items: list[tuple[ChannelLabel, float, np.ndarray]] = []
            for label in self._fmt.channels:
                ch = label.value
                if ch in skip or ch not in stem_routing:
                    continue

                gain = stem_routing[ch] * self._channel_gain(label)
                if c_redirect > 0.0 and label in (ChannelLabel.FL, ChannelLabel.FR):
                    gain += c_redirect

                if label == ChannelLabel.LFE:
                    lfe_bus[:n] += gain * stem_mono
                elif label in _LEFT_CHANNELS:
                    signal = height_L if label in _HEIGHT_CHANNELS else (
                        surround_L if label in _SURROUND_CHANNELS else stem_L
                    )
                    route_items.append((label, gain, signal))
                elif label in _RIGHT_CHANNELS:
                    signal = height_R if label in _HEIGHT_CHANNELS else (
                        surround_R if label in _SURROUND_CHANNELS else stem_R
                    )
                    route_items.append((label, gain, signal))
                elif label == ChannelLabel.C:
                    route_items.append((label, gain, stem_mono))

            input_energy = float(np.dot(stem_L, stem_L) + np.dot(stem_R, stem_R))
            routed_energy = sum(
                gain * gain * float(np.dot(signal, signal))
                for _, gain, signal in route_items
            )
            route_scale = np.sqrt(input_energy / routed_energy) if routed_energy > 1e-20 else 1.0
            for label, gain, signal in route_items:
                channels[label.value][:n] += route_scale * gain * signal

        if "LFE" in channels:
            channels["LFE"] += self._lfe_gain * sosfilt(self._lfe_sos, lfe_bus)

        return channels

    def get_routing(self, stem_key: str) -> dict[str, float] | None:
        """Return effective routing dict for a stem key ("StemName" or "StemName@zone")."""
        return self._routing_for(stem_key)
