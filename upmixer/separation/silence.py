"""Silence detection and span stitching for the stem separation pipeline.

Provides utilities to locate active (non-silent) regions in audio and to
reassemble per-span separator outputs into a full-length array without
audible clicks at the active/silent boundaries.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import as_strided


def find_active_spans(
    audio: np.ndarray,
    sr: int,
    threshold_db: float = -90.0,
    min_silence_s: float = 2.0,
    pad_ms: float = 200.0,
    min_active_s: float = 5.0,
) -> list[tuple[int, int]]:
    """Find contiguous active (non-silent) spans in *audio*.

    Args:
        audio: ``(n_samples,)`` or ``(n_samples, channels)`` float array.
        sr: Sample rate in Hz.
        threshold_db: Peak threshold in dBFS.  Windows at or below this level
            are treated as silent.
        min_silence_s: Minimum silent run duration in seconds.  Silent gaps
            shorter than this are merged into the surrounding active span to
            avoid fragmenting on brief pauses.
        pad_ms: Padding in milliseconds added to both ends of each active span
            so the separator has musical context near transient boundaries.
        min_active_s: Minimum active span duration in seconds.  Shorter spans
            are expanded outward — very short clips can destabilise some
            separator models.

    Returns:
        List of ``(start_sample, end_sample)`` tuples (end is exclusive).
        Returns ``[]`` when the entire zone is silent (AI should be skipped).
        Returns ``[(0, n_samples)]`` when the entire zone is active.
    """
    mono = np.mean(audio, axis=1) if audio.ndim == 2 else np.asarray(audio, dtype=float)
    n = len(mono)
    if n == 0:
        return []

    window_len = max(1, int(0.020 * sr))
    hop_len = max(1, int(0.010 * sr))

    if n < window_len:
        peak = float(np.max(np.abs(mono)))
        db = 20.0 * np.log10(max(peak, 1e-12))
        return [(0, n)] if db > threshold_db else []

    n_windows = (n - window_len) // hop_len + 1
    abs_mono = np.abs(mono)
    strides = (abs_mono.strides[0] * hop_len, abs_mono.strides[0])
    windows = as_strided(abs_mono, shape=(n_windows, window_len), strides=strides)
    peaks = windows.max(axis=1)
    db_vals = 20.0 * np.log10(np.maximum(peaks, 1e-12))
    active_mask = db_vals > threshold_db

    if not np.any(active_mask):
        return []
    if np.all(active_mask):
        return [(0, n)]

    runs = _mask_to_runs(active_mask)

    min_silence_windows = max(1, int(min_silence_s * sr / hop_len))
    runs = _merge_short_gaps(runs, min_silence_windows)

    spans: list[tuple[int, int]] = []
    for w_start, w_end in runs:
        s_start = w_start * hop_len
        s_end = min(n, w_end * hop_len + window_len)
        spans.append((s_start, s_end))

    pad_samp = int(pad_ms / 1000.0 * sr)
    spans = [(max(0, s - pad_samp), min(n, e + pad_samp)) for s, e in spans]
    spans = _merge_overlapping(spans)

    min_active_samp = int(min_active_s * sr)
    expanded: list[tuple[int, int]] = []
    for s_start, s_end in spans:
        if s_end - s_start < min_active_samp:
            center = (s_start + s_end) // 2
            s_start = max(0, center - min_active_samp // 2)
            s_end = min(n, s_start + min_active_samp)
            if s_end > n:
                s_end = n
                s_start = max(0, n - min_active_samp)
        expanded.append((s_start, s_end))
    spans = _merge_overlapping(expanded)

    if len(spans) == 1 and spans[0] == (0, n):
        return [(0, n)]
    return spans


def stitch_with_crossfade(
    span_outputs: list[tuple[int, int, np.ndarray]],
    total_length: int,
    fade_samples: int,
) -> np.ndarray:
    """Assemble per-span separator outputs into a full-length stereo array.

    Args:
        span_outputs: List of ``(start_sample, end_sample, audio)`` tuples.
            *end_sample* is exclusive.  *audio* should be ``(n, 2) float32``.
        total_length: Number of samples in the output array.
        fade_samples: Linear fade length in samples applied at each
            active/silent boundary to prevent clicks.

    Returns:
        ``(total_length, 2) float32`` array.  Silent regions are exactly zero.
        Active regions contain the separator output with a linear fade applied
        at both edges.
    """
    out = np.zeros((total_length, 2), dtype=np.float32)

    for s_start, s_end, audio in span_outputs:
        span_len = s_end - s_start
        if span_len <= 0:
            continue
        n_audio = len(audio)

        if n_audio >= span_len:
            chunk = audio[:span_len].astype(np.float32)
        else:
            chunk = np.zeros((span_len, 2), dtype=np.float32)
            chunk[:n_audio] = audio.astype(np.float32)

        if fade_samples > 0:
            fade_len = min(fade_samples, span_len // 2)
            if fade_len > 0:
                ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
                chunk[:fade_len] *= ramp[:, np.newaxis]
                chunk[span_len - fade_len:] *= ramp[::-1, np.newaxis]

        actual_end = min(s_end, total_length)
        write_len = actual_end - s_start
        if write_len > 0:
            out[s_start:actual_end] = chunk[:write_len]

    return out


def _mask_to_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    diff = np.diff(padded)
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0] - 1
    return list(zip(starts.tolist(), ends.tolist()))


def _merge_short_gaps(
    runs: list[tuple[int, int]], min_gap: int
) -> list[tuple[int, int]]:
    if len(runs) <= 1:
        return runs
    merged = [[runs[0][0], runs[0][1]]]
    for start, end in runs[1:]:
        gap = start - merged[-1][1] - 1
        if gap < min_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _merge_overlapping(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return spans
    merged = [[spans[0][0], spans[0][1]]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]
