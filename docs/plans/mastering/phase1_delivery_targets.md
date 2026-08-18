# Phase 1 — Delivery targets and spec-correct immersive measurement

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phase 0 (its 5.1-fold delta audit decides how prominent the
fold-referenced number must be, and its report generator renders the
compliance output).

## Goal

Two changes that turn "a loudness slider" into "a delivery target":

1. **Measure immersive compliance on the 5.1 re-render.** The Dolby
   Atmos Music spec (−18 LKFS / −1 dBTP, the source of the config
   defaults) and the Netflix Atmos spec both measure integrated loudness
   on the 5.1 re-render of the mix, not the full bed. For layouts above
   5.1, add a BS.775-governed fold to 5.1 (heights into their base-layer
   channels, back pair into the surround pair — coefficients per
   `docs/standards/spatial_layouts_bs775_bs2051.md`, which this phase
   extends with the 5.1 fold table) and drive loudness normalization and
   the reported compliance number from the folded programme. The
   full-bed measurement stays available as a secondary diagnostic.
2. **Named delivery targets.** A `mastering.loudness.target_preset`
   manifest key (config: `loudness_target_preset`), resolving exactly
   like the existing comp/bass profiles — preset supplies values,
   individual fields override:

   | Preset | Integrated | Ceiling | Notes |
   |---|---|---|---|
   | `atmos-music` | −18 LKFS | −1.0 dBTP | measured on 5.1 re-render; the current defaults, now named |
   | `netflix-atmos` | −27 LKFS | −2.0 dBTP | fold-referenced; tolerance ±2 LU recorded in the report (dialog gating is a non-goal — note the deviation) |
   | `ebu-r128` | −23 LUFS | −1.0 dBTP | ±0.5 LU tolerance |
   | `atsc-a85` | −24 LKFS | −2.0 dBTP | ±2 LU tolerance |
   | `streaming-stereo` | −14 LUFS | −1.0 dBTP | stereo/binaural/transaural deliveries |
   | `apple-music` | −16 LUFS | −1.0 dBTP | stereo deliveries |
   | (none) | free sliders | free | current behavior, unchanged default |

   Tolerances live with the preset so the compliance report can render
   pass/fail, not just numbers.

## Deliverables

1. Core: the 5.1 fold for measurement (in `dsp-core` next to the stereo
   downmix kernel — one fold implementation, offline + streaming), the
   `measurement_programme` selection in `normalize_loudness` /
   `MasteringChain`, and preset resolution in config/manifest (register
   via `register_block_keys` like every other mastering block).
2. Compliance fields in `MasteringResult`: target preset name, target
   value, tolerance, pass/fail, plus the fold-referenced vs full-bed
   pair for immersive layouts.
3. API: jobs schema (`apps/api/src/features/jobs/schemas.py`) carries the
   measured/compliance block; the preset list is served with
   configuration choices. Manifest parity per
   `docs/project_manifest_parity.md`.
4. Web: the Loudness effect panel gains a target picker (preset +
   the existing sliders as overrides), and finished jobs show measured
   LKFS / dBTP / pass-fail. Preview correction
   (`audioEngine.ts::loudnessGainFor`) reads the same resolved target, and
   the preview's measurement pass measures the folded programme for
   immersive layouts so preview and export normalize to the same number
   (`stream/measure.rs` forks the engine — fold at the meter input,
   exactly as the export does).
5. Parity contract §1–§3 updated (new measurement programme is shared
   code; the served preset table is a §2 constant).
6. `docs/standards/loudness_dsp_bs1770.md` gains a "measurement
   programme" section citing the Atmos/Netflix fold-referenced rule;
   `spatial_layouts_bs775_bs2051.md` gains the 5.1 fold coefficients.

## Watch out for

- **Normalization loop:** normalizing to the folded measurement while
  processing the full bed is still a single scalar gain — no iteration
  needed (scalar gain commutes with the fold). Keep it that way.
- The stereo/binaural/transaural delivery paths already measure their own
  two-channel programme — presets apply, the fold does not. Guard the
  fold behind layout arity, not output type.
- `loudness_max_gain_db = 30` stays, but a preset that would engage more
  than the preset's documented tolerance in limiter GR must surface in
  the compliance block (phase 0's GR stats make this a report row, not a
  new mechanism).
- Do not rename existing manifest keys; `loudness.target` / `max_tp`
  keep working as overrides.

## Validation

- Golden test: synthetic 7.1.4 programme with height-only content —
  fold-referenced measurement matches a hand-computed BS.775 fold; full
  suite green.
- `stream_equivalence.rs` covers the streaming fold meter against the
  offline one.
- Phase report: re-run phase 0's compliance table on both test
  programmes under `atmos-music` and `streaming-stereo`; A/B note that
  normalization landing on the folded number is audibly equivalent or
  better on height-heavy material.
- `npm run bench:engine` (measurement pass gained a fold stage).
