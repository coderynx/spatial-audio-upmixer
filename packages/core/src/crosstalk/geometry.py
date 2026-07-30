"""Speaker geometry helpers for the crosstalk-cancellation (transaural) target.

Unlike :mod:`upmixer.binaural.geometry`'s fixed virtual-loudspeaker table,
real playback speakers here are listener-relative and profile-specific — a
stereo pair's span differs from a smart speaker's dual close-spaced drivers
or a car's off-center driver seat — so geometry lives on each
:class:`~upmixer.crosstalk.profiles.XtcParams` instance instead of a shared
fixed table. This module holds the one conversion helper both the renderer
and ``scripts/build_crosstalk_filters.py`` need.
"""
from __future__ import annotations

import math

from upmixer.crosstalk.profiles import XtcParams


def speaker_azimuths_rad(params: XtcParams) -> tuple[float, float]:
    """Return (left_speaker_azimuth_rad, right_speaker_azimuth_rad)."""
    return math.radians(params.azimuth_left_deg), math.radians(params.azimuth_right_deg)
