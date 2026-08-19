# Phase 3 report — Preview metering and loudness-matched A/B

Plan: `docs/plans/mastering/phase3_preview_metering_ab.md`.
Date: 2026-08-19. Suites: Rust **200 → 204 passed**, Python **1176 passed /
44 deselected** (unchanged), web **262 → 283 passed**.

## What shipped

**Core (`packages/dsp`).**

- `loudness_stream::WindowLoudnessMeter` — EBU Tech 3341 momentary (400 ms)
  and short-term (3 s) loudness over a signal delivered in slices. It reuses
  `ChannelGate` with its block equal to its hop, so a meter block is the same
  K-weighted, `pairwise_sum`-accumulated mean square the integrated meter's
  gating blocks are; a window is the mean of the last 4 or 30 of them. Nothing
  about the filter or the block arithmetic is duplicated.
- `stream::meters::MasterMeters` — momentary, short-term, bus-compressor GR,
  limiter GR (mains), limiter GR (LFE). It rides the existing flat float array
  the worklet already reads once per report, five values after the output
  pair, so the wasm boundary keeps one call and one array.
- `stream::engine::master_meters` (in `engine/analysis.rs`) fills it, all at
  the **emit position**: the loudness windows are fed the collapsed output
  just written (folded to the 5.1 re-render when that is the measurement
  programme, exactly as `MeasurementPass` does), the limiter reports on the
  region it just gained, and the compressor's per-frame reduction — produced a
  whole look-ahead earlier — is queued and read back at the frames now being
  heard, the discipline the duck trace already established (ledger D37).
- `StreamingCompressor::tick` now returns `(gain, gr_db)`. Limiter GR needed
  no new tap: phase 2 already made `StreamingLimiter::process` return
  `LimiterInfo`, including the LFE's own curve.
- `EngineParams::meter_weights` carries the BS.1770 weights for the live
  meters — the same set `measureWeights` already sent with the measurement
  pass, kept with the caller as §2 of the parity contract requires.

**Web (`apps/web`).**

- `LoudnessMeters.tsx` — the preview panel's loudness line (M / S / I / TP /
  target / PLR / PSR, plus the A/B match gain while bypassed) and the master
  strip's gain-reduction bars (compressor, limiter mains, limiter LFE). No
  DSP: every number is measured in the core, sampled from the meter refs at
  ~10 Hz so the page does not re-render at frame rate.
- `wasmEngine/calibration.ts` — the calibration state machine, lifted out of
  `audioEngine.ts`: which programme is measured, which pass is in flight, and
  a per-key cache of every measurement this session.
- `audioAnalysis.ts` grows `correctionGain` and `bypassMatchDb`, the two pure
  functions the A/B match is made of.
- The mode key gains a `mastered`/`bypassed` component, so the A/B's two sides
  are measured separately and the transport is gated on the side it is about
  to play — the same rule a mode switch already went through.

Docs updated in the same phase: parity contract §1 (the momentary/short-term
row), §2 (`meter_weights`), §3 (new **P4**, the monitor-only A/B
compensation) and ledger **D42**; `standards/loudness_dsp_bs1770.md`
(momentary/short-term on the 100 ms grid, where each is measured, and a new
crest-metrics section); `web_ui_design.md` §6.4 (GR bars) and a new §6.8
(loudness readout).

## The A/B: what it matches, and why not `bypass_mastering`

The plan asked for the bypassed side to be measured by forking the engine
with `params.bypass_mastering` set. It is measured by forking the engine
**as it is** instead, which is not the same thing and is the reason for the
deviation: the web's bypass button does not use that flag at all. It strips
the mastering block down to loudness (`monitorMastering`, `masterPreview.ts`)
and pushes that as ordinary parameters, and the core's `bypass_mastering`
skips a *different* set of stages (it leaves the LF unifier running, for one).
Measuring the flag's programme would have calibrated a render nobody plays.
Since the pass already forks whatever the engine currently holds, measuring
the live bypassed configuration is both the smaller change and the correct
one. `bypass_mastering` is left as it was: still parsed, still unused by the
web.

