"""Regression tests for stem spatial routing and content analysis."""
from __future__ import annotations

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_analyzer import StemFeatures, analyze_stem
from upmixer.separation.stem_router import StemRouter


def _audio(n: int = 48000, frequency: float = 440.0) -> np.ndarray:
    t = np.arange(n) / 48000
    signal = 0.2 * np.sin(2.0 * np.pi * frequency * t)
    return np.column_stack([signal, signal])


def _router(**kwargs: object) -> StemRouter:
    config = UpmixConfig(output_format="7.1.4", **kwargs)
    return StemRouter(config, FORMAT_MAP["7.1.4"], 48000)


def _features() -> StemFeatures:
    return StemFeatures(0.4, 0.3, 0.2, 0.3)


def test_channel_class_controls_change_stem_output():
    stems = {"Other": _audio()}
    features = {"Other": _features()}
    quiet = _router(surround_gain=0.1, height_gain=0.1).route(
        stems, len(stems["Other"]), stem_features=features
    )
    loud = _router(surround_gain=1.0, height_gain=1.0).route(
        stems, len(stems["Other"]), stem_features=features
    )

    assert np.sum(loud["SL"] ** 2) > np.sum(quiet["SL"] ** 2)
    assert np.sum(loud["TFL"] ** 2) > np.sum(quiet["TFL"] ** 2)


def test_content_mix_strength_disables_content_scaling():
    stems = {"Vocals": _audio()}
    flat = _router(content_mix_strength=0.0).route(stems, len(stems["Vocals"]))
    dynamic = _router(content_mix_strength=1.0).route(
        stems, len(stems["Vocals"]), stem_features={"Vocals": _features()}
    )

    assert not np.allclose(flat["C"], dynamic["C"])


def test_surround_send_removes_low_frequency_direct_copy():
    stems = {"Other": _audio(frequency=80.0)}
    channels = _router().route(
        stems, len(stems["Other"]), stem_features={"Other": _features()}
    )

    assert np.sqrt(np.mean(channels["SL"] ** 2)) < np.sqrt(np.mean(channels["FL"] ** 2))


def test_main_bed_routing_is_constant_power():
    stems = {"Vocals": _audio()}
    channels = _router().route(
        stems, len(stems["Vocals"]), stem_features={"Vocals": _features()}
    )
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


def test_analyzer_treats_antiphase_and_hard_pan_as_wide():
    signal = _audio()[:, 0]
    antiphase = analyze_stem(np.column_stack([signal, -signal]), 48000)
    hard_left = analyze_stem(np.column_stack([signal, np.zeros_like(signal)]), 48000)

    assert antiphase.stereo_width > 0.9
    assert hard_left.stereo_width > 0.9
