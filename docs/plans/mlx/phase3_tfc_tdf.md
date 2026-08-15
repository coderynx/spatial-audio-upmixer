# Phase 3 — TFC-TDF v3 in MLX + demix wiring

Read `docs/plans/mlx/README.md` first for context and ground rules.
Requires phases 1-2 merged.

## Goal

First runnable MLX arch, deliberately the easiest one: TFC-TDF v3 (plain
conv net, one registry checkpoint: `MDX23C-DrumSep-aufr33-jarredou.ckpt`).
Establishes the pattern later phases copy: arch module, weight-key mapping,
backend-agnostic demix, parity test, engine registration.

## Context

- Reference implementation:
  `packages/core/src/separation/inference/archs/tfc_tdf_v3.py` (276 lines).
  Components: the `STFT` helper class (replaced by phase 2 `ops.stft` /
  `ops.istft`), conv blocks with configurable norm/activation, TFC_TDF
  residual blocks, up/down scales, final `conv2d`. Config arrives as a
  namespace (`config.as_namespace()` — see `inference/config.py`).
- Demix loop: `inference/demix.py` `demix_tfc_tdf` (lines 132-204) —
  numpy in, torch chunk loop, numpy out. Overlap-add here is plain ordered
  sum on CPU; only the per-batch `model(batch)` call and the host↔device
  moves are torch-specific.
- Engine: `inference/engine.py` `_demix_arch` picks the demix function;
  `separator.py` builds the engine. Phase 1 left an MLX factory raising
  NotImplementedError — this phase registers the first arch in it.
- MLX conv note: `mx.nn.Conv2d` expects NHWC (channels-last), torch uses
  NCHW. Weight conversion must transpose conv kernels
  (`(out, in, kh, kw)` → `(out, kh, kw, in)`) and the forward pass must
  keep a consistent layout. This is the main mechanical trap of this phase.

## Steps

1. `inference/mlx/archs/tfc_tdf_v3.py`: port the arch to `mlx.nn`. Same
   hyperparameter surface (driven by the same YAML config namespace). Use
   phase 2 ops for STFT/iSTFT — no CPU bounce, complex stays on GPU.
2. `inference/mlx/archs/` weight mapping for this arch: torch state-dict
   keys → MLX module tree, conv kernels transposed, norm params mapped.
   Keep the mapping next to the arch (a `load_weights(model, state)`
   function per arch file), not in a generic framework.
3. `inference/mlx/demix.py`: `demix_tfc_tdf` mirroring the torch version's
   contract exactly (same args minus `device`, same numpy-in/numpy-out,
   same padding and overlap-add arithmetic so results stay comparable).
   Convert chunk batches with `mx.array(...)`, run the model, pull results
   back with `np.array(mx.eval(...))`-style materialization. Accumulate in
   numpy float32 on the host exactly like the torch loop does, so output is
   device-independent (this mirrors the existing design decision in
   `demix.py`'s module docstring).
4. Engine wiring: make the phase 1 MLX factory build a working engine for
   `arch == "tfc_tdf_v3"` (loader: registry → converted safetensors →
   arch instance; engine: reuse `SeparationEngine` if its only torch
   dependency is the model+demix pair — inspect first; if `SeparationEngine`
   can take the demix callable and an opaque model, prefer injecting over
   duplicating the class. Chunking/TTA/pitch logic in `engine.py` is
   numpy/scipy and must not be duplicated.)
5. Parity test `packages/core/tests/test_mlx_tfc_tdf.py`
   (`importorskip("mlx")`, and skip when the checkpoint is not in the local
   model cache — CI has no weights; mark the with-weights test `perf`):
   - Unit level: random-weight tiny config (small dims, built from a
     stripped YAML in the test), identical weights loaded into torch and
     MLX arch, same random input chunk → outputs within `atol=1e-3`,
     SNR ≥ 60 dB.
   - Checkpoint level (perf-marked): real drum-sep checkpoint, 10 s
     deterministic input, full `separate()` on torch-CPU vs MLX → per-stem
     SNR ≥ 40 dB and no NaN/inf.
6. Benchmark: time MLX vs the phase 0 baseline for the drum model on the
   same 60 s input; append the numbers to `docs/plans/mlx/phase0_report.md`
   under a "Phase 3 results" heading.
7. Full suite green.

## Out of scope

- Roformer archs.
- Batch-size tuning, memory limits beyond phase 1 defaults.
- Changing the torch demix loop.

## Done when

- `UPMIXER_MLX=1` runs a real drum separation end-to-end via
  `StemSeparator` on the MLX backend.
- Both parity tests pass; benchmark numbers recorded.
- Full suite green with and without mlx installed.
