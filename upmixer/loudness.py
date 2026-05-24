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

import math
import os
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.signal import sosfilt

from upmixer.formats import ChannelLabel, OutputFormat

_SURROUND_WEIGHT: float = 10.0 ** (1.5 / 10.0)

_CH_WEIGHT: dict[ChannelLabel, float] = {
    ChannelLabel.FL:  1.0,
    ChannelLabel.FR:  1.0,
    ChannelLabel.C:   1.0,
    ChannelLabel.LFE: 0.0,
    ChannelLabel.SL:  _SURROUND_WEIGHT,
    ChannelLabel.SR:  _SURROUND_WEIGHT,
    ChannelLabel.BL:  _SURROUND_WEIGHT,
    ChannelLabel.BR:  _SURROUND_WEIGHT,
    ChannelLabel.TFL: _SURROUND_WEIGHT,
    ChannelLabel.TFR: _SURROUND_WEIGHT,
    ChannelLabel.TBL: _SURROUND_WEIGHT,
    ChannelLabel.TBR: _SURROUND_WEIGHT,
}

_BLOCK_S = 0.400
_HOP_S   = 0.100
_ABS_GATE = -70.0
_REL_GATE_OFFSET = -10.0


def _shelf_sos(Wn: float, dBgain: float, Q: float, fs: int) -> list[float]:
    """High-shelf biquad (Audio EQ Cookbook). Returns [b0,b1,b2,1,a1,a2]."""
    A = 10.0 ** (dBgain / 40.0)
    w0 = 2.0 * math.pi * Wn / fs
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * Q)
    two_sqA_alpha = 2.0 * math.sqrt(A) * alpha

    b0 =  A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqA_alpha)
    b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0)
    b2 =  A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqA_alpha)
    a0 =       (A + 1.0) - (A - 1.0) * cos_w0 + two_sqA_alpha
    a1 =  2.0 * ((A - 1.0) - (A + 1.0) * cos_w0)
    a2 =       (A + 1.0) - (A - 1.0) * cos_w0 - two_sqA_alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def _hpf_sos(Wn: float, Q: float, fs: int) -> list[float]:
    """2nd-order HPF biquad (Audio EQ Cookbook). Returns [b0,b1,b2,1,a1,a2]."""
    w0 = 2.0 * math.pi * Wn / fs
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * Q)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


@lru_cache(maxsize=8)
def _k_weighting_sos(sample_rate: int) -> np.ndarray:
    """BS.1770-4 K-weighting filter as (2, 6) SOS array.

    Stage 1: pre-filter  — high shelf +4 dB above ~1.68 kHz
    Stage 2: RLB filter  — 2nd-order HPF at 38.1 Hz
    Parameters match pyloudnorm / ITU-R BS.1770-4 Annex 1.
    Cached by sample_rate so repeated calls with the same rate are free.
    """
    s1 = _shelf_sos(1681.974450955533, 3.999843853973347, 0.7071752369554196, sample_rate)
    s2 = _hpf_sos(38.13547087602444, 0.5003270373238773, sample_rate)
    return np.array([s1, s2])


def _channel_weighted_blocks(
    audio: np.ndarray,
    weight: float,
    sos: np.ndarray,
    block_len: int,
    hop_len: int,
) -> np.ndarray | None:
    """K-weight one channel and return weighted mean-square per block.

    Designed to run in a thread (scipy/numpy release the GIL).
    Returns None if the channel is too short.
    """
    if len(audio) < block_len:
        return None
    filtered = sosfilt(sos, audio.astype(np.float64))
    n_blocks = (len(filtered) - block_len) // hop_len + 1
    return np.array([
        np.mean(filtered[i * hop_len : i * hop_len + block_len] ** 2)
        for i in range(n_blocks)
    ]) * weight


