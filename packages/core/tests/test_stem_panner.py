"""Regression tests for the MDAP placement panner."""
from __future__ import annotations

import pytest

from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
from upmixer.formats import FORMAT_MAP, ChannelLabel
from upmixer.separation.stem_placement import MINIMUM_SEND, StemPlacement, placement_route

_LAYOUTS = ("stereo", "5.1", "5.1.2", "5.1.4", "7.1", "7.1.2", "7.1.4")


def _positional(route: dict[str, float]) -> dict[str, float]:
    return {channel: gain for channel, gain in route.items() if channel != ChannelLabel.LFE.value}


def _power(route: dict[str, float]) -> float:
    return sum(gain * gain for gain in _positional(route).values())


def test_point_placement_stays_on_one_simplex() -> None:
    route = placement_route(StemPlacement(45.0, 20.0, 0.0, 0.0), FORMAT_MAP["7.1.4"])

    assert set(route) == {"FL", "SL", "TFL"}
    assert _power(route) == pytest.approx(1.0)


def test_placement_on_a_speaker_resolves_to_that_speaker() -> None:
    output_format = FORMAT_MAP["7.1.4"]

    for label in output_format.channels:
        if label not in SPEAKER_AZIMUTH_ELEVATION:
            continue
        position = SPEAKER_AZIMUTH_ELEVATION[label]
        route = placement_route(
            StemPlacement(position.azimuth_deg, position.elevation_deg, 0.0, 0.0), output_format
        )
        assert route == {label.value: pytest.approx(1.0)}, f"{label.value} did not resolve to itself"


def test_spread_widens_a_point_without_moving_it() -> None:
    narrow = placement_route(StemPlacement(0.0, 0.0, 0.0, 0.0), FORMAT_MAP["7.1.4"])
    wide = placement_route(StemPlacement(0.0, 0.0, 0.0, 60.0), FORMAT_MAP["7.1.4"])

    assert set(narrow) == {"C"}
    assert {"FL", "FR"} <= set(wide)
    assert wide["C"] > max(wide["FL"], wide["FR"])
    assert wide["FL"] == pytest.approx(wide["FR"])


@pytest.mark.parametrize("layout", [layout for layout in _LAYOUTS if layout != "stereo"])
def test_azimuth_sweep_has_no_gain_jumps(layout: str) -> None:
    """A dragged placement crossfades between speakers; it never switches."""
    output_format = FORMAT_MAP[layout]
    channels = [label.value for label in output_format.channels]

    for elevation in (0.0, 20.0, 35.0):
        previous = None
        for step in range(721):
            azimuth = -180.0 + step * 0.5
            route = placement_route(
                StemPlacement(azimuth, elevation, 0.0, 60.0), output_format
            )
            current = [route.get(channel, 0.0) for channel in channels]
            if previous is not None:
                jump = max(abs(now - before) for now, before in zip(current, previous))
                assert jump < 0.05, f"{layout} at {azimuth}°/{elevation}° jumps {jump:.3f}"
            previous = current


def test_elevation_outside_the_layout_is_clamped_onto_it() -> None:
    output_format = FORMAT_MAP["7.1.4"]
    highest = max(
        SPEAKER_AZIMUTH_ELEVATION[label].elevation_deg
        for label in output_format.channels
        if label in SPEAKER_AZIMUTH_ELEVATION
    )

    overhead = placement_route(StemPlacement(0.0, 90.0, 0.0, 0.0), output_format)
    assert overhead == placement_route(StemPlacement(0.0, highest, 0.0, 0.0), output_format)

    below = placement_route(StemPlacement(0.0, -30.0, 0.0, 0.0), output_format)
    assert below == placement_route(StemPlacement(0.0, 0.0, 0.0, 0.0), output_format)


def test_direction_the_layout_cannot_reach_projects_onto_its_edge() -> None:
    behind_51 = placement_route(StemPlacement(180.0, 0.0, 0.0, 0.0), FORMAT_MAP["5.1"])
    assert set(behind_51) == {"SL", "SR"}
    assert behind_51["SL"] == pytest.approx(behind_51["SR"])

    behind_stereo = placement_route(StemPlacement(180.0, 0.0, 0.0, 0.0), FORMAT_MAP["stereo"])
    assert behind_stereo["FL"] > 0.0
    assert behind_stereo["FL"] == pytest.approx(behind_stereo["FR"])


@pytest.mark.parametrize("layout", _LAYOUTS)
def test_every_send_clears_the_floor_and_stays_constant_power(layout: str) -> None:
    output_format = FORMAT_MAP[layout]
    placement = StemPlacement(30.0, 12.0, 90.0, 60.0, lfe=0.4)

    route = placement_route(placement, output_format)

    assert all(gain > MINIMUM_SEND for gain in _positional(route).values())
    assert _power(route) == pytest.approx(1.0)
    if ChannelLabel.LFE in output_format.channels:
        assert route[ChannelLabel.LFE.value] == 0.4
    else:
        assert ChannelLabel.LFE.value not in route


def test_a_floor_placement_is_not_lifted_into_the_height_layer() -> None:
    route = placement_route(StemPlacement(0.0, 0.0, 120.0, 60.0), FORMAT_MAP["7.1.4"])

    assert not [channel for channel in route if channel.startswith("T")]
