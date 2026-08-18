# Phase 13 report — multiband transient duck

Plan: `docs/plans/mixing/phase13_multiband_duck.md`. Extends phase 11's
ducker (`phase11_report.md`); baseline tables cited are phase 8's, per the
README ground rules.

## 1. What shipped

`routing::transient::MultibandDucker` replaces `TransientDucker` as the thing
the send path calls. `TransientDucker` survives unchanged — it is now the
*per-band* detector, one instance per band, with every property phase 11 §3
paid for intact.

Nothing crosses the wire that did not already. `depth` is still the only
served value; the two crossover corners join the time constants and the ratio
floor as structural constants in `routing::transient`.

```
DUCK_BAND_LOW_HZ  = 200.0
DUCK_BAND_HIGH_HZ = 4000.0
CROSSOVER_ORDER   = 4          // Linkwitz-Riley, 24 dB/oct
```

Three files changed in `packages/dsp`: `routing/transient.rs` (the split),
`stream/routing.rs` (`StemRouteState`'s ducker type and its per-sample call),
and nothing else — the PyO3 `transient_duck` and the wasm engine keep their
signatures, because the replacement is behind them.

## 2. The crossover, and why it is not a crossover pair

The plan asks for "a complementary Linkwitz-Riley crossover … so the bands
sum flat at unity gain". The bands here are two LR4 **low-passes** and their
**subtractive** complements:

```
low  = LP(200 Hz, x)
rest = x − low
mid  = LP(4 kHz, rest)
high = rest − mid
```

An LR low/high *pair* sums to an all-pass: flat in magnitude, not equal to the
input. Subtraction is exact, and `mastering::bass::lf_unify` already made this
exact argument in this repo ("the complement is taken by subtraction rather
than by a second filter, so it is exact for any low-pass"). The regression
anchor the plan asks for is therefore stronger than "flat within a
third-octave tolerance": `the_bands_sum_back_to_the_input` asserts the three
bands sum to the input sample for sample within 1e-12, on every sample of a
click train.

Both LR designs come from `kernels::butter::linkwitz_riley_lowpass_sos`, the
phase 5 kernel, unmodified. No new filter design was written.

## 3. Measurements

48 kHz, snare-like hits (1 ms rise, 40 ms decay) every 500 ms over a steady
bed of noise + 330 Hz tone, hit windows vs sustain windows, against the same
signal with the duck off.

### 3.1 Broadband hit — the phase 11 case, re-measured

| depth | hit | sustain | separation |
|---|---|---|---|
| 0.3 | −2.42 dB | −0.00 dB | 2.42 dB |
| 0.5 | −4.35 dB | −0.00 dB | 4.35 dB |
| 0.7 | −6.46 dB | −0.00 dB | 6.46 dB |
| 1.0 | −8.77 dB | −0.00 dB | 8.77 dB |

**The new reference figure: at depth 0.7 a broadband onset reaches the
surround and height sends 6.46 dB quieter than the sustain around it**, where
phase 11's broadband ducker gave 7.26 dB.

That 0.8 dB is the honest cost of the split and it is not a regression to fix.
A hit's energy is now judged three times against three local references, and
the crossover's group delay spreads the leading edge across the band
boundaries, so the fast follower sees a slightly gentler slope in each band
than it saw across the sum. Sustain still reads 0.00 dB at every depth — the
ratio floor works per band exactly as it worked broadband.

### 3.2 Per band — a hit confined to one band

Same bed, hit carried by a single tone so it lands in one band only, depth
0.7:

| hit band | hit | sustain |
|---|---|---|
| low (80 Hz) | −4.78 dB | −0.01 dB |
| mid (1 kHz) | −4.00 dB | +0.00 dB |
| high (9 kHz) | −3.18 dB | −0.00 dB |

These read lower than §3.1 by design: a hit living in one band leaves the
other two thirds of the send at unity, so the whole-send energy moves less.
That is the feature, measured. Sustain is untouched in all three.

### 3.3 The motivating case: snare + ride wash

A 200 Hz snare-shaped hit every 500 ms plus a continuous 6–12 kHz band-limited
noise wash. The wash is isolated by band-pass after the duck and measured
inside the 60 ms window each hit ducks, against the same window of the input,
at depth 0.7. Phase 11's broadband detector is reimplemented in the
measurement script for the A/B.

| duck | wash level through the hit |
|---|---|
| phase 11 broadband | **−8.78 dB** |
| phase 13 multiband | **−1.99 dB** |

**6.8 dB more of the ride wash survives the snare hit**, which is the whole
phase in one number. The tolerance the plan asks to be stated is that residual
1.99 dB: the snare is not perfectly band-limited (its own attack has energy
above 4 kHz) and the wash has micro-onsets of its own, so the high band's
detector does fire a little. It is not zero and is not claimed to be.

Whole-file (rather than in-window) the two duckers look nearly identical
(−0.61 vs −0.51 dB on the wash) — the duck only fires for 60 ms out of every
500, so averaging over the file hides exactly the defect this phase fixes.
That is worth recording as a measurement trap for phase 14.

### 3.4 Off is still off

`transient_duck(x, x, sr, 0.0)` returns `x` bit for bit — the crossover is
never even constructed at depth 0.0 — and the Python routing path with
`stem_transient_duck=0.0` stays `np.array_equal` to the untouched field on
every channel (`test_transient_duck_defaults_off_and_leaves_routing_bit_identical`,
`zero_duck_depth_leaves_the_sends_bit_for_bit`).

## 4. Budget

The plan puts the bench first. It ran, and the answer is that the stage is
close to free — but the session was **not on a quiet machine**, and that has
to be stated rather than rounded away.

Method: the phase 11 wasm artifact and the phase 13 one were benched
alternately, four passes, same shell, same fixture (`stem_transient_duck: 1` —
benched on, per §4 of the parity contract).

| case (mean, full depth) | phase 11 | phase 13 | delta |
|---|---|---|---|
| binaural (order-3 decode) | 0.950 / 0.970 ms | 0.972 / 0.998 ms | +2.5% |
| transaural | 0.941 / 0.926 ms | 0.939 / 0.983 ms | +2.9% |
| native 7.1.4 + limiter | 0.710 / 0.802 ms | 0.724 / 0.741 ms | −2.4% |
| stereo downmix | 0.609 / 0.636 ms | 0.609 / 0.592 ms | −3.5% |
| measuring (exact, paused) | 2.066 / 2.139 ms | 2.174 / 2.080 ms | +1.2% |
| measuring (fast excerpt, playing) | 1.734 / 1.746 ms | 1.753 / 1.806 ms | +2.3% |

p99 tracks the same way, within 0.05x of the deadline on every case. **No case
changes verdict between the two artifacts.** Eight extra biquads and two extra
envelope pairs per routed stem cost 2–3% of the mean quantum; the plan's
fallback to 2 bands is not needed.

**What is not green:** in this session `binaural`, `transaural`,
`measuring (exact, paused)` and `measuring (fast excerpt, playing)` sit over
budget — *at both artifacts equally*. The machine carried a load average near
3 with a browser running, and phase 11's own artifact benches at mean 0.36x
here against the 0.31x its report recorded on an idle machine. The absolute
numbers in this session measure the machine, not the change; the delta column
is the readable result. A quiet-machine re-run is owed before the stage is
ever defaulted on, and is recorded in the ledger as D36.

At the shipped default of 0.0 none of this runs: the ducker returns early, the
crossover is never ticked, and the numbers sit on the phase 8 baseline.

## 5. Tests

Phase 11's detector properties now run per band, plus the crossover anchor:

- `the_bands_sum_back_to_the_input` — three bands, no gain, back to the input
  within 1e-12, sample for sample.
- `a_steady_tone_in_any_band_is_left_alone` — 60 Hz, 440 Hz and 9 kHz all pass
  through untouched after their own onset. (Replaces phase 11's single 440 Hz
  test, which only ever exercised the mid band.)
- `a_low_band_hit_leaves_the_high_band_wash_alone` — the §3.3 case as a unit
  test: the wash keeps its level within 0.5 dB through an 80 Hz hit.
- `one_sided_onset_ducks_both_sides` — rewritten. Phase 11 asserted
  `out_l·bed == out_r·left`, which only holds when the duck is one scalar on
  the whole signal; with per-band gains the equivalent statement is that the
  *quiet* side ducks with the loud one, which is what it now asserts.
- `proportional_sides_stay_proportional` — the shared-gain property in its
  surviving exact form: `right = 0.5·left` in, `out_r = 0.5·out_l` out to
  1e-12.
- `zero_depth_is_the_input_bit_for_bit`, `gain_never_leaves_the_depth_bound`,
  `ticking_in_blocks_matches_one_pass` — carried over, the last one now over
  `MultibandDucker` so the crossover state is covered by the ragged-block
  equivalence too.

One existing bound moved: `transients_are_attenuated_and_sustain_is_not` used
a 24-sample (0.5 ms) click and asserted −6 dB; it now asserts −4.5 dB and
measures −5.1. A sub-millisecond click is phase 11's stated ceiling (the
1.5 ms attack is longer than the event), and the crossover's group delay
spreads it further. Real drum transients are 5–50 ms and land in §3.1.

## 6. Validation

- `cd packages/dsp && cargo test` — **184 passed, 0 failed** (139 lib + goldens
  + `stream_equivalence`). The goldens are unaffected because they render at
  depth 0.0, which is the untouched path.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  **1151 passed, 38 deselected**, exactly the phase 12 baseline. No Python
  changed; the suite is what proves the PyO3 surface did not.
- `cd apps/web && npm test` — 246 passed, 31 files. `npm run build` — clean.
- `npm run build:wasm` then `npm run bench:engine` — §4. The committed
  `apps/web/public/wasm/upmixer_dsp.wasm` is rebuilt in the same commit, per
  the build-provenance rule.
- `uv sync … --reinstall-package upmixer-dsp` — the wheel is rebuilt too, so
  both bindings carry the new core.

## 7. Still open

**The A/B listening note the plan asks for has not been done.** Same position
phase 11 ended in, and for the same reason: the objective case is measured
(§3.3 is the motivating case, and it moved 6.8 dB) and the budget delta is
cleared (§4), but "ride/hat wash present in heights through snare hits, no
audible band-split coloration at depth 0" is a judgement on real material.
Until it runs:

- The default stays 0.0 and nothing in the presets sets it.
- The 0.7 figures here are measurements, not a recommended value.

**The quiet-machine bench re-run (§4, ledger D36).** The delta is trustworthy;
the absolutes in this session are not.

This joins phase 7's directional-band A/B, phase 10's preset A/B, phase 11's
duck A/B and phase 12's licensed-corpus run as outstanding listening work. The
phase 11 note about the shared gain — does a hard-panned hi-hat duck the whole
send pair distractingly — is now a *per-band* shared gain, which should make it
less audible, and is still unanswered by measurement.
