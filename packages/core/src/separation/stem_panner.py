"""MDAP panning of a stem placement into speaker gains.

Multiple-Direction Amplitude Panning: a placement becomes a set of virtual
sources spanning its width, each panned by VBAP onto one speaker simplex; the
gain vectors sum and the result is normalized to constant power. VBAP holds a
point placement on the simplex that contains it instead of leaking into every
speaker within a falloff radius, and the virtual-source set — rather than how
densely the layout happens to be populated around the target — is what decides
the image's width.

Simplices
  VBAP needs a triangulation, not just a speaker set: overlapping candidate
  triplets would let a direction resolve onto a wide triplet that skips the
  speakers it sits between. The simplices are the facets of the speakers' own
  convex hull, derived per layout and cached — a candidate pair (flat layout)
  or triplet (layout with heights) survives only when every other speaker lies
  on the listener's side of its plane, which for the basis ``B`` is
  ``q · B⁻¹ · 1 ≤ 1``. No hull library: the layouts are small and fixed, and
  the test is one expression over the basis inverse the panning already needs.
  Three floor speakers are coplanar with the listener, so their basis is
  singular and they drop out; a floor-level direction is carried by the side
  facets (two floor speakers plus a height) whose height gain solves to exactly
  zero, which is pairwise panning on the horizontal ring by another route. Flat
  layouts have no height to lean on and are solved in the horizontal plane
  directly, where the same test picks out the ring's adjacent pairs.

Spread
  ``spread_deg`` blurs each virtual source horizontally, by
  ``SPREAD_RING_FACTOR`` of its value to either side. The blur is horizontal
  and not a full ring on purpose: elevation is what puts a placement overhead,
  and a vertical blur would lift part of every floor-level stem into the height
  layer — the lifted-bed sound the preset tables are voiced against.

Coplanar walls
  A layout's rear wall and its height layer are flat: four speakers in one
  plane admit both diagonals as hull facets, so "the" triangulation is not
  unique there and picking one by score makes the gains jump where the choice
  flips. Every facet holding the direction contributes instead, averaged.
  Each of them reproduces the direction exactly, so their mean does too, and a
  mean of continuous solutions stays continuous where the set of holders
  changes.

Out of hull
  Elevation is clamped to what the layout spans (nothing below the horizontal
  plane, nothing above the height layer). A direction no simplex holds resolves
  to the simplex with the least negative gain, negatives clamped to zero, which
  projects it onto the nearest hull edge — the rear of a 5.1 bed lands on its
  side pair this way. If that leaves nothing, which only the two-speaker bed's
  rear half can do, the layout is weighted by cosine similarity instead so the
  placement degrades toward the back of the pair rather than vanishing.

Determinism
  Pure function of (placement, layout): no RNG, fixed iteration order, ties
  resolved by the first candidate in sorted order. ``apps/web``'s
  ``lib/spatial.ts`` carries the same maths for the preview's scene-drag
  fallback.
"""
from __future__ import annotations

import math
from functools import lru_cache
from itertools import combinations

import numpy as np

from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
from upmixer.formats import ChannelLabel

VIRTUAL_SOURCE_STEP_DEG = 15.0
"""Angular spacing of the virtual sources spanning a placement's width. Finer
than the tightest speaker spacing in any supported layout, so a width reads as
an arc rather than as its two edges."""

SPREAD_RING_FACTOR = 0.5
"""Half-width of the blur applied to each virtual source, as a fraction of
``spread_deg`` — so the blur spans ``spread_deg`` in total. Also where the
perceived width of the shipped preset tables moves least against the panner
this replaced, within ~2° on average: see
docs/plans/mixing/phase10_report.md §3."""

_SINGULAR_BASIS = 1e-6
_FACET_EPS = 1e-9


