"""Source-zone anchoring for artifact-resistant stem upmixing."""
from __future__ import annotations

import numpy as np

from upmixer.formats import OutputFormat


_ZONE_CHANNELS: dict[str, tuple[str, str]] = {
    "front": ("FL", "FR"),
    "surround": ("SL", "SR"),
    "back": ("BL", "BR"),
    "height_front": ("TFL", "TFR"),
    "height_back": ("TBL", "TBR"),
}


def apply_source_anchor(
    channels: dict[str, np.ndarray],
    source_zones: dict[str, np.ndarray],
    output_fmt: OutputFormat,
    strength: float,
) -> dict[str, np.ndarray]:
    """Blend original source zones into matching native output channel pairs."""
    if not 0.0 <= strength <= 1.0:
        raise ValueError("stem_source_anchor_strength must be between 0.0 and 1.0")
    if strength == 0.0:
        return channels

    output_channels = {label.value for label in output_fmt.channels}
    for zone, source in source_zones.items():
        pair = _ZONE_CHANNELS.get(zone)
        if pair is None or not set(pair).issubset(output_channels):
            continue
        if source.ndim != 2 or source.shape[1] == 0:
            continue
        n = min(len(source), len(channels[pair[0]]), len(channels[pair[1]]))
        if n == 0:
            continue
        left = source[:n, 0]
        right = source[:n, 1] if source.shape[1] > 1 else left
        channels[pair[0]][:n] = (
            (1.0 - strength) * channels[pair[0]][:n] + strength * left
        )
        channels[pair[1]][:n] = (
            (1.0 - strength) * channels[pair[1]][:n] + strength * right
        )
    return channels
