# Phase 10 report — MDAP panner behind the placement model

Plan: `phase10_mdap_panner.md`. Verdict: **shipped, with the preset re-check
open for a listening pass.** The panner is replaced, the localization and
spread metrics moved the way the plan predicted, and the placement model,
preset tables and every public entry point are untouched. What is *not* closed:
the A/B listening note. No listening was possible in this environment, and 54
of 96 placements move more than 2 dB in some zone (§4), so the tables are
shipped as voiced and the listening pass is a follow-up, not a formality.

Measurement kit: `test_mix_measurement.py` (measurements 1–5, unchanged) plus
the new `packages/core/tests/test_mix_localization.py` (measurement 6), kept in
its own file for the file-size policy. Run:

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s
uv run pytest packages/core/tests/test_mix_localization.py -m perf -s
```

Both are deterministic and model-free. Every number below is a verbatim run,
before = `74aaa40` (phase 9), after = this phase.

## 1. What replaced what

`placement_route` used a raised-cosine window over angular distance:
`cos(pi/2 · min(1, d/span))` per speaker, `span = max(spread_deg, nearest
distance)`, with elevation counted 1.6× in the distance metric
(`ELEVATION_DISTANCE_WEIGHT`). Two failures follow from that shape and both are
visible in §2: a point placement leaks into every speaker inside its falloff
radius, and the realized width depends on how densely the layout is populated
around the target — the same `width_deg` reads as 9° of spread in one direction
and 45° in another.

It is now MDAP (`packages/core/src/separation/stem_panner.py`): virtual sources
every `VIRTUAL_SOURCE_STEP_DEG` (15°) across `azimuth ± width/2`, each blurred
±`SPREAD_RING_FACTOR · spread_deg` in azimuth, each panned by VBAP, gain
vectors summed and L2-normalized. The module docstring carries the design
decisions; the contract table is in
`docs/standards/spatial_layouts_bs775_bs2051.md` § "Stem placement and the
routing presets".

Three decisions worth restating here:

- **Triangulation without a hull library.** A candidate simplex — a pair on a
  flat layout, a triplet on a layout with heights — is kept only if it is a
  facet of the speakers' convex hull, which for basis `B` is
  `q · B⁻¹ · 1 ≤ 1` for every other speaker `q`. It reuses the basis inverse
  the panning already needs. Pulkki's original "no speaker inside the triplet"
  rule was tried first and is *not* sufficient here: it left 51 candidate
  triplets on 7.1.4, including ones spanning half the sphere, and a direction
  at (0°, 35°) resolved onto `FL`+`TBR`+`TFR`. The hull test leaves 17.
- **Coplanar walls are averaged, not chosen between.** The rear wall
  (`BL`/`BR`/`TBL`/`TBR`) and the height layer are exactly coplanar — four
  points, two valid diagonals, both of them genuine hull facets. Scoring them
  against each other makes the gains jump by up to 0.43 where the winner
  flips. Every facet holding the direction contributes, averaged: each
  reproduces the direction exactly, so the mean does too, and continuity
  survives the changeover (§3).
- **The blur is horizontal.** A spherical spread ring would lift part of every
  floor-level stem into the height layer. Elevation is what puts a placement
  overhead; the ring stays in azimuth, and a floor placement reaches the height
  channels exactly not at all (`test_a_floor_placement_is_not_lifted_into_the_height_layer`).

`ELEVATION_DISTANCE_WEIGHT` is gone with the window that needed it.

## 2. Localization concentration (measurement 6a/6b)

"Concentration" is the power fraction carried by one panning simplex — the
three loudest speakers on a layout with heights, the two loudest on a flat one.
"Routed speakers" is how many channels the map touches at all.

### 6a. Point placement (width 0, spread 60°), 5.1

| azimuth | elevation | routed speakers before → after | concentration before → after | spread ° before → after |
|---|---|---|---|---|
| 0 | 0 | 3 → 5 | 0.749 → 0.667 | 21.2 → 24.4 |
| 0 | 20 | 3 → 5 | 0.784 → 0.667 | 19.6 → 24.4 |
| 30 | 0 | 3 → 3 | 1.000 → 0.941 | 14.1 → 24.1 |
| 60 | 0 | 2 → 2 | 1.000 → 1.000 | 26.2 → 36.8 |
| 90 | 0 | **1** → 3 | 1.000 → 0.991 | **0.0** → 32.6 |
| 135 | 0 | **1** → 3 | 1.000 → 1.000 | **0.0** → 58.0 |
| 180 | 0 | 5 → 2 | **0.400** → **1.000** | 72.0 → 70.1 |

The two failures are the bolded cells. At 90° and 135° the old window collapsed
a *spread* placement onto a single speaker — `span` fell back to the nearest
distance and every other speaker landed at or past the zero of the cosine — so
`spread_deg` did nothing at all there. At 180° it did the opposite and sprayed
a point across all five speakers at 40% concentration. MDAP gives the rear
direction its side pair (1.000) and gives 90°/135° a real, non-degenerate
image.

### 6a. Point placement (width 0, spread 60°), 7.1.4

| azimuth | elevation | concentration before → after | spread ° before → after |
|---|---|---|---|
| 0 | 0 | 1.000 → 1.000 | 21.2 → 24.4 |
| 0 | 20 | 0.709 → 0.886 | 29.3 → 31.5 |
| 30 | 0 | 1.000 → 1.000 | 14.4 → 24.1 |
| 30 | 20 | 1.000 → 0.917 | 20.7 → 27.9 |
| 60 | 0 | 1.000 → 1.000 | 26.2 → 36.8 |
| 90 | 0 | 1.000 → 1.000 | 9.3 → 34.6 |
| 90 | 20 | 0.999 → 0.986 | 15.4 → 37.3 |
| 135 | 0 | 1.000 → 0.998 | 12.5 → 24.1 |
| 180 | 0 | 1.000 → 1.000 | 45.0 → 45.0 |
| 180 | 20 | 0.885 → 0.800 | 41.4 → 46.0 |

Floor-level directions are fully triplet-confined. Elevated ones sit at
0.80–0.99 rather than 1.0, and that is the honest reading: an elevated
direction between the floor ring and the height layer *is* reproduced by four
or five speakers, and the metric's three-speaker simplex cannot hold it. The
old panner scored 1.000 in some of those cells by not realizing the elevation
at all (§4).

### 6b. Off-centre preset placements (7.1.4)

| Preset | Stem | azimuth | width | concentration before → after |
|---|---|---|---|---|
| balanced | Crowd | 180 | 120 | 0.578 → 0.799 |
| intimate | Crowd | 180 | 88 | 0.772 → 0.963 |
| stage | Toms | −18 | 52 | 1.000 → 0.979 |
| stage | Hi-Hat | 32 | 36 | 1.000 → 0.914 |
| stage | Ride | −36 | 36 | 1.000 → 0.896 |
| stage | Guitar | 48 | 52 | 0.998 → 1.000 |
| stage | Piano | −48 | 52 | 0.996 → 0.972 |
| wide | Crowd | 180 | 132 | 0.565 → 0.713 |
| immersive | Crowd | 180 | 132 | 0.679 → 0.871 |
| live | Crowd | 180 | 150 | 0.545 → 0.705 |

The motivating case — "stage"'s point placements — was already well localized
on the old panner at floor level (Guitar, Piano) and stays so. Where the old
panner was worst is `Crowd`: a wide rear placement smeared across 6–10 speakers
at 0.55 concentration, now 0.71–0.96. `stage`'s Hi-Hat and Ride lose
concentration because their 14–16° elevation is now realized across the
floor-to-height arc instead of being flattened onto the front wall.

## 3. Spread linearity (measurement 6c) — the headline result

Power-weighted angular spread (°) at fixed `width_deg`, swept across azimuth.
The "range" column is max − min: how much the *same* width knob means
different things in different directions. Lower is better; a flat column is the
goal.

### 7.1.4

| width_deg | range before | range after |
|---|---|---|
| 0 | 35.7 | **20.9** |
| 30 | 33.7 | **19.7** |
| 60 | 39.5 | **17.5** |
| 90 | 30.0 | **11.6** |
| 120 | 27.8 | **4.8** |

### 5.1

| width_deg | range before | range after |
|---|---|---|
| 0 | 72.0 | **46.1** |
| 30 | 70.1 | **44.8** |
| 60 | 70.1 | **42.5** |
| 90 | 70.1 | **36.0** |
| 120 | 36.0 | **28.0** |

The width response is also monotone now, which it was not. On 5.1 at 135° the
old panner produced 0.0° of spread for widths 0, 30, 60 and 90 and then 45.1°
at 120 — the knob did nothing until it did everything. Every azimuth now widens
smoothly with `width_deg`. The residual range is layout geometry: 5.1's 140°
rear gap cannot render a narrow image at 180° no matter what the panner does,
and that floor (70.1°) is what keeps 5.1's range high.

Continuity, swept at 0.5° steps over the full circle at 0°/20°/35° elevation
(`test_azimuth_sweep_has_no_gain_jumps`): the largest per-step gain change is
0.007 on 7.1.4 and 0.006 on 5.1/5.1.2/5.1.4/7.1/7.1.2 — panning slope, not a
switch. The bare `stereo` bed is the one exception: behind ±169° it crosses
from clamped VBAP into the cosine-similarity fallback with a 0.34 step. That
layout has no hull edge to project onto behind the pair, the fallback exists so
rear content stays audible and symmetric, and production stereo never takes
this path (`build_stem_routing` pans stereo on 7.1.4 and folds).

## 4. Preset re-check (measurement 4)

### 4b. Zone fraction averaged over all 16 stems, 7.1.4

| Preset | front before → after | surround before → after | height before → after |
|---|---|---|---|
| balanced | 0.835 → 0.812 | 0.023 → 0.029 | 0.048 → 0.055 |
| intimate | 0.890 → 0.914 | 0.028 → 0.029 | 0.019 → 0.012 |
| stage | 0.825 → 0.782 | 0.023 → 0.035 | 0.053 → 0.060 |
| wide | 0.728 → 0.694 | 0.021 → 0.026 | 0.101 → 0.112 |
| immersive | 0.618 → 0.567 | 0.014 → 0.008 | 0.159 → 0.190 |
| live | 0.768 → 0.737 | 0.020 → 0.018 | 0.083 → 0.100 |

Preset character holds: the front-to-height ordering across presets is
unchanged and every preset moves in the direction it already leaned.

Per placement it is coarser. Measured on the routes themselves (zone power,
7.1.4, zones under 2% ignored), **54 of 96 placements move more than 2 dB in
some zone**, and on 5.1, 48 of 96. Per preset, over 2 dB: balanced 8, intimate
7, stage 10, wide 9, immersive 11, live 9. Two mechanisms account for nearly
all of it:

1. **Elevation is realized.** The old distance metric counted elevation 1.6×
   against a height layer only ~35° up, so placements below ~20° of elevation
   barely reached the height channels and placements above it went almost
   entirely overhead. VBAP interpolates between the floor ring and the height
   layer by geometry, so a 16° placement now images at 16°. `intimate` Ride
   (elevation 10°) goes from 0.000 to 0.164 height power; `immersive` Crash
   (elevation 36°) loses front power it should not have had at that elevation.
   The tables were voiced against the compressed response, so **elevated
   placements now sit lower than they used to sound** even though their numbers
   are unchanged.
2. **Wide placements reach their own edge.** The old window put its outermost
   energy at exactly the zero of the cosine — a placement of width `w` and
   spread `s` reached `w/2 + s` with *zero* gain there. MDAP's blur reaches
   `w/2 + s/2` with real gain. Hence the long list of surround zones moving
   from exactly 0.000 to 0.02–0.10 (`balanced` Instrumental 0.000 → 0.078,
   `stage` Ride 0.000 → 0.100). In absolute terms these are −10 to −13 dB
   sends; as a dB *change* they read as +25 to +30 because they started at
   silence.

Per the plan, nothing was re-voiced: `658bbb5`'s reasoning was about where
stems sit, not about the window that realized it, and re-voicing without an A/B
would be guessing. The listening pass has a concrete question to answer — do
the elevated placements (Hi-Hat, Ride, Crash, Backing Vocals in `wide`,
`immersive`, `live`) want their `elevation_deg` raised now that it is honoured
literally?

Perceived width, which is what `width_deg`/`spread_deg` are voiced against,
moves much less: over every placement of every preset on 5.1 and 7.1.4, the
angular-spread error against the old panner is **−2.2° mean, 6.2° mean
absolute**, which is what set `SPREAD_RING_FACTOR = 0.5` (sweep: 0.15 → −7.4°
mean, 0.30 → −5.9°, 0.45 → −3.3°, 0.50 → −2.2°, 0.60 → +0.4° with a worse mean
absolute). 0.5 also gives `spread_deg` a clean reading: the blur spans exactly
`spread_deg`, half to either side.

## 5. What did not move

- **Loudness (measurement 5).** Phase 9's invariant survives the new panner
  untouched: per-stem loudness spread within a preset is 0.00 LU on every
  layout, 0.09 LU with LFE weighted at +10 dB (Kick, on the LFE send, on every
  preset). A panner change could have re-opened this and did not.
- **The placement model and its public surface.** `StemPlacement`,
  `resolve_placements`, `_project`, `HEIGHT_FLATTEN_WIDTH_FACTOR`,
  `STEREO_PLACEMENT_LAYOUT`, `SCENE_PLACEMENT_SPREAD_DEG`, `MINIMUM_SEND`,
  `preset_routing`, `placement_route`'s signature, `build_stem_routing`, the
  scene-drag path and `ZONE_ROUTING` are all unchanged.
- **The Rust core and the worklet.** Verified against the parity contract: no
  stage in `packages/dsp` knows what a placement is (`routing::scene` in the
  contract's §1 table named a module that does not exist; corrected to
  `stem_panner.py`). Gains are computed in Python and travel as routing maps,
  so no wasm rebuild and no `bench:engine` run applies to this phase.

## 6. Parity

The plan's assumption — "gains are computed server-side, so no wasm/worklet
change is expected" — held for the Rust core but **not** for the browser:
`apps/web/src/lib/spatial.ts` carried `routingFromAzimuthElevation`, a hand-port
of the raised-cosine panner used when a scene-positioned stem has no resolved
routing yet. Left alone it would have previewed a placement the export can no
longer produce.

It was deleted rather than re-ported. `apps/web/AGENTS.md` forbids DSP in the
web layer and the parity contract's whole premise is that there is one
implementation; porting VBAP into TypeScript would have re-created the class of
bug the contract exists to prevent. Routing now reaches the preview only as
maps the core computed. Recorded as ledger **D34**. The cost is that a stem
with a scene position and no routing resolved yet previews silent instead of
approximately — one request's window, and if that ever matters the fix belongs
in the API (serve the core's routing), not in a second panner.

## 7. Validation

```
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
1133 passed, 34 deselected

