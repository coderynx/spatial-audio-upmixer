"""Bit-exact golden tests for the mastering chain and loudness measurement.

Lock current output before performance refactoring. Any change that alters
these hashes changes the audio output and must be treated as a regression.

To regenerate after an intentional behaviour change, run:
    REGENERATE_GOLDEN=1 python3 tests/test_mastering_golden.py
"""
from __future__ import annotations

import hashlib
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

_GOLDEN_CHANNEL_HASHES = {
    "BL":  "8e8f58cee701dc98f5821c727ffec12c6203d52146b3f5deaddaf15a814ae3b7",
    "BR":  "cca1c081f35baad187dc9a8cb6220b82f3370b20c4ebec5e7e8a0d3b47414539",
    "C":   "f93c7ef0bfd1c87612344fe858a95db59b3c5ebc807d26f3c710b884823e7e1e",
    "FL":  "97f957a3461b038ec13179a4c2f552beaa73d690db2cbc824dc39bdaf31c7da4",
    "FR":  "141a8531d890bd6e89c45e5297acbc083fc9cc3a8ff464a71d27c5b7f6518a2d",
    "LFE": "037611086f9b1a1e55e584ad2963b82a786fbbff7e2cdf29c19df181d007ef91",
    "SL":  "6085b5d4620592414839d7d1523e8af4ffbf5fc79cfba78339d1d59ce98b660b",
    "SR":  "31e156154e5d3224a78e3f8bd5e62b1b229692e6dda480c7c1be7ada39ec8302",
    "TBL": "22931a0097ad71f78b53e097aee17d80bc0535fec1794fc704123197146e421a",
    "TBR": "3d1c335da83c6ced674d171ac98c5049c110141b5b38a528a6b33c0f4b46c21d",
    "TFL": "bd28d9fc7cc8bb46b762ffd3457a39e0e6b3fc803317dc76355fe5c1add1403f",
    "TFR": "84edfe7a70724f975e4ce9e36dc7f15cadcb0534b185326997abc8f8adba9497",
}

_GOLDEN_LKFS_HEX    = "3b3e30ee2fc90ac0"   # -3.348236 LKFS (pre-norm)
_GOLDEN_TP_HEX      = "e9502f2be33730c0"   # -16.218310 dBTP (post-LN)
_GOLDEN_GAIN_HEX    = "71f07304b44d2dc0"   # -14.651764 dB
_GOLDEN_TP_LIMITED  = False

_GOLDEN_RAW_LKFS_HEX = "6a27992994c5f3bf"  # -1.235737002 (raw, before chain)
_GOLDEN_RAW_TP_HEX   = "c10f01d08af103c0"  # -2.492940545 (raw)


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
    """Full mastering chain output must not change bit-for-bit."""

    def test_channel_hashes(self):
        channels = _make_channels()
        cfg = _make_config()
        chain = MasteringChain(cfg)
        result, _ = chain.process(channels, _SR, _FMT)

        for name, arr in result.items():
            h = hashlib.sha256(arr.tobytes()).hexdigest()
            assert h == _GOLDEN_CHANNEL_HASHES[name], (
                f"Channel {name!r} output changed. "
                f"Got {h}, expected {_GOLDEN_CHANNEL_HASHES[name]}"
            )

    def test_mastering_result_lkfs(self):
        channels = _make_channels()
        cfg = _make_config()
        chain = MasteringChain(cfg)
        _, mr = chain.process(channels, _SR, _FMT)

        assert struct.pack("<d", mr.measured_lkfs).hex() == _GOLDEN_LKFS_HEX
        assert struct.pack("<d", mr.measured_tp_dbtp).hex() == _GOLDEN_TP_HEX
        assert struct.pack("<d", mr.applied_gain_db).hex() == _GOLDEN_GAIN_HEX
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

    print("_GOLDEN_CHANNEL_HASHES = {")
    for name, arr in sorted(result.items()):
        h = hashlib.sha256(arr.tobytes()).hexdigest()
        print(f'    "{name}": "{h}",')
    print("}")
    lkfs_hex = struct.pack("<d", mr.measured_lkfs).hex()
    tp_hex = struct.pack("<d", mr.measured_tp_dbtp).hex()
    gain_hex = struct.pack("<d", mr.applied_gain_db).hex()
    print(f"_GOLDEN_LKFS_HEX    = '{lkfs_hex}'")
    print(f"_GOLDEN_TP_HEX      = '{tp_hex}'")
    print(f"_GOLDEN_GAIN_HEX    = '{gain_hex}'")
    print(f"_GOLDEN_TP_LIMITED  = {mr.tp_limited}")

    raw_channels = _make_channels()
    lkfs = measure_integrated_loudness(raw_channels, _SR, _FMT)
    tp = measure_true_peak(raw_channels, _SR)
    print(f"_GOLDEN_RAW_LKFS_HEX = '{struct.pack('<d', lkfs).hex()}'")
    print(f"_GOLDEN_RAW_TP_HEX   = '{struct.pack('<d', tp).hex()}'")
    sys.exit(0)
