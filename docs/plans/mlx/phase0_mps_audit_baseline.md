# Phase 0 — MPS gate audit + performance baseline

Read `docs/plans/mlx/README.md` first for context and ground rules.

## Goal

Two deliverables, no production behavior change shipped without evidence:

1. Determine which CPU-fallback gates in the vendored archs are obsolete on
   torch 2.13 (they were written for the torch 2.1-era MPS backend, upstream
   python-audio-separator Issue #292). If MPS now runs the complex ops, remove
   the gates — that alone may capture a large share of the MLX win for free.
2. A reproducible per-model timing baseline on this machine (Apple silicon,
   MPS), so later phases can prove speedups instead of asserting them.

This phase decides project scope: if gate removal gets within ~20% of
expected MLX gains, later phases may be re-scoped or dropped. Report either
way.

## Context

- Gates to audit:
  - `packages/core/src/separation/inference/archs/bs_roformer.py` — lines
    ~471-564: `x_is_mps` forces `torch.stft`, `view_as_complex`, complex
    mask multiply, and `torch.istft` through CPU.
  - `packages/core/src/separation/inference/archs/mel_band_roformer.py` —
    lines ~380-485: same, plus `scatter_add_` over complex tensors and
    `freq_indices` gathers forced to CPU.
  - `packages/core/src/separation/inference/archs/tfc_tdf_v3.py` — lines
    ~20-60: whole `STFT.__call__`/`STFT.inverse` bounce via CPU for any
    non-cuda/cpu device.
- Batch restriction: `packages/core/src/separation/inference/engine.py`
  lines ~205-211 force Roformer batch 1 off-CUDA (real M3 Pro freeze,
  unified-memory pressure, not catchable OOM). Do NOT lift this in phase 0;
  just measure with it in place.
- SDPA: `archs/attend.py` — check whether `F.scaled_dot_product_attention`
  runs on MPS in torch 2.13 and whether the flash path is taken.

## Steps

1. Write a standalone probe script in the scratchpad (not the repo) that, on
   the `mps` device with torch 2.13, exercises each gated op with
   representative shapes: `torch.stft(return_complex=True)` (n_fft 2048),
   `torch.istft`, `torch.view_as_complex` / `view_as_real`, complex
   multiply, `scatter_add_` on complex64, advanced indexing with a
   LongTensor, `F.scaled_dot_product_attention`. Record works / errors /
   silently-wrong-results (compare against CPU reference, `atol=1e-4`).
2. Build the timing baseline BEFORE touching any gate. Use one fixed test
   input (generate a deterministic 60 s stereo 44.1 kHz signal, or a real
   track if one is available locally). Time `SeparationEngine.separate` via
   the public `StemSeparator` surface for one checkpoint per arch family at
   minimum: `BS-Roformer-SW.ckpt`, `mel_band_roformer_vocals_becruily.ckpt`,
   `MDX23C-DrumSep-aufr33-jarredou.ckpt`. 3 runs each, report median.
   Save the harness script to the scratchpad and paste results into the
   report.
3. For every op the probe proves correct on MPS: remove that gate from the
   arch file (smallest possible diff, keep the gate for ops that still
   fail). Keep the DML (`privateuseone`) branches intact — they are a
   different backend and out of scope.
4. Re-run the timing baseline with gates removed. Verify stem output parity
   gate-on vs gate-off: max abs sample difference on the written stem WAVs
   ≤ 1e-4 (accumulation is on CPU either way, see `demix.py` docstring, so
   differences should be tiny FFT-rounding only).
5. Run the full suite: `uv run pytest packages/core/tests apps/api/tests
   apps/cli/tests -q` — zero regressions.
6. Write the report to `docs/plans/mlx/phase0_report.md`: probe matrix
   (op × works/fails), baseline table (model × time, gates on/off), parity
   numbers, and a one-paragraph recommendation on whether the MLX phases
   remain worthwhile.

## Out of scope

- Any MLX code.
- Lifting the Roformer batch=1 restriction.
- Changing chunking, overlap, or any demix parameters.

## Done when

- `phase0_report.md` exists with all three tables and recommendation.
- Any gate removals are committed separately from the report, with the full
  suite green and stem-parity numbers in the commit message.
