# Phase 1 — Remove the StemRebalancer full-signal soft clip

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 0's report (for the re-scope verdict only; no code
dependency).

## Goal

`packages/core/src/separation/stem_rebalance.py` applies
`tanh(arr / 0.95) * 0.95` to the **entire stem** whenever the requested
boost exceeds +3 dB. tanh is nonlinear at every level, so this adds
odd-harmonic distortion to material far below the threshold — permanently,
pre-routing, pre-mastering. Overload protection is already owned by the
mastering chain's look-ahead true-peak limiter
(`packages/core/src/mastering/limiter.py`), which runs on the routed bed
in both pipelines. Delete the redundant, distorting stage.

## Deliverables

1. In `stem_rebalance.py`: remove the `gain_db > _BOOST_DB_CLIP_TRIGGER`
   tanh branch and the now-unused `_SOFT_CLIP_THRESHOLD` /
   `_BOOST_DB_CLIP_TRIGGER` constants. Update the module docstring's
   "Gain application" section accordingly (keep the 10 ms ramp).
2. Search all five packages for references to the removed behavior before
   deleting (per AGENTS.md): constants, docstrings, tests, and any web/API
   surface that documents rebalance clipping. Update or remove what you
   find.
3. **Parity check (investigate, then act):** determine whether the web
   preview applies rebalance gains through the wasm engine
   (`packages/dsp/crates/dsp-core/src/stream/`, `engineParams.ts`) and
   whether that path mirrors the tanh. If the preview never clipped,
   removing the export-side clip *closes* a parity gap for >+3 dB boosts —
   say so in the ledger. If the preview does mirror it, remove it there in
   the same phase and re-hash `docs/contracts/preview_export_parity.md`.

## Tests

- Update/extend `packages/core/tests/` coverage for `StemRebalancer`
  (find the existing test file by grep): a +6 dB boost on a −20 dBFS sine
  must now be exactly linear (output = input × 10^(6/20) after the ramp,
  `atol=1e-12`), and THD of the boosted sine must be at the numerical
  floor.
- A boost driving samples past full scale must pass through unclipped
  here (the limiter downstream owns it) — assert no clamping in this
  stage.

## Out of scope

- Any change to routing, sends, or the mastering chain.
- Per-stem limiting features (if someone wants stem-level overload
  control later, it's a proper limiter, not tanh — note this in the PR,
  do not build it).

## Done when

- Full suite green (baseline: 846 tests; count may shift with test
  updates — report the new count).
- Parity investigation result recorded in
  `docs/contracts/preview_export_parity.md` (ledger entry either way).
- One-paragraph phase report appended to `docs/plans/mixing/phase0_report.md`
  or as PR description: what changed, measured THD before/after on the
  synthetic case.
