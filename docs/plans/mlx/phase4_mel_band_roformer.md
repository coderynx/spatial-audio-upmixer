# Phase 4 — MelBandRoformer in MLX

Read `docs/plans/mlx/README.md` first for context and ground rules.
Requires phases 1-3 merged. Biggest-value phase: 10 of 14 registry
checkpoints are `mel_band_roformer`.

## Context

- Reference: `packages/core/src/separation/inference/archs/mel_band_roformer.py`
  (532 lines) + shared `archs/attend.py`. Structure:
  - STFT → per-band gather via `freq_indices` (mel filterbank from
    `librosa.filters.mel`, computed at construction, lines ~316-330),
  - `BandSplit` (per-band RMSNorm + Linear),
  - alternating time/freq `Transformer` stacks with RoPE
    (`rotary_embedding_torch`) and SDPA (`Attend`),
  - `MaskEstimator` (per-band MLP → GLU) producing complex masks,
  - `scatter_add_` of masked bands back to full spectrum + overlap-count
    denominator (lines ~454-485), iSTFT.
  - All the `x_is_mps` / `x_is_dml` CPU-bounce branches are torch-backend
    workarounds — do NOT port them; MLX complex ops run on GPU natively.
  - Ignore training-only code (multi-STFT loss, target branches): inference
    engine only calls `forward` without targets. Port the inference path
    only; porting loss code violates the "no dead code" convention.
- Single-target squeeze rule: for `num_stems == 1` configs the forward
  returns `(2, samples)` with the stem axis squeezed away; `demix.py`'s
  module docstring documents how the accumulator and residual
  (`orig_mix - primary`) depend on this. The MLX arch must reproduce the
  same output shape contract, and `inference/mlx/demix.py`'s roformer loop
  must apply the same residual rule.
- Demix loop reference: `demix.py` `demix_roformer` (lines 40-129) —
  hamming-window overlap-add, `starts` list with final-window pullback,
  accumulation on host. Mirror the arithmetic exactly.
- Config: `mel_band_roformer_*` YAMLs in `inference/configs/` — `model:`
  section is passed as constructor kwargs verbatim. Keep that surface.
- The mel filterbank must be built identically: same `librosa.filters.mel`
  call, same `[0][0] = 1.0` / `[-1,-1] = 1.0` edge fixups, same
  `freqs_per_band > 0` band-index derivation. Compute it in numpy exactly as
  the torch arch does, then convert indices to `mx.array` — do not
  reimplement the filterbank math.

## Steps

1. `inference/mlx/archs/mel_band_roformer.py`: port the inference forward
   path. Use phase 2 `ops` for STFT/iSTFT/RoPE/attention. `scatter_add_`
   equivalent: `mx.zeros(...).at[..., idx, :].add(masked)` — verify
   duplicate-index accumulation semantics match torch's `scatter_add_`
   (that is the whole point of the op here: mel bands overlap).
2. Weight mapping (`load_weights` beside the arch): transformer stacks,
   band-split/mask-estimator Linears, RMSNorm gammas. Torch `nn.Linear`
   weight is `(out, in)`; `mlx.nn.Linear` stores the same orientation —
   verify once with a probe, don't assume either way. GLU has no weights but
   check gate-half ordering vs `F.glu` (first half = value, second = gate).
3. `inference/mlx/demix.py`: add `demix_roformer` mirroring the torch loop
   (chunk starts, hamming weighting, num_stems==1 residual rule, batch_size
   honored — MLX unified memory may allow batch > 1; keep default 1 in this
   phase, expose knob for phase 6 tuning).
4. Register the arch in the MLX engine factory.
5. Parity tests `packages/core/tests/test_mlx_mel_band_roformer.py`, same
   two-tier pattern as phase 3:
   - tiny random-weight config, torch vs MLX forward: `atol=1e-3`,
     SNR ≥ 60 dB. Cover both a multi-stem config shape and a
     single-target (`num_stems == 1`) squeeze shape.
   - perf-marked real-checkpoint test with the vocals model
     (`mel_band_roformer_vocals_becruily.ckpt`): full separate torch-CPU vs
     MLX, per-stem SNR ≥ 40 dB.
   - one test asserting scatter-add duplicate-index parity on a crafted
     overlapping-band case (this is the likeliest silent-corruption spot).
6. Smoke every mel_band checkpoint present in the local cache: shape-only
   run (load + one chunk) across all 10 configs, since dims/depths differ
   per checkpoint. Perf-marked.
7. Benchmark vocals + karaoke models vs phase 0 baseline; append to
   `phase0_report.md` under "Phase 4 results". Karaoke matters: its larger
   chunks were an MPS pain point (see `device.py` thread-cap docstring).
8. Full suite green.

## Out of scope

- BSRoformer.
- Batch-size/memory tuning, default flip.
- TTA/pitch-shift changes (already backend-agnostic in `engine.py`).

## Done when

- All 10 mel_band checkpoints load and run one chunk on MLX; vocals model
  passes full-file parity.
- Benchmarks recorded; full suite green.
