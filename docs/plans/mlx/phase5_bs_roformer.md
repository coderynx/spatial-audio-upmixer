# Phase 5 — BSRoformer in MLX

Read `docs/plans/mlx/README.md` first for context and ground rules.
Requires phase 4 merged (reuses its patterns; smallest marginal phase).

## Context

- Reference: `packages/core/src/separation/inference/archs/bs_roformer.py`
  (619 lines). Differences from MelBandRoformer:
  - Fixed `freqs_per_bands` tuple from config instead of a mel filterbank —
    bands partition the spectrum contiguously, no overlap, so
    reassembly is a plain concat/reshape, no scatter-add.
  - Optional `linear_attn` transformer variant (`LinearAttention`, lines
    ~148-170). Check the three bs_roformer YAMLs
    (`BS-Roformer-SW`, `BS-Roformer-Resurrection-Inst`,
    `bs_roformer_inst_hyperacev2`) for whether any production config
    enables it; port only what production configs reach.
  - Same RoPE/SDPA/BandSplit/MaskEstimator/GLU building blocks as phase 4 —
    reuse `inference/mlx/ops.py` and factor genuinely shared submodules
    (BandSplit, MaskEstimator, transformer block) into a shared module under
    `inference/mlx/archs/` rather than duplicating between the two roformer
    files. Follow the file-size policy while doing it.
- Checkpoints: 3 bs_roformer entries in the registry. `BS-Roformer-SW` is
  the multi-stem workhorse (`default_chunk_samples=882000` — 20 s chunks;
  memory-heaviest model in the registry).
- Demix: reuses `demix_roformer` from phase 4 unchanged (arch family switch
  is `engine.py` `_ARCH_ROFORMER`; mirror that in the MLX engine factory).

## Steps

1. `inference/mlx/archs/bs_roformer.py` + shared-submodule refactor of the
   phase 4 arch file (keep phase 4 tests green through the refactor).
2. Weight mapping for the three checkpoints' key layout.
3. Register in the MLX engine factory under the roformer demix loop.
4. Parity tests `packages/core/tests/test_mlx_bs_roformer.py`:
   - tiny random-weight config torch-vs-MLX forward, `atol=1e-3`,
     SNR ≥ 60 dB, covering multi-stem and single-target shapes.
   - perf-marked real-checkpoint full-file parity with
     `BS-Roformer-SW.ckpt`, per-stem SNR ≥ 40 dB. Watch memory: 20 s chunks
     on unified memory is exactly the scenario that froze MPS at batch 2 —
     stay at batch 1, and confirm `mx.metal` memory limit from phase 1
     actually bounds peak (log `mx.metal.get_peak_memory()` in the test and
     report it).
5. Benchmark BS-Roformer-SW vs phase 0 baseline; append to
   `phase0_report.md` under "Phase 5 results".
6. Full suite green.

## Out of scope

- Default flip, tuning, docs — phase 6.

## Done when

- All 3 bs_roformer checkpoints load and run; SW model passes full-file
  parity with peak-memory figure recorded.
- Every registry checkpoint now runs on MLX (14/14).
- Full suite green.
