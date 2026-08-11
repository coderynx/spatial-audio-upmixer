"""``ReferenceMatchProcessor`` -- reference matching, mastering step 0.

Ties the gated spectral-matching algorithm (:mod:`.curve`, :mod:`.spectrum`)
to a per-project reference audio file. Two independent, individually-
toggleable stages:

spectral matching
    One BS.1770-weighted, gated power-spectrum ratio between reference and
    target (:func:`.curve.compute_reference_curve`), smoothed at 1/3 octave,
    applied as a *single* minimum-phase FIR to every non-LFE channel.
    Applying one shared curve — rather than a curve per channel — preserves
    inter-channel phase relationships, which ITU-R BS.775 downmix folding and
    transaural crosstalk cancellation both depend on; independently-matched
    per-channel curves would desynchronize exactly the correlation those two
    delivery paths rely on.

level matching
    Global scalar: reference's BS.1770 integrated loudness minus the
    target's, clamped, applied uniformly to ALL channels (including LFE) so
    inter-channel balance is preserved.

LFE handling
------------
LFE is excluded from both stages' *analysis* (BS.1770 already excludes it
from loudness measurement; the spectral stage does the same) and from the
spectral *correction* — a channel band-limited to 120 Hz by BS.775-4 has no
meaningful ratio against a full-range reference above that frequency. LFE
still receives the level-matching scalar, so bed/LFE balance moves
consistently with the rest of the mix.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from upmixer.formats import OutputFormat
from upmixer.loudness import measure_integrated_loudness

from ..eq import _apply_fir
from .curve import build_curve_fir, compute_reference_curve
from .spectrum import reference_integrated_loudness

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

_RMS_CLAMP_DB: float = 6.0
_LOUDNESS_SILENCE_LKFS: float = -69.9
_N_FFT_DEFAULT: int = 8192
_N_TAPS_DEFAULT: int = 1023


def _load_reference(path: str, target_sr: int) -> np.ndarray:
    """Load reference audio, resample to ``target_sr`` if needed.

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
            "  Match reference: resampled reference %d -> %d Hz", ref_sr, target_sr
        )
    else:
        ref_data = ref_raw

    return ref_data


class ReferenceMatchProcessor:
    """Spectral + level reference matching — mastering step 0.

    Computes one correction curve and/or a global level gain by comparing a
    reference audio file against target channels.

    Args:
        reference_path:     Path to reference audio (WAV, FLAC, AIFF, etc.).
        output_fmt:          Target output format — selects BS.1770 channel
                             weights for the level-matching stage.
        strength:            dB-domain scale for the spectral curve [0.0-1.0].
                             Does NOT affect level matching (always applied
                             at full strength to preserve inter-channel
                             balance).
        match_spectrum:      Enable the shared spectral correction curve.
        match_rms:           Enable global level (BS.1770 loudness) matching.
        max_correction_db:   Maximum spectral correction magnitude (dB), soft
                             knee. Sub-bass below 120 Hz is additionally
                             clamped to +/-2 dB.
        sample_rate:         Audio sample rate in Hz.
        n_fft:               STFT frame length for spectral analysis.
        n_taps:              FIR tap count (default 1023).
    """

    def __init__(
        self,
        reference_path: str,
        output_fmt: OutputFormat,
        strength: float = 0.7,
        match_spectrum: bool = True,
        match_rms: bool = True,
        max_correction_db: float = 6.0,
        sample_rate: int = 48000,
        n_fft: int = _N_FFT_DEFAULT,
        n_taps: int = _N_TAPS_DEFAULT,
    ) -> None:
        self._ref_path = reference_path
        self._output_fmt = output_fmt
        self._strength = float(np.clip(strength, 0.0, 1.0))
        self._match_spectrum = match_spectrum
        self._match_rms = match_rms
        self._max_db = float(max_correction_db)
        self._sr = sample_rate
        self._n_fft = n_fft
        self._n_taps = n_taps

        self._ref_data: np.ndarray | None = None

    def _load_if_needed(self) -> None:
        if self._ref_data is not None:
            return
        self._ref_data = _load_reference(self._ref_path, self._sr)
        _log.info(
            "  Match reference: loaded '%s' (%d ch, %d samples)",
            Path(self._ref_path).name,
            self._ref_data.shape[1],
            self._ref_data.shape[0],
        )

    def _compute_level_gain_db(self, target_channels: dict[str, np.ndarray]) -> float:
        """BS.1770 integrated-loudness delta, reference minus target, clamped.

        Loudness units are already logarithmic, so the gain is a direct
        subtraction — no RMS-ratio-to-dB conversion needed.
        """
        ref_lkfs = reference_integrated_loudness(self._ref_data, self._sr)
        tgt_lkfs = measure_integrated_loudness(target_channels, self._sr, self._output_fmt)
        if ref_lkfs <= _LOUDNESS_SILENCE_LKFS or tgt_lkfs <= _LOUDNESS_SILENCE_LKFS:
            return 0.0
        return float(np.clip(ref_lkfs - tgt_lkfs, -_RMS_CLAMP_DB, _RMS_CLAMP_DB))

    def compute_curve(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
    ) -> tuple[list[tuple[float, float]], float]:
        """Compute the strength/max_db-independent correction curve plus the
        level-matching gain, without applying either.

        Lets a caller (e.g. the web preview's server-side precompute) persist
        the curve once and later realize it at any ``(strength, max_db)``
        via :func:`.curve.build_curve_fir`, instead of re-running the
        spectral analysis on every knob change.

        Returns:
            ``(curve, rms_gain_db)``. ``curve`` is empty when spectral
            matching is disabled.
        """
        self._load_if_needed()
        rms_gain_db = self._compute_level_gain_db(channels) if self._match_rms else 0.0
        rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)

        curve: list[tuple[float, float]] = []
        if self._match_spectrum:
            scaled = {
                name: ch.astype(np.float64) * rms_gain_lin
                for name, ch in channels.items()
                if name != lfe_key
            }
            curve = compute_reference_curve(scaled, self._ref_data, self._sr, self._n_fft, lfe_key)
        return curve, rms_gain_db

    def process(self, channels: dict[str, np.ndarray], lfe_key: str = "LFE") -> dict[str, np.ndarray]:
        """Apply spectral and/or level matching against the reference.

        Args:
            channels: Dict channel_name -> 1-D float64 array.
            lfe_key:  LFE channel name (default ``"LFE"``). Receives the
                      level-matching gain but not the spectral correction.

        Returns:
            New channel dict with matching applied.
        """
        if not self._match_spectrum and not self._match_rms:
            return channels

        curve, rms_gain_db = self.compute_curve(channels, lfe_key)
        rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)

        out: dict[str, np.ndarray] = {}
        if self._match_rms:
            _log.info("  Match reference: level gain %+.1f dB", rms_gain_db)
            for name, ch in channels.items():
                out[name] = ch.astype(np.float64) * rms_gain_lin
        else:
            out = dict(channels)

        if self._match_spectrum and self._strength > 0.0 and curve:
            _log.info(
                "  Match reference: spectral correction  strength=%.2f  max=%.1f dB",
                self._strength, self._max_db,
            )
            fir = build_curve_fir(curve, self._sr, self._n_taps, self._strength, self._max_db)
            for name in list(out.keys()):
                if name == lfe_key:
                    continue
                out[name] = _apply_fir(out[name].astype(np.float64), fir, 1.0)

        return out
