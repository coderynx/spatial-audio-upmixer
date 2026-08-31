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

import math

import numpy as np
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel, OutputFormat
from upmixer.io.adm_writer import AdmObject
from upmixer.manifest import register_block as _rb
from upmixer.loudness import CHANNEL_WEIGHT, k_weighted_power
from upmixer.separation.stem_placement import (
    STEM_ROUTING_PRESET_NAMES,
    STEM_ROUTING_PRESETS,
    StemPlacement,
    placement_route,
    preset_routing,
)
from upmixer.utils import (
    HEIGHT_VELVET_SEED,
    SURROUND_VELVET_SEED,
    velvet_send,
)

_rb("routing", {
    "center_gain":            ("config", "center_gain"),
    "surround_gain":          ("config", "surround_gain"),
    "back_gain":              ("config", "back_gain"),
    "height_gain":            ("config", "height_gain"),
    "lfe_gain":               ("config", "lfe_gain"),
    "lfe_cutoff":             ("config", "lfe_cutoff"),
    "height_low_rolloff_gain":("config", "height_low_rolloff_gain"),
    "height_high_shelf_gain": ("config", "height_high_shelf_gain"),
    "height_directional_band_gain": ("config", "height_directional_band_gain"),
})
del _rb

DEFAULT_ROUTING_PRESET = "balanced"
DEFAULT_ROUTING_LAYOUT = "7.1.4"

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

_SURROUND_CHANNELS = {ChannelLabel.SL, ChannelLabel.SR, ChannelLabel.BL, ChannelLabel.BR}
_HEIGHT_CHANNELS   = {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}

_VOCAL_STEM_NAMES: frozenset[str] = frozenset({
    "Vocals", "Lead Vocals", "Backing Vocals",
})

_BED_STEM_NAMES: frozenset[str] = frozenset({
    "Bass", "Kick", "Snare", "Other", "Crowd", "Backing Vocals", "Vocals Reverb",
})

