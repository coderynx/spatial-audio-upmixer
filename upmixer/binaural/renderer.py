"""Renders a discrete multichannel bed to headphone-ready binaural stereo.

Signal graph (matches the web preview 1:1 — see
``docs/standards/spatial_audio_engine.md`` §1):

    bed channels -> per-speaker order-3 SH encode -> sum to 16ch HOA bus
    -> convolve with profile decode filters -> stereo -> re-add LFE
    -> profile voicing chain
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.binaural.ambisonics import N_ACN_CHANNELS, encode_gains
from upmixer.binaural.decoder import decode_to_binaural, load_decode_filter_set
from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION, positional_labels
from upmixer.binaural.profiles import DECODE_FILTER_SET, VOICING_PARAMS, resolve_profile
from upmixer.binaural.voicing import apply_voicing
from upmixer.formats import ChannelLabel, FORMAT_MAP, OutputFormat
from upmixer.loudness import normalize_loudness
from upmixer.mastering.chain import MasteringResult
from upmixer.utils import soft_limit


def _lfe_lowpass(signal: np.ndarray, sample_rate: int, cutoff_hz: float = 120.0) -> np.ndarray:
    sos = butter(2, cutoff_hz / (sample_rate / 2.0), btype="low", output="sos")
    return sosfilt(sos, signal)


def render_binaural(
    channels: dict[str, np.ndarray],
    bed_fmt: OutputFormat,
    sample_rate: int,
    profile: str,
    lfe_gain: float = 0.31622776601683794,
) -> tuple[np.ndarray, np.ndarray]:
    """Render a discrete multichannel bed to raw (unmastered) binaural stereo.

    Args:
        channels: bed channel dict, channel_name -> 1D float64 array.
        bed_fmt: the discrete bed's OutputFormat (e.g. 7.1.4).
        sample_rate: sample rate shared by all channels.
        profile: one of "studio", "listening", "flat".
        lfe_gain: linear gain applied to the LFE before it is summed into
            both ears (default -10 dB, matching ``UpmixConfig.lfe_gain``).
            The LFE is fully correlated across both ears, so summing it at
            unity doubles its perceived weight relative to the HRTF-decoded
            bed and reads as boomy.

    Returns:
        (left, right) float64 arrays of the same length as the bed channels.
    """
    resolved = resolve_profile(profile)
    labels = positional_labels(list(bed_fmt.channels))
    n_samples = next((len(channels[label.value]) for label in labels if label.value in channels), 0)

    hoa = np.zeros((N_ACN_CHANNELS, n_samples), dtype=np.float64)
    for label in labels:
        signal = channels.get(label.value)
        if signal is None or not np.any(signal):
            continue
        position = SPEAKER_AZIMUTH_ELEVATION[label]
        gains = encode_gains(position.azimuth_rad, position.elevation_rad)
        hoa += np.outer(gains, signal[:n_samples])

    filter_set = load_decode_filter_set(DECODE_FILTER_SET[resolved], sample_rate)
    left, right = decode_to_binaural(hoa, filter_set)

    if ChannelLabel.LFE.value in channels:
        lfe = _lfe_lowpass(channels[ChannelLabel.LFE.value][:n_samples], sample_rate) * lfe_gain
        left = left + lfe
        right = right + lfe

    voicing = VOICING_PARAMS[resolved]
    left, right = apply_voicing(left, right, sample_rate, voicing)
    return left, right


BINAURAL_LOUDNESS_MAX_GAIN_DB: float = 6.0
"""Ceiling for the collapse-stage loudness correction.

The bed is already BS.1770-normalized before binaural collapse (see
``UpmixPipeline``), so this pass only needs to correct for the level shift
introduced by the HOA/HRTF collapse itself — not perform a second full
loudness match. A small ceiling keeps the result "preserve the mastered
bed, correct lightly" instead of cranking a quiet collapse back up to
target, which is what made prior renders read louder than the source.
"""


def render_binaural_delivery(
    channels: dict[str, np.ndarray],
    bed_fmt: OutputFormat,
    sample_rate: int,
    cfg,
) -> tuple[dict[str, np.ndarray], MasteringResult]:
    """Render + finalize a mastered bed into a delivery-ready binaural WAV pair.

    The bed passed in is already BS.1770-mastered by ``MasteringChain``, so
    this stage only lightly corrects for the level shift introduced by the
    HOA/HRTF collapse (bounded by ``BINAURAL_LOUDNESS_MAX_GAIN_DB``) rather
    than re-running a full loudness match — the collapse concentrates energy
    from many channels into two, so a second full match would inflate
    loudness beyond the mastered bed. A gentle soft limiter runs last, after
    the gain correction, so it only ever engages as a true-peak safety net
    instead of clipping the raw HRTF sum.
    """
    left, right = render_binaural(
        channels, bed_fmt, sample_rate, cfg.binaural_profile, lfe_gain=cfg.lfe_gain
    )
    stereo_channels = {ChannelLabel.FL.value: left, ChannelLabel.FR.value: right}

    if not cfg.loudness_normalize:
        stereo_channels[ChannelLabel.FL.value] = soft_limit(left, cfg.peak_limit_threshold)
        stereo_channels[ChannelLabel.FR.value] = soft_limit(right, cfg.peak_limit_threshold)
        return stereo_channels, MasteringResult()

    resolved = resolve_profile(cfg.binaural_profile)
    target_lkfs = VOICING_PARAMS[resolved].loudness_target_lkfs or cfg.loudness_target_lkfs
    stereo_channels, info = normalize_loudness(
        stereo_channels,
        sample_rate,
        FORMAT_MAP["binaural"],
        target_lkfs=target_lkfs,
        max_tp_dbtp=cfg.loudness_max_tp,
        max_gain_db=min(cfg.loudness_max_gain_db, BINAURAL_LOUDNESS_MAX_GAIN_DB),
    )
    stereo_channels[ChannelLabel.FL.value] = soft_limit(
        stereo_channels[ChannelLabel.FL.value], cfg.peak_limit_threshold
    )
    stereo_channels[ChannelLabel.FR.value] = soft_limit(
        stereo_channels[ChannelLabel.FR.value], cfg.peak_limit_threshold
    )
    result = MasteringResult(
        measured_lkfs=info["measured_lkfs"],
        measured_tp_dbtp=info["measured_tp_dbtp"],
        applied_gain_db=info["applied_gain_db"],
        tp_limited=info["tp_limited"],
    )
    return stereo_channels, result
