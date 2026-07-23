"""Web-owned conversion from draggable stem positions to speaker gains."""

from __future__ import annotations

import math
from typing import Any

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel


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


def routing_for_scene(scene: dict[str, Any], config: UpmixConfig) -> dict[str, dict[str, float]]:
    """Build constant-power speaker maps for positioned project stems."""
    stems = scene.get("stems", {})
    if not isinstance(stems, dict):
        return {}
    labels = [label for label in FORMAT_MAP[config.output_format].channels if label != ChannelLabel.LFE]
    available = [(label, _POSITIONS[label]) for label in labels if label in _POSITIONS]
    if not available:
        return {}
    output: dict[str, dict[str, float]] = {}
    for stem, raw in stems.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled", True) is False:
            output[str(stem)] = {label.value: 0.0 for label in FORMAT_MAP[config.output_format].channels}
            continue
        if "azimuth_deg" not in raw:
            continue
        azimuth = float(raw.get("azimuth_deg", 0.0))
        elevation = float(raw.get("elevation_deg", 0.0))
        ranked = sorted(
            available,
            key=lambda item: (item[1][0] - azimuth) ** 2 + (item[1][1] - elevation) ** 2,
        )[: min(3, len(available))]
        weights = [1.0 / max(1.0, math.dist((azimuth, elevation), position)) for _, position in ranked]
        norm = math.sqrt(sum(weight * weight for weight in weights)) or 1.0
        mapping = {label.value: 0.0 for label in labels}
        for (label, _), weight in zip(ranked, weights, strict=True):
            mapping[label.value] = weight / norm
        output[str(stem)] = mapping
    return output
