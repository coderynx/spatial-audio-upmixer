"""Tests for upmixer.separation.stem_rebalance — StemRebalancer + REBALANCE_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.separation.stem_rebalance import (
    REBALANCE_PROFILES,
    REBALANCE_PROFILE_NAMES,
    StemRebalancer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stems(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    """4-stem dict with (n, 2) stereo arrays."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {
        "Vocals": np.stack([sig, sig], axis=1),
        "Drums":  np.stack([sig * 0.9, sig * 0.9], axis=1),
        "Bass":   np.stack([sig * 0.7, sig * 0.7], axis=1),
        "Other":  np.stack([sig * 0.5, sig * 0.5], axis=1),
    }


def _stems_zoned(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    """Zone-tagged stem dict (@front suffix)."""
    base = _stems(n, amplitude)
    return {f"{k}@front": v for k, v in base.items()}


# ---------------------------------------------------------------------------
# REBALANCE_PROFILES sanity
# ---------------------------------------------------------------------------

class TestRebalanceProfiles:
    def test_all_profiles_are_dicts(self):
        for name, p in REBALANCE_PROFILES.items():
            assert isinstance(p, dict), f"Profile '{name}' is not a dict"

    def test_profile_names_tuple(self):
        assert isinstance(REBALANCE_PROFILE_NAMES, tuple)
        assert set(REBALANCE_PROFILE_NAMES) == set(REBALANCE_PROFILES.keys())

    def test_balanced_is_empty(self):
        assert REBALANCE_PROFILES["balanced"] == {}

    def test_vocal_forward_boosts_vocals(self):
        assert REBALANCE_PROFILES["vocal-forward"]["Vocals"] > 0

    def test_instrumental_cuts_vocals(self):
        assert REBALANCE_PROFILES["instrumental"]["Vocals"] < 0


# ---------------------------------------------------------------------------
# StemRebalancer construction
# ---------------------------------------------------------------------------

class TestStemRebalancerInit:
    def test_constructs_with_empty_gains(self):
        r = StemRebalancer({}, 44100)
        assert r is not None

    def test_constructs_with_gains(self):
        r = StemRebalancer({"Vocals": 2.0, "Drums": -1.0}, 44100)
        assert r is not None


# ---------------------------------------------------------------------------
# StemRebalancer.process — identity / pass-through
# ---------------------------------------------------------------------------

class TestStemRebalancerIdentity:
    def test_zero_gain_returns_original_arrays(self):
        """0 dB gain → same array objects (no copy)."""
        stems = _stems()
        r = StemRebalancer({"Vocals": 0.0}, 44100)
        out = r.process(stems)
        assert out["Vocals"] is stems["Vocals"]

    def test_absent_key_returns_original(self):
        stems = _stems()
        r = StemRebalancer({"UnknownStem": 3.0}, 44100)
        out = r.process(stems)
        assert out["Vocals"] is stems["Vocals"]

    def test_empty_gains_returns_original_arrays(self):
        stems = _stems()
        r = StemRebalancer({}, 44100)
        out = r.process(stems)
        for k in stems:
            assert out[k] is stems[k]

    def test_output_shape_preserved(self):
        stems = _stems(n=22050)
        r = StemRebalancer({"Vocals": 1.5}, 44100)
        out = r.process(stems)
        for k, arr in out.items():
            assert arr.shape == stems[k].shape

    def test_all_keys_present_in_output(self):
        stems = _stems()
        r = StemRebalancer({"Vocals": 1.0}, 44100)
        out = r.process(stems)
        assert set(out.keys()) == set(stems.keys())


# ---------------------------------------------------------------------------
# StemRebalancer.process — gain application
# ---------------------------------------------------------------------------

class TestStemRebalancerGain:
    def test_positive_gain_increases_rms(self):
        stems = _stems()
        r = StemRebalancer({"Vocals": 6.0}, 44100)
        out = r.process(stems)
        rms_in  = float(np.sqrt(np.mean(stems["Vocals"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["Vocals"] ** 2)))
        assert rms_out > rms_in, "Positive gain did not increase RMS"

    def test_negative_gain_decreases_rms(self):
        stems = _stems()
        r = StemRebalancer({"Drums": -6.0}, 44100)
        out = r.process(stems)
        rms_in  = float(np.sqrt(np.mean(stems["Drums"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["Drums"] ** 2)))
        assert rms_out < rms_in, "Negative gain did not decrease RMS"

    def test_unaddressed_stems_unchanged(self):
        stems = _stems()
        r = StemRebalancer({"Vocals": 3.0}, 44100)
        out = r.process(stems)
        np.testing.assert_array_equal(out["Bass"],  stems["Bass"])
        np.testing.assert_array_equal(out["Other"], stems["Other"])

    def test_output_is_finite(self):
        stems = _stems()
        r = StemRebalancer({"Vocals": 6.0, "Drums": -3.0}, 44100)
        out = r.process(stems)
        for arr in out.values():
            assert np.all(np.isfinite(arr)), "Non-finite values in output"

    def test_large_boost_soft_clips(self):
        """Boost > 3 dB should soft-clip via tanh — output must not exceed 1.0."""
        t = np.linspace(0, 1, 44100, endpoint=False)
        sig = 0.9 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        stems = {"Vocals": np.stack([sig, sig], axis=1)}
        r = StemRebalancer({"Vocals": 12.0}, 44100)  # big boost
        out = r.process(stems)
        assert np.max(np.abs(out["Vocals"])) <= 1.0, "Soft-clip did not limit output"


# ---------------------------------------------------------------------------
# StemRebalancer.process — zone-tagged stems
# ---------------------------------------------------------------------------

class TestStemRebalancerZone:
    def test_zone_suffix_stripped(self):
        """@zone suffix should be stripped; canonical name used for lookup."""
        stems = _stems_zoned()
        r = StemRebalancer({"Vocals": 3.0}, 44100)
        out = r.process(stems)
        rms_in  = float(np.sqrt(np.mean(stems["Vocals@front"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["Vocals@front"] ** 2)))
        assert rms_out > rms_in, "Zone-tagged stem not processed"

    def test_unaddressed_zone_stems_unchanged(self):
        stems = _stems_zoned()
        r = StemRebalancer({"Vocals": 3.0}, 44100)
        out = r.process(stems)
        np.testing.assert_array_equal(out["Bass@front"], stems["Bass@front"])


# ---------------------------------------------------------------------------
# All REBALANCE_PROFILES run without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_name", list(REBALANCE_PROFILES.keys()))
def test_all_profiles_run(profile_name):
    stems = _stems()
    r = StemRebalancer(REBALANCE_PROFILES[profile_name], 44100)
    out = r.process(stems)
    for arr in out.values():
        assert np.all(np.isfinite(arr)), f"Non-finite in profile {profile_name}"
