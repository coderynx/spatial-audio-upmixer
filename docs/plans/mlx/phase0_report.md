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

Recommendation: **do not start phase 1 yet.** Profile first — see §5, which
was run immediately after and found a 1.4-1.5x speedup available in torch.

## 5. Follow-up profile (where the time actually goes)

Stage timers around the demix loop, with an MPS sync at every boundary. One
full 60 s separation per model:

| Stage | BS-Roformer-SW | MDX23C-DrumSep |
|---|---|---|
| model forward | 43.56 s (99.7%) | 33.03 s (99.5%) |
| — of which `torch.stft`/`istft` | 0.32 s (0.7%) | 1.02 s (3.1%) |
| — of which everything else | 43.24 s (99.0%) | 32.01 s (96.4%) |
| host→device | 0.01 s | 0.05 s |
| device→host | 0.01 s | 0.06 s |
| CPU accumulate + normalize | 0.06 s | 0.05 s |

Transfers and the CPU-side overlap-add are noise. Confirms §2 from the other
direction: the STFT gates never mattered because STFT is ~1-3% of the work.

Per-module hooks inside one forward (BS-Roformer): `Attention` 75.9%,
`FeedForward` 16.6%, `RMSNorm` 8.7%, `MaskEstimator` 2.8%, `BandSplit` 0.8%
(RMSNorm nests inside the other two, so these sum over 100%). But the SDPA
call inside `Attention` is only 11.5% of the forward — the attention cost is
not the attention kernel.

Splitting `Attention.forward` itself:

| Step | Time | % of forward |
|---|---|---|
| **rotary embedding** | **1.29 s** | **38.0%** |
| `to_qkv` + rearrange | 0.40 s | 11.8% |
| attend (SDPA) | 0.40 s | 11.7% |
| `to_out` | 0.25 s | 7.2% |
| gates | 0.15 s | 4.4% |
| RMSNorm | 0.14 s | 4.2% |

Rotary position embedding is the single largest cost in the whole pipeline.
`rotary_embedding_torch`'s `apply_rotary_emb` runs `t * cos + rotate_half(t)
* sin` plus a `torch.cat` as separate MPS kernels over a 102 MB tensor,
48 times per chunk — pure memory-bandwidth waste at shapes
`(62, 8, 801, 64)` and `(801, 8, 62, 64)`.

Fixes measured at those real shapes:

| Variant | Per-chunk rotary | vs library |
|---|---|---|
| A: `rotary_embedding_torch` (current) | 1.119 s | — |
| B: no-cat path (already in-repo for DML) | 0.904 s | 1.28x |
| C: B + cached cos/sin | 0.909 s | 1.28x |
| **D: `torch.compile` of B** | **0.163 s** | **6.88x** |

Caching cos/sin buys nothing — `freqs` are already cached upstream, and the
transcendentals are trivial next to the elementwise passes. Inductor fusing
the chain into one kernel is the whole win, and it is **bit-exact** (max diff
0.00e+00 against the library path).

End-to-end, with that compiled rope patched into both roformer archs:

| Model | before | after | speedup |
|---|---|---|---|
| `BS-Roformer-SW.ckpt` | 49.39 s | 32.27 s | **1.53x** |
| `kimmel_unwa_ft2_bleedless.ckpt` | 34.33 s | 24.66 s | **1.39x** |

Stem parity: **max abs diff 0.00e+00** across all 8 stems — bit-identical
output, not merely within tolerance. First run pays ~3 s of compile time
(two shapes), amortized across chunks.

## 6. Revised recommendation

The profile inverts the plan's premise. Time is not in complex ops, not in
STFT, not in transfers, and not in the SDPA kernel — it is 38% in an
unfused rotary embedding, which `torch.compile` fixes at 6.9x for a
few-line change with bit-identical output.

Do this before any MLX work:

1. **Land the compiled rope** in both roformer archs (~1.4-1.5x, bit-exact).
   Needs backend gating and a CPU/CUDA correctness pass; verify first-call
   compile cost is acceptable for short jobs.
2. **Then re-profile.** With rope fused, the remaining hot spots shift —
   `FeedForward`, `to_qkv`, and the gates are all elementwise-adjacent and
   may fuse the same way. Compiling the whole `Attention.forward`, or the
   transformer block, is the obvious next experiment.
3. **Then re-test Roformer batch > 1** on torch 2.13 (see the freeze warning
   in `engine.py:205-211`).

Only after those should phases 1-6 be re-costed. MLX's remaining advantage
over a `torch.compile`d MPS path is unproven, and the two motivations §1
already voided are not coming back. If step 2 shows inductor fusing most of
the transformer stack, the case for a six-phase manual port of ~1400 vendored
lines is weak.

## Reproducing

Probe, bench harnesses, profilers, and stem comparator were written to the
session scratchpad, not the repo: `mps_probe.py`, `bench_separation.py`
(`RUNS=n python bench_separation.py <tag>`), `compare_stems.py <dir_a>
<dir_b>`, `profile_demix.py`, `profile_modules.py`, `profile_attention.py`,
`bench_rope.py`, `bench_rope_compile.py`, `bench_rope_e2e.py [--patch]`.
The gate removals are commit `e714f66`.

Caveats on the §5 numbers: every hook and stage timer forces an MPS sync, so
absolute per-stage times are inflated a few percent — the proportions are the
finding, and the end-to-end table is unsynced and unaffected.
`ProfilerActivity.MPS` does not exist in this torch build, hence hooks rather
than `torch.profiler`.
