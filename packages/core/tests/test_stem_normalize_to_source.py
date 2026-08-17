"""Output renormalization against the source (mixing phase 9)."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness
from upmixer.separation.stem_pipeline import _normalize_to_source

_SR = 48000


def _noise(n: int = _SR, seed: int = 20260817) -> np.ndarray:
    return 0.2 * np.random.default_rng(seed).standard_normal(n)


def test_output_lands_on_the_source_loudness():
    fmt = FORMAT_MAP["7.1.4"]
    source = np.column_stack([_noise(), _noise(seed=7)])
    channels = {
        label.value: 0.4 * _noise(seed=index)
        for index, label in enumerate(fmt.channels)
    }

    normalized = _normalize_to_source(channels, source, _SR, _SR, fmt)

    source_lkfs = measure_integrated_loudness(
        {"FL": source[:, 0], "FR": source[:, 1]}, _SR, FORMAT_MAP["stereo"]
    )
    assert measure_integrated_loudness(normalized, _SR, fmt) == pytest.approx(
        source_lkfs, abs=0.05
    )


def test_short_material_falls_back_to_energy_matching():
    fmt = FORMAT_MAP["stereo"]
    source = np.column_stack([_noise(1000), _noise(1000, seed=7)])
    channels = {"FL": 0.05 * _noise(1000), "FR": 0.05 * _noise(1000, seed=7)}

    normalized = _normalize_to_source(channels, source, _SR, _SR, fmt)

    routed = sum(float(np.dot(ch, ch)) for ch in normalized.values())
    assert routed == pytest.approx(float(np.vdot(source, source).real), rel=1e-6)
