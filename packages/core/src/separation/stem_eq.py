"""Per-stem EQ settings and shared-DSP processing before spatial routing."""
from __future__ import annotations

import copy
import json
import math
from typing import Any

import numpy as np
import upmixer_dsp


STEM_EQ_PROFILES: dict[str, list[tuple[float, float]]] = {
    "vocal-presence": [(20, 0.0), (800, 0.0), (2000, 0.5), (4000, 2.0), (6000, 1.5), (10000, 1.0), (20000, 1.5)],
    "vocal-warmth": [(20, 0.0), (200, 1.5), (500, 0.8), (2000, 0.0), (4000, -0.5), (8000, 0.0), (20000, 0.0)],
    "bass-warmth": [(20, 0.0), (50, 1.5), (100, 1.0), (200, 0.5), (500, 0.0), (20000, 0.0)],
    "bass-cut": [(20, -2.0), (60, -1.0), (120, 0.0), (400, 0.0), (20000, 0.0)],
    "drums-punch": [(20, 0.0), (80, 1.5), (200, 0.0), (3000, 0.0), (5000, 1.5), (8000, 1.0), (20000, 0.0)],
    "other-air": [(20, 0.0), (1000, 0.0), (8000, 0.5), (14000, 1.5), (20000, 2.0)],
    "flat": [(20, 0.0), (20000, 0.0)],
}
STEM_EQ_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(STEM_EQ_PROFILES))
STEM_EQ_FIR_ASSET_PREFIX = "stem_"
STEM_EQ_FIR_ASSETS: dict[str, str] = {name: f"{STEM_EQ_FIR_ASSET_PREFIX}{name}" for name in STEM_EQ_PROFILE_NAMES}
STEM_EQ_PRESETS_BY_STEM: dict[str, tuple[str, ...]] = {
    "Vocals": ("vocals-balance",),
    "Lead Vocals": ("lead-vocals-presence",),
    "Backing Vocals": ("backing-vocals-air",),
    "Bass": ("bass-foundation",),
    "Drums": ("drums-kit-punch",),
    "Kick": ("kick-weight",),
    "Snare": ("snare-crack",),
    "Toms": ("toms-body",),
    "Hi-Hat": ("hihat-sheen",),
    "Ride": ("ride-clarity",),
    "Crash": ("crash-smooth",),
    "Guitar": ("guitar-clarity",),
    "Piano": ("piano-warmth",),
    "Other": ("other-air",),
    "Crowd": ("crowd-clarity",),
}

_FIELDS = frozenset({"preset", "bypass", "highpass", "low_shelf", "bell_1", "bell_2", "high_shelf", "lowpass", "mix"})
_FILTER_FIELDS = frozenset({"enabled", "freq_hz"})
_BAND_FIELDS = frozenset({"enabled", "freq_hz", "gain_db", "q"})


def _filter(freq_hz: float) -> dict[str, Any]:
    return {"enabled": False, "freq_hz": freq_hz}


def _band(freq_hz: float, gain_db: float = 0.0, q: float = 0.707) -> dict[str, Any]:
    return {"enabled": False, "freq_hz": freq_hz, "gain_db": gain_db, "q": q}


def default_stem_eq() -> dict[str, Any]:
    """Return the neutral, no-op editable EQ state."""
    return {"preset": None, "bypass": False, "highpass": _filter(20.0), "low_shelf": _band(120.0), "bell_1": _band(1000.0, q=1.0), "bell_2": _band(4000.0, q=1.0), "high_shelf": _band(8000.0), "lowpass": _filter(20000.0), "mix": 100.0}


def _preset(**values: Any) -> dict[str, Any]:
    result = default_stem_eq()
    result.update(values)
    return result


