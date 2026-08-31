"""Regression tests for static stem spatial routing."""
from __future__ import annotations

import math

import numpy as np
import pytest
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness
from upmixer.separation.stem_router import (
    DEFAULT_ROUTING,
    DEFAULT_ROUTING_LAYOUT,
    DEFAULT_ROUTING_PRESET,
    ZONE_ROUTING,
    StemRouter,
    apply_stem_pan,
    build_stem_routing,
    default_lfe_send,
    fold_route_to_stereo,
)
from upmixer.utils import ITU_CENTER_COEFF
from upmixer.utils import itu_downmix_stereo


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
    stems = {"Other": _audio()}
    router = _router(stem_routing={"Other": {"C": 0.0, "SL": 1.0}})
    channels = router.route(stems, len(stems["Other"]))

    assert np.max(np.abs(channels["C"])) == 0.0
    assert np.max(np.abs(channels["SL"])) > 0.0


def test_surround_send_removes_low_frequency_direct_copy():
    stems = {"Other": _audio(frequency=80.0)}
    channels = _router().route(stems, len(stems["Other"]))

    assert np.sqrt(np.mean(channels["SL"] ** 2)) < np.sqrt(np.mean(channels["FL"] ** 2))


def test_main_bed_routing_is_constant_power():
    stems = {"Other": _audio()}
    channels = _router().route(stems, len(stems["Other"]))
    input_energy = float(np.vdot(stems["Other"], stems["Other"]).real)
    bed_energy = sum(
        float(np.vdot(channels[name], channels[name]).real) for name in channels if name != "LFE"
    )

    # Not exact since phase 9: route_scale matches loudness, so a band-limited
    # send zone lands a little under its share of raw energy.
    np.testing.assert_allclose(bed_energy, input_energy, rtol=0.02)


def test_bed_trim_changes_beds_without_changing_objects():
    audio = _audio()
    gain = 10.0 ** (6.0 / 20.0)

    for stem in ("Bass", "Vocals"):
        plain = _router().route({stem: audio}, len(audio))
        trimmed = _router(bed_trim_db=6.0).route({stem: audio}, len(audio))
        expected_gain = gain if stem == "Bass" else 1.0
        for channel in plain:
            np.testing.assert_allclose(trimmed[channel], expected_gain * plain[channel])


def test_custom_routing_overrides_zone_table():
    stems = {"Other@front": _audio()}
    router = StemRouter(
        UpmixConfig(output_format="5.1"),
        FORMAT_MAP["5.1"],
        48000,
        {"Other@front": {"C": 0.0, "SL": 1.0}},
    )
    channels = router.route(stems, len(stems["Other@front"]))

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
    wide = build_stem_routing(["Other"], fmt, "wide")

    assert wide["Other"]["FL"] < balanced["Other"]["FL"]
    assert wide["Other"]["TFL"] > balanced["Other"]["TFL"]
    assert "TFL" not in build_stem_routing(["Other"], FORMAT_MAP["5.1"])["Other"]
    assert set(build_stem_routing(["Other"], FORMAT_MAP["stereo"])["Other"]) == {"FL", "FR"}


def test_unknown_routing_preset_is_rejected():
    with pytest.raises(ValueError, match="Unknown stem routing preset"):
        build_stem_routing(["Other"], FORMAT_MAP["7.1.4"], "spacious")


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
    expected = config.lfe_gain * upmixer_dsp.lfe_lowpass(
        np.ascontiguousarray(stem_mono, dtype=np.float64),
        48000,
        config.lfe_cutoff_hz,
        config.lfe_filter_order,
    )

    np.testing.assert_allclose(channels["LFE"], expected)


def test_default_routing_is_the_default_preset_on_the_widest_layout():
    assert DEFAULT_ROUTING == build_stem_routing(
        list(DEFAULT_ROUTING), FORMAT_MAP[DEFAULT_ROUTING_LAYOUT], DEFAULT_ROUTING_PRESET
    )
    for stem in ("Bass", "Kick", "Drums", "Toms", "Instrumental", "Other"):
        assert DEFAULT_ROUTING[stem]["LFE"] > 0.0, f"{stem} lost its LFE send"


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
    assert default_lfe_send("Snare") == 0.0
    assert default_lfe_send("Nonexistent") == 0.0


def test_generic_and_percussion_defaults_start_conservative():
    """The overhead ladder, as zone power: a placement's elevation is realized
    across the whole front-to-height arc, so no single channel pair states it."""
    router = _router()

    def zone(stem: str, channels: set[str]) -> float:
        route = router.get_routing(stem) or {}
        return sum(gain * gain for channel, gain in route.items() if channel in channels)

    front, height = {"FL", "FR", "C"}, {"TFL", "TFR", "TBL", "TBR"}

    assert zone("Other", front) > zone("Other", height) > zone("Other", {"SL", "SR"})
    assert zone("Hi-Hat", front) > zone("Hi-Hat", height)
    assert zone("Crash", height) > zone("Crash", front)
    assert zone("Crash", height) > zone("Hi-Hat", height)


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
    channels = router.route({"Other": audio}, len(audio))

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


