# Phase 8 — Downmix and render QC

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phases 0 (measurement kit) and 1 (targets + the 5.1 fold).
Independent of phases 4–7.

## Goal

Every delivery of an immersive master is judged on its folds — the
BS.775 stereo fold-down, the 5.1 re-render, the binaural render — but the
chain only ever measures the native bed (and, after phase 1, the 5.1
fold for compliance). Nothing reports whether the stereo fold of a
mastered 7.1.4 bed lands 3 LU quiet, or whether correlated content sums
over the ceiling post-fold (the fold is a linear mix — the limiter's
per-bed guarantee does not survive it).

Add fold QC to the export and surface it:

1. **Measured folds.** After mastering, measure integrated loudness, TP,
   and (from phase 0) PLR of: the BS.775 stereo fold, the 5.1 fold
   (>5.1 layouts; phase 1 built it), and — when the binaural path is
   available for the layout — the binaural render. Report each against
   the native bed's numbers in `MasteringResult` (a `folds` block) and
   the compliance report.
2. **Warnings, not correction.** A fold that exceeds the TP ceiling or
   diverges from the native loudness beyond a documented threshold
   (suggest ±1.5 LU, decided from phase 0/1 data) flags in the
   compliance block and the UI. Automatic fold-referenced re-limiting is
   explicitly out of scope — the mitigation is the user revisiting the
   mix/mastering (or the mixing plan's fold-flat send work), not a
   hidden second limiter.
3. **UI surface.** Finished jobs show the fold table next to the
   phase 1 compliance row. In the preview, the numbers come for free
   when the user auditions a fold output mode — the measurement pass
   already measures whatever collapse mode is active; label the readout
   (phase 3's meters) with the active mode so a stereo-fold measurement
   is not mistaken for the bed's.

## Design decisions (make, document, implement)

- Measurement-only phase: no new processing DSP. The folds reuse
  `itu_downmix_stereo`, phase 1's 5.1 fold, and the binaural renderer
  as measurement programmes — the delivered bed is untouched.
- Cost: each fold measurement is one BS.1770 pass over a folded copy.
  Binaural render for measurement is the expensive one (full HOA
  render); gate it behind a config flag (`qc_measure_binaural`, default
  on for immersive layouts, off for stereo where it is the delivery
  itself and already measured).
- The binaural delivery path already normalizes its own two-channel
  programme (`render_binaural_delivery`) — QC here measures the
  *binaural render of a speaker-bed delivery*, a different artifact.
  Name the fields unambiguously (`folds.stereo`, `folds.surround_51`,
  `folds.binaural`).
- Thresholds and the ±1.5 LU proposal live in
  `docs/standards/spatial_layouts_bs775_bs2051.md` next to the fold
  coefficients, with the phase 0/1 evidence cited.

## Deliverables

1. Core: fold measurement pass in `MasteringChain`/`pipeline.py` after
   the limiter, `MasteringResult.folds`, compliance-report rows.
2. API: jobs schema carries the folds block; no new endpoints.
3. Web: fold table on the job view; active-collapse-mode label on the
   phase 3 meters.
4. Standards doc thresholds section; parity contract untouched unless
   the preview gains a served threshold (then §2).

## Validation

- Golden: height-heavy synthetic bed — stereo-fold loudness delta
  matches hand-computed BS.775 arithmetic; post-fold TP over-ceiling
  fixture (correlated in-phase content in SL/SR + FL/FR) flags.
- Full suite; API schema tests; web vitest for the table and labels.
- Phase report: fold tables for both phase 0 test programmes across
  stereo/5.1/7.1.4, with a paragraph on what the numbers say about the
  current presets (feeds back into the mixing plan if send changes are
  implicated).
