"""Tests for upmixer.mastering_eq — SpectralShaper + EQ_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.mastering.eq import EQ_PROFILES, EQ_PROFILE_NAMES, SpectralShaper, _build_fir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channels(n: int = 44100, amplitude: float = 0.2) -> dict[str, np.ndarray]:
    """Return a minimal 5.1 channel dict with a 440 Hz sine."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


# ---------------------------------------------------------------------------
# EQ_PROFILES sanity checks
# ---------------------------------------------------------------------------

class TestEqProfiles:
    def test_all_profiles_have_entries(self):
        for name, bps in EQ_PROFILES.items():
            assert len(bps) >= 2, f"Profile '{name}' has fewer than 2 breakpoints"

    def test_breakpoints_are_ascending(self):
        for name, bps in EQ_PROFILES.items():
            freqs = [f for f, _ in bps]
            assert freqs == sorted(freqs), f"Profile '{name}' breakpoints not ascending"

    def test_all_freqs_positive(self):
        for name, bps in EQ_PROFILES.items():
            for f, _ in bps:
                assert f > 0, f"Profile '{name}': non-positive frequency {f}"

    def test_profile_names_tuple(self):
        assert isinstance(EQ_PROFILE_NAMES, tuple)
        assert set(EQ_PROFILE_NAMES) == set(EQ_PROFILES.keys())


# ---------------------------------------------------------------------------
# _build_fir
# ---------------------------------------------------------------------------

class TestBuildFir:
    def test_returns_ndarray(self):
        ir = _build_fir("spatial-air", 44100, 1023)
        assert isinstance(ir, np.ndarray)

    def test_fir_length(self):
        # minimum_phase returns (n_taps // 2 + 1) length
        ir = _build_fir("spatial-air", 44100, 1023)
        assert len(ir) == 1023 // 2 + 1

    def test_fir_cached(self):
        ir1 = _build_fir("spatial-warm", 48000, 1023)
        ir2 = _build_fir("spatial-warm", 48000, 1023)
        assert ir1 is ir2  # same object (cached)

    def test_different_sample_rates_differ(self):
        ir1 = _build_fir("spatial-air", 44100, 1023)
        ir2 = _build_fir("spatial-air", 48000, 1023)
        assert not np.allclose(ir1, ir2)

    def test_all_profiles_build_at_48k(self):
        for name in EQ_PROFILES:
            ir = _build_fir(name, 48000, 1023)
            assert len(ir) > 0
            assert np.all(np.isfinite(ir))


# ---------------------------------------------------------------------------
# SpectralShaper construction
# ---------------------------------------------------------------------------

class TestSpectralShaperInit:
    def test_valid_profile_constructs(self):
        s = SpectralShaper("spatial-air", 1.0, 44100)
        assert s is not None

    def test_unknown_profile_raises_key_error(self):
        with pytest.raises(KeyError, match="not_a_profile"):
            SpectralShaper("not_a_profile", 1.0, 44100)

    def test_strength_clamped_below(self):
        s = SpectralShaper("spatial-warm", -0.5, 44100)
        assert s._strength == 0.0

    def test_strength_clamped_above(self):
        s = SpectralShaper("spatial-warm", 1.5, 44100)
        assert s._strength == 1.0


# ---------------------------------------------------------------------------
# SpectralShaper.process — bypass / identity
# ---------------------------------------------------------------------------

class TestSpectralShaperBypass:
    def test_strength_zero_returns_original(self):
        """strength=0 must be an exact identity — no copy, no processing."""
        chs = _channels()
        out = SpectralShaper("spatial-air", 0.0, 44100).process(chs)
        assert out is chs  # same dict object (bypass shortcut)

    def test_lfe_always_unchanged(self):
        chs = _channels()
        lfe_orig = chs["LFE"].copy()
        out = SpectralShaper("spatial-air", 1.0, 44100).process(chs)
        np.testing.assert_array_equal(out["LFE"], lfe_orig)

    def test_lfe_unchanged_with_custom_key(self):
        chs = {"FL": np.ones(1024), "SUB": np.ones(1024) * 0.5}
        sub_orig = chs["SUB"].copy()
        out = SpectralShaper("spatial-air", 1.0, 44100).process(chs, lfe_key="SUB")
        np.testing.assert_array_equal(out["SUB"], sub_orig)


# ---------------------------------------------------------------------------
# SpectralShaper.process — filtering
# ---------------------------------------------------------------------------

class TestSpectralShaperProcess:
    @pytest.fixture(params=list(EQ_PROFILES.keys()))
    def profile(self, request):
        return request.param

    def test_output_shape_preserved(self, profile):
        chs = _channels(n=22050)
        out = SpectralShaper(profile, 1.0, 44100).process(chs)
        for name, arr in out.items():
            assert arr.shape == chs[name].shape, f"Shape mismatch for channel {name}"

    def test_output_is_finite(self, profile):
        chs = _channels()
        out = SpectralShaper(profile, 1.0, 44100).process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr)), f"Non-finite values in channel {name}"

    def test_all_channel_keys_preserved(self, profile):
        chs = _channels()
        out = SpectralShaper(profile, 1.0, 44100).process(chs)
        assert set(out.keys()) == set(chs.keys())

    def test_strength_one_modifies_non_lfe(self):
        """Full-strength processing should change the signal (except LFE)."""
        chs = _channels()
        out = SpectralShaper("spatial-air", 1.0, 44100).process(chs)
        # spatial-air has gains ≠ 0 dB at HF → signal should differ
        assert not np.allclose(out["FL"], chs["FL"]), "FL unchanged at strength=1.0"

    def test_strength_partial_between_dry_and_wet(self):
        """At strength=0.5, output should differ from both dry (s=0) and wet (s=1)."""
        chs = _channels()
        dry = chs["FL"].copy()
        wet = SpectralShaper("spatial-air", 1.0, 44100).process(chs)["FL"]
        half = SpectralShaper("spatial-air", 0.5, 44100).process(chs)["FL"]
        # Not equal to dry
        assert not np.allclose(half, dry)
        # Not equal to wet
        assert not np.allclose(half, wet)

    def test_spatial_transparent_near_identity(self):
        """spatial-transparent gains are all 0 dB — output ≈ input."""
        chs = _channels()
        out = SpectralShaper("spatial-transparent", 1.0, 44100).process(chs)
        for name in ["FL", "FR"]:
            # Allow small difference due to filter edge effects
            max_diff = float(np.max(np.abs(out[name] - chs[name])))
            assert max_diff < 0.02, f"{name} max diff={max_diff:.4f} for transparent profile"

    def test_48k_sample_rate(self):
        """Filter should work at 48 kHz without error."""
        chs = _channels(n=48000)
        out = SpectralShaper("atmos-streaming", 1.0, 48000).process(chs)
        assert np.all(np.isfinite(out["FL"]))
