# Phase 2 report — velvet-noise decorrelator pair kernel (2026-08-17)

Plan: `docs/plans/mixing/phase2_velvet_decorrelator_kernel.md`. Baseline it is
judged against: `phase0_report.md` §1b, §1d.

Shipped: `packages/dsp/crates/dsp-core/src/routing/decorrelate.rs` (kernel,
offline + streaming), `kernels/rng.rs` (SplitMix64, now shared with
`mastering/decorrelate.rs` instead of duplicated), the
`upmixer_dsp.velvet_pair_send` PyO3 binding with the `VELVET_*` constants, a
Rust tap-table pin in `golden_kernels.rs`, and
`packages/core/tests/test_velvet_decorrelator.py`. **No call site changed** —
`diffuse_send`/`haas_decorrelate` still run every send, as the plan requires.

## 1. The construction

`2 * taps` velvet cells are laid over the span and handed out alternately, so
the two sides never share a tap position. Three properties then hold by
construction rather than by tuning:

- `<h_L, h_R> = 0` exactly (measured `0.000e+00`);
- `h_L + h_R` is itself a velvet sequence of twice the density, so a
  fold-down has no cancellation structure at any frequency;
- each side carries unit energy at any wet setting (constant-power blend).

Defaults: 30 ms span, 30 taps/side (~1 tap/ms), envelope ratio 0.9 per tap
(−27 dB by the tail), `wet = 1.0`, seed 260797.

## 2. Measured, against the blend it replaces

48 kHz, 65536-point spectra, band 200 Hz–16 kHz. "Dips" counts per-bin
crossings below −10 dB — the metric that distinguishes a comb from a sparse
aperiodic filter. Energy is the filter's own, so it is the broadband gain the
send applies.

| filter | third-octave min/max (dB) | per-bin sigma (dB) | deepest bin (dB) | dips < −10 dB | energy (dB) |
|---|---|---|---|---|---|
| velvet L | −2.31 / +1.99 | 5.81 | −49.8 | 78 | +0.00 |
| velvet R | −1.26 / +2.47 | 5.14 | −45.3 | 72 | −0.00 |
| velvet L+R | −1.84 / +1.95 | 5.46 | −41.0 | 64 | **+3.01** |
| `diffuse_send` L (31 ms) | −0.28 / +0.37 | 5.62 | −17.0 | **490** | −2.97 |
| `diffuse_send` R (37 ms) | −0.33 / +0.57 | 5.62 | −17.0 | **585** | −2.97 |
| `diffuse_send` L+R | −2.42 / +1.33 | 5.17 | −34.2 | 363 | +1.51 |

| property | `diffuse_send` pair | velvet pair |
|---|---|---|
| interchannel correlation, white noise | +0.4032 | **+0.0036** |
| tap inner product | +0.401 | **0.000e+00** |
| fold-down vs power sum | −1.50 dB (cancellation) | **+0.00 dB** |
| broadband gain per side | −2.97 dB | **0.00 dB** |
| dips < −10 dB per side | 490 / 585, evenly spaced | 78 / 72, scattered |

The three wins are the fold-down (phase 0 §1d's worst result: −32 to −238 dB
periodic nulls, gone — the pair cannot cancel), the correlation, and the
2.97 dB the blend was quietly taking off every send.

## 3. Two plan targets are not met, and one plan test is wrong

**Per-channel ±1.5 dB and pair-sum ±1 dB third-octave are not achievable by
any sparse FIR.** With M taps the magnitude response has ~M²/2 non-zero
autocorrelation lags against 2M free parameters, so the response cannot be
flattened; its per-bin magnitude is Rayleigh, with a 5.57 dB sigma floor.
Measured, that floor does not move:

| span | taps/side | best worst-third-octave, best of 400 seeds | per-bin sigma |
|---|---|---|---|
| 30 ms | 30 | 4.14 dB | 5.62 dB |
| 60 ms | 60 | 4.06 dB | 5.90 dB |
| 120 ms | 120 | 3.95 dB | 5.84 dB |
| 200 ms | 200 | 3.97 dB | 5.85 dB |

A sweep over taps ∈ {20, 30, 40, 60} × span ∈ {20, 30, 45} ms × envelope ratio
∈ {0.8, 0.9, 0.97, 1.0} moved per-bin sigma across the range 4.84–6.00 dB and
nothing else. So the seed is the only lever, and it is a real one: over 400k
seeds the pair's worst third-octave deviation ranges 2.5–16 dB, hence
`VELVET_SEED = 260797`, the best of that search (2.47 dB by the search's own
16384-point score, −2.31/+2.47 dB re-measured at 65536).

**The plan's third-octave test would have rejected this kernel and passed the
defect.** `diffuse_send` scores ±0.38 dB third-octave — better than velvet's
±2.5 dB — while carrying 490 evenly spaced −20 dB notches. Third-octave bands
are wider than the notch spacing, exactly as phase 0 §1b warned. The tests in
`decorrelate.rs` therefore assert third-octave deviation < 3.5 dB (a
loose regression bound, not a quality claim) and gate quality on dip count,
fold-down energy, tap disjointness and correlation instead. One test also
re-measures the comb baseline, so the comparison cannot silently rot.

## 4. The wet knob's optimum is 1.0 on all three criteria

Correlation is exactly `1 − wet` (only the dry taps overlap), so the knob
buys nothing:

