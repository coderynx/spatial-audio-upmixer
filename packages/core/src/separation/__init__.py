"""Public stem-separation and static routing helpers."""

from upmixer.separation.stem_placement import (
    STEM_ROUTING_PRESET_NAMES,
    resolve_placements,
)
from upmixer.separation.stem_router import (
    apply_stem_pan,
    build_stem_routing,
    default_lfe_send,
    fold_route_to_stereo,
)
from upmixer.separation.prepared_stems import render_prepared_stem_bed

__all__ = [
    "STEM_ROUTING_PRESET_NAMES",
    "apply_stem_pan",
    "build_stem_routing",
    "default_lfe_send",
    "fold_route_to_stereo",
    "resolve_placements",
    "render_prepared_stem_bed",
]
