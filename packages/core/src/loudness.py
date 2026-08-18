"""ITU-R BS.1770-4 integrated loudness measurement and normalization.

Targets Dolby Atmos Music Delivery Playbook (June 2024):
  Integrated loudness : -18.0 LKFS  (Dolby Atmos Music target)
  True Peak           :  -1.0 dBTP  (Dolby ceiling)

Channel weights follow BS.1770-4 §2.2 Table 1:
  L / R / C   : 1.0
  LFE         : excluded (weight 0)
  all surround / height : 10^(1.5/10) ≈ 1.4125  (+1.5 dB)
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import upmixer_dsp

from upmixer.formats import ChannelLabel, OutputFormat

_SURROUND_WEIGHT: float = 1.41  # BS.1770-4 Annex 1 Table 3 literal value

CHANNEL_WEIGHT: dict[ChannelLabel, float] = {
    ChannelLabel.FL:  1.0,
    ChannelLabel.FR:  1.0,
    ChannelLabel.C:   1.0,
    ChannelLabel.LFE: 0.0,
    ChannelLabel.SL:  _SURROUND_WEIGHT,
    ChannelLabel.SR:  _SURROUND_WEIGHT,
    # BS.1770-5 Annex 3 Table 5: rear (M±135) and upper channels
    # have unity gain.  Only ear-level side channels receive +1.5 dB.
    ChannelLabel.BL:  1.0,
    ChannelLabel.BR:  1.0,
    ChannelLabel.TFL: 1.0,
    ChannelLabel.TFR: 1.0,
    ChannelLabel.TBL: 1.0,
    ChannelLabel.TBR: 1.0,
}

_BLOCK_S = 0.400
_HOP_S   = 0.100
_ABS_GATE = -70.0
_LKFS_OFFSET = -0.691
_REL_GATE_OFFSET = -10.0


@lru_cache(maxsize=8)
def _k_weighting_sos(sample_rate: int) -> np.ndarray:
    """BS.1770-4 K-weighting filter as (2, 6) SOS array.

    Stage 1: pre-filter  — high shelf +4 dB above ~1.68 kHz
    Stage 2: RLB filter  — 2nd-order HPF at 38.1 Hz
    At 48 kHz: exact tabulated values per BS.1770-4 Annex 1 Tables 1-2.
    At 96 kHz and other rates: analytically re-derived to match 48 kHz
    frequency response shape, per BS.1770-4 Annex 1 note.
    """
    return upmixer_dsp.k_weighting_sos(sample_rate)


def _weighted_channels(
    channels: dict[str, np.ndarray],
    fmt: OutputFormat,
) -> tuple[list[float], list[np.ndarray]]:
    """Pair each present, non-zero-weight channel with its BS.1770 weight."""
    weights: list[float] = []
    audio: list[np.ndarray] = []
    for label in fmt.channels:
        weight = CHANNEL_WEIGHT.get(label, 0.0)
        if weight == 0.0:
            continue
        channel = channels.get(label.value)
        if channel is not None:
            weights.append(weight)
            audio.append(np.ascontiguousarray(channel, dtype=np.float64))
    return weights, audio


def measure_integrated_loudness(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
) -> float:
    """BS.1770-4 integrated loudness with absolute + relative two-pass gating.

    Returns LKFS. Returns -70.0 for silence or content shorter than one block.
    """
    weights, audio = _weighted_channels(channels, fmt)
    if not weights:
        return -70.0
    return upmixer_dsp.integrated_loudness(weights, audio, sample_rate)


def k_weighted_power(signal: np.ndarray, sample_rate: int) -> float:
    """Gated K-weighted mean square of one channel — BS.1770-4's ``z_i``.

    The per-channel term the loudness sum is built from, so callers can weight
    and combine channels themselves. Returns 0.0 when the material is too
    short (under one 400 ms block) or too quiet to gate.
    """
    lkfs = upmixer_dsp.integrated_loudness(
        [1.0], [np.ascontiguousarray(signal, dtype=np.float64)], sample_rate
    )
    return 10.0 ** ((lkfs - _LKFS_OFFSET) / 10.0) if lkfs > _ABS_GATE else 0.0


def measure_true_peak(channels: dict[str, np.ndarray]) -> float:
    """True Peak across all channels (BS.1770-4 Annex 2).

    Uses BS.1770-5 Annex 2 order-48 4-phase FIR interpolation.  Four-times
    oversampling is retained at 96 kHz because higher ratios are permitted.
    Returns dBTP. LFE is included per spec.
    """
    audio = [np.ascontiguousarray(v, dtype=np.float64) for v in channels.values()]
    if not audio:
        return -120.0
    return upmixer_dsp.true_peak_dbtp(audio)


def normalize_loudness(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
    target_lkfs: float = -18.0,
    max_tp_dbtp: float = -1.0,
    max_gain_db: float = 30.0,
    apply_tp_gain: bool = True,
) -> tuple[dict[str, np.ndarray], dict]:
    """Apply a single linear gain for BS.1770-4 loudness (+ optional True Peak) compliance.

    Non-destructive: a scalar multiplier only — no compression, no clipping.
    If content would exceed max_tp_dbtp after loudness normalization, gain is
    reduced further (still linear) to meet the True Peak ceiling.

    Args:
        channels: channel_name → 1D float64 array.
        sample_rate: audio sample rate.
        fmt: output format (selects channel weights for loudness measurement).
        target_lkfs: integrated loudness target in LKFS.
        max_tp_dbtp: True Peak ceiling in dBTP.
        max_gain_db: maximum upward gain to prevent noise amplification.
        apply_tp_gain: if ``False``, skip the scalar True-Peak gain reduction
            below and only apply the loudness gain — for callers that hand
            True-Peak compliance to a downstream limiter instead (see
            :class:`~upmixer.mastering.limiter.LookAheadLimiter`, used by
            :class:`~upmixer.mastering.chain.MasteringChain`). Callers
            without such a limiter (e.g. the binaural renderer) must leave
            this ``True``.

    Returns:
        (adjusted_channels, info) where info dict has keys:
            measured_lkfs, measured_tp_dbtp, applied_gain_db, tp_limited.
    """
    measured_lkfs = measure_integrated_loudness(channels, sample_rate, fmt)
    measurable = measured_lkfs > _ABS_GATE
    gain_db = min(target_lkfs - measured_lkfs, max_gain_db) if measurable else 0.0
    gain_linear = 10.0 ** (gain_db / 20.0)
    adjusted = {k: v.copy() for k, v in channels.items()}
    for v in adjusted.values():
        v *= gain_linear

    measured_tp = measure_true_peak(adjusted)
    tp_limited = False

    if apply_tp_gain and measured_tp > max_tp_dbtp:
        tp_excess_db = measured_tp - max_tp_dbtp
        tp_gain = 10.0 ** (-tp_excess_db / 20.0)
        for v in adjusted.values():
            v *= tp_gain
        gain_db -= tp_excess_db
        tp_limited = True

    final_lkfs = measure_integrated_loudness(adjusted, sample_rate, fmt)
    final_tp = measure_true_peak(adjusted)
    return adjusted, {
        "pre_lkfs":         measured_lkfs,
        "measured_lkfs":    final_lkfs,
        "measured_tp_dbtp": final_tp,
        "applied_gain_db":  gain_db,
        "tp_limited":       tp_limited,
    }
