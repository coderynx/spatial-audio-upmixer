"""Loads and applies the 2x2 crosstalk-cancellation (XTC) FIR filter matrix.

An XTC filter set is 4 FIR filters (H_LL, H_LR, H_RL, H_RR) baked to a single
4-channel WAV file ``<name>.wav`` — unlike the binaural decode bank's 32
channels, 4 fits well inside the browser's native 8-channel WAV decode cap, so
no multi-file split is needed. See ``docs/standards/transaural_speakers.md``
§4 for the full contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve, resample_poly

XTC_DIR = Path(__file__).parent / "xtc"


@dataclass(frozen=True)
class XtcFilterSet:
    """FIR taps for the 2x2 crosstalk canceller. Shape (2, 2, n_taps): [out_speaker][in_ear]."""

    name: str
    sample_rate: int
    taps: np.ndarray


@lru_cache(maxsize=16)
def load_xtc_filter_set(name: str, sample_rate: int) -> XtcFilterSet:
    """Load an XTC filter set, resampling to *sample_rate* if needed."""
    path = XTC_DIR / f"{name}.wav"
    if not path.exists():
        raise FileNotFoundError(f"Missing crosstalk filter set: {path}")
    raw, file_sr = sf.read(str(path), dtype="float64", always_2d=True)
    if raw.shape[1] != 4:
        raise ValueError(
            f"Crosstalk filter set '{name}' has {raw.shape[1]} channels, expected 4"
        )
    if file_sr != sample_rate:
        g = np.gcd(sample_rate, file_sr)
        up, down = sample_rate // g, file_sr // g
        raw = resample_poly(raw, up, down, axis=0)
    n_taps = raw.shape[0]
    taps = np.zeros((2, 2, n_taps), dtype=np.float64)
    taps[0, 0] = raw[:, 0]  # H_LL: left speaker <- left ear signal
    taps[0, 1] = raw[:, 1]  # H_LR: left speaker <- right ear signal
    taps[1, 0] = raw[:, 2]  # H_RL: right speaker <- left ear signal
    taps[1, 1] = raw[:, 3]  # H_RR: right speaker <- right ear signal
    return XtcFilterSet(name=name, sample_rate=sample_rate, taps=taps)


def apply_xtc(left: np.ndarray, right: np.ndarray, filter_set: XtcFilterSet) -> tuple[np.ndarray, np.ndarray]:
    """Apply the 2x2 XTC matrix: ``speaker = H @ ear_signal``.

    ``left``/``right`` are the intended binaural ear signals (see
    :func:`upmixer.binaural.renderer.render_binaural`); the returned pair is
    what must be fed to the physical left/right speakers so that, after
    acoustic crosstalk, the intended ear signals arrive.
    """
    n_samples = left.shape[0]
    speaker_l = fftconvolve(left, filter_set.taps[0, 0]) + fftconvolve(right, filter_set.taps[0, 1])
    speaker_r = fftconvolve(left, filter_set.taps[1, 0]) + fftconvolve(right, filter_set.taps[1, 1])
    return speaker_l[:n_samples], speaker_r[:n_samples]