def _noise(n: int = 48000, seed: int = 7) -> np.ndarray:
    mono = np.random.default_rng(seed).standard_normal(n) * 0.2
    return np.column_stack([mono, mono])


def test_send_pairs_survive_a_mono_fold_down():
    stems = {"Other": _noise()}
    channels = _router(stem_routing={"Other": {"SL": 0.7, "SR": 0.7, "TFL": 0.7, "TFR": 0.7}}).route(
        stems, len(stems["Other"])
    )

    for left, right in (("SL", "SR"), ("TFL", "TFR")):
        summed = channels[left] + channels[right]
        power_sum = float(np.dot(channels[left], channels[left])) + float(
            np.dot(channels[right], channels[right])
        )
        loss = 10.0 * math.log10(float(np.dot(summed, summed)) / power_sum)
        assert abs(loss) < 0.5, f"{left}+{right} fold-down moved {loss:.2f} dB"


def test_surround_and_height_sends_are_decorrelated_from_each_other():
    stems = {"Other": _noise()}
    channels = _router(stem_routing={"Other": {"SL": 0.7, "TFL": 0.7}}).route(
        stems, len(stems["Other"])
    )

    # Different zone seeds: without them a stem sent around and overhead would
    # arrive as the same signal from two directions.
    surround, height = channels["SL"], channels["TFL"]
    correlation = float(np.dot(surround, height)) / math.sqrt(
        float(np.dot(surround, surround)) * float(np.dot(height, height))
    )
    assert abs(correlation) < 0.2, f"surround/height correlation {correlation:.3f}"


def _noise(n: int = 48000) -> np.ndarray:
    signal = 0.2 * np.random.default_rng(20260817).standard_normal(n)
    return np.column_stack([signal, signal])


def test_surround_routed_stem_lands_at_the_same_loudness_as_a_front_one():
    """Phase 9: raw-energy matching left Crowd +3.86 LU over Lead Vocals."""
    fmt = FORMAT_MAP["7.1.4"]
    router = StemRouter(UpmixConfig(output_format="7.1.4"), fmt, 48000)
    audio = _noise()

    front = measure_integrated_loudness(
        router.route({"Other": audio}, len(audio)), 48000, fmt
    )
    wide = measure_integrated_loudness(
        router.route({"Crowd": audio}, len(audio)), 48000, fmt
    )

    assert abs(wide - front) < 0.25


def test_front_only_stereo_route_still_matches_raw_energy():
    """Regression anchor: plain stereo renders must not move (no send filters,
    unity BS.1770 weights, so the loudness scalar is the energy scalar)."""
    router = StemRouter(UpmixConfig(output_format="stereo"), FORMAT_MAP["stereo"], 48000)
    audio = _noise()
    channels = router.route({"Other": audio}, len(audio))

    routed = sum(float(np.dot(ch, ch)) for ch in channels.values())
    source = float(np.dot(audio[:, 0], audio[:, 0]) + np.dot(audio[:, 1], audio[:, 1]))
    assert routed == pytest.approx(source, rel=1e-6)


