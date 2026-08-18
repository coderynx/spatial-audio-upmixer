"""Look-ahead true-peak brickwall limiter for the master bus.

Replaces a memoryless soft-knee saturator with a linked, look-ahead gain
computer built on the same BS.1770-5 4x oversampled true-peak detector used
for loudness metering (``upmixer.loudness._TRUE_PEAK_FIR_4X``), so the
limiter reacts to genuine inter-sample peaks (ISPs), not just sample-domain
values.

Algorithm
---------
1.  **Detect**: every channel (including LFE — true-peak scanning covers all
    channels per BS.1770-5 Annex 2) is 4x oversampled with the standard FIR.
    The envelopes are combined with a running element-wise maximum across
    channels ("linked" detection), so the same gain curve is applied to
    every channel and stereo/surround imaging is preserved.
2.  **Instantaneous gain**: ``min(1, ceiling / envelope)`` at every
    oversampled position.
3.  **Look-ahead**: a forward-window minimum over the instantaneous gain
    lets the limiter start reducing gain *before* an oncoming peak arrives.
    This is computed as a single vectorized ``scipy.ndimage.minimum_filter1d``
    call, not a per-sample loop.
4.  **Release smoothing**: reusing the same fast/slow envelope-follower
    trick as :class:`~upmixer.mastering.compressor.BusCompressor` — the
    look-ahead gain itself supplies the (already anticipatory) attack, and a
    one-pole IIR low-pass on the required-reduction curve bounds how fast
    gain is allowed to recover, so recovery doesn't audibly pump.
5.  **Apply**: the smoothed gain envelope is decimated back to the base
    sample rate (via a block *minimum*, not an average, so the ceiling
    guarantee at the oversampled rate is never loosened) and multiplied
    into every channel.

No output latency is introduced. This is an offline, whole-buffer mastering
stage (unlike a real-time limiter, which must physically delay its output
to realize look-ahead causally): computing the forward-window minimum
directly against the already-available future samples is mathematically
equivalent to delaying the signal by the look-ahead window and trimming
that same delay back off afterwards, so the delay/trim cancels out exactly
and can be skipped.

Gain-modulation edge effect
----------------------------
Steps 1-4 measure and shape the gain envelope entirely from the
*unprocessed* signal's oversampled peaks. Multiplying a per-original-sample
gain onto the base-rate signal and then re-interpolating (as any true-peak
meter, including this limiter's own compliance check, will do) is *not*
equivalent to interpolating first and scaling second, whenever the gain
changes sharply between samples within the detector FIR's own support
width (half the detector FIR either side, ~6 original samples): a
sample that was heavily reduced sitting next to an unreduced neighbour can
still recombine, under interpolation, into a fresh inter-sample peak the
per-block analysis didn't foresee. ``gain_base`` is therefore dilated with
a symmetric minimum filter spanning the FIR's own kernel width before being
applied, so gain reduction always extends at least that far past any point
it protects — this was verified empirically (see ``tests/test_limiter.py``)
to eliminate the residual overshoot entirely. A small additional
``_SAFETY_MARGIN_DB`` is folded into the ceiling used for gain computation
only (the limiter still *reports*/targets the caller's nominal ceiling) as
routine extra headroom, standard practice for sample-domain true-peak
limiting.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "limiter": {
        "lookahead_ms": ("config", "limiter_lookahead_ms"),
        "release_ms":   ("config", "limiter_release_ms"),
    },
})
del _rbk

# Extra internal headroom folded into the gain-computation ceiling only;
# see "Gain-modulation edge effect" above.
_SAFETY_MARGIN_DB = 0.1


def _forward_window_min(values: np.ndarray, window: int) -> np.ndarray:
    """Causal-for-the-caller, anticipative running minimum.

    Returns ``result[n] = min(values[n : n + window])`` for every ``n``,
    treating positions beyond the end of ``values`` as ``1.0`` (no gain
    reduction — there is no more signal to protect against).  ``window`` is
    forced odd so the centered running minimum aligns exactly with the
    desired forward span (verified by ``tests/test_limiter.py`` against a
    brute-force reference).
    """
    return upmixer_dsp.forward_window_min(
        np.ascontiguousarray(values, dtype=np.float64), window
    )


class LookAheadLimiter:
    """Linked, look-ahead true-peak limiter for a multichannel bed.

    Args:
        ceiling_dbtp:  True-peak ceiling in dBTP (typically
                       ``config.loudness_max_tp``, e.g. −1.0 for Dolby Atmos
                       Music delivery).
        lookahead_ms:  Look-ahead window length in milliseconds.
        release_ms:    Release time constant in milliseconds — how fast
                       gain reduction is allowed to recover once a peak has
                       passed.
        sample_rate:   Audio sample rate in Hz.
    """

    def __init__(
        self,
        ceiling_dbtp: float,
        lookahead_ms: float,
        release_ms: float,
        sample_rate: int,
    ) -> None:
        self._ceiling_dbtp = float(ceiling_dbtp)
        self._lookahead_ms = float(lookahead_ms)
        self._release_ms = float(release_ms)
        self._sr = int(sample_rate)
        self.gr_peak_db: float = 0.0
        self.gr_duty: float = 0.0

    def process(self, channels: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Apply the linked look-ahead limiter to every channel (incl. LFE).

        Sets :attr:`gr_peak_db` and :attr:`gr_duty` — the deepest gain
        reduction applied and the fraction of samples held under reduction —
        so a caller can report what the limiter actually did.

        Args:
            channels: Dict channel_name -> 1D array.

        Returns:
            New channel dict, same shapes/dtypes as input.
        """
        if not channels:
            return channels
        names = list(channels)
        if max(len(channels[name]) for name in names) == 0:
            return channels

        limited, max_gr_db, duty = upmixer_dsp.lookahead_limit(
            [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
            self._sr,
            self._ceiling_dbtp,
            self._lookahead_ms,
            self._release_ms,
            _SAFETY_MARGIN_DB,
        )
        self.gr_peak_db = max_gr_db
        self.gr_duty = duty

        if max_gr_db > 1e-6:
            _log.info(
                "  Look-ahead limiter: ceiling=%.1f dBTP  lookahead=%.1f ms  "
                "release=%.0f ms  GR peak=%.1f dB  GR duty=%.1f%%",
                self._ceiling_dbtp, self._lookahead_ms,
                self._release_ms, max_gr_db, 100.0 * duty,
            )

        return {
            name: arr.astype(channels[name].dtype)
            for name, arr in zip(names, limited)
        }
