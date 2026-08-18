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
  rather than collapsing onto the nearest speaker.  ``spread_deg`` is how far
  each of those sources is blurred to either side (see ``stem_panner``).

Placement guidance (Dolby Atmos music practice, matching the routing
philosophy in ``stem_router``'s module docstring): lead vocal, kick, snare and
bass stay on the front wall in every preset; backing vocals, cymbals, pads and
room content are what moves to the sides and heights; sub weight goes to LFE
rather than into the spatial field.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
from upmixer.formats import FORMAT_MAP, ChannelLabel, OutputFormat
from upmixer.separation.stem_panner import panning_gains

STEREO_PLACEMENT_LAYOUT = "7.1.4"
"""Two-channel output resolves against the full layout and is folded by the
caller (``stem_router.build_stem_routing``), so a stereo mix keeps the same
relative image the immersive layouts get."""

SCENE_PLACEMENT_SPREAD_DEG = 60.0
"""Falloff radius for a single dragged scene position, which carries no width
or spread of its own. Wide enough that dragging a stem crossfades smoothly
between neighbouring speakers instead of snapping to the nearest one."""

MINIMUM_SEND = 1e-3
"""Sends below this are dropped: without a floor a wide placement's outermost
virtual sources leave dust in channels the image does not reach."""

HEIGHT_FLATTEN_WIDTH_FACTOR = 2.0
"""Degrees of image width a layout with no height pair gets back per degree of
elevation it cannot reproduce."""


@dataclass(frozen=True)
class StemPlacement:
    """Where one stem sits, before any layout is applied."""

    azimuth_deg: float
    elevation_deg: float
    width_deg: float
    spread_deg: float
    lfe: float = 0.0


def _p(azimuth: float, elevation: float, width: float, spread: float, lfe: float = 0.0) -> StemPlacement:
    return StemPlacement(azimuth, elevation, width, spread, lfe)


BALANCED_PLACEMENTS: dict[str, StemPlacement] = {
    "Lead Vocals":    _p(0.0, 0.0, 0.0, 46.0),
    "Vocals":         _p(0.0, 3.0, 22.0, 54.0),
    "Backing Vocals": _p(0.0, 14.0, 84.0, 54.0),
    "Bass":           _p(0.0, 0.0, 66.0, 40.0, 0.75),
    "Kick":           _p(0.0, 0.0, 60.0, 40.0, 0.85),
    "Snare":          _p(0.0, 0.0, 58.0, 44.0),
    "Toms":           _p(0.0, 0.0, 104.0, 60.0, 0.20),
    "Drums":          _p(0.0, 0.0, 96.0, 58.0, 0.30),
    "Hi-Hat":         _p(0.0, 16.0, 76.0, 48.0),
    "Ride":           _p(0.0, 18.0, 80.0, 48.0),
    "Crash":          _p(0.0, 20.0, 90.0, 52.0),
    "Guitar":         _p(0.0, 0.0, 128.0, 70.0),
    "Piano":          _p(0.0, 4.0, 110.0, 62.0),
    "Other":          _p(0.0, 8.0, 116.0, 62.0, 0.15),
    "Instrumental":   _p(0.0, 6.0, 104.0, 58.0, 0.40),
    "Crowd":          _p(180.0, 12.0, 120.0, 80.0),
    "Vocals Reverb":  _p(180.0, 22.0, 150.0, 84.0),
}
"""The reference table. Every other preset is a deliberate departure from it."""


STEM_ROUTING_PRESETS: dict[str, dict[str, StemPlacement]] = {
    "balanced": BALANCED_PLACEMENTS,
    # Near-field: narrow front stage, heights barely used, room content pulled
    # in off the rear wall.
    "intimate": BALANCED_PLACEMENTS | {
        "Lead Vocals":    _p(0.0, 0.0, 0.0, 38.0),
        "Vocals":         _p(0.0, 0.0, 16.0, 42.0),
        "Backing Vocals": _p(0.0, 6.0, 52.0, 42.0),
        "Bass":           _p(0.0, 0.0, 52.0, 38.0, 0.70),
        "Kick":           _p(0.0, 0.0, 32.0, 36.0, 0.80),
        "Snare":          _p(0.0, 0.0, 30.0, 38.0),
        "Toms":           _p(0.0, 0.0, 48.0, 38.0, 0.18),
        "Drums":          _p(0.0, 0.0, 44.0, 42.0, 0.28),
        "Hi-Hat":         _p(0.0, 8.0, 50.0, 38.0),
        "Ride":           _p(0.0, 10.0, 52.0, 38.0),
        "Crash":          _p(0.0, 10.0, 58.0, 42.0),
        "Guitar":         _p(0.0, 0.0, 60.0, 42.0),
        "Piano":          _p(0.0, 0.0, 56.0, 42.0),
        "Other":          _p(0.0, 6.0, 72.0, 50.0, 0.12),
        "Instrumental":   _p(0.0, 4.0, 64.0, 46.0, 0.35),
        "Crowd":          _p(180.0, 6.0, 88.0, 58.0),
        "Vocals Reverb":  _p(180.0, 10.0, 104.0, 62.0),
    },
    # Band across the front arc: each melodic voice gets its own azimuth
    # instead of a symmetric pair, which is what point placements buy over
    # zone gains. Rhythm section and lead vocal stay centre-front.
    "stage": BALANCED_PLACEMENTS | {
        "Backing Vocals": _p(0.0, 16.0, 96.0, 52.0),
        "Toms":           _p(-18.0, 0.0, 52.0, 42.0, 0.20),
        "Hi-Hat":         _p(32.0, 14.0, 36.0, 48.0),
        "Ride":           _p(-36.0, 16.0, 36.0, 48.0),
        "Crash":          _p(0.0, 24.0, 88.0, 50.0),
        "Guitar":         _p(48.0, 0.0, 52.0, 54.0),
        "Piano":          _p(-48.0, 4.0, 52.0, 54.0),
        "Other":          _p(0.0, 10.0, 104.0, 60.0, 0.15),
    },
    # Widened front, sides carrying sustained content, everything lifted a
    # little off the floor.
    "wide": BALANCED_PLACEMENTS | {
        "Lead Vocals":    _p(0.0, 0.0, 24.0, 52.0),
        "Vocals":         _p(0.0, 0.0, 40.0, 58.0),
        "Backing Vocals": _p(0.0, 20.0, 116.0, 62.0),
        "Bass":           _p(0.0, 0.0, 84.0, 50.0, 0.75),
        "Snare":          _p(0.0, 0.0, 52.0, 50.0),
        "Toms":           _p(0.0, 0.0, 88.0, 52.0, 0.20),
        "Drums":          _p(0.0, 0.0, 78.0, 56.0, 0.30),
        "Hi-Hat":         _p(0.0, 20.0, 96.0, 52.0),
        "Ride":           _p(0.0, 24.0, 100.0, 52.0),
        "Crash":          _p(0.0, 28.0, 116.0, 58.0),
        "Guitar":         _p(0.0, 4.0, 116.0, 60.0),
        "Piano":          _p(0.0, 8.0, 106.0, 58.0),
        "Other":          _p(0.0, 18.0, 130.0, 68.0, 0.15),
        "Instrumental":   _p(0.0, 10.0, 116.0, 62.0, 0.40),
        "Crowd":          _p(180.0, 16.0, 132.0, 74.0),
        "Vocals Reverb":  _p(180.0, 26.0, 160.0, 88.0),
    },
    # Heights carry the non-core content. The core — lead vocal, kick, snare,
    # bass — is untouched: lifting the pulse overhead is the classic Atmos
    # music mistake.
    "immersive": BALANCED_PLACEMENTS | {
        "Backing Vocals": _p(0.0, 30.0, 108.0, 68.0),
        "Toms":           _p(0.0, 8.0, 74.0, 48.0, 0.20),
        "Drums":          _p(0.0, 6.0, 66.0, 52.0, 0.30),
        "Hi-Hat":         _p(0.0, 32.0, 92.0, 66.0),
        "Ride":           _p(0.0, 34.0, 96.0, 68.0),
        "Crash":          _p(0.0, 36.0, 112.0, 72.0),
        "Guitar":         _p(0.0, 14.0, 96.0, 56.0),
        "Piano":          _p(0.0, 18.0, 90.0, 54.0),
        "Other":          _p(0.0, 30.0, 124.0, 70.0, 0.15),
        "Instrumental":   _p(0.0, 18.0, 102.0, 60.0, 0.40),
        "Crowd":          _p(180.0, 28.0, 132.0, 76.0),
        "Vocals Reverb":  _p(180.0, 38.0, 160.0, 90.0),
    },
    # Venue: audience behind and above the listener, kit and room opening into
    # the side pair, melodic content wrapped rather than pinned to the wall.
    "live": BALANCED_PLACEMENTS | {
        "Vocals":         _p(0.0, 0.0, 30.0, 56.0),
        "Backing Vocals": _p(0.0, 18.0, 104.0, 60.0),
        "Toms":           _p(0.0, 0.0, 86.0, 54.0, 0.20),
        "Drums":          _p(0.0, 4.0, 92.0, 60.0, 0.30),
        "Hi-Hat":         _p(0.0, 18.0, 88.0, 52.0),
        "Ride":           _p(0.0, 22.0, 92.0, 52.0),
        "Crash":          _p(0.0, 26.0, 104.0, 58.0),
        "Guitar":         _p(0.0, 2.0, 106.0, 58.0),
        "Piano":          _p(0.0, 6.0, 96.0, 56.0),
        "Other":          _p(0.0, 14.0, 136.0, 72.0, 0.15),
        "Instrumental":   _p(0.0, 8.0, 118.0, 62.0, 0.40),
        "Crowd":          _p(180.0, 22.0, 150.0, 84.0),
        "Vocals Reverb":  _p(180.0, 30.0, 168.0, 92.0),
    },
}

STEM_ROUTING_PRESET_NAMES: tuple[str, ...] = tuple(STEM_ROUTING_PRESETS)


def _project(placement: StemPlacement, output_format: OutputFormat) -> StemPlacement:
    """Restate a canonical placement as what *output_format* can reproduce.

    A layout with no height pair cannot carry an elevated placement, and simply
    zeroing the elevation would pull the stem *inward* onto the front wall —
    the opposite of what it was placed overhead for. The elevation is spent on
    width instead, so overhead content wraps to the sides and rear.

    Azimuth needs no clamp: the panner's span already widens to the rearmost
    pair a layout does have.
    """
    if output_format.has_height or placement.elevation_deg <= 0.0:
        return placement
    return replace(
        placement,
        elevation_deg=0.0,
        width_deg=placement.width_deg + HEIGHT_FLATTEN_WIDTH_FACTOR * placement.elevation_deg,
    )


def resolve_placements(preset: str, layout: str) -> dict[str, StemPlacement]:
    """Return *preset*'s per-stem placements as realized on *layout*."""
    if preset not in STEM_ROUTING_PRESETS:
        raise ValueError(
            f"Unknown stem routing preset '{preset}'. Valid: {STEM_ROUTING_PRESET_NAMES}"
        )
    if layout not in FORMAT_MAP:
        raise ValueError(f"Unknown channel layout '{layout}'")
    output_format = FORMAT_MAP[layout if layout != "stereo" else STEREO_PLACEMENT_LAYOUT]
    return {
        stem: _project(placement, output_format)
        for stem, placement in STEM_ROUTING_PRESETS[preset].items()
    }


def placement_route(placement: StemPlacement, output_format: OutputFormat) -> dict[str, float]:
    """Pan one placement into *output_format*'s speakers, constant power.

    The panning itself is MDAP (see ``stem_panner``); this adds the send floor
    and the LFE passthrough, and renormalizes so dropping the floored sends
    does not cost the map its constant power.
    """
    labels = tuple(
        label for label in output_format.channels if label in SPEAKER_AZIMUTH_ELEVATION
    )
    gains = panning_gains(
        placement.azimuth_deg,
        placement.elevation_deg,
        placement.width_deg,
        placement.spread_deg,
        labels,
    )
    kept = {channel: gain for channel, gain in gains.items() if gain > MINIMUM_SEND}
    norm = math.sqrt(sum(gain * gain for gain in kept.values()))
    if norm <= 0.0:
        return {}
    route = {channel: gain / norm for channel, gain in kept.items()}
    if ChannelLabel.LFE in output_format.channels and placement.lfe > 0.0:
        route[ChannelLabel.LFE.value] = placement.lfe
    return route


def preset_routing(preset: str, output_format: OutputFormat) -> dict[str, dict[str, float]]:
    """Speaker maps for every stem of *preset*, realized on *output_format*."""
    return {
        stem: placement_route(placement, output_format)
        for stem, placement in resolve_placements(preset, output_format.name).items()
    }
