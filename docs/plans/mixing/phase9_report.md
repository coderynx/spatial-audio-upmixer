# Phase 9 report — Loudness-domain energy renormalization

Plan: `phase9_loudness_renorm.md`. Verdict: **measured, offsets real, fix
shipped.** The step 1 gate (worst per-stem spread under ~1 LU → close without
change) was missed by a wide margin — up to **4.66 LU** spread inside a single
shipped preset — so step 2 landed.

Measurement kit: `packages/core/tests/test_mix_measurement.py` gained
measurement 5 (`test_routing_loudness_offset`), same perf marking and
deterministic pink-noise source as the rest of the kit. Each stem is routed
alone, its routed bed measured with BS.1770 (channel weights per BS.1770-5,
LFE excluded per standard, plus a variant weighting LFE for its +10 dB in-band
playback), and compared against the same signal measured as a stereo pair.

Run:

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s -k loudness
```

## 1. Before — what raw-energy matching cost

`route_scale = sqrt(input_energy / routed_energy)` equalized raw energy
exactly (measurement 4's non-LFE total was 1.0000 for every stem), and that is
precisely the problem: the surround send is high-passed at 250 Hz and the
height send runs the elevation EQ, so a send-routed stem had to be scaled *up*
to replace energy the filters removed — energy K-weighting barely counts. On
top of that, BS.1770-5 gives the side surrounds +1.5 dB.

### 5a. Per-stem loudness offset after routing, spread within preset (before)

| Layout | Preset | spread, LFE excluded (LU) | spread, LFE +10 dB (LU) | min / max, LFE excluded (LU) | worst stem, LFE +10 dB |
|---|---|---|---|---|---|
| stereo | balanced | 0.00 | 0.00 | +0.00 / +0.00 | Ride +0.00 |
| stereo | intimate | 0.00 | 0.00 | +0.00 / +0.00 | Vocals +0.00 |
| stereo | stage | 0.00 | 0.00 | +0.00 / +0.00 | Hi-Hat +0.00 |
| stereo | wide | 0.00 | 0.00 | +0.00 / +0.00 | Bass +0.00 |
| stereo | immersive | 0.00 | 0.00 | +0.00 / +0.00 | Lead Vocals +0.00 |
| stereo | live | 0.00 | 0.00 | +0.00 / +0.00 | Guitar +0.00 |
| 5.1 | balanced | 4.63 | 4.63 | +0.00 / +4.63 | Crowd +4.63 |
| 5.1 | intimate | 4.66 | 4.66 | -0.00 / +4.66 | Crowd +4.66 |
| 5.1 | stage | 4.63 | 4.63 | +0.00 / +4.63 | Crowd +4.63 |
| 5.1 | wide | 4.58 | 4.58 | -0.00 / +4.58 | Crash +4.58 |
| 5.1 | immersive | 3.93 | 3.93 | +0.00 / +3.93 | Other +3.93 |
| 5.1 | live | 2.82 | 2.82 | +0.00 / +2.82 | Crash +2.82 |
| 7.1.4 | balanced | 3.86 | 3.86 | +0.00 / +3.86 | Crowd +3.86 |
| 7.1.4 | intimate | 3.80 | 3.80 | +0.00 / +3.80 | Crowd +3.80 |
| 7.1.4 | stage | 3.86 | 3.86 | +0.00 / +3.86 | Crowd +3.86 |
| 7.1.4 | wide | 3.87 | 3.87 | -0.00 / +3.87 | Crowd +3.87 |
| 7.1.4 | immersive | 3.66 | 3.66 | +0.00 / +3.66 | Crowd +3.66 |
| 7.1.4 | live | 3.81 | 3.81 | +0.00 / +3.81 | Crowd +3.81 |

### 5b. Per-stem offset — balanced, 7.1.4 (before)

| Stem | offset, LFE excluded (LU) | offset, LFE +10 dB (LU) |
|---|---|---|
| Lead Vocals | +0.00 | +0.00 |
| Vocals | +0.00 | +0.00 |
| Backing Vocals | +0.43 | +0.43 |
| Bass | +0.00 | +0.07 |
| Kick | +0.00 | +0.09 |
| Snare | +0.00 | +0.00 |
| Toms | +0.01 | +0.01 |
| Drums | +0.00 | +0.01 |
| Hi-Hat | +0.58 | +0.58 |
| Ride | +0.93 | +0.93 |
| Crash | +1.30 | +1.30 |
| Guitar | +0.57 | +0.57 |
| Piano | +0.12 | +0.12 |
| Other | +0.30 | +0.30 |
| Instrumental | +0.08 | +0.10 |
| Crowd | +3.86 | +3.86 |

Three findings from the before tables:

- **Stereo is untouched by any of this.** A two-channel layout runs no
  surround or height send and has unity BS.1770 weights, so the loudness
  scalar *is* the energy scalar. Every stereo row reads 0.00.
- **The plan's LFE hypothesis does not survive measurement.** The "LFE +10 dB"
  column is within 0.09 LU of the standard column everywhere: the LFE bus is
  low-passed at 80 Hz, and K-weighting discards that band almost entirely.
  Weighting LFE into the scalar would move nothing audible, so it stays out
  of both sums (it is also added *after* its lowpass, so its pre-filter energy
  is not even the right term).
- **The driver is the send filtering, not the channel weights.** For Crowd on
  7.1.4 the +1.5 dB side-surround weight accounts for 0.65 dB of the 3.86 LU;
  the remaining ~3.2 LU is the 250 Hz high-pass on the surround send being
  compensated as if the removed bass had been audible at full weight.

The plan's candidate fix (channel-class weights on the raw energy terms) would
therefore have closed less than a fifth of the gap.

## 2. After — what shipped

`StemRouter._route_scale` (`packages/core/src/separation/stem_router.py`) now
builds both sides of the ratio from BS.1770 terms: per routed channel, the
channel's weight × gain² × the send signal's gated K-weighted mean square
(`loudness.k_weighted_power`, the standard's `z_i`), against the same measure
of the stem's own L/R. Still one scalar per stem per render — nothing
time-varying. LFE stays outside both sums, as before. When the material is too
short (under one 400 ms block) or too quiet to gate, it falls back to the old
raw-energy scalar, so short buffers behave exactly as they did.

### 5a. Spread within preset (after)

| Layout | Preset | spread, LFE excluded (LU) | spread, LFE +10 dB (LU) | worst stem, LFE +10 dB |
|---|---|---|---|---|
| stereo | all six | 0.00 | 0.00 | — |
| 5.1 | balanced | 0.00 | 0.09 | Kick +0.09 |
| 5.1 | intimate | 0.00 | 0.08 | Kick +0.08 |
| 5.1 | stage / wide / immersive / live | 0.00 | 0.09 | Kick +0.09 |
| 7.1.4 | balanced | 0.00 | 0.09 | Kick +0.09 |
| 7.1.4 | intimate | 0.00 | 0.08 | Kick +0.08 |
| 7.1.4 | stage / wide / immersive / live | 0.00 | 0.09 | Kick +0.09 |

Every per-stem offset is 0.00 LU with LFE excluded (worst |value| under
0.005 LU across all 18 layout × preset combinations). The residual 0.09 LU on
Kick is the deliberately unscaled LFE send seen through the +10 dB playback
weighting — that is the whole remaining spread.

### Zone energy accounting is no longer 1.0 (measurement 4 re-baselined)

Matching loudness instead of energy means a band-limited send zone lands below
its input energy by construction. Measurement 4's `abs(total - 1.0) < 1e-6`
assertion became a 0.2–2.0 sanity band; measurement 5 now carries the level
invariant. The phase 8 baseline's table 4 is superseded by:

| Stem | front | surround | height | LFE | non-LFE total |
|---|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Backing Vocals | 0.825 | 0.000 | 0.081 | 0.000 | 0.9061 |
| Bass | 1.000 | 0.000 | 0.000 | 0.014 | 1.0000 |
| Kick | 1.000 | 0.000 | 0.000 | 0.018 | 1.0000 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Toms | 0.997 | 0.001 | 0.001 | 0.001 | 0.9982 |
| Drums | 1.000 | 0.000 | 0.000 | 0.002 | 0.9999 |
| Hi-Hat | 0.768 | 0.000 | 0.107 | 0.000 | 0.8756 |
| Ride | 0.643 | 0.000 | 0.165 | 0.000 | 0.8079 |
| Crash | 0.520 | 0.000 | 0.222 | 0.000 | 0.7420 |
| Guitar | 0.811 | 0.059 | 0.008 | 0.000 | 0.8776 |
| Piano | 0.952 | 0.006 | 0.014 | 0.000 | 0.9722 |
| Other | 0.884 | 0.012 | 0.038 | 0.001 | 0.9333 |
| Instrumental | 0.965 | 0.000 | 0.016 | 0.004 | 0.9811 |
| Crowd | 0.000 | 0.287 | 0.124 | 0.000 | 0.4114 |

(balanced, 7.1.4 — the fraction of each stem's input energy that lands in each
zone. Crowd at 0.41 is the −3.9 dB of raw energy the old scalar was adding to
make a fully surround-routed stem "energy-correct".)

## 3. `_normalize_to_source`

`stem_pipeline.py::_normalize_to_source` matched the bed's total raw energy to
the source's, summing LFE and heights 1:1 with the fronts. It now matches
BS.1770 integrated loudness on both sides (`measure_integrated_loudness`, the
source's layout resolved with `detect_input_format` from the array it is
handed, so a wider input already folded to stereo measures as stereo). Same
fallback rule: too short, too quiet, or an unrecognized channel count → the
old energy scalar.

Measured on a 16-stem pink-noise bed, source = the stereo sum of the stems
(scratch measurement, balanced preset, 4 s):

| Layout | energy-match gain | loudness-match gain | delta |
|---|---|---|---|
| stereo | −0.00 dB | +0.00 dB | +0.00 dB |
| 5.1 | +0.28 dB | −0.00 dB | −0.28 dB |
| 7.1.4 | +0.44 dB | −0.00 dB | −0.44 dB |

Small, because it runs *after* the per-stem fix and the mastering chain's
BS.1770 normalization runs after it — what it changes is the level driving the
compressor and limiter, which is now consistent across layouts instead of
drifting up with channel count.

## 4. Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  **1114 passed, 32 deselected** (baseline 1110/31; +4 new tests, and the extra
  deselection is measurement 5, perf-marked).
- `uv run pytest packages/core/tests/test_mix_measurement.py -m perf -q` — 5
  passed.
- New tests:
  `test_stem_router.py::test_surround_routed_stem_lands_at_the_same_loudness_as_a_front_one`
  (the plan's synthetic case: identical noise routed as Lead Vocals vs Crowd
  on 7.1.4, now within 0.25 LU — it was +3.86 LU apart, §1),
  `::test_front_only_stereo_route_still_matches_raw_energy` (regression anchor:
  plain stereo renders do not move, 1e-6),
  `test_stem_normalize_to_source.py` (loudness match, and the short-material
  energy fallback).
- `apps/web`: `npm test` 249 passed, `npm run build` clean.
- Cost: the scalar needs a gated BS.1770 measurement per distinct send signal
  (at most 7 per stem — L, R, mono, and the surround/height pair when routed).
  Routing 16 stems of 60 s noise to 7.1.4 takes 3.6 s, of which ~2.5 s is the
  measurement (22.6 ms per 60 s channel). Offline export only; ~12 s on a
  five-minute track, against separation's minutes.
- **`npm run bench:engine` not run — the streaming path was not touched.** The
  scalar is computed in `packages/core` and reaches the worklet as the existing
  `route_scale` parameter; no `packages/dsp` change, so no wasm rebuild either.
- Parity: `docs/contracts/preview_export_parity.md` §3 and ledger D3 updated.
  `estimateRouteScale` now applies the BS.1770 side-surround weight (the part
  it can evaluate from the routing table); the K-weighting of the send chains
  stays out of reach without the decoded buffers, ~3.2 LU on a fully
  surround-routed stem, and remains D3's open residual.

## 5. Outstanding

The A/B listening note the plan asks for (surround-heavy preset, front-routed
vs surround-routed stem level match) has **not** been done — the objective
tables above are the only evidence so far. Worth doing on a real mix before
this is treated as perceptually settled, since every number here comes from
stationary pink noise, which loads the 250 Hz high-pass more heavily than most
program material.
