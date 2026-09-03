"""Chain head: subsonic high-pass and DC removal.

Nothing else in the mastering chain removes DC offset or sub-20 Hz energy,
and both waste limiter headroom and skew the low end of the BS.1770
measurement.  This stage runs first, ahead of reference matching, so no later
stage matches, shapes or measures rumble.

Every non-LFE channel runs one shared 12 dB/oct Butterworth high-pass, which
keeps the stage identical LTI filtering across the bed and so commuting with
the LF sum (``docs/contracts/preview_export_parity.md`` §1).  LFE is
band-limited upstream and its sub content is the point, so it gets a
first-order pole-zero DC blocker instead — the corner is structural and lives
in ``mastering::head``'s ``DC_BLOCK_HZ``.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger(__name__)

MANIFEST_FIELDS = {
        "enabled":   ("config", "mastering_highpass_enabled"),
        "cutoff_hz": ("config", "mastering_highpass_hz"),
}


def apply_chain_head(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    cutoff_hz: float,
    lfe_key: str = "LFE",
) -> dict[str, np.ndarray]:
    """High-pass every channel, LFE on the DC blocker alone.

    Args:
        channels:    Dict channel_name -> 1-D float array.
        sample_rate: Audio sample rate in Hz.
        cutoff_hz:   Subsonic corner for the non-LFE channels.
        lfe_key:     Channel name that takes the DC blocker instead.

    Returns:
        New channel dict with the same shapes and dtypes.
    """
    if not channels:
        return channels
    names = list(channels)
    _log.info("  Chain head: high-pass %.1f Hz (LFE: DC block)", cutoff_hz)
    filtered = upmixer_dsp.chain_head(
        [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
        names.index(lfe_key) if lfe_key in channels else None,
        sample_rate,
        float(cutoff_hz),
    )
    return {
        name: arr.astype(channels[name].dtype) for name, arr in zip(names, filtered)
    }
