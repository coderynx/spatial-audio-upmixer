# Phase 13 — Multiband transient duck

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 11 merged (extends its ducker). Independent of phase 12,
but run after it so listening comparisons happen on the final stem set —
dry stems still carry attack/sustain structure the duck refines.

## Goal

The phase 11 duck is broadband: one gain over the whole stem per sample.
A cymbal-heavy drum stem shows the limit — the snare hit and the ride
wash occupy the same moment, so the onset ducks the wash out of the
sends along with the attack. Split the detector and the gain into bands
so each band ducks on its own onsets: the 6–12 kHz shimmer keeps
flowing to the heights while the 200 Hz–2 kHz body of the hit stays
front-anchored.

## Design

1. **Band split.** 3 bands as the starting point (low / mid / high;
   candidate edges ~200 Hz and ~4 kHz — tune by measurement), realized
   as a complementary Linkwitz-Riley crossover in `dsp-core` so the
   bands sum flat at unity gain (the phase 5 LR work is the in-repo
   precedent for the alignment math; reuse those kernels, do not write a
   new crossover). Reconstruction with all band gains at 1.0 must be
   transparent within float tolerance — this is the regression anchor.
2. **Per-band detector.** The phase 11 detector per band, unchanged
   design (fast/slow envelope pair tracking `fast`, ratio floor, divide
   behind the threshold — all three hard-won properties from
   `phase11_report.md` §3 carry over and their tests run per band).
   Both stereo sides still share one gain per band
   (`one_sided_onset_does_not_shift_the_balance` stays true bandwise).
3. **Structural vs served.** Band edges and time constants are
   structural in `routing::transient`, like the phase 11 constants; only
   `depth` crosses the wire — no new engine constants unless tuning
   proves a second knob is truly needed (resist it; every knob is parity
   surface).
4. **Replacement, not a mode.** Multiband replaces broadband outright —
   no `bands: 1` compatibility switch (`depth = 0.0` remains the off
   switch and stays bit-identical). Phase 11's shipped default is 0.0,
   so no released default behavior changes; the 7.26 dB onset-vs-sustain
   figure gets re-measured per band and recorded as the new reference.
5. **Budget first.** Three detectors + one crossover per routed stem in
   the worklet. Bench the streaming engine cost *before* wiring
   (the D33 lesson, called out in phase 11's constraints): prototype the
   stage, run `npm run bench:engine`, and if it cannot fit alongside
   everything else at worst-case stem counts, reduce to 2 bands or stop
   and report — do not ship a stage that starves the quantum.

## Deliverables

- `dsp-core` `routing/transient.rs` extended (split the file per the
  size policy if it crosses ~400 lines), streaming state in
  `stream::routing` updated, PyO3 + wasm bindings, wasm artifact rebuilt
  and committed (build-provenance rule).
- Parity contract ledger entry + re-hash (the send DSP changed).
- Measurement kit: per-band onset-vs-sustain attenuation figures; a
  synthetic "snare + ride wash" case (click train mixed with band-
  limited sustained noise) proving the wash's send level is unaffected
  by clicks outside its band, within a stated dB tolerance.

## Tests

- Crossover reconstruction flat at unity (third-octave deviation within
  float-level tolerance).
- Phase 11's detector property tests re-run per band: steady sine ducks
  0.00 dB in every band; sub-onset ripple scores zero; one-sided onset
  does not shift balance.
- `depth = 0.0` bit-identical to no-duck path.
- Offline/stream equivalence (`stream_equivalence.rs`) covers the new
  state.
- Full suites green; `npm run bench:engine` green with numbers in the
  report.

## Out of scope

- Per-band *routing* (bands go to the same sends the stem already has —
  this phase shapes send input, never gain maps, per the phase 11
  failure-mode list).
- More than 3 bands, dynamic band edges, program-dependent time
  constants.
- Applying the duck anywhere new (front/centre paths stay unducked).

## Done when

- Bench + measurement + property tests green and reported in
  `docs/plans/mixing/phase13_report.md`.
- A/B listening note: drum-stem-forward track at depth ~0.7 — ride/hat
  wash present in heights through snare hits (the motivating case),
  broadband phase 11 behavior as the comparison, no audible band-split
  coloration on the sends at depth 0 vs phase 11 head.
