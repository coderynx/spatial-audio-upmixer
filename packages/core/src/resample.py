"""Delivery-rate sample-rate conversion.

The export changes rate exactly once, before mastering, so the limiter sees
the delivery rate and the true-peak ceiling holds where it is measured — see
``docs/standards/loudness_dsp_bs1770.md`` §"Export tail".
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import firwin, kaiserord, resample_poly

# scipy's own default (Kaiser beta 5, 10x max_rate half-length) rolls off from
# ~18.5 kHz and leaves images 37 dB down on 44.1 -> 48 kHz; measured in
# docs/plans/mastering/phase6_report.md.
_STOPBAND_DB = 120.0
_TRANSITION_FRACTION = 0.10


def anti_imaging_fir(up: int, down: int) -> np.ndarray:
    """Kaiser FIR for a ``up``/``down`` polyphase stage, cut off at the lower
    rate's Nyquist with a transition of ``_TRANSITION_FRACTION`` of that rate."""
    cutoff = 1.0 / max(up, down)
    n_taps, beta = kaiserord(_STOPBAND_DB, _TRANSITION_FRACTION * 2.0 * cutoff)
    return firwin(n_taps | 1, cutoff, window=("kaiser", beta))


def resample_channels(
    channels: dict[str, np.ndarray], src_sr: int, dst_sr: int
) -> dict[str, np.ndarray]:
    """Resample every channel of a bed from ``src_sr`` to ``dst_sr``."""
    if dst_sr == src_sr:
        return channels
    divisor = math.gcd(dst_sr, src_sr)
    up, down = dst_sr // divisor, src_sr // divisor
    fir = anti_imaging_fir(up, down)
    return {
        name: resample_poly(channel, up, down, window=fir).astype(np.float64)
        for name, channel in channels.items()
    }
