# Phase 14 — Precomputed spatial automation (optional, product-heavy)

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phases 11–13 merged (the automation rides on the final send/stem
architecture). Optional: this is a product feature with a large API/UI
surface, not a DSP fix — ship it only if the product wants "suggested
spatial automation"; the DSP half alone is not worth the surface area.

## Goal

Static routing cannot do what human Atmos mixes do per section: open the
heights in the chorus, pull the surrounds in for the verse, widen backing
vocals when the arrangement thickens. The phase 11 archaeology killed
*hidden whole-file* adaptation — the displayed routing must never lie —
but its constraints leave exactly one honest path open, and this phase is
that path:

- analysis runs at **prepare time** (non-causal is fine there; stems
  themselves are whole-file inference),
- its output is **data** — per-stem automation curves both export and
  preview consume identically (parity by construction, like stems and
  routing maps),
- the curves are **visible and editable** in the web UI before anything
  renders — analysis proposes, the user owns. This converts the
  archaeology's product objection ("a hidden scale makes the displayed
  routing a lie") into the feature itself.

## Slices (each independently shippable; stop after any of them)

1. **Curve plumbing without analysis.** The data model and both
   consumers, seeded by hand-authored curves:
   - Curve format: per stem, a sparse breakpoint envelope (time,
     value) per automatable parameter. First parameters: stem gain and
     send openness (a 0–1 scalar the router maps onto its
     surround/height send levels — it scales the *send input*, in the
     duck's insertion region, not the user's routing gains; the phase 11
     "shape the signal, not the gain" rule holds).
   - Manifest block + config representation (headless CLI parity),
     API schema on the project surface, persistence with the project.
   - Export: `StemRouter.route` samples the envelope (interpolation
     convention documented; `SpatialPlan`'s hop interpolation is the
     in-repo precedent). Preview: the streaming engine takes the same
     breakpoints over the wire and interpolates identically —
     `dsp-core` once, both bindings, golden test on the interpolation.
   - Web UI: an automation lane on the routing surface — read the
     existing routing-matrix UX before inventing one; smallest honest
     editor wins (breakpoints on a timeline, per stem, per parameter).
2. **Suggested curves from analysis.** Prepare-time section analysis on
   the cached stems: novelty/self-similarity segmentation (energy +
   spectral contrast per stem is enough for verse/chorus contrast; no
   new dependencies — hand-rolled on the STFT machinery core already
   has). Output: proposed curves (chorus → backing-vocal/height
   openness up; sparse sections → surrounds in), clearly marked as
   suggestions in the UI, applied only when the user accepts (or when a
   headless flag opts in: `--spatial-automation suggest|off`, default
   off).
3. **Per-preset suggestion voicing.** How strongly suggestions move per
   preset ("intimate" barely moves, "immersive" moves most). Only if 2
   ships and listening asks for it.

## Constraints

- Analysis code lives in core (`packages/core/src/analysis/` is the
  natural home); web stays delivery-only; the analyzer and its consumer
  ship together (phase 11 failure-mode 3 — no orphaned analyzer
  modules).
- Curves are bounded (document min/max per parameter) and default-empty:
  no curves = bit-identical to phase 13 head, asserted by test.
- Parity: new wire surface (breakpoints) goes through the
  engine-constants/params path, with the parity contract's §1 table and §5
  parameter seam updated (a new wire surface is exactly D30's failure mode);
  interpolation golden-tested cross-binding. Bench: interpolation is
  near-free, but run `npm run bench:engine` anyway and report.

## Tests

- Envelope sampling: golden vectors across bindings; breakpoint edge
  cases (empty, single point, points beyond track end).
- Router with a step curve on send openness: send energy follows the
  curve, front/centre untouched, user routing gains untouched.
- Slice 2: segmentation determinism on a synthetic verse/chorus
  construction (quiet/dense alternation); suggestions bounded; `off`
  produces no curves.
- Full suites (Python, Rust, web) green; manifest round-trip with a
  curves block.

## Out of scope

- Beat/bar alignment, key detection, lyrics/structure ML — segmentation
  stays energy/novelty-based.
- Automating placement azimuth/elevation (gain and openness only; moving
  images per section is the gimmick the Atmos guidance warns against).
- Making suggestions default-on.

## Done when

- Slice 1: curves round-trip manifest → render and preview → export
  null against each other with a hand-authored curve set;
  `docs/plans/mixing/phase14_report.md` records the format decisions.
- Slice 2 (if built): suggestion quality A/B on two tracks (protocol
  `evaluation.md` §6) — chorus lift audible and musical, no pumping at
  section boundaries; suggestions visibly editable in the UI before
  render.
- README table and test-count baseline updated.
