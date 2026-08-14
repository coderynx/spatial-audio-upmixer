"""Offline content analysis and smooth controls for creative spatial routing."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d

from upmixer.config import UpmixConfig

_PROFILES = frozenset({"auto", "balanced", "intimate", "rhythmic", "spacious", "live", "detailed"})


@dataclass(frozen=True)
class SpatialPlan:
    """Per-frame spatial gains. Values are smooth, bounded multipliers."""
    profile: str
    confidence: float
    hop_size: int
    front: np.ndarray
    surround: np.ndarray
    back: np.ndarray
    height: np.ndarray
    detail: np.ndarray

    def controls_at(self, sample_offset: int) -> dict[str, float]:
        if len(self.front) == 0:
            return {"front": 1.0, "surround": 1.0, "back": 1.0, "height": 1.0, "detail": 0.0}
        i = min(max(sample_offset // self.hop_size, 0), len(self.front) - 1)
        return {name: float(values[i]) for name, values in (
            ("front", self.front), ("surround", self.surround),
            ("back", self.back), ("height", self.height), ("detail", self.detail),
        )}


def _frame_features(left: np.ndarray, right: np.ndarray, sr: int, hop: int) -> tuple[np.ndarray, ...]:
    n = max(1, int(np.ceil(len(left) / hop)))
    activity = np.zeros(n)
    width = np.zeros(n)
    transient = np.zeros(n)
    air = np.zeros(n)
    diffuse = np.zeros(n)
    previous = 0.0
    for i in range(n):
        start, end = i * hop, min((i + 1) * hop, len(left))
        l, r = left[start:end], right[start:end]
        if len(l) < 8:
            continue
        mono = (l + r) * 0.5
        side = (l - r) * 0.5
        rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
        activity[i] = rms
        width[i] = float(np.clip(np.mean(side * side) / (np.mean(l * l + r * r) * 0.5 + 1e-12), 0.0, 1.0))
        transient[i] = max(0.0, rms - previous) / (rms + previous + 1e-12)
        previous = rms
        spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
        total = float(np.sum(spectrum) + 1e-12)
        air[i] = float(np.sum(spectrum[freqs >= 4000.0]) / total)
        diffuse[i] = width[i] * (1.0 - transient[i])
    activity /= max(float(np.percentile(activity, 90)), 1e-12)
    return tuple(np.clip(x, 0.0, 1.0) for x in (activity, width, transient, air, diffuse))


def _profile(width: float, transient: float, air: float, diffuse: float, requested: str) -> tuple[str, float]:
    if requested not in _PROFILES:
        raise ValueError(f"Unknown spatial_profile '{requested}'. Valid: {sorted(_PROFILES)}")
    if requested != "auto":
        return requested, 1.0
    scores = {
        "intimate": (1.0 - width) * 0.55 + (1.0 - diffuse) * 0.45,
        "rhythmic": transient * 0.75 + air * 0.25,
        "spacious": diffuse * 0.70 + width * 0.30,
        "live": diffuse * 0.55 + width * 0.25 + (1.0 - transient) * 0.20,
        "detailed": air * 0.45 + width * 0.30 + (1.0 - transient) * 0.25,
    }
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    confidence = max(0.0, ordered[0][1] - ordered[1][1])
    return (ordered[0][0] if confidence >= 0.08 else "balanced"), confidence


def analyze_spatial_plan(left: np.ndarray, right: np.ndarray, sample_rate: int, config: UpmixConfig) -> SpatialPlan:
    """Build an offline plan; analysis never changes source audio."""
    hop = max(1, int(sample_rate * 0.25))
    activity, width, transient, air, diffuse = _frame_features(left, right, sample_rate, hop)
    profile, confidence = _profile(*(float(np.mean(x)) for x in (width, transient, air, diffuse)), config.spatial_profile)
    base = {
        "balanced": (1.0, 1.0, 0.92, 1.0),
        "intimate": (1.06, 0.68, 0.55, 0.68),
        "rhythmic": (1.03, 0.88, 0.72, 1.05),
        "spacious": (0.96, 1.20, 1.12, 1.18),
        "live": (0.98, 1.16, 1.22, 0.96),
        "detailed": (1.02, 1.05, 0.92, 1.12),
    }[profile]
    sustain = diffuse * activity
    # 0.75 s smoothing prevents location jumps across musical sections.
    smooth = lambda x: gaussian_filter1d(x, sigma=3.0, mode="nearest") if len(x) > 1 else x
    front = smooth(base[0] * (1.0 + 0.05 * transient))
    surround = smooth(base[1] * (0.72 + 0.55 * sustain) * (1.0 - 0.35 * transient))
    back = smooth(base[2] * (0.65 + 0.65 * sustain) * (1.0 - 0.45 * transient))
    height = smooth(base[3] * (0.72 + 0.45 * np.maximum(air, sustain)) * (1.0 - 0.20 * transient))
    # Detail is an auxiliary-only reveal gate. It never boosts bed/LFE.
    detail_source = (air + width) * 0.5 * (1.0 - transient) * activity
    detail = smooth(np.clip(detail_source, 0.0, 1.0)) if profile == "detailed" else np.zeros_like(activity)
    intensity = float(np.clip(config.spatial_intensity, 0.0, 1.0))
    blend = lambda x: 1.0 + intensity * (x - 1.0)
    return SpatialPlan(profile, confidence, hop, blend(front), blend(surround), blend(back), blend(height), intensity * detail)
