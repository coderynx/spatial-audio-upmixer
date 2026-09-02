"""Speaker-layout resolution and layout-dependent manifest shaping.

A project track carries a *set* of speaker layouts, each with its own complete
mix, master and delivery block (`ProjectTrack.layout_overrides`). Everything
here is pure manifest/ORM-attribute work with no session: `service` composes
it into the write paths.
"""

from __future__ import annotations

import copy
from typing import Any

from upmixer.codecs import DEFAULT_CODEC, validate_codec
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, validate_delivery
from upmixer.separation import preset_ambient, resolve_placements
from upmixer.separation.stem_router import (
    DEFAULT_ROUTING_PRESET,
    build_stem_routing,
    fold_route_to_stereo,
)
from upmixer_web.shared.models import Project, ProjectTrack

DEFAULT_LAYOUT = "7.1.4"


def project_default_layout(project: Project) -> str:
    """The layout a track inherits when it has none of its own. No longer a
    user-facing control — it is the seed a new track's first layout starts
    from, and the export root's own value."""
    manifest = project.manifest if isinstance(project.manifest, dict) else {}
    mixing = manifest.get("mixing", {}) if isinstance(manifest.get("mixing"), dict) else {}
    return str(mixing.get("channel_layout") or DEFAULT_LAYOUT)


def track_layouts(track: ProjectTrack, project: Project) -> list[str]:
    """A track's speaker layouts, in first-use order. A track that has never
    been given one of its own stands in the project's default layout, so
    every track always answers with at least one."""
    return list(track.layout_overrides) or [project_default_layout(project)]


def track_prepare_overrides(track: ProjectTrack) -> dict[str, Any]:
    """The override block that stands for the whole track where the speaker
    layout is not what varies — stem separation and reference matching.

    Separation settings are per track, not per layout: a new layout is seeded
    from the track's existing mix (`service.set_track_layouts`), so every
    layout on a track carries the same `engine` block and the first one
    answers for all.
    """
    return next(iter(track.layout_overrides.values()), {})


def migrate_legacy_binaural_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fold older stored shapes of the binaural render into the current one.

    Binaural has moved twice: originally a ``mixing.channel_layout: binaural``
    value with the real bed under ``mixing.binaural.bed``, then briefly an
    independent ``mixing.binaural.enabled`` flag — it is now
    ``format.type: binaural`` (a delivery format, alongside ``wav``/
    ``adm-bwf``) with ``format.binaural.profile``. Migrate in place so
    previously stored projects keep validating and round-tripping.
    """
    manifest = copy.deepcopy(manifest)
    mixing = manifest.get("mixing")
    if not isinstance(mixing, dict):
        return manifest
    legacy_binaural = mixing.pop("binaural", None)
    if not isinstance(legacy_binaural, dict):
        return manifest
    was_binaural = mixing.get("channel_layout") == "binaural" or legacy_binaural.get("enabled") is True
    if mixing.get("channel_layout") == "binaural":
        mixing["channel_layout"] = legacy_binaural.get("bed", DEFAULT_LAYOUT)
    if not was_binaural:
        return manifest
    format_block = manifest.setdefault("format", {})
    format_block["type"] = "binaural"
    format_block["binaural"] = {"profile": legacy_binaural.get("profile", "studio")}
    return manifest


def delivery_type_for_layout(channel_layout: str, output_type: str) -> str:
    """Fall back to a multichannel bed when a delivery type cannot carry the layout.

    A track's speaker layout is its primary control — it drives routing, the
    spatial views and the preview engine — so narrowing it (7.1.4 to 5.1, or to
    stereo) retargets a delivery type the new layout cannot carry instead of
    rejecting the edit over a field the user did not touch. Explicit job
    manifests and CLI flags stay strict; ``formats.validate_delivery`` still
    rejects them.
    """
    try:
        validate_delivery(channel_layout, output_type)
    except ValueError:
        return "multichannel"
    return output_type


def delivery_codec_for_layout(
    channel_layout: str, output_type: str, output_codec: str, output_subtype: str
) -> str:
    """Fall back to WAV when a stored codec cannot carry the layout.

    Same reasoning as :func:`delivery_type_for_layout`: widening a layout past
    FLAC's 8-channel cap retargets the codec rather than rejecting the edit.
    """
    try:
        validate_codec(channel_layout, output_type, output_codec, output_subtype)
    except ValueError:
        return DEFAULT_CODEC
    return output_codec


def normalize_layout_mix(block: dict[str, Any], layout: str, stems: list[str]) -> dict[str, Any]:
    """Apply everything that depends on the speaker layout to one manifest or
    override block: pin the layout, retarget a delivery it cannot carry, and
    build or fold `mixing.stem_routing` onto that layout's own channel set.

    The two-channel fold is load-bearing, not cosmetic: the client preview
    reads routing only from the manifest while the export folds the built-in
    base route, so an unfolded route on a `stereo` layout previews several dB
    below the render — see `docs/project_manifest_parity.md`, "Two-channel
    (`stereo`) layouts".
    """
    if layout not in FORMAT_MAP:
        raise ValueError("Unknown channel layout")
    mixing = block.setdefault("mixing", {})
    mixing["channel_layout"] = layout
    format_block = block.setdefault("format", {})
    output_type = delivery_type_for_layout(
        layout, str(format_block.get("type", "multichannel"))
    )
    format_block["type"] = output_type
    format_block["codec"] = delivery_codec_for_layout(
        layout,
        output_type,
        str(format_block.get("codec", DEFAULT_CODEC)),
        str(format_block.get("subtype", UpmixConfig.output_subtype)),
    )
    routing_fmt = FORMAT_MAP[layout]
    if not mixing.get("stem_routing"):
        mixing["stem_routing"] = build_stem_routing(stems, routing_fmt)
    elif routing_fmt.n_channels == 2:
        mixing["stem_routing"] = {
            stem: fold_route_to_stereo(route)
            for stem, route in mixing["stem_routing"].items()
        }
    return block


def seed_balanced_mix(block: dict[str, Any], layout: str, stems: list[str]) -> dict[str, Any]:
    """Complete a mix with the shared balanced placement and room defaults."""
    normalize_layout_mix(block, layout, stems)
    mixing = block["mixing"]
    stem_routing = mixing["stem_routing"]
    if stem_routing:
        for stem, route in build_stem_routing(stems, FORMAT_MAP[layout]).items():
            stem_routing.setdefault(stem, route)
    placements = resolve_placements(DEFAULT_ROUTING_PRESET, layout)
    ambient = preset_ambient(DEFAULT_ROUTING_PRESET)
    placement_map = mixing.setdefault("stem_placement", {})
    rear_map = mixing.setdefault("stem_ambient_rear", {})
    height_map = mixing.setdefault("stem_ambient_height", {})
    crossover_map = mixing.setdefault("stem_ambient_height_crossover_hz", {})
    for stem in stems:
        base = stem.split("@", 1)[0]
        placement = placements.get(base)
        if placement is None:
            continue
        placement_map.setdefault(stem, {
            "azimuth_deg": placement.azimuth_deg,
            "elevation_deg": placement.elevation_deg,
            "width_deg": placement.width_deg,
            "object_size": placement.object_size,
            "diversity": placement.diversity,
            "center_level_db": placement.center_level_db,
        })
        sends = ambient.get(base)
        if sends is None:
            continue
        rear, height, crossover = sends
        rear_map.setdefault(stem, rear)
        height_map.setdefault(stem, height)
        crossover_map.setdefault(stem, crossover)
    return block