Matching is then a difference of two *delivered* loudnesses, not of two raw
measurements. Each side is normalized as far as its own true-peak ceiling
allows — and the unmastered side, having no limiter, is usually the one whose
ceiling clamp bites, which is precisely the level bias the old A/B suffered
from. What is left over is applied as a monitor scalar folded into the
preview's `output_gain` ramp, capped by the ceiling when it boosts so a
compensated bypass cannot clip the DAC. It is zero whenever the chain is not
bypassed, never reaches a manifest or an export (P4), and is displayed in the
readout while it is in effect.

Invalidation follows the existing signature discipline rather than inventing
one: a measurement is keyed by output mode, spatial profile, transaural
profile and chain state, and every key measured this session is cached, so
the *first* B press costs a fast pass (a few seconds, with the same
"calibrating loudness" banner and transport gate as a mode switch) and every
press after that is instant. Mix edits do not invalidate it — they never did
for the mastered side either, and making the A/B stricter than the thing it
compares against would only make it disagree with what is playing.

## Validation

```
cd packages/dsp && cargo test                      # 204 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                   # 1176 passed / 44 deselected
cd apps/web && npm run build:wasm && npm test && npm run build
                                                   # 283 passed, build ok
cd apps/web && npm run bench:engine
```

New coverage:

- `unit_loudness.rs::the_sliding_windows_match_the_offline_maxima` — the live
  windows' maxima against `measure_loudness_stats`'s, on the same programme,
  to 1e-9, at two slice sizes. This is the meter-vs-offline-kit check the plan
  asked for, stated as an equivalence rather than a stored vector.
- `unit_loudness.rs::the_sliding_windows_floor_at_the_absolute_gate`.
- `unit_stream_engine.rs::the_loudness_windows_read_the_emitted_programme` —
  a whole engine render, then the meter's short-term reading against the
  offline measurement of the last three seconds it actually emitted (0.1 LU).
- `unit_stream_engine.rs::the_gain_reduction_taps_follow_the_stages` — a hot
  programme moves all three GR taps; a quiet one leaves every stage at exactly
  0.0 and the momentary window under −40 LKFS.
- `unit_stream.rs::the_flat_block_is_stems_then_channels_then_output_then_the_master`
  and `dspWasm.test.ts` pin the wire layout from both ends.
- `calibration.test.ts` (7 tests) — the bypass state machine: the playback
  gate, one pass per side then free switching, `raw` only while in flight,
  pause/resume, and which refinements are kept or dropped.
- `audioEngine.test.ts` — `bypassMatchDb` unity until both sides exist, the
  ceiling-induced gap it closes, the normalization-off case, and the no-op.
- `LoudnessMeters.test.ts` / `.render.test.tsx` — readout formatting, crest
  metrics, and that the A/B cell appears only while bypassed and the LFE bar
  only where the layout has one.
- `meters.test.ts` — the master block decodes after the output pair, and a
  short frame falls back to silence rather than `undefined`.

## What the meters cost

`npm run bench:engine` after `npm run build:wasm`, three runs each side,
against a HEAD build measured the same way (`git stash`, rebuild, bench,
restore):

| case | HEAD mean | this branch mean | HEAD p99 | this branch p99 |
|---|---|---|---|---|
| native 7.1.4 + limiter | 0.763 ms (0.29x) | 0.745–0.781 ms (0.28x) | 2.171 ms (0.81x) | 2.078–2.207 ms (0.78x) |
| binaural (order-3 decode) | 0.909 ms (0.34x) | 0.848–0.893 ms (0.32x) | 2.440 ms (0.91x) | 2.214–2.342 ms (0.84x) |
| stereo downmix | 0.617 ms (0.23x) | 0.600–0.620 ms (0.22x) | 2.014 ms (0.76x) | 1.910–1.998 ms (0.75x) |

The honest reading: **the meters cost less than the run-to-run spread on this
host** (±0.03 ms), so the table cannot resolve their price. Budget is mean ≤
0.4x, p99 ≤ 1x; both hold on every row with the same margin they had before.

