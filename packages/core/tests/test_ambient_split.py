"""Parity tests for the primary/ambient split binding.

The literals are the same pin `packages/dsp/crates/dsp-core/tests/
unit_routing_ambient.rs::the_split_matches_the_pinned_samples` asserts, so a
wheel built from a different split than the wasm preview fails here.
"""
from __future__ import annotations

import numpy as np
import upmixer_dsp

SR = 48000
N = 9600
PROBES = (2048, 4096, 6144)
PINNED = {
    "rear_l": [0.00949903053205785, -0.02137585394233137, 0.015521223284353647],
    "rear_r": [0.012354056080600629, -0.01844941093216676, 0.007393748905077962],
    "height_l": [-0.0005359773449861317, 0.0002232876237728415, 0.00038587992947320196],
    "height_r": [-0.00029197729661793563, -0.00020581934326996051, 0.0005281452483678123],
}


def _deterministic(n: int, sample_rate: int, seed_phase: float) -> np.ndarray:
    """Mirror of ``dump_golden_vectors.py::deterministic_signal``."""
    tones = [(55.0, 0.30), (220.0, 0.22), (1000.0, 0.18), (3500.0, 0.12), (11000.0, 0.07)]
    t = np.arange(n) / sample_rate
    signal = sum(
        amp * np.sin(2.0 * np.pi * freq * t + seed_phase * freq / 100.0)
        for freq, amp in tones
    )
    return np.ascontiguousarray(signal * (0.6 + 0.4 * np.sin(2.0 * np.pi * 0.7 * t)))


def _split() -> dict[str, np.ndarray]:
    left = _deterministic(N, SR, 0.0)
    right = _deterministic(N, SR, 1.0)
    rear_l, rear_r, height_l, height_r = upmixer_dsp.ambient_split(left, right, SR)
    return {"rear_l": rear_l, "rear_r": rear_r, "height_l": height_l, "height_r": height_r}


def test_the_split_matches_the_pinned_samples():
    got = _split()
    for name, want in PINNED.items():
        for probe, expected in zip(PROBES, want):
            assert got[name][probe] == expected, (name, probe)


def test_the_tilt_pair_is_power_complementary():
    got = _split()
    for rear, height in (("rear_l", "height_l"), ("rear_r", "height_r")):
        settled = slice(2048, N)
        summed = got[rear][settled] + got[height][settled]
        split = np.mean(got[rear][settled] ** 2) + np.mean(got[height][settled] ** 2)
        assert abs(np.mean(summed**2) / split - 1.0) < 0.02


def test_the_split_constants_are_the_ones_the_preview_was_built_with():
    assert upmixer_dsp.AMBIENT_FFT_SIZE == 1024
    assert upmixer_dsp.AMBIENT_TILT_HZ == 2000.0
