"""Tests for the scene-position -> speaker-gain routing in
`upmixer_web.features.projects.routing`.

Covers the azimuth-wraparound fix: BL/TBL sit at +135 deg and BR/TBR at
-135 deg, so a rear-positioned stem (azimuth near +-180 deg) must still
route to both rear speakers, not collapse onto one side.
"""

import math

import pytest

pytest.importorskip("fastapi")

from upmixer.config import UpmixConfig
from upmixer_web.features.projects.routing import routing_for_scene


def _scene(azimuth_deg: float, elevation_deg: float) -> dict:
    return {"stems": {"Vocals": {"azimuth_deg": azimuth_deg, "elevation_deg": elevation_deg}}}


def test_rear_center_routes_to_both_rear_channels():
    config = UpmixConfig(output_format="7.1.4")
    routing = routing_for_scene(_scene(180.0, 0.0), config)["Vocals"]
    assert routing["BL"] > 0.0
    assert routing["BR"] > 0.0


def test_rear_routing_is_left_right_symmetric():
    config = UpmixConfig(output_format="7.1.4")
    left = routing_for_scene(_scene(175.0, 0.0), config)["Vocals"]
    right = routing_for_scene(_scene(-175.0, 0.0), config)["Vocals"]
    assert left["BL"] == pytest.approx(right["BR"], abs=1e-9)
    assert left["BR"] == pytest.approx(right["BL"], abs=1e-9)
    assert left.get("TBL", 0.0) == pytest.approx(right.get("TBR", 0.0), abs=1e-9)
    assert left.get("TBR", 0.0) == pytest.approx(right.get("TBL", 0.0), abs=1e-9)


@pytest.mark.parametrize("elevation_deg", [0.0, 35.0])
def test_routing_is_constant_power_at_every_azimuth(elevation_deg):
    config = UpmixConfig(output_format="7.1.4")
    for azimuth_deg in range(-180, 181, 15):
        routing = routing_for_scene(_scene(float(azimuth_deg), elevation_deg), config)["Vocals"]
        power = math.sqrt(sum(weight * weight for weight in routing.values()))
        assert power == pytest.approx(1.0, abs=1e-6)


def test_out_of_range_azimuth_wraps():
    config = UpmixConfig(output_format="7.1.4")
    wrapped = routing_for_scene(_scene(225.0, 0.0), config)["Vocals"]
    reference = routing_for_scene(_scene(-135.0, 0.0), config)["Vocals"]
    assert wrapped == pytest.approx(reference, abs=1e-9)


def test_positioned_stem_gets_the_default_lfe_send():
    config = UpmixConfig(output_format="7.1.4")
    scene = {"stems": {"Bass": {"azimuth_deg": 0.0, "elevation_deg": 0.0}}}
    routing = routing_for_scene(scene, config)["Bass"]
    assert routing["LFE"] > 0.0


def test_positioned_stem_honours_an_explicit_manifest_lfe_send():
    config = UpmixConfig(output_format="7.1.4", stem_routing={"Bass": {"LFE": 0.42}})
    scene = {"stems": {"Bass": {"azimuth_deg": 0.0, "elevation_deg": 0.0}}}
    routing = routing_for_scene(scene, config)["Bass"]
    assert routing["LFE"] == pytest.approx(0.42)
