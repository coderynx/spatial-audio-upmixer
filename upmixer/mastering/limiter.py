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
width (``_FIR_DELAY`` oversampled taps either side, ~6 original samples): a
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
from scipy.ndimage import minimum_filter1d
from scipy.signal import lfilter, upfirdn

from upmixer.loudness import _TRUE_PEAK_FIR_4X

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "limiter": {
        "lookahead_ms": ("config", "limiter_lookahead_ms"),
        "release_ms":   ("config", "limiter_release_ms"),
    },
})
del _rbk

_OVERSAMPLE = 4
# upfirdn's convolution introduces a constant group delay of (len(FIR)-1)/2
# oversampled samples for this symmetric (linear-phase) filter; slicing the
# raw output at [0:span] would silently return an *earlier*, mis-aligned
# window (verified against upmixer.loudness.measure_true_peak, which
# correctly accounts for this via its own history-buffer bookkeeping).
_FIR_DELAY = (len(_TRUE_PEAK_FIR_4X) - 1) // 2
# Half-width, in *base-rate* samples, of the detector FIR's support — the
# span across which gain reduction must be held constant to prevent the
# gain-modulation edge effect documented in the module docstring above.
_FIR_MARGIN_SAMPLES = -(-_FIR_DELAY // _OVERSAMPLE)  # ceil division
# Extra internal headroom folded into the gain-computation ceiling only;
# see "Gain-modulation edge effect" above.
_SAFETY_MARGIN_DB = 0.1


def _forward_window_min(values: np.ndarray, window: int) -> np.ndarray:
    """Causal-for-the-caller, anticipative running minimum.

    Returns ``result[n] = min(values[n : n + window])`` for every ``n``,
    treating positions beyond the end of ``values`` as ``1.0`` (no gain
    reduction — there is no more signal to protect against). Implemented as
    one centered :func:`scipy.ndimage.minimum_filter1d` call over a
    right-padded copy of ``values``; ``window`` is forced odd so the
    centered filter's window aligns exactly with the desired forward span
    (verified by ``tests/test_limiter.py`` against a brute-force reference).
    """
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    padded = np.concatenate([values, np.ones(window - 1, dtype=values.dtype)])
    filtered = minimum_filter1d(padded, size=window)
    half = (window - 1) // 2
    return filtered[half: half + len(values)]


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
        self._ceiling_linear = 10.0 ** ((ceiling_dbtp - _SAFETY_MARGIN_DB) / 20.0)
        self._lookahead_ms = float(lookahead_ms)
        self._release_ms = float(release_ms)
        self._sr = int(sample_rate)

    def process(self, channels: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Apply the linked look-ahead limiter to every channel (incl. LFE).

        Args:
            channels: Dict channel_name -> 1D array.

        Returns:
            New channel dict, same shapes/dtypes as input.
        """
        if not channels:
            return channels
        n = max(len(v) for v in channels.values())
        if n == 0:
            return channels

        over_sr = self._sr * _OVERSAMPLE
        envelope = np.zeros(n * _OVERSAMPLE, dtype=np.float64)
        for ch in channels.values():
            ch64 = np.asarray(ch, dtype=np.float64)
            length = min(len(ch64), n)
            if length == 0:
                continue
            upsampled = upfirdn(_TRUE_PEAK_FIR_4X, ch64[:length], up=_OVERSAMPLE)
            span = length * _OVERSAMPLE
            aligned = upsampled[_FIR_DELAY: _FIR_DELAY + span]
            np.maximum(envelope[:span], np.abs(aligned), out=envelope[:span])

        gain_inst = np.minimum(1.0, self._ceiling_linear / np.maximum(envelope, 1e-12))

        lookahead_samples = max(1, round(self._lookahead_ms / 1000.0 * over_sr))
        gain_lookahead = _forward_window_min(gain_inst, lookahead_samples)

        need_db = -20.0 * np.log10(np.maximum(gain_lookahead, 1e-12))
        alpha_release = 1.0 - np.exp(-1.0 / (max(self._release_ms, 0.01) / 1000.0 * over_sr))
        b_r = np.array([alpha_release], dtype=np.float64)
        a_r = np.array([1.0, -(1.0 - alpha_release)], dtype=np.float64)
        slow_need_db = lfilter(b_r, a_r, need_db)
        need_db_smoothed = np.maximum(need_db, slow_need_db)

        gain_smoothed = np.power(10.0, -need_db_smoothed / 20.0)
        gain_base = gain_smoothed.reshape(n, _OVERSAMPLE).min(axis=1)

        # Dilate reduction across the detector FIR's own kernel width so a
        # heavily-reduced sample's neighbours can't recombine, under
        # interpolation, into a fresh inter-sample peak — see the module
        # docstring's "Gain-modulation edge effect" section.
        dilate_window = 2 * _FIR_MARGIN_SAMPLES + 1
        gain_base = minimum_filter1d(gain_base, size=dilate_window, mode="nearest")

        max_gr_db = float(np.max(need_db_smoothed))
        if max_gr_db > 1e-6:
            _log.info(
                "  Look-ahead limiter: ceiling=%.1f dBTP  lookahead=%.1f ms  "
                "release=%.0f ms  GR peak=%.1f dB",
                self._ceiling_dbtp, self._lookahead_ms,
                self._release_ms, max_gr_db,
            )

        out = {}
        for name, ch in channels.items():
            ch64 = np.asarray(ch, dtype=np.float64)
            m = len(ch64)
            out[name] = (ch64 * gain_base[:m]).astype(ch.dtype)
        return out
