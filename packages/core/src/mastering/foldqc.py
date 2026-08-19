"""Post-limiter fold QC — what the delivered master measures after collapse.

Measurement only: every fold is a collapsed *copy* of the mastered bed, and the
delivered bed is untouched.  The limiter's true-peak guarantee holds per
channel and does not survive a linear fold, so correlated content can sum over
the ceiling in the stereo downmix of a master that passes on its own.

Reported against the bed's own integrated loudness.  Thresholds and their
evidence: ``docs/standards/spatial_layouts_bs775_bs2051.md`` §"Fold QC
thresholds".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import (
    BINAURAL,
    BINAURAL_BED_FORMATS,
    STEREO_OUT,
    ChannelLabel,
    OutputFormat,
)

FOLD_DIVERGENCE_LU: float = 1.5
"""Loudness divergence between a fold and the native bed that raises a warning."""


@dataclass
class FoldMeasurement:
    """Delivery numbers for one collapsed programme."""

    lkfs: float
    """BS.1770-5 integrated loudness of the fold, in LKFS."""

    tp_dbtp: float
    """Maximum True Peak of the fold, in dBTP."""

    plr_db: float
    """Peak-to-loudness ratio of the fold, in dB."""

    lkfs_delta_lu: float
    """Fold loudness minus the native bed's, in LU."""

    tp_compliant: bool
    """Whether the fold stays under the delivery target's True Peak ceiling."""

    loudness_divergent: bool
    """Whether ``lkfs_delta_lu`` exceeds :data:`FOLD_DIVERGENCE_LU`."""


@dataclass
class FoldQC:
    """Every fold measured for one delivered master.

    Field names say which artifact each number belongs to: ``stereo`` is the
    BS.775 2/0 downmix, ``surround_51`` the 5.1 re-render, and ``binaural`` the
    finished binaural render of this speaker bed — not a binaural *delivery*,
    which is its own mastered programme (``render_binaural_delivery``).
    """

    native_lkfs: float
    """Integrated loudness of the delivered bed, the reference every delta is
    taken against."""

    stereo: FoldMeasurement | None = None
    surround_51: FoldMeasurement | None = None
    binaural: FoldMeasurement | None = None

    def measurements(self) -> dict[str, FoldMeasurement]:
        """The folds that were actually measured, keyed by field name."""
        found = {
            "stereo": self.stereo,
            "surround_51": self.surround_51,
            "binaural": self.binaural,
        }
        return {name: m for name, m in found.items() if m is not None}

    def flagged(self) -> bool:
        """Whether any fold clears the ceiling or diverges in loudness."""
        return any(
            not m.tp_compliant or m.loudness_divergent
            for m in self.measurements().values()
        )


def measure_binaural_qc(cfg: UpmixConfig, output_fmt: OutputFormat) -> bool:
    """Whether the expensive binaural QC render runs for this bed.

    Unset (``None``) renders it for the height-bearing beds a binaural delivery
    is valid for, and skips it everywhere else — on a two-channel delivery the
    binaural path is the delivery itself and already measured.
    """
    if cfg.qc_measure_binaural is not None:
        return cfg.qc_measure_binaural
    return output_fmt.name in BINAURAL_BED_FORMATS


def _measure(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
    native_lkfs: float,
    ceiling_dbtp: float,
) -> FoldMeasurement:
    from upmixer.loudness import measure_integrated_loudness, measure_true_peak

    lkfs = measure_integrated_loudness(channels, sample_rate, fmt)
    tp_dbtp = measure_true_peak(channels)
    delta = lkfs - native_lkfs
    return FoldMeasurement(
        lkfs=lkfs,
        tp_dbtp=tp_dbtp,
        plr_db=tp_dbtp - lkfs,
        lkfs_delta_lu=delta,
        tp_compliant=tp_dbtp <= ceiling_dbtp,
        loudness_divergent=abs(delta) > FOLD_DIVERGENCE_LU,
    )


def measure_folds(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    output_fmt: OutputFormat,
    cfg: UpmixConfig,
    native_lkfs: float,
    ceiling_dbtp: float,
) -> FoldQC | None:
    """Measure every fold available for *output_fmt*.

    Args:
        channels:     the delivered (mastered, limited) bed.
        sample_rate:  audio sample rate in Hz.
        output_fmt:   the delivered bed's layout.
        cfg:          supplies the downmix coefficients and the binaural gate.
        native_lkfs:  the bed's own integrated loudness, already measured.
        ceiling_dbtp: the delivery target's True Peak ceiling.

    Returns:
        A :class:`FoldQC`, or *None* for a bed that has no fold to measure
        (a two-channel delivery is its own stereo programme).
    """
    from upmixer.loudness import measurement_programme
    from upmixer.utils import itu_downmix_stereo

    qc = FoldQC(native_lkfs=native_lkfs)

    if len(output_fmt.channels) > 2:
        left, right = itu_downmix_stereo(
            channels, cfg.surround_downmix_coeff, cfg.height_downmix_coeff
        )
        qc.stereo = _measure(
            {ChannelLabel.FL.value: left, ChannelLabel.FR.value: right},
            sample_rate,
            STEREO_OUT,
            native_lkfs,
            ceiling_dbtp,
        )

    if len(output_fmt.channels) > 6:
        programme, programme_fmt = measurement_programme(channels, output_fmt)
        qc.surround_51 = _measure(
            programme, sample_rate, programme_fmt, native_lkfs, ceiling_dbtp
        )

    if measure_binaural_qc(cfg, output_fmt):
        from upmixer.binaural.renderer import render_binaural_delivery

        rendered, _ = render_binaural_delivery(channels, output_fmt, sample_rate, cfg)
        qc.binaural = _measure(
            rendered, sample_rate, BINAURAL, native_lkfs, ceiling_dbtp
        )

    return qc if qc.measurements() else None
