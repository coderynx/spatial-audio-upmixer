"""Multichannel-to-zone extraction and resampling for stem separation."""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import resample_poly

from upmixer.formats import ChannelLabel

_ZONE_PAIRS: list[tuple[str, ChannelLabel, ChannelLabel]] = [
    ("front",        ChannelLabel.FL,  ChannelLabel.FR),
    ("surround",     ChannelLabel.SL,  ChannelLabel.SR),
    ("back",         ChannelLabel.BL,  ChannelLabel.BR),
    ("height_front", ChannelLabel.TFL, ChannelLabel.TFR),
    ("height_back",  ChannelLabel.TBL, ChannelLabel.TBR),
]

_PASSTHROUGH_LABELS: list[ChannelLabel] = [ChannelLabel.C, ChannelLabel.LFE]


def _extract_zones(
    audio: np.ndarray,
    input_fmt: object,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Split multichannel audio into stereo pairs by spatial zone and passthrough channels.

    Returns:
        zones: zone_name → (n_samples, 2) float32 array for stem separation.
        passthrough: channel_name → (n_samples,) float32 view for direct injection.
    """
    ch_map = {
        label: audio[:, i]
        for i, label in enumerate(input_fmt.channels)
    }

    zones: dict[str, np.ndarray] = {}
    for zone_name, left_label, right_label in _ZONE_PAIRS:
        if left_label in ch_map and right_label in ch_map:
            zones[zone_name] = np.column_stack(
                [ch_map[left_label], ch_map[right_label]]
            )

    passthrough: dict[str, np.ndarray] = {}
    for label in _PASSTHROUGH_LABELS:
        if label in ch_map:
            passthrough[label.value] = ch_map[label]

    return zones, passthrough


def _as_stereo_pair(audio: np.ndarray) -> np.ndarray:
    """Return a stereo representation of mono or stereo source audio."""
    if audio.ndim == 1 or audio.shape[1] == 1:
        mono = audio if audio.ndim == 1 else audio[:, 0]
        return np.column_stack([mono, mono])
    return audio[:, :2]


def _resample_zones(
    zones: dict[str, np.ndarray],
    source_sr: int,
    target_sr: int,
) -> dict[str, np.ndarray]:
    """Return source zones at routing sample rate."""
    if source_sr == target_sr:
        return zones
    divisor = math.gcd(source_sr, target_sr)
    up, down = target_sr // divisor, source_sr // divisor
    return {
        name: resample_poly(audio, up, down, axis=0).astype(np.float32, copy=False)
        for name, audio in zones.items()
    }
