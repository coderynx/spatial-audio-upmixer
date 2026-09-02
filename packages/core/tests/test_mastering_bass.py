"""Tests for upmixer.mastering.bass — BassController + BASS_PROFILES."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.mastering.bass import (
    BASS_PROFILES,
    BASS_PROFILE_NAMES,
    LF_SPREADS,
    LFE_MODES,
    BassController,
    resolve_lf_targets,
)

LFE_AUTHORING_GAIN = 0.31622776601683794
LFE_REPLAY_GAIN = 10.0 ** (10.0 / 20.0)


def _channels_51(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in ["FL", "FR", "C", "LFE", "SL", "SR"]}


def _channels_714(n: int = 44100, amplitude: float = 0.3) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = amplitude * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {k: sig.copy() for k in
            ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"]}


def _bass_in_front(n: int = 96000, sample_rate: int = 48000) -> dict[str, np.ndarray]:
    """A stereo source's low end as the router leaves it: front pair only."""
    t = np.arange(n) / sample_rate
    bass = (0.5 * np.sin(2 * np.pi * 40 * t)).astype(np.float64)
    silence = np.zeros(n, dtype=np.float64)
    return {
        "FL": bass.copy(), "FR": bass.copy(), "C": silence.copy(), "LFE": silence.copy(),
        "SL": silence.copy(), "SR": silence.copy(), "BL": silence.copy(), "BR": silence.copy(),
    }


def _make_bc(**kwargs) -> BassController:
    defaults = dict(
        sub_gain_db=0.0, mid_gain_db=0.0, unify_hz=None, spread="bed",
        punch=0.0, excite=False, lfe_mode="off", lfe_send=0.0, lfe_gain_db=0.0,
        decorrelate=0.0, lfe_authoring_gain=LFE_AUTHORING_GAIN, sample_rate=44100,
    )
    defaults.update(kwargs)
    return BassController(**defaults)


class TestBassProfiles:
    def test_all_profiles_have_required_keys(self):
        required = {"sub_gain_db", "mid_gain_db", "unify_hz", "spread", "punch",
                    "excite", "lfe_mode", "lfe_send", "lfe_gain_db", "decorrelate"}
        for name, p in BASS_PROFILES.items():
            assert required <= set(p.keys()), f"Profile '{name}' missing keys"

    def test_profile_names_tuple(self):
        assert isinstance(BASS_PROFILE_NAMES, tuple)
        assert set(BASS_PROFILE_NAMES) == set(BASS_PROFILES.keys())

    def test_profile_spreads_and_modes_are_valid(self):
        for name, p in BASS_PROFILES.items():
            assert p["spread"] in LF_SPREADS, f"Profile '{name}' has an unknown spread"
            assert p["lfe_mode"] in LFE_MODES, f"Profile '{name}' has an unknown LFE mode"

    def test_deep_is_the_unified_multichannel_preset(self):
        p = BASS_PROFILES["deep"]
        assert p["unify_hz"] is not None
        assert p["spread"] == "bed"
        assert p["excite"] is True
        assert p["punch"] > 0.0

    def test_cinema_splits_into_the_lfe(self):
        assert BASS_PROFILES["cinema"]["lfe_mode"] == "split"
        assert BASS_PROFILES["cinema"]["lfe_send"] > 0.0


