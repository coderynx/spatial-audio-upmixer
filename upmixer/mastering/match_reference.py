"""Spectral + RMS reference matching for the mastering bus (step 0).

Runs before SpectralShaper to imprint the overall "feel" of
a reference track onto the target mix.  Two independent, individually-toggleable
stages:

spectral matching
    Per-channel ratio EQ: Welch PSD of reference proxy / target → smoothed
    correction curve → clamped → minimum-phase FIR → wet/dry blend.

RMS matching
    Global scalar: mean RMS of reference bed channels → mean RMS of target bed
    channels → clamped gain applied uniformly to ALL channels (including LFE)
    to preserve inter-channel balance.

LFE handling
------------
Unlike :class:`~upmixer.mastering.eq.SpectralShaper`, LFE is NOT bypassed.
The proxy table already provides sensible mappings for LFE:

- 1-ch ref  → channel 0 (mono)
- 2-ch ref  → ``"mid_lp"`` (lowpass of stereo mid — low-frequency biased)
- 6-ch ref  → channel 3 (actual reference LFE)
- 8-ch ref  → channel 3 (actual reference LFE)

Bypassing LFE would create a spectral imbalance at the bed/LFE crossover.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import butter, sosfilt, welch

from .eq import _apply_fir, _build_fir_from_breakpoints

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "match_reference": {
        "path":     ("config", "mastering_match_ref_path"),
        "strength": ("config", "mastering_match_ref_strength"),
        "spectrum": ("config", "mastering_match_ref_spectrum"),
        "rms":      ("config", "mastering_match_ref_rms"),
        "max_db":   ("config", "mastering_match_ref_max_db"),
    },
})
del _rbk

_CHANNEL_PROXIES: dict[int, dict[str, Any]] = {
    1: {
        "FL": 0, "FR": 0, "C": 0, "LFE": 0,
        "SL": 0, "SR": 0, "BL": 0, "BR": 0,
        "TFL": 0, "TFR": 0, "TBL": 0, "TBR": 0,
    },
    2: {
        "FL": 0, "FR": 1, "C": "mid", "LFE": "mid_lp",
        "SL": 0, "SR": 1,
        "BL": 0, "BR": 1,
        "TFL": 0, "TFR": 1,
        "TBL": "mid", "TBR": "mid",
    },
    6: {
        "FL": 0, "FR": 1, "C": 2, "LFE": 3,
        "SL": 4, "SR": 5,
        "BL": 4, "BR": 5,
        "TFL": 0, "TFR": 1,
        "TBL": "mid46", "TBR": "mid46",
    },
    8: {
        "FL": 0, "FR": 1, "C": 2, "LFE": 3,
        "BL": 4, "BR": 5, "SL": 6, "SR": 7,
        "TFL": 0, "TFR": 1,
        "TBL": 4, "TBR": 5,
    },
}

_N_BREAKPOINTS: int = 40
_MIN_FREQ_HZ: float = 20.0
_NORM_LOW_HZ: float = 80.0
_NORM_HIGH_HZ: float = 8000.0
_BASS_CLAMP_HZ: float = 120.0
_BASS_CLAMP_DB: float = 2.0
_RMS_CLAMP_DB: float = 6.0
_EPS: float = 1e-20
_SMOOTH_SIGMA_OCT: float = 0.25
_N_FFT_DEFAULT: int = 8192


def _gaussian_smooth_log(
    log_freqs: np.ndarray,
    values: np.ndarray,
    sigma_oct: float,
) -> np.ndarray:
    n = len(log_freqs)
    smoothed = np.empty(n)
    df = log_freqs[1] - log_freqs[0] if n > 1 else 1.0
    sigma_bins = sigma_oct / max(df, 1e-10)

    half_w = int(3 * sigma_bins) + 1
    if half_w < 1:
        return values.copy()

    kernel_idx = np.arange(-half_w, half_w + 1, dtype=float)
    kernel = np.exp(-0.5 * (kernel_idx / sigma_bins) ** 2)
    kernel /= kernel.sum()

    padded = np.pad(values, half_w, mode="reflect")
    for i in range(n):
        smoothed[i] = np.dot(padded[i: i + 2 * half_w + 1], kernel)
    return smoothed


def _resolve_proxy(ref_data: np.ndarray, proxy: Any, sr: int) -> np.ndarray:
    """Return a 1-D float64 array for a proxy specification.

    Args:
        ref_data: ``(n_samples, n_channels)`` reference array.
        proxy:    Integer channel index, ``"mid"``, ``"mid_lp"``, or ``"mid46"``.
        sr:       Sample rate (needed for the mid_lp butter filter).
    """
    if isinstance(proxy, int):
        return ref_data[:, proxy]
    if proxy == "mid":
        if ref_data.shape[1] >= 2:
            return (ref_data[:, 0] + ref_data[:, 1]) * 0.5
        return ref_data[:, 0]
    if proxy == "mid_lp":
        mid = _resolve_proxy(ref_data, "mid", sr)
        nyq = sr / 2.0
        sos = butter(2, 80.0 / nyq, btype="low", output="sos")
        return sosfilt(sos, mid)
    if proxy == "mid46":
        n_ch = ref_data.shape[1]
        if n_ch > 5:
            return (ref_data[:, 4] + ref_data[:, min(5, n_ch - 1)]) * 0.5
        return ref_data[:, min(4, n_ch - 1)]
    raise ValueError(f"Unknown proxy specification: {proxy!r}")


def _load_reference(path: str, target_sr: int) -> np.ndarray:
    """Load reference audio, resample to target_sr if needed.

    Returns:
        ``(n_samples, n_channels)`` float64 array (always 2-D).

    Raises:
        ImportError: if soundfile is not installed.
    """
    try:
        import soundfile as sf  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "soundfile is required for reference matching. "
            "Install it with: pip install soundfile"
        ) from exc

    ref_raw, ref_sr = sf.read(path, dtype="float64", always_2d=True)
    n_ch = ref_raw.shape[1]

    if ref_sr != target_sr:
        from scipy.signal import resample_poly

        g = math.gcd(ref_sr, target_sr)
        up, down = target_sr // g, ref_sr // g
        cols = [resample_poly(ref_raw[:, ch], up, down) for ch in range(n_ch)]
        ref_data = np.stack(cols, axis=1)
        _log.info(
            "  Match reference: resampled reference %d → %d Hz", ref_sr, target_sr
        )
    else:
        ref_data = ref_raw

    return ref_data


def _select_proxy_table(n_ref_ch: int) -> dict[str, Any]:
    """Pick the closest supported proxy table for *n_ref_ch* reference channels."""
    supported = sorted(_CHANNEL_PROXIES.keys())
    proxy_n = min(supported, key=lambda x: abs(x - n_ref_ch))
    if proxy_n != n_ref_ch:
        _log.warning(
            "Match reference: reference has %d channels; using %d-channel proxy "
            "table.  For best results use a reference with the same channel count "
            "as the upmix target.",
            n_ref_ch,
            proxy_n,
        )
    return _CHANNEL_PROXIES[proxy_n]


class ReferenceMatchProcessor:
    """Spectral + RMS reference matching — mastering step 0.

    Computes per-channel spectral correction FIRs and/or a global RMS gain
    scalar by comparing target channels against a reference audio file.

    Both stages are independently toggleable via boolean flags.

    Args:
        reference_path:      Path to reference audio (WAV, FLAC, AIFF, etc.).
        strength:            Wet/dry blend for spectral FIR correction [0.0–1.0].
                             Does NOT affect RMS correction (always applied at
                             full strength to preserve inter-channel balance).
        match_spectrum:      Enable per-channel spectral envelope matching.
        match_rms:           Enable global RMS level matching.
        max_correction_db:   Maximum spectral correction magnitude (dB).
                             Sub-bass below 120 Hz is additionally clamped to ±2 dB.
        sample_rate:         Audio sample rate in Hz.
        n_fft:               Welch window length (default 8192).
        n_taps:              FIR tap count (default 1023).
    """

    def __init__(
        self,
        reference_path: str,
        strength: float = 0.7,
        match_spectrum: bool = True,
        match_rms: bool = True,
        max_correction_db: float = 12.0,
        sample_rate: int = 48000,
        n_fft: int = _N_FFT_DEFAULT,
        n_taps: int = 1023,
    ) -> None:
        self._ref_path = reference_path
        self._strength = float(np.clip(strength, 0.0, 1.0))
        self._match_spectrum = match_spectrum
        self._match_rms = match_rms
        self._max_db = float(max_correction_db)
        self._sr = sample_rate
        self._n_fft = n_fft
        self._n_taps = n_taps

        self._ref_data: np.ndarray | None = None
        self._proxy_table: dict[str, Any] | None = None


    def _load_if_needed(self) -> None:
        if self._ref_data is not None:
            return
        self._ref_data = _load_reference(self._ref_path, self._sr)
        n_ch = self._ref_data.shape[1]
        self._proxy_table = _select_proxy_table(n_ch)
        _log.info(
            "  Match reference: loaded '%s' (%d ch, %d samples)",
            Path(self._ref_path).name,
            n_ch,
            self._ref_data.shape[0],
        )

    def _compute_spectral_breakpoints(
        self,
        ref_ch: np.ndarray,
        tgt_ch: np.ndarray,
    ) -> list[tuple[float, float]]:
        """Compute spectral correction breakpoints (freq_hz, gain_dB).

        Algorithm:
        1. Welch PSD of ref and target (DC bin skipped).
        2. correction_db = 10 * log10(ref / tgt).
        3. 0.25-oct Gaussian smooth.
        4. Subtract 80–8 kHz mean (separates level from tonal shape).
        5. Clamp globally to ±max_db; sub-bass to ±2 dB.
        6. Downsample to 40 log-spaced breakpoints.
        """
        nyquist = self._sr / 2.0

        freqs, ref_psd = welch(ref_ch, fs=self._sr, nperseg=self._n_fft, window="hann")
        _, tgt_psd = welch(tgt_ch, fs=self._sr, nperseg=self._n_fft, window="hann")
        freqs = freqs[1:]
        ref_psd = ref_psd[1:]
        tgt_psd = tgt_psd[1:]

        correction_db = 10.0 * np.log10((ref_psd + _EPS) / (tgt_psd + _EPS))

        log_freqs = np.log2(freqs + 1e-10)
        correction_db = _gaussian_smooth_log(log_freqs, correction_db, _SMOOTH_SIGMA_OCT)

        norm_mask = (freqs >= _NORM_LOW_HZ) & (freqs <= _NORM_HIGH_HZ)
        if norm_mask.any():
            correction_db -= correction_db[norm_mask].mean()

        correction_db = np.clip(correction_db, -self._max_db, self._max_db)
        bass_mask = freqs < _BASS_CLAMP_HZ
        correction_db[bass_mask] = np.clip(
            correction_db[bass_mask], -_BASS_CLAMP_DB, _BASS_CLAMP_DB
        )

        bp_freqs = np.logspace(
            np.log10(_MIN_FREQ_HZ), np.log10(nyquist), num=_N_BREAKPOINTS
        )
        bp_freqs[-1] = nyquist
        bp_gains = np.interp(bp_freqs, freqs, correction_db)

        return [(float(f), float(g)) for f, g in zip(bp_freqs, bp_gains)]

    def _compute_rms_gain_db(
        self,
        ref_data: np.ndarray,
        proxy_table: dict[str, Any],
        channels: dict[str, np.ndarray],
        lfe_key: str,
    ) -> float:
        """Compute a global RMS gain (dB) to match reference level.

        Reference RMS is computed from bed (non-LFE) proxy channels only;
        LFE is narrow-band and its level is not comparable to bed levels.
        The resulting scalar is applied to ALL channels including LFE so
        inter-channel balance is preserved.

        Clamped to ±6 dB.
        """
        ref_rms_vals: list[float] = []
        for ch_name in channels:
            if ch_name == lfe_key:
                continue
            proxy = proxy_table.get(ch_name, "mid")
            if proxy == "mid_lp":
                continue
            ref_ch = _resolve_proxy(ref_data, proxy, self._sr)
            ref_rms_vals.append(float(np.sqrt(np.mean(ref_ch ** 2) + _EPS)))

        if not ref_rms_vals:
            return 0.0
        rms_ref = float(np.mean(ref_rms_vals))

        tgt_rms_vals = [
            float(np.sqrt(np.mean(arr ** 2) + _EPS))
            for name, arr in channels.items()
            if name != lfe_key
        ]
        if not tgt_rms_vals:
            return 0.0
        rms_tgt = float(np.mean(tgt_rms_vals))

        gain_db = 20.0 * np.log10((rms_ref + _EPS) / (rms_tgt + _EPS))
        return float(np.clip(gain_db, -_RMS_CLAMP_DB, _RMS_CLAMP_DB))


    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
    ) -> dict[str, np.ndarray]:
        """Apply spectral and/or RMS matching against the reference.

        Args:
            channels: Dict channel_name → 1-D float64 array.
            lfe_key:  LFE channel name (default ``"LFE"``).
                      Unlike other mastering processors, LFE is NOT bypassed —
                      its proxy is resolved from the reference instead.

        Returns:
            New channel dict with matching applied.
        """
        if not self._match_spectrum and not self._match_rms:
            return channels

        self._load_if_needed()
        ref_data = self._ref_data
        proxy_table = self._proxy_table

        out: dict[str, np.ndarray] = {}

        if self._match_rms:
            rms_gain_db = self._compute_rms_gain_db(
                ref_data, proxy_table, channels, lfe_key
            )
            rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)
            _log.info("  Match reference: RMS gain %+.1f dB", rms_gain_db)
            for name, ch in channels.items():
                out[name] = ch.astype(np.float64) * rms_gain_lin
        else:
            out = dict(channels)

        if self._match_spectrum and self._strength > 0.0:
            _log.info(
                "  Match reference: spectral correction  strength=%.2f  max=%.1f dB",
                self._strength,
                self._max_db,
            )
            for name in list(out.keys()):
                ch = out[name].astype(np.float64)
                proxy = proxy_table.get(name, "mid")
                ref_ch = _resolve_proxy(ref_data, proxy, self._sr)

                bps = self._compute_spectral_breakpoints(ref_ch, ch)
                ir = _build_fir_from_breakpoints(bps, self._sr, self._n_taps)
                out[name] = _apply_fir(ch, ir, self._strength)
                _log.debug("  Match reference: %s (proxy=%s) corrected", name, proxy)

        return out