STEM_EQ_SETTINGS: dict[str, dict[str, Any]] = {
    "flat": _preset(preset="flat"),
    "vocals-balance": _preset(preset="vocals-balance", bell_1={"enabled": True, "freq_hz": 300.0, "gain_db": -1.0, "q": 1.0}, bell_2={"enabled": True, "freq_hz": 3500.0, "gain_db": 1.5, "q": 1.0}),
    "lead-vocals-presence": _preset(preset="lead-vocals-presence", bell_1={"enabled": True, "freq_hz": 3000.0, "gain_db": 2.0, "q": 1.0}, high_shelf={"enabled": True, "freq_hz": 10000.0, "gain_db": 1.0, "q": 0.707}),
    "backing-vocals-air": _preset(preset="backing-vocals-air", highpass={"enabled": True, "freq_hz": 120.0}, high_shelf={"enabled": True, "freq_hz": 11000.0, "gain_db": 1.5, "q": 0.707}),
    "bass-foundation": _preset(preset="bass-foundation", low_shelf={"enabled": True, "freq_hz": 80.0, "gain_db": 1.5, "q": 0.707}, bell_1={"enabled": True, "freq_hz": 350.0, "gain_db": -1.0, "q": 1.0}),
    "drums-kit-punch": _preset(preset="drums-kit-punch", bell_1={"enabled": True, "freq_hz": 80.0, "gain_db": 1.5, "q": 1.0}, bell_2={"enabled": True, "freq_hz": 5000.0, "gain_db": 1.5, "q": 1.0}),
    "vocal-presence": _preset(preset="vocal-presence", bell_1={"enabled": True, "freq_hz": 4000.0, "gain_db": 2.0, "q": 1.0}, high_shelf={"enabled": True, "freq_hz": 10000.0, "gain_db": 1.0, "q": 0.707}),
    "vocal-warmth": _preset(preset="vocal-warmth", low_shelf={"enabled": True, "freq_hz": 200.0, "gain_db": 1.5, "q": 0.707}, bell_1={"enabled": True, "freq_hz": 4000.0, "gain_db": -0.5, "q": 1.0}),
    "bass-warmth": _preset(preset="bass-warmth", low_shelf={"enabled": True, "freq_hz": 70.0, "gain_db": 1.5, "q": 0.707}),
    "bass-cut": _preset(preset="bass-cut", low_shelf={"enabled": True, "freq_hz": 70.0, "gain_db": -2.0, "q": 0.707}),
    "drums-punch": _preset(preset="drums-punch", bell_1={"enabled": True, "freq_hz": 80.0, "gain_db": 1.5, "q": 1.0}, bell_2={"enabled": True, "freq_hz": 5000.0, "gain_db": 1.5, "q": 1.0}),
    "kick-weight": _preset(preset="kick-weight", low_shelf={"enabled": True, "freq_hz": 65.0, "gain_db": 2.0, "q": 0.707}, bell_1={"enabled": True, "freq_hz": 250.0, "gain_db": -1.0, "q": 1.0}),
    "snare-crack": _preset(preset="snare-crack", bell_1={"enabled": True, "freq_hz": 200.0, "gain_db": -1.0, "q": 1.0}, bell_2={"enabled": True, "freq_hz": 4000.0, "gain_db": 2.0, "q": 1.0}),
    "toms-body": _preset(preset="toms-body", low_shelf={"enabled": True, "freq_hz": 110.0, "gain_db": 1.5, "q": 0.707}, bell_1={"enabled": True, "freq_hz": 450.0, "gain_db": -1.0, "q": 1.2}),
    "hihat-sheen": _preset(preset="hihat-sheen", highpass={"enabled": True, "freq_hz": 250.0}, high_shelf={"enabled": True, "freq_hz": 10000.0, "gain_db": 1.5, "q": 0.707}),
    "ride-clarity": _preset(preset="ride-clarity", highpass={"enabled": True, "freq_hz": 200.0}, bell_1={"enabled": True, "freq_hz": 3500.0, "gain_db": -1.0, "q": 1.2}),
    "crash-smooth": _preset(preset="crash-smooth", highpass={"enabled": True, "freq_hz": 200.0}, bell_1={"enabled": True, "freq_hz": 6500.0, "gain_db": -1.5, "q": 1.5}),
    "guitar-clarity": _preset(preset="guitar-clarity", highpass={"enabled": True, "freq_hz": 80.0}, bell_1={"enabled": True, "freq_hz": 250.0, "gain_db": -1.0, "q": 1.0}, bell_2={"enabled": True, "freq_hz": 2500.0, "gain_db": 1.0, "q": 1.0}),
    "piano-warmth": _preset(preset="piano-warmth", highpass={"enabled": True, "freq_hz": 35.0}, low_shelf={"enabled": True, "freq_hz": 180.0, "gain_db": 1.0, "q": 0.707}, bell_1={"enabled": True, "freq_hz": 3000.0, "gain_db": -0.5, "q": 1.0}),
    "crowd-clarity": _preset(preset="crowd-clarity", highpass={"enabled": True, "freq_hz": 200.0}, bell_1={"enabled": True, "freq_hz": 2500.0, "gain_db": -2.0, "q": 1.0}),
    "other-air": _preset(preset="other-air", high_shelf={"enabled": True, "freq_hz": 14000.0, "gain_db": 1.5, "q": 0.707}),
}


def _number(value: Any, path: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path} must be a finite number.")
    if not low <= float(value) <= high:
        raise ValueError(f"{path} must be between {low} and {high}.")
    return float(value)


