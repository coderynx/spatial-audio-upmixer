"""Linked-detection dynamic EQ.

The profile EQ is a static curve and the bus compressor is full-range, so
neither can do the surgical moves mastering actually reaches for: tame a harsh
3-4 kHz region only when it flares, tuck a resonant low-mid on loud passages.
Each band here is a parametric bell that rides between 0 dB and whatever its
own detector asks for, so the signal is never split into bands and recombined
— below its threshold a band is the identity, sample for sample.

Detection is per band and **linked across channels**: one band-pass per bed
channel feeds a single RMS, exactly as ``bus_compress``'s sidechain does, and
the resulting gain is realized as one bell design applied identically to every
non-LFE channel.  That keeps the stage a shared time-varying filter, which
commutes with the LF sum the same way the linked compressor's gain does
(``docs/contracts/preview_export_parity.md`` §1), and makes channel divergence
— the failure that closed ``docs/plans/mixing/phase13_report.md`` — impossible
by construction.

The gain computer is the compressor's, so ``threshold_db`` and ``ratio`` mean
what they mean there; the soft knee is structural and lives in
``mastering::dyneq``'s ``KNEE_DB``.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "dynamic_eq": {
        "bands": ("config", "mastering_dyneq_bands"),
    },
})
del _rbk

BAND_FIELDS: tuple[str, ...] = (
    "freq_hz",
    "q",
    "threshold_db",
    "ratio",
    "attack_ms",
    "release_ms",
)

#: Bands a chain may carry, mirroring ``mastering::dyneq``'s ``MAX_BANDS``.
MAX_BANDS = 4


def apply_dynamic_eq(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    bands: list[dict],
    lfe_key: str = "LFE",
) -> dict[str, np.ndarray]:
    """Run every band over the bed, leaving *lfe_key* untouched.

    Args:
        channels:    Dict channel_name -> 1-D float array.
        sample_rate: Audio sample rate in Hz.
        bands:       One dict per band, keyed by :data:`BAND_FIELDS`.
        lfe_key:     Channel name kept out of both the detector and the bells.

    Returns:
        New channel dict with the same shapes and dtypes.
    """
    if not channels or not bands:
        return channels
    names = list(channels)
    processed, cuts = upmixer_dsp.dynamic_eq(
        [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
        names.index(lfe_key) if lfe_key in channels else None,
        sample_rate,
        [tuple(float(band[field]) for field in BAND_FIELDS) for band in bands],
    )
    for band, cut in zip(bands, cuts):
        _log.info(
            "  Dynamic EQ: %.0f Hz Q %.1f  peak cut %.2f dB",
            band["freq_hz"], band["q"], cut,
        )
    return {
        name: arr.astype(channels[name].dtype) for name, arr in zip(names, processed)
    }