class TestResolveLfTargets:
    def test_weights_sum_to_one_without_an_lfe_send(self):
        names = list(_channels_51())
        targets = resolve_lf_targets(names, "bed", "off", 0.5, LFE_AUTHORING_GAIN)
        assert sum(w for _, w in targets) == pytest.approx(1.0)

    def test_only_layout_channels_are_targeted(self):
        targets = resolve_lf_targets(["FL", "FR"], "all", "off", 0.0, LFE_AUTHORING_GAIN)
        assert [i for i, _ in targets] == [0, 1]

    def test_add_leaves_the_bed_at_full_weight(self):
        names = list(_channels_51())
        targets = resolve_lf_targets(names, "bed", "add", 0.25, LFE_AUTHORING_GAIN)
        lfe = names.index("LFE")
        bed = [w for i, w in targets if i != lfe]
        assert sum(bed) == pytest.approx(1.0)
        assert dict(targets)[lfe] == pytest.approx(0.25 * LFE_AUTHORING_GAIN)

    def test_split_conserves_the_sum_through_the_replay_gain(self):
        names = list(_channels_51())
        targets = resolve_lf_targets(names, "bed", "split", 0.5, LFE_AUTHORING_GAIN)
        lfe = names.index("LFE")
        played = sum(
            w * LFE_REPLAY_GAIN if i == lfe else w
            for i, w in targets
        )
        assert played == pytest.approx(1.0)

    def test_a_layout_without_an_lfe_ignores_the_send(self):
        targets = resolve_lf_targets(["FL", "FR"], "front", "split", 0.5, LFE_AUTHORING_GAIN)
        assert sum(w for _, w in targets) == pytest.approx(1.0)

    def test_unknown_spread_raises(self):
        with pytest.raises(KeyError):
            resolve_lf_targets(["FL", "FR"], "nowhere", "off", 0.0, LFE_AUTHORING_GAIN)

    def test_unknown_lfe_mode_raises(self):
        with pytest.raises(ValueError):
            resolve_lf_targets(["FL", "FR"], "bed", "sideways", 0.0, LFE_AUTHORING_GAIN)


class TestBassControllerInit:
    def test_constructs_with_defaults(self):
        assert _make_bc() is not None

    def test_zero_params_no_error(self):
        out = _make_bc().process(_channels_51())
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_unify_cutoff_is_clamped_to_the_lfe_band(self):
        """Above 120 Hz the bus would carry content the LFE cannot."""
        chs = _bass_in_front()
        low = _make_bc(unify_hz=400.0, sample_rate=48000).process(chs)
        clamped = _make_bc(unify_hz=120.0, sample_rate=48000).process(chs)
        np.testing.assert_allclose(low["FL"], clamped["FL"], atol=1e-12)


class TestBassControllerBypass:
    def test_all_zero_passes_through(self):
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
        ratio = float(np.max(np.abs(out["LFE"]))) / (float(np.max(np.abs(lfe_orig))) + 1e-20)
        assert ratio == pytest.approx(2.0, rel=0.01), "6 dB LFE gain not applied correctly"

    def test_lfe_cut_applied(self):
        chs = _channels_51()
        out = _make_bc(lfe_gain_db=-6.0).process(chs)
        rms_in = float(np.sqrt(np.mean(chs["LFE"] ** 2)))
        rms_out = float(np.sqrt(np.mean(out["LFE"] ** 2)))
        assert rms_out < rms_in

    def test_custom_lfe_key(self):
        chs = {k: np.ones(1024) * 0.3 for k in ["FL", "FR", "SUB"]}
        sub_orig = chs["SUB"].copy()
        out = _make_bc(sub_gain_db=3.0).process(chs, lfe_key="SUB")
        np.testing.assert_array_equal(out["SUB"], sub_orig)


class TestBassControllerEQ:
    def test_sub_boost_increases_rms(self):
        chs = _bass_in_front()
        out = _make_bc(sub_gain_db=6.0, sample_rate=48000).process(chs)
        assert np.sum(out["FL"] ** 2) > np.sum(chs["FL"] ** 2) * 3.0

    def test_output_finite_with_all_stages(self):
        chs = _channels_714()
        bc = _make_bc(
            sub_gain_db=2.0, mid_gain_db=1.0, unify_hz=90.0, spread="all",
            punch=0.3, excite=True, lfe_mode="add", lfe_send=0.3, lfe_gain_db=1.5,
        )
        out = bc.process(chs)
        for name, arr in out.items():
            assert np.all(np.isfinite(arr)), f"Non-finite in {name}"


