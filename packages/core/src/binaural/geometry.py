"""Virtual-loudspeaker positions for the binaural bed.

Mirrors ``web/src/lib/spatial.ts`` ``speakerCoordinates`` /
``positionToAzimuthElevation`` exactly — same unit-sphere coordinates, same
azimuth/elevation convention (positive azimuth = left, listener facing -Z).
Keep the two files numerically identical; this is the geometry half of the
signal-graph contract in ``docs/standards/spatial_audio_engine.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from upmixer.formats import ChannelLabel

@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float


SPEAKER_COORDINATES: dict[ChannelLabel, Vec3] = {
    ChannelLabel.FL: Vec3(-0.5, 0.0, -0.87),
    ChannelLabel.FR: Vec3(0.5, 0.0, -0.87),
    ChannelLabel.C: Vec3(0.0, 0.0, -1.0),
    ChannelLabel.SL: Vec3(-0.94, 0.0, 0.34),
    ChannelLabel.SR: Vec3(0.94, 0.0, 0.34),
    ChannelLabel.BL: Vec3(-0.7, 0.0, 0.7),
    ChannelLabel.BR: Vec3(0.7, 0.0, 0.7),
    ChannelLabel.TFL: Vec3(-0.5, 0.6, -0.7),
    ChannelLabel.TFR: Vec3(0.5, 0.6, -0.7),
    ChannelLabel.TBL: Vec3(-0.6, 0.6, 0.6),
    ChannelLabel.TBR: Vec3(0.6, 0.6, 0.6),
}


@dataclass(frozen=True)
class AzimuthElevation:
    azimuth_deg: float
    elevation_deg: float

    @property
    def azimuth_rad(self) -> float:
        return math.radians(self.azimuth_deg)

    @property
    def elevation_rad(self) -> float:
        return math.radians(self.elevation_deg)


def position_to_azimuth_elevation(position: Vec3) -> AzimuthElevation:
    """Inverse of the JSAmbisonics/ambisonic convention used by the web preview.

    azimuth = atan2(-x, -z), positive = left; elevation = asin(y / radius).
    """
    radius = math.sqrt(position.x**2 + position.y**2 + position.z**2)
    if radius == 0:
        return AzimuthElevation(0.0, 0.0)
    elev = math.degrees(math.asin(max(-1.0, min(1.0, position.y / radius))))
    azim = math.degrees(math.atan2(-position.x, -position.z))
    return AzimuthElevation(azim, elev)


SPEAKER_AZIMUTH_ELEVATION: dict[ChannelLabel, AzimuthElevation] = {
    label: position_to_azimuth_elevation(position)
    for label, position in SPEAKER_COORDINATES.items()
}


def positional_labels(labels: tuple[ChannelLabel, ...]) -> list[ChannelLabel]:
    """Return the subset of *labels* that have a virtual-loudspeaker position."""
    return [label for label in labels if label in SPEAKER_COORDINATES]