ZONE_ROUTING: dict[str, dict[str, dict[str, float]]] = {
    "front": {
        "Vocals":         {"C": 0.72, "FL": 0.28, "FR": 0.28, "TFL": 0.08, "TFR": 0.08},
        "Bass":           {"FL": 0.65, "FR": 0.65, "C": 0.22, "LFE": 0.75},
        "Drums":          {"C": 0.22, "FL": 0.58, "FR": 0.58, "LFE": 0.32,
                           "TFL": 0.18, "TFR": 0.18},
        "Other":          {"FL": 0.38, "FR": 0.38, "SL": 0.18, "SR": 0.18,
                           "LFE": 0.15, "TFL": 0.30, "TFR": 0.30},
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
        "Vocals Reverb":  {"SL": 0.34, "SR": 0.34, "TFL": 0.26, "TFR": 0.26,
                           "TBL": 0.18, "TBR": 0.18},
    },
    "surround": {
        "Vocals":         {"SL": 0.22, "SR": 0.22, "TBL": 0.14, "TBR": 0.14},
        "Bass":           {"LFE": 0.20},
        "Drums":          {"SL": 0.52, "SR": 0.52, "BL": 0.22, "BR": 0.22,
                           "TFL": 0.15, "TFR": 0.15, "TBL": 0.22, "TBR": 0.22},
        "Other":          {"SL": 0.62, "SR": 0.62, "BL": 0.32, "BR": 0.32,
                           "LFE": 0.10, "TFL": 0.28, "TFR": 0.28, "TBL": 0.32, "TBR": 0.32},
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
        "Vocals Reverb":  {"SL": 0.40, "SR": 0.40, "BL": 0.24, "BR": 0.24,
                           "TBL": 0.26, "TBR": 0.26},
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
        "Vocals Reverb":  {"BL": 0.38, "BR": 0.38, "TBL": 0.30, "TBR": 0.30},
    },
    "height_front": {
        "Vocals":         {"TFL": 0.32, "TFR": 0.32},
        "Bass":           {"FL": 0.45, "FR": 0.45, "LFE": 0.70},
        "Drums":          {"TFL": 0.58, "TFR": 0.58, "TBL": 0.18, "TBR": 0.18},
        "Other":          {"TFL": 0.68, "TFR": 0.68, "TBL": 0.28, "TBR": 0.28},
        "Guitar":         {"TFL": 0.45, "TFR": 0.45, "TBL": 0.10, "TBR": 0.10},
        "Piano":          {"TFL": 0.42, "TFR": 0.42},
        "Instrumental":   {"TFL": 0.52, "TFR": 0.52, "TBL": 0.18, "TBR": 0.18},
        "Lead Vocals":    {"TFL": 0.22, "TFR": 0.22},
        "Backing Vocals": {"TFL": 0.50, "TFR": 0.50},
        "Kick":           {"FL": 0.45, "FR": 0.45, "LFE": 0.80},
        "Snare":          {"TFL": 0.15, "TFR": 0.15},
        "Toms":           {"TFL": 0.22, "TFR": 0.22},
        "Hi-Hat":         {"TFL": 0.72, "TFR": 0.72},
        "Ride":           {"TFL": 0.68, "TFR": 0.68},
        "Crash":          {"TFL": 0.80, "TFR": 0.80, "TBL": 0.25, "TBR": 0.25},
        "Crowd":          {"TFL": 0.20, "TFR": 0.20, "TBL": 0.12, "TBR": 0.12},
        "Vocals Reverb":  {"TFL": 0.52, "TFR": 0.52, "TBL": 0.26, "TBR": 0.26,
                           "SL": 0.16, "SR": 0.16},
    },
    "height_back": {
        "Vocals":         {"TBL": 0.25, "TBR": 0.25},
        "Bass":           {"FL": 0.45, "FR": 0.45, "LFE": 0.70},
        "Drums":          {"TBL": 0.52, "TBR": 0.52},
        "Other":          {"TBL": 0.72, "TBR": 0.72},
        "Guitar":         {"TBL": 0.42, "TBR": 0.42},
        "Piano":          {"TBL": 0.38, "TBR": 0.38},
        "Instrumental":   {"TBL": 0.58, "TBR": 0.58},
        "Lead Vocals":    {"TBL": 0.10, "TBR": 0.10},
        "Backing Vocals": {"TBL": 0.50, "TBR": 0.50},
        "Kick":           {"FL": 0.45, "FR": 0.45, "LFE": 0.80},
        "Snare":          {"TBL": 0.12, "TBR": 0.12},
        "Toms":           {"TBL": 0.18, "TBR": 0.18},
        "Hi-Hat":         {"TBL": 0.52, "TBR": 0.52},
        "Ride":           {"TBL": 0.62, "TBR": 0.62},
        "Crash":          {"TBL": 0.75, "TBR": 0.75},
        "Crowd":          {"TBL": 0.28, "TBR": 0.28},
        "Vocals Reverb":  {"TBL": 0.58, "TBR": 0.58, "SL": 0.14, "SR": 0.14},
    },
}


DEFAULT_ROUTING: dict[str, dict[str, float]] = preset_routing(
    DEFAULT_ROUTING_PRESET, FORMAT_MAP[DEFAULT_ROUTING_LAYOUT]
)
"""Fallback route per stem when nothing else supplies one — the default preset
realized on the widest layout, so the built-in fallback and the preset a user
applies are the same placement."""


def build_stem_routing(
    stems: list[str],
    output_format: OutputFormat,
    preset: str = DEFAULT_ROUTING_PRESET,
) -> dict[str, dict[str, float]]:
    """Return explicit speaker maps for a stem-routing preset.

    The preset's placements are resolved for *output_format*'s layout and
    panned into its speakers; two-channel output is folded here, having been
    panned across the full layout first (see ``stem_placement``).
    """
    if preset not in STEM_ROUTING_PRESET_NAMES:
        raise ValueError(
            f"Unknown stem routing preset '{preset}'. Valid: {STEM_ROUTING_PRESET_NAMES}"
        )
    channels = [label.value for label in output_format.channels]
    routing = upmixer_dsp.build_stem_routing(stems, channels, preset)
    return {
        stem: {channel: gain for channel, gain in zip(channels, gains) if gain > 0.0}
        for stem, gains in routing
    }


