"""EQ Match from reference audio — generates per-channel EQ profiles.

Analyses a reference audio file (mono through 7.1.4), computes a smoothed
spectral envelope per channel, and returns (freq_hz, gain_dB) breakpoints
suitable for :class:`~upmixer.mastering.eq.SpectralShaper`.

Analogous to iZotope RX 12 / Ozone 12 "Match EQ" but designed for
multichannel and spatial audio workflows.

Algorithm
---------
For each target channel:

1. Identify which reference channel(s) to use via ``_CHANNEL_PROXIES``.
   Special proxies: ``"mid"`` = (L+R)/2, ``"mid_lp"`` = lowpass of mid.
2. Compute Welch power spectral density (scipy.signal.welch, Hann window).
3. Convert PSD to log-magnitude: ``10 * log10(psd + ε)`` dB.
4. Apply 1/3-octave Gaussian smoothing in log-frequency space.
5. Normalise: subtract the mean dB over 80 Hz – Nyquist/2 to keep the curve
   centred at 0 dB (avoids DC-offset artefacts in the FIR).
6. Downsample to 40 log-spaced breakpoints from 20 Hz to Nyquist.

When the reference has fewer channels than the target, the proxy table maps
each target channel to the closest available reference channel or derives a
virtual channel (mid, mid_lp).  A warning is emitted recommending a matched-
format reference for best results.

EQ match strength
-----------------
:func:`scale_breakpoints` scales all gain_dB values by a strength factor
before the FIR is built.  This is semantically different from the wet/dry
``strength`` in :class:`~upmixer.mastering.eq.SpectralShaper`:

* Wet/dry blend:  ``out = (1 - s) * dry + s * filtered``  — applies the full
  FIR at fraction *s* of the signal path.  Not intuitive for broad spectral
  shapes; 0.1 still passes significant coloration.
* Gain scaling:   all deviations from 0 dB are multiplied by *strength*.
  At 0.5: a +4 dB peak becomes +2 dB; the FIR is then built from the gentler
  curve.  More predictable for music mastering.

Saving / loading
----------------
Generated profiles can be saved as YAML (``save_profile``) and reloaded
without re-running the analysis (``load_profile``).  This allows a
once-computed reference profile to be applied to multiple files.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import butter, sosfilt, welch

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "eq_match": {
        "reference": ("config", "mastering_match_ref_path"),
        "strength":  ("config", "mastering_match_ref_strength"),
        "spectrum":  ("config", "mastering_match_ref_spectrum"),
        "rms":       ("config", "mastering_match_ref_rms"),
        "max_db":    ("config", "mastering_match_ref_max_db"),
    },
})
del _rbk

# ── Channel proxy table ───────────────────────────────────────────────────────
# Maps (n_reference_channels) → {target_channel: reference_index | proxy_name}
# Special proxy names:
#   "mid"     = (ch0 + ch1) / 2  — stereo L+R average
#   "mid_lp"  = lowpass of mid  — proxy for LFE (dense sub-bass)
#   "mid46"   = mean of channels at indices 4 and 5 in the reference

_CHANNEL_PROXIES: dict[int, dict[str, Any]] = {
    # Mono reference: everything maps to the single channel
    1: {
        "FL": 0, "FR": 0, "C": 0, "LFE": 0,
        "SL": 0, "SR": 0, "BL": 0, "BR": 0,
        "TFL": 0, "TFR": 0, "TBL": 0, "TBR": 0,
    },
    # Stereo reference (L=0, R=1)
    2: {
        "FL": 0, "FR": 1, "C": "mid", "LFE": "mid_lp",
        "SL": 0, "SR": 1,
        "BL": 0, "BR": 1,
        "TFL": 0, "TFR": 1,
        "TBL": "mid", "TBR": "mid",
    },
    # 5.1 reference (FL FR C LFE SL SR → indices 0 1 2 3 4 5)
    6: {
        "FL": 0, "FR": 1, "C": 2, "LFE": 3,
        "SL": 4, "SR": 5,
        "BL": 4, "BR": 5,
        "TFL": 0, "TFR": 1,
        "TBL": "mid46", "TBR": "mid46",
    },
    # 7.1 reference (FL FR C LFE BL BR SL SR → 0 1 2 3 4 5 6 7)
    8: {
        "FL": 0, "FR": 1, "C": 2, "LFE": 3,
        "BL": 4, "BR": 5, "SL": 6, "SR": 7,
        "TFL": 0, "TFR": 1,
        "TBL": 4, "TBR": 5,
    },
}

# Number of log-spaced breakpoints returned
_N_BREAKPOINTS: int = 40
_MIN_FREQ_HZ: float = 20.0
# Normalisation band — mean dB is computed over this range (80 Hz – 8 kHz)
_NORM_LOW_HZ: float = 80.0
_NORM_HIGH_HZ: float = 8000.0

# Gain clamp below this frequency — prevents bed channels from excessive bass
# boost that conflicts with the LFE channel (approximate LFE crossover).
_LFE_XOVER_HZ: float = 120.0
_BASS_LIMIT_DB: float = 2.0

# 1/3-octave smoothing sigma (in octave units)
_SMOOTH_SIGMA_OCT: float = 0.25


def _gaussian_smooth_log(
    log_freqs: np.ndarray,
    values: np.ndarray,
    sigma_oct: float,
) -> np.ndarray:
    """Gaussian kernel smoothing on a log-frequency axis.

    Args:
        log_freqs: 1-D array of log2(frequency) values (uniform spacing ok).
        values:    1-D array of signal values at each frequency.
        sigma_oct: Gaussian σ in octaves.

    Returns:
        Smoothed values array.
    """
    n = len(log_freqs)
    smoothed = np.empty(n)
    df = log_freqs[1] - log_freqs[0] if n > 1 else 1.0
    sigma_bins = sigma_oct / max(df, 1e-10)

    # Build a Gaussian kernel of width 6σ, normalised
    half_w = int(3 * sigma_bins) + 1
    if half_w < 1:
        return values.copy()

    kernel_idx = np.arange(-half_w, half_w + 1, dtype=float)
    kernel = np.exp(-0.5 * (kernel_idx / sigma_bins) ** 2)
    kernel /= kernel.sum()

    # Convolve with edge padding (reflect)
    padded = np.pad(values, half_w, mode="reflect")
    for i in range(n):
        smoothed[i] = np.dot(padded[i: i + 2 * half_w + 1], kernel)
    return smoothed


def scale_breakpoints(
    breakpoints: dict[str, list[tuple[float, float]]],
    strength: float,
) -> dict[str, list[tuple[float, float]]]:
    """Scale all gain_dB values by *strength* before FIR design.

    This is the recommended way to control EQ match intensity: a strength of
    0.5 halves every deviation from 0 dB before the FIR is built, producing a
    gentler curve.  Semantically different from wet/dry blending (which applies
    a fixed FIR at a fractional signal level).

    Args:
        breakpoints: Per-channel breakpoints dict as returned by
                     :meth:`EQMatcher.analyze`.
        strength:    Scale factor in [0.0, 1.0].  0.0 → flat (no EQ);
                     1.0 → full reference curve.

    Returns:
        New dict with all gain_dB values multiplied by *strength*.
    """
    s = float(np.clip(strength, 0.0, 1.0))
    return {
        ch: [(f, g * s) for f, g in bps]
        for ch, bps in breakpoints.items()
    }


class EQMatcher:
    """Compute per-channel EQ profiles from a reference audio file.

    Args:
        sample_rate: Target audio sample rate in Hz.
        n_fft:       Welch window length (default 8192).
    """

    def __init__(self, sample_rate: int, n_fft: int = 8192) -> None:
        self._sr = sample_rate
        self._n_fft = n_fft

    # ── helpers ───────────────────────────────────────────────────────────────

    def _psd_to_breakpoints(
        self,
        ref_ch: np.ndarray,
    ) -> list[tuple[float, float]]:
        """Compute 1/3-oct smoothed, normalised spectral envelope.

        Returns a list of (freq_hz, gain_dB) breakpoints.
        """
        freqs, psd = welch(ref_ch, fs=self._sr, nperseg=self._n_fft, window="hann")

        # Skip DC bin
        freqs = freqs[1:]
        psd = psd[1:]

        mag_db = 10.0 * np.log10(psd + 1e-20)

        # Log-frequency axis (octaves)
        log_freqs = np.log2(freqs + 1e-10)

        # 1/3-oct Gaussian smooth
        smoothed = _gaussian_smooth_log(log_freqs, mag_db, _SMOOTH_SIGMA_OCT)

        # Normalise to mid-band mean (80 Hz – 8 kHz)
        norm_mask = (freqs >= _NORM_LOW_HZ) & (freqs <= _NORM_HIGH_HZ)
        if norm_mask.any():
            smoothed -= smoothed[norm_mask].mean()

        # Downsample to N log-spaced breakpoints in [20 Hz, Nyquist]
        nyquist = self._sr / 2.0
        bp_freqs = np.logspace(
            np.log10(_MIN_FREQ_HZ), np.log10(nyquist),
            num=_N_BREAKPOINTS,
        )

        # Interpolate smoothed curve at breakpoint frequencies
        bp_gains = np.interp(bp_freqs, freqs, smoothed)

        # Clamp sub-bass gains — bed channels should not carry the deep bass
        # that the LFE channel handles; prevents boominess from bass-heavy refs.
        bass_mask = bp_freqs < _LFE_XOVER_HZ
        bp_gains[bass_mask] = np.clip(bp_gains[bass_mask], -_BASS_LIMIT_DB, _BASS_LIMIT_DB)

        return [(float(f), float(g)) for f, g in zip(bp_freqs, bp_gains)]

    def _resolve_proxy(
        self,
        ref_data: np.ndarray,
        proxy: Any,
    ) -> np.ndarray:
        """Return a 1-D array for a proxy specification.

        Args:
            ref_data: ``(n_samples, n_channels)`` reference array.
            proxy:    Integer channel index, ``"mid"``, ``"mid_lp"``, or
                      ``"mid46"`` (mean of channels 4 and 5).
        """
        if isinstance(proxy, int):
            return ref_data[:, proxy]

        if proxy == "mid":
            if ref_data.shape[1] >= 2:
                return (ref_data[:, 0] + ref_data[:, 1]) * 0.5
            return ref_data[:, 0]

        if proxy == "mid_lp":
            mid = self._resolve_proxy(ref_data, "mid")
            nyq = self._sr / 2.0
            sos = butter(2, 80.0 / nyq, btype="low", output="sos")
            return sosfilt(sos, mid)

        if proxy == "mid46":
            n_ch = ref_data.shape[1]
            if n_ch > 5:
                return (ref_data[:, 4] + ref_data[:, min(5, n_ch - 1)]) * 0.5
            return ref_data[:, min(4, n_ch - 1)]

        raise ValueError(f"Unknown proxy specification: {proxy!r}")

    # ── public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        reference_path: str,
        target_channels: list[str],
    ) -> dict[str, list[tuple[float, float]]]:
        """Analyse a reference file and return per-channel EQ breakpoints.

        Args:
            reference_path: Path to the reference audio file (any format
                            readable by soundfile: WAV, FLAC, AIFF, etc.).
            target_channels: List of channel names to generate profiles for
                             (e.g. ``["FL","FR","C","LFE","SL","SR"]``).

        Returns:
            Dict channel_name → list of (freq_hz, gain_dB) breakpoints.

        Raises:
            ImportError: if soundfile is not available.
        """
        try:
            import soundfile as sf  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "soundfile is required for EQ matching. "
                "Install it with: pip install soundfile"
            ) from exc

        ref_raw, ref_sr = sf.read(reference_path, dtype="float64", always_2d=True)
        n_ref_ch = ref_raw.shape[1]

        # Resample reference to match target sample rate if needed
        if ref_sr != self._sr:
            from scipy.signal import resample_poly
            import math
            g = math.gcd(ref_sr, self._sr)
            up, down = self._sr // g, ref_sr // g
            resampled = np.empty(
                (resample_poly(ref_raw[:, 0], up, down).shape[0], n_ref_ch),
                dtype=np.float64,
            )
            for ch in range(n_ref_ch):
                resampled[:, ch] = resample_poly(ref_raw[:, ch], up, down)
            ref_data = resampled
            _log.info(
                "  EQMatcher: resampled reference %d → %d Hz",
                ref_sr, self._sr,
            )
        else:
            ref_data = ref_raw

        # Choose proxy table by closest supported reference channel count
        supported = sorted(_CHANNEL_PROXIES.keys())
        proxy_n = min(supported, key=lambda x: abs(x - n_ref_ch))
        if proxy_n != n_ref_ch:
            _log.warning(
                "EQMatcher: reference has %d channels; using %d-channel proxy "
                "table. For best results use a reference with the same channel "
                "count as the upmix target.",
                n_ref_ch, proxy_n,
            )
        proxy_table = _CHANNEL_PROXIES[proxy_n]

        result: dict[str, list[tuple[float, float]]] = {}
        for ch_name in target_channels:
            proxy = proxy_table.get(ch_name, "mid")
            ref_ch = self._resolve_proxy(ref_data, proxy)
            bps = self._psd_to_breakpoints(ref_ch)
            result[ch_name] = bps
            _log.debug(
                "  EQMatcher: %s (proxy=%s) — %d breakpoints", ch_name, proxy, len(bps)
            )

        _log.info(
            "  EQMatcher: %d channels analysed from '%s'",
            len(result), Path(reference_path).name,
        )
        return result

    def save_profile(
        self,
        breakpoints: dict[str, list[tuple[float, float]]],
        path: str,
    ) -> None:
        """Save generated per-channel breakpoints to a YAML or JSON file.

        Args:
            breakpoints: Output of :meth:`analyze`.
            path:        Destination path (``*.yaml``, ``*.yml``, or ``*.json``).
        """
        p = Path(path)
        # Convert tuples to lists for serialisation
        serialisable = {
            ch: [[f, g] for f, g in bps]
            for ch, bps in breakpoints.items()
        }
        if p.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required to save YAML profiles. "
                    "Install with: pip install pyyaml"
                ) from exc
            p.write_text(yaml.dump(serialisable, default_flow_style=False), encoding="utf-8")
        else:
            p.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")
        _log.info("  EQMatcher: profile saved to '%s'", path)

    @staticmethod
    def load_profile(
        path: str,
    ) -> dict[str, list[tuple[float, float]]]:
        """Load a previously saved per-channel EQ profile.

        Args:
            path: Path to a ``.yaml``, ``.yml``, or ``.json`` profile file.

        Returns:
            Dict channel_name → list of (freq_hz, gain_dB) tuples.
        """
        p = Path(path)
        if p.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required to load YAML profiles. "
                    "Install with: pip install pyyaml"
                ) from exc
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        else:
            raw = json.loads(p.read_text(encoding="utf-8"))

        return {
            ch: [(float(f), float(g)) for f, g in bps]
            for ch, bps in (raw or {}).items()
        }
