"""Coverage for per-stem shared dynamic EQ."""
import numpy as np
import pytest

from manifest_helpers import _minimal
from upmixer.manifest import ManifestError, validate_manifest
from upmixer.separation.stem_dynamic_eq import StemDynamicEq, default_stem_dynamic_eq, resolve_stem_dynamic_eq


def test_neutral_settings_are_exact_noops() -> None:
    audio = np.column_stack((np.linspace(-.5, .5, 256), np.linspace(.5, -.5, 256)))
    for patch in ({}, {"enabled": True}, {"enabled": True, "mix": 0}):
        out = StemDynamicEq({"Vocals": default_stem_dynamic_eq() | patch}, 48000).process({"Vocals": audio})["Vocals"]
        assert out is audio


def test_profile_is_capped_linked_and_validated() -> None:
    n = np.arange(48000)
    tone = .9 * np.sin(2 * np.pi * 7500 * n / 48000)
    stereo = np.column_stack((tone, tone * .5))
    settings = {"enabled": True, "profile": "vocal-sibilance", "bands": [], "mix": 100}
    out = StemDynamicEq({"Vocals": settings}, 48000).process({"Vocals": stereo})["Vocals"]
    assert np.sqrt(np.mean(out[-12000:, 0] ** 2)) < np.sqrt(np.mean(stereo[-12000:, 0] ** 2))
    assert np.allclose(out[:, 1], out[:, 0] * .5, atol=1e-10)
    assert resolve_stem_dynamic_eq({"enabled": True, "bands": [{"enabled": True, "freq_hz": 20000, "q": 1, "threshold_db": -24, "ratio": 2, "max_cut_db": 4, "attack_ms": 5, "release_ms": 80}], "mix": 100}, 32000)["bands"][0]["freq_hz"] < 16000


def test_manifest_round_trip_rejects_unknown_band_field() -> None:
    data = _minimal(mixing={"stem_dynamic_eq": {"Vocals": {"enabled": True, "profile": "vocal-sibilance", "bands": [], "mix": 100}}})
    validate_manifest(data)
    data["mixing"]["stem_dynamic_eq"]["Vocals"]["bands"] = [{"enabled": True, "freq_hz": 1000, "q": 1, "threshold_db": -24, "ratio": 2, "max_cut_db": 4, "attack_ms": 10, "release_ms": 100, "boost_db": 1}]
    with pytest.raises(ManifestError):
        validate_manifest(data)
