# DSP stem-cleanup migration report

Date: 2026-08-28

Decision: ship the bounded DSP implementation behind the existing default-off
`stem_bleed_reduction` Boolean, relabeled **DSP stem cleanup**. The retired
phase-reference and per-stem debleed inference paths are removed. Do not
enable the DSP path by default until licensed-corpus and listening gates pass.

## Implementation

The cleanup runs only on the ordered `becruily_deux` pair (`Vocals`,
`_deux_inst`) at the separation-task boundary. The inference engine retains
its exact resampled, restored-level parent only for that enabled task. Cleanup
runs before either child is cached, discarded, or used by a downstream task.

Rust uses a fixed 1024-sample, 256-hop sqrt-Hann STFT with ERB-band power
history. A transfer is admitted only for finite, energetic, coherent bands
with a dominant proposed owner and a material weaker estimate. Accepted
components are subtracted from one child and added to the other; the final
overlap-add remainder is split across the children so their sum matches the
exact parent.

The private Python policy is fixed at:

| Parameter | Value |
| --- | ---: |
| Relative energy floor | `1e-8` |
| Relative leakage floor | `0.05` |
| Coherence floor | `0.8` |
| Dominance ratio | `4.0` |
| Transfer cap | `0.25` |

FFT size, hop, history, and product voicing are not user controls. Processing
crosses PyO3 in 65,536-frame blocks and removes the processor's fixed
1024-sample latency after its explicit flush.

## Controlled leakage probe

This deterministic five-second stereo probe is an engineering check, not a
real-music acceptance corpus. The vocal estimate contained 20% of the true
instrumental, while the complementary instrumental estimate retained 80%.
All scores use `upmixer.eval.metrics` at 44.1 kHz.

| Stem | Variant | SDR (dB) | Fullness | Bleedless |
| --- | --- | ---: | ---: | ---: |
| Vocals | raw | 10.7441 | 0.99886 | 0.77723 |
| Vocals | DSP | 13.2341 | 0.99879 | 0.82269 |
| Instrumental | raw | 13.9794 | 0.80000 | 1.00000 |
| Instrumental | DSP | 16.4694 | 0.84952 | 0.99979 |

The cleaned child sum differed from the exact parent by at most
`3.33e-16`. Focused Rust tests also cover silence, exact complementary input,
duplicated mono, hard pans, correlated leakage, unrelated overlapping tones,
impulses, low energy, non-finite rejection, supported rates, arbitrary block
partitioning, explicit flush, and duration-independent scratch capacity.

## Runtime

Release PyO3 wheel, Apple M3 Pro, macOS arm64, stereo 44.1 kHz synthetic
tones:

| Programme | Wall time | RTF |
| --- | ---: | ---: |
| 60 seconds | 0.4550 s | 0.00758 |
| 300 seconds | 2.2356 s | 0.00745 |

The measured tier clears the `0.1` DSP RTF gate with substantial margin.
Rust's bounded-scratch test confirms processor capacity is unchanged after a
long input; required parent/child/output storage is outside that scratch
measurement. CUDA and additional CPU reference tiers were not available in
this workspace, so this does not complete the multi-tier performance gate.

## Validation

- `cd packages/dsp && cargo test` — passed.
- `uv sync --all-packages --extra dev --extra web-dev --extra manifest --extra separation-cpu --reinstall-package upmixer-dsp` — rebuilt the release wheel.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` — 1,227 passed, 41 deselected.
- `cd apps/web && npm run build:wasm` — passed; committed WASM artifact unchanged because cleanup has no browser binding.
- `cd apps/web && npm test` — 374 passed.
- `cd apps/web && npm run build` — passed.
- `cd apps/web && npm run bench:engine` — passed its declared mean/p99/worst/cold budgets.

## Remaining default-on gates

The repository still lacks a licensed held-out musical corpus, level-matched
listening results, and CPU/CUDA/MPS reference-tier measurements. Until those
exist and show repeatable benefit without fullness, transient, or high-band
damage, raw output remains the default behavior.
