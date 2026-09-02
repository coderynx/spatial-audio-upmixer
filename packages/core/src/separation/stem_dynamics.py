"""Constrained shared-DSP downward compression before spatial routing."""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import upmixer_dsp

_FIELDS = frozenset({"enabled", "profile", "threshold_db", "ratio", "attack_ms", "release_ms", "mix"})
_DEFAULTS = {
    "enabled": False,
    "profile": None,
    "threshold_db": -18.0,
    "ratio": 1.5,
    "attack_ms": 30.0,
    "release_ms": 250.0,
    "mix": 100.0,
}
STEM_DYNAMICS_PROFILES: dict[str, dict[str, float]] = {
    "vocal-control": {"threshold_db": -20.0, "ratio": 1.8, "attack_ms": 15.0, "release_ms": 180.0, "mix": 100.0},
    "bass-control": {"threshold_db": -20.0, "ratio": 2.0, "attack_ms": 35.0, "release_ms": 300.0, "mix": 100.0},
    "drum-control": {"threshold_db": -16.0, "ratio": 2.0, "attack_ms": 20.0, "release_ms": 180.0, "mix": 100.0},
    "kick-control": {"threshold_db": -18.0, "ratio": 2.5, "attack_ms": 20.0, "release_ms": 140.0, "mix": 100.0},
    "snare-control": {"threshold_db": -17.0, "ratio": 2.0, "attack_ms": 10.0, "release_ms": 140.0, "mix": 100.0},
    "toms-control": {"threshold_db": -18.0, "ratio": 2.0, "attack_ms": 15.0, "release_ms": 180.0, "mix": 100.0},
    "hihat-control": {"threshold_db": -22.0, "ratio": 1.5, "attack_ms": 5.0, "release_ms": 100.0, "mix": 100.0},
    "ride-control": {"threshold_db": -22.0, "ratio": 1.5, "attack_ms": 5.0, "release_ms": 120.0, "mix": 100.0},
    "crash-control": {"threshold_db": -24.0, "ratio": 1.4, "attack_ms": 10.0, "release_ms": 200.0, "mix": 100.0},
    "vocals-control": {"threshold_db": -20.0, "ratio": 1.6, "attack_ms": 20.0, "release_ms": 200.0, "mix": 100.0},
    "lead-vocals-control": {"threshold_db": -21.0, "ratio": 1.8, "attack_ms": 15.0, "release_ms": 180.0, "mix": 100.0},
    "backing-vocals-control": {"threshold_db": -20.0, "ratio": 1.5, "attack_ms": 20.0, "release_ms": 220.0, "mix": 100.0},
    "bass-foundation-control": {"threshold_db": -20.0, "ratio": 2.0, "attack_ms": 35.0, "release_ms": 300.0, "mix": 100.0},
    "drums-kit-control": {"threshold_db": -16.0, "ratio": 2.0, "attack_ms": 20.0, "release_ms": 180.0, "mix": 100.0},
    "guitar-control": {"threshold_db": -18.0, "ratio": 1.5, "attack_ms": 25.0, "release_ms": 220.0, "mix": 100.0},
    "piano-control": {"threshold_db": -18.0, "ratio": 1.5, "attack_ms": 30.0, "release_ms": 250.0, "mix": 100.0},
    "crowd-control": {"threshold_db": -24.0, "ratio": 1.3, "attack_ms": 30.0, "release_ms": 350.0, "mix": 100.0},
    "instrument-control": {"threshold_db": -18.0, "ratio": 1.5, "attack_ms": 30.0, "release_ms": 250.0, "mix": 100.0},
    "ambience-control": {"threshold_db": -24.0, "ratio": 1.3, "attack_ms": 30.0, "release_ms": 350.0, "mix": 100.0},
}
STEM_DYNAMICS_PRESETS_BY_STEM: dict[str, tuple[str, ...]] = {
    "Vocals": ("vocals-control",),
    "Lead Vocals": ("lead-vocals-control",),
    "Backing Vocals": ("backing-vocals-control",),
    "Bass": ("bass-foundation-control",),
    "Drums": ("drums-kit-control",),
    "Kick": ("kick-control",),
    "Snare": ("snare-control",),
    "Toms": ("toms-control",),
    "Hi-Hat": ("hihat-control",),
    "Ride": ("ride-control",),
    "Crash": ("crash-control",),
    "Guitar": ("guitar-control",),
    "Piano": ("piano-control",),
    "Other": ("instrument-control",),
    "Crowd": ("crowd-control",),
}


def default_stem_dynamics() -> dict[str, Any]:
    """Return the neutral stored settings for one stem."""
    return dict(_DEFAULTS)


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Stem dynamics {name} must be a finite number.")
    if not low <= float(value) <= high:
        raise ValueError(f"Stem dynamics {name} must be between {low} and {high}.")
    return float(value)


def resolve_stem_dynamics(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and complete one manifest dynamics block."""
    if not isinstance(value, dict):
        raise ValueError("Stem dynamics must be a mapping.")
    unknown = set(value) - _FIELDS
    if unknown:
        raise ValueError(f"Unknown stem dynamics field '{sorted(unknown)[0]}'.")
    profile = value.get("profile")
    if profile is not None and (not isinstance(profile, str) or profile not in STEM_DYNAMICS_PROFILES):
        raise ValueError(f"Unknown stem dynamics profile {profile!r}.")
    result = default_stem_dynamics()
    if profile:
        result.update(STEM_DYNAMICS_PROFILES[profile])
    result.update(value)
    if not isinstance(result["enabled"], bool):
        raise ValueError("Stem dynamics enabled must be a boolean.")
    result["threshold_db"] = _number(result["threshold_db"], "threshold_db", -36.0, -6.0)
    result["ratio"] = _number(result["ratio"], "ratio", 1.0, 3.0)
    result["attack_ms"] = _number(result["attack_ms"], "attack_ms", 5.0, 80.0)
    result["release_ms"] = _number(result["release_ms"], "release_ms", 80.0, 600.0)
    result["mix"] = _number(result["mix"], "mix", 0.0, 100.0)
    return result


class StemDynamics:
    """Apply independent linked dynamics settings to addressed stems."""

    def __init__(self, settings: dict[str, dict[str, Any]], sample_rate: int) -> None:
        self._settings = {name: resolve_stem_dynamics(value) for name, value in settings.items()}
        self._sample_rate = sample_rate

    @staticmethod
    def _canonical(key: str) -> str:
        return key.split("@")[0]

    def process(self, all_stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Process addressed stems, retaining other arrays by identity."""
        out: dict[str, np.ndarray] = {}
        for key, audio in all_stems.items():
            settings = self._settings.get(self._canonical(key))
            if settings is None or not settings["enabled"] or settings["ratio"] == 1.0 or settings["mix"] == 0.0:
                out[key] = audio
                continue
            arr = np.asarray(audio, dtype=np.float64)
            channels = [arr] if arr.ndim == 1 else [arr[:, index] for index in range(arr.shape[1])]
            params = dict(settings)
            params.pop("profile", None)
            params["mix"] = float(params["mix"]) / 100.0
            processed = upmixer_dsp.stem_dynamics(channels, self._sample_rate, json.dumps(params))
            out[key] = processed[0] if arr.ndim == 1 else np.column_stack(processed)
        return out
