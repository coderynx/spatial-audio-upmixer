"""Tests for upmixer.separation.stem_eq — StemEQ + STEM_EQ_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.separation.stem_eq import (
    STEM_EQ_PROFILES,
    STEM_EQ_PROFILE_NAMES,
    StemEQ,
    _build_fir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stems(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {
        "Vocals": np.stack([sig.copy(), sig.copy()], axis=1),
        "Bass":   np.stack([sig.copy(), sig.copy()], axis=1),
        "Drums":  np.stack([sig.copy(), sig.copy()], axis=1),
        "Other":  np.stack([sig.copy(), sig.copy()], axis=1),
    }


def _stems_zoned(n: int = 44100) -> dict[str, np.ndarray]:
    base = _stems(n)
    return {f"{k}@front": v for k, v in base.items()}


# ---------------------------------------------------------------------------
# STEM_EQ_PROFILES sanity
# ---------------------------------------------------------------------------

class TestStemEqProfiles:
    def test_all_profiles_have_at_least_two_breakpoints(self):
        for name, bps in STEM_EQ_PROFILES.items():
            assert len(bps) >= 2, f"Profile '{name}' has fewer than 2 breakpoints"

    def test_breakpoints_ascending(self):
        for name, bps in STEM_EQ_PROFILES.items():
            freqs = [f for f, _ in bps]
            assert freqs == sorted(freqs), f"'{name}' breakpoints not ascending"

    def test_all_freqs_positive(self):
        for name, bps in STEM_EQ_PROFILES.items():
            for f, _ in bps:
                assert f > 0, f"'{name}': non-positive freq {f}"

    def test_profile_names_tuple(self):
        assert isinstance(STEM_EQ_PROFILE_NAMES, tuple)
        assert set(STEM_EQ_PROFILE_NAMES) == set(STEM_EQ_PROFILES.keys())

    def test_flat_profile_all_zero(self):
        for f, g in STEM_EQ_PROFILES["flat"]:
            assert g == pytest.approx(0.0), "flat profile has non-zero gain"


# ---------------------------------------------------------------------------
# _build_fir
# ---------------------------------------------------------------------------

class TestBuildFirStem:
    def test_returns_ndarray(self):
        ir = _build_fir("vocal-presence", 44100, 511)
        assert isinstance(ir, np.ndarray)

    def test_fir_length(self):
        # half=False preserves requested magnitude response and tap count
        # (matches upmixer/mastering/eq.py's identical choice).
        ir = _build_fir("drums-punch", 44100, 511)
        assert len(ir) == 511

    def test_cached(self):
        ir1 = _build_fir("bass-warmth", 48000, 511)
        ir2 = _build_fir("bass-warmth", 48000, 511)
        assert ir1 is ir2

    def test_different_sample_rates_differ(self):
        ir1 = _build_fir("vocal-presence", 44100, 511)
        ir2 = _build_fir("vocal-presence", 48000, 511)
        assert not np.allclose(ir1, ir2)

    def test_all_profiles_build_finite(self):
        for name in STEM_EQ_PROFILES:
            ir = _build_fir(name, 44100, 511)
            assert np.all(np.isfinite(ir)), f"Non-finite IR for '{name}'"

    def test_reaches_configured_breakpoint_gain(self):
        # Regression: minimum_phase's default half=True halves every
        # breakpoint's dB (magnitude becomes its own square root). Without
        # half=False, vocal-presence's +2.0dB@4kHz breakpoint only reached
        # ~+1.0dB in the actual filter response.
        from scipy.signal import freqz

        ir = _build_fir("vocal-presence", 48000, 511)
        w, h = freqz(ir, worN=8192, fs=48000)
        idx = int(np.argmin(np.abs(w - 4000)))
        gain_db = 20.0 * np.log10(np.abs(h[idx]))
        assert gain_db == pytest.approx(2.0, abs=0.2)


# ---------------------------------------------------------------------------
# StemEQ construction
# ---------------------------------------------------------------------------

class TestStemEqInit:
    def test_constructs_with_valid_profiles(self):
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        assert eq is not None

    def test_unknown_profile_raises_key_error(self):
        with pytest.raises(KeyError, match="not_a_profile"):
            StemEQ({"Vocals": "not_a_profile"}, 44100)

    def test_empty_profiles_dict(self):
        eq = StemEQ({}, 44100)
        assert eq is not None


# ---------------------------------------------------------------------------
# StemEQ.process — identity / bypass
# ---------------------------------------------------------------------------

class TestStemEqIdentity:
    def test_unaddressed_stem_returns_original(self):
        stems = _stems()
        eq = StemEQ({"Drums": "drums-punch"}, 44100)
        out = eq.process(stems)
        assert out["Vocals"] is stems["Vocals"]
        assert out["Bass"]   is stems["Bass"]

    def test_empty_profiles_all_original(self):
        stems = _stems()
        eq = StemEQ({}, 44100)
        out = eq.process(stems)
        for k in stems:
            assert out[k] is stems[k]

    def test_all_keys_present(self):
        stems = _stems()
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        out = eq.process(stems)
        assert set(out.keys()) == set(stems.keys())


# ---------------------------------------------------------------------------
# StemEQ.process — filtering
# ---------------------------------------------------------------------------

class TestStemEqFiltering:
    def test_output_shape_preserved(self):
        stems = _stems(n=22050)
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        out = eq.process(stems)
        for k, arr in out.items():
            assert arr.shape == stems[k].shape

    def test_output_finite(self):
        stems = _stems()
        eq = StemEQ(
            {"Vocals": "vocal-presence", "Bass": "bass-warmth",
             "Drums": "drums-punch", "Other": "other-air"},
            44100,
        )
        out = eq.process(stems)
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_addressed_stem_modified(self):
        stems = _stems()
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        out = eq.process(stems)
        assert not np.allclose(out["Vocals"], stems["Vocals"]), \
            "Vocal stem unchanged after EQ"

    def test_48k_sample_rate(self):
        t = np.linspace(0, 1, 48000, endpoint=False)
        sig = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        stems = {"Vocals": np.stack([sig, sig], axis=1)}
        eq = StemEQ({"Vocals": "vocal-warmth"}, 48000)
        out = eq.process(stems)
        assert np.all(np.isfinite(out["Vocals"]))

    def test_both_channels_filtered(self):
        """Both L and R channels of the stereo array should be modified."""
        stems = _stems()
        eq = StemEQ({"Vocals": "spatial-air" if "spatial-air" in STEM_EQ_PROFILES
                     else "vocal-presence"}, 44100)
        out = eq.process(stems)
        # Both channels should differ from dry
        assert not np.allclose(out["Vocals"][:, 0], stems["Vocals"][:, 0])
        assert not np.allclose(out["Vocals"][:, 1], stems["Vocals"][:, 1])


# ---------------------------------------------------------------------------
# Zone-tagged stems
# ---------------------------------------------------------------------------

class TestStemEqZone:
    def test_zone_suffix_stripped(self):
        stems = _stems_zoned()
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        out = eq.process(stems)
        assert not np.allclose(out["Vocals@front"], stems["Vocals@front"])

    def test_unaddressed_zone_unchanged(self):
        stems = _stems_zoned()
        eq = StemEQ({"Vocals": "vocal-presence"}, 44100)
        out = eq.process(stems)
        assert out["Bass@front"] is stems["Bass@front"]


# ---------------------------------------------------------------------------
# All profiles run without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_name", list(STEM_EQ_PROFILES.keys()))
def test_all_profiles_run(profile_name):
    stems = _stems()
    eq = StemEQ({"Vocals": profile_name}, 44100)
    out = eq.process(stems)
    for arr in out.values():
        assert np.all(np.isfinite(arr)), f"Non-finite in profile {profile_name}"