class TestLfUnify:
    """The invariant the whole stage rests on: redistribution must move the
    low end around the array without changing its coherent level."""

    def _bed_sum(self, channels: dict[str, np.ndarray]) -> np.ndarray:
        return sum(ch for name, ch in channels.items() if name != "LFE")

    def test_the_coherent_low_end_is_preserved(self):
        chs = _bass_in_front()
        before = self._bed_sum(chs)
        out = _make_bc(unify_hz=90.0, spread="bed", sample_rate=48000).process(chs)
        after = self._bed_sum(out)
        residual = float(np.sum((after[24000:] - before[24000:]) ** 2))
        assert residual < float(np.sum(before[24000:] ** 2)) * 1e-6

    def test_the_low_end_reaches_the_whole_bed(self):
        chs = _bass_in_front()
        out = _make_bc(unify_hz=90.0, spread="bed", sample_rate=48000).process(chs)
        for name in ["C", "SL", "SR", "BL", "BR"]:
            assert np.sum(out[name] ** 2) > 0.0, f"{name} got no low end"
        assert np.sum(out["FL"] ** 2) < np.sum(chs["FL"] ** 2) * 0.5

    def test_front_spread_keeps_the_surrounds_high_passed(self):
        chs = _bass_in_front()
        out = _make_bc(unify_hz=90.0, spread="front", sample_rate=48000).process(chs)
        for name in ["C", "SL", "SR", "BL", "BR"]:
            np.testing.assert_allclose(out[name], 0.0, atol=1e-12)

    def test_a_stereo_layout_is_a_no_op(self):
        """`bed` resolves to {FL, FR} at 1/2 each, which is what was there."""
        n = 48000
        t = np.arange(n) / 48000
        bass = (0.5 * np.sin(2 * np.pi * 40 * t)).astype(np.float64)
        chs = {"FL": bass.copy(), "FR": bass.copy()}
        out = _make_bc(unify_hz=90.0, spread="bed", sample_rate=48000).process(chs)
        np.testing.assert_allclose(out["FL"], chs["FL"], atol=1e-9)

    def test_add_leaves_the_mains_untouched(self):
        chs = _bass_in_front()
        without = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        with_send = _make_bc(
            unify_hz=90.0, lfe_mode="add", lfe_send=0.3, sample_rate=48000
        ).process(chs)
        for name in ["FL", "FR", "C", "SL", "SR"]:
            np.testing.assert_array_equal(without[name], with_send[name])
        assert np.sum(with_send["LFE"] ** 2) > 0.0

    def test_split_conserves_the_low_end_through_the_replay_gain(self):
        chs = _bass_in_front()
        mains = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        split = _make_bc(
            unify_hz=90.0, lfe_mode="split", lfe_send=0.5, sample_rate=48000
        ).process(chs)
        reference = self._bed_sum(mains) + mains["LFE"] * LFE_REPLAY_GAIN
        played = self._bed_sum(split) + split["LFE"] * LFE_REPLAY_GAIN
        np.testing.assert_allclose(played, reference, atol=1e-9)

    def test_unification_commutes_with_a_shared_upstream_gain(self):
        """EQ and reference matching apply one shared curve to every bed
        channel — which is what lets bass control ignore them."""
        chs = _bass_in_front()
        gain = 1.7
        scaled = {name: ch * gain for name, ch in chs.items()}
        first = _make_bc(unify_hz=90.0, sample_rate=48000).process(scaled)
        after = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        for name in chs:
            np.testing.assert_allclose(first[name], after[name] * gain, atol=1e-9)

    def test_out_of_phase_bass_collapses(self):
        n = 96000
        t = np.arange(n) / 48000
        bass = (0.5 * np.sin(2 * np.pi * 40 * t)).astype(np.float64)
        chs = {"FL": bass.copy(), "FR": -bass.copy()}
        out = _make_bc(unify_hz=90.0, spread="front", sample_rate=48000).process(chs)
        assert np.sum(out["FL"][24000:] ** 2) < np.sum(bass[24000:] ** 2) * 0.02

    def test_output_finite_on_a_full_immersive_bed(self):
        out = _make_bc(unify_hz=90.0, spread="all").process(_channels_714())
        for arr in out.values():
            assert np.all(np.isfinite(arr))


