"""Web-owned conversion from draggable stem positions to speaker gains."""

from __future__ import annotations

from typing import Any

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel
from upmixer.separation import default_lfe_send
from upmixer.separation.stem_placement import (
    SCENE_PLACEMENT_SPREAD_DEG,
    StemPlacement,
    placement_route,
)


def merge_scene(scene: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(scene)
    merged_stems = dict(scene.get("stems", {}))
    merged_stems.update(overrides.get("stems", {}))
    merged["stems"] = merged_stems
    return merged


def routing_for_scene(scene: dict[str, Any], config: UpmixConfig) -> dict[str, dict[str, float]]:
    """Build constant-power speaker maps for positioned project stems.

    A dragged position is a zero-width placement — the same panner the routing
    presets go through, so a hand-placed stem and a preset-placed one are
    positioned by identical maths.
    """
    stems = scene.get("stems", {})
    if not isinstance(stems, dict):
        return {}
    # Binaural rendering collapses config.output_format's own bed to stereo
    # after routing/mastering, so routing always targets that bed directly —
    # config.output_format is a real speaker layout even when binaural is on.
    out_fmt = FORMAT_MAP[config.output_format]
    labels = [label for label in out_fmt.channels if label != ChannelLabel.LFE]
    if not labels:
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
        manifest_route = (config.stem_routing or {}).get(str(stem))
        lfe = (
            manifest_route["LFE"]
            if manifest_route and "LFE" in manifest_route
            else default_lfe_send(str(stem))
        )
        placement = StemPlacement(
            azimuth_deg=float(raw.get("azimuth_deg", 0.0)),
            elevation_deg=float(raw.get("elevation_deg", 0.0)),
            width_deg=0.0,
            spread_deg=SCENE_PLACEMENT_SPREAD_DEG,
            lfe=lfe,
        )
        mapping = {label.value: 0.0 for label in labels}
        mapping.update(placement_route(placement, out_fmt))
        if ChannelLabel.LFE in out_fmt.channels:
            # Written even at zero: this map merges *over* the built-in route,
            # so an absent key would let the default send back in.
            mapping[ChannelLabel.LFE.value] = lfe
        output[str(stem)] = mapping
    return output
