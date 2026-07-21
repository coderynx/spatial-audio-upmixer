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
    "BL":  "f480f7f8c038df9c05cef6a539d46194f71784865c21e356e487367a159ceda3",
    "BR":  "d1de93b7464dff18827e3d23882c684863ba31fd51e44f5dba20e4c869d54a9c",
    "C":   "77c1e073fd93b26a3a7fef5083d7ed810405a85623ae574d8224f4babff8ccc5",
    "FL":  "ddf26038990b2fd743d82ee323ceae85fb1489eaaf1e1caa24e6d18fded4679e",
    "FR":  "e9285ce165a11243771700b3b5026ffbec964cae53a473eb743cc204e867547b",
    "LFE": "60d8c4535a085c4e1f15b5fad9eb82536fcbce24322767bdc8c994fe9cacc699",
    "SL":  "677e14039d19798c9ef8c19214527202142e86c44ec4cb384d3b84db6cf82ffc",
    "SR":  "f2c5e9fa424cd3e1c852b4585e7e9153682ff8d42e33ba3b2b43cf859a24a13d",
    "TBL": "9ae984331bfa7f7867efded136e7e84bc66eab2b5540f8882f7f033d36a90ae7",
    "TBR": "70df233846e46af294e8d64305e1a548b66b57e00b88af39a9a5ecb703673c8c",
    "TFL": "b0aed5c0f20d15c4108ba3b75e9f9ee6468acc26c5ccdcf08c17f34b1a02e7a7",
    "TFR": "fa2325e521050b27838b7ecb644ebdae80b87c7ac57129d7fe3f47044dfde6c7",
}

_GOLDEN_LKFS_HEX    = "05000000000032c0"   # -18.0 LKFS (final)
_GOLDEN_TP_HEX      = "9afddc17d6692fc0"
_GOLDEN_GAIN_HEX    = "36c860dbfd232cc0"
_GOLDEN_TP_LIMITED  = False

_GOLDEN_RAW_LKFS_HEX = "867969f8b14b00c0"  # BS.1770-5 Annex 3 weights
_GOLDEN_RAW_TP_HEX   = "8bf2b30ef3c503c0"  # BS.1770-5 FIR


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
