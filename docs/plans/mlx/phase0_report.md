# Phase 0 report — MPS gate audit + performance baseline

Machine: Apple silicon, macOS 26.5.2 (arm64), torch **2.13.0**, MPS
available. All measurements on a deterministic 60 s stereo 44.1 kHz signal
(harmonic bed + noise + periodic transients), separated through the public
`StemSeparator` surface. Roformer batch size stayed at 1 (engine restriction
left in place, as required).

## 1. MPS op probe

Each op run on `mps` and on `cpu` from identical inputs; float32 FFT is never
bit-exact across backends, so relative SNR is the criterion (≥ 60 dB = same
op, not a different result).

| Op (representative shapes) | MPS | max abs diff | SNR |
|---|---|---|---|
| `torch.stft(return_complex=True)`, n_fft 2048 | works | 5.72e-05 | 128.0 dB |
| `torch.istft` | works | 2.15e-06 | 132.0 dB |
| `torch.view_as_real` | works | 0 | exact |
| `torch.view_as_complex` | works | 0 | exact |
| complex64 multiply | works | 0 | exact |
| `scatter_add_` on complex64 | works | 0 | exact |
| advanced indexing with LongTensor | works | 0 | exact |
| `F.scaled_dot_product_attention` | works | 1.04e-06 | 124.7 dB |

SDPA backends on MPS: `FLASH_ATTENTION`, `EFFICIENT_ATTENTION`, and `MATH`
all execute. Attention was already running on the GPU before this phase.

Nothing silently wrong, nothing failing. **Every gated op is obsolete on
torch 2.13.**

## 2. Timing baseline, gates on vs off

Three runs per model. Run 1 is cold (first forward pass after model load) and
is reported but excluded from the comparison; the delta column uses the
median of runs 2-3.

| Model (arch) | run1 | run2 | run3 | warm median |
|---|---|---|---|---|
| **gates on** | | | | |
| `BS-Roformer-SW.ckpt` (bs_roformer) | 63.60 | 51.06 | 50.56 | 50.81 |
| `kimmel_unwa_ft2_bleedless.ckpt` (mel_band_roformer) | 39.30 | 35.00 | 34.35 | 34.68 |
| `MDX23C-DrumSep-aufr33-jarredou.ckpt` (tfc_tdf_v3) | 36.53 | 35.68 | 36.16 | 35.92 |
| **gates off** | | | | |
| `BS-Roformer-SW.ckpt` | 50.25 | 48.46 | 48.71 | 48.59 |
| `kimmel_unwa_ft2_bleedless.ckpt` | 35.82 | 32.81 | 32.82 | 32.82 |
| `MDX23C-DrumSep-aufr33-jarredou.ckpt` | 34.72 | 34.64 | 34.58 | 34.61 |

| Model | gates on | gates off | speedup |
|---|---|---|---|
| `BS-Roformer-SW.ckpt` | 50.81 s | 48.59 s | **-4.4%** |
| `kimmel_unwa_ft2_bleedless.ckpt` | 34.68 s | 32.82 s | **-5.4%** |
| `MDX23C-DrumSep-aufr33-jarredou.ckpt` | 35.92 s | 34.61 s | **-3.6%** |

The plan named `mel_band_roformer_vocals_becruily.ckpt` for the mel-band
slot; `kimmel_unwa_ft2_bleedless.ckpt` was substituted because it is the
mel-band checkpoint already in the local model cache (same arch, same code
path). Effect size is small enough to be near cold/warm variance — a separate
cold-run-only pass did not reproduce the ordering on two of three models, so
treat ~5% as the honest ceiling for this change, not a precise figure.

## 3. Stem parity, gates on vs off

Stems written as float32 WAV (an earlier 16-bit pass floored every diff at
3.05e-05 = one 16-bit LSB, measuring the container rather than the change).
14 stems across the three models:

| Metric | Worst across all stems | Bar |
|---|---|---|
| max abs sample difference | **9.24e-07** | ≤ 1e-4 |
| SNR vs gates-on stems | **107.5 dB** | — |

FFT-rounding only, as expected. Full suite after the change:
`uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
**1068 passed, 27 deselected**, zero regressions.

## 4. Recommendation

Gate removal is worth keeping — it is free, it deletes three workarounds, and
it buys 3-5%. But it does **not** capture a large share of the expected MLX
win, and the probe changes the premise the MLX project was scoped on.

Two of the three motivations in `README.md` are now void: MPS *does* have
working complex64 FFT and complex tensor ops (so "MLX removes the per-chunk
CPU bounce" is worth ~5%, not a multiple), and SDPA already runs on the GPU
with the flash path available (so "fused attention" is a smaller delta than
assumed). What remains genuinely MLX-only is unified-memory batching — and
the batch=1 restriction that motivates it is a memory-pressure workaround
that phase 0 was explicitly told not to touch.

Recommendation: **do not start phase 1 yet.** The next cheap step is a
profile of where the ~48 s actually goes (transformer stack vs STFT vs demix
accumulation vs the CPU-side overlap-add in `demix.py`), plus a re-test of
Roformer batch > 1 on torch 2.13 to see whether the M3 Pro freeze still
reproduces. If the profile shows time concentrated in the transformer stack,
an MLX port is still the only path to a real speedup and phases 1-6 stand as
written. If it shows a large share in demix accumulation or batch=1 stalls,
those are far smaller changes than a six-phase arch port and should be done
first.

## Reproducing

Probe, bench harness, and stem comparator were written to the session
scratchpad, not the repo: `mps_probe.py`, `bench_separation.py`
(`RUNS=n python bench_separation.py <tag>`), `compare_stems.py <dir_a>
<dir_b>`. The gate removals are commit `e714f66`.
