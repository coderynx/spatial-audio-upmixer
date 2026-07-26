"""Order-3 real spherical-harmonic (ACN/N3D) ambisonic encoding.

Implements the standard AmbiX ACN channel ordering / N3D normalization real
SH basis up to order 3 (16 channels), the same convention JSAmbisonics (the
web preview's ambisonic library) documents for its encoders/decoders. See
``docs/standards/spatial_audio_engine.md`` for the channel-order table and
the parity policy between this module and the browser implementation.
"""
from __future__ import annotations

import math

import numpy as np

AMBISONIC_ORDER = 3
N_ACN_CHANNELS = (AMBISONIC_ORDER + 1) ** 2  # 16


def encode_gains(azimuth_rad: float, elevation_rad: float) -> np.ndarray:
    """Return the 16 ACN/N3D real-SH encode gains for a point source direction.

    ``azimuth_rad``: 0 = front, positive = left (matches
    :mod:`upmixer.binaural.geometry`). ``elevation_rad``: 0 = horizon,
    positive = up.
    """
    theta = azimuth_rad
    delta = elevation_rad
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    sin_d, cos_d = math.sin(delta), math.cos(delta)
    sin_2t, cos_2t = math.sin(2 * theta), math.cos(2 * theta)
    sin_3t, cos_3t = math.sin(3 * theta), math.cos(3 * theta)

    gains = np.zeros(N_ACN_CHANNELS, dtype=np.float64)
    # Order 0
    gains[0] = 1.0
    # Order 1 (ACN 1=Y, 2=Z, 3=X)
    gains[1] = math.sqrt(3.0) * cos_d * sin_t
    gains[2] = math.sqrt(3.0) * sin_d
    gains[3] = math.sqrt(3.0) * cos_d * cos_t
    # Order 2 (ACN 4=V, 5=T, 6=R, 7=S, 8=U)
    gains[4] = (math.sqrt(15.0) / 2.0) * cos_d**2 * sin_2t
    gains[5] = math.sqrt(15.0) * sin_d * cos_d * sin_t
    gains[6] = (math.sqrt(5.0) / 2.0) * (3.0 * sin_d**2 - 1.0)
    gains[7] = math.sqrt(15.0) * sin_d * cos_d * cos_t
    gains[8] = (math.sqrt(15.0) / 2.0) * cos_d**2 * cos_2t
    # Order 3 (ACN 9=Q, 10=O, 11=M, 12=K, 13=L, 14=N, 15=P)
    gains[9] = math.sqrt(35.0 / 8.0) * cos_d**3 * sin_3t
    gains[10] = (math.sqrt(105.0) / 2.0) * sin_d * cos_d**2 * sin_2t
    gains[11] = math.sqrt(21.0 / 8.0) * cos_d * (5.0 * sin_d**2 - 1.0) * sin_t
    gains[12] = 0.5 * sin_d * (5.0 * sin_d**2 - 3.0)
    gains[13] = math.sqrt(21.0 / 8.0) * cos_d * (5.0 * sin_d**2 - 1.0) * cos_t
    gains[14] = (math.sqrt(105.0) / 2.0) * sin_d * cos_d**2 * cos_2t
    gains[15] = math.sqrt(35.0 / 8.0) * cos_d**3 * cos_3t
    return gains


def encoding_matrix(directions: list[tuple[float, float]]) -> np.ndarray:
    """Return the (16, M) encoding matrix for a list of (azimuth, elevation) radians."""
    return np.column_stack([encode_gains(az, el) for az, el in directions])