| wet | pair correlation | third-octave min/max (dB) | per-bin sigma (dB) | fold-down vs power sum (dB) |
|---|---|---|---|---|
| 0.25 | +0.750 | −4.05 / +2.24 | 3.21 | +2.43 |
| 0.50 | +0.500 | −5.62 / +2.71 | 4.93 | +1.76 |
| 0.75 | +0.250 | −5.27 / +2.85 | 5.26 | +0.97 |
| 0.90 | +0.100 | −3.81 / +2.71 | 5.38 | +0.41 |
| 1.00 | +0.000 | −2.31 / +1.99 | 5.81 | **+0.00** |

Any dry component re-correlates the pair *and* puts the fold-down build-up
back (+2.43 dB at wet 0.25) — it is the same defect the phase removes. The
plan's "highest wet fraction within ±1.5 dB" has no answer; the knob is kept
because phase 3 may want a diffusion-depth control, and its default is 1.0.
Per-bin sigma is the one number that improves as the mix goes dry, and only
because the direct path starts dominating.

## 5. Lateral balance

The pair is symmetric in tap count, envelope and energy, and asymmetric in two
places worth recording before phase 3 wires it:

| property | `diffuse_send` pair | velvet pair |
|---|---|---|
| first arrival, L vs R | 0 / 0 ms dry, 31 / 37 ms wet | 0.02 / 0.96 ms |
| energy centroid, L vs R | 18.57 / 22.16 ms | 4.41 / 5.02 ms |
| per-bin \|L\|−\|R\| sigma | 7.96 dB | 7.75 dB |
| third-octave \|L\|−\|R\| sigma / worst | 0.17 / 0.60 dB | 0.98 / 2.07 dB |

The onset lead is 0.94 ms, against the 6 ms interchannel offset of the delays
it replaces (and against `MultichannelUpmixer`, which delays only the right
side by ~30 ms — phase 0 finding 2). Because the two sides are orthogonal there
is no ITD cue to follow, so the residual concern is precedence on transients,
30x smaller than the status quo. Per-side band balance is *worse* than the
blend's (0.98 vs 0.17 dB sigma) — inherent to two independent random draws. If
phase 3's listening pass hears a pull, the cheap fix is a shared first tap
(`wet < 1`), which costs correlation `1 − wet` and the fold-down flatness in §4.

## 6. Cost

`--release`, M3 Pro, both sides of one pair:

| measurement | value |
|---|---|
| 1 s of 48 kHz audio, pair | 2.28 ms (0.23% of realtime) |
| per 128-frame quantum, pair | 6.1 µs of the 2.67 ms budget (0.23%) |
| 8 stems × 2 pairs (surround + height) | ~3.6% of the quantum budget |
| `velvet_pair_default` construction | 0.6 µs |

`VelvetLine`'s ring is rounded up to a power of two so the read index masks
rather than divides — 30 integer divisions per sample saved on the audio
thread. `npm run bench:engine` is not implicated: no streaming code path calls
this yet.

## 7. Parity

Tap positions come from integer SplitMix64 draws and gains from multiplies and
one `sqrt`, so no libm transcendental enters the tap table and native and
wasm builds produce identical filters by construction rather than by
tolerance. The pin is shared verbatim between
`golden_kernels.rs::velvet_pair_matches_the_pinned_tap_table` and
`test_velvet_decorrelator.py`: tap positions integer-exact, gains to 1e-15,
filtered output to 1e-12. Rust and an independent NumPy implementation of the
same construction agree at those tolerances, which is how the seed search
transferred.

No `preview_export_parity.md` ledger entry: nothing routes through this kernel
yet, so there is no discrepancy to record. The ledger entry, the §2 constants
table and the engine-constants endpoint all belong to phase 3, in the commit
that wires it.

## 8. Deliberate deviations from the plan

- **No `dsp-wasm` C export.** The plan asks for one "mirroring the existing
  send exports" — there are none; `dsp-wasm` exposes the engine, the measure
  pass and three offline whole-bed entry points, and sends only reach the
  browser through `stream::engine`, which links `dsp-core` directly. A
  standalone `dsp_velvet_pair` export would have no caller and no test path
  (`dspWasm.test.ts` drives only the ABI the worklet uses). Phase 3's engine
  wiring picks the kernel up with no new export.
- **Constants live in Rust, against `packages/dsp/AGENTS.md`.** That file gives
  tunable acoustic constants to `config.py`; the plan puts `VELVET_*` in
  `dsp-core`. The seed genuinely belongs in Rust (it *is* the filter, and both
  bindings must draw the same one), but span, taps and wet are tunable and
  phase 3 should decide whether they move to `config.py` and get served by
  `engine_constants()`. The functions take all of them as parameters either
  way.
- **No segmented tap gains.** The reference design groups taps into segments
  sharing one gain so sparse convolution needs only adds. Measured, the
  segmented form was slightly *less* flat (best-of-12000 third-octave 3.46 dB
  vs 2.86 dB for per-tap decay) and §6 shows the multiply costs 0.23% of
  budget, so the per-tap envelope stays.

## 9. Validation

- `cd packages/dsp && cargo test` → 121 lib + 45 golden/stream tests pass,
  including 8 new kernel tests and the tap pin; no existing golden vector
  changed.
- `cargo build --release --target wasm32-unknown-unknown -p upmixer-dsp-wasm`
  → clean.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1090 passed, 31 deselected** (phase 1 left 1085; +5 from the new file).
- No `apps/web` change, so `npm test` / `npm run build` / `bench:engine` are
  not implicated.
- No listening note: the kernel is not in any signal path yet. The A/B belongs
  to phase 3, which is also where phase 0 §4's energy table must be re-measured
  (a flat send replaces a −2.97 dB one, so `route`'s renormalization will
  shift the front/send balance at identical send weights).
