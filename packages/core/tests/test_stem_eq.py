"""Tests for compact shared-DSP stem EQ."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.separation.stem_eq import StemEQ, default_stem_eq, resolve_stem_eq


def _tone(freq_hz: float, sample_rate: int = 48000) -> np.ndarray:
    t = np.arange(sample_rate) / sample_rate
    return np.sin(2 * np.pi * freq_hz * t)


def test_legacy_preset_loads_and_custom_round_trips() -> None:
    legacy = resolve_stem_eq("vocal-presence")
    assert legacy["bell_1"]["gain_db"] == 2.0
    custom = default_stem_eq()
    custom["bell_2"] = {"enabled": True, "freq_hz": 3000, "gain_db": -3, "q": 2}
    assert resolve_stem_eq(custom, 48000)["bell_2"]["q"] == 2.0


@pytest.mark.parametrize("field,value", [("gain_db", 6.1), ("q", 8.1), ("freq_hz", float("inf"))])
def test_band_bounds_are_validated(field: str, value: float) -> None:
    settings = default_stem_eq()
    settings["bell_1"]["enabled"] = True
    settings["bell_1"][field] = value
    with pytest.raises(ValueError):
        resolve_stem_eq(settings, 48000)


def test_neutral_bypassed_and_mixed_out_are_unchanged() -> None:
    audio = _tone(1000)
    for settings in (default_stem_eq(), {**default_stem_eq(), "bypass": True}, {**default_stem_eq(), "mix": 0}):
        out = StemEQ({"Vocals": settings}, 48000).process({"Vocals": audio})["Vocals"]
        assert np.array_equal(out, audio)


def test_mono_stereo_and_expected_filter_response() -> None:
    settings = default_stem_eq()
    settings["highpass"] = {"enabled": True, "freq_hz": 500}
    low = _tone(80)
    mono = StemEQ({"Vocals": settings}, 48000).process({"Vocals": low})["Vocals"]
    stereo = StemEQ({"Vocals": settings}, 48000).process({"Vocals": np.column_stack((low, low))})["Vocals"]
    assert mono.shape == low.shape
    assert stereo.shape == (len(low), 2)
    assert np.sqrt(np.mean(mono[24000:] ** 2)) < 0.1
    assert np.array_equal(stereo[:, 0], stereo[:, 1])
