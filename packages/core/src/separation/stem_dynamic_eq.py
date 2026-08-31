"""Conservative shared-DSP dynamic EQ for separated stems."""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import upmixer_dsp

_FIELDS = frozenset({"enabled", "profile", "bands", "mix"})
_BAND_FIELDS = ("enabled", "freq_hz", "q", "threshold_db", "ratio", "max_cut_db", "attack_ms", "release_ms")
_BOUNDS = {
    "freq_hz": (20.0, 20000.0), "q": (0.5, 8.0), "threshold_db": (-48.0, -6.0),
    "ratio": (1.0, 6.0), "max_cut_db": (0.0, 6.0), "attack_ms": (1.0, 100.0),
    "release_ms": (30.0, 600.0),
}


def _band(**values: float | bool) -> dict[str, float | bool]:
    return {"enabled": False, "freq_hz": 1000.0, "q": 1.0, "threshold_db": -24.0,
            "ratio": 1.0, "max_cut_db": 0.0, "attack_ms": 20.0, "release_ms": 200.0} | values


STEM_DYNAMIC_EQ_PROFILES: dict[str, list[dict[str, float | bool]]] = {
    "vocal-sibilance": [_band(enabled=True, freq_hz=7500.0, q=3.0, threshold_db=-31.0, ratio=4.0, max_cut_db=6.0, attack_ms=2.0, release_ms=80.0)],
    "vocal-harshness": [_band(enabled=True, freq_hz=3500.0, q=1.8, threshold_db=-32.0, ratio=3.0, max_cut_db=4.0, attack_ms=15.0, release_ms=180.0)],
    "drum-ring": [_band(enabled=True, freq_hz=450.0, q=4.0, threshold_db=-26.0, ratio=3.0, max_cut_db=4.0, attack_ms=8.0, release_ms=180.0)],
    "cymbal-bite": [_band(enabled=True, freq_hz=8500.0, q=2.0, threshold_db=-28.0, ratio=3.0, max_cut_db=4.0, attack_ms=3.0, release_ms=120.0)],
    "bass-bloom": [_band(enabled=True, freq_hz=90.0, q=1.0, threshold_db=-24.0, ratio=2.5, max_cut_db=4.0, attack_ms=25.0, release_ms=250.0)],
    "low-mid-mud": [_band(enabled=True, freq_hz=250.0, q=1.2, threshold_db=-25.0, ratio=2.5, max_cut_db=4.0, attack_ms=30.0, release_ms=250.0)],
    "kick-boxiness": [_band(enabled=True, freq_hz=280.0, q=1.5, threshold_db=-24.0, ratio=3.0, max_cut_db=4.0, attack_ms=15.0, release_ms=160.0)],
    "snare-ring": [_band(enabled=True, freq_hz=650.0, q=4.0, threshold_db=-26.0, ratio=3.0, max_cut_db=4.0, attack_ms=6.0, release_ms=160.0)],
    "toms-ring": [_band(enabled=True, freq_hz=350.0, q=3.0, threshold_db=-25.0, ratio=3.0, max_cut_db=4.0, attack_ms=10.0, release_ms=200.0)],
    "hihat-harshness": [_band(enabled=True, freq_hz=8000.0, q=2.0, threshold_db=-30.0, ratio=3.0, max_cut_db=4.0, attack_ms=2.0, release_ms=100.0)],
    "ride-ping": [_band(enabled=True, freq_hz=3500.0, q=3.0, threshold_db=-29.0, ratio=2.5, max_cut_db=3.0, attack_ms=5.0, release_ms=140.0)],
    "crash-harshness": [_band(enabled=True, freq_hz=6500.0, q=2.0, threshold_db=-30.0, ratio=3.0, max_cut_db=4.0, attack_ms=3.0, release_ms=160.0)],
    "vocals-harshness": [_band(enabled=True, freq_hz=3500.0, q=1.8, threshold_db=-32.0, ratio=3.0, max_cut_db=4.0, attack_ms=15.0, release_ms=180.0)],
    "lead-vocals-sibilance": [_band(enabled=True, freq_hz=7500.0, q=3.0, threshold_db=-31.0, ratio=4.0, max_cut_db=6.0, attack_ms=2.0, release_ms=80.0)],
    "backing-vocals-sibilance": [_band(enabled=True, freq_hz=7000.0, q=2.5, threshold_db=-30.0, ratio=3.0, max_cut_db=4.0, attack_ms=3.0, release_ms=100.0)],
    "vocals-reverb-mud": [_band(enabled=True, freq_hz=250.0, q=1.2, threshold_db=-28.0, ratio=2.0, max_cut_db=3.0, attack_ms=30.0, release_ms=250.0)],
    "bass-bloom-control": [_band(enabled=True, freq_hz=90.0, q=1.0, threshold_db=-24.0, ratio=2.5, max_cut_db=4.0, attack_ms=25.0, release_ms=250.0)],
    "drums-kit-ring": [_band(enabled=True, freq_hz=450.0, q=4.0, threshold_db=-26.0, ratio=3.0, max_cut_db=4.0, attack_ms=8.0, release_ms=180.0)],
    "guitar-honk": [_band(enabled=True, freq_hz=900.0, q=1.5, threshold_db=-27.0, ratio=2.5, max_cut_db=3.0, attack_ms=15.0, release_ms=180.0)],
    "piano-hardness": [_band(enabled=True, freq_hz=2800.0, q=1.5, threshold_db=-30.0, ratio=2.5, max_cut_db=3.0, attack_ms=10.0, release_ms=180.0)],
    "crowd-harshness": [_band(enabled=True, freq_hz=3000.0, q=1.5, threshold_db=-30.0, ratio=2.0, max_cut_db=3.0, attack_ms=20.0, release_ms=220.0)],
}
STEM_DYNAMIC_EQ_PROFILE_NAMES = tuple(sorted(STEM_DYNAMIC_EQ_PROFILES))
STEM_DYNAMIC_EQ_PRESETS_BY_STEM: dict[str, tuple[str, ...]] = {
    "Vocals": ("vocals-harshness",),
    "Lead Vocals": ("lead-vocals-sibilance",),
    "Backing Vocals": ("backing-vocals-sibilance",),
    "Vocals Reverb": ("vocals-reverb-mud",),
    "Bass": ("bass-bloom-control",),
    "Drums": ("drums-kit-ring",),
    "Kick": ("kick-boxiness",),
    "Snare": ("snare-ring",),
    "Toms": ("toms-ring",),
    "Hi-Hat": ("hihat-harshness",),
    "Ride": ("ride-ping",),
    "Crash": ("crash-harshness",),
    "Guitar": ("guitar-honk",),
    "Piano": ("piano-hardness",),
    "Other": ("low-mid-mud",),
    "Crowd": ("crowd-harshness",),
}


