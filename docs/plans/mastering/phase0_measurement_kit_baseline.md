# Phase 0 — Mastering measurement kit and compliance baseline

Read `docs/plans/mastering/README.md` first for context and ground rules.
No dependencies; run first. This phase writes no production DSP — it builds
the yardstick every later phase is judged with, captures the "before"
numbers, and settles four audit questions that size phases 1, 2 and 6.

## Goal

Extend the measurement surface from two numbers (integrated LKFS, max
dBTP) to the set a mastering decision actually needs, and baseline the
current chain against the delivery specs.

New measurements, all offline (core-side; streaming counterparts come in
phase 3 only where the UI needs them):

1. **Loudness range (LRA)** per EBU Tech 3342: short-term loudness
   distribution, −20 LU relative gate, 10th–95th percentile spread.
2. **Momentary / short-term maxima** (400 ms / 3 s windows, Tech 3341) —
   the streaming `IntegratedLoudnessMeter` (`loudness_stream.rs`) already
   owns 400 ms blocks at 75% overlap; extend rather than duplicate.
3. **PLR and PSR**: true peak minus integrated, and true peak minus
   loudest short-term. PSR in the loudest sections is the over-limiting
   canary (floor of ~8 is the widely cited guidance).
4. **Per-channel true peak** — today `measure_true_peak` collapses to one
   max; keep the max but retain the per-channel vector (the limiter and
   phase 2's LFE policy both need to know *which* channel peaked).
5. **Gain-reduction statistics** from the limiter and bus compressor:
   peak GR dB, mean GR dB, and duty (fraction of samples under
   reduction). `lookahead_limit` and `bus_compress` already compute peak
   GR internally — surface it instead of recomputing.

## Deliverables

1. Measurement functions in `dsp-core` (`loudness.rs` /
   `loudness_stream.rs`), exposed through PyO3 (`dsp-py`) and wrapped in
   `packages/core/src/loudness.py`. LRA and short-term derive from the
   same K-weighted per-channel power path as integrated loudness — one
   K-weighting pass, three statistics.
2. `MasteringResult` (`mastering/chain.py`) grows optional fields:
   `lra_lu`, `max_momentary_lkfs`, `max_short_term_lkfs`, `plr_db`,
   `psr_db`, `limiter_gr_peak_db`, `limiter_gr_duty`,
   `comp_gr_peak_db`, `comp_gr_avg_db`, `per_channel_tp_dbtp`.
   Populated when `loudness_normalize` is on (same gate as today's
   fields).
3. A report generator (suggested home: `packages/core/src/analysis/` or
   `packages/core/tests/measurement/`, mirroring the mixing kit's
   placement) that renders one text/markdown compliance table for a
   finished render: measured values against a target row. No API/UI work
   in this phase.
4. **Baseline report** committed as `docs/plans/mastering/phase0_report.md`,
   covering at minimum: stereo, 5.1 and 7.1.4 renders of two contrasting
   test programmes (one dense/loud, one dynamic), each at default
   mastering settings and at a deliberately hot target (−10 LKFS) to
   record what the limiter does when leaned on.

## Audit questions to settle (these size later phases)

1. **5.1-fold loudness delta.** For 7.1.4 renders: integrated loudness of
   the full bed (current measurement) vs the same render folded to 5.1
   per BS.775. Report the delta on both test programmes. This is the
   error bar on every Atmos compliance claim the chain currently makes;
   phase 1 implements the fold-referenced measurement.
2. **LFE-link duck depth.** Construct a bed where only LFE approaches the
   ceiling (e.g. `cinema` bass profile, LFE-heavy programme) and measure
   how much gain reduction the *mains* receive from `lookahead_limit`'s
   all-channel link. Sizes phase 2.
3. **96 kHz true-peak factor.** `TRUE_PEAK_FIR_4X` is the BS.1770 ≤48 kHz
   4-phase filter; the standard's table asks 2x at 96 kHz.  Verify what
   the code actually runs at 96 kHz output rate, whether the detector's
   passband is correct there, and record the measured error on a
   synthetic ISP fixture at 96 kHz. Fix in this phase only if it is a
   plain bug; otherwise document in `loudness_dsp_bs1770.md`.
4. **Quantization floor.** Difference signal (24-bit and 16-bit export vs
   float64 master) spectrum, to quantify what undithered truncation
   costs today. Sizes phase 6 (and gives it its acceptance fixture).

## Validation

- New measurements pinned against reference values: pyloudnorm-style
  fixtures for LRA/short-term (synthetic tone steps with known LRA), the
  existing golden true-peak fixtures for the per-channel vector, and
  `stream_equivalence.rs`-style tests only where a streaming counterpart
  is built.
- Full suite green; no behavior change to any production DSP path (this
  phase is measurement-only — `MasteringResult` extras are additive).
- Report includes exact commands to reproduce every table.
