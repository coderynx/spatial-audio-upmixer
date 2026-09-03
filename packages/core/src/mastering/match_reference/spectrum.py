"""Gated, BS.1770-weighted power spectrum estimation for reference matching.

Shared analysis primitive for :mod:`.curve` (the correction-curve algorithm)
and :mod:`.processor` (level matching). Turns a multichannel bed or reference
file into one power spectrum

    S(f) = Sum_i G_i * mean_gated(|X_i(f)|^2)

where ``G_i`` is the BS.1770 channel weight (:data:`upmixer.loudness.
CHANNEL_WEIGHT` -- 1.0 for L/R/C/back/height, 1.41 for ear-level side
surrounds, 0.0 for LFE) and the gate excludes near-silent frames (two-stage
absolute/relative energy gate, same shape as BS.1770's loudness gate but
applied to broadband STFT-frame energy rather than 400 ms loudness blocks) so
an intro, outro, or fade doesn't bias the average.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.loudness import CHANNEL_WEIGHT, measure_integrated_loudness

_log = logging.getLogger(__name__)

_ABS_GATE_DB: float = -70.0
_REL_GATE_OFFSET_DB: float = -10.0
_EPS: float = 1e-20

_REFERENCE_CHANNEL_ORDER: dict[int, tuple[ChannelLabel, ...]] = {
    6: (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.LFE,
        ChannelLabel.SL, ChannelLabel.SR),
    8: (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.LFE,
        ChannelLabel.BL, ChannelLabel.BR, ChannelLabel.SL, ChannelLabel.SR),
    10: (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.LFE,
         ChannelLabel.BL, ChannelLabel.BR, ChannelLabel.SL, ChannelLabel.SR,
         ChannelLabel.TFL, ChannelLabel.TFR),
    12: (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.LFE,
         ChannelLabel.BL, ChannelLabel.BR, ChannelLabel.SL, ChannelLabel.SR,
         ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR),
}
"""Canonical WAV channel order per supported reference channel count (matches
``upmixer.formats.SURROUND_714``'s back-before-side ordering). 1- and 2-
channel references need no table: every label at those counts is unity
weight in BS.1770, so channel identity doesn't matter."""

_SUPPORTED_REF_CHANNEL_COUNTS: tuple[int, ...] = (1, 2, 6, 8, 10, 12)


def _canonicalize_reference(ref_data: np.ndarray) -> tuple[np.ndarray, tuple[ChannelLabel, ...]]:
    """Map a reference file's channel count onto a canonical layout.

    Exact matches (1, 2, 6, 8, 10, 12) pass through unchanged. Anything else
    is truncated or zero-padded to the nearest supported count so LFE
    exclusion and surround weighting still apply — the alternative (treating
    every channel as unity-weight full-range) would let a genuine LFE
    channel dominate the spectrum.
    """
    n_ch = ref_data.shape[1]
    if n_ch == 1:
        return ref_data, (ChannelLabel.C,)
    if n_ch == 2:
        return ref_data, (ChannelLabel.FL, ChannelLabel.FR)
    order = _REFERENCE_CHANNEL_ORDER.get(n_ch)
    if order is not None:
        return ref_data, order

    nearest = min(_SUPPORTED_REF_CHANNEL_COUNTS, key=lambda x: abs(x - n_ch))
    order = (ChannelLabel.C,) if nearest == 1 else (
        (ChannelLabel.FL, ChannelLabel.FR) if nearest == 2 else _REFERENCE_CHANNEL_ORDER[nearest]
    )
    _log.warning(
        "Match reference: reference has %d channels; no canonical layout for "
        "this count, using nearest (%d-channel). For best results use a "
        "reference with a standard channel count (1, 2, 6, 8, 10, 12).",
        n_ch, nearest,
    )
    if n_ch > nearest:
        data = ref_data[:, :nearest]
    else:
        pad = np.zeros((ref_data.shape[0], nearest - n_ch), dtype=ref_data.dtype)
        data = np.concatenate([ref_data, pad], axis=1)
    return data, order


def weighted_power_spectrum_arrays(
    arrays: list[np.ndarray],
    weights: list[float],
    sample_rate: int,
    n_fft: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Gated, weighted sum of per-array power spectra.

    ``weights`` follow BS.1770 channel weighting — pass 0.0 to exclude a
    channel (e.g. LFE) from both the gate and the sum. The gate is computed
    once from the weighted broadband energy across all kept channels, then
    applied identically when averaging every channel's spectrum, so silence
    is judged on the whole programme rather than per channel.

    Assumes every array is the same length (true of a bed's own channels and
    of one reference file's columns — both come from a single aligned
    array); a length mismatch would silently misalign frequency bins rather
    than raise, since ``_frame_power``'s ``nperseg`` tracks each array's own
    length.

    Returns ``(freqs, power)`` with the DC bin stripped.
    """
    if not any(w > 0.0 for w in weights):
        raise ValueError("weighted_power_spectrum_arrays: no channels with nonzero weight")
    return upmixer_dsp.weighted_power_spectrum(
        [np.ascontiguousarray(a, dtype=np.float64) for a in arrays],
        list(weights),
        sample_rate,
        n_fft,
        _ABS_GATE_DB,
        _REL_GATE_OFFSET_DB,
        _EPS,
    )


def weighted_power_spectrum(
    channels: dict[str, np.ndarray],
    sample_rate: int,
    n_fft: int,
    lfe_key: str = "LFE",
) -> tuple[np.ndarray, np.ndarray]:
    """:func:`weighted_power_spectrum_arrays` over a channel-name dict.

    LFE (``lfe_key``) is always excluded. Names not in ``ChannelLabel``
    default to unity weight.
    """
    names = list(channels.keys())
    arrays = [channels[n] for n in names]
    weights: list[float] = []
    for name in names:
        if name == lfe_key:
            weights.append(0.0)
            continue
        try:
            weights.append(CHANNEL_WEIGHT.get(ChannelLabel(name), 1.0))
        except ValueError:
            weights.append(1.0)
    return weighted_power_spectrum_arrays(arrays, weights, sample_rate, n_fft)


def weighted_power_spectrum_reference(
    ref_data: np.ndarray,
    sample_rate: int,
    n_fft: int,
) -> tuple[np.ndarray, np.ndarray]:
    """:func:`weighted_power_spectrum_arrays` over a loaded reference file."""
    data, order = _canonicalize_reference(ref_data)
    weights = [CHANNEL_WEIGHT.get(label, 1.0) for label in order]
    arrays = [data[:, i] for i in range(data.shape[1])]
    return weighted_power_spectrum_arrays(arrays, weights, sample_rate, n_fft)


def reference_integrated_loudness(ref_data: np.ndarray, sample_rate: int) -> float:
    """BS.1770 integrated loudness (LKFS) of a loaded reference file, reusing
    :func:`upmixer.loudness.measure_integrated_loudness` against a canonical
    layout resolved by :func:`_canonicalize_reference`."""
    data, order = _canonicalize_reference(ref_data)
    fmt = OutputFormat(name=f"reference-{len(order)}ch", channels=order)
    channels = {label.value: data[:, i] for i, label in enumerate(order)}
    return measure_integrated_loudness(channels, sample_rate, fmt)
