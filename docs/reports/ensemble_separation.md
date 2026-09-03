# Primary-stem ensemble

The opt-in `stem_ensemble` path runs `BS-Roformer-SW.ckpt` and
`model_scnet_ep_36_sdr_10.0891.ckpt` on the same primary parent, then uses the
fixed `avg_wave` 0.5/0.5 sample average for Bass and Drums. Guitar, Piano, and
Other remain the SW estimates; SCNet Vocals and Other are ignored. Drum-piece
requests ensemble the intermediate Drums stem before drumsep.

The model registry entry and SCNet inference implementation are in
`packages/core/src/separation/inference/`; the algorithm and eligibility
choice follow `~/Projects/upmixer-knowledge/techniques/ensembling.md` and the
SCNet model source registry.

## Pre-MLX evaluation (CPU ensemble)

The metrics below are the pre-MLX CPU-ensemble evaluation and remain the
ensemble baseline.

Environment: Apple M3 Pro (18 GB), macOS 26.5.2; model-native 44.1 kHz;
4.0-second `synthetic_corpus` default item; backend defaults; TTA and pitch
shift off. Deux and BS-Roformer SW ran on MPS; SCNet was forced to CPU. The
real SCNet checkpoint load also passed.

| Run | Bass SDR / fullness / bleedless | Drums SDR / fullness / bleedless | Other SDR / fullness / bleedless | Vocals SDR / fullness / bleedless | Aggregate SDR / fullness / bleedless | Time |
|---|---:|---:|---:|---:|---:|---:|
| Baseline (`stem_ensemble=False`) | 1.62 / .879 / .121 | 0.00 / .000 / .273 | −.08 / .599 / .955 | 0.00 / .000 / .072 | .39 / .369 / .355 | ~28 s |
| Ensemble (`avg_wave`, 0.5/0.5) | 2.49 / .949 / .227 | 2.16 / .607 / .301 | 1.66 / .232 / .972 | 0.00 / .000 / .072 | 1.58 / .447 / .393 | ~74 s |

Other's final metrics can change because the primary residual remask runs
after Bass/Drums fusion, although its raw SW estimate is retained before that
remask. This is a plumbing/gross-regression result, not a model-ranking or
shipping-quality claim. The synthetic evaluation is functional-only and is
not evidence of musical quality; no quality claim should be inferred from
these numbers.

## SCNet-only MLX validation (2026-09-03)

Machine: Apple M3 Pro, 18 GB. MLX **0.32.2**, `mlx-spectro` **0.7.0**, Torch
**2.13.0**. Exact checkpoint SHA256:
`ac25975f0f5704f3d1a3c3c251505b7a0f417a22eafe82773440ee4f7e14b74f`.

For an exact-checkpoint deterministic 1-second direct forward, CPU took
**1.908 s**; MLX took **0.489 s** cold and **0.351 s** warm (**5.44x** warm
speedup). CPU-vs-MLX output parity was `max_abs 1.43051147e-06` and **108.12
dB SNR**. MLX peak was **1.063 GiB**, with zero swap.

The public `StemSeparator` production-path smoke used a 1-second stereo
44.1 kHz synthetic file (internally one zero-padded native 485100-sample
chunk, overlap configured 4, batch 1). It auto-selected `mlx` and took
**4.843 s** inference+load/output. Canonical `Bass`, `Drums`, `Other`, and
`Vocals` were all finite with shape `(44100, 2)`. MLX peak was **4.166 GiB**
under the 6 GiB cap; max RSS was **983,990,272 bytes**, with zero swap.

Reported focused checks: the wiring agent reported **36 passed**; SCNet had
**7 passed / 1 perf deselected**; hardening `test_scnet_mlx.py` had **6 passed
/ 1 deselected**; the exact parity perf test had **1 passed in 4.71 s**.

The full Python baseline before implementation was **1316 passed / 36
deselected**. It was not rerun after implementation because the user reported
a machine freeze, and no full ensemble/evaluation corpus was rerun. These
checks make no new quality claim: numerical parity supports backend
equivalence only.
