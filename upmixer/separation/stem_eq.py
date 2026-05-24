"""Per-stem minimum-phase FIR EQ for the stem-separation mixing pipeline.

Applies an independent tonal EQ curve to each stem *before* spatial routing.
Analogous to iZotope Ozone 12 stem-level EQ, but operating on already-
separated stems rather than post-fader inserts.

Each stem addressed by its canonical name (zone suffix stripped).  Stems
without an entry in the ``profiles`` dict are passed through unmodified.

Processing
----------
Same algorithm as :mod:`upmixer.mastering_eq`:

1. Look up the profile's (freq_hz, gain_dB) breakpoints.
2. Design a symmetric FIR via ``scipy.signal.firwin2``.
3. Convert to minimum-phase via ``scipy.signal.minimum_phase``.
4. Apply to both channels of the ``(n_samples, 2)`` stem array via
   ``scipy.signal.fftconvolve`` with ``mode="full"``, truncated to the
   original length.

Filter impulse responses are cached at module level by
``(profile, sample_rate, n_taps)`` so the expensive design step runs only
once per session even when the same profile is applied to multiple stems.

Built-in profiles
-----------------
vocal-presence   Mid-high presence: +2 dB at 4 kHz, HF shimmer.
vocal-warmth     Low-mid warmth: +1.5 dB at 200 Hz.
bass-warmth      Sub/mid-bass boost: +1.5 dB at 50 Hz, +1 dB at 100 Hz.
bass-cut         Reduce muddiness: cut below 120 Hz.
drums-punch      Kick punch (+1.5 dB at 80 Hz) + snap (+1.5 dB at 5 kHz).
other-air        High-frequency air: +1.5 dB at 14 kHz, +2 dB at 20 kHz.
flat             Identity (0 dB at all frequencies).
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.signal import fftconvolve, firwin2, minimum_phase

_log = logging.getLogger("upmixer")


STEM_EQ_PROFILES: dict[str, list[tuple[float, float]]] = {
    "vocal-presence": [
        (20, 0.0), (800, 0.0), (2000, 0.5), (4000, 2.0),
        (6000, 1.5), (10000, 1.0), (20000, 1.5),
    ],
    "vocal-warmth": [
        (20, 0.0), (200, 1.5), (500, 0.8), (2000, 0.0),
        (4000, -0.5), (8000, 0.0), (20000, 0.0),
    ],
    "bass-warmth": [
        (20, 0.0), (50, 1.5), (100, 1.0), (200, 0.5),
        (500, 0.0), (20000, 0.0),
    ],
    "bass-cut": [
        (20, -2.0), (60, -1.0), (120, 0.0), (400, 0.0), (20000, 0.0),
    ],
    "drums-punch": [
        (20, 0.0), (80, 1.5), (200, 0.0), (3000, 0.0),
        (5000, 1.5), (8000, 1.0), (20000, 0.0),
    ],
    "other-air": [
        (20, 0.0), (1000, 0.0), (8000, 0.5), (14000, 1.5), (20000, 2.0),
    ],
    "flat": [
        (20, 0.0), (20000, 0.0),
    ],
}

STEM_EQ_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(STEM_EQ_PROFILES.keys()))

_FIR_CACHE: dict[tuple[str, int, int], np.ndarray] = {}


def _build_fir(profile: str, sample_rate: int, n_taps: int) -> np.ndarray:
    """Build and cache a minimum-phase FIR for *profile* at *sample_rate*.

    Returns 1-D float64 minimum-phase impulse response of length
    ``n_taps // 2 + 1``.
    """
    cache_key = (profile, sample_rate, n_taps)
    if cache_key in _FIR_CACHE:
        return _FIR_CACHE[cache_key]

    nyquist = sample_rate / 2.0
    breakpoints = STEM_EQ_PROFILES[profile]

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

    _FIR_CACHE[cache_key] = h_mp
    return h_mp



class StemEQ:
    """Apply per-stem minimum-phase FIR EQ before spatial routing.

    Args:
        profiles:    Dict stem_name → profile_name.  Zone suffixes in the
                     stem dict (``"Vocals@front"``) are stripped before
                     lookup.  Stems without an entry are left unchanged.
        sample_rate: Audio sample rate in Hz.
        n_taps:      Symmetric FIR tap count before minimum-phase conversion
                     (default 511 — shorter than mastering EQ for lower
                     per-stem latency).

    Raises:
        KeyError: if a profile name is not in :data:`STEM_EQ_PROFILES`.
    """

    def __init__(
        self,
        profiles: dict[str, str],
        sample_rate: int,
        n_taps: int = 511,
    ) -> None:
        for stem_name, profile in profiles.items():
            if profile not in STEM_EQ_PROFILES:
                raise KeyError(
                    f"Unknown stem EQ profile '{profile}' for stem '{stem_name}'. "
                    f"Valid choices: {STEM_EQ_PROFILE_NAMES}"
                )
        self._profiles = profiles
        self._sr = sample_rate
        self._n_taps = n_taps

    @staticmethod
    def _canonical(key: str) -> str:
        return key.split("@")[0]

    def process(
        self,
        all_stems: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        """Apply EQ to addressed stems; pass others through unmodified.

        Args:
            all_stems: Dict stem_key → ``(n_samples, 2)`` float64 array.

        Returns:
            New dict.  Unaddressed stems share the original array objects.
        """
        out: dict[str, np.ndarray] = {}
        for key, audio in all_stems.items():
            canonical = self._canonical(key)
            profile = self._profiles.get(canonical)
            if profile is None:
                out[key] = audio
                continue

            ir = _build_fir(profile, self._sr, self._n_taps)
            arr = audio.astype(np.float64)

            if arr.ndim == 1:
                out[key] = fftconvolve(arr, ir, mode="full")[: len(arr)]
            else:
                n = arr.shape[0]
                filtered = np.empty_like(arr)
                for ch in range(arr.shape[1]):
                    filtered[:, ch] = fftconvolve(arr[:, ch], ir, mode="full")[:n]
                out[key] = filtered

            _log.debug("  StemEQ: %s  profile=%s  IR=%d taps", key, profile, len(ir))

        return out
