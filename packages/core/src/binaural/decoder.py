"""Loads and applies order-3 ambisonic-to-binaural decode filter sets.

A decode filter set is 16 ACN channels × {L, R} FIR filters (32 filters
total), stored as four 8-channel WAV files (``<name>_01-08ch.wav`` ...
``_25-32ch.wav``) so the same files can be fetched and decoded natively in a
browser, which caps multichannel WAV decode at 8 channels. Channel order
within the concatenated 32 channels is ``[ACN0_L, ACN0_R, ACN1_L, ACN1_R,
..., ACN15_L, ACN15_R]``. See
``docs/standards/spatial_audio_engine.md`` §4 for the full contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
import upmixer_dsp
from scipy.signal import resample_poly

from upmixer.binaural.ambisonics import N_ACN_CHANNELS

HRIR_DIR = Path(__file__).parent / "hrir"

_SPLIT_SUFFIXES = ("01-08ch", "09-16ch", "17-24ch", "25-32ch")


@dataclass(frozen=True)
class DecodeFilterSet:
    """FIR taps for the 16 ACN channels, per ear. Shape (16, 2, n_taps)."""

    name: str
    sample_rate: int
    taps: np.ndarray


def _load_raw(name: str) -> tuple[np.ndarray, int]:
    channels: list[np.ndarray] = []
    sample_rate: int | None = None
    for suffix in _SPLIT_SUFFIXES:
        path = HRIR_DIR / f"{name}_{suffix}.wav"
        if not path.exists():
            raise FileNotFoundError(f"Missing decode filter part: {path}")
        audio, sr = sf.read(str(path), dtype="float64", always_2d=True)
        if sample_rate is None:
            sample_rate = sr
        elif sr != sample_rate:
            raise ValueError(f"Decode filter set '{name}' has mismatched sample rates")
        channels.append(audio)
    stacked = np.concatenate(channels, axis=1)
    if stacked.shape[1] != 2 * N_ACN_CHANNELS:
        raise ValueError(
            f"Decode filter set '{name}' has {stacked.shape[1]} channels, "
            f"expected {2 * N_ACN_CHANNELS}"
        )
    assert sample_rate is not None
    return stacked, sample_rate


@lru_cache(maxsize=16)
def load_decode_filter_set(name: str, sample_rate: int) -> DecodeFilterSet:
    """Load a decode filter set, resampling to *sample_rate* if needed."""
    raw, file_sr = _load_raw(name)
    if file_sr != sample_rate:
        g = np.gcd(sample_rate, file_sr)
        up, down = sample_rate // g, file_sr // g
        raw = resample_poly(raw, up, down, axis=0)
    n_taps = raw.shape[0]
    taps = np.zeros((N_ACN_CHANNELS, 2, n_taps), dtype=np.float64)
    for acn in range(N_ACN_CHANNELS):
        taps[acn, 0] = raw[:, 2 * acn]
        taps[acn, 1] = raw[:, 2 * acn + 1]
    return DecodeFilterSet(name=name, sample_rate=sample_rate, taps=taps)


def decode_to_binaural(hoa: np.ndarray, filter_set: DecodeFilterSet) -> tuple[np.ndarray, np.ndarray]:
    """Convolve a 16-channel HOA bus (16, n_samples) to stereo (L, R)."""
    if hoa.shape[0] != N_ACN_CHANNELS:
        raise ValueError(f"Expected {N_ACN_CHANNELS} HOA channels, got {hoa.shape[0]}")
    return upmixer_dsp.decode_hoa_to_binaural(
        [np.ascontiguousarray(hoa[acn], dtype=np.float64) for acn in range(N_ACN_CHANNELS)],
        np.ascontiguousarray(filter_set.taps.reshape(-1), dtype=np.float64),
        filter_set.taps.shape[-1],
    )
