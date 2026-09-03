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

MANIFEST_FIELDS = {
        "profile": ("config", "mastering_dyneq_profile"),
        "bands":   ("config", "mastering_dyneq_bands"),
}

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

DYNEQ_PROFILES: dict[str, list[dict]] = {
    "tame-harshness": [
        dict(freq_hz=3500.0, q=1.8, threshold_db=-32.0, ratio=3.0,
             attack_ms=15.0, release_ms=180.0),
    ],
    "tame-sibilance": [
        dict(freq_hz=7500.0, q=3.0, threshold_db=-31.0, ratio=4.0,
             attack_ms=2.0, release_ms=80.0),
    ],
    "clear-low-mid": [
        dict(freq_hz=250.0, q=1.2, threshold_db=-25.0, ratio=2.5,
             attack_ms=30.0, release_ms=250.0),
    ],
    "tighten-low-end": [
        dict(freq_hz=75.0, q=1.0, threshold_db=-23.0, ratio=3.0,
             attack_ms=20.0, release_ms=200.0),
    ],
    "immersive-polish": [
        dict(freq_hz=250.0, q=1.2, threshold_db=-25.0, ratio=2.0,
             attack_ms=30.0, release_ms=250.0),
        dict(freq_hz=3500.0, q=1.8, threshold_db=-31.0, ratio=2.5,
             attack_ms=15.0, release_ms=180.0),
        dict(freq_hz=11000.0, q=2.0, threshold_db=-29.0, ratio=2.5,
             attack_ms=5.0, release_ms=120.0),
    ],
}
"""Named band sets, chosen for what *this* pipeline does to a bed rather than
for generic mastering moves.

``clear-low-mid`` and ``tighten-low-end`` target coherent summing: bass
management unifies below its crossover, but from there to ~400 Hz every bed
channel still carries correlated content that sums at up to 10·log₁₀(N) dB, and
nothing else in the chain addresses it.  ``tame-harshness`` and
``immersive-polish``'s middle band target the presence region, which the height
sends' high shelf and the surround/height decorrelation both push on.
``tame-sibilance`` is the one bus-level move the knowledge base argues against
(``techniques/mastering_restoration.md``: de-ess the vocal stem instead) — it
is here because the realtime pipeline has no stems to fix at stem level, and in
stem mode it is a safety net rather than the first tool.

Thresholds are absolute dBFS on the *pre*-normalization bed, the same
convention and the same chain position as :data:`COMP_PROFILES`.  They are
calibrated for a bed whose full-band linked RMS sits near −20 dBFS, which is
what those profiles also assume.

The two families are set differently because they catch different things.  The
high bands sit well above their band's median, near where a dense programme's
transient flares reach, so they act on those and are inert between them.  The
low bands sit near their median: correlated low-frequency content varies by
under a dB within a passage, so there are no flares to catch there — what a low
band is for is the 6-10 dB a mix moves between a quiet passage and a loud one,
which means engaging through the loud one and relaxing through the quiet one.
"""

DYNEQ_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(DYNEQ_PROFILES))


def resolve_dyneq_bands(
    profile: str | None,
    bands: list[dict] | None,
) -> list[dict]:
    """Bands the chain should run: explicit *bands* win over *profile*.

    Manifest values beat profile defaults, the same precedence every other
    stage applies.  An unknown profile name resolves to no bands.
    """
    if bands:
        return bands
    if profile is None:
        return []
    return [dict(band) for band in DYNEQ_PROFILES.get(profile, ())]


def apply_dynamic_eq(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    bands: list[dict],
    lfe_key: str = "LFE",
    detector_channels: dict[str, np.ndarray] | None = None,
    detector_lfe_key: str = "LFE",
) -> dict[str, np.ndarray]:
    """Run every band over the bed, leaving *lfe_key* untouched.

    Args:
        channels:    Dict channel_name -> 1-D float array.
        sample_rate: Audio sample rate in Hz.
        bands:       One dict per band, keyed by :data:`BAND_FIELDS`.
        lfe_key:     Channel name kept out of both the detector and the bells.
        detector_channels: Optional rendered speaker programme that drives the
            shared bell curves applied to ``channels``.
        detector_lfe_key: LFE name in ``detector_channels``.

    Returns:
        New channel dict with the same shapes and dtypes.
    """
    if not channels or not bands:
        return channels
    names = list(channels)
    targets = [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names]
    params = [(*tuple(float(band[field]) for field in BAND_FIELDS[:4]),
               float(band.get("max_cut_db", float("inf"))),
               *tuple(float(band[field]) for field in BAND_FIELDS[4:])) for band in bands]
    if detector_channels is None:
        processed, cuts = upmixer_dsp.dynamic_eq(
            targets,
            names.index(lfe_key) if lfe_key in channels else None,
            sample_rate,
            params,
        )
    else:
        detector_names = list(detector_channels)
        processed, cuts = upmixer_dsp.dynamic_eq_linked(
            targets,
            names.index(lfe_key) if lfe_key in channels else None,
            [
                np.ascontiguousarray(detector_channels[name], dtype=np.float64)
                for name in detector_names
            ],
            detector_names.index(detector_lfe_key)
            if detector_lfe_key in detector_channels else None,
            sample_rate,
            params,
        )
    for band, cut in zip(bands, cuts):
        _log.info(
            "  Dynamic EQ: %.0f Hz Q %.1f  peak cut %.2f dB",
            band["freq_hz"], band["q"], cut,
        )
    return {
        name: arr.astype(channels[name].dtype) for name, arr in zip(names, processed)
    }
