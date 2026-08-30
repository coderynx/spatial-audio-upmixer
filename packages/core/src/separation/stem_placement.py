"""Routing presets as per-layout stem placements.

A preset holds one canonical placement per stem — where the stem sits in the
listener's sphere, how wide its image is, and how much of it goes to LFE. The
preset's *realization* is layout-dependent: ``resolve_placements`` projects the
canonical table onto what a speaker layout can actually reproduce, so every
layout gets its own position table, and ``preset_routing`` pans that table into
speaker gains.

Placement model
  ``azimuth_deg``/``elevation_deg`` is the image centre in the geometry
  convention of ``upmixer.binaural.geometry`` — 0 = front, positive azimuth =
  left, positive elevation = up.  ``width_deg`` is the image's left/right
  extent: the stem renders as an arc of virtual sources spanning
  ``azimuth ± width/2``, so a stem can be L/R-dominant with only a center fill
  rather than collapsing onto the nearest speaker. ``object_size`` is the
  normalized ADM Cartesian extent shared with the renderer.

The preset tables and the projection live in ``packages/dsp``
(``spatial::presets`` and ``spatial::panner``) so the export pipeline and the
web preview voice stems identically; this module mirrors them as Python
dataclasses and keeps the layout validation, which is a boundary concern.
"""
from __future__ import annotations

from dataclasses import dataclass

import upmixer_dsp

from upmixer.formats import FORMAT_MAP, OutputFormat

STEREO_PLACEMENT_LAYOUT = "7.1.4"
"""Two-channel output resolves against the full layout and is folded by the
caller (``stem_router.build_stem_routing``), so a stereo mix keeps the same
relative image the immersive layouts get."""

SCENE_OBJECT_SIZE = 0.0
"""ADM extent for a single dragged scene position, which starts as a point."""

MINIMUM_SEND: float = upmixer_dsp.MINIMUM_SEND
"""Sends below this are dropped: without a floor a wide placement's outermost
virtual sources leave dust in channels the image does not reach."""

HEIGHT_FLATTEN_WIDTH_FACTOR: float = upmixer_dsp.HEIGHT_FLATTEN_WIDTH_FACTOR
"""Degrees of image width a layout with no height pair gets back per degree of
elevation it cannot reproduce."""


@dataclass(frozen=True)
class StemPlacement:
    """Where one stem sits, before any layout is applied."""

    azimuth_deg: float
    elevation_deg: float
    width_deg: float
    object_size: float
    lfe: float = 0.0


def _placement(values: tuple[float, float, float, float, float]) -> StemPlacement:
    return StemPlacement(*values)


STEM_ROUTING_PRESET_NAMES: tuple[str, ...] = tuple(upmixer_dsp.preset_names())

STEM_ROUTING_PRESETS: dict[str, dict[str, StemPlacement]] = {
    preset: {stem: _placement(values) for stem, values in upmixer_dsp.preset_placements(preset)}
    for preset in STEM_ROUTING_PRESET_NAMES
}
"""Every preset's canonical table, mirrored from the shared core."""

BALANCED_PLACEMENTS: dict[str, StemPlacement] = STEM_ROUTING_PRESETS["balanced"]
"""The reference table. Every other preset is a deliberate departure from it."""


def _channel_names(output_format: OutputFormat) -> list[str]:
    return [label.value for label in output_format.channels]


def resolve_placements(preset: str, layout: str) -> dict[str, StemPlacement]:
    """Return *preset*'s per-stem placements as realized on *layout*."""
    if preset not in STEM_ROUTING_PRESETS:
        raise ValueError(
            f"Unknown stem routing preset '{preset}'. Valid: {STEM_ROUTING_PRESET_NAMES}"
        )
    if layout not in FORMAT_MAP:
        raise ValueError(f"Unknown channel layout '{layout}'")
    output_format = FORMAT_MAP[layout if layout != "stereo" else STEREO_PLACEMENT_LAYOUT]
    channels = _channel_names(output_format)
    return {
        stem: _placement(
            upmixer_dsp.project_placement(
                placement.azimuth_deg,
                placement.elevation_deg,
                placement.width_deg,
                placement.object_size,
                placement.lfe,
                channels,
            )
        )
        for stem, placement in STEM_ROUTING_PRESETS[preset].items()
    }


def placement_route(placement: StemPlacement, output_format: OutputFormat) -> dict[str, float]:
    """Pan one placement into *output_format*'s speakers, constant power.

    The panning itself is MDAP (see ``stem_panner``); this adds the send floor
    and the LFE passthrough, and renormalizes so dropping the floored sends
    does not cost the map its constant power.
    """
    channels = _channel_names(output_format)
    gains = upmixer_dsp.placement_route(
        placement.azimuth_deg,
        placement.elevation_deg,
        placement.width_deg,
        placement.object_size,
        placement.lfe,
        channels,
    )
    return {channel: gain for channel, gain in zip(channels, gains) if gain > 0.0}


def preset_routing(preset: str, output_format: OutputFormat) -> dict[str, dict[str, float]]:
    """Speaker maps for every stem of *preset*, realized on *output_format*."""
    return {
        stem: placement_route(placement, output_format)
        for stem, placement in resolve_placements(preset, output_format.name).items()
    }
