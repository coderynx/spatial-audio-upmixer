"""Pre-limiter soft clipper.

On transient material the look-ahead limiter otherwise does all the peak
control alone, which is the pumping-prone configuration a modern chain avoids:
a clipper shaves the sharpest transients and the limiter cleans up what is
left.  This stage runs after loudness normalization and directly before the
limiter — normalizing an already-clipped signal would make the clip depth
depend on the loudness target.

``clip_db`` sets how far below the ceiling the knee sits; ``knee`` blends a
hard clip at the ceiling (0.0) into a tanh whose slope at the knee is exactly
1.0 (1.0).  One curve with one set of parameters reaches every non-LFE
channel, which is the only sense a memoryless nonlinearity can be linked in —
it does not commute with the LF sum, which is why it sits after bass
management (``docs/contracts/preview_export_parity.md`` §1).
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger(__name__)

MANIFEST_FIELDS = {
        "enabled": ("config", "mastering_clip_enabled"),
        "clip_db": ("config", "mastering_clip_db"),
        "knee":    ("config", "mastering_clip_knee"),
}


def apply_soft_clip(
    channels: dict[str, np.ndarray],
    ceiling_dbtp: float,
    clip_db: float,
    knee: float,
    lfe_key: str = "LFE",
) -> dict[str, np.ndarray]:
    """Soft-clip every channel except *lfe_key* against the limiter's ceiling.

    Args:
        channels:     Dict channel_name -> 1-D float array.
        ceiling_dbtp: The limiter's ceiling — the curve's asymptote.
        clip_db:      How far below the ceiling the knee sits, in dB.
        knee:         0.0 hard clip … 1.0 full tanh.
        lfe_key:      Channel name left untouched.

    Returns:
        New channel dict with the same shapes and dtypes.
    """
    if not channels:
        return channels
    names = list(channels)
    _log.info(
        "  Soft clip: knee %.1f dB below %.1f dBTP  shape=%.2f",
        clip_db, ceiling_dbtp, knee,
    )
    clipped = upmixer_dsp.soft_clip(
        [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
        names.index(lfe_key) if lfe_key in channels else None,
        float(ceiling_dbtp),
        float(clip_db),
        float(knee),
    )
    return {
        name: arr.astype(channels[name].dtype) for name, arr in zip(names, clipped)
    }
