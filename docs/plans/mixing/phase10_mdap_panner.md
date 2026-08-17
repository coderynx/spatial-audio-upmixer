# Phase 10 — MDAP panner behind the placement model

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 8 (fresh baseline); run after phase 9 so the energy
accounting used for validation is loudness-consistent. Largest redesign
of the remaining phases.

## Goal

`stem_placement.py::placement_route` pans with a raised-cosine window
over angular distance — effectively distance-based panning with spread.
Its weaknesses: gain spills to many speakers (blurred localization,
multi-speaker timbre shift), and perceived spread depends on speaker
density around the target, not on the placement's `width_deg`/`spread_deg`
alone. Replace the *internals* with MDAP (Multiple-Direction Amplitude
Panning): VBAP triplet gains for a set of virtual sources distributed
across the placement's width, summed and power-normalized. MDAP keeps
point placements confined to the nearest speaker triplet (VBAP accuracy)
while making spread direction-independent — exactly what the "stage"
preset's point placements (guitar 48°, piano −48°) need.

The **placement model is the public contract and does not change**:
`StemPlacement(azimuth_deg, elevation_deg, width_deg, spread_deg, lfe)`,
the preset tables, `resolve_placements`, `_project` (elevation-to-width
flattening for heightless layouts), `STEREO_PLACEMENT_LAYOUT` fold path,
and `MINIMUM_SEND` all stay. Only `placement_route`'s math is replaced.
Note: presets were re-tuned in commit `658bbb5` ("rework stem placement
presets") — read that commit's reasoning before re-voicing anything.

## Design points (decide, document in module docstring, implement)

1. **Triangulation.** VBAP needs a speaker mesh. Layouts here are few and
   fixed (`FORMAT_MAP` × `SPEAKER_AZIMUTH_ELEVATION`); precompute the
   triplet set per layout. Heightless layouts degenerate to 2D pairwise
   VBAP on the horizontal ring. Handle the layouts' actual geometry —
   e.g. 5.1's 80° front-to-surround gap, and no speaker below the
   horizontal plane (elevation clamps at 0 from below).
2. **Virtual-source set.** Map `width_deg` to the angular span of the
   virtual sources (e.g. edge points ± intermediate points every ~15°)
   and `spread_deg` to MDAP's spread ring around each point. Keep the
   two knobs' perceptual meaning close to today's so preset tables stay
   valid; verify with the energy-accounting comparison below.
3. **Out-of-hull directions.** Placements behind/above what a layout
   covers (Crowd at 180° on 5.1, elevated placements after `_project`)
   must not silently vanish — project onto the convex hull edge like the
   current window's "span widens to the nearest speaker" behavior. State
   the rule.
4. **Determinism.** Pure function of (placement, layout), no RNG, exact
   same output on Python and any future port. Gains are computed
   server-side and travel as routing maps, so no wasm/worklet change is
   expected — verify that assumption against the parity contract and
   record it; if the web computes any placement locally, it must get the
   same math.

## Deliverables

1. New panner in `stem_placement.py` (split per file-size policy if
   needed — sibling module `stem_panner.py` following the flat-module
   convention, re-exported so import paths hold).
2. `placement_route` keeps its signature and its callers
   (`preset_routing`, `build_stem_routing`, scene drag path with
   `SCENE_PLACEMENT_SPREAD_DEG`) untouched.
3. Measurement kit extension: localization concentration metric — for a
   point placement (width 0), fraction of routed power inside the
   containing triplet (MDAP target: ~1.0; report today's number for
   contrast), and spread linearity — perceived-width proxy (power-
   weighted angular spread) vs `width_deg` across azimuths, which should
   flatten vs today.
4. Preset re-check: energy-accounting table (phase 8 kit) per preset
   before/after. Placements whose realized channel distribution moves by
   more than ~2 dB in any zone get listened to; re-voice the placement
   only if the A/B says the new realization is wrong, and record each
   change against `658bbb5`'s rationale.

## Tests

- Panner unit tests: point placement inside a triplet → gains only on
  that triplet, power-normalized; placement exactly on a speaker → that
  speaker dominant; azimuth sweep continuity (no gain jumps > small
  epsilon per degree — the raised-cosine panner is continuous, MDAP must
  stay so); out-of-hull rule; stereo fold unchanged in kind.
- `MINIMUM_SEND` floor still applied; LFE passthrough untouched.
- Full suites green (baseline 1107/31). Existing routing tests will
  change numerically — regenerate expectations knowingly, listing every
  changed expectation in the PR.

## Out of scope

- Preset table redesign beyond the re-check above.
- Time-varying panning, scene animation.
- `ZONE_ROUTING` hand-authored tables (multichannel-input zone routing is
  not placement-derived; untouched).

## Done when

- Localization/spread metrics show triplet confinement and flatter
  width response; tables in `docs/plans/mixing/phase10_report.md`.
- A/B listening note: "stage" preset (point placements — the motivating
  case) and "balanced" (wide symmetric case — must not audibly change).
- Parity assumption (server-side gains only) verified and recorded.
