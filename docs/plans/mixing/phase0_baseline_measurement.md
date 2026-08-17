# Phase 0 — Objective measurement kit and baseline report

Read `docs/plans/mixing/README.md` first for context and ground rules.
No dependencies; run first. This phase writes no production DSP — it builds
the yardstick every later phase is judged with, and captures the "before"
numbers. Its results may shrink later phases (e.g. if measured comb depth
or downmix loss is milder than the audit's analytic estimates).

## Goal

A small, deterministic measurement kit for the mixing path plus a baseline
report. Four measurements:

1. **Send frequency response.** Feed white noise and a log sweep through
   the exact surround and height send chains as `StemRouter.route` builds
   them (`_surround_send` → `diffuse_send` at 31/37 ms, `_height_send` →
   `diffuse_send` at 23/29 ms). Report third-octave-band deviation from
   flat and the worst notch depth in dB, per side. Also measure the
   `MultichannelUpmixer` send chains (surround/back/height derivations).
2. **Downmix null / fold-down comb.** Route a mono pink-noise "stem" with
   a routing that hits SL+SR (and TFL+TFR), render 7.1.4, run
   `itu_downmix_stereo`, and compare the downmix spectrum against the same
   stem folded via `fold_route_to_stereo`. Report (a) spectral ripple
   caused by delayed-copy summation, (b) total energy lost by dropping
   heights, per built-in preset (use `preset_routing` on the 7.1.4 layout
   to get realistic per-stem height fractions — report per stem).
3. **LFE energy audit.** For each preset and stem with an LFE send:
   in-band (< `lfe_cutoff_hz`) energy delivered via LFE **with +10 dB
   playback weighting applied** vs in-band energy delivered via the mains.
   Report the ratio in dB. Also measure the phase relationship between the
   LFE bus lowpass output and the unfiltered mains bass at the crossover
   frequency (magnitude of the complex sum vs power sum, i.e. how much
   cancellation/build-up a coincident listener gets).
4. **Channel energy accounting.** Per preset × layout (stereo, 5.1,
   7.1.4): fraction of each stem's energy landing in front / surround /
   height / LFE after `StemRouter.route` renormalization. This is the
   table later phases use to prove they only changed what they claimed.

## Deliverables

- `packages/core/tests/test_mix_measurement.py` — the kit, marked
  `@pytest.mark.perf` (opt-in, like the eval harness), pure synthetic
  signals, seeded, no model inference and no audio files needed. Helper
  code lives inside the test module; per AGENTS.md, no test-only helpers
  in production code.
- `docs/plans/mixing/phase0_report.md` — the baseline numbers as tables,
  plus a short reading: which later phases the numbers confirm, shrink, or
  kill. Follow the style of `docs/plans/mlx/phase0_report.md`.

## How to run

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s
```

The `-s` run prints the tables; paste them into the report.

## Out of scope

- Any change to production code.
- Listening tests (phase-by-phase A/B comes later; this phase is numbers
  only).
- Real-music corpus measurements — synthetic signals are enough to
  characterize LTI send chains and gain tables.

## Done when

- Kit runs deterministically on a clean checkout via the command above.
- Report written with all four measurement tables and a re-scope verdict
  per later phase (proceed / shrink / kill, one line each).
- Full suite still green (the kit is perf-marked, so the default run is
  unchanged; baseline: 846 tests).
