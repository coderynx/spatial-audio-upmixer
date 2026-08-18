# Phase 13 report — multiband transient duck

> **Outcome: the band split was built, measured, shipped, and reverted the
> same day.** It is not in the signal path. On real cymbal stems at the depth
> a user actually reached for it, it modulates timbre badly enough to be
> heard immediately — see §9, which is the part of this report that matters.
> Everything between here and there describes the version that was reverted.


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

### 3.4 Where the split changes nothing: an isolated cymbal stem

**Superseded by §9.3 — this section's conclusion is wrong, and the way it is
wrong is the point.** It was written after the first "muted hi-hats" report to
show the split was not responsible. The signal it used cannot exercise the
split, so it returned no difference; that was read as "no effect" when it
meant "not tested". Left in place because the reasoning error is worth seeing.

Band-limited noise bursts standing in for a cymbal stem, ducked at the same
depth by phase 11's broadband detector and phase 13's multiband one:

| case | depth | phase 11 | phase 13 | delta |
|---|---|---|---|---|
| closed hi-hat, 8ths @120 bpm, 60 ms decay | 0.7 | −4.85 dB | −4.85 dB | 0.00 dB |
| crash, one per 2 s, 1.5 s decay | 0.7 | −0.79 dB | −0.80 dB | 0.00 dB |
| ride, 8ths, 400 ms decay | 0.7 | −0.16 dB | −0.15 dB | +0.00 dB |

**Zero difference, to two decimals.** A cymbal stem is almost entirely inside
one band, so the high band's detector sees what the broadband detector saw and
computes the same score; the other two bands are empty and return unity. The
split can only change behaviour on a stem whose bands carry *different*
material — §3.3's snare-plus-wash — which is the case it was built for and the
only case where it moves.

The conclusion drawn at the time — since disproved — was that the phase
neither helps nor harms a per-piece kit. §9.1 measures the opposite on the
same stems, using real audio instead of band-limited noise.

### 3.5 Off is still off

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


## 9. Reverted — why

A listening report came in the same day: hi-hats and crashes "lose some
frequencies" on a project running `stem_transient_duck: 1`. The stems had not
been re-separated (file mtimes a day older), and the only `dsp-core` change
between the previous committed wasm and this one was the band split, so the
split was the whole difference.

### 9.1 What the split does to a cymbal

Measured on the reporter's own stems, loudest 15 s window of each, depth 1.0.
The statistic is the standard deviation over time of the stem's low→high
spectral tilt *error* against the unducked stem — i.e. how much the duck moves
the timbre rather than the level. A level-only duck scores near zero.

| stem | broadband (phase 11) | multiband (phase 13) |
|---|---|---|
| **Crash** | 10.00 dB | **23.58 dB** |
| **Ride** | 3.63 dB | **11.24 dB** |
| Hi-Hat | 6.69 dB | 7.09 dB |
| Snare | 6.27 dB | 4.76 dB |
| Kick | 8.24 dB | 3.95 dB |

Total level change is within 0.2 dB between the two on every stem. The split
does not duck *more* — it ducks **unevenly**, and on a cymbal that is the
whole audible defect.

The mechanism is the one thing §3 never tested: a cymbal is broadband **and**
decays at a different rate in each band. Three detectors on three decays
diverge through the tail, so the crash's timbre morphs while it rings. A
snare or kick, whose energy is concentrated, gets *better* — the bands track
each other, and the wrong band stops being dragged by the right one.

### 9.2 No coupling value saves it

Blending each band's gain toward an energy-weighted common gain
(`g' = common + k·(g − common)`), swept:

| stem | broadband | k=1.0 | k=0.7 | k=0.5 | k=0.3 |
|---|---|---|---|---|---|
| Crash | 10.00 | 23.58 | 15.10 | 13.23 | 11.24 |
| Ride | 3.63 | 11.24 | 7.28 | 5.99 | 4.60 |

Crash is still worse than broadband at k=0.3, where the split has almost no
selectivity left to justify its cost. There is no setting that keeps the §3.3
benefit and clears the defect.

### 9.3 Why the phase's own measurements missed it

Three tests, all of which the split passed, and none of which could have
caught this:

- §3.3's wash was **continuous band-limited noise** — no attack, no decay, so
  no per-band envelope divergence.
- `a_low_band_hit_leaves_the_high_band_wash_alone` used a **steady 9 kHz
  sine** as the wash, for the same reason.
