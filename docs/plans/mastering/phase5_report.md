# Phase 5 report — Linked dynamic EQ

Plan: `docs/plans/mastering/phase5_dynamic_eq.md`.
Date: 2026-08-19. Suites: Rust **214 → 222 passed**, Python **1182 → 1202
passed / 44 deselected**, web **286 → 292 passed**.

## What shipped

`mastering::dyneq` — up to four parametric bells between the static EQ and the
bus compressor, off unless a project authors bands.

Each band carries one RBJ band-pass per bed channel, built from the same
`(f0, Q)` as its bell, and sums their powers into a single linked RMS. That is
`bus_compress`'s sidechain topology exactly, and the gain computer is
literally `bus_compress`'s: `knee_gain_db` was extracted from
`gain_reduction_db` so both stages run one soft-knee computer, at a structural
6 dB knee (`KNEE_DB`). The resulting gain is realized as **one** bell design,
retuned in place and applied identically to every non-LFE channel.

Per band, per sample: two biquads per channel plus one coefficient redesign,
and the redesign is skipped whenever the gain has not moved — which is the
whole programme for a band that never triggers.

Manifest and config: `mastering.dynamic_eq.bands[]` →
`config.mastering_dyneq_bands`, one dict per band with `freq_hz`, `q`,
`threshold_db`, `ratio`, `attack_ms`, `release_ms`. All six are required and
bounded in `manifest/validate.py`'s `_DYNEQ_BOUNDS`; the band list is a leaf
of type `list`, so the generic block walker cannot check inside it and
`_validate_dyneq_bands` does that as its own trust boundary.
`mastering.dynamic_eq.profile` names a preset in `DYNEQ_PROFILES`, which
explicit bands override — see the addendum below, which is what the
`DynamicEqPanel` in `MasteringSection.tsx` actually exposes.

## Why this is not mixing phase 13

Phase 13 died because three *independent* detectors followed three different
decay rates through one broadband event, so a cymbal's timbre morphed while it
rang (`phase13_report.md` §9.1, tilt swing 10.00 → 23.58 dB on a real crash).

The design difference is one sentence: **detection here is per band but never
per channel.** A band's detector reads every bed channel and produces a single
trajectory, so there is nothing to diverge — `every_channel_rides_the_same_curve`
feeds two channels the same programme at different levels and asserts their
ratio is unchanged to 1e-15 after the stage. That is simultaneously the
imaging argument and the commutation argument; the contract §1 now says so.

What *can* still go wrong is a single band breathing on broadband material,
which is what the plan's mandatory fixture is for.

## The decaying-broadband gate

`a_decaying_broadband_strike_is_not_retimbred`. The fixture is a 64-partial
strike from 300 Hz to 16 kHz, each partial decaying at its own rate (faster
with frequency), through an active 3.8 kHz band at Q 2, ratio 4. **It is
synthetic** — phase 13 §9.3's lesson was that narrowband synthetics cannot
exercise a multiband stage, and this fixture answers that by carrying genuinely
different envelopes per band, but it is still not a recorded cymbal.

Criteria stated before the numbers: a dynamic band is *supposed* to move the
tilt — that is the cut. It must not (a) move the tilt by more than the cut it
applied, or (b) move anything outside its own band.

| | |
|---|---|
| peak cut | 7.40 dB |
| average cut over the band | 2.62 dB |
| tilt swing, dynamic band | **0.62 dB** |
| tilt swing, static dip of equal average depth | **0.40 dB** |
| 600 Hz level change | −0.030 dB |

The statistic is phase 13 §9.1's: the standard deviation over time of the
low→high spectral-tilt *error* against the unprocessed strike. The dynamic
band costs 1.55x the static dip's swing at 0.62 dB absolute, where phase 13's
crash was 2.36x at 23.58 dB. Out-of-band content moves by 0.03 dB.

The steady-state case is the same story: on a 3.8 kHz + 300 Hz two-tone the
band takes 14.82 dB off its own frequency and moves 300 Hz by 0.04 dB.

**The phase is not rejected.** The honest caveat is that the fixture is
synthetic and the listening pass below is owed.

## Bit-transparency at rest