def default_stem_dynamic_eq() -> dict[str, Any]:
    """Return the neutral stored state."""
    return {"enabled": False, "profile": None, "bands": [], "mix": 100.0}


def _number(value: Any, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Stem dynamic EQ {field} must be a finite number.")
    if not low <= float(value) <= high:
        raise ValueError(f"Stem dynamic EQ {field} must be between {low} and {high}.")
    return float(value)


def resolve_stem_dynamic_eq(value: dict[str, Any], sample_rate: int | None = None) -> dict[str, Any]:
    """Validate one block; explicit bands take precedence over its profile."""
    if not isinstance(value, dict):
        raise ValueError("Stem dynamic EQ must be a mapping.")
    unknown = set(value) - _FIELDS
    if unknown:
        raise ValueError(f"Unknown stem dynamic EQ field '{sorted(unknown)[0]}'.")
    result = default_stem_dynamic_eq() | value
    if not isinstance(result["enabled"], bool):
        raise ValueError("Stem dynamic EQ enabled must be a boolean.")
    profile = result["profile"]
    if profile is not None and (not isinstance(profile, str) or profile not in STEM_DYNAMIC_EQ_PROFILES):
        raise ValueError(f"Unknown stem dynamic EQ profile {profile!r}.")
    bands = result["bands"]
    if not isinstance(bands, list):
        raise ValueError("Stem dynamic EQ bands must be a list.")
    if len(bands) > 2:
        raise ValueError("Stem dynamic EQ takes at most 2 bands.")
    if not bands and profile:
        bands = [dict(band) for band in STEM_DYNAMIC_EQ_PROFILES[profile]]
    nyquist = sample_rate / 2.0 if sample_rate else None
    resolved = []
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            raise ValueError(f"Stem dynamic EQ bands[{index}] must be a mapping.")
        unknown = set(band) - set(_BAND_FIELDS)
        if unknown:
            raise ValueError(f"Unknown stem dynamic EQ field 'bands[{index}].{sorted(unknown)[0]}'.")
        if set(band) != set(_BAND_FIELDS):
            raise ValueError(f"Stem dynamic EQ bands[{index}] must specify every band field.")
        if not isinstance(band["enabled"], bool):
            raise ValueError(f"Stem dynamic EQ bands[{index}].enabled must be a boolean.")
        item = dict(band)
        for field, (low, high) in _BOUNDS.items():
            item[field] = _number(item[field], f"bands[{index}].{field}", low, high)
        if nyquist is not None and item["enabled"]:
            item["freq_hz"] = min(item["freq_hz"], nyquist * 0.999)
        resolved.append(item)
    result["bands"] = resolved
    result["mix"] = _number(result["mix"], "mix", 0.0, 100.0)
    return result


class StemDynamicEq:
    """Apply dynamic EQ to addressed stems before gentle dynamics and routing."""

    def __init__(self, settings: dict[str, dict[str, Any]], sample_rate: int) -> None:
        self._settings = {name: resolve_stem_dynamic_eq(value, sample_rate) for name, value in settings.items()}
        self._sample_rate = sample_rate

    def process(self, all_stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Process only active addressed stems, retaining all others by identity."""
        out: dict[str, np.ndarray] = {}
        for key, audio in all_stems.items():
            settings = self._settings.get(key.split("@")[0])
            if settings is None or not settings["enabled"] or settings["mix"] == 0.0:
                out[key] = audio
                continue
            if not any(band["enabled"] and band["ratio"] > 1.0 and band["max_cut_db"] > 0.0 for band in settings["bands"]):
                out[key] = audio
                continue
            arr = np.asarray(audio, dtype=np.float64)
            channels = [arr] if arr.ndim == 1 else [arr[:, index] for index in range(arr.shape[1])]
            params = dict(settings)
            params.pop("profile", None)
            params["mix"] /= 100.0
            processed = upmixer_dsp.stem_dynamic_eq(channels, self._sample_rate, json.dumps(params))
            out[key] = processed[0] if arr.ndim == 1 else np.column_stack(processed)
        return out
