"""Localization measurements for the placement panner (mixing phase 10).

Skipped by default. Run with:
    uv run pytest packages/core/tests/test_mix_localization.py -m perf -s

Measurement 6 of the mixing kit, kept out of ``test_mix_measurement.py`` for
the file-size policy. Two numbers, both computed from the public
``placement_route`` only, so the same code measures whichever panner is behind
it:

* **concentration** — the fraction of a placement's routed power carried by the
  speakers of one panning simplex: the three loudest on a layout with heights,
  the two loudest on a flat one. Amplitude panning localizes from one simplex;
  everything outside it is spill, and spill is what blurs the image and shifts
  the timbre by summing the same signal out of more speakers.
* **angular spread** — power-weighted angular deviation from the routed energy
  centroid, in degrees. A perceived-width proxy: read across azimuth at fixed
  ``width_deg``, it says whether the knob means the same thing in every
  direction or only where the layout happens to be dense.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from upmixer.binaural.geometry import speaker_azimuth_elevation
from upmixer.formats import FORMAT_MAP, ChannelLabel, OutputFormat
from upmixer.separation.stem_placement import (
    SCENE_OBJECT_SIZE,
    STEM_ROUTING_PRESET_TREATMENTS,
    StemPlacement,
    placement_route,
)

pytestmark = pytest.mark.perf

_LAYOUTS: tuple[str, ...] = ("5.1", "7.1.4")
_AZIMUTHS: tuple[float, ...] = (0.0, 30.0, 60.0, 90.0, 135.0, 180.0)
_WIDTHS: tuple[float, ...] = (0.0, 30.0, 60.0, 90.0, 120.0)


def _print_table(title: str, header: tuple[str, ...], rows: list[tuple]) -> None:
    print(f"\n### {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def _unit(azimuth_deg: float, elevation_deg: float) -> np.ndarray:
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    return np.array(
        [
            -math.cos(elevation) * math.sin(azimuth),
            math.sin(elevation),
            -math.cos(elevation) * math.cos(azimuth),
        ]
    )


def _powers(route: dict[str, float]) -> dict[str, float]:
    return {
        channel: gain * gain
        for channel, gain in route.items()
        if channel != ChannelLabel.LFE.value and gain > 0.0
    }


def _concentration(placement: StemPlacement, output_format: OutputFormat) -> tuple[float, int]:
    """(power fraction in the loudest panning simplex, count of routed speakers)."""
    powers = _powers(placement_route(placement, output_format))
    total = sum(powers.values())
    if total <= 0.0:
        return 0.0, 0
    simplex = 3 if output_format.has_height else 2
    loudest = sorted(powers.values(), reverse=True)[:simplex]
    return sum(loudest) / total, len(powers)


def _angular_spread_deg(placement: StemPlacement, output_format: OutputFormat) -> float:
    """Power-weighted angular deviation from the routed energy centroid."""
    powers = _powers(placement_route(placement, output_format))
    speaker_units = {
        label.value: _unit(position.azimuth_deg, position.elevation_deg)
        for label, position in speaker_azimuth_elevation(output_format).items()
    }
    total = sum(powers.values())
    if total <= 0.0:
        return 0.0
    centroid = sum(power * speaker_units[channel] for channel, power in powers.items())
    norm = float(np.linalg.norm(centroid))
    if norm <= 0.0:
        return 180.0
    centroid = centroid / norm
    variance = 0.0
    for channel, power in powers.items():
        cosine = float(np.clip(np.dot(speaker_units[channel], centroid), -1.0, 1.0))
        variance += power * math.degrees(math.acos(cosine)) ** 2
    return math.sqrt(variance / total)


def test_localization_concentration() -> None:
    """Measurement 6a — how much of a point placement stays on its own speakers."""
    for layout in _LAYOUTS:
        output_format = FORMAT_MAP[layout]
        rows = []
        for azimuth in _AZIMUTHS:
            for elevation in (0.0, 20.0):
                placement = StemPlacement(azimuth, elevation, 0.0, SCENE_OBJECT_SIZE)
                fraction, speakers = _concentration(placement, output_format)
                spread = _angular_spread_deg(placement, output_format)
                rows.append(
                    (
                        f"{azimuth:.0f}",
                        f"{elevation:.0f}",
                        speakers,
                        f"{fraction:.3f}",
                        f"{spread:.1f}",
                    )
                )
                assert 0.0 <= fraction <= 1.0
        _print_table(
                f"6a. Point placement (width 0, size {SCENE_OBJECT_SIZE:.2f}) — {layout}",
            ("azimuth", "elevation", "routed speakers", "concentration", "spread °"),
            rows,
        )

    rows = []
    for preset, treatments in STEM_ROUTING_PRESET_TREATMENTS.items():
        for stem, treatment in treatments.items():
            placement = treatment.placement
            if placement.azimuth_deg == 0.0:
                continue
            fraction, speakers = _concentration(placement, FORMAT_MAP["7.1.4"])
            rows.append(
                (
                    preset,
                    stem,
                    f"{placement.azimuth_deg:.0f}",
                    f"{placement.width_deg:.0f}",
                    speakers,
                    f"{fraction:.3f}",
                )
            )
    _print_table(
        "6b. Off-centre preset placements (7.1.4)",
        ("Preset", "Stem", "azimuth", "width_deg", "routed speakers", "concentration"),
        rows,
    )


def test_spread_linearity() -> None:
    """Measurement 6c — does ``width_deg`` mean the same thing in every direction?"""
    for layout in _LAYOUTS:
        output_format = FORMAT_MAP[layout]
        rows = []
        for width in _WIDTHS:
            spreads = [
                _angular_spread_deg(
                    StemPlacement(azimuth, 0.0, width, SCENE_OBJECT_SIZE), output_format
                )
                for azimuth in _AZIMUTHS
            ]
            rows.append(
                (
                    f"{width:.0f}",
                    *[f"{spread:.1f}" for spread in spreads],
                    f"{max(spreads) - min(spreads):.1f}",
                )
            )
            assert all(spread >= 0.0 for spread in spreads)
        _print_table(
            f"6c. Angular spread (°) vs width_deg, per azimuth — {layout}",
            ("width_deg", *[f"az {azimuth:.0f}" for azimuth in _AZIMUTHS], "range"),
            rows,
        )

    rows = []
    for stem, treatment in STEM_ROUTING_PRESET_TREATMENTS["balanced"].items():
        placement = treatment.placement
        rows.append(
            (
                stem,
                f"{placement.width_deg:.0f}",
                f"{placement.object_size:.2f}",
                *[
                    f"{_angular_spread_deg(placement, FORMAT_MAP[layout]):.1f}"
                    for layout in _LAYOUTS
                ],
            )
        )
    _print_table(
        "6d. Angular spread of the balanced preset's placements",
        ("Stem", "width_deg", "object_size", *[f"spread ° {layout}" for layout in _LAYOUTS]),
        rows,
    )
