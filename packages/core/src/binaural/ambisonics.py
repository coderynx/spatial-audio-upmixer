"""Order-3 real spherical-harmonic (ACN/N3D) ambisonic encoding.

Implements the standard AmbiX ACN channel ordering / N3D normalization real
SH basis up to order 3 (16 channels), the same convention JSAmbisonics (the
web preview's ambisonic library) documents for its encoders/decoders. See
``docs/standards/spatial_audio_engine.md`` for the channel-order table and
the parity policy between this module and the browser implementation.
"""
from __future__ import annotations

import numpy as np
import upmixer_dsp

AMBISONIC_ORDER = 3
N_ACN_CHANNELS = (AMBISONIC_ORDER + 1) ** 2  # 16


def encode_gains(azimuth_rad: float, elevation_rad: float) -> np.ndarray:
    """Return the 16 ACN/N3D real-SH encode gains for a point source direction.

    ``azimuth_rad``: 0 = front, positive = left (matches
    :mod:`upmixer.binaural.geometry`). ``elevation_rad``: 0 = horizon,
    positive = up.
    """
    return upmixer_dsp.ambisonic_encode_gains(azimuth_rad, elevation_rad)


def encoding_matrix(directions: list[tuple[float, float]]) -> np.ndarray:
    """Return the (16, M) encoding matrix for a list of (azimuth, elevation) radians."""
    return np.column_stack([encode_gains(az, el) for az, el in directions])
