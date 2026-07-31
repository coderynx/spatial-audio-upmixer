"""Golden tests for the mastering chain and loudness measurement.

Lock current output before performance refactoring. Any change that alters
these values changes the audio output and must be treated as a regression.

`test_channel_rms_golden`/`test_mastering_result_lkfs` pin per-channel RMS
and the LKFS/true-peak/gain metrics with a tolerance (`pytest.approx`)
rather than bit-exact hashes: the mastering chain's spectral EQ stage
drifts at ULP level across numpy/scipy FFT implementations (observed going
from numpy<2.4/scipy<1.18 to numpy 2.4.6/scipy 1.18.0), which flips a
bit-exact hash on every such library bump without any audible change.
`TestLoudnessMeasurementGolden` stays bit-exact — the time-domain
K-weighting/true-peak FIR path has not shown this drift.

To regenerate after an intentional behaviour change, run:
    REGENERATE_GOLDEN=1 uv run python packages/core/tests/test_mastering_golden.py
"""
from __future__ import annotations

import os
import struct

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness, measure_true_peak
from upmixer.mastering import MasteringChain

_SR = 48000
_DURATION_S = 5
_FMT = FORMAT_MAP["7.1.4"]

_GOLDEN_CHANNEL_RMS_HEX = {
    "BL": "32482b93b0cba23f",
    "BR": "48be7722c0cca23f",
    "C": "4f902852101da23f",
    "FL": "4cf59bc79b1aa23f",
    "FR": "e544a6b65718a23f",
    "LFE": "2954ac787535ac3f",
    "SL": "a2e8d0bf5ac5a23f",
    "SR": "ffc369d090c3a23f",
    "TBL": "f0bca6b7dbd0a23f",
    "TBR": "66570e2417c3a23f",
    "TFL": "82b27bea8ac6a23f",
    "TFR": "f75ddf574dc4a23f",
}

_GOLDEN_LKFS_HEX    = "09000000000032c0"   # -18.0 LKFS (final)
_GOLDEN_TP_HEX      = "9bfddc17d6692fc0"
_GOLDEN_GAIN_HEX    = "38c860dbfd232cc0"
_GOLDEN_TP_LIMITED  = False

_GOLDEN_RAW_LKFS_HEX = "867969f8b14b00c0"  # BS.1770-5 Annex 3 weights
_GOLDEN_RAW_TP_HEX   = "8bf2b30ef3c503c0"  # BS.1770-5 FIR


def _tohex(value: float) -> str:
    return struct.pack("<d", value).hex()


def _unhex(value: str) -> float:
    return struct.unpack("<d", bytes.fromhex(value))[0]


def _make_channels() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(456)
    n = _SR * _DURATION_S
    channels: dict[str, np.ndarray] = {}
    for label in _FMT.channels:
        name = label.value
        t = np.linspace(0, _DURATION_S, n, endpoint=False)
        freq = 220.0 if name in ("FL", "FR", "C") else 110.0
        sig = 0.3 * np.sin(2 * np.pi * freq * t)
        sig += 0.1 * rng.standard_normal(n)
        channels[name] = sig.astype(np.float64)
    return channels


def _make_config() -> UpmixConfig:
    return UpmixConfig(
        loudness_normalize=True,
        loudness_target_lkfs=-18.0,
        loudness_max_tp=-1.0,
        mastering_eq_profile="spatial-air",
        mastering_eq_strength=0.8,
        mastering_comp_profile="glue",
        mastering_bass_profile="boost",
    )


class TestMasteringChainGolden:
    """Full mastering chain output must not drift beyond a tight tolerance."""

    def test_channel_rms_golden(self):
        channels = _make_channels()
        cfg = _make_config()
        chain = MasteringChain(cfg)
        result, _ = chain.process(channels, _SR, _FMT)

        for name, arr in result.items():
            rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))
            assert rms == pytest.approx(_unhex(_GOLDEN_CHANNEL_RMS_HEX[name])), (
                f"Channel {name!r} RMS drifted from its golden value"
            )

    def test_mastering_result_lkfs(self):
        channels = _make_channels()
        cfg = _make_config()
        chain = MasteringChain(cfg)
        _, mr = chain.process(channels, _SR, _FMT)

        assert mr.measured_lkfs == pytest.approx(_unhex(_GOLDEN_LKFS_HEX))
        assert mr.measured_tp_dbtp == pytest.approx(_unhex(_GOLDEN_TP_HEX))
        assert mr.applied_gain_db == pytest.approx(_unhex(_GOLDEN_GAIN_HEX))
        assert mr.tp_limited == _GOLDEN_TP_LIMITED

    def test_channel_shapes_preserved(self):
        channels = _make_channels()
        cfg = _make_config()
        chain = MasteringChain(cfg)
        result, _ = chain.process(channels, _SR, _FMT)

        for name in channels:
            assert result[name].shape == channels[name].shape
            assert result[name].dtype == np.float64


class TestLoudnessMeasurementGolden:
    """Loudness measurement functions must be bit-exact."""

    def test_integrated_loudness(self):
        channels = _make_channels()
        lkfs = measure_integrated_loudness(channels, _SR, _FMT)
        assert struct.pack("<d", lkfs).hex() == _GOLDEN_RAW_LKFS_HEX, (
            f"measure_integrated_loudness changed: {lkfs:.9f}"
        )

    def test_true_peak(self):
        channels = _make_channels()
        tp = measure_true_peak(channels, _SR)
        assert struct.pack("<d", tp).hex() == _GOLDEN_RAW_TP_HEX, (
            f"measure_true_peak changed: {tp:.9f}"
        )


if __name__ == "__main__" and os.getenv("REGENERATE_GOLDEN"):
    import sys
    channels = _make_channels()
    cfg = _make_config()
    chain = MasteringChain(cfg)
    result, mr = chain.process(channels, _SR, _FMT)

    print("_GOLDEN_CHANNEL_RMS_HEX = {")
    for name, arr in sorted(result.items()):
        rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))
        print(f'    "{name}": "{_tohex(rms)}",')
    print("}")
    print(f'_GOLDEN_LKFS_HEX    = "{_tohex(mr.measured_lkfs)}"')
    print(f'_GOLDEN_TP_HEX      = "{_tohex(mr.measured_tp_dbtp)}"')
    print(f'_GOLDEN_GAIN_HEX    = "{_tohex(mr.applied_gain_db)}"')
    print(f"_GOLDEN_TP_LIMITED  = {mr.tp_limited}")

    raw_channels = _make_channels()
    lkfs = measure_integrated_loudness(raw_channels, _SR, _FMT)
    tp = measure_true_peak(raw_channels, _SR)
    print(f'_GOLDEN_RAW_LKFS_HEX = "{struct.pack("<d", lkfs).hex()}"')
    print(f'_GOLDEN_RAW_TP_HEX   = "{struct.pack("<d", tp).hex()}"')
    sys.exit(0)
