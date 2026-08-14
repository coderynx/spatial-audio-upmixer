"""Web-owned conversion from draggable stem positions to speaker gains."""

from __future__ import annotations

import math
from typing import Any

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel
from upmixer.separation import default_lfe_send


_POSITIONS: dict[ChannelLabel, tuple[float, float]] = {
    ChannelLabel.FL: (30.0, 0.0), ChannelLabel.FR: (-30.0, 0.0),
    ChannelLabel.C: (0.0, 0.0), ChannelLabel.SL: (110.0, 0.0),
    ChannelLabel.SR: (-110.0, 0.0), ChannelLabel.BL: (135.0, 0.0),
    ChannelLabel.BR: (-135.0, 0.0), ChannelLabel.TFL: (45.0, 35.0),
    ChannelLabel.TFR: (-45.0, 35.0), ChannelLabel.TBL: (135.0, 35.0),
    ChannelLabel.TBR: (-135.0, 35.0),
}


def merge_scene(scene: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(scene)
    merged_stems = dict(scene.get("stems", {}))
    merged_stems.update(overrides.get("stems", {}))
    merged["stems"] = merged_stems
    return merged


def _angular_distance(azimuth: float, elevation: float, position: tuple[float, float]) -> float:
    """Degree-space distance with azimuth wrapped to ±180° — BL/TBL sit at
    +135° and BR/TBR at −135°, so an unwrapped difference puts the far rear
    pair ~315° away and drops one whole side from the nearest-3 selection."""
    delta_azimuth = (position[0] - azimuth + 180.0) % 360.0 - 180.0
    return math.hypot(delta_azimuth, position[1] - elevation)


def routing_for_scene(scene: dict[str, Any], config: UpmixConfig) -> dict[str, dict[str, float]]:
    """Build constant-power speaker maps for positioned project stems."""
    stems = scene.get("stems", {})
    if not isinstance(stems, dict):
        return {}
    # Binaural rendering collapses config.output_format's own bed to stereo
    # after routing/mastering, so routing always targets that bed directly —
    # config.output_format is a real speaker layout even when binaural is on.
    out_fmt = FORMAT_MAP[config.output_format]
    labels = [label for label in out_fmt.channels if label != ChannelLabel.LFE]
    available = [(label, _POSITIONS[label]) for label in labels if label in _POSITIONS]
    if not available:
        return {}
    output: dict[str, dict[str, float]] = {}
    for stem, raw in stems.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled", True) is False:
            output[str(stem)] = {label.value: 0.0 for label in out_fmt.channels}
            continue
        if "azimuth_deg" not in raw:
            continue
        azimuth = float(raw.get("azimuth_deg", 0.0))
        elevation = float(raw.get("elevation_deg", 0.0))
        ranked = sorted(
            available,
            key=lambda item: _angular_distance(azimuth, elevation, item[1]),
        )[: min(3, len(available))]
        weights = [1.0 / max(1.0, _angular_distance(azimuth, elevation, position)) for _, position in ranked]
        norm = math.sqrt(sum(weight * weight for weight in weights)) or 1.0
        mapping = {label.value: 0.0 for label in labels}
        for (label, _), weight in zip(ranked, weights, strict=True):
            mapping[label.value] = weight / norm
        if ChannelLabel.LFE in out_fmt.channels:
            manifest_route = (config.stem_routing or {}).get(str(stem))
            mapping["LFE"] = (
                manifest_route["LFE"]
                if manifest_route and "LFE" in manifest_route
                else default_lfe_send(str(stem))
            )
        output[str(stem)] = mapping
    return output
