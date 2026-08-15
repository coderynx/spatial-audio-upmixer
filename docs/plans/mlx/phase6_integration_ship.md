# Phase 6 — Integration, tuning, quality gate, ship

Read `docs/plans/mlx/README.md` first for context and ground rules.
Requires phases 0-5 merged. This phase decides whether MLX becomes the
default Apple-silicon backend; every step produces the evidence for that
call.

## Steps

1. **Batch/segment tuning.** The torch path auto-tunes batch/segment in
   `separator.py` (see `_detect_backend` usage around lines ~40-61). For
   MLX, sweep roformer batch size (1, 2, 4) and the registry
   `default_chunk_samples` models on a real machine, watching
   `mx.metal.get_peak_memory()` vs device limit. Unified memory freeze risk
   (the batch=2 MPS incident, `engine.py:205-211` comment) must be
   disproven per batch size before enabling it — a hang is worse than a
   slow default. Encode results as MLX-specific defaults in the engine
   factory, not scattered conditionals.
2. **OOM/retry behavior.** Torch path has an OOM-retry ladder keyed on
   backend (`separator.py` — see `"mps backend out of memory"` matching,
   line ~127). Decide the MLX equivalent: catch MLX allocation failures,
   `clear_cache()`, retry at reduced batch/segment. Wire into the same
   retry structure; do not invent a parallel one.
3. **Quality gate (blocking).** Run the objective evaluation harness
   (`docs/evaluation_harness.md`, `packages/core/src/eval/`) on the MLX
   backend for the checkpoints the harness corpus covers, and compare
   against the same harness run on the torch backend at the deterministic
   settings the harness contract specifies. Ship bar: SDR/fullness/
   bleedless deltas within the harness's stated noise floor. Save both
   reports; no default flip without them.
4. **Default flip.** Make `_detect_backend` prefer `"mlx"` over `"mps"`
   when mlx imports on Apple silicon; retire the `UPMIXER_MLX` opt-in and
   replace with an opt-out (`UPMIXER_NO_MLX=1`) for one release cycle of
   escape hatch. Verify `StemSeparator.backend` consumers: `grep -rn
   "backend" apps/api/src apps/cli/src apps/web/src` — anywhere that
   string surfaces to users (capability endpoints, CLI output) must render
   "mlx" sensibly.
5. **Benchmarks and report.** Final table: every registry checkpoint,
   torch-MPS (phase 0 gates-off numbers) vs MLX, same input, median of 3.
   Consolidate `phase0_report.md` accumulated results into
   `docs/plans/mlx/final_report.md`: speedups, peak memory, harness
   deltas, tuning decisions.
6. **Docs.**
   - `packages/core/AGENTS.md`: one paragraph in the in-core inference
     boundary section — MLX is an inference-internal backend, same rule:
     web/CLI must not import mlx or reach MLX internals.
   - `packages/core/README.md`: `separation-mlx` extra install note.
   - `~/Projects/upmixer-knowledge/`: add MLX port notes (conventions
     discovered, per-model tuning) where the tooling/models docs live —
     that repo is independently versioned; commit there separately.
7. **Cleanup.** Delete anything phases 1-5 left dead (unused knobs,
   scaffold-era NotImplementedError paths now unreachable). Re-check
   file-size policy across `inference/mlx/`. Full suite green with and
   without mlx installed; `apps/api` and `apps/cli` suites too.

## Done when

- MLX is the default backend on Apple silicon with an opt-out.
- Harness parity reports archived; final benchmark report written.
- Full suite green in both install configurations.
- Docs and knowledge base updated.