def measure_integrated_loudness(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
) -> float:
    """BS.1770-4 integrated loudness with absolute + relative two-pass gating.

    Channel K-weighting is computed in parallel across threads (scipy releases
    the GIL, so actual concurrency is achieved on multi-core systems).

    Returns LKFS. Returns -70.0 for silence or content shorter than one block.
    """
    sos = _k_weighting_sos(sample_rate)
    block_len = int(_BLOCK_S * sample_rate)
    hop_len   = int(_HOP_S * sample_rate)

    tasks = []
    for label in fmt.channels:
        weight = _CH_WEIGHT.get(label, 0.0)
        if weight == 0.0:
            continue
        audio = channels.get(label.value)
        if audio is not None:
            tasks.append((weight, audio))

    if not tasks:
        return -70.0

    def _process(args):
        weight, audio = args
        return _channel_weighted_blocks(audio, weight, sos, block_len, hop_len)

    with ThreadPoolExecutor(max_workers=max(1, min(len(tasks), (os.cpu_count() or 4) // 2, 4))) as ex:
        results = list(ex.map(_process, tasks))

    power_blocks: np.ndarray | None = None
    for meansq in results:
        if meansq is None:
            continue
        if power_blocks is None:
            power_blocks = meansq
        else:
            n = min(len(power_blocks), len(meansq))
            power_blocks = power_blocks[:n] + meansq[:n]

    if power_blocks is None or len(power_blocks) == 0:
        return -70.0

    block_lkfs = -0.691 + 10.0 * np.log10(np.maximum(power_blocks, 1e-30))
    abs_mask = block_lkfs >= _ABS_GATE
    if not np.any(abs_mask):
        return -70.0

    ungated_lkfs = -0.691 + 10.0 * math.log10(max(float(np.mean(power_blocks[abs_mask])), 1e-30))
    rel_mask = abs_mask & (block_lkfs >= ungated_lkfs + _REL_GATE_OFFSET)

    gated = rel_mask if np.any(rel_mask) else abs_mask
    return -0.691 + 10.0 * math.log10(max(float(np.mean(power_blocks[gated])), 1e-30))


def measure_true_peak(channels: dict[str, np.ndarray]) -> float:
    """True Peak across all channels at 4× oversample (BS.1770-4 Annex 2).

    Oversampling is computed in parallel across threads; scipy/numpy release
    the GIL so multi-core systems benefit proportionally to channel count.
    Returns dBTP. LFE is included per spec.
    """
    from scipy.signal import resample_poly

    def _channel_tp(audio: np.ndarray) -> float:
        return float(np.max(np.abs(resample_poly(audio.astype(np.float64), 4, 1))))

    audio_list = list(channels.values())
    if not audio_list:
        return -120.0

    with ThreadPoolExecutor(max_workers=max(1, min(len(audio_list), (os.cpu_count() or 4) // 2, 4))) as ex:
        peaks = list(ex.map(_channel_tp, audio_list))

    max_tp = max(peaks) if peaks else 1e-30
    return 20.0 * math.log10(max(max_tp, 1e-30))


def normalize_loudness(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
    target_lkfs: float = -18.0,
    max_tp_dbtp: float = -1.0,
    max_gain_db: float = 30.0,
) -> tuple[dict[str, np.ndarray], dict]:
    """Apply a single linear gain for BS.1770-4 loudness + True Peak compliance.

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

    Returns:
        (adjusted_channels, info) where info dict has keys:
            measured_lkfs, measured_tp_dbtp, applied_gain_db, tp_limited.
    """
    measured_lkfs = measure_integrated_loudness(channels, sample_rate, fmt)

    gain_db = min(target_lkfs - measured_lkfs, max_gain_db)
    gain_linear = 10.0 ** (gain_db / 20.0)
    adjusted = {k: v * gain_linear for k, v in channels.items()}

    measured_tp = measure_true_peak(adjusted)
    tp_limited = False

    if measured_tp > max_tp_dbtp:
        tp_excess_db = measured_tp - max_tp_dbtp
        tp_gain = 10.0 ** (-tp_excess_db / 20.0)
        adjusted = {k: v * tp_gain for k, v in adjusted.items()}
        gain_db -= tp_excess_db
        tp_limited = True

    return adjusted, {
        "measured_lkfs":    measured_lkfs,
        "measured_tp_dbtp": measured_tp,
        "applied_gain_db":  gain_db,
        "tp_limited":       tp_limited,
    }