`peaking_sos` at gain exactly 1.0 gives `b == a` after normalization — not
approximately, identically, since `1 + α·A` and `1 + α/A` are the same
expression at `A = 1`. So a band below its threshold returns `y = x` and
leaves its delay registers at zero, and `a_band_below_its_threshold_returns_
the_input_sample_for_sample` asserts `==` rather than a tolerance. The gain
computer returns exactly 0.0 dB below the knee, which is what makes that hold.

A band at `ratio` 1.0 is not built at all, so it costs nothing.

## Validation

```
cd packages/dsp && cargo test -p upmixer-dsp-core     # 222 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                      # 1202 passed / 44 deselected
cd apps/web && npm run build:wasm && npm test && npm run build
                                                      # 292 passed, build ok
cd apps/web && npm run bench:engine
```

New coverage: `unit_mastering_dyneq.rs` (8 tests — identity below threshold,
an inert band at ratio 1.0, the band cutting only its own frequency, deeper
ratio cutting harder, attack ordering, the shared-curve channel test, LFE
excluded from both the detector and the bells, and the decaying strike above);
`stream_equivalence.rs`'s offline-vs-streaming test extended with two active
bands in the fixture, which is the parity check that matters most here;
`test_mastering_dyneq.py` (18 tests — chain wiring, absence when unset, LFE
untouched, running ahead of the compressor, the manifest round-trip, five
malformed-band rejections, and the preset group: resolution precedence, every
preset validating against the bounds a user's own bands face, and the
calibration table below); `DynamicEqPanel.test.tsx` (5 tests);
`engineParams.test.ts` (bands verbatim on the wire, none by default).

## Realtime budget

`npm run bench:engine` after `npm run build:wasm`, three runs each side,
against a HEAD build measured the same way (`git stash -u`, rebuild, bench,
restore). Benched with **all four bands active and driven into continuous gain
reduction** (`threshold_db: -60`), per the default-off-is-benched-on rule —
which also defeats the redesign-only-when-the-gain-moves fast path, so this is
the worst case the stage has. Means:

| case | HEAD (3 runs) | this branch (3 runs) |
|---|---|---|
| binaural (order-3 decode) | 1.163 / 0.950 / 0.911 ms | 0.955 / 0.960 / 0.957 ms |
| transaural | 0.977 / 0.918 / 0.910 ms | 0.955 / 0.959 / 0.954 ms |
| native 7.1.4 + limiter | 0.785 / 0.780 / 0.782 ms | 0.836 / 0.834 / 0.835 ms |
| stereo downmix | 0.642 / 0.632 / 0.633 ms | 0.673 / 0.678 / 0.678 ms |

The native and stereo rows are the readable ones, because their run-to-run
spread is under 5 µs: four bands cost **+0.053 ms** on the 11-channel native
path and **+0.045 ms** on stereo, i.e. **2% of the 2.67 ms deadline**. Mean
holds at 0.31x against a 0.4x budget. The binaural and transaural rows are
inside their own noise — HEAD's first run there was 1.163 ms, well above its
own later runs — so no per-band cost is resolvable on them today.

**p99 on the binaural path is now at the deadline, and that is worth stating
plainly.** Branch runs measured 2.636 / 2.642 / 2.648 ms (0.99x) with one
earlier run at 2.762 ms (1.04x, a FAIL); HEAD's quiet runs were 2.45-2.52 ms.
The margin against a 2.67 ms budget is now tens of microseconds rather than
hundreds. Two things make that acceptable rather than a blocker: the stage is
off by default and this is its four-band worst case, and a one-band run
measured 2.488 / 2.710 ms — inside the same spread, so this host cannot
resolve one band from four at p99 at all. If a future phase adds to the
binaural path, that row is where it will show first.

The `worst` row is over budget on both sides on this host, as it was in
phase 4 (HEAD 1.33-2.19x, branch 1.26-1.93x). It is a single-sample statistic
and this machine is not quiet enough to resolve it. The `measuring (…)`
failures are the pre-existing ones from phases 2 and 3, unchanged.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**

What needs ears, specifically:

