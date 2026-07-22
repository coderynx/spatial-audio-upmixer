"""Tests for source-zone anchoring in stem mode."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.source_anchor import apply_source_anchor
from upmixer.separation.stem_pipeline import StemUpmixPipeline


def _channels(fmt: str, value: float = -0.25) -> dict[str, np.ndarray]:
    return {
        label.value: np.full(8, value, dtype=np.float64)
        for label in FORMAT_MAP[fmt].channels
    }


def _zones() -> dict[str, np.ndarray]:
    return {
        "front": np.column_stack([np.full(8, 0.1), np.full(8, 0.2)]),
        "surround": np.column_stack([np.full(8, 0.3), np.full(8, 0.4)]),
        "back": np.column_stack([np.full(8, 0.5), np.full(8, 0.6)]),
        "height_front": np.column_stack([np.full(8, 0.7), np.full(8, 0.8)]),
        "height_back": np.column_stack([np.full(8, 0.9), np.full(8, 1.0)]),
    }


def test_zero_strength_returns_original_channels():
    channels = _channels("7.1.4")
    result = apply_source_anchor(channels, _zones(), FORMAT_MAP["7.1.4"], 0.0)

    assert result is channels
    assert all(np.all(audio == -0.25) for audio in channels.values())


def test_full_strength_anchors_every_multichannel_source_zone():
    channels = _channels("7.1.4")
    result = apply_source_anchor(channels, _zones(), FORMAT_MAP["7.1.4"], 1.0)

    expected = {
        "FL": 0.1, "FR": 0.2, "SL": 0.3, "SR": 0.4,
        "BL": 0.5, "BR": 0.6, "TFL": 0.7, "TFR": 0.8,
        "TBL": 0.9, "TBR": 1.0,
    }
    for channel, value in expected.items():
        np.testing.assert_allclose(result[channel], value)
    np.testing.assert_allclose(result["C"], -0.25)
    np.testing.assert_allclose(result["LFE"], -0.25)


def test_fractional_strength_blends_native_pair_only():
    channels = _channels("5.1")
    source = {"front": np.column_stack([np.full(8, 0.5), np.full(8, 0.25)])}

    result = apply_source_anchor(channels, source, FORMAT_MAP["5.1"], 0.5)

    np.testing.assert_allclose(result["FL"], 0.125)
    np.testing.assert_allclose(result["FR"], 0.0)
    for channel in ("C", "LFE", "SL", "SR"):
        np.testing.assert_allclose(result[channel], -0.25)


def test_missing_target_pair_is_not_anchored():
    channels = _channels("5.1")
    result = apply_source_anchor(channels, _zones(), FORMAT_MAP["5.1"], 1.0)

    np.testing.assert_allclose(result["FL"], 0.1)
    np.testing.assert_allclose(result["FR"], 0.2)
    np.testing.assert_allclose(result["SL"], 0.3)
    np.testing.assert_allclose(result["SR"], 0.4)
    for channel in ("C", "LFE"):
        np.testing.assert_allclose(result[channel], -0.25)


@pytest.mark.parametrize("strength", [-0.1, 1.1])
def test_invalid_strength_raises(strength: float):
    with pytest.raises(ValueError, match="stem_source_anchor_strength"):
        apply_source_anchor(_channels("5.1"), {}, FORMAT_MAP["5.1"], strength)


def test_pipeline_rejects_invalid_strength_before_reading_input():
    pipeline = StemUpmixPipeline(UpmixConfig(stem_source_anchor_strength=1.1))

    with pytest.raises(ValueError, match="stem_source_anchor_strength"):
        pipeline.process_file("missing.wav", "output.wav")
