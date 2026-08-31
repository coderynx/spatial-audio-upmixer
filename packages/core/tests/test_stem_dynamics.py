"""Tests for constrained shared-DSP stem dynamics."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.separation.stem_dynamics import StemDynamics, default_stem_dynamics, resolve_stem_dynamics
from upmixer.manifest import ManifestError, validate_manifest

from manifest_helpers import _minimal


def test_noops_and_validation() -> None:
    audio = np.column_stack((np.ones(4800), np.full(4800, 0.25)))
    for patch in ({}, {"enabled": False}, {"enabled": True, "ratio": 1.0}, {"enabled": True, "mix": 0.0}):
        settings = default_stem_dynamics() | patch
        assert StemDynamics({"Vocals": settings}, 48000).process({"Vocals": audio})["Vocals"] is audio
    with pytest.raises(ValueError):
        resolve_stem_dynamics({"threshold_db": float("nan")})
    assert resolve_stem_dynamics({"profile": "vocal-control"})["ratio"] == 1.8


def test_linked_cap_mono_and_finite_output() -> None:
    settings = default_stem_dynamics() | {"enabled": True, "ratio": 3.0, "attack_ms": 5.0, "release_ms": 80.0}
    stereo = np.column_stack((np.ones(48000), np.full(48000, 0.25)))
    out = StemDynamics({"Vocals": settings}, 48000).process({"Vocals": stereo})["Vocals"]
    assert np.allclose(out[-1, 0] / out[-1, 1], 4.0)
    assert np.isfinite(out).all()
    assert 20 * np.log10(stereo[-1, 0] / out[-1, 0]) <= 6.000001
    mono = StemDynamics({"Bass": settings}, 48000).process({"Bass": np.array([np.inf, 1.0])})["Bass"]
    assert np.isfinite(mono).all()


def test_manifest_settings_round_trip_and_reject_unknown_fields() -> None:
    settings = default_stem_dynamics() | {"enabled": True, "threshold_db": -24.0}
    data = _minimal(mixing={"stem_dynamics": {"Vocals": settings}})
    validate_manifest(data)
    data["mixing"]["stem_dynamics"]["Vocals"]["makeup_db"] = 1
    with pytest.raises(ManifestError):
        validate_manifest(data)