class TestBassPunch:
    def _burst(self, n: int = 96000, sample_rate: int = 48000) -> dict[str, np.ndarray]:
        t = np.arange(n) / sample_rate
        tone = (0.5 * np.sin(2 * np.pi * 40 * t)).astype(np.float64)
        tone[n // 3:] *= 0.2
        return {"FL": tone.copy(), "FR": tone.copy()}

    def _attack_to_sustain(self, ch: np.ndarray, n: int) -> float:
        attack = float(np.sum(ch[4800:n // 3] ** 2))
        sustain = float(np.sum(ch[n // 3 + 9600:] ** 2))
        return attack / max(sustain, 1e-20)

    def test_zero_punch_is_a_bypass(self):
        chs = self._burst()
        flat = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        explicit = _make_bc(unify_hz=90.0, punch=0.0, sample_rate=48000).process(chs)
        np.testing.assert_array_equal(flat["FL"], explicit["FL"])

    def test_positive_punch_favours_the_attack(self):
        chs = self._burst()
        n = len(chs["FL"])
        flat = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        shaped = _make_bc(unify_hz=90.0, punch=0.5, sample_rate=48000).process(chs)
        assert (
            self._attack_to_sustain(shaped["FL"], n)
            > self._attack_to_sustain(flat["FL"], n) * 1.05
        )

    def test_negative_punch_densifies(self):
        chs = self._burst()
        n = len(chs["FL"])
        flat = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        smooth = _make_bc(unify_hz=90.0, punch=-0.5, sample_rate=48000).process(chs)
        assert (
            self._attack_to_sustain(smooth["FL"], n)
            < self._attack_to_sustain(flat["FL"], n)
        )


class TestBassExciter:
    def test_exciter_needs_unification(self):
        """The exciter runs on the LF bus, so it is inert without one."""
        chs = _channels_51()
        out = _make_bc(excite=True).process(chs)
        for name in chs:
            np.testing.assert_allclose(out[name], chs[name], atol=1e-6)

    def test_exciter_output_finite(self):
        out = _make_bc(unify_hz=90.0, excite=True).process(_channels_51())
        for arr in out.values():
            assert np.all(np.isfinite(arr))

    def test_exciter_adds_energy_to_the_bed(self):
        chs = _bass_in_front()
        plain = _make_bc(unify_hz=90.0, sample_rate=48000).process(chs)
        excited = _make_bc(unify_hz=90.0, excite=True, sample_rate=48000).process(chs)
        assert np.sum(excited["FL"] ** 2) > np.sum(plain["FL"] ** 2)

    def test_harmonics_scales_the_exciter_blend(self):
        chs = _bass_in_front()
        args = dict(unify_hz=90.0, sample_rate=48000)
        plain = _make_bc(**args).process(chs)
        half = _make_bc(harmonics=0.5, **args).process(chs)
        full = _make_bc(harmonics=1.0, **args).process(chs)
        np.testing.assert_allclose(
            half["FL"] - plain["FL"],
            0.5 * (full["FL"] - plain["FL"]),
            atol=1e-12,
        )

    def test_exciter_stays_out_of_the_lfe(self):
        """tanh's harmonics land above the 120 Hz the LFE is limited to."""
        chs = _bass_in_front()
        args = dict(unify_hz=90.0, lfe_mode="add", lfe_send=0.3, sample_rate=48000)
        plain = _make_bc(**args).process(chs)
        excited = _make_bc(excite=True, **args).process(chs)
        np.testing.assert_array_equal(plain["LFE"], excited["LFE"])

    def test_harmonics_alone_uses_the_safe_unification_default(self):
        from upmixer.config import UpmixConfig
        from upmixer.formats import FORMAT_MAP
        from upmixer.mastering.chain import MasteringChain

        chs = _bass_in_front(n=48000)
        fmt = FORMAT_MAP["7.1"]
        base = dict(mastering_bass_harmonics=0.5, loudness_normalize=False)
        automatic, _ = MasteringChain(UpmixConfig(**base)).process(dict(chs), 48000, fmt)
        explicit, _ = MasteringChain(
            UpmixConfig(**base, mastering_bass_unify_hz=90.0)
        ).process(dict(chs), 48000, fmt)
        for name in chs:
            np.testing.assert_array_equal(automatic[name], explicit[name])
        assert not np.array_equal(automatic["FL"], chs["FL"])

    def test_explicit_module_bypass_beats_legacy_values(self):
        from upmixer.config import UpmixConfig
        from upmixer.formats import FORMAT_MAP
        from upmixer.mastering.chain import MasteringChain

        chs = _bass_in_front(n=48000)
        fmt = FORMAT_MAP["7.1"]
        disabled = UpmixConfig(
            mastering_bass_enabled=False,
            mastering_bass_profile="deep",
            mastering_bass_harmonics=1.0,
            loudness_normalize=False,
        )
        out, _ = MasteringChain(disabled).process(dict(chs), 48000, fmt)
        for name in chs:
            np.testing.assert_array_equal(out[name], chs[name])


@pytest.mark.parametrize("profile_name", list(BASS_PROFILES.keys()))
def test_all_profiles_run(profile_name):
    p = BASS_PROFILES[profile_name]
    bc = _make_bc(**{k: p[k] for k in p})
    chs = _channels_51()
    out = bc.process(chs)
    for name, arr in out.items():
        assert np.all(np.isfinite(arr)), f"Non-finite in profile {profile_name}, {name}"


class TestExciteOverrideParity:
    """`excite` is nullable so the UI switch can force it off. A plain bool
    made "unset" and "explicitly off" the same value, so the export kept a
    profile's exciter running while the preview turned it off."""

    def _resolved(self, excite):
        from upmixer.config import UpmixConfig
        cfg = UpmixConfig(mastering_bass_profile="deep", mastering_bass_excite=excite)
        preset = BASS_PROFILES["deep"]
        val = cfg.mastering_bass_excite
        return val if val is not None else preset["excite"]

    def test_unset_takes_the_profile(self):
        assert BASS_PROFILES["deep"]["excite"] is True
        assert self._resolved(None) is True

    def test_explicitly_off_beats_a_profile_that_wants_it_on(self):
        assert self._resolved(False) is False

    def test_explicitly_on_is_kept(self):
        assert self._resolved(True) is True

    def test_the_exciter_actually_changes_the_output(self, ):
        chs = _bass_in_front()
        args = dict(unify_hz=90.0, sample_rate=48000)
        off = _make_bc(excite=False, **args).process(chs)
        on = _make_bc(excite=True, **args).process(chs)
        assert np.sum(on["FL"] ** 2) > np.sum(off["FL"] ** 2)


class TestBassDecorrelate:
    """The 100-300 Hz band, spread across channels by an allpass cascade.

    Everything below ``unify_hz`` is deliberately untouched — that band is
    mono by design and carries the Sigma-a = 1 invariant.
    """

    def _mid_bass_bed(self, n=96000, sample_rate=48000):
        # Noise already confined to the band, so total energy is in-band
        # energy. A single tone would only probe one frequency, where any
        # blend of a signal with a phase-rotated copy of itself combs.
        from scipy.signal import butter, sosfiltfilt

        rng = np.random.default_rng(7)
        sos = butter(4, [120 / (sample_rate / 2), 280 / (sample_rate / 2)],
                     "bandpass", output="sos")
        band = sosfiltfilt(sos, rng.standard_normal(n)) * 0.3
        return {name: band.copy() for name in ("FL", "FR", "C", "SL", "SR")}

    def test_zero_is_a_bypass(self):
        chs = self._mid_bass_bed()
        out = _make_bc(decorrelate=0.0, sample_rate=48000).process(chs)
        for name in chs:
            assert np.array_equal(out[name], chs[name])

    def test_channels_diverge_from_each_other(self):
        chs = self._mid_bass_bed()
        out = _make_bc(decorrelate=1.0, sample_rate=48000).process(chs)
        settled = slice(24000, None)
        assert not np.allclose(out["FL"][settled], out["FR"][settled])
        assert not np.allclose(out["FL"][settled], out["SL"][settled])

    def test_each_channel_keeps_its_own_level(self):
        chs = self._mid_bass_bed()
        out = _make_bc(decorrelate=1.0, sample_rate=48000).process(chs)
        settled = slice(24000, None)
        # The cascade is unity-magnitude and the blend is constant-power, so
        # the band holds its level. Under a dB is the claim — decorrelation
        # must not become a gain control.
        for name in chs:
            ratio = np.sum(out[name][settled] ** 2) / np.sum(chs[name][settled] ** 2)
            assert abs(10 * np.log10(ratio)) < 1.0, f"{name} level moved by {ratio}"

    def test_the_level_holds_at_partial_depth_too(self):
        # Where a linear blend would fail: it averages (1-w)**2 + w**2 of the
        # power, since the band and its rotated copy are decorrelated.
        chs = self._mid_bass_bed()
        settled = slice(24000, None)
        for depth in (0.25, 0.5, 0.7):
            out = _make_bc(decorrelate=depth, sample_rate=48000).process(dict(chs))
            ratio = np.sum(out["FL"][settled] ** 2) / np.sum(chs["FL"][settled] ** 2)
            assert abs(10 * np.log10(ratio)) < 1.0, f"depth {depth} moved by {ratio}"

    def test_the_coherent_sum_drops(self):
        chs = self._mid_bass_bed()
        out = _make_bc(decorrelate=1.0, sample_rate=48000).process(chs)
        settled = slice(24000, None)
        before = sum(chs[name][settled] for name in chs)
        after = sum(out[name][settled] for name in chs)
        assert np.sum(after ** 2) < np.sum(before ** 2) * 0.9

    def test_the_unified_sub_band_survives_intact(self):
        # Unified at 90 Hz with a 40 Hz source. The decorrelator's band starts
        # at 100 Hz, so what reaches the mono bus is only the band-pass
        # skirt — bounded, not zero, the same way any soft crossover leaks.
        from scipy.signal import butter, sosfiltfilt

        chs = _bass_in_front()
        args = dict(unify_hz=90.0, spread="bed", sample_rate=48000)
        plain = _make_bc(decorrelate=0.0, **args).process(chs)
        spread = _make_bc(decorrelate=1.0, **args).process(chs)

        sos = butter(2, 90 / 24000, "low", output="sos")
        settled = slice(24000, None)
        low_plain = sosfiltfilt(sos, sum(plain.values()))[settled]
        low_spread = sosfiltfilt(sos, sum(spread.values()))[settled]
        moved = np.sum((low_spread - low_plain) ** 2) / np.sum(low_plain ** 2)
        assert moved < 1e-6, f"the mono bus moved by {moved}"

    def test_output_stays_finite(self):
        chs = self._mid_bass_bed()
        out = _make_bc(decorrelate=1.0, unify_hz=90.0, sample_rate=48000).process(chs)
        for name in out:
            assert np.all(np.isfinite(out[name])), name

    def test_the_config_param_reaches_the_chain(self):
        from upmixer.config import UpmixConfig
        from upmixer.mastering.chain import MasteringChain
        from upmixer.formats import FORMAT_MAP

        cfg = UpmixConfig(mastering_bass_decorrelate=0.5, loudness_normalize=False)
        chs = self._mid_bass_bed(n=48000)
        fmt = FORMAT_MAP["5.1"]
        bed = {ch.value: chs.get(ch.value, np.zeros(48000)) for ch in fmt.channels}
        out, _ = MasteringChain(cfg).process(dict(bed), 48000, fmt)
        settled = slice(24000, None)
        assert not np.allclose(out["FL"][settled], bed["FL"][settled])
        assert not np.allclose(out["FL"][settled], out["FR"][settled])
