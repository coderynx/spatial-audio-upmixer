"""Spectral shaping (EQ) for the mastering bus.

:class:`SpectralShaper`
    Applies a predefined tonal curve to all channels except LFE via minimum-
    phase FIR filtering.

Processing
--------------------------
1. Build a linear-phase FIR with ``scipy.signal.firwin2`` from normalized
   frequency / linear-gain pairs (DC and Nyquist endpoints added automatically).
2. Convert to minimum-phase via ``scipy.signal.minimum_phase``.
3. Apply with FFT-based convolution (``fftconvolve``), then wet/dry blend.

Filter impulse responses are cached at module level so the expensive
firwin2 + minimum_phase computation runs only once per session.

Built-in profiles (SpectralShaper)
------------------------------------
spatial-transparent   Nearly flat — enables the pipeline without audible effect.
spatial-air           High-frequency air: +2.5 dB shelf above 15 kHz.
spatial-warm          Low-mid warmth: +1.5 dB around 300 Hz, subtle 3 kHz
                      softening.  Balances bright or thin mixes.
spatial-present       Presence curve: +2 dB 4 kHz region.  Adds clarity and
                      definition to dialogue / vocals in the bed.
atmos-streaming       Modeled on well-mastered Dolby Atmos Music streaming
                      content: subtle bass warmth (100 Hz), presence bump
                      (5 kHz), and air shelf (18 kHz).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import numpy as np
from scipy.signal import fftconvolve, firwin2, minimum_phase

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "eq": {
        "profile":  ("config", "mastering_eq_profile"),
        "strength": ("config", "mastering_eq_strength"),
    },
})
del _rbk

_PC_FIR_CACHE: dict[tuple[str, int, int], np.ndarray] = {}


def _breakpoints_hash(bps: list[tuple[float, float]]) -> str:
    """Return a short hex digest that identifies a breakpoints list."""
    return hashlib.md5(str(bps).encode()).hexdigest()[:16]


def _build_fir_from_breakpoints(
    breakpoints: list[tuple[float, float]],
    sample_rate: int,
    n_taps: int,
) -> np.ndarray:
    """Design a minimum-phase FIR from arbitrary (freq_hz, gain_dB) breakpoints.

    Cached by (breakpoints_hash, sample_rate, n_taps).
    """
    bps_hash = _breakpoints_hash(breakpoints)
    cache_key = (bps_hash, sample_rate, n_taps)
    if cache_key in _PC_FIR_CACHE:
        return _PC_FIR_CACHE[cache_key]

    nyquist = sample_rate / 2.0
    freqs_hz = [f for f, _ in breakpoints]
    gains_db = [g for _, g in breakpoints]

    freqs_norm = [f / nyquist for f in freqs_hz]
    gains_lin = [10.0 ** (g / 20.0) for g in gains_db]

    if freqs_norm[0] > 0.0:
        freqs_norm = [0.0] + freqs_norm
        gains_lin = [gains_lin[0]] + gains_lin

    freqs_norm = [min(f, 1.0) for f in freqs_norm]

    if freqs_norm[-1] < 1.0:
        freqs_norm.append(1.0)
        gains_lin.append(gains_lin[-1])

    seen: set[float] = set()
    pairs: list[tuple[float, float]] = []
    for f, g in zip(freqs_norm, gains_lin):
        f_r = round(f, 9)
        if f_r not in seen:
            seen.add(f_r)
            pairs.append((f, g))
    freqs_norm = [p[0] for p in pairs]
    gains_lin = [p[1] for p in pairs]

    h_lp = firwin2(n_taps, freqs_norm, gains_lin)
    h_mp = minimum_phase(h_lp)
    _PC_FIR_CACHE[cache_key] = h_mp
    return h_mp



EQ_PROFILES: dict[str, list[tuple[float, float]]] = {
    "spatial-transparent": [
        (20, 0.0), (20000, 0.0),
    ],
    "spatial-air": [
        (20, 0.0), (1000, 0.0), (5000, 0.5), (10000, 1.5), (15000, 2.5), (20000, 2.5),
    ],
    "spatial-warm": [
        (20, 0.0), (100, 1.0), (300, 1.5), (1000, 0.5),
        (3000, -0.5), (8000, 0.0), (20000, 0.0),
    ],
    "spatial-present": [
        (20, 0.0), (500, 0.0), (2000, 1.0), (4000, 2.0),
        (6000, 1.5), (10000, 1.0), (20000, 1.5),
    ],
    "atmos-streaming": [
        (20, 0.0), (60, 1.0), (100, 0.8), (500, 0.0),
        (2000, 0.5), (5000, 1.0), (12000, 1.5), (18000, 2.0), (20000, 2.0),
    ],
}

EQ_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(EQ_PROFILES.keys()))

_FIR_CACHE: dict[tuple[str, int, int], np.ndarray] = {}


def _build_fir(profile: str, sample_rate: int, n_taps: int) -> np.ndarray:
    """Build and cache a minimum-phase FIR for *profile* at *sample_rate*.

    Args:
        profile:     Name in :data:`EQ_PROFILES`.
        sample_rate: Audio sample rate in Hz.
        n_taps:      Linear-phase FIR length (odd preferred; minimum-phase
                     version will have length ``n_taps // 2 + 1``).

    Returns:
        1-D float64 minimum-phase impulse response.
    """
    cache_key = (profile, sample_rate, n_taps)
    if cache_key in _FIR_CACHE:
        return _FIR_CACHE[cache_key]

    nyquist = sample_rate / 2.0
    breakpoints = EQ_PROFILES[profile]

    freqs_hz = [f for f, _ in breakpoints]
    gains_db = [g for _, g in breakpoints]

    freqs_norm: list[float] = [f / nyquist for f in freqs_hz]
    gains_lin: list[float] = [10.0 ** (g / 20.0) for g in gains_db]

    if freqs_norm[0] > 0.0:
        freqs_norm = [0.0] + freqs_norm
        gains_lin = [gains_lin[0]] + gains_lin

    freqs_norm = [min(f, 1.0) for f in freqs_norm]

    if freqs_norm[-1] < 1.0:
        freqs_norm.append(1.0)
        gains_lin.append(gains_lin[-1])

    seen: set[float] = set()
    pairs: list[tuple[float, float]] = []
    for f, g in zip(freqs_norm, gains_lin):
        f_r = round(f, 9)
        if f_r not in seen:
            seen.add(f_r)
            pairs.append((f, g))
    freqs_norm = [p[0] for p in pairs]
    gains_lin = [p[1] for p in pairs]

    h_lp = firwin2(n_taps, freqs_norm, gains_lin)

    h_mp = minimum_phase(h_lp)

    _FIR_CACHE[cache_key] = h_mp
    return h_mp



def _apply_fir(ch: np.ndarray, ir: np.ndarray, strength: float) -> np.ndarray:
    """Apply *ir* to *ch* with wet/dry *strength* blend."""
    ch64 = ch.astype(np.float64)
    filtered = fftconvolve(ch64, ir, mode="full")[: len(ch64)]
    if strength >= 1.0:
        return filtered.astype(ch.dtype)
    blended = (1.0 - strength) * ch64 + strength * filtered
    return blended.astype(ch.dtype)



class SpectralShaper:
    """Preset profile EQ for the multichannel mastering bus.

    Applies a single predefined tonal curve to all channels except LFE via
    minimum-phase FIR filtering.  A wet/dry ``strength`` parameter controls the
    blend from full bypass (0.0) to full effect (1.0).

    For reference-based matching use :class:`~upmixer.mastering.match_reference.ReferenceMatchProcessor`.

    Args:
        profile:     EQ profile name.  See :data:`EQ_PROFILE_NAMES`.
        strength:    Wet/dry blend [0.0 = bypass … 1.0 = full].
        sample_rate: Audio sample rate in Hz.
        n_taps:      FIR tap count before minimum-phase conversion (default 1023).

    Raises:
        KeyError: if *profile* is not in :data:`EQ_PROFILES`.
    """

    def __init__(
        self,
        profile: str,
        strength: float,
        sample_rate: int,
        n_taps: int = 1023,
    ) -> None:
        if profile not in EQ_PROFILES:
            raise KeyError(
                f"Unknown EQ profile '{profile}'. "
                f"Valid choices: {EQ_PROFILE_NAMES}"
            )
        self._profile = profile
        self._strength = float(np.clip(strength, 0.0, 1.0))
        self._sr = sample_rate
        self._n_taps = n_taps
        self._ir: np.ndarray | None = None

    def _get_ir(self) -> np.ndarray:
        if self._ir is None:
            self._ir = _build_fir(self._profile, self._sr, self._n_taps)
        return self._ir

    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
    ) -> dict[str, np.ndarray]:
        """Apply spectral shaping to all channels except *lfe_key*.

        Args:
            channels: Dict channel_name → 1-D float array.
            lfe_key:  Channel name to bypass (default ``"LFE"``).

        Returns:
            New channel dict with the same shape/dtype.  LFE is unchanged.
        """
        if self._strength == 0.0:
            return channels
        ir = self._get_ir()
        _log.info(
            "  EQ shaping: profile=%s  strength=%.2f  IR=%d taps",
            self._profile, self._strength, len(ir),
        )
        out: dict[str, np.ndarray] = {}
        for name, ch in channels.items():
            if name == lfe_key:
                out[name] = ch
            else:
                out[name] = _apply_fir(ch, ir, self._strength)
        return out
