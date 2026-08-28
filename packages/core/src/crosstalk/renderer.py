"""Renders a discrete multichannel bed to speaker-ready transaural stereo.

Signal graph (matches the web preview 1:1 — see
``docs/standards/transaural_speakers.md`` §1):

    bed channels -> render_binaural(profile="flat") -> anechoic ear signals
    -> profile voicing chain -> 2x2 crosstalk-cancellation FIR matrix
"""
from __future__ import annotations

import numpy as np

from upmixer.binaural.renderer import render_binaural
from upmixer.binaural.voicing import apply_voicing
from upmixer.crosstalk.filters import apply_xtc, load_xtc_filter_set
from upmixer.crosstalk.profiles import VOICING_PARAMS, XTC_FILTER_SET, resolve_profile
from upmixer.formats import TRANSAURAL, ChannelLabel, OutputFormat
from upmixer.loudness import normalize_loudness
from upmixer.mastering.chain import MasteringResult
from upmixer.mastering.delivery import resolve_delivery_target
from upmixer.utils import soft_limit


def render_crosstalk(
    channels: dict[str, np.ndarray],
    bed_fmt: OutputFormat,
    sample_rate: int,
    profile: str,
    lfe_gain: float = 0.31622776601683794,
    lfe_cutoff_hz: float = 120.0,
    lfe_filter_order: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Render a discrete multichannel bed to raw (unmastered) transaural stereo.

    Reuses the anechoic ``flat`` binaural render for the ear signals — a
    physical room and the real speaker cabinets already supply reverberant
    coloration on playback, so unlike the headphone ``studio``/``listening``
    profiles no baked room tail belongs in this path.

    Args:
        channels: bed channel dict, channel_name -> 1D float64 array.
        bed_fmt: the discrete bed's OutputFormat (e.g. 7.1.4).
        sample_rate: sample rate shared by all channels.
        profile: one of "stereo", "smart_speaker", "car", "laptop", "phone".
        lfe_gain: linear gain applied to the LFE before the ambisonic HOA
            decode (see :func:`upmixer.binaural.renderer.render_binaural`).
        lfe_cutoff_hz: LFE lowpass cutoff, matching ``UpmixConfig.lfe_cutoff_hz``.
        lfe_filter_order: LFE lowpass order, matching ``UpmixConfig.lfe_filter_order``.

    Returns:
        (left, right) float64 speaker-feed arrays, same length as the bed.
    """
    resolved = resolve_profile(profile)
    ear_left, ear_right = render_binaural(
        channels, bed_fmt, sample_rate, "flat", lfe_gain=lfe_gain,
        lfe_cutoff_hz=lfe_cutoff_hz, lfe_filter_order=lfe_filter_order,
    )

    # Voicing shapes the ear signals the canceller is asked to deliver, so it
    # runs first: applied afterwards, its M/S widening would re-introduce
    # crosstalk the matrix had just removed (asymmetric geometry only).
    ear_left, ear_right = apply_voicing(ear_left, ear_right, sample_rate, VOICING_PARAMS[resolved])

    filter_set = load_xtc_filter_set(XTC_FILTER_SET[resolved], sample_rate)
    return apply_xtc(ear_left, ear_right, filter_set)


CROSSTALK_LOUDNESS_MAX_GAIN_DB: float = 6.0
"""Ceiling for the collapse-stage loudness correction — same rationale as
``upmixer.binaural.renderer.BINAURAL_LOUDNESS_MAX_GAIN_DB``: the bed is
already BS.1770-normalized before collapse, so this pass only needs to
correct for the level shift the collapse itself introduces, not perform a
second full loudness match."""


def render_crosstalk_delivery(
    channels: dict[str, np.ndarray],
    bed_fmt: OutputFormat,
    sample_rate: int,
    cfg,
) -> tuple[dict[str, np.ndarray], MasteringResult]:
    """Render + finalize a mastered bed into a delivery-ready transaural WAV pair.

    Mirrors :func:`upmixer.binaural.renderer.render_binaural_delivery`'s gain
    staging exactly: a small bounded loudness correction for the collapse's
    own level shift, then a soft-limit safety net last (after the gain
    correction, not before — limiting the raw pre-gain sum would bake in
    saturation no later stage can undo).
    """
    left, right = render_crosstalk(
        channels, bed_fmt, sample_rate, cfg.transaural_profile, lfe_gain=cfg.lfe_gain,
        lfe_cutoff_hz=cfg.lfe_cutoff_hz, lfe_filter_order=cfg.lfe_filter_order,
    )
    stereo_channels = {ChannelLabel.FL.value: left, ChannelLabel.FR.value: right}

    resolved = resolve_profile(cfg.transaural_profile)
    delivery = resolve_delivery_target(cfg)
    target_lkfs = VOICING_PARAMS[resolved].loudness_target_lkfs or delivery.target_lkfs
    stereo_channels, info = normalize_loudness(
        stereo_channels,
        sample_rate,
        TRANSAURAL,
        target_lkfs=target_lkfs,
        max_tp_dbtp=delivery.max_tp_dbtp,
        max_gain_db=min(cfg.loudness_max_gain_db, CROSSTALK_LOUDNESS_MAX_GAIN_DB),
        apply_loudness_gain=cfg.loudness_normalize,
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
