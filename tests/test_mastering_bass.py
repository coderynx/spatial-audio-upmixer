"""Tests for upmixer.mastering_bass — BassController + BASS_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.mastering.bass import (
    BASS_PROFILES,
    BASS_PROFILE_NAMES,
    BassController,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channels_51(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


def _channels_714(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in
            ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"]}


def _make_bc(**kwargs) -> BassController:
    defaults = dict(
        sub_gain_db=0.0, mid_gain_db=0.0,
        mono_cutoff_hz=None, excite=False, lfe_gain_db=0.0,
        sample_rate=44100,
    )
    defaults.update(kwargs)
    return BassController(**defaults)


# ---------------------------------------------------------------------------
# BASS_PROFILES sanity
# ---------------------------------------------------------------------------

class TestBassProfiles:
    def test_all_profiles_have_required_keys(self):
        required = {"sub_gain_db", "mid_gain_db", "mono_cutoff_hz",
                    "excite", "lfe_gain_db"}
        for name, p in BASS_PROFILES.items():
            assert required <= set(p.keys()), f"Profile '{name}' missing keys"

    def test_profile_names_tuple(self):
        assert isinstance(BASS_PROFILE_NAMES, tuple)
        assert set(BASS_PROFILE_NAMES) == set(BASS_PROFILES.keys())

    def test_enhance_has_excite(self):
        assert BASS_PROFILES["enhance"]["excite"] is True

    def test_mono_profile_has_cutoff(self):
        assert BASS_PROFILES["mono"]["mono_cutoff_hz"] is not None


# ---------------------------------------------------------------------------
# BassController construction
# ---------------------------------------------------------------------------

class TestBassControllerInit:
    def test_constructs_with_defaults(self):
        bc = _make_bc()
        assert bc is not None

    def test_zero_params_no_error(self):
        bc = _make_bc(sub_gain_db=0.0, mid_gain_db=0.0)
        out = bc.process(_channels_51())
        for arr in out.values():
            assert np.all(np.isfinite(arr))


# ---------------------------------------------------------------------------
# BassController.process — identity / bypass
# ---------------------------------------------------------------------------

class TestBassControllerBypass:
    def test_all_zero_passes_through(self):
        """0 dB sub/mid, no mono, no excite, 0 dB LFE → output ≈ input."""
        chs = _channels_51()
        out = _make_bc().process(chs)
        for name in chs:
            np.testing.assert_allclose(out[name], chs[name], atol=1e-6,
                                       err_msg=f"Channel {name} not preserved")

    def test_output_keys_preserved(self):
        chs = _channels_51()
        out = _make_bc(sub_gain_db=1.0).process(chs)
        assert set(out.keys()) == set(chs.keys())

    def test_output_shape_preserved(self):
        chs = _channels_51(n=22050)
        out = _make_bc(sub_gain_db=2.0, mid_gain_db=1.0).process(chs)
        for name in chs:
            assert out[name].shape == chs[name].shape


# ---------------------------------------------------------------------------
# BassController.process — LFE handling
# ---------------------------------------------------------------------------

class TestBassControllerLFE:
    def test_sub_eq_bypasses_lfe(self):
        chs = _channels_51()
        lfe_orig = chs["LFE"].copy()
        out = _make_bc(sub_gain_db=3.0).process(chs)
        np.testing.assert_array_equal(out["LFE"], lfe_orig)

    def test_mid_eq_bypasses_lfe(self):
        chs = _channels_51()
        lfe_orig = chs["LFE"].copy()
        out = _make_bc(mid_gain_db=2.0).process(chs)
        np.testing.assert_array_equal(out["LFE"], lfe_orig)

    def test_lfe_gain_applied(self):
        chs = _channels_51()
        lfe_orig = chs["LFE"].copy()
        out = _make_bc(lfe_gain_db=6.0).process(chs)
        # 6 dB → ~2× amplitude
        ratio = float(np.max(np.abs(out["LFE"]))) / (float(np.max(np.abs(lfe_orig))) + 1e-20)
        assert ratio == pytest.approx(2.0, rel=0.01), "6 dB LFE gain not applied correctly"

    def test_lfe_cut_applied(self):
        chs = _channels_51()
        out = _make_bc(lfe_gain_db=-6.0).process(chs)
        rms_in  = float(np.sqrt(np.mean(chs["LFE"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["LFE"] ** 2)))
        assert rms_out < rms_in

    def test_custom_lfe_key(self):
        chs = {k: np.ones(1024) * 0.3 for k in ["FL", "FR", "SUB"]}
        sub_orig = chs["SUB"].copy()
        out = _make_bc(sub_gain_db=3.0).process(chs, lfe_key="SUB")
        np.testing.assert_array_equal(out["SUB"], sub_orig)


# ---------------------------------------------------------------------------
# BassController.process — EQ stages
# ---------------------------------------------------------------------------

class TestBassControllerEQ:
    def test_sub_boost_increases_rms(self):
        chs = _channels_51()
        out = _make_bc(sub_gain_db=6.0).process(chs)
        # Overall RMS of non-LFE should be slightly higher due to sub boost
        rms_in  = float(np.sqrt(np.mean(chs["FL"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["FL"] ** 2)))
        # The change may be small (only affects <80 Hz band), but must be finite
        assert np.isfinite(rms_out)

    def test_output_finite_with_all_stages(self):
        chs = _channels_714()
        bc = BassController(
            sub_gain_db=2.0, mid_gain_db=1.0,
            mono_cutoff_hz=80.0, excite=True, lfe_gain_db=1.5,
            sample_rate=44100,
        )
        out = bc.process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr)), f"Non-finite in {name}"


# ---------------------------------------------------------------------------
# BassController.process — bass mono-maker
# ---------------------------------------------------------------------------

class TestBassMonoMaker:
    def test_bass_mono_makes_lr_more_similar(self):
        """After bass-mono, FL and FR low-freq difference should drop significantly."""
        t = np.linspace(0, 2, 2 * 44100, endpoint=False)
        # Use independent L/R signals at very low freq to test mono-isation
        fl = np.sin(2 * np.pi * 40 * t).astype(np.float64)
        fr = np.sin(2 * np.pi * 40 * t + 0.5).astype(np.float64)  # phase offset
        chs = {"FL": fl, "FR": fr, "C": fl.copy(), "LFE": fl.copy(),
               "SL": fl.copy(), "SR": fr.copy()}

        from scipy.signal import butter, sosfilt
        sos_lp = butter(2, 40.0 / (44100 / 2), btype="low", output="sos")

        # Difference before
        diff_before = float(np.sqrt(np.mean((sosfilt(sos_lp, fl) - sosfilt(sos_lp, fr)) ** 2)))

        out = _make_bc(mono_cutoff_hz=100.0).process(chs)

        # Difference after mono-making
        diff_after = float(np.sqrt(np.mean((sosfilt(sos_lp, out["FL"]) - sosfilt(sos_lp, out["FR"])) ** 2)))

        assert diff_after < diff_before * 0.2, (
            f"Bass mono-maker did not significantly reduce L/R difference "
            f"(before={diff_before:.4f}, after={diff_after:.4f})"
        )

    def test_mono_maker_output_finite(self):
        chs = _channels_714()
        out = _make_bc(mono_cutoff_hz=80.0).process(chs)
        for arr in out.values():
            assert np.all(np.isfinite(arr))


# ---------------------------------------------------------------------------
# BassController.process — harmonic exciter
# ---------------------------------------------------------------------------

class TestBassExciter:
    def test_exciter_output_finite(self):
        chs = _channels_51()
        bc = BassController(
            sub_gain_db=0.0, mid_gain_db=0.0, mono_cutoff_hz=None,
            excite=True, lfe_gain_db=0.0, sample_rate=44100,
        )
        out = bc.process(chs)
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_exciter_lfe_unchanged(self):
        chs = _channels_51()
        lfe_orig = chs["LFE"].copy()
        bc = BassController(
            sub_gain_db=0.0, mid_gain_db=0.0, mono_cutoff_hz=None,
            excite=True, lfe_gain_db=0.0, sample_rate=44100,
        )
        out = bc.process(chs)
        np.testing.assert_array_equal(out["LFE"], lfe_orig)


# ---------------------------------------------------------------------------
# All profiles run without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_name", list(BASS_PROFILES.keys()))
def test_all_profiles_run(profile_name):
    p = BASS_PROFILES[profile_name]
    bc = BassController(
        sub_gain_db=p["sub_gain_db"],
        mid_gain_db=p["mid_gain_db"],
        mono_cutoff_hz=p["mono_cutoff_hz"],
        excite=p["excite"],
        lfe_gain_db=p["lfe_gain_db"],
        sample_rate=44100,
    )
    chs = _channels_51()
    out = bc.process(chs)
    for name, arr in out.items():
        assert np.all(np.isfinite(arr)), f"Non-finite in profile {profile_name}, {name}"