cd apps/web && npm test    →  31 files, 244 tests passed
cd apps/web && npm run build  →  built in 1.44s
```

Baseline was 1114 passed / 32 deselected; this phase adds
`test_stem_panner.py` (19 tests) and `test_mix_localization.py` (2, perf-marked).

Two existing expectations were regenerated knowingly:

- `test_stem_router.py::test_main_bed_routing_is_constant_power` summed
  `FL/FR/C/TFL/TFR` only and asserted 5 significant digits against the input
  energy. `Vocals` now puts 0.03 of its gain into `SL`/`SR`, which that list
  misses; it sums the whole bed and allows `rtol=1e-3`, the slack phase 9's
  loudness-domain `route_scale` introduced (the send chains are band-limited, so
  raw energy is no longer conserved exactly — 1919.71 of 1920).
- `test_stem_router.py::test_generic_and_percussion_defaults_start_conservative`
  asserted `hi_hat["TFL"] < hi_hat["FL"]`. With elevation realized, balanced
  Hi-Hat (16°) puts more into `TFL` than into `FL` while the *front zone* still
  leads the height zone (0.52 vs 0.47). The overhead ladder it protects —
  Other front-dominant, Hi-Hat front-leaning, Crash overhead — is now asserted
  as zone power, which is what the voicing actually means.

## 8. Still open

- **A/B listening note.** Required by the plan, not delivered: no listening in
  this environment. Protocol:
  `~/Projects/upmixer-knowledge/techniques/evaluation.md` §6. Two cases to
  judge — "stage" (the point placements, the motivating case) and "balanced"
  (must not audibly change) — plus the §4 question about whether the elevated
  placements want re-voicing now that `elevation_deg` is honoured literally.
- Until that pass happens, treat the preset tables as unverified against the
  new realization, not as re-voiced.
