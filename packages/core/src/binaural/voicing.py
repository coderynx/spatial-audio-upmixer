"""Post-decode voicing chain: crossfeed, shelving/presence EQ, M/S widen.

Applies :class:`~upmixer.binaural.profiles.VoicingParams`. Bypassed entirely
for Flat and Studio (all-zero params); only Listening uses this to apply a
flattering "hi-fi enhance" (crossfeed for externalization, a Harman-style
bass/air/presence tilt, and a wide soundstage) on top of the profile's
reference cinema room decode. Filter topology mirrors
``upmixer/utils.py`` ``elevation_eq`` (subtract/add shelf trick) so the web
preview's Web Audio ``BiquadFilterNode`` chain can match parameter-for-
parameter — see ``docs/standards/spatial_audio_engine.md`` §5.
"""
from __future__ import annotations

import numpy as np
import upmixer_dsp

from upmixer.binaural.profiles import VoicingParams


def apply_voicing(
    left: np.ndarray, right: np.ndarray, sample_rate: int, params: VoicingParams
) -> tuple[np.ndarray, np.ndarray]:
    """Apply the full voicing chain in signal-graph order: crossfeed → EQ → widen."""
    return upmixer_dsp.apply_voicing(
        np.ascontiguousarray(left, dtype=np.float64),
        np.ascontiguousarray(right, dtype=np.float64),
        sample_rate,
        params.crossfeed_amount,
        params.crossfeed_cutoff_hz,
        params.bass_shelf_hz,
        params.bass_shelf_gain_db,
        params.air_shelf_hz,
        params.air_shelf_gain_db,
        params.presence_hz,
        params.presence_gain_db,
        params.presence_q,
        params.stereo_widen,
    )