def direction(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    """Unit vector in ``binaural.geometry``'s convention: +azimuth = left."""
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    return (
        -math.cos(elevation) * math.sin(azimuth),
        math.sin(elevation),
        -math.cos(elevation) * math.cos(azimuth),
    )


@lru_cache(maxsize=None)
def _speakers(labels: tuple[ChannelLabel, ...]) -> tuple[np.ndarray, list[int]]:
    """(unit vectors, the axes a direction is solved in for this layout)."""
    positions = [SPEAKER_AZIMUTH_ELEVATION[label] for label in labels]
    vectors = np.array(
        [direction(position.azimuth_deg, position.elevation_deg) for position in positions]
    )
    axes = [0, 1, 2] if any(position.elevation_deg > 0.0 for position in positions) else [0, 2]
    return vectors, axes


@lru_cache(maxsize=None)
def _simplices(labels: tuple[ChannelLabel, ...]) -> tuple[np.ndarray, np.ndarray]:
    """(speaker indices per simplex, inverse basis per simplex)."""
    vectors, axes = _speakers(labels)
    coordinates = vectors[:, axes]
    members: list[tuple[int, ...]] = []
    inverses: list[np.ndarray] = []
    for combination in combinations(range(len(labels)), len(axes)):
        basis = coordinates[list(combination)]
        if abs(float(np.linalg.det(basis))) < _SINGULAR_BASIS:
            continue
        inverse = np.linalg.inv(basis)
        if np.any(coordinates @ inverse @ np.ones(len(axes)) > 1.0 + _FACET_EPS):
            continue
        members.append(combination)
        inverses.append(inverse)
    return np.array(members), np.array(inverses)


def _pan(directions: np.ndarray, labels: tuple[ChannelLabel, ...]) -> np.ndarray:
    """VBAP gains, one row of speaker gains per direction."""
    vectors, axes = _speakers(labels)
    members, inverses = _simplices(labels)
    points = directions[:, axes]
    norms = np.linalg.norm(points, axis=1, keepdims=True)
    points = points / np.where(norms > 0.0, norms, 1.0)

    gains = np.einsum("ds,ksi->kdi", points, inverses)
    per_simplex = np.zeros((len(members), len(points), len(labels)))
    for index, simplex in enumerate(members):
        per_simplex[index][:, simplex] = np.maximum(gains[index], 0.0)

    holding = (gains.min(axis=2) >= -_FACET_EPS).astype(float)
    outside = holding.sum(axis=0) == 0.0
    holding[np.argmax(gains.min(axis=2), axis=0)[outside], np.flatnonzero(outside)] = 1.0
    panned = np.einsum("kd,kdn->dn", holding, per_simplex) / holding.sum(axis=0)[:, None]

    unreachable = panned.sum(axis=1) <= 0.0
    if unreachable.any():
        similarity = points[unreachable] @ vectors[:, axes].T
        panned[unreachable] = np.maximum(0.0, 0.5 * (1.0 + similarity))
    return panned


def _virtual_sources(
    azimuth_deg: float,
    elevation_deg: float,
    width_deg: float,
    spread_deg: float,
    labels: tuple[ChannelLabel, ...],
) -> np.ndarray:
    positions = [SPEAKER_AZIMUTH_ELEVATION[label] for label in labels]
    elevation = min(
        max(elevation_deg, 0.0), max(position.elevation_deg for position in positions)
    )
    width = max(0.0, width_deg)
    count = 1 if width <= 0.0 else max(2, math.ceil(width / VIRTUAL_SOURCE_STEP_DEG) + 1)
    azimuths = (
        [azimuth_deg]
        if count == 1
        else [azimuth_deg - width / 2.0 + width * index / (count - 1) for index in range(count)]
    )
    ring = SPREAD_RING_FACTOR * max(0.0, spread_deg)
    offsets = (0.0,) if ring <= 0.0 else (-ring, 0.0, ring)
    return np.array(
        [direction(azimuth + offset, elevation) for azimuth in azimuths for offset in offsets]
    )


def panning_gains(
    azimuth_deg: float,
    elevation_deg: float,
    width_deg: float,
    spread_deg: float,
    labels: tuple[ChannelLabel, ...],
) -> dict[str, float]:
    """Constant-power speaker gains for one placement, keyed by channel name."""
    if not labels:
        return {}
    directions = _virtual_sources(azimuth_deg, elevation_deg, width_deg, spread_deg, labels)
    summed = _pan(directions, labels).sum(axis=0)
    norm = float(np.linalg.norm(summed))
    if norm <= 0.0:
        return {}
    return {label.value: float(gain) / norm for label, gain in zip(labels, summed)}
