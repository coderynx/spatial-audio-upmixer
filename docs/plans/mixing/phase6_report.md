# Phase 6 report — coherence-based derived centre (2026-08-17)

Plan: `docs/plans/mixing/phase6_multichannel_center.md`.

Shipped: `MultichannelUpmixer` no longer derives a missing centre by passive sum.
`_extract_center` pulls the coherent, centre-panned part of FL/FR out with the
same STFT machinery the stereo pipeline uses, hands it to C scaled by `1/a₀`,
and leaves the residual in FL/FR. Convention and its algebra are recorded in
`docs/standards/spatial_layouts_bs775_bs2051.md` § "Deriving a missing centre".

## 1. Reachability — read this before the numbers

The defect is **not reachable through `UpmixPipeline`**. Every input format
wider than stereo in `INPUT_FORMAT_MAP` (5.0, 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2)
contains C, and `pipeline.py` sends ≤ 2 channels to the stereo pipeline
instead, so `"C" not in out` never fires on a file job. The branch is live only
for direct library use of `MultichannelUpmixer` with a centre-less channel dict
— which is exactly what phase 0's measurement kit does (`FL = FR = impulse`,
`INPUT_FORMAT_MAP["stereo"]`), and what any caller upmixing a quad/stereo bed
through this class would hit.

Kept rather than deleted because the class's contract is "derive missing
channels", the kit depends on it, and the fix is small. But it is not a defect
that shipped audio was carrying, and the A/B below is a synthetic direct-call
render, not a real 5.1 file — no delivered mix could have taken this path.

The derived-**LFE** change in the same commit *is* on the live path: 5.0 input →
5.1 output derives LFE.

## 2. Measured — A/B, same source, old vs new derivation

Scratch harness (not committed): 6 s, 48 kHz, stereo-ish bed built as a centred
220 Hz lead (1.5 Hz amplitude envelope) + hard-panned 587 Hz / 740 Hz guitars +
independent noise beds, upmixed to 7.1.4 by direct `MultichannelUpmixer` call.
"FL+FR+C" is the coherent sum at the reference position; "downmix" is
`itu_downmix_stereo` of the full 7.1.4 result.

| measurement | old (`C = a₀·m`, fronts untouched) | new (subtractive) |
|---|---|---|
| front triple energy vs input pair | +0.75 dB | **−0.11 dB** |
| centred lead, FL+FR+C vs input FL+FR | +2.63 dB | **−2.95 dB** |
| panned guitars, FL+FR+C vs input FL+FR | +2.63 dB | **−0.01 dB** |
| downmix, centred lead vs input | +3.41 dB | **+0.42 dB** |
| downmix, panned guitars vs input | +5.88 dB | +4.66 dB |
| front fold null, `FL + a₀·C − FL` | −7.25 dB | **−313 dB** |
| C level rel. input FL | −4.24 dB | −0.08 dB |

Three things to read out of that table:

- The old derivation weighted **everything** in the fronts up by +2.63 dB at the
  reference position, centred and panned alike, because C was a copy of the mid
  and nothing was subtracted. The new one leaves panned content at 0.00 dB.
- The −2.95 dB on centred content is not a loss of the lead, it is the
  power-preserving convention: `√2·m` from one speaker instead of `m` from two.
  Enforcing both the fold identity and the energy identity pins the extraction
  gain at 1.0 — the algebra is in the standards doc — so this 3 dB is
  structural, not a tuning choice. Whoever wants the old on-axis level back is
  asking for the +3.4 dB downmix over-weight back with it.
- The remaining +4.66 dB on the downmixed guitars is the surround/height sends
  folding back in, not the centre; it is unchanged in kind from the old column
  and belongs to the send levels, not this phase.

Fold null at −313 dB means the front triple reconstructs the input fronts to
double precision: BS.775 fold-down of the result is now bit-for-bit the source
front pair, so nothing centred is double-counted downstream.

## 3. Behaviour on the four content cases

`packages/core/tests/test_derived_center.py`, 1 s at 48 kHz:

| input | C | residual fronts |
|---|---|---|
| FL = FR (mono 440 Hz sine) | +3.01 dB (√2·m), all of it | −32 dB, energy sum −0.05 dB vs input pair |
| independent noise L/R | −10.5 dB rel. input | −0.85 dB (essentially unchanged) |
| hard-panned (FR = 0) | −338 dB | FL exact to double precision |
| existing C in input | untouched | untouched, `array_equal` |