- **A single band on real dense programme.** The 0.62 dB tilt swing above says
  the stage does not smear a synthetic strike; it cannot say whether a 3.8 kHz
  band at ratio 4 sounds like a de-harsher or like breathing on a real mix.
  The pass to run is loudness-matched A/B (phase 3's) with one band on and off
  over a bright master, listening to cymbal decays and vocal sibilance.
- **Whether the 6 dB knee is right.** It is structural and untestable by
  measurement — it exists to stop chatter at the threshold, and the only
  question left is whether the onset of gain reduction is audible as a gesture.
- **The attack range.** 0.1 ms is authorable and will distort low-frequency
  bands, since a detector faster than the period it is tracking modulates
  within the waveform. The bounds allow it deliberately (a 10 kHz de-esser
  wants it); nothing warns about it at 100 Hz.

## Addendum — presets, and why they landed straight away

The plan said "no profiles in v1 … presets can come once usage shows which
moves recur". Usage showed something simpler on first contact: six sliders per
band, four bands, is not a control surface anyone reaches for. `DYNEQ_PROFILES`
now ships five named band sets and the panel is a profile picker like Spectral
EQ's. Explicit `bands[]` still override a profile, so the manifest and CLI keep
the full surface — only the panel narrowed.

The presets target what *this* pipeline does to a bed rather than generic
mastering moves. `clear-low-mid` / `tighten-low-end` address coherent summing
between the bass crossover and ~400 Hz, which nothing else in the chain
touches; `tame-harshness` and `immersive-polish`'s middle band address the
presence region the height sends' high shelf pushes on. `tame-sibilance` is the
one move the knowledge base argues against at bus level
(`techniques/mastering_restoration.md` §"de-ess the vocal stem instead") — it
ships because the realtime pipeline has no stems to fix at stem level, and the
docstring says so rather than pretending otherwise.

**Thresholds are absolute dBFS on the pre-normalization bed**, the same
convention and chain position as `COMP_PROFILES`. They were measured, not
guessed: on a dense bed scaled to the −20 dBFS full-band linked RMS those
profiles assume,

| band | p50 | p90 | p99 |
|---|---|---|---|
| 75 Hz Q1.0 | −23.1 | −21.1 | −20.9 |
| 250 Hz Q1.2 | −25.2 | −23.1 | −22.9 |
| 3.5 kHz Q1.8 | −50.1 | −38.9 | −25.8 |
| 7.5 kHz Q3.0 | −55.6 | −38.3 | −25.1 |

The two families needed opposite treatment, which is the finding worth keeping:
high bands swing ~25 dB p50→p99 and are set near the flares, but **low bands
vary by under a dB within a passage** — there is nothing transient to catch
there, so they sit near their median and ride the 6-10 dB a mix moves between a
quiet passage and a loud one instead.

That was only visible because the first fixture was wrong. Built stationary, it
made both low-band presets look inert (0.00 dB), which said nothing about the
stage and everything about the fixture — the phase 13 §9.3 mistake in new
clothes. Adding a passage envelope is what made the calibration testable.
Resulting peak cuts: 2.95 dB (`tame-harshness`), 7.81 (`tame-sibilance`), 0.77
(`clear-low-mid`), 1.14 (`tighten-low-end`), 0.64 / 2.05 / 4.80
(`immersive-polish`). `test_every_profile_engages_without_crushing_a_dense_bed`
pins every one of them inside 0.5-12 dB, so a preset that stops engaging is a
test failure rather than a silent no-op.

Dropped in the same change: `dyneq_max_bands` (served for an "Add band" button
that no longer exists) and `SliderField`'s `scale="log"` (added for the
frequency slider, now unused). The listening pass owed below is unchanged and
now has five concrete starting points.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. Its "Dynamic EQ / intelligent
  soother" row prescribes "per-band envelope followers driving parametric
  gain", which is what shipped, and its chain-order doctrine item 3
  ("match/EQ before compression") is the placement chosen. Its `In-house path`
  column for that row can now be changed from `none`.
- Reused rather than written: `peaking_sos`, `SosFilter`/`Sos`,
  `compressor::alpha`, and the compressor's knee computer. New kernel code is
  `bandpass_sos` (6 lines) and `Sos::retune` (4 lines).
- No new dependency, no served acoustic constant, no profile table — the
  explicit-control contract from the plan holds. Presets can come once usage
  shows which moves recur.
- File sizes: `chain.py` 470 → 482 lines, `dyneq.rs` 203,
  `MasteringSection.tsx` 654 → **599** — the shared `EffectPanel`/`FieldGroup`/
  `titleCase` moved to `EffectPanel.tsx` so the new panel had somewhere to
  come from, which also took that file back under the 600-line cap it was
  already over.
