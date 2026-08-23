# packages/dsp Agent Guide

Global conventions (comment policy, file size, code style, commits) live in
the root `AGENTS.md`. This file covers only what is specific to the Rust DSP
core.

## What lives here

`packages/dsp` is the single implementation of every DSP stage that the
export pipeline and the browser preview both run. Python reaches it through
PyO3 (`upmixer_dsp`), the browser through WebAssembly inside an
AudioWorklet. Nothing else belongs here: separation inference, IO,
orchestration, and manifest handling stay in `packages/core`.

- `crates/dsp-core` — pure Rust, no binding dependencies. All DSP.
- `crates/dsp-py` — PyO3 `cdylib` exposing `upmixer_dsp`.
- `crates/dsp-wasm` — `wasm32-unknown-unknown` `cdylib` with a plain C ABI.
  Deliberately not wasm-bindgen: an `AudioWorkletGlobalScope` has no module
  loader for its generated glue, so the worklet shim marshals by hand.

## Constants stay in Python

Tunable acoustic constants are owned by `packages/core/src/config.py` and the
profile tables, served to the web by `engine_constants()`
(`docs/contracts/preview_export_parity.md` §2). Every function here takes
them as parameters. Only structural math constants that would otherwise be
duplicated (the BS.1770 true-peak FIR, ambisonic normalization, filter-design
internals) live in Rust.

## Numerics

Everything is `f64` internally, matching NumPy. `rustfft`/`realfft` do not
reproduce pocketfft bit-for-bit — no FFT library does — so parity is defined
as a per-stage tolerance budget, enforced by the fixtures below:

| Stage class | Tolerance vs the Python fixture |
|---|---|
| Filter coefficients (`butter`, bilinear, K-weighting retarget) | 1e-14 |
| FIR design (`firwin2`, `minimum_phase`, curve FIRs) | 1e-10 |
| Time-domain IIR (`sosfilt`, `sosfiltfilt`, `lfilter`) | 1e-12 |
| FFT convolution (EQ, decode, XTC) | 1e-9 |
| Scalar measurements (LKFS, dBTP, gains) | 1e-10 |
| Full chain, end to end | 1e-6 |

The end-to-end number is the contract: 1e-6 sits below 24-bit output
quantization, so a rendered file is unchanged in practice.

`kernels/butter.rs` and `kernels/fir_design.rs` are transcriptions of SciPy
source, not re-derivations. They were taken against **SciPy 1.18.0**; if that
pin moves, regenerate the fixtures and expect the diff to tell you whether
SciPy changed behaviour.

## The streaming path also has a time budget

`stream/` runs on the browser's audio thread, where a 128-frame quantum must
complete in 2.67 ms. Numerical correctness is not sufficient there: a render
that overruns starves the callback, and because the worklet is the audio
*source* the result is silence, not a glitch. `cargo test` cannot see this.

So any change to `stream/conv.rs`, `stream/master.rs`, `stream/output.rs`, or
`stream/engine/` gets checked with `npm run bench:engine` from `apps/web`
(after `npm run build:wasm`). Budget and rationale:
`docs/contracts/preview_export_parity.md` §4.

Two structural choices exist for that budget alone, and both must survive
refactoring:

- `StreamingConvolver` is uniform-partitioned overlap-save with the kernel
  transformed **once**. The obvious implementation — `fftconvolve(block,
  kernel)` per call — re-transforms a 6,128-tap decode filter every 128
  samples and lands the binaural path at 1.4x realtime.
- `stream::scale`'s `RouteScalePass` and the render read the same
  `PreviewEngine::route_stem_block`. Do not give the pass its own copy of the
  routing chain: the point is that the normalization is measured off the
  signals that are played, and a second assembly of the same stages is the
  seam `docs/contracts/preview_export_parity.md` §5 warns about.
- The mono-maker advances in `MONO_STRIDE` frames, not one quantum at a time.
  Its zero-phase pass reads `MONO_HORIZON_MS` either side of what it emits, so
  per-quantum granularity redoes that context ~75 times over.

## Fixtures

`tests/golden/` holds vectors dumped from the Python implementation:

```
uv run python packages/dsp/tools/dump_golden_vectors.py
```

Each case is a `<name>.json` (parameters, tolerance) plus little-endian
float64 `<name>.<array>.f64` blobs. Regenerate only against a Python stage
that has **not** yet been swapped for the Rust one — after a swap the fixture
is a regression pin, not an independent reference.

## Commands

- `cd packages/dsp && cargo test` — kernel and stage parity.
- `uv run maturin develop --manifest-path packages/dsp/crates/dsp-py/Cargo.toml`
  — fast rebuild of the Python extension during iteration.
- `uv sync --all-packages --extra dev --extra web-dev --extra manifest
  --extra separation-cpu --reinstall-package upmixer-dsp` — clean rebuild
  after a Rust edit. This is the one that bites: `uv run` and a plain `uv
  sync` both reuse the cached wheel and silently keep running the *old*
  Rust, and dropping `--all-packages`/the extras uninstalls the other
  workspace packages out from under the test suite.
- `cargo build --release --target wasm32-unknown-unknown -p upmixer-dsp-wasm`
  — the browser artifact.

`dsp_core_version()` is exported by both bindings so a wheel/wasm mismatch
surfaces instead of silently rendering two different algorithms.
