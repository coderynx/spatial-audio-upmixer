"""Regression tests for routing-preset stem placements and their panner."""
from __future__ import annotations

import pytest

from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_placement import (
    STEM_ROUTING_PRESET_NAMES,
    STEM_ROUTING_PRESET_TREATMENTS,
    StemPlacement,
    placement_route,
    preset_routing,
    resolve_placements,
)

_HEIGHT = {"TFL", "TFR", "TBL", "TBR"}
_FRONT = {"FL", "FR", "C"}
_CORE_STEMS = ("Lead Vocals", "Vocals", "Kick", "Snare", "Bass")


def _energy(route: dict[str, float], channels: set[str]) -> float:
    return sum(gain * gain for channel, gain in route.items() if channel in channels)


@pytest.mark.parametrize("preset", STEM_ROUTING_PRESET_NAMES)
@pytest.mark.parametrize("layout", list(FORMAT_MAP))
def test_every_preset_resolves_onto_every_layout(preset: str, layout: str) -> None:
    output_format = FORMAT_MAP[layout]
    allowed = {label.value for label in output_format.channels}

    for stem, route in preset_routing(preset, output_format).items():
        assert route, f"{preset}/{layout}: {stem} routes nowhere"
        assert set(route) <= allowed, f"{preset}/{layout}: {stem} reaches a missing channel"
        assert all(gain >= 0.0 for gain in route.values())


@pytest.mark.parametrize("preset", STEM_ROUTING_PRESET_NAMES)
def test_core_stems_stay_on_the_front_wall(preset: str) -> None:
    routing = preset_routing(preset, FORMAT_MAP["7.1.4"])

    for stem in _CORE_STEMS:
        route = routing[stem]
        assert _energy(route, _FRONT) > _energy(route, _HEIGHT), f"{preset}: {stem} lifted overhead"
        assert not _energy(route, {"BL", "BR"}), f"{preset}: {stem} sent behind the listener"


@pytest.mark.parametrize("preset", STEM_ROUTING_PRESET_NAMES)
def test_each_preset_places_stems_differently_per_layout(preset: str) -> None:
    assert resolve_placements(preset, "5.1") != resolve_placements(preset, "7.1.4")


def test_layout_without_height_flattens_elevation_outward() -> None:
    elevated = [
        stem
        for stem, placement in resolve_placements("immersive", "7.1.4").items()
        if placement.elevation_deg > 0.0
    ]
    assert elevated

    for stem in elevated:
        flattened = resolve_placements("immersive", "7.1")[stem]
        raised = resolve_placements("immersive", "7.1.4")[stem]
        assert flattened.elevation_deg == 0.0
        assert flattened.width_deg > raised.width_deg


def test_elevated_placement_reaches_height_only_where_the_layout_has_it() -> None:
    placement = StemPlacement(0.0, 30.0, 60.0, 60.0)

    assert _energy(placement_route(placement, FORMAT_MAP["5.1.4"]), _HEIGHT) > 0.0
    assert not _energy(placement_route(placement, FORMAT_MAP["5.1"]), _HEIGHT)


def test_rear_placement_falls_back_to_the_rearmost_pair() -> None:
    placement = StemPlacement(180.0, 0.0, 120.0, 80.0)

    assert placement_route(placement, FORMAT_MAP["7.1.4"])["BL"] > 0.0
    on_51 = placement_route(placement, FORMAT_MAP["5.1"])
    assert on_51["SL"] > 0.0
    assert on_51["SL"] == pytest.approx(on_51["SR"])


def test_width_controls_how_many_speakers_a_placement_covers() -> None:
    narrow = placement_route(StemPlacement(0.0, 0.0, 0.0, 0.0), FORMAT_MAP["7.1.4"])
    wide = placement_route(StemPlacement(0.0, 0.0, 120.0, 70.0), FORMAT_MAP["7.1.4"])

    assert narrow == {"C": pytest.approx(1.0)}
    assert len(wide) > len(narrow)


def test_preset_routes_are_nonnegative() -> None:
    for preset in STEM_ROUTING_PRESET_NAMES:
        for stem, route in preset_routing(preset, FORMAT_MAP["7.1.4"]).items():
            assert all(gain >= 0.0 for gain in route.values()), f"{preset}/{stem} has a negative gain"


def test_presets_define_stem_appropriate_bed_controls() -> None:
    for treatments in STEM_ROUTING_PRESET_TREATMENTS.values():
        for treatment in treatments.values():
            placement = treatment.placement
            assert 0.0 <= placement.diversity <= 1.0
            assert -6.0 <= placement.center_level_db <= 2.0
        assert treatments["Lead Vocals"].placement.center_level_db > treatments["Crowd"].placement.center_level_db

    assert (
        STEM_ROUTING_PRESET_TREATMENTS["intimate"]["Other"].placement.diversity
        < STEM_ROUTING_PRESET_TREATMENTS["wide"]["Other"].placement.diversity
    )


def test_unknown_preset_or_layout_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown stem routing preset"):
        resolve_placements("spacious", "7.1.4")
    with pytest.raises(ValueError, match="Unknown channel layout"):
        resolve_placements("balanced", "9.1.6")