The uncorrelated case is the honest weak spot: **−10.5 dB, not silence.**
`CoherenceEstimator.directness_frame` clips negative real correlation to zero,
so per-bin phase noise rectifies to a positive mean directness, and
`center_weight` passes ~0.2 of the mid on genuinely diffuse material. It is
steady-state, not a startup transient (−10.28 dB over 1 s, −10.56 dB over 8 s).
Two reasons it ships as is: the old derivation put −6 dB of the same content in
C *and* kept it in the fronts, so this is a 4.5 dB improvement with the fronts
now accounted for; and the estimator is shared with the stereo pipeline, where
changing the rectification is a separate, wider change. Roadmap 2.3
(CenterWide-class model extraction) is the real fix and slots in behind the same
seam — not started here.

## 4. Code

`center_weight(X_L, X_R, directness, epsilon)` moved to a module-level function
in `decomposition/direct_ambient.py`; `decompose_frame` and `decompose` call it
with identical arithmetic (the stereo path's output is unchanged —
`test_direct_ambient.py` passes untouched), and `_extract_center` is its second
caller. One formula, two paths.

`_extract_center` reuses `STFTAnalyzer` (batch, offline — the multichannel path
is file-based) and `CoherenceEstimator`, driving the frame loop itself so it can
read `directness_frame` per frame; the batch `estimate()` exposes only magnitude
coherence, which treats anti-phase content as centred. Cost measured at **0.33 s
for 30 s of stereo** — irrelevant next to separation.

Other derivations read the **original** FL/FR, as the plan asked: a residual
front has its centred content removed, and the surround/height/LFE sends want it.
Recorded in the class docstring.

LFE source changed from `C if C is not None else mid` to the original front mid
whenever fronts exist. This is a live-path change for 5.0 → 5.1: the LFE feed is
now `0.5·(FL + FR)` lowpassed, not the input C lowpassed. The tradeoff both ways
is real — a real centre channel often carries the kick — but the fronts are the
full-bandwidth pair and the derived centre is not a source of anything the fronts
lack. `test_derived_center.py::test_derived_lfe_uses_the_original_fronts` anchors
it: with near-silent residual fronts, a residual-fed LFE would sit ~30 dB lower.

## 5. Phase 0 kit

Measurement 1c's build-up row was measuring `(FL+FR+C)/(FL+FR)` on the *derived*
fronts, which is meaningless once the fronts are a residual (it would print
+30 dB). Replaced by two rows against the **input** pair, plus the fold error:

| row | value |
|---|---|
| C build-up (FL+FR+C vs input pair) | −0.00 dB |
| C fold-down error (FL+0.707C vs input FL) | −300 dB |
| C broadband gain | +3.01 dB |

Impulse in, so `w = 1` and C is exactly `√2·`impulse. Note for future readers of
that table: unlike every other chain in the kit, the derived C is now
signal-dependent (the coherence weight), so its row is not a transfer function.

## 6. Parity

**No parity work, and no ledger entry.** Channel derivation in
`MultichannelUpmixer` has no counterpart in `dsp-core` — `grep` for centre
handling there returns only the downmix coefficient and ambisonic tests — and
the preview engine renders stems/stereo through the wasm streaming path, never
this class. `docs/contracts/preview_export_parity.md` does not mention
`MultichannelUpmixer` and still does not. No wasm rebuild, no
`npm run bench:engine` run: nothing in this change reaches the audio thread.
D33 (mid-bass decorrelation bench failure) is untouched and still open.

## 7. Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1107 passed, 31 deselected** (baseline on this tree before the change: 1101;
  +6 in `test_derived_center.py`). Zero regressions, no golden regenerated.
- `uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s` → §5.
- Generated audio, direct-call 7.1.4 render + its BS.775 downmix, old and new
  side by side, for the listening pass that is the user's to make:
  `<scratchpad>/upmix_{old,new}.wav`, `downmix_{old,new}.wav`, `source.wav`
  (paths in the session scratchpad, not committed). Objective A/B is §2; I did
  not listen to them, and the phase's "centre image stable, no hollow phantom /
  real-centre comb" claim is unverified by ear.

## 8. What phase 6 did not do

- The stem pipeline's centre handling (routing-table-driven, intentional).
- Roadmap 2.3 model-based extraction.
- The shared coherence estimator's rectification bias (§3).