def fold_route_to_stereo(route: dict[str, float]) -> dict[str, float]:
    """Collapse a speaker map onto FL/FR for a 2-channel output format.

    Only the resulting left/right *ratio* is meaningful: ``StemRouter.route``
    renormalizes each stem to its own loudness afterwards, so the side weights
    are a pan law, not the BS.775-4 level law. Idempotent.
    """
    channels = list(route)
    left, right = upmixer_dsp.fold_route_to_stereo(
        [route[channel] for channel in channels], channels
    )
    return {ChannelLabel.FL.value: left, ChannelLabel.FR.value: right}


def apply_stem_pan(route: dict[str, float], pan: float) -> dict[str, float]:
    """Return *route* with its FL/FR pair repositioned by a constant-power pan.

    ``pan`` is 0.0 (hard left) to 1.0 (hard right), 0.5 centred. The pair's
    combined magnitude is preserved so the stem's balance against any other
    channels in the route is unchanged.

    A gain-table edit, not a placement move: this is the CLI's ``--stem-pan``
    flag, which has no placement to rotate.
    """
    angle = min(1.0, max(0.0, pan)) * (math.pi / 2.0)
    magnitude = math.hypot(
        route.get(ChannelLabel.FL.value, 0.0), route.get(ChannelLabel.FR.value, 0.0)
    ) or 1.0
    return {
        **route,
        ChannelLabel.FL.value: magnitude * math.cos(angle),
        ChannelLabel.FR.value: magnitude * math.sin(angle),
    }


