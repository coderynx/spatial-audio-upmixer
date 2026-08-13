"""Regression tests for static stem spatial routing."""
from __future__ import annotations

import math

import numpy as np
import pytest
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_analyzer import analyze_stem
from upmixer.separation.stem_router import (
    DEFAULT_ROUTING,
    ZONE_ROUTING,
    StemRouter,
    apply_stem_pan,
    build_stem_routing,
    default_lfe_send,
    fold_route_to_stereo,
)
from upmixer.utils import ITU_CENTER_COEFF


def _audio(n: int = 48000, frequency: float = 440.0) -> np.ndarray:
    t = np.arange(n) / 48000
    signal = 0.2 * np.sin(2.0 * np.pi * frequency * t)
    return np.column_stack([signal, signal])


def _router(**kwargs: object) -> StemRouter:
    config = UpmixConfig(output_format="7.1.4", **kwargs)
    return StemRouter(config, FORMAT_MAP["7.1.4"], 48000)


def test_channel_class_controls_change_stem_output():
    stems = {"Other": _audio()}
    quiet = _router(surround_gain=0.1, height_gain=0.1).route(stems, len(stems["Other"]))
    loud = _router(surround_gain=1.0, height_gain=1.0).route(stems, len(stems["Other"]))

    assert np.sum(loud["SL"] ** 2) > np.sum(quiet["SL"] ** 2)
    assert np.sum(loud["TFL"] ** 2) > np.sum(quiet["TFL"] ** 2)


def test_manifest_routing_overrides_builtin_table():
    stems = {"Vocals": _audio()}
    router = _router(stem_routing={"Vocals": {"C": 0.0, "SL": 1.0}})
    channels = router.route(stems, len(stems["Vocals"]))

    assert np.max(np.abs(channels["C"])) == 0.0
    assert np.max(np.abs(channels["SL"])) > 0.0


def test_surround_send_removes_low_frequency_direct_copy():
    stems = {"Other": _audio(frequency=80.0)}
    channels = _router().route(stems, len(stems["Other"]))

    assert np.sqrt(np.mean(channels["SL"] ** 2)) < np.sqrt(np.mean(channels["FL"] ** 2))


def test_main_bed_routing_is_constant_power():
    stems = {"Vocals": _audio()}
    channels = _router().route(stems, len(stems["Vocals"]))
    input_energy = float(np.vdot(stems["Vocals"], stems["Vocals"]).real)
    bed_energy = sum(float(np.vdot(channels[name], channels[name]).real) for name in ("FL", "FR", "C", "TFL", "TFR"))

    np.testing.assert_approx_equal(bed_energy, input_energy, significant=5)


def test_custom_routing_overrides_zone_table():
    stems = {"Vocals@front": _audio()}
    router = StemRouter(
        UpmixConfig(output_format="5.1"),
        FORMAT_MAP["5.1"],
        48000,
        {"Vocals@front": {"C": 0.0, "SL": 1.0}},
    )
    channels = router.route(stems, len(stems["Vocals@front"]))

    assert np.max(np.abs(channels["C"])) == 0.0
    assert np.max(np.abs(channels["SL"])) > 0.0


def test_explicit_empty_zone_routing_does_not_fall_back_to_default():
    router = StemRouter(
        UpmixConfig(output_format="7.1.4"),
        FORMAT_MAP["7.1.4"],
        48000,
        {"CustomStem@height_front": {}},
    )

    assert router.get_routing("CustomStem@height_front") == {}


def test_bass_and_kick_reach_height_zones():
    router = _router()

    bass = router.get_routing("Bass@height_front")
    kick = router.get_routing("Kick@height_back")

    assert bass["FL"] > 0.0 and bass["LFE"] > 0.0
    assert kick["FL"] > 0.0 and kick["LFE"] > 0.0


def test_routing_preset_is_explicit_and_layout_aware():
    fmt = FORMAT_MAP["7.1.4"]
    balanced = build_stem_routing(["Other"], fmt)
    spacious = build_stem_routing(["Other"], fmt, "spacious")
    neutral = build_stem_routing(["Other"], fmt, "spacious", intensity=0.0)

    assert spacious["Other"]["SL"] > balanced["Other"]["SL"]
    assert spacious["Other"]["TFL"] > balanced["Other"]["TFL"]
    assert neutral == balanced
    assert "TFL" not in build_stem_routing(["Other"], FORMAT_MAP["5.1"])["Other"]


def test_stem_enabled_mutes_stem():
    stems = {"Vocals": _audio()}
    channels = _router(stem_enabled={"Vocals": False}).route(stems, len(stems["Vocals"]))

    assert all(np.max(np.abs(channel)) == 0.0 for channel in channels.values())


def test_stem_solo_routes_selected_stems_only():
    stems = {"Vocals": _audio(), "Bass": _audio(frequency=80.0)}
    channels = _router(stem_solo=["Vocals", "Bass"]).route(stems, len(stems["Vocals"]))

    assert np.max(np.abs(channels["C"])) > 0.0
    assert np.max(np.abs(channels["LFE"])) > 0.0


def test_default_lfe_gain_is_applied_once():
    stems = {"Bass": _audio(frequency=80.0)}
    config = UpmixConfig(output_format="7.1.4")
    router = StemRouter(
        config,
        FORMAT_MAP["7.1.4"],
        48000,
        {"Bass": {"LFE": 1.0}},
    )

    channels = router.route(stems, len(stems["Bass"]))
    stem_mono = stems["Bass"][:, 0]
    expected = config.lfe_gain * upmixer_dsp.lowpass(
        np.ascontiguousarray(stem_mono, dtype=np.float64),
        48000,
        config.lfe_cutoff_hz,
        config.lfe_filter_order,
    )

    np.testing.assert_allclose(channels["LFE"], expected)


