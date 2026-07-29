"""Parametric spherical-head HRTF model shared by the engine's offline filter
synthesis scripts.

Woodworth ITD formula + frequency-dependent head-shadow ILD lowpass. Not a
measured HRTF — a documented approximation used because no measured dataset
ships with this repository (see ``docs/standards/spatial_audio_engine.md`` §4
and ``docs/standards/transaural_speakers.md`` §3). Used by both
``scripts/build_binaural_filters.py`` (headphone HRIR decode) and
``scripts/build_crosstalk_filters.py`` (speaker-to-ear crosstalk matrix) so
both spatial-audio targets share one head model instead of two independently
drifting copies.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.utils import elevation_eq

HEAD_RADIUS_M = 0.0875
SPEED_OF_SOUND = 343.0


def fractional_impulse(delay_samples: float, n_taps: int) -> np.ndarray:
    arr = np.zeros(n_taps, dtype=np.float64)
    i0 = int(math.floor(delay_samples))
    frac = delay_samples - i0
    if 0 <= i0 < n_taps:
        arr[i0] += 1.0 - frac
    if 0 <= i0 + 1 < n_taps:
        arr[i0 + 1] += frac
    return arr


def synth_hrir(azimuth: float, elevation: float, sr: int, n_taps: int) -> tuple[np.ndarray, np.ndarray]:
    """Parametric spherical-head-model HRIR: Woodworth ITD + head-shadow ILD.

    Not a measured HRTF — a documented approximation (see the contract docs
    referenced above) used because no measured dataset ships with this
    repository. ``azimuth``/``elevation`` in radians, 0 = front/horizon,
    positive azimuth = left, positive elevation = up.
    """
    itd_s = (HEAD_RADIUS_M / SPEED_OF_SOUND) * (azimuth + math.sin(azimuth))
    itd_samples = itd_s * sr
    shadow_amount = abs(math.sin(azimuth))
    shadow_cutoff_hz = max(1500.0, 8000.0 - 5000.0 * shadow_amount)
    shadow_atten_db = -8.0 * shadow_amount
    sos_shadow = butter(2, shadow_cutoff_hz / (sr / 2.0), btype="low", output="sos")

    near = fractional_impulse(0.0, n_taps)
    if shadow_amount == 0.0:
        # Dead center: no ITD, no head shadow — both ears hear the literal
        # same signal. Filtering "far" through sos_shadow even at 0 dB
        # commanded attenuation would still color it (an 8 kHz lowpass isn't
        # transparent to a full-band impulse), splitting a dead-center source
        # into two non-identical ears where physically there's no near/far
        # distinction at all.
        far = near
    else:
        far = fractional_impulse(abs(itd_samples), n_taps)
        far = sosfilt(sos_shadow, far) * (10.0 ** (shadow_atten_db / 20.0))
    left, right = (near, far) if azimuth >= 0 else (far, near)

    elevation_gain = max(0.0, math.sin(elevation))
    if elevation_gain > 0:
        left = elevation_eq(left, sr, high_shelf_gain=1.0 + 0.5 * elevation_gain)
        right = elevation_eq(right, sr, high_shelf_gain=1.0 + 0.5 * elevation_gain)

    return left, right
