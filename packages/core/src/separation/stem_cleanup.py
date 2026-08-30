"""Fixed DSP cleanup for the primary vocal/instrumental separation pair."""
from __future__ import annotations

import numpy as np

_BLOCK_FRAMES = 65_536
_RELATIVE_ENERGY_FLOOR = 1e-8
_RELATIVE_LEAKAGE_FLOOR = 0.05
_COHERENCE_FLOOR = 0.8
_DOMINANCE_RATIO = 4.0
_TRANSFER_CAP = 0.25


def _as_stereo(audio: np.ndarray, name: str) -> np.ndarray:
    value = np.asarray(audio)
    if value.ndim == 1:
        value = value[:, None]
    if value.ndim != 2 or value.shape[1] not in (1, 2):
        raise ValueError(f"{name} must have shape (frames, 1 or 2)")
    if value.shape[1] == 1:
        value = np.repeat(value, 2, axis=1)
    return np.asarray(value, dtype=np.float64)


def apply_stem_cleanup(
    parent: np.ndarray,
    vocals: np.ndarray,
    instrumental: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Clean one ordered two-child split while preserving its parent sum."""
    parent_stereo = _as_stereo(parent, "parent")
    vocal_stereo = _as_stereo(vocals, "vocals")
    instrumental_stereo = _as_stereo(instrumental, "instrumental")
    if not (
        parent_stereo.shape == vocal_stereo.shape == instrumental_stereo.shape
    ):
        raise ValueError("parent and child estimates must have matching shapes")
    if not len(parent_stereo):
        dtype = np.result_type(vocals, instrumental)
        return vocal_stereo.astype(dtype), instrumental_stereo.astype(dtype)

    from upmixer_dsp import StemCleanup

    processor = StemCleanup(
        sample_rate,
        _RELATIVE_ENERGY_FLOOR,
        _RELATIVE_LEAKAGE_FLOOR,
        _COHERENCE_FLOOR,
        _DOMINANCE_RATIO,
        _TRANSFER_CAP,
    )
    vocal_blocks = []
    instrumental_blocks = []
    for start in range(0, len(parent_stereo), _BLOCK_FRAMES):
        stop = min(start + _BLOCK_FRAMES, len(parent_stereo))
        vocal_block, instrumental_block = processor.process(
            parent_stereo[start:stop],
            vocal_stereo[start:stop],
            instrumental_stereo[start:stop],
        )
        vocal_blocks.append(vocal_block)
        instrumental_blocks.append(instrumental_block)
    vocal_tail, instrumental_tail = processor.flush()
    vocal_blocks.append(vocal_tail)
    instrumental_blocks.append(instrumental_tail)

    latency = processor.latency_samples
    length = len(parent_stereo)
    cleaned_vocals = np.concatenate(vocal_blocks)[latency:latency + length]
    cleaned_instrumental = np.concatenate(instrumental_blocks)[latency:latency + length]
    dtype = np.result_type(vocals, instrumental)
    return (
        cleaned_vocals.astype(dtype, copy=False),
        cleaned_instrumental.astype(dtype, copy=False),
    )
