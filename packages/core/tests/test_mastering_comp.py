"""Tests for upmixer.mastering_comp — BusCompressor + COMP_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.mastering.compressor import (
    COMP_PROFILES,
    COMP_PROFILE_NAMES,
    BusCompressor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channels_51(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    """5.1 channel dict with a 440 Hz sine."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


def _loud_channels(n: int = 44100) -> dict[str, np.ndarray]:
    """Channels well above typical compressor thresholds."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = 0.9 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


def _quiet_channels(n: int = 44100) -> dict[str, np.ndarray]:
    """Channels well below all thresholds — should pass through nearly unchanged."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = 1e-4 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


def _make_comp(**kwargs) -> BusCompressor:
    defaults = dict(
        threshold_db=-18.0,
        ratio=2.0,
        attack_ms=20.0,
        release_ms=200.0,
        knee_db=6.0,
        makeup_db=0.0,
        sample_rate=44100,
    )
    defaults.update(kwargs)
    return BusCompressor(**defaults)


# ---------------------------------------------------------------------------
# COMP_PROFILES sanity
# ---------------------------------------------------------------------------

class TestCompProfiles:
    def test_all_profiles_have_required_keys(self):
        required = {"threshold_db", "ratio", "attack_ms", "release_ms", "knee_db", "makeup_db"}
        for name, p in COMP_PROFILES.items():
            assert required <= set(p.keys()), f"Profile '{name}' missing keys"

    def test_ratios_ge_one(self):
        for name, p in COMP_PROFILES.items():
            assert p["ratio"] >= 1.0, f"Profile '{name}' ratio < 1.0"

    def test_profile_names_tuple(self):
        assert isinstance(COMP_PROFILE_NAMES, tuple)
        assert set(COMP_PROFILE_NAMES) == set(COMP_PROFILES.keys())


# ---------------------------------------------------------------------------
# BusCompressor construction
# ---------------------------------------------------------------------------

class TestBusCompressorInit:
    def test_constructs_with_valid_params(self):
        c = _make_comp()
        assert c is not None

    def test_ratio_below_one_raises(self):
        with pytest.raises(ValueError, match="ratio"):
            _make_comp(ratio=0.5)

    def test_knee_clamped_to_zero(self):
        c = _make_comp(knee_db=-5.0)
        assert c._knee == 0.0


# ---------------------------------------------------------------------------
# BusCompressor.process — pass-through / bypass conditions
# ---------------------------------------------------------------------------

class TestBusCompressorBypass:
    def test_ratio_one_returns_original(self):
        """ratio=1.0 (unity) must return original dict unchanged."""
        chs = _channels_51()
        c = _make_comp(ratio=1.0)
        out = c.process(chs)
        assert out is chs

    def test_lfe_unchanged(self):
        chs = _loud_channels()
        lfe_orig = chs["LFE"].copy()
        out = _make_comp().process(chs)
        np.testing.assert_array_equal(out["LFE"], lfe_orig)

    def test_lfe_unchanged_with_custom_key(self):
        chs = {"FL": np.ones(1024) * 0.5, "SUB": np.ones(1024) * 0.5}
        sub_orig = chs["SUB"].copy()
        out = _make_comp().process(chs, lfe_key="SUB")
        np.testing.assert_array_equal(out["SUB"], sub_orig)

    def test_quiet_input_passes_through_nearly_unchanged(self):
        """Signal well below threshold → minimal gain reduction."""
        chs = _quiet_channels()
        fl_orig = chs["FL"].copy()
        out = _make_comp(threshold_db=-18.0).process(chs)
        max_diff = float(np.max(np.abs(out["FL"] - fl_orig)))
        assert max_diff < 1e-3, f"Too much gain reduction on quiet signal: {max_diff}"


# ---------------------------------------------------------------------------
# BusCompressor.process — compression behavior
# ---------------------------------------------------------------------------

class TestBusCompressorCompression:
    def test_loud_input_reduced(self):
        """Loud input should result in output RMS < input RMS."""
        chs = _loud_channels()
        out = _make_comp(threshold_db=-20.0, ratio=4.0).process(chs)
        rms_in = float(np.sqrt(np.mean(chs["FL"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["FL"] ** 2)))
        assert rms_out < rms_in, f"No gain reduction applied (in={rms_in:.4f}, out={rms_out:.4f})"

    def test_output_is_finite(self):
        chs = _loud_channels()
        out = _make_comp().process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr)), f"Non-finite values in {name}"

    def test_output_channel_keys_preserved(self):
        chs = _channels_51()
        out = _make_comp().process(chs)
        assert set(out.keys()) == set(chs.keys())

    def test_output_shape_preserved(self):
        chs = _channels_51(n=22050)
        out = _make_comp().process(chs)
        for name in chs:
            assert out[name].shape == chs[name].shape

    def test_makeup_gain_increases_output(self):
        """Positive makeup_db should raise output level relative to no makeup."""
        chs = _loud_channels()
        c_no_makeup = _make_comp(makeup_db=0.0)
        c_makeup = _make_comp(makeup_db=6.0)
        out_no = c_no_makeup.process(chs)
        out_mk = c_makeup.process({k: v.copy() for k, v in chs.items()})
        rms_no = float(np.sqrt(np.mean(out_no["FL"] ** 2)))
        rms_mk = float(np.sqrt(np.mean(out_mk["FL"] ** 2)))
        assert rms_mk > rms_no, "Makeup gain did not increase output level"

    def test_hard_knee_works(self):
        chs = _loud_channels()
        out = _make_comp(knee_db=0.0).process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr))

    @pytest.mark.parametrize("profile_name", list(COMP_PROFILES.keys()))
    def test_all_profiles_run(self, profile_name):
        """Each built-in profile should compress without error."""
        p = COMP_PROFILES[profile_name]
        c = BusCompressor(
            threshold_db=p["threshold_db"],
            ratio=p["ratio"],
            attack_ms=p["attack_ms"],
            release_ms=p["release_ms"],
            knee_db=p["knee_db"],
            makeup_db=p["makeup_db"],
            sample_rate=48000,
        )
        chs = _loud_channels()
        out = c.process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr))
