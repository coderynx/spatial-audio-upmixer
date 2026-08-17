# Phase 5 report — LFE crossover alignment (2026-08-17)

Plan: `docs/plans/mixing/phase5_lfe_calibration.md`, as re-scoped by
`phase0_report.md`'s verdict ("shrink substantially, one part killed").

Shipped: the LFE bus lowpass is a **Linkwitz-Riley** filter — even order,
squared Butterworth, −6 dB at `lfe_cutoff_hz` — designed once in
`kernels::butter::linkwitz_riley_lowpass_sos` and used by every LFE producer.
Nothing else about LFE level or the preset sends changed.

## 1. What did not ship, and why

The plan's first defect ("the LFE channel plays back +10 dB in-band, the preset
sends are uncompensated") **does not exist**. `UpmixConfig.lfe_gain` is 0.3162 =
−10 dB, cited to BS.775-4 Annex 7, so the send weights are already referenced to
the compensated bus; phase 0 §3 measured LFE arriving 9–10 dB *below* the mains
in-band on the strongest stems. Applying the plan's "fixed −10 dB at build time"
would have taken it 10 dB further down. Neither the compensation multiply nor the
preset re-scale is in this change, and the preset review it gated is answered
below by measurement rather than by adjustment.

## 2. Measured — phase 0's kit, measurement 3

`uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s`.
Definitions unchanged from phase 0 §3: in-band is < 120 Hz, "LFE vs mains"
applies the +10 dB playback weighting before comparing against every other
channel summed, "crossover sum vs power" is the coincident sum against the
incoherent sum over bins within ±10 Hz of 120 Hz.

Both columns are measured on **this tree**, Butterworth-4 against LR4, rather
than against phase 0's printed table: phases 3–4 changed the surround/height
sends, which moves the in-band mains energy for send-heavy stems (Other,
Instrumental) by up to ~2 dB. Bass and Kick reproduce phase 0's numbers exactly,
which is the check that the two runs are otherwise comparable.

| Preset | Stem | LFE vs mains, before | after | crossover, before | after |
|---|---|---|---|---|---|
| balanced | Bass | −9.96 | −10.13 | −2.41 | **−1.62** |
| balanced | Kick | −9.12 | −9.28 | −2.71 | **−1.82** |
| balanced | Toms | −21.37 | −21.53 | −0.56 | −0.40 |
| balanced | Drums | −18.00 | −18.17 | −0.84 | −0.59 |
| balanced | Other | −23.04 | −23.21 | −0.50 | −0.35 |
| balanced | Instrumental | −15.06 | −15.22 | −1.29 | −0.89 |
| intimate | Bass | −10.92 | −11.08 | −2.11 | −1.43 |
| intimate | Kick | −9.57 | −9.73 | −2.55 | −1.71 |
| intimate | Toms | −22.75 | −22.92 | −0.48 | −0.33 |
| intimate | Drums | −18.88 | −19.04 | −0.76 | −0.53 |
| intimate | Other | −26.13 | −26.29 | −0.32 | −0.23 |
| intimate | Instrumental | −16.90 | −17.07 | −1.01 | −0.70 |
| stage | Bass | −9.96 | −10.13 | −2.41 | −1.62 |
| stage | Kick | −9.12 | −9.28 | −2.71 | −1.82 |
| stage | Toms | −21.72 | −21.89 | −0.54 | −0.38 |
| stage | Drums | −18.00 | −18.17 | −0.84 | −0.59 |
| stage | Other | −23.47 | −23.64 | −0.48 | −0.34 |
| stage | Instrumental | −15.06 | −15.22 | −1.29 | −0.89 |
| wide | Bass | −9.94 | −10.11 | −2.42 | −1.63 |
| wide | Kick | −9.12 | −9.28 | −2.71 | −1.82 |
| wide | Toms | −21.41 | −21.58 | −0.56 | −0.39 |
| wide | Drums | −18.27 | −18.44 | −0.81 | −0.57 |
| wide | Other | −22.41 | −22.58 | −0.63 | −0.44 |
| wide | Instrumental | −14.32 | −14.48 | −1.54 | −1.06 |
| immersive | Bass | −9.96 | −10.13 | −2.41 | −1.62 |
| immersive | Kick | −9.12 | −9.28 | −2.71 | −1.82 |
| immersive | Toms | −21.57 | −21.74 | −0.56 | −0.39 |
| immersive | Drums | −18.31 | −18.48 | −0.83 | −0.58 |
| immersive | Other | −20.44 | −20.61 | −0.98 | −0.68 |
| immersive | Instrumental | −13.78 | −13.94 | −1.82 | −1.24 |
| live | Bass | −9.96 | −10.13 | −2.41 | −1.62 |
| live | Kick | −9.12 | −9.28 | −2.71 | −1.82 |
| live | Toms | −21.61 | −21.77 | −0.55 | −0.38 |
| live | Drums | −18.17 | −18.33 | −0.86 | −0.60 |
| live | Other | −21.81 | −21.97 | −0.67 | −0.47 |
| live | Instrumental | −14.31 | −14.47 | −1.52 | −1.04 |