- §3.4's cymbal A/B used **band-limited noise bursts**, which live in one band
  and therefore cannot diverge across bands at all — it returned 0.00 dB
  difference and was read as "the split is neutral here", when the real
  finding was "this signal cannot exercise the split".

The common error: every synthetic case was **narrowband where real cymbals are
broadband**. A multiband stage can only be validated on material whose bands
carry genuinely different envelopes, and the corpus never contained any.

### 9.4 What stands

Reverted in full (`git revert 42e797f`): `routing::transient` is phase 11's
broadband ducker again, `stream::routing` runs `TransientDucker`, and both
bindings are rebuilt. Phase 13 is **closed as rejected**, not deferred — the
motivating case in §3.3 is real, but it is worth less than the timbre damage
it costs on the stems most likely to be routed overhead, and the plan's own
"no audible band-split coloration" acceptance criterion is exactly what
failed.

If it is ever revisited, the gate is a corpus of **real, broadband, decaying**
cymbal recordings, and the acceptance statistic is §9.1's tilt swing, not
onset-vs-sustain separation.

### 9.5 Postscript — the depth-1.0 endpoint

§9.1's measurements were all taken at depth 1.0, which is the one degenerate
point in the parameter's range: `1.0 - depth * score` with `score` saturated
lands on gain exactly 0.0, so a band that scores full is annihilated rather
than ducked and its neighbours are left sounding alone. A depth sweep over the
same stems shows a discontinuity, not a slope — 0.68 / 2.55 / 1.72 dB
(Crash / Ride / Hi-Hat) at 0.90, 1.79 / 3.71 / 3.18 at 0.99, then
11.41 / 4.90 / 10.74 at 1.00. The broadband ducker jumps the same way
(0.63 → 3.02 on the Crash), so the singularity belongs to the duck, not to
the split.

`DUCK_MIN_GAIN = 0.1` floors the per-band gain at −20 dB and removes it: the
same stems now score 0.70 / 2.70 / 1.80 at depth 1.0, for 0.04–0.4 dB of
maximum ducking depth. See ledger D38.

This does not reopen the phase. Off the endpoint the split still costs
~1.8-2.8x broadband's timbre swing, and §9.2's coupling sweep still has no
setting that clears it. What it does establish is that **23.58 dB was not the
split's number** — it was the endpoint's, amplified by the split. A revisit
would have to re-measure the whole §9.1 table at a floored depth before
arguing anything from it.

### 9.6 Postscript — the detector never had the selectivity the phase assumed

§9.5 is not the only thing measured at the wrong operating point. The
detector's own window, `DUCK_THRESHOLD_RATIO`/`DUCK_FULL_RATIO` = 1.25/2.5,
sits at 1.9 to 8.0 dB over the running mean. Measured over real stems the
fast/slow ratio of *sustained* material runs p75 ~1.2 and p90 ~1.5, while
percussive onsets reach 16-45. The threshold was therefore triggering on the
top quartile of ordinary crest variation, and the stage ducked a ride wash as
heavily as a snare hit — mean score 0.120 against 0.126.

That is the premise of this whole phase inverted. §3.3's motivating case is
"a snare hit must not duck the ride wash sharing its moment", and the band
split was adopted to buy that separation. The detector was never delivering
it in the first place, at any number of bands.

Moved to **2.5/4.0** (8 to 12 dB over the mean), ledger D39. Duty cycle on
sustained material: Guitar 9.3% -> 0.9%, Lead Vocals 20.7% -> 3.6%, Ride
27.6% -> 5.0%; Snare holds 9.5% active and 5.8% saturated. Snare/Ride mean
score 1.05 -> 2.21, and at depth 0.7 a Snare now loses 7.53 dB against a
Ride's 1.90 — the 4x transient/wash separation this stage was supposed to
provide all along.

The threshold and the span turned out to be independent controls, which is
worth stating because tuning them as one number is how the first attempt at
this fix went wrong. `active%` depends on the threshold alone (identical at
2.5/3.5, 2.5/4.0, 2.5/6.0 and 2.5/8.0); the span sets how hard a qualifying
onset ducks. An intermediate 2.5/8.0 measured as excellent selectivity and
was inaudible, because a 5.5-wide span drops snare saturation to 2.5% against
the original window's 9.5%.

**§3.3's 6.8 dB benefit figure is therefore also stale** — it compares one
band against three at a threshold where both ducked the wash indiscriminately.
Before this phase is argued either way again, §3.3 and the §9.1 table both
need re-measuring at the corrected window and a floored depth. The revert
still stands on its own evidence; what no longer stands is the measurement
either side of it.