def resolve_stem_eq(value: str | dict[str, Any], sample_rate: int | None = None) -> dict[str, Any]:
    """Resolve a legacy preset name or structured EQ mapping to one setting."""
    if isinstance(value, str):
        if value not in STEM_EQ_SETTINGS:
            raise KeyError(f"Unknown stem EQ profile '{value}'. Valid choices: {STEM_EQ_PROFILE_NAMES}")
        value = {"preset": value}
    if not isinstance(value, dict):
        raise ValueError("Stem EQ must be a preset name or mapping.")
    unknown = set(value) - _FIELDS
    if unknown:
        raise ValueError(f"Unknown stem EQ field '{sorted(unknown)[0]}'.")
    preset = value.get("preset")
    if preset is not None and (not isinstance(preset, str) or preset not in STEM_EQ_SETTINGS):
        raise ValueError(f"Unknown stem EQ preset {preset!r}.")
    result = copy.deepcopy(STEM_EQ_SETTINGS[preset] if preset else default_stem_eq())
    for name, item in value.items():
        if name in {"highpass", "lowpass", "low_shelf", "bell_1", "bell_2", "high_shelf"} and item is not None:
            if not isinstance(item, dict):
                raise ValueError(f"Stem EQ {name} must be a mapping.")
            allowed = _FILTER_FIELDS if name in {"highpass", "lowpass"} else _BAND_FIELDS
            unknown = set(item) - allowed
            if unknown:
                raise ValueError(f"Unknown stem EQ field '{name}.{sorted(unknown)[0]}'.")
            result[name].update(item)
        else:
            result[name] = item
    if not isinstance(result["bypass"], bool):
        raise ValueError("Stem EQ bypass must be a boolean.")
    result["mix"] = _number(result["mix"], "Stem EQ mix", 0.0, 100.0)
    nyquist = sample_rate / 2.0 if sample_rate else None
    for name in ("highpass", "lowpass"):
        band = result[name]
        if not isinstance(band.get("enabled"), bool):
            raise ValueError(f"Stem EQ {name}.enabled must be a boolean.")
        band["freq_hz"] = _number(band.get("freq_hz"), f"Stem EQ {name}.freq_hz", 20.0 if name == "highpass" else 1000.0, 20000.0)
        if nyquist is not None and band["enabled"] and band["freq_hz"] >= nyquist:
            raise ValueError(f"Stem EQ {name}.freq_hz must be below Nyquist.")
    for name in ("low_shelf", "bell_1", "bell_2", "high_shelf"):
        band = result[name]
        if not isinstance(band.get("enabled"), bool):
            raise ValueError(f"Stem EQ {name}.enabled must be a boolean.")
        band["freq_hz"] = _number(band.get("freq_hz"), f"Stem EQ {name}.freq_hz", 20.0, 20000.0)
        band["gain_db"] = _number(band.get("gain_db"), f"Stem EQ {name}.gain_db", -12.0, 6.0)
        band["q"] = _number(band.get("q"), f"Stem EQ {name}.q", 0.3, 8.0)
        if nyquist is not None and band["enabled"] and band["freq_hz"] >= nyquist:
            raise ValueError(f"Stem EQ {name}.freq_hz must be below Nyquist.")
    return result


def stem_eq_params(value: str | dict[str, Any], sample_rate: int) -> dict[str, Any]:
    """Resolve settings for the core's normalized, 0..1 mix parameter."""
    result = resolve_stem_eq(value, sample_rate)
    result.pop("preset", None)
    result["mix"] /= 100.0
    return result


class StemEQ:
    """Apply per-stem shared-Rust EQ before spatial routing."""

    def __init__(self, profiles: dict[str, str | dict[str, Any]], sample_rate: int, n_taps: int = 511) -> None:
        del n_taps
        self._profiles = {name: stem_eq_params(value, sample_rate) for name, value in profiles.items() if value}
        self._sr = sample_rate

    @staticmethod
    def _canonical(key: str) -> str:
        return key.split("@")[0]

    def process(self, all_stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Apply EQ to addressed stems; unaddressed stems retain identity."""
        out: dict[str, np.ndarray] = {}
        for key, audio in all_stems.items():
            settings = self._profiles.get(self._canonical(key))
            if settings is None:
                out[key] = audio
                continue
            arr = np.asarray(audio, dtype=np.float64)
            channels = [arr] if arr.ndim == 1 else [arr[:, channel] for channel in range(arr.shape[1])]
            filtered = upmixer_dsp.stem_eq(channels, self._sr, json.dumps(settings))
            out[key] = filtered[0] if arr.ndim == 1 else np.column_stack(filtered)
        return out