def _reverberant(n: int = 48000, seed: int = 7) -> np.ndarray:
    """A centred note with a decorrelated tail — the shape a separated stem
    has. Repeats every second, so a longer fixture is more of the same
    material rather than one event and a fade."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 48000
    note = 0.3 * np.sin(2.0 * np.pi * 440.0 * t) * ((t % 1.0) < 0.5)
    tail = 0.15 * np.exp(-1.5 * (t % 1.0))
    return np.column_stack([
        note + tail * rng.standard_normal(n),
        note + tail * rng.standard_normal(n),
    ])


def test_a_zero_ambient_send_leaves_the_route_untouched():
    stems = {"Other": _reverberant()}
    plain = _router().route(stems, len(stems["Other"]))
    zeroed = _router(
        stem_ambient_rear={"Other": 0.0}, stem_ambient_height={"Other": 0.0}
    ).route(stems, len(stems["Other"]))

    for channel, samples in plain.items():
        assert np.array_equal(samples, zeroed[channel]), channel


def test_an_ambient_send_reaches_surrounds_a_front_routed_stem_never_touches():
    stems = {"Other": _reverberant()}
    front_only = {"Other": {ch: 0.0 for ch in ("SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR")}}
    front_only["Other"].update({"FL": 1.0, "FR": 1.0})
    dry = _router(stem_routing=front_only).route(stems, len(stems["Other"]))
    sent = _router(stem_routing=front_only, stem_ambient_rear={"Other": 0.8}).route(
        stems, len(stems["Other"])
    )

    assert np.max(np.abs(dry["SL"])) == 0.0
    assert np.max(np.abs(sent["SL"])) > 0.0
    # Only the rear slider moved, so the heights stay where the routing left them.
    assert np.max(np.abs(sent["TFL"])) == 0.0


def test_the_ambient_send_takes_its_level_out_of_the_front():
    stems = {"Other": _reverberant()}
    routing = {"Other": {ch: 0.0 for ch in ("SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR")}}
    routing["Other"].update({"FL": 1.0, "FR": 1.0})
    dry = _router(stem_routing=routing).route(stems, len(stems["Other"]))
    sent = _router(stem_routing=routing, stem_ambient_rear={"Other": 0.9}).route(
        stems, len(stems["Other"])
    )

    # Both are route-scale normalized, so compare the front's share of the bed.
    front = lambda bed: np.sum(bed["FL"] ** 2) + np.sum(bed["FR"] ** 2)
    total = lambda bed: sum(np.sum(samples ** 2) for samples in bed.values())
    assert front(sent) / total(sent) < front(dry) / total(dry)


def test_an_ambient_send_is_ignored_by_a_layout_without_that_class():
    stems = {"Other": _reverberant()}
    config = UpmixConfig(output_format="stereo", stem_ambient_rear={"Other": 0.9})
    router = StemRouter(config, FORMAT_MAP["stereo"], 48000)
    sent = router.route(stems, len(stems["Other"]))

    plain = StemRouter(
        UpmixConfig(output_format="stereo"), FORMAT_MAP["stereo"], 48000
    ).route(stems, len(stems["Other"]))
    for channel, samples in plain.items():
        assert np.array_equal(samples, sent[channel]), channel


def test_the_zone_key_beats_the_stem_name_for_an_ambient_send():
    stems = {"Other@surround": _reverberant()}
    router = _router(
        stem_ambient_rear={"Other": 0.0, "Other@surround": 0.8},
    )
    assert router._ambient_for("Other@surround") == (0.8, 0.0)


def test_the_zone_key_beats_the_stem_name_for_an_ambient_height_crossover():
    router = _router(
        stem_ambient_height_crossover_hz={"Other": 4000.0, "Other@surround": 500.0},
    )
    assert router._ambient_height_crossover_for("Other@surround") == 500.0


def test_a_mono_stem_has_almost_no_ambient_half_to_send():
    """Coherence cannot tell a mono stem apart from its own dry signal, so the
    send carries only the mask floor. Stated here because it is a real limit of
    the stage, not a bug to chase later."""
    n = 48000
    t = np.arange(n) / 48000
    tone = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)
    mono = np.column_stack([tone, tone])
    rear_l, _, height_l, _ = upmixer_dsp.ambient_split(
        np.ascontiguousarray(mono[:, 0]), np.ascontiguousarray(mono[:, 1]), 48000
    )
    tail = slice(2048, n)
    ambient = np.mean(rear_l[tail] ** 2) + np.mean(height_l[tail] ** 2)
    assert ambient / np.mean(tone[tail] ** 2) < 0.05


def test_an_ambient_send_keeps_the_stem_at_its_own_loudness():
    """The sends move energy, they do not add it: the route normalization has
    to match the routed sum to the *unsplit* stem, or a stem gets quieter as
    its sends come up."""
    # Three seconds: one is short enough that BS.1770's gate, not the
    # routing, decides the difference between the two measurements.
    stems = {"Other": _reverberant(n=144_000)}
    fmt = FORMAT_MAP["7.1.4"]
    dry = _router().route(stems, len(stems["Other"]))
    sent = _router(
        stem_ambient_rear={"Other": 0.8}, stem_ambient_height={"Other": 0.8}
    ).route(stems, len(stems["Other"]))

    quiet = measure_integrated_loudness(dry, 48000, fmt)
    loud = measure_integrated_loudness(sent, 48000, fmt)
    # Not exact: the normalization matches K-weighted power per routed
    # signal, where this measures the gated loudness of the assembled bed.
    assert abs(loud - quiet) < 0.5, (quiet, loud)


@pytest.mark.parametrize("audio", [
    _audio(),
    np.column_stack([_audio()[:, 0], -_audio()[:, 0]]),
    _reverberant(),
])
def test_downmix_lock_restores_each_routed_stem_pair(audio: np.ndarray):
    config = UpmixConfig(
        output_format="7.1.4",
        spatial_downmix_lock=True,
        stem_routing={"Other": {"FL": 0.3, "FR": 0.2, "C": 0.4, "SL": 0.8, "SR": 0.8, "TFL": 0.7, "TFR": 0.7}},
        stem_ambient_rear={"Other": 0.8},
        stem_ambient_height={"Other": 0.8},
    )
    channels = StemRouter(config, FORMAT_MAP["7.1.4"], 48000).route({"Other": audio}, len(audio))
    left, right = itu_downmix_stereo(channels, config.surround_downmix_coeff, config.height_downmix_coeff)
    residual = np.column_stack([left - audio[:, 0], right - audio[:, 1]])
    peak = np.max(np.abs(residual))
    rms = np.sqrt(np.mean(residual ** 2))

    assert peak < 1e-9, f"fold residual peak={peak:.3e}, rms={rms:.3e}"


def test_downmix_lock_off_is_identical_to_the_existing_route():
    stems = {"Other": _reverberant()}
    plain = _router(stem_ambient_rear={"Other": 0.8}, stem_ambient_height={"Other": 0.8}).route(
        stems, len(stems["Other"])
    )
    locked_off = _router(
        spatial_downmix_lock=False,
        stem_ambient_rear={"Other": 0.8},
        stem_ambient_height={"Other": 0.8},
    ).route(stems, len(stems["Other"]))

    for channel in plain:
        assert np.array_equal(plain[channel], locked_off[channel])


def test_object_bed_routes_linked_feeds_to_placement_endpoints():
    audio = np.column_stack([_audio()[:, 0], -_audio()[:, 0]])
    config = UpmixConfig(
        output_format="7.1.4",
        stem_placement={"Vocals": {"azimuth_deg": 0, "elevation_deg": 0, "width_deg": 100, "object_size": 0}},
    )
    rendered = StemRouter(config, FORMAT_MAP["7.1.4"], 48000).route({"Vocals": audio}, len(audio))
    assert np.max(np.abs(rendered["FL"] - rendered["FR"])) > 1e-5


def test_correlated_object_feeds_keep_the_stem_energy():
    audio = _noise()
    config = UpmixConfig(
        output_format="7.1.4",
        stem_placement={"Vocals": {
            "azimuth_deg": 0,
            "elevation_deg": 0,
            "width_deg": 0,
            "object_size": 0,
        }},
    )
    rendered = StemRouter(config, FORMAT_MAP["7.1.4"], 48000).route(
        {"Vocals": audio}, len(audio)
    )

    source = float(np.vdot(audio, audio).real)
    routed = sum(float(np.vdot(channel, channel).real) for channel in rendered.values())
    assert routed == pytest.approx(source, rel=1e-6)


def test_stem_object_and_bed_classes_follow_the_delivery_table():
    router = _router()

    for stem in ("Crash", "Ride", "Hi-Hat", "Toms", "Guitar", "Piano", "Lead Vocals"):
        assert router._object_placement_for(stem) is not None
    for stem in ("Bass", "Kick", "Snare", "Other", "Crowd", "Backing Vocals", "Vocals Reverb"):
        assert router._object_placement_for(stem) is None


def test_object_bed_mono_mode_collapses_the_direct_feed():
    audio = np.column_stack([_audio()[:, 0], -_audio()[:, 0]])
    config = UpmixConfig(
        output_format="7.1.4",
        stem_object_mode={"Vocals": "mono"},
        stem_placement={"Vocals": {"azimuth_deg": 0, "elevation_deg": 0, "width_deg": 100, "object_size": 0}},
    )
    rendered = StemRouter(config, FORMAT_MAP["7.1.4"], 48000).route({"Vocals": audio}, len(audio))
    assert max(np.max(np.abs(channel)) for channel in rendered.values()) < 1e-10


def test_adm_objects_keep_the_dry_stem_out_of_the_shared_bed():
    audio = _reverberant()
    config = UpmixConfig(
        output_format="7.1.4",
        spatial_render_model="bed",
        stem_ambient_rear={"Vocals": 0.8},
    )
    objects = []
    bed = StemRouter(config, FORMAT_MAP["7.1.4"], 48000).route(
        {"Vocals": audio}, len(audio), object_tracks=objects,
    )

    assert len(objects) == 2
    assert np.max(np.abs(bed["FL"])) < 1e-10
    assert np.max(np.abs(bed["SL"])) > 1e-10


def test_adm_objects_carry_profile_rendering_metadata():
    audio = _audio()
    config = UpmixConfig(
        output_format="5.1",
        stem_object_metadata={
            "Vocals": {
                "gain": 0.5,
                "importance": 7,
                "channel_lock": True,
                "zone_exclusion": ["ZM1", "ZT"],
            },
        },
    )
    objects = []
    StemRouter(config, FORMAT_MAP["5.1"], 48_000).route(
        {"Vocals": audio}, len(audio), object_tracks=objects,
    )

    assert len(objects) == 2
    assert all(obj.gain == 0.5 for obj in objects)
    assert all(obj.importance == 7 for obj in objects)
    assert all(obj.channel_lock for obj in objects)
    assert all(obj.zone_exclusion == ("ZM1", "ZT") for obj in objects)
