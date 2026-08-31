"""Layout-specific virtual-loudspeaker directions for binaural bed rendering."""
from __future__ import annotations

import math
from dataclasses import dataclass

from upmixer.direct_speakers import direct_speakers
from upmixer.formats import ChannelLabel, OutputFormat


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


def speaker_azimuth_elevation(
    fmt: OutputFormat,
) -> dict[ChannelLabel, AzimuthElevation]:
    """Return the format's BS.2051/BS.2094 nominal speaker directions."""
    return {
        speaker.channel: AzimuthElevation(
            speaker.azimuth_deg,
            speaker.elevation_deg,
        )
        for speaker in direct_speakers(fmt)
        if speaker.azimuth_deg is not None and speaker.elevation_deg is not None
    }
