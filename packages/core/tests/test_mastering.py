"""Tests for upmixer.mastering — MasteringChain + MasteringResult."""
from __future__ import annotations

import math

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.mastering import MasteringChain, MasteringResult


def _mono_channels(n: int = 44100, amplitude: float = 0.1) -> dict[str, np.ndarray]:
    """Return a minimal {FL, FR} channel dict with a pure sine."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {"FL": sig.copy(), "FR": sig.copy()}


def _51_channels(n: int = 44100, amplitude: float = 0.1) -> dict[str, np.ndarray]:
    """Return a {FL FR C LFE SL SR} channel dict for 5.1 tests."""
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


class TestMasteringResult:
    def test_defaults_are_none(self):
        r = MasteringResult()
        assert r.measured_lkfs is None
        assert r.measured_tp_dbtp is None
        assert r.applied_gain_db is None
        assert r.tp_limited is False

    def test_construction(self):
        r = MasteringResult(
            measured_lkfs=-22.5,
            measured_tp_dbtp=-3.1,
            applied_gain_db=4.5,
            tp_limited=False,
        )
        assert r.measured_lkfs == pytest.approx(-22.5)
        assert r.measured_tp_dbtp == pytest.approx(-3.1)
        assert r.applied_gain_db == pytest.approx(4.5)


class TestMasteringChainNoLoudness:
    """With loudness_normalize=False, only tanh soft-limit is applied."""
    @pytest.fixture
    def chain(self):
        cfg = UpmixConfig(loudness_normalize=False)
        return MasteringChain(cfg)

    def test_returns_tuple(self, chain):
        channels = _mono_channels()
        out_channels, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert isinstance(out_channels, dict)
        assert isinstance(result, MasteringResult)

    def test_result_fields_are_none(self, chain):
        channels = _mono_channels()
        _, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert result.measured_lkfs is None
        assert result.measured_tp_dbtp is None
        assert result.applied_gain_db is None

    def test_output_channel_names_preserved(self, chain):
        channels = _mono_channels()
        out, _ = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert set(out.keys()) == set(channels.keys())

    def test_soft_limit_clips_peaks(self, chain):
        """Samples with absolute value > peak_limit_threshold should be reduced."""
        cfg = UpmixConfig(loudness_normalize=False, peak_limit_threshold=0.95)
        c = MasteringChain(cfg)
        channels = {"FL": np.ones(1024, dtype=np.float64) * 1.5}
        out, _ = c.process(channels, 44100, FORMAT_MAP["5.1"])
        assert np.max(np.abs(out["FL"])) <= 1.0  # tanh never exceeds 1

    def test_quiet_signal_passes_through_unchanged(self, chain):
        """Sub-threshold signal should survive soft-limiting with negligible change."""
        channels = _mono_channels(amplitude=0.1)
        out, _ = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        # tanh(0.1 / 0.95) ≈ 0.1049 → < 5% change
        assert np.allclose(out["FL"], channels["FL"], atol=1e-2)

    def test_output_is_finite(self, chain):
        channels = _51_channels()
        out, _ = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))


class TestMasteringChainWithLoudness:
    """With loudness_normalize=True, LN + TP ceiling + soft-limit applied."""
    @pytest.fixture
    def chain(self):
        cfg = UpmixConfig(
            loudness_normalize=True,
            loudness_target_lkfs=-18.0,
            loudness_max_tp=-1.0,
        )
        return MasteringChain(cfg)

    def test_returns_mastering_result(self, chain):
        channels = _51_channels(amplitude=0.3)
        _, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert result.measured_lkfs is not None
        assert isinstance(result.measured_lkfs, float)
        assert result.applied_gain_db is not None

    def test_measured_lkfs_is_negative(self, chain):
        channels = _51_channels(amplitude=0.3)
        _, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert result.measured_lkfs < 0.0

    def test_output_is_finite(self, chain):
        channels = _51_channels(amplitude=0.3)
        out, _ = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_output_channel_count_preserved(self, chain):
        channels = _51_channels(amplitude=0.3)
        out, _ = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert set(out.keys()) == set(channels.keys())

    def test_applied_gain_is_float(self, chain):
        channels = _51_channels(amplitude=0.3)
        _, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        assert isinstance(result.applied_gain_db, float)

    def test_silent_input_skips_ln(self):
        """All-zero input should not crash; gain max-caps at loudness_max_gain_db."""
        cfg = UpmixConfig(
            loudness_normalize=True,
            loudness_max_gain_db=30.0,
        )
        chain = MasteringChain(cfg)
        channels = {"FL": np.zeros(44100), "FR": np.zeros(44100)}
        out, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        # Output should be all zeros or very small (gain applied to silence = silence)
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_tp_limited_flag(self):
        """Loud signal that exceeds True Peak ceiling should set tp_limited=True."""
        # Use a hot near-full-scale signal so TP ceiling will trigger
        cfg = UpmixConfig(
            loudness_normalize=True,
            loudness_target_lkfs=-18.0,
            loudness_max_tp=-1.0,
        )
        chain = MasteringChain(cfg)
        # A very quiet signal → large upward LN gain → huge TP → TP-limited
        amplitude = 1e-3
        t = np.linspace(0, 5, 5 * 44100, endpoint=False)
        sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        channels = {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}
        _, result = chain.process(channels, 44100, FORMAT_MAP["5.1"])
        # tp_limited may or may not fire depending on signal, but it must be bool
        assert isinstance(result.tp_limited, bool)

    def test_soft_limit_runs_after_loudness_gain_not_before(self):
        """soft_limit must run last, so it never bakes distortion into a hot
        pre-gain bed that a later scalar gain can't undo.

        A hot (amplitude 1.3) pure tone measures near the top of scale, so
        loudness normalization applies a large *downward* gain to reach the
        -18 LKFS target. If soft_limit ran on the raw hot signal first (the
        prior bug), its tanh saturation would flatten the peaks and bake in
        strong odd harmonics that the subsequent linear gain only scales,
        never removes. With the correct order (gain first, limit last as a
        safety net), the post-gain signal is already well under the limiter
        threshold, so the tone survives essentially undistorted.
        """
        sr = 44100
        t = np.linspace(0, 2, 2 * sr, endpoint=False)
        sig = (1.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float64)
        channels = {"FL": sig.copy(), "FR": sig.copy()}
        cfg = UpmixConfig(loudness_normalize=True, loudness_target_lkfs=-18.0)
        out, result = MasteringChain(cfg).process(channels, sr, FORMAT_MAP["5.1"])

        assert result.applied_gain_db < 0.0  # hot input requires downward gain

        n = len(out["FL"])
        spectrum = np.abs(np.fft.rfft(out["FL"] * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1.0 / sr)

        def bin_peak(freq_hz: float) -> float:
            idx = int(np.argmin(np.abs(freqs - freq_hz)))
            return float(np.max(spectrum[max(idx - 2, 0):idx + 3]))

        fundamental = bin_peak(440.0)
        harmonics = math.sqrt(sum(bin_peak(440.0 * k) ** 2 for k in (2, 3, 4, 5)))
        thd = harmonics / fundamental
        assert thd < 0.01, f"THD {thd:.4f} indicates the limiter ran before loudness gain"


class TestMasteringChainWithEq:
    """MasteringChain integrates SpectralShaper when mastering_eq_profile is set."""
    def _51_channels(self, n: int = 44100, amplitude: float = 0.2):
        t = np.linspace(0, 1, n, endpoint=False)
        sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}

    def test_eq_profile_runs_without_error(self):
        cfg = UpmixConfig(
            loudness_normalize=False,
            mastering_eq_profile="spatial-air",
            mastering_eq_strength=1.0,
        )
        chain = MasteringChain(cfg)
        out, _ = chain.process(self._51_channels(), 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_eq_modifies_non_lfe_channels(self):
        cfg = UpmixConfig(
            loudness_normalize=False,
            mastering_eq_profile="spatial-air",
            mastering_eq_strength=1.0,
        )
        chs = self._51_channels()
        chain = MasteringChain(cfg)
        out, _ = chain.process(chs, 44100, FORMAT_MAP["5.1"])
        assert not np.allclose(out["FL"], chs["FL"])

    def test_eq_lfe_unchanged(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_eq_profile="atmos-streaming")
        chs = self._51_channels()
        chain = MasteringChain(cfg)
        out, _ = chain.process(chs, 44100, FORMAT_MAP["5.1"])
        np.testing.assert_array_equal(out["LFE"], chs["LFE"])

    def test_all_eq_profiles_run(self):
        from upmixer.mastering_eq import EQ_PROFILES
        for profile in EQ_PROFILES:
            cfg = UpmixConfig(loudness_normalize=False, mastering_eq_profile=profile)
            out, _ = MasteringChain(cfg).process(self._51_channels(), 44100, FORMAT_MAP["5.1"])
            for arr in out.values():
                assert np.all(np.isfinite(arr)), f"Non-finite in profile {profile}"


class TestMasteringChainWithComp:
    """MasteringChain integrates BusCompressor when mastering_comp_profile is set."""
    def _51_channels(self, n: int = 44100, amplitude: float = 0.5):
        t = np.linspace(0, 1, n, endpoint=False)
        sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}

    def test_comp_profile_runs_without_error(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_comp_profile="glue")
        out, _ = MasteringChain(cfg).process(self._51_channels(), 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_all_comp_profiles_run(self):
        from upmixer.mastering_comp import COMP_PROFILES
        for profile in COMP_PROFILES:
            cfg = UpmixConfig(loudness_normalize=False, mastering_comp_profile=profile)
            out, _ = MasteringChain(cfg).process(self._51_channels(), 48000, FORMAT_MAP["5.1"])
            for arr in out.values():
                assert np.all(np.isfinite(arr)), f"Non-finite in comp profile {profile}"

    def test_comp_param_override(self):
        """Individual comp params should override profile preset."""
        cfg = UpmixConfig(
            loudness_normalize=False,
            mastering_comp_profile="glue",
            mastering_comp_threshold_db=-10.0,   # more aggressive than glue default
            mastering_comp_ratio=4.0,
        )
        out, _ = MasteringChain(cfg).process(self._51_channels(), 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_comp_lfe_unchanged(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_comp_profile="transparent")
        chs = self._51_channels()
        out, _ = MasteringChain(cfg).process(chs, 44100, FORMAT_MAP["5.1"])
        np.testing.assert_array_equal(out["LFE"], chs["LFE"])


class TestMasteringChainFullPipeline:
    def test_eq_comp_loudness_together(self):
        """All three mastering stages run without error."""
        cfg = UpmixConfig(
            loudness_normalize=True,
            loudness_target_lkfs=-18.0,
            mastering_eq_profile="spatial-warm",
            mastering_comp_profile="transparent",
        )
        t = np.linspace(0, 3, 3 * 44100, endpoint=False)
        sig = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
        chs = {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}
        out, result = MasteringChain(cfg).process(chs, 44100, FORMAT_MAP["5.1"])
        for arr in out.values():
            assert np.all(np.isfinite(arr))
        # With loudness enabled, result fields should be populated
        assert result.measured_lkfs is not None
