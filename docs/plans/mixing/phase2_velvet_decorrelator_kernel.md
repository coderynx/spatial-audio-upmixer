# Phase 2 — Velvet-noise decorrelator pair kernel in dsp-core

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 0 (its send-response measurement is the before/after
yardstick). Independent of phase 1.

## Goal

The kernel that replaces `diffuse_send` + `haas_decorrelate` for
surround/height sends: a **pair** of sparse velvet-noise FIR decorrelators
(one per side of a channel pair) that is

- near-flat per channel (no audible comb coloration),
- mutually decorrelated (interchannel coherence low enough to widen),
- **downmix-flat by construction**: the two filters are designed so that
  `h_L + h_R` sums close to a pure delay/impulse, so BS.775 fold-down of a
  pair fed from the same source does not comb.

Reference design: Optimized Velvet-Noise Decorrelator (Välimäki et al.,
DAFx 2018) — segmented velvet noise, ~30 taps over ~30 ms with
exponentially decaying segment gains. Sparse convolution only (adds, no
multiplies per tap beyond segment gains) — this is what keeps it inside
the worklet budget in phase 3.

This phase delivers the kernel + bindings + tests only. No call-site
changes anywhere — `diffuse_send`/`haas_decorrelate` keep working
untouched until phase 3.

## Deliverables

1. `packages/dsp/crates/dsp-core/src/routing/decorrelate.rs`:
   - `velvet_pair(sample_rate, length_ms, taps, seed) -> (VelvetFir, VelvetFir)`
     — deterministic from `seed`; tap sign/position sets constructed as
     complements so the pair sums near-impulse. Document the construction
     in the module docstring (this is a DSP-constraint comment the policy
     allows).
   - `VelvetFir::process(&[f64]) -> Vec<f64>` (offline, whole-buffer) and
     a streaming form with carried state matching the existing stream
     kernel conventions in `stream/` (look at how `stream/routing.rs`
     carries per-channel send state today and match it).
   - A dry/wet mix parameter equivalent in role to `DIFFUSE_SEND_BLEND`
     (default chosen by measurement: highest wet fraction whose
     third-octave deviation stays within ±1.5 dB).
2. PyO3 binding in `packages/dsp/crates/dsp-py/src/spatial.rs`
   (`upmixer_dsp.velvet_pair_send(...)` or similar — mirror the existing
   `diffuse_send` binding signature style, taking an explicit `seed` and
   `side` so Python and wasm produce identical taps).
3. wasm export in `packages/dsp/crates/dsp-wasm/` mirroring the existing
   send exports (offline + engine-side; actual engine wiring is phase 3).
4. Kernel constants (default length, taps, seed) defined once in
   `dsp-core` and re-exported to both bindings — these become
   engine-constants in phase 3, so name them accordingly.

## Tests

Rust, alongside the existing golden tests:

- `dsp-core` unit tests in `decorrelate.rs`:
  - determinism: same seed → identical taps across two constructions;
  - sparsity: tap count and length as specified;
  - per-channel flatness: white-noise (or analytic DFT of the FIR)
    third-octave deviation ≤ ±1.5 dB above 200 Hz at the default mix;
  - pair sum: `|FFT(h_L + h_R)|` deviation from flat ≤ ±1 dB above
    200 Hz (the downmix-flat property — this is the test that must never
    regress);
  - interchannel coherence of the pair outputs on white noise ≤ 0.4
    broadband.
- Golden vector in `packages/dsp/crates/dsp-core/tests/golden_kernels.rs`
  (fixed seed, short input, hashed output) so Python/wasm bit-parity is
  checkable in phase 3.
- Python-side smoke test in `packages/core/tests/` asserting the PyO3
  binding matches the golden vector.

## Out of scope

- Any call-site change (`stem_router.py`, `multichannel.py`, `utils.py`,
  stream engine graph, web) — phase 3.
- Removing `diffuse_send`/`haas_decorrelate` — phase 3 decides what
  survives (Haas is also used by the non-stem `MultichannelUpmixer`, whose
  rework spans phases 3 and 6).
- Frequency-dependent or time-varying decorrelation.

## Done when

- All new Rust tests green (`cargo test` in `packages/dsp`), existing
  golden tests untouched and green.
- Python binding smoke test green; full Python suite unchanged
  (baseline: 846).
- Short numbers table in the PR: per-channel flatness, pair-sum flatness,
  coherence, taps/length/CPU cost estimate per second of audio — phase 3
  cites these.
