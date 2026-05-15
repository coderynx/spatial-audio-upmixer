"""Whole-file content analysis for separated instrument stems.

Extracts perceptually meaningful features from a stereo stem to guide
content-aware spatial routing. All analysis is done in one pass over the
full signal, so it is suitable for file-based (non-streaming) pipelines only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class StemFeatures:
    """Per-stem content descriptor used by ContentMixer for routing decisions."""

    rms: float                 # linear RMS of the stereo mix
    spectral_centroid_hz: float  # energy-weighted frequency centre (Hz)
    lf_ratio: float            # fraction of energy below lf_cutoff_hz  ∈ [0,1]
    hf_ratio: float            # fraction of energy above hf_cutoff_hz  ∈ [0,1]
    stereo_width: float        # Side / (Mid + Side + ε)                ∈ [0,1]
    transient_density: float   # normalised onset rate                  ∈ [0,1]
    spectral_flatness: float   # Wiener entropy (0=tonal, 1=noise-like) ∈ [0,1]


class StemAnalyzer:
    """Compute StemFeatures from a complete stereo stem array.

    Args:
        sample_rate: audio sample rate.
        lf_cutoff_hz: upper edge of the low-frequency analysis band.
        hf_cutoff_hz: lower edge of the high-frequency analysis band.
        fft_size: FFT window for spectral analysis.
        transient_norm_rate: onsets/second that maps to transient_density = 1.0.
    """

    def __init__(
        self,
        sample_rate: int,
        lf_cutoff_hz: float = 250.0,
        hf_cutoff_hz: float = 4000.0,
        fft_size: int = 4096,
        transient_norm_rate: float = 8.0,
    ) -> None:
        self._sr = sample_rate
        self._lf_hz = lf_cutoff_hz
        self._hf_hz = hf_cutoff_hz
        self._fft = fft_size
        self._trans_norm = transient_norm_rate

        # Pre-compute constant spectral masks
        freqs = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
        self._freqs = freqs
        self._lf_mask = freqs < lf_cutoff_hz
        self._hf_mask = freqs > hf_cutoff_hz
        self._window = np.hanning(fft_size)

    def analyze(self, audio: np.ndarray) -> StemFeatures:
        """Extract StemFeatures from a (n_samples, 2) float stereo array."""
        L = audio[:, 0].astype(np.float64)
        R = audio[:, 1].astype(np.float64) if audio.shape[1] > 1 else audio[:, 0].astype(np.float64)
        mono = (L + R) * 0.5

        # ── RMS ──────────────────────────────────────────────────────────────
        rms = float(np.sqrt(np.mean(mono ** 2)))

        # ── Stereo width (M/S) ───────────────────────────────────────────────
        mid  = mono
        side = (L - R) * 0.5
        m_pow = float(np.mean(mid ** 2))
        s_pow = float(np.mean(side ** 2))
        stereo_width = s_pow / (m_pow + s_pow + 1e-12)

        # ── Spectral features via short-time FFT ────────────────────────────
        n = len(mono)
        fft_size = self._fft
        hop = fft_size // 2

        if n < fft_size:
            # Too short: fall back to a single zero-padded frame
            padded = np.zeros(fft_size)
            padded[:n] = mono[:n]
            specs = np.abs(np.fft.rfft(padded * self._window))[np.newaxis, :]
        else:
            starts = range(0, n - fft_size, hop)
            specs = np.array([
                np.abs(np.fft.rfft(mono[s : s + fft_size] * self._window))
                for s in starts
            ])  # (n_frames, n_bins)

        mean_spec = np.mean(specs, axis=0)  # (n_bins,)
        spec_sq   = mean_spec ** 2
        total_sq  = float(np.sum(spec_sq)) + 1e-12

        # Spectral centroid (Hz)
        spectral_centroid_hz = float(np.sum(self._freqs * spec_sq) / total_sq)

        # LF / HF energy fractions
        lf_ratio = float(np.sum(spec_sq[self._lf_mask]) / total_sq)
        hf_ratio = float(np.sum(spec_sq[self._hf_mask]) / total_sq)

        # Spectral flatness (Wiener entropy, clipped to [0, 1])
        eps = 1e-12
        log_geom = float(np.exp(np.mean(np.log(mean_spec + eps))))
        arith_mean = float(np.mean(mean_spec)) + eps
        spectral_flatness = min(1.0, max(0.0, log_geom / arith_mean))

        # ── Transient density via HFC onset detection ────────────────────────
        # High-frequency content (HFC) function: sum of bin_index * magnitude
        bin_idx = np.arange(specs.shape[1], dtype=np.float64)
        hfc = specs @ bin_idx                           # (n_frames,)
        flux = np.diff(hfc, prepend=hfc[:1])
        flux = np.maximum(flux, 0.0)                   # positive flux only

        if len(flux) > 2:
            threshold = np.mean(flux) + 2.0 * np.std(flux)
            n_onsets = int(np.sum(flux > threshold))
            duration_s = n / max(self._sr, 1)
            transient_density = min(1.0, (n_onsets / max(duration_s, 1e-3)) / self._trans_norm)
        else:
            transient_density = 0.0

        return StemFeatures(
            rms=rms,
            spectral_centroid_hz=spectral_centroid_hz,
            lf_ratio=lf_ratio,
            hf_ratio=hf_ratio,
            stereo_width=stereo_width,
            transient_density=transient_density,
            spectral_flatness=spectral_flatness,
        )
