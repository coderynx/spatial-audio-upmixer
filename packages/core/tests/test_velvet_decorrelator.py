"""Parity tests for the velvet-noise decorrelator pair binding.

The literals are the same pin `packages/dsp/crates/dsp-core/tests/
golden_kernels.rs::velvet_pair_matches_the_pinned_tap_table` asserts, so a
wheel built from different taps than the wasm preview fails here.
"""
from __future__ import annotations

import numpy as np
import pytest
import upmixer_dsp

SR = 48000
TAP_HEADS = {
    "left": [
        (1, 0.43628207230905075),
        (68, 0.3926538650781457),
        (109, -0.3533884785703311),
        (146, -0.31804963071329806),
    ],
    "right": [
        (46, 0.4362820723090507),
        (88, -0.39265386507814565),
        (134, 0.35338847857033107),
        (176, 0.318049630713298),
    ],
}
SPANS = {"left": 1411, "right": 1428}


def _impulse_response(side: str, wet: float = upmixer_dsp.VELVET_WET) -> np.ndarray:
    impulse = np.zeros(int(SR * upmixer_dsp.VELVET_LENGTH_MS / 1000.0))
    impulse[0] = 1.0
    return upmixer_dsp.velvet_pair_send(
        impulse,
        SR,
        side,
        upmixer_dsp.VELVET_LENGTH_MS,
        upmixer_dsp.VELVET_TAPS_PER_SIDE,
        upmixer_dsp.VELVET_SEED,
        wet,
    )


@pytest.mark.parametrize("side", ["left", "right"])
def test_binding_taps_match_the_pinned_table(side: str) -> None:
    response = _impulse_response(side)
    positions = np.flatnonzero(response)
    assert len(positions) == upmixer_dsp.VELVET_TAPS_PER_SIDE
    assert positions[-1] == SPANS[side]
    for position, gain in TAP_HEADS[side]:
        assert response[position] == pytest.approx(gain, abs=1e-15)


def test_binding_output_matches_the_pinned_vector() -> None:
    signal = np.sin(0.05 * np.arange(2048))
    expected = {"left": (-0.08037244581415715, -0.20449503809930403),
                "right": (0.18897751998941792, 0.17069075512360038)}
    for side, (at_1500, at_2000) in expected.items():
        out = upmixer_dsp.velvet_pair_send(
            signal,
            SR,
            side,
            upmixer_dsp.VELVET_LENGTH_MS,
            upmixer_dsp.VELVET_TAPS_PER_SIDE,
            upmixer_dsp.VELVET_SEED,
            upmixer_dsp.VELVET_WET,
        )
        assert out[1500] == pytest.approx(at_1500, abs=1e-12)
        assert out[2000] == pytest.approx(at_2000, abs=1e-12)


def test_the_pair_folds_down_to_the_power_sum() -> None:
    rng = np.random.default_rng(20260817)
    signal = rng.standard_normal(SR)
    left, right = (
        upmixer_dsp.velvet_pair_send(
            signal,
            SR,
            side,
            upmixer_dsp.VELVET_LENGTH_MS,
            upmixer_dsp.VELVET_TAPS_PER_SIDE,
            upmixer_dsp.VELVET_SEED,
            upmixer_dsp.VELVET_WET,
        )
        for side in ("left", "right")
    )
    tail = slice(1440, None)
    fold = 10.0 * np.log10(
        np.sum((left[tail] + right[tail]) ** 2) / (2.0 * np.sum(signal[tail] ** 2))
    )
    assert abs(fold) < 0.15
    correlation = np.corrcoef(left[tail], right[tail])[0, 1]
    assert abs(correlation) < 0.4


def test_an_unknown_side_is_rejected() -> None:
    with pytest.raises(ValueError):
        upmixer_dsp.velvet_pair_send(np.zeros(16), SR, "middle", 30.0, 30, 1, 1.0)
