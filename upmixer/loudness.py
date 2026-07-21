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
from functools import lru_cache

import numpy as np
from scipy.signal import sosfilt, upfirdn

from upmixer.formats import ChannelLabel, OutputFormat

_SURROUND_WEIGHT: float = 1.41  # BS.1770-4 Annex 1 Table 3 literal value

_CH_WEIGHT: dict[ChannelLabel, float] = {
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
_REL_GATE_OFFSET = -10.0


def _retarget_biquad(section: list[float], sample_rate: int) -> list[float]:
    """Retarget an exact 48 kHz digital biquad by inverse/forward bilinear maps."""
    b0, b1, b2, _, a1, a2 = section
    k = 2.0 * 48_000.0

    def _to_analog(c0: float, c1: float, c2: float) -> list[float]:
        return [
            (c0 - c1 + c2) / (k * k),
            2.0 * (c0 - c2) / k,
            c0 + c1 + c2,
        ]

    b_a = _to_analog(b0, b1, b2)
    a_a = _to_analog(1.0, a1, a2)
    from scipy.signal import bilinear

    b_z, a_z = bilinear(b_a, a_a, fs=sample_rate)
    return [b_z[0], b_z[1], b_z[2], 1.0, a_z[1], a_z[2]]


@lru_cache(maxsize=8)
def _k_weighting_sos(sample_rate: int) -> np.ndarray:
    """BS.1770-4 K-weighting filter as (2, 6) SOS array.

    Stage 1: pre-filter  — high shelf +4 dB above ~1.68 kHz
    Stage 2: RLB filter  — 2nd-order HPF at 38.1 Hz
    At 48 kHz: exact tabulated values per BS.1770-4 Annex 1 Tables 1-2.
    At 96 kHz and other rates: analytically re-derived to match 48 kHz
    frequency response shape, per BS.1770-4 Annex 1 note.
    """
    if sample_rate == 48000:
        # BS.1770-4 Annex 1, Table 1 (Stage 1) + Table 2 (Stage 2) — exact values
        s1 = [1.53512485958697, -2.69169618940638, 1.19839281085285,
              1.0, -1.69065929318241, 0.73248077421585]
        s2 = [1.0, -2.0, 1.0,
              1.0, -1.99004745483398, 0.99007225036621]
        return np.array([s1, s2])
    s1_48 = [1.53512485958697, -2.69169618940638, 1.19839281085285,
              1.0, -1.69065929318241, 0.73248077421585]
    s2_48 = [1.0, -2.0, 1.0,
              1.0, -1.99004745483398, 0.99007225036621]
    return np.array([
        _retarget_biquad(s1_48, sample_rate),
        _retarget_biquad(s2_48, sample_rate),
    ])


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
    np.square(filtered, out=filtered)
    np.cumsum(filtered, out=filtered)
    starts = np.arange(0, len(audio) - block_len + 1, hop_len)
    ends = starts + block_len - 1
    block_sums = filtered[ends].copy()
    nonzero = starts > 0
    block_sums[nonzero] -= filtered[starts[nonzero] - 1]
    return block_sums * (weight / block_len)


def measure_integrated_loudness(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    fmt: OutputFormat,
) -> float:
    """BS.1770-4 integrated loudness with absolute + relative two-pass gating.

    Processes one channel at a time to bound memory for long immersive masters.

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

    power_blocks: np.ndarray | None = None
    for weight, audio in tasks:
        meansq = _channel_weighted_blocks(audio, weight, sos, block_len, hop_len)
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


_TRUE_PEAK_FIR_4X = np.array([
    0.0017089843750, -0.0291748046875, -0.0189208984375, -0.0083007812500,
    0.0109863281250, 0.0292968750000, 0.0330810546875, 0.0148925781250,
    -0.0196533203125, -0.0517578125000, -0.0582275390625, -0.0266113281250,
    0.0332031250000, 0.0891113281250, 0.1015625000000, 0.0476074218750,
    -0.0594482421875, -0.1665039062500, -0.2003173828125, -0.1022949218750,
    0.1373291015625, 0.4650878906250, 0.7797851562500, 0.9721679687500,
    0.9721679687500, 0.7797851562500, 0.4650878906250, 0.1373291015625,
    -0.1022949218750, -0.2003173828125, -0.1665039062500, -0.0594482421875,
    0.0476074218750, 0.1015625000000, 0.0891113281250, 0.0332031250000,
    -0.0266113281250, -0.0582275390625, -0.0517578125000, -0.0196533203125,
    0.0148925781250, 0.0330810546875, 0.0292968750000, 0.0109863281250,
    -0.0083007812500, -0.0189208984375, -0.0291748046875, 0.0017089843750,
], dtype=np.float64)


def _true_peak_channel(audio: np.ndarray, chunk_size: int = 262_144) -> float:
    """Meter one channel with bounded-memory BS.1770 4x interpolation."""
    if len(audio) == 0:
        return 0.0
    history = np.zeros(len(_TRUE_PEAK_FIR_4X) - 1, dtype=np.float64)
    peak = 0.0
    for start in range(0, len(audio), chunk_size):
        chunk = np.asarray(audio[start:start + chunk_size], dtype=np.float64)
        padded = np.concatenate((history, chunk))
        upsampled = upfirdn(_TRUE_PEAK_FIR_4X, padded, up=4)
        begin = len(history) * 4
        end = begin + len(chunk) * 4
        peak = max(peak, float(np.max(np.abs(upsampled[begin:end]))))
        history = padded[-len(history):]
    tail = upfirdn(_TRUE_PEAK_FIR_4X, history, up=4)
    peak = max(peak, float(np.max(np.abs(tail[-(len(_TRUE_PEAK_FIR_4X) - 1):]))))
    return peak


def measure_true_peak(channels: dict[str, np.ndarray], sample_rate: int = 48000) -> float:
    """True Peak across all channels (BS.1770-4 Annex 2).

    Uses BS.1770-5 Annex 2 order-48 4-phase FIR interpolation.  Four-times
    oversampling is retained at 96 kHz because higher ratios are permitted.
    Channels and samples are processed in bounded chunks.
    Returns dBTP. LFE is included per spec.
    """
    audio_list = list(channels.values())
    if not audio_list:
        return -120.0

    max_tp: float = 1e-30
    for audio in audio_list:
        peak = _true_peak_channel(audio)
        if peak > max_tp:
            max_tp = peak

    return 20.0 * math.log10(max_tp)


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
    measurable = measured_lkfs > _ABS_GATE
    gain_db = min(target_lkfs - measured_lkfs, max_gain_db) if measurable else 0.0
    gain_linear = 10.0 ** (gain_db / 20.0)
    adjusted = {k: v.copy() for k, v in channels.items()}
    for v in adjusted.values():
        v *= gain_linear

    measured_tp = measure_true_peak(adjusted, sample_rate)
    tp_limited = False

    if measured_tp > max_tp_dbtp:
        tp_excess_db = measured_tp - max_tp_dbtp
        tp_gain = 10.0 ** (-tp_excess_db / 20.0)
        for v in adjusted.values():
            v *= tp_gain
        gain_db -= tp_excess_db
        tp_limited = True

    final_lkfs = measure_integrated_loudness(adjusted, sample_rate, fmt)
    final_tp = measure_true_peak(adjusted, sample_rate)
    return adjusted, {
        "pre_lkfs":         measured_lkfs,
        "measured_lkfs":    final_lkfs,
        "measured_tp_dbtp": final_tp,
        "applied_gain_db":  gain_db,
        "tp_limited":       tp_limited,
    }
