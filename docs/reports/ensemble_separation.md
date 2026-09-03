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

## Final validation

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