Every row improves, monotonically and in the same direction: the crossover
cancellation is cut by 30–40%, worst case Kick at −2.71 → −1.82 dB, and the LFE
bus loses a uniform **0.16–0.17 dB** of in-band energy (the −6 dB point).

**Preset review: no change.** The plan's target was LFE-vs-mains ≤ 0 dB with the
+10 dB weighting applied. Every row is 9–26 dB under it, and LR moves each one
further under. Kick 0.85–0.90 / Bass 0.75 / `default_lfe_send` stay as they are.

## 3. The ±1.5 dB target is missed, and the allpass that would close it is refused

The plan asks for residual crossover ripple within ±1.5 dB, and for an allpass
evaluation if that is missed. It is missed: Kick lands at −1.82 dB, Bass at
−1.62 dB.

The reason is worth recording, because the plan's expectation was wrong about the
mechanism. **LR4 and Butterworth-4 have the same phase at `f_c` (−180°).** LR's
in-phase property is against its own complementary high-pass, and this crossover
has no complementary high-pass — the mains stay full-range by design. So the
improvement above is not a phase correction at all: it is the −6 dB point putting
less correlated energy into the overlap region. The phase error is essentially
untouched.

Designs measured on the balanced preset, same kit metric (scratch harness, not
committed — it monkeypatches the router's lowpass):

| design | Bass: LFE vs mains | crossover | Kick: LFE vs mains | crossover |
|---|---|---|---|---|
| Butterworth-4 (before) | −9.96 | −2.41 | −9.12 | −2.71 |
| **LR4 (shipped)** | −10.13 | **−1.62** | −9.28 | **−1.82** |
| LR2 | −10.36 | −0.00 | −9.52 | −0.01 |
| Butterworth-2 | −10.03 | −0.00 | −9.18 | −0.00 |
| LR8 | −10.01 | +1.13 | −9.17 | +1.23 |
| LR4 + allpass @ 0.5·f_c | −10.13 | −0.05 | −9.28 | −0.06 |
| LR4 + allpass @ 1.0·f_c | −10.13 | +1.15 | −9.28 | +1.25 |
| zero-phase (filtfilt) LR4 | −10.12 | +1.18 | −9.28 | +1.28 |

Read the zero-phase row as the metric's ceiling: with the LFE 10 dB down, perfect
coherence is +1.2 dB, not 0 dB — so "0.00" is not a target, it is halfway.

**The allpass is refused.** A 2nd-order allpass at 0.5·f_c does reach −0.05 dB,
by rotating the LFE another ~250° at 120 Hz until it wraps back near in-phase.
What it costs, measured on the same biquad: **+8.2 ms group delay at 20 Hz,
+9.1 ms at 40 Hz, +7.5 ms at 60 Hz**, 2.2 ms at 120 Hz. That smears the sub
transient exactly where the LFE carries its energy, to gain 1.8 dB of coincident
sum at 120 Hz where the LFE is already 10 dB below the mains. It would also need
state in four separate producers, and the frequency-domain `LFEExtractor` — a
magnitude mask — cannot represent it at all, so the paths would diverge.

LR2 / Butterworth-2 reach the same place for free, but at 12 dB/octave the LFE
feed carries audible, localizable upper bass past 120 Hz. Steepness is the reason
the order is 4.

So the residual −1.6/−1.8 dB is structural for a full-range-mains layout, and the
only clean way to remove it is a complementary high-pass on the mains — which the
plan puts out of scope, correctly: that is the playback system's bass management,
and BS.775 keeps LFE out of the stereo downmix, so redirecting there would leak
into a path the standard defines without it. Recorded in
`docs/standards/spatial_layouts_bs775_bs2051.md` § "LFE lowpass".

## 4. Where the filter lives now

One design, six call sites, no second implementation:

| Producer | Before | Now |
|---|---|---|
| `StemRouter.route` LFE bus | `upmixer_dsp.lowpass` (Butterworth) | `upmixer_dsp.lfe_lowpass` |
| `MultichannelUpmixer._lfe_filter` | `scipy.signal.butter` + `sosfilt` | `upmixer_dsp.lfe_lowpass` |
| `AdmWriter` LFE bed | `scipy.signal.butter` + `sosfilt` | `upmixer_dsp.lfe_lowpass` |
| binaural / transaural LFE feed | `upmixer_dsp.lowpass` | `upmixer_dsp.lfe_lowpass` |
| `stream::routing::LfeBus` (preview) | `butter_sos` | `linkwitz_riley_lowpass_sos` |
| `dsp-wasm` offline collapse | `butter_sos` | `linkwitz_riley_lowpass_sos` |
| `routing/lfe.py::LFEExtractor` | `1/√(1+r^2N)` mask | `1/(1+r^N)` mask (LR magnitude) |

`lowpass` was renamed to `lfe_lowpass` in the PyO3 surface: it is the LFE feed
and nothing else, and an LR response is wrong for a general-purpose lowpass. The
two scipy LFE filters are gone, so `packages/core` has no second filter design
for this path. Order validation is where the value enters DSP: the Rust design
asserts even and ≥ 2, `LFEExtractor` raises `ValueError`.

`lfe_filter_order` semantics changed meaning — it is now the LR order (even),
where before it was the Butterworth order (any). The default 4 keeps the same
24 dB/octave asymptote, so no config, manifest, or CLI surface changed.

## 5. Parity

Both sides land on the same design, so there is no new ledger entry. Two notes
added to `docs/contracts/preview_export_parity.md` §1:

- the LFE bus row now names the shared design and its two entry points;
- `lfe_filter_order` is the one LFE parameter the browser does **not** receive —
  `lfe_cutoff_hz` is served through `engine_constants()`, the order is hardcoded
  to `4` in both `config.py` and `engineParams.ts`. Nothing user-facing writes it
  (no CLI flag, no manifest key), so it cannot drift in the field; it is a
  two-place edit if it ever moves.

The committed `apps/web/public/wasm/upmixer_dsp.wasm` **did change** this time and
is rebuilt in this commit — `LfeBus` is live in the streaming engine, unlike
phase 4's downmix arms which the wasm build eliminates as dead code.

## 6. Realtime budget

`npm run bench:engine`, freshly built wasm, same machine:

| case | phase 4 | now |
|---|---|---|
| stereo downmix | mean 1.718 ms (0.64x), p99 7.020 ms (2.63x) | mean 1.682 ms (0.63x), p99 6.740 ms (2.53x) |
| native 7.1.4 + limiter | mean 1.847 ms (0.69x), p99 6.870 ms (2.58x) | mean 1.823 ms (0.68x), p99 7.196 ms (2.70x) |

LR4 is two biquad sections, exactly as `butter_sos(4)` was, so the cost is
identical and the difference is run-to-run noise. **The bench still FAILs**, for
the same reason it has since phase 3: mid-bass decorrelation, ledger **D33**,
open and not this phase's to fix.

## 7. Validation

- `cd packages/dsp && cargo test` → **122 lib** (+1: the LR-vs-Butterworth
  cutoff-magnitude test) + 45 integration/golden, all pass.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1101 passed, 31 deselected** (phase 4 left 1099; +2 in `test_lfe.py`: the
  −6 dB mask signature and the odd-order rejection).
- `cd apps/web && npm test` → 249 passed; `npm run build` → clean;
  `npm run build:wasm` → artifact changed (§5).
- Measurement kit → §2.

One golden was regenerated knowingly:
`test_render_metrics_golden.py::test_python_binaural_metrics_golden`, whose
true peak moved −11.199678 → −11.199171 dBTP because the binaural LFE feed is
now LR. BS.1770 loudness excludes LFE from measurement, so the LKFS value moved
only in its last ULP (`c032000000000000` → `c032000000000001`) — through the
mastering chain's own gain staging, not directly. No mastering-chain golden
moved: the chain never sees a differently-built LFE in those fixtures.

The plan also asks for a bit-identity check on a stem with `lfe: 0`. Not written:
the compensation multiply that could have leaked into non-LFE paths is the part
that did not ship (§1), and the only line this change touches on the routing side
writes `channels["LFE"]`. `test_stem_router.py` already pins the LFE bus against
the kernel directly.

## 8. Not done: the A/B listening note

The plan asks for a bass-heavy track on a calibrated 5.1/7.1.4 chain, checking
"low end tighter, no lost weight". **I cannot listen**, so it is outstanding.

What is objectively settled: the LFE bus is 0.16 dB quieter in-band and the
coincident sum through the crossover is ~0.8 dB less cancelled, so the expected
audible direction is slightly *more* low end at the crossover, not less. What a
listener should check: whether 120 Hz now reads as a bump on Kick-heavy material
(the LR8 and allpass rows in §3 show that region can go +1.2 dB coherent if
pushed further); and whether the stereo downmix is unchanged — it must be, BS.775
excludes LFE from the sum, and no downmix test moved.
