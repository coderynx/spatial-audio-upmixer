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

## Bounded SCNet architecture (2026-09-04)

Production remains on the exact XL-IHF checkpoint, float32 inference, the
485,100-sample model chunk, and overlap 4. The MLX loader now applies an 8 GiB
allocator limit and a 512 MiB cache limit before model construction. The
adapter releases inactive allocations after each forward. Five consecutive
real-checkpoint chunks took 2.793–2.854 seconds each; MLX peak allocation held
at 5.291 GiB, active allocation returned to 0.201 GiB, and cached allocation
returned to zero after every chunk.

Default SCNet runs now overlap-add into a one-chunk rolling buffer and stream
finalized float32 frames to atomic WAV outputs. Only the stems requested by the
stage are accumulated and written. The prior in-memory function remains for
TTA, pitch-shift, and explicit outer-chunk modes. Synthetic padding, chunk-edge,
and batched-chunk tests matched the prior overlap-add output bit for bit.

On ordinary callers, one persistent `spawn` worker owns MLX and the checkpoint,
reports per-chunk progress, and is terminated when its inactivity watchdog
expires. API jobs already run in daemonized subprocesses, which cannot create a
child process, so they use the same bounded engine in that existing isolation
boundary; their outer supervisor terminates an ensemble job after 120 seconds
without progress. Request outputs are published by directory rename only after
every stem succeeds.

### Direct MPSGraph gate

The native prototype used exact float32 production activation shapes for all
eight dual-path blocks (16 bidirectional LSTMs). Representative activation
parity passed at max error `2.67e-6` and 126.7 dB SNR. The recurrent core took
approximately 2.1–2.6 seconds; with the measured 0.53-second non-core work, the
projected full forward was 2.6–3.1 seconds versus approximately 2.79 seconds
for MLX. Four native blocks already reached 6.5–7.2 GiB RSS.

The gate required at least 1.5x throughput and a proven peak below 8 GiB.
MPSGraph failed the throughput gate and did not establish full-graph memory or
waveform parity, so no native backend or PyObjC dependency ships.

Validation after integration: **1343 passed / 38 deselected** across core, API,
and CLI tests. A real-checkpoint public-path smoke test ran two isolated
separations of a one-second stereo float32 WAV in 5.21 and 5.52 seconds and
passed full-precision output comparison. An opt-in real-checkpoint five-chunk
regression matched streaming and legacy overlap-add bit for bit. These changes
alter resource use and failure containment, not the separation model or quality
settings.
