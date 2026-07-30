"""Optional performance benchmarks for mastering hot paths.

Skipped by default. Run with:
    python3 -m pytest tests/test_mastering_perf.py -m perf -s

Tracks wall-clock time and peak memory (via tracemalloc) per stage so
regressions surface without requiring a profiler.
"""
from __future__ import annotations

import time
import tracemalloc

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness, measure_true_peak
from upmixer.mastering import MasteringChain

pytestmark = pytest.mark.perf

_SR = 48000
_DURATION_S = 10
_FMT = FORMAT_MAP["7.1.4"]

_CHANNEL_PEAK_MB = 600
_LOUDNESS_WALL_S = 3.0
_TP_WALL_S = 30.0
_CHAIN_WALL_S = 60.0
_CHAIN_PEAK_MB = 2000


def _make_channels(duration_s: int = _DURATION_S) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(42)
    n = _SR * duration_s
    return {
        label.value: (
            0.3 * np.sin(2 * np.pi * 220 * np.linspace(0, duration_s, n, endpoint=False))
            + 0.1 * rng.standard_normal(n)
        ).astype(np.float64)
        for label in _FMT.channels
    }


@pytest.mark.perf
def test_integrated_loudness_speed():
    channels = _make_channels()
    t0 = time.perf_counter()
    measure_integrated_loudness(channels, _SR, _FMT)
    elapsed = time.perf_counter() - t0
    print(f"\n  measure_integrated_loudness: {elapsed:.3f}s (limit {_LOUDNESS_WALL_S}s)")
    assert elapsed < _LOUDNESS_WALL_S, (
        f"measure_integrated_loudness too slow: {elapsed:.3f}s > {_LOUDNESS_WALL_S}s"
    )


@pytest.mark.perf
def test_true_peak_memory():
    channels = _make_channels()
    tracemalloc.start()
    measure_true_peak(channels, _SR)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak / 1024 / 1024
    print(f"\n  measure_true_peak peak memory: {peak_mb:.1f} MB (limit {_CHANNEL_PEAK_MB} MB)")
    assert peak_mb < _CHANNEL_PEAK_MB, (
        f"measure_true_peak memory too high: {peak_mb:.1f} MB > {_CHANNEL_PEAK_MB} MB"
    )


@pytest.mark.perf
def test_true_peak_speed():
    channels = _make_channels()
    t0 = time.perf_counter()
    measure_true_peak(channels, _SR)
    elapsed = time.perf_counter() - t0
    print(f"\n  measure_true_peak: {elapsed:.3f}s (limit {_TP_WALL_S}s)")
    assert elapsed < _TP_WALL_S, (
        f"measure_true_peak too slow: {elapsed:.3f}s > {_TP_WALL_S}s"
    )


@pytest.mark.perf
def test_mastering_chain_speed_and_memory():
    channels = _make_channels()
    cfg = UpmixConfig(
        loudness_normalize=True,
        mastering_eq_profile="spatial-air",
        mastering_comp_profile="glue",
        mastering_bass_profile="boost",
    )
    chain = MasteringChain(cfg)

    tracemalloc.start()
    t0 = time.perf_counter()
    chain.process(channels, _SR, _FMT)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / 1024 / 1024
    print(f"\n  MasteringChain.process: {elapsed:.3f}s (limit {_CHAIN_WALL_S}s)")
    print(f"  MasteringChain.process peak: {peak_mb:.1f} MB (limit {_CHAIN_PEAK_MB} MB)")
    assert elapsed < _CHAIN_WALL_S, (
        f"MasteringChain.process too slow: {elapsed:.3f}s > {_CHAIN_WALL_S}s"
    )
    assert peak_mb < _CHAIN_PEAK_MB, (
        f"MasteringChain.process memory too high: {peak_mb:.1f} MB > {_CHAIN_PEAK_MB} MB"
    )
