"""Per-stem content analysis for dynamic spatial routing.

Extracts four normalized features from each separated stem:

  stereo_width   — Pearson de-correlation of L vs R channels.
                   0 = mono, 1 = fully uncorrelated.
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


def analyze_stem(audio: np.ndarray, sample_rate: int) -> StemFeatures:
    """Compute content features from a (n_samples, ≥1) float array.

    Uses up to 60 s of audio so analysis time is bounded regardless of
    track length.  Returns _NEUTRAL for silence or sub-threshold content.
    """
    if audio.ndim == 1:
        L = audio.astype(np.float64)
        R = L
    else:
        L = audio[:, 0].astype(np.float64)
        R = audio[:, 1].astype(np.float64) if audio.shape[1] > 1 else L

    mono = (L + R) * 0.5

    l_e = float(np.mean(L ** 2))
    r_e = float(np.mean(R ** 2))
    if l_e < 1e-20 or r_e < 1e-20:
        stereo_width = 0.0
    else:
        cross = float(np.mean(L * R))
        stereo_width = float(np.clip(1.0 - abs(cross) / math.sqrt(l_e * r_e), 0.0, 1.0))

    max_n = min(len(mono), sample_rate * 60)
    chunk = mono[:max_n]

    nperseg = min(4096, len(chunk))
    if nperseg < 64 or float(np.max(np.abs(chunk))) < 1e-8:
        return _NEUTRAL

    freqs, psd = welch(chunk, fs=sample_rate, nperseg=nperseg)
    total_power = float(np.sum(psd)) + 1e-30
    high_freq_ratio = float(np.clip(np.sum(psd[freqs >= 4000.0]) / total_power, 0.0, 1.0))
    low_freq_ratio  = float(np.clip(np.sum(psd[freqs <= 200.0])  / total_power, 0.0, 1.0))

    seg_len = min(2048, len(chunk))
    _, _, Sxx = spectrogram(
        chunk, fs=sample_rate,
        nperseg=seg_len,
        noverlap=seg_len * 3 // 4,
    )
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
    max_workers: int = 4,
) -> dict[str, StemFeatures]:
    """Analyze all stems in parallel.  Returns {stem_key: StemFeatures}."""
    keys = list(stems.keys())
    if not keys:
        return {}

    def _analyze(key: str) -> StemFeatures:
        return analyze_stem(stems[key], sample_rate)

    with ThreadPoolExecutor(max_workers=min(len(keys), max_workers)) as ex:
        features = list(ex.map(_analyze, keys))

    return dict(zip(keys, features))