def default_lfe_send(stem_key: str) -> float:
    """Return the built-in LFE send weight for a stem (ignores overrides).

    Resolves "StemName@zone" against ``ZONE_ROUTING`` first, falling back to
    ``DEFAULT_ROUTING`` by stem name — same precedence as
    ``StemRouter._routing_for`` without the manifest/custom override layers.
    """
    stem_name, _, zone = stem_key.partition("@")
    if zone:
        zone_routing = ZONE_ROUTING.get(zone, {}).get(stem_name)
        if zone_routing is not None:
            return zone_routing.get("LFE", 0.0)
    return DEFAULT_ROUTING.get(stem_name, {}).get("LFE", 0.0)


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
        self._ambient_rear = config.stem_ambient_rear or {}
        self._ambient_height = config.stem_ambient_height or {}
        self._ambient_height_crossover = config.stem_ambient_height_crossover_hz or {}
        self._stem_solo = set(config.stem_solo or [])
        self._sr = sample_rate
        self._lfe_gain = config.lfe_gain

    def _object_placement_for(self, stem_key: str) -> StemPlacement | None:
        stem_name = stem_key.rsplit("@", 1)[0]
        if stem_name in _BED_STEM_NAMES:
            return None
        overrides = self._config.stem_placement or {}
        raw = overrides.get(stem_key, overrides.get(stem_name, {}))
        default = STEM_ROUTING_PRESETS[DEFAULT_ROUTING_PRESET].get(stem_name)
        if not raw and default is None:
            return None
        placement = StemPlacement(
            float(raw.get("azimuth_deg", default.azimuth_deg if default else 0.0)),
            float(raw.get("elevation_deg", default.elevation_deg if default else 0.0)),
            float(raw.get("width_deg", default.width_deg if default else 0.0)),
            float(raw.get("object_size", default.object_size if default else 0.0)),
            diversity=float(raw.get("diversity", 0.0)),
            center_level_db=float(raw.get("center_level_db", 0.0)),
        )
        return placement

    def _object_routes_for(self, stem_key: str) -> list[dict[str, float]] | None:
        placement = self._object_placement_for(stem_key)
        if placement is None:
            return None
        stem_name = stem_key.rsplit("@", 1)[0]
        mode = (self._config.stem_object_mode or {}).get(
            stem_key, (self._config.stem_object_mode or {}).get(stem_name, "linked-stereo")
        )
        metadata = self._object_metadata_for(stem_key)
        if mode == "mono":
            labels = [label.value for label in self._fmt.channels if label != ChannelLabel.LFE]
            mono, _ = upmixer_dsp.adm_object_routes(
                placement.azimuth_deg, placement.elevation_deg, 0.0,
                placement.object_size, metadata[2], list(metadata[3]), labels,
            )
            return [{label: gain for label, gain in zip(labels, mono) if gain > 0.0}]
        labels = [label.value for label in self._fmt.channels if label != ChannelLabel.LFE]
        left, right = upmixer_dsp.adm_object_routes(
            placement.azimuth_deg, placement.elevation_deg, placement.width_deg,
            placement.object_size, metadata[2], list(metadata[3]), labels,
        )
        return [
            {label: gain for label, gain in zip(labels, left) if gain > 0.0},
            {label: gain for label, gain in zip(labels, right) if gain > 0.0},
        ]

    def _object_metadata_for(
        self, stem_key: str,
    ) -> tuple[float, int, bool, tuple[str, ...]]:
        stem_name = stem_key.rsplit("@", 1)[0]
        table = self._config.stem_object_metadata or {}
        raw = table.get(stem_key, table.get(stem_name, {}))
        return (
            float(raw.get("gain", 1.0)),
            int(raw.get("importance", 10)),
            bool(raw.get("channel_lock", False)),
            tuple(raw.get("zone_exclusion", ())),
        )

    def _adm_objects_for(
        self,
        stem_key: str,
        left: np.ndarray,
        right: np.ndarray,
        gain: float,
    ) -> list[AdmObject]:
        placement = self._object_placement_for(stem_key)
        if placement is None:
            return []
        stem_name = stem_key.rsplit("@", 1)[0]
        mode = (self._config.stem_object_mode or {}).get(
            stem_key, (self._config.stem_object_mode or {}).get(stem_name, "linked-stereo")
        )
        metadata = self._object_metadata_for(stem_key)

        def position(azimuth_deg: float) -> tuple[float, float, float]:
            x, y, z = upmixer_dsp.direction(azimuth_deg, placement.elevation_deg)
            return (x, -z, y)

        if mode == "mono":
            return [
                AdmObject(
                    stem_key, gain * (left + right) * 0.5,
                    position(placement.azimuth_deg), placement.object_size,
                    gain=metadata[0], importance=metadata[1],
                    channel_lock=metadata[2], zone_exclusion=metadata[3],
                )
            ]
        half_width = placement.width_deg * 0.5
        return [
            AdmObject(
                f"{stem_key} Left", gain * left,
                position(placement.azimuth_deg + half_width), placement.object_size,
                gain=metadata[0], importance=metadata[1],
                channel_lock=metadata[2], zone_exclusion=metadata[3],
            ),
            AdmObject(
                f"{stem_key} Right", gain * right,
                position(placement.azimuth_deg - half_width), placement.object_size,
                gain=metadata[0], importance=metadata[1],
                channel_lock=metadata[2], zone_exclusion=metadata[3],
            ),
        ]

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

        # Folded before the overrides merge, never after: folding the merged
        # map would re-add the base's SL/BL weight on top of a user's pan.
        if base is not None and self._fmt.n_channels == 2:
            base = fold_route_to_stereo(base)

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

    def _ambient_for(self, stem_key: str) -> tuple[float, float]:
        """Ambient send amounts for a stem, zone key first."""
        stem_name = stem_key.rsplit("@", 1)[0]

        def amount(table: dict) -> float:
            value = table[stem_key] if stem_key in table else table.get(stem_name, 0.0)
            return max(0.0, min(1.0, float(value)))

        return amount(self._ambient_rear), amount(self._ambient_height)

    def _ambient_height_crossover_for(self, stem_key: str) -> float:
        stem_name = stem_key.rsplit("@", 1)[0]
        value = self._ambient_height_crossover.get(
            stem_key, self._ambient_height_crossover.get(stem_name, 2000.0)
        )
        return float(value)

    def _class_share(self, labels: set[ChannelLabel]) -> float:
        """Per-speaker share of an ambient send: the amount is spread over the
        class as 1/sqrt(n), so a 7.1.4's four surrounds carry the same total as
        a 5.1's two.  Mirrors ``EngineParams::ambient_share``."""
        count = sum(1 for label in self._fmt.channels if label in labels)
        return 1.0 / math.sqrt(count) if count else 0.0

    def _is_enabled(self, stem_key: str) -> bool:
        stem_name = stem_key.rsplit("@", 1)[0]
        if self._stem_solo and not {stem_key, stem_name}.intersection(self._stem_solo):
            return False
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
        return upmixer_dsp.elevation_eq(
            np.ascontiguousarray(signal, dtype=np.float64),
            self._sr,
            self._config.height_low_rolloff_hz,
            self._config.height_low_rolloff_gain,
            self._config.height_crossover_hz,
            self._config.height_high_shelf_gain,
            self._config.height_directional_band_hz,
            self._config.height_directional_band_gain,
        )

    def _surround_send(self, signal: np.ndarray) -> np.ndarray:
        return upmixer_dsp.highpass(
            np.ascontiguousarray(signal, dtype=np.float64),
            self._sr,
            self._config.surround_bass_cutoff_hz,
            2,
        )

    def _route_scale(
        self,
        route_items: list[tuple[ChannelLabel, float, np.ndarray]],
        stem_L: np.ndarray,
        stem_R: np.ndarray,
    ) -> float:
        """Per-stem scalar matching routed loudness to the stem's own loudness.

        Raw energy equates a band-limited surround send with a full-band front
        one and ignores BS.1770's channel weights, so a surround-routed stem
        lands up to ~4.7 LU loud at the same energy (phase 9 report). Falls
        back to raw energy when the material is too short or quiet to gate.
        LFE is outside both sums: it is added unscaled, after its lowpass.
        """
        powers: dict[int, float] = {}

        def power(signal: np.ndarray) -> float:
            key = id(signal)
            if key not in powers:
                powers[key] = k_weighted_power(signal, self._sr)
            return powers[key]

        input_power = power(stem_L) + power(stem_R)
        routed: dict[ChannelLabel, np.ndarray] = {}
        for label, gain, signal in route_items:
            contribution = gain * signal
            routed[label] = routed[label] + contribution if label in routed else contribution
        routed_power = sum(
            CHANNEL_WEIGHT.get(label, 1.0) * power(signal)
            for label, signal in routed.items()
        )
        if input_power > 0.0 and routed_power > 0.0:
            return math.sqrt(input_power / routed_power)

        input_energy = float(np.dot(stem_L, stem_L) + np.dot(stem_R, stem_R))
        routed_energy = sum(float(np.dot(signal, signal)) for signal in routed.values())
        return math.sqrt(input_energy / routed_energy) if routed_energy > 1e-20 else 1.0

    def route(
        self,
        stems: dict[str, np.ndarray],
        n_samples: int,
        passthrough_channels: set[str] | None = None,
        object_tracks: list[AdmObject] | None = None,
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
            object_routes = self._object_routes_for(stem_key)

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64, copy=False)
            stem_R = audio[:n, 1].astype(np.float64, copy=False) if audio.shape[1] > 1 else stem_L
            rear_amount, height_amount = self._ambient_for(stem_key)
            rear_share = self._class_share(_SURROUND_CHANNELS)
            height_share = self._class_share(_HEIGHT_CHANNELS)
            if not rear_share:
                rear_amount = 0.0
            if not height_share:
                height_amount = 0.0
            # The stem's own level, before the sends take their share: the
            # route normalization matches the routed sum to this, or a stem
            # would get quieter as its sends come up.
            input_L, input_R = stem_L, stem_R
            ambient: dict[ChannelLabel, np.ndarray] = {}
            if rear_amount > 0.0 or height_amount > 0.0:
                # A send the layout has no speaker for gets no ambient: the
                # amount is taken out of the dry pair, so sending it nowhere
                # would be a hole rather than a move.
                rear_L, rear_R, height_L_amb, height_R_amb = upmixer_dsp.ambient_split(
                    np.ascontiguousarray(stem_L, dtype=np.float64),
                    np.ascontiguousarray(stem_R, dtype=np.float64),
                    self._sr,
                    self._ambient_height_crossover_for(stem_key),
                )
                stem_L = stem_L - rear_amount * rear_L - height_amount * height_L_amb
                stem_R = stem_R - rear_amount * rear_R - height_amount * height_R_amb
                ambient = {
                    ChannelLabel.SL: self._surround_send(rear_L),
                    ChannelLabel.SR: self._surround_send(rear_R),
                    ChannelLabel.TFL: self._height_send(height_L_amb),
                    ChannelLabel.TFR: self._height_send(height_R_amb),
                }

            stem_mono = (stem_L + stem_R) * 0.5
            needs_surround = any(
                label in _SURROUND_CHANNELS and label.value in stem_routing
                for label in self._fmt.channels
            )
            needs_height = any(
                label in _HEIGHT_CHANNELS and label.value in stem_routing
                for label in self._fmt.channels
            )
            if object_routes is not None:
                needs_surround = False
                needs_height = False
            surround_L = (
                velvet_send(self._surround_send(stem_L), self._sr, "left", SURROUND_VELVET_SEED)
                if needs_surround else stem_L
            )
            surround_R = (
                velvet_send(self._surround_send(stem_R), self._sr, "right", SURROUND_VELVET_SEED)
                if needs_surround else stem_R
            )
            height_L = (
                velvet_send(self._height_send(stem_L), self._sr, "left", HEIGHT_VELVET_SEED)
                if needs_height else stem_L
            )
            height_R = (
                velvet_send(self._height_send(stem_R), self._sr, "right", HEIGHT_VELVET_SEED)
                if needs_height else stem_R
            )

            c_redirect: float = 0.0
            if "C" in skip and "C" in stem_routing and stem_name in _VOCAL_STEM_NAMES:
                c_redirect = stem_routing["C"] * 0.5

            direct_items: list[tuple[ChannelLabel, float, np.ndarray]] = []
            for label in self._fmt.channels:
                if label.value not in skip and label == ChannelLabel.LFE:
                    lfe_bus[:n] += stem_routing.get("LFE", 0.0) * stem_mono

            if object_routes is None:
                for label in self._fmt.channels:
                    ch = label.value
                    if ch in skip or ch not in stem_routing or label == ChannelLabel.LFE:
                        continue
                    gain = stem_routing[ch] * self._channel_gain(label)
                    if c_redirect > 0.0 and label in (ChannelLabel.FL, ChannelLabel.FR):
                        gain += c_redirect
                    if label in _LEFT_CHANNELS:
                        signal = height_L if label in _HEIGHT_CHANNELS else (
                            surround_L if label in _SURROUND_CHANNELS else stem_L
                        )
                    elif label in _RIGHT_CHANNELS:
                        signal = height_R if label in _HEIGHT_CHANNELS else (
                            surround_R if label in _SURROUND_CHANNELS else stem_R
                        )
                    else:
                        signal = stem_mono
                    direct_items.append((label, gain, signal))
            else:
                direct = [stem_mono] if len(object_routes) == 1 else [stem_L, stem_R]
                for route, signal in zip(object_routes, direct):
                    for label in self._fmt.channels:
                        if label.value in skip or label == ChannelLabel.LFE:
                            continue
                        gain = route.get(label.value, 0.0) * self._channel_gain(label)
                        if gain > 0.0:
                            direct_items.append((label, gain, signal))

            route_items = list(direct_items)

            for label in self._fmt.channels:
                if label.value in skip:
                    continue
                if label in _SURROUND_CHANNELS and rear_amount > 0.0:
                    signal = ambient[
                        ChannelLabel.SL if label in _LEFT_CHANNELS else ChannelLabel.SR
                    ]
                    gain = rear_amount * rear_share * self._channel_gain(label)
                elif label in _HEIGHT_CHANNELS and height_amount > 0.0:
                    signal = ambient[
                        ChannelLabel.TFL if label in _LEFT_CHANNELS else ChannelLabel.TFR
                    ]
                    gain = height_amount * height_share * self._channel_gain(label)
                else:
                    continue
                route_items.append((label, gain, signal))

            route_scale = self._route_scale(route_items, input_L, input_R)
            if object_tracks is not None and object_routes is not None:
                object_tracks.extend(self._adm_objects_for(stem_key, stem_L, stem_R, route_scale))
                route_items = route_items[len(direct_items):]
            if self._config.spatial_downmix_lock and object_tracks is None:
                routed = {
                    label.value: np.zeros(n, dtype=np.float64) for label in self._fmt.channels
                }
                for label, gain, signal in route_items:
                    routed[label.value] += route_scale * gain * signal
                corrected = upmixer_dsp.apply_stereo_downmix_lock(
                    [label.value for label in self._fmt.channels],
                    [routed[label.value] for label in self._fmt.channels],
                    np.ascontiguousarray(input_L, dtype=np.float64),
                    np.ascontiguousarray(input_R, dtype=np.float64),
                    self._config.surround_downmix_coeff,
                    self._config.height_downmix_coeff,
                )
                for label, signal in zip(self._fmt.channels, corrected):
                    channels[label.value][:n] += signal
            else:
                for label, gain, signal in route_items:
                    channels[label.value][:n] += route_scale * gain * signal

        if "LFE" in channels:
            channels["LFE"] += self._lfe_gain * upmixer_dsp.lfe_lowpass(
                np.ascontiguousarray(lfe_bus, dtype=np.float64),
                self._sr,
                self._config.lfe_cutoff_hz,
                self._config.lfe_filter_order,
            )

        return channels

    def get_routing(self, stem_key: str) -> dict[str, float] | None:
        """Return effective routing dict for a stem key ("StemName" or "StemName@zone")."""
        return self._routing_for(stem_key)


def stem_reaches_surround_height(
    stem_key: str, output_fmt: OutputFormat
) -> tuple[bool, bool]:
    """Whether a stem's built-in routing sends to surround / height channels.

    Uses the ZONE_ROUTING / DEFAULT_ROUTING tables only (not manifest or custom
    overrides): the bleed-reduction gate runs at separation time, before any user
    3D placement exists, so it keys on a stem's default spatial role rather than
    its final routed position. Returns ``(reaches_surround, reaches_height)``
    restricted to *output_fmt*.
    """
    if "@" in stem_key:
        stem_name, zone = stem_key.rsplit("@", 1)
        zone_routing = ZONE_ROUTING.get(zone, {})
        base = (
            zone_routing[stem_name]
            if stem_name in zone_routing
            else DEFAULT_ROUTING.get(stem_name)
        )
    else:
        base = DEFAULT_ROUTING.get(stem_key)
    if not base:
        return (False, False)
    fmt_channels = {label.value for label in output_fmt.channels}
    surround = any(
        label.value in base and label.value in fmt_channels
        for label in _SURROUND_CHANNELS
    )
    height = any(
        label.value in base and label.value in fmt_channels
        for label in _HEIGHT_CHANNELS
    )
    return (surround, height)