Why it is that cheap, given the plan flagged it as the risk: the K-weighting
does not run per bed channel. It runs on the **measurement programme**, which
for every native bed wider than 5.1 is the five folded channels (and the LFE
never reaches it — BS.1770 weights it zero, so `ChannelGate` skips it), and
for every collapse mode is the delivered pair. So the worst case is five
channels x two biquad sections per sample plus a memoryless fold, against a
chain that already runs reference-match and EQ convolutions, a linked
compressor, a zero-phase LF unifier and a 4x-oversampled limiter over twelve
channels. The GR taps are free by construction: the compressor's value is a
by-product of a division it already performed, and the limiter's was already
being computed and thrown away.

The two `measuring (…)` rows are unchanged from phase 2 — `fast excerpt,
playing` reports FAIL on **both** sides, identically (HEAD p99 4.693 ms, this
branch 4.440–5.201 ms). Pre-existing, unrelated to this phase.

## Deviations and honest limits

- **Momentary is evaluated on a 100 ms grid**, not continuously. Tech 3341
  asks a meter for at least ten updates a second and that is exactly this
  grid; a continuously-sliding 400 ms window would cost a per-sample ring
  buffer per channel for a difference no one can see at a 30 Hz report rate.
  The offline kit's maxima are taken on the same grid, which is what lets the
  two be pinned against each other.
- **PSR is a sample-peak crest, PLR a true-peak one.** The live meters carry
  sample peak only (`Level::measure`), so the short-term crest reads slightly
  low against a true-peak meter; PLR comes off the measurement pass and is
  true-peak throughout. Both are documented that way in
  `standards/loudness_dsp_bs1770.md` § "Crest metrics". Adding a streaming
  true-peak meter to the render path would put a 4x-oversampled FIR on every
  emitted sample for a readout, which is the one place in this phase where the
  budget genuinely would have been at risk.
- **Integrated and TP are shown as delivered**, i.e. the measurement plus the
  correction gain applied, so they read against the target line directly.
  While a pass is in flight they show the previous programme's numbers; the
  banner already says a calibration is running.

## Phase 2's inherited ceiling overshoot

Phase 2 handed this phase the +0.0636 dB ceiling overshoot with the
recommendation to "leave it, and fold it into phase 3, where the meters make
deep-GR operation visible to the user in the first place". That is what
shipped: the overshoot only appears at ~38% limiter duty and 4.7 dB peak GR,
and both of those are now on screen — three GR bars on the master strip, and a
TP cell that turns `warning` when the delivered true peak clears the ceiling.
No DSP change was made for it, and `test_compliance_baseline` still pins it
under the limiter's 0.1 dB safety margin.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**
The plan says this is the phase that makes every later listening note
trustworthy, so it is worth being precise about what is now true and what
still needs ears.

What is now true, and testable without ears: pressing B compares two
programmes normalized to the same delivered loudness, to within the
floating-point noise of two BS.1770 measurements, and the preview refuses to
play the bypassed side until it has measured it. What needs ears: whether the
match *sounds* level-matched on real programme material — a 0.0 dB
integrated-loudness match can still read as "the mastered one is louder" when
the mastering moved the crest factor, which is exactly what PLR/PSR are on
screen to explain. The pass to run is the two phase 0 test programmes, B
toggled at matched loudness, listening for tone and dynamics rather than
level, with the PSR readout watched across the toggle.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It carries no metering, crest or
  A/B guidance at all — its mastering entries stop at the stages themselves —
  so nothing in it conflicts with or informs the above.
- Two files were over the repo's ~600-line hard cap after this phase and were
  split along their existing seams: `stream/engine/mod.rs` (689 → 375) lost
  its render loop and look-ahead queues to a new `stream/engine/render.rs`
  (330), and `audioEngine.ts` (706 → 647) lost its calibration state machine
  to `wasmEngine/calibration.ts` (124). The second split is why the bypass
  state machine has direct unit tests at all.
- One latent bug was fixed on the way, since the new compressor-GR trace would
  have inherited it: `jump_to` left the per-frame meter traces based at frame
  0 while the audio queues moved to the landing frame, so the first `fill_pre`
  after a seek resized them across the whole skipped span — minutes of `1.0`
  allocated per stem on a seek into a long track.