def test_every_default_stem_has_an_explicit_lfe_send():
    for stem, route in DEFAULT_ROUTING.items():
        assert "LFE" in route, f"{stem} is missing an explicit LFE weight"


def test_stem_lfe_send_scales_the_lfe_bus():
    stems = {"Bass": _audio(frequency=80.0)}
    quiet = _router(stem_routing={"Bass": {"LFE": 0.2}}).route(stems, len(stems["Bass"]))
    loud = _router(stem_routing={"Bass": {"LFE": 0.8}}).route(stems, len(stems["Bass"]))

    assert np.sum(loud["LFE"] ** 2) > np.sum(quiet["LFE"] ** 2)


def test_zero_stem_lfe_send_silences_that_stem_in_the_lfe_bus():
    stems = {"Bass": _audio(frequency=80.0)}
    channels = _router(stem_routing={"Bass": {"LFE": 0.0}}).route(stems, len(stems["Bass"]))

    assert np.max(np.abs(channels["LFE"])) == 0.0


def test_default_lfe_send_resolves_zone_before_stem_name():
    assert default_lfe_send("Bass@height_front") == ZONE_ROUTING["height_front"]["Bass"]["LFE"]
    assert default_lfe_send("Bass") == DEFAULT_ROUTING["Bass"]["LFE"]
    assert default_lfe_send("Bass@unknown_zone") == DEFAULT_ROUTING["Bass"]["LFE"]
    assert default_lfe_send("Nonexistent") == 0.0


def test_generic_and_percussion_defaults_start_conservative():
    router = _router()
    other = router.get_routing("Other")
    hi_hat = router.get_routing("Hi-Hat")
    crash = router.get_routing("Crash")

    assert other is not None and other["FL"] > other["SL"] > other["TFL"]
    assert hi_hat is not None and hi_hat["TFL"] == 0.40
    assert crash is not None and crash["TFL"] == 0.50


def test_analyzer_treats_antiphase_and_hard_pan_as_wide():
    signal = _audio()[:, 0]
    antiphase = analyze_stem(np.column_stack([signal, -signal]), 48000)
    hard_left = analyze_stem(np.column_stack([signal, np.zeros_like(signal)]), 48000)

    assert antiphase.stereo_width > 0.9
    assert hard_left.stereo_width > 0.9


def test_fold_route_to_stereo_splits_center_and_drops_lfe():
    folded = fold_route_to_stereo({"C": 1.0, "LFE": 0.8, "SL": 0.5, "BR": 0.25})
    assert folded == {
        "FL": pytest.approx(ITU_CENTER_COEFF + 0.5),
        "FR": pytest.approx(ITU_CENTER_COEFF + 0.25),
    }
    assert fold_route_to_stereo(folded) == folded


def test_build_stem_routing_reaches_stereo_for_front_less_stems():
    routing = build_stem_routing(["Crowd", "Vocals"], FORMAT_MAP["stereo"])
    for stem in ("Crowd", "Vocals"):
        assert set(routing[stem]) == {"FL", "FR"}
        assert routing[stem]["FL"] > 0.0


def test_stereo_router_emits_two_channels_and_preserves_stem_energy():
    config = UpmixConfig(output_format="stereo")
    router = StemRouter(config, FORMAT_MAP["stereo"], 48000)
    audio = _audio()
    channels = router.route({"Vocals": audio}, len(audio))

    assert set(channels) == {"FL", "FR"}
    routed = float(np.dot(channels["FL"], channels["FL"]) + np.dot(channels["FR"], channels["FR"]))
    source = float(np.dot(audio[:, 0], audio[:, 0]) + np.dot(audio[:, 1], audio[:, 1]))
    assert routed == pytest.approx(source, rel=0.01)


def test_stereo_router_folds_zone_routes_that_have_no_front_send():
    config = UpmixConfig(output_format="stereo")
    router = StemRouter(config, FORMAT_MAP["stereo"], 48000)
    audio = _audio()
    channels = router.route({"Guitar@surround": audio}, len(audio))

    assert np.max(np.abs(channels["FL"])) > 0.0


def test_apply_stem_pan_is_constant_power_and_keeps_magnitude():
    route = {"FL": 0.6, "FR": 0.6, "LFE": 0.3}

    left = apply_stem_pan(route, 0.0)
    assert left["FL"] == pytest.approx(math.hypot(0.6, 0.6))
    assert left["FR"] == pytest.approx(0.0, abs=1e-12)
    assert left["LFE"] == 0.3

    right = apply_stem_pan(route, 1.0)
    assert right["FR"] == pytest.approx(math.hypot(0.6, 0.6))

    centre = apply_stem_pan(route, 0.5)
    assert centre["FL"] == pytest.approx(centre["FR"])
    for panned in (left, right, centre):
        assert math.hypot(panned["FL"], panned["FR"]) == pytest.approx(math.hypot(0.6, 0.6))


def test_apply_stem_pan_round_trips_through_the_inverse():
    for pan in (0.0, 0.25, 0.5, 0.75, 1.0):
        panned = apply_stem_pan({"FL": 1.0, "FR": 1.0}, pan)
        recovered = math.atan2(panned["FR"], panned["FL"]) / (math.pi / 2)
        assert recovered == pytest.approx(pan, abs=1e-9)
