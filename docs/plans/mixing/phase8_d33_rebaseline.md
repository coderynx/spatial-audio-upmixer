# Phase 8 — Close D33 and re-baseline the measurement kit

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phases 0–7 merged (`feature/mixing-revamp`). This phase is the
gate for phases 9–11: two ground rules are currently violated —
`npm run bench:engine` fails (parity ledger D33) and the phase 0 baseline
tables are stale (phases 3–5 moved send energy and LFE level). Nothing
audible ships here.

## Goal

1. **Close D33.** `docs/contracts/preview_export_parity.md` ledger D33:
   the preview engine with `decorrelate: 1` benches mean 0.72x /
   p99 2.6x / worst 2.8x of the 2.67 ms quantum deadline against budgets
   of 0.4x / 1.0x / 1.5x — the mid-bass decorrelation stage starves the
   worklet. The same build with `decorrelate: 0` is inside budget
   (mean 0.30x / p99 0.82x), so the cost is attributable to that stage
   alone. Give it the same optimization treatment ledger entries D25/D26
   record for earlier over-budget stages — read those entries first and
   follow their pattern (they are the precedent for what counts as
   "fixed" here).
2. **Re-baseline.** Re-run the phase 0 measurement kit
   (`uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s`)
   on the current head and write the tables to a new
   `docs/plans/mixing/phase8_baseline.md`. The phase 0 report's tables
   predate the velvet sends, height fold, and LFE changes — phases 9–11
   must cite phase 8 numbers, never phase 0's.

## Deliverables

1. Mid-bass decorrelation stage optimized in `dsp-core` (streaming path)
   until `npm run bench:engine` passes all three budget gates with
   `decorrelate: 1`. Export-path output must stay bit-identical unless
   the optimization is exactly mirrored on both paths — either way the
   golden tests (`golden_kernels.rs`, `stream_equivalence.rs`) and the
   parity contract decide; re-hash the contract and close D33 in the
   ledger with the new bench numbers.
2. `docs/plans/mixing/phase8_baseline.md` — fresh run of all four
   phase 0 measurements plus anything the kit gained in phases 4–6
   (grep `test_mix_measurement.py` for measurements added since the
   phase 0 report; include them all).
3. If optimizing the stage changes its output audibly (e.g. reduced
   filter order), that is a send change: phase 3's validation applies
   (A/B listening note, measurement kit before/after). Prefer a
   bit-transparent optimization; take the audible route only if the
   transparent one cannot meet budget, and say so in the report.

## Tests

- `npm run bench:engine` green (all budget gates, `decorrelate: 1`);
  paste numbers into the ledger entry.
- `cargo test` in `packages/dsp`; full Python suite; `npm test` +
  `npm run build` in `apps/web`.
- Current suite baseline: 1107 passed / 31 deselected (2026-08-17) —
  report any drift.

## Out of scope

- Any gain-table, panner, or renorm change (phases 9–11).
- New measurements beyond re-running what exists (phases 9–11 extend the
  kit themselves where they need to).

## Done when

- D33 closed in the ledger with passing bench numbers; wasm artifact
  rebuilt and committed (the §1 build-provenance rule D33 itself cites).
- `phase8_baseline.md` written; phases 9–11 unblocked.
- All suites green.
