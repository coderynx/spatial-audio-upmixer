# Phase 9 — Loudness-domain energy renormalization

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 8 (fresh baseline, bench green). Measurement-first: this
phase may legitimately conclude "no change ships" — that outcome still
closes it.

## Goal

Two level-matching stages use full-track raw energy where perception is
loudness:

1. `StemRouter.route` renormalizes each stem with one full-track scalar
   (`route_scale = sqrt(input_energy / routed_energy)`). Correct on
   average, but energy equates correlated and decorrelated summation and
   weights LFE/height sends as if they were mains — a stem routed mostly
   through decorrelated surround sends can sit audibly quieter (or a
   heavy-LFE stem louder, given +10 dB in-band playback) than its
   front-routed equivalent at identical `route_scale`.
2. `stem_pipeline.py::_normalize_to_source` matches total output energy
   to source energy summing all channels equally — LFE energy counts 1:1
   despite +10 dB playback, heights 1:1 despite elevation EQ shaping.

Related context (read before measuring): the stem null-test audit found
the peak-normalize cascade costs 1.7–3.8 dB on loud masters — that is a
separation-side finding, out of scope here, but its measurement approach
(null tests against the source) is the model for this phase's evidence.

## Step 1 — Measure (gate for the rest of the phase)

Extend `test_mix_measurement.py` with a loudness-tracking measurement:
for each preset × layout, per stem, compare (a) BS.1770-weighted loudness
of the stem's routed contribution (channel-weighted per BS.1770-5
multichannel weighting, LFE excluded per standard, +10 dB applied for the
perceptual variant) against (b) the stem's input loudness — report the
per-stem offset in LU, plus the spread across stems within each preset.
Reuse `packages/core/src/loudness.py` / `dsp-core` loudness kernels; do
not re-implement BS.1770.

Decision gate: if the worst per-stem offset spread within any shipped
preset is under ~1 LU, stop — write the numbers up, close the phase as
"measured, within tolerance, no change". Only proceed if real offsets
show up.

## Step 2 — Fix (only what measurement justified)

- `StemRouter.route`: replace the raw-energy scalar with a
  loudness-consistent one (still a single per-stem scalar — do NOT make
  it time-varying in this phase; windowed gain riding is a different,
  pumping-prone feature). Candidate: weight `routed_energy` terms by
  channel class (BS.1770 channel weights; LFE term weighted for +10 dB
  playback) so the scalar equalizes perceived contribution.
- `_normalize_to_source`: same weighting in the output-energy sum.
- Both changes must keep the escape hatch of exact previous behavior
  reachable for one release only if parity demands it — check whether
  the wasm streaming engine implements the same renormalization
  (`stream/routing.rs`); if it does, the change lands in `dsp-core` once
  and both paths move together; parity contract re-hash either way.

## Tests

- Measurement additions deterministic and perf-marked, like the rest of
  the kit.
- If step 2 ships: a synthetic case proving the fix — two identical-
  loudness stems, one routed front-only and one surround/LFE-heavy, must
  land within 0.25 LU of each other after routing (they do not today;
  assert the before number in the phase report, the after number in the
  test).
- Front-only-routed stems on stereo output: `route_scale` unchanged
  within float tolerance (regression anchor — this change must not move
  plain stereo renders).
- Full suites green (baseline 1107/31); `npm run bench:engine` stays
  green if the streaming path was touched.

## Out of scope

- Time-varying / windowed gain.
- The peak-normalize cascade from the null-test audit (separation-side).
- Mastering-chain loudness normalization (already BS.1770-correct).

## Done when

- Measurement tables in `docs/plans/mixing/phase9_report.md` with either
  the "within tolerance, closed" verdict or before/after numbers.
- If shipped: A/B listening note (surround-heavy preset, level match
  perceived between front-routed and surround-routed stems), parity
  contract updated if touched.
