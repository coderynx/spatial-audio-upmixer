"""Per-stem content analysis for dynamic spatial routing.

Extracts four normalized features from each separated stem:

  stereo_width   — Signed L/R coherence measure.
                   0 = mono, 1 = uncorrelated, anti-phase, or hard-panned.
                   Drives surround channel gain scaling.

  high_freq_ratio — Fraction of total spectral energy at or above 4 kHz.
                    Drives height channel gain scaling.

  low_freq_ratio  — Fraction of total spectral energy at or below 200 Hz.
                    Drives LFE gain scaling.

  transient_ratio — Fraction of analysis frames with strong positive spectral flux.
                    High = percussive/direct; low = sustained/diffuse.
                    Drives front vs. surround balance.

All features are in [0, 1].  Analysis runs in parallel across stems.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from scipy.signal import welch, spectrogram


@dataclass(frozen=True)
class StemFeatures:
    """Content features for one separated stem.

    All values are in [0, 1].  Used by StemRouter to scale static routing
    table gains so that spatial placement adapts to the actual audio.
    """
    stereo_width: float
    high_freq_ratio: float
    low_freq_ratio: float
    transient_ratio: float


_NEUTRAL = StemFeatures(
    stereo_width=0.5,
    high_freq_ratio=0.3,
    low_freq_ratio=0.2,
    transient_ratio=0.3,
)


def analyze_stem(
    audio: np.ndarray,
    sample_rate: int,
    high_frequency_hz: float = 4000.0,
) -> StemFeatures:
    """Compute content features from a (n_samples, ≥1) float array.

    Uses up to 60 s sampled across the track so analysis time and memory stay
    bounded regardless of track length.  Returns _NEUTRAL for silence.
    """
    if audio.ndim == 1:
        left = audio
        right = audio
    else:
        left = audio[:, 0]
        right = audio[:, 1] if audio.shape[1] > 1 else left

    max_n = min(len(left), sample_rate * 60)
    if max_n < 64:
        return _NEUTRAL

    if len(left) > max_n:
        n_windows = min(3, len(left) // max(1, sample_rate))
        n_windows = max(1, n_windows)
        window_n = max_n // n_windows
        starts = np.linspace(0, len(left) - window_n, n_windows, dtype=int)
        L = np.concatenate([left[s:s + window_n] for s in starts]).astype(np.float32)
        R = np.concatenate([right[s:s + window_n] for s in starts]).astype(np.float32)
    else:
        L = left[:max_n].astype(np.float32, copy=False)
        R = right[:max_n].astype(np.float32, copy=False)

    l_e = float(np.dot(L, L) / len(L))
    r_e = float(np.dot(R, R) / len(R))
    if max(l_e, r_e) < 1e-20:
        return _NEUTRAL
    if min(l_e, r_e) < 1e-20:
        stereo_width = 1.0
    else:
        correlation = float(np.dot(L, R) / (len(L) * math.sqrt(l_e * r_e)))
        stereo_width = float(np.clip(1.0 - correlation, 0.0, 1.0))

    nperseg = min(4096, len(L))

    freqs, psd_l = welch(L, fs=sample_rate, nperseg=nperseg)
    _, psd_r = welch(R, fs=sample_rate, nperseg=nperseg)
    psd = (psd_l + psd_r) * 0.5
    total_power = float(np.sum(psd)) + 1e-30
    high_freq_ratio = float(np.clip(np.sum(psd[freqs >= high_frequency_hz]) / total_power, 0.0, 1.0))
    low_freq_ratio  = float(np.clip(np.sum(psd[freqs <= 200.0])  / total_power, 0.0, 1.0))

    seg_len = min(2048, len(L))
    _, _, s_l = spectrogram(
        L, fs=sample_rate,
        nperseg=seg_len,
        noverlap=seg_len * 3 // 4,
    )
    _, _, s_r = spectrogram(
        R, fs=sample_rate,
        nperseg=seg_len,
        noverlap=seg_len * 3 // 4,
    )
    Sxx = (s_l + s_r) * 0.5
    if Sxx.shape[1] > 1:
        flux = np.sum(np.maximum(np.diff(Sxx, axis=1), 0.0), axis=0)
        threshold = float(np.percentile(flux, 75)) + 1e-30
        transient_ratio = float(np.clip(np.mean(flux > threshold * 1.5), 0.0, 1.0))
    else:
        transient_ratio = _NEUTRAL.transient_ratio

    return StemFeatures(
        stereo_width=stereo_width,
        high_freq_ratio=high_freq_ratio,
        low_freq_ratio=low_freq_ratio,
        transient_ratio=transient_ratio,
    )


def analyze_stems(
    stems: dict[str, np.ndarray],
    sample_rate: int,
    high_frequency_hz: float = 4000.0,
    max_workers: int = 2,
) -> dict[str, StemFeatures]:
    """Analyze all stems in parallel.  Returns {stem_key: StemFeatures}."""
    keys = list(stems.keys())
    if not keys:
        return {}

    def _analyze(key: str) -> StemFeatures:
        return analyze_stem(stems[key], sample_rate, high_frequency_hz)

    with ThreadPoolExecutor(max_workers=min(len(keys), max_workers)) as ex:
        features = list(ex.map(_analyze, keys))

    return dict(zip(keys, features))
