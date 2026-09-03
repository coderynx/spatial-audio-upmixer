# MLX Inference Backend — Phase Plans

Goal: add an MLX backend to the in-core separation inference engine
(`packages/core/src/separation/inference/`) for faster inference on Apple
silicon. MLX is **additive** — the PyTorch path stays the only backend on
CUDA/ROCm/CPU and remains the reference implementation everywhere.

Why: the current MPS path bounces every STFT/iSTFT/complex-tensor op to CPU
per chunk (see `archs/bs_roformer.py:498-564`, `archs/mel_band_roformer.py:388-485`,
`archs/tfc_tdf_v3.py:22-57`) and forces Roformer batch size 1
(`engine.py:205-211`). MLX has native complex64 GPU FFT, fused
attention/RoPE kernels, and unified memory — it removes all of that.

## Current status

The broad MLX phases below remain parked. SCNet is an isolated implemented
exception because Torch MPS is unreliable for this path and CPU inference is
unusably slow. See the [SCNet-only MLX validation](../../reports/ensemble_separation.md#scnet-only-mlx-validation-2026-09-03).

## Phases

Run in order. Each phase is a self-contained agent task with its own
validation; a phase must be green before the next starts.

| Phase | File | Deliverable |
|-------|------|-------------|
| 0 | `phase0_mps_audit_baseline.md` | MPS gate audit on torch 2.13 + per-model timing baseline. May shrink or kill the whole project — run first. |
| 1 | `phase1_scaffold_conversion.md` | `inference/mlx/` package, backend selection plumbing (opt-in), weight conversion to safetensors. |
| 2 | `phase2_stft_numerics.md` | MLX STFT/iSTFT + RoPE/SDPA parity kit with tests vs torch. |
| 3 | `phase3_tfc_tdf.md` | TFC-TDF v3 arch in MLX + demix wiring + parity. |
| 4 | `phase4_mel_band_roformer.md` | MelBandRoformer arch in MLX + parity (covers 10 of 14 checkpoints). |
| 5 | `phase5_bs_roformer.md` | BSRoformer arch in MLX + parity. |
| 6 | `phase6_integration_ship.md` | Default-on selection, memory/batch tuning, eval-harness reports, benchmarks, docs. |

## Ground rules for every phase

- Read the repo root `AGENTS.md` and `packages/core/AGENTS.md` first.
  Comment policy, file-size policy (~400 soft / ~600 hard), and the in-core
  inference boundary (web/CLI must never import torch or MLX internals) all
  apply.
- The torch path's behavior must be byte-identical after every phase.
  `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` must
  pass before and after (baseline: 846 tests).
- MLX is an optional dependency (new `separation-mlx` extra). Every MLX
  import lives behind the backend check; nothing in core may fail to import
  when `mlx` is absent.
- Parity bar (phases 3-5): per-chunk model output vs torch-CPU within
  `atol=1e-3` / relative SNR ≥ 60 dB on deterministic random input, and
  full-file stem output audibly identical. Final quality sign-off is the
  eval harness in phase 6 (`docs/evaluation_harness.md`).
- Numerical-parity trap to watch everywhere: `mx.fast.rope` must match
  `rotary_embedding_torch`'s rotate-half convention exactly. Wrong
  convention degrades separation quality silently — it does not crash.
  Phase 2 builds the parity test; phases 4-5 must use it.
- Consult `~/Projects/upmixer-knowledge/` (external sibling repo, read by
  absolute path) before touching model registry or separation-quality
  behavior; `tooling.md` there notes the pymss family ships MLX builds of
  this same MSST model family — useful as reference implementations.
