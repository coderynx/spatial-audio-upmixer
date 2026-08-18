# Phase 0 report — Mastering measurement kit and compliance baseline

Plan: `docs/plans/mastering/phase0_measurement_kit_baseline.md`.
Date: 2026-08-18. Suite: **1157 passed / 38 deselected**, before and after.

## What shipped

- `dsp-core/src/loudness.rs` — EBU Tech 3342 loudness range and the Tech 3341
  momentary (400 ms) / short-term (3 s) maxima, sharing one K-weighting pass
  with the BS.1770 gated integrated measurement (`measure_loudness_stats`),
  plus `measure_true_peak_per_channel`.
- `dsp-core/src/mastering/limiter.rs` — `lookahead_limit` returns
  `LimiterInfo { max_gr_db, duty }` instead of a bare peak.
- `packages/core/src/loudness.py` — `measure_loudness_stats`,
  `measure_true_peak_per_channel`; `ABS_GATE` is now public.
- `MasteringResult` grows `lra_lu`, `max_momentary_lkfs`,
  `max_short_term_lkfs`, `plr_db`, `psr_db`, `limiter_gr_peak_db`,
  `limiter_gr_duty`, `comp_gr_peak_db`, `comp_gr_avg_db`,
  `per_channel_tp_dbtp`. `BusCompressor` and `LookAheadLimiter` expose their
  gain reduction as attributes after `process()`.
- `packages/core/tests/test_master_measurement.py` — the kit and the report
  generator (`_compliance_table`), mirroring the mixing kit's placement.

No production DSP path changed behaviour. `psr_db` is `None` for programmes
shorter than one short-term window, where the short-term maximum is undefined.

## How to reproduce every table

```
uv run pytest packages/core/tests/test_master_measurement.py -m perf -s
```

Programmes are synthetic, seeded (`_SEED = 20260818`), 30 s, 48 kHz, and
deterministic across processes. `dense` is loud near-constant broadband
material with a low crest factor; `dynamic` is the same spectrum under a slow
±9 dB envelope with 24 sparse transient hits. Beds are constructed directly —
the mastering chain is what is under measurement, so nothing upstream of it
participates.

## Compliance baseline

Target row: Dolby Atmos Music, −18 LKFS / −1.0 dBTP (the config defaults).
`max M` / `max S` are the momentary and short-term maxima in LKFS.

### dense programme, target −18 LKFS

| render | LKFS | Δ target | dBTP | worst ch dBTP | LRA LU | max M | max S | PLR | PSR | lim GR pk | lim GR duty | TP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stereo | -18.00 | -0.00 | -7.09 | FL -7.09 | 0.1 | -17.8 | -17.9 | 10.9 | 10.9 | 0.00 | 0.0% | PASS |
| 5.1 | -18.00 | +0.00 | -9.23 | FL -9.23 | 0.1 | -17.9 | -17.9 | 8.8 | 8.7 | 0.00 | 0.0% | PASS |
| 7.1.4 | -18.00 | +0.00 | -10.30 | FL -10.30 | 0.1 | -17.9 | -18.0 | 7.7 | 7.7 | 0.00 | 0.0% | PASS |

### dynamic programme, target −18 LKFS

| render | LKFS | Δ target | dBTP | worst ch dBTP | LRA LU | max M | max S | PLR | PSR | lim GR pk | lim GR duty | TP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stereo | -18.00 | -0.00 | -4.42 | FR -4.42 | 12.9 | -14.2 | -14.5 | 13.6 | 10.1 | 0.00 | 0.0% | PASS |
| 5.1 | -18.00 | -0.00 | -5.75 | FR -5.75 | 5.6 | -15.2 | -15.5 | 12.3 | 9.8 | 0.00 | 0.0% | PASS |
| 7.1.4 | -18.00 | +0.00 | -6.56 | FR -6.56 | 3.4 | -15.9 | -16.2 | 11.4 | 9.7 | 0.00 | 0.0% | PASS |

### dense programme, target −10 LKFS (hot)

| render | LKFS | Δ target | dBTP | worst ch dBTP | LRA LU | max M | max S | PLR | PSR | lim GR pk | lim GR duty | TP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stereo | -10.01 | -0.01 | -1.10 | FL -1.10 | 0.1 | -9.8 | -10.0 | 8.9 | 8.9 | 2.01 | 1.8% | PASS |
| 5.1 | -10.00 | +0.00 | -1.23 | FL -1.23 | 0.1 | -9.9 | -9.9 | 8.8 | 8.7 | 0.00 | 0.0% | PASS |
| 7.1.4 | -10.00 | +0.00 | -2.30 | FL -2.30 | 0.1 | -9.9 | -10.0 | 7.7 | 7.7 | 0.00 | 0.0% | PASS |

### dynamic programme, target −10 LKFS (hot)

| render | LKFS | Δ target | dBTP | worst ch dBTP | LRA LU | max M | max S | PLR | PSR | lim GR pk | lim GR duty | TP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stereo | -10.96 | -0.96 | -0.94 | FR -0.94 | 11.6 | -7.6 | -7.8 | 10.0 | 6.9 | 4.68 | 38.0% | FAIL |
| 5.1 | -10.21 | -0.21 | -0.94 | FR -0.94 | 5.2 | -7.7 | -7.9 | 9.3 | 7.0 | 3.35 | 26.5% | FAIL |
| 7.1.4 | -10.07 | -0.07 | -1.08 | FR -1.08 | 3.2 | -8.1 | -8.4 | 9.0 | 7.3 | 2.54 | 10.6% | PASS |

Reading:

- At the default target the chain is comfortable everywhere: no limiting at
  all, PLR 7.6–14.8, PSR never near the ~8 dB over-limiting floor except on
  the widest dense renders (7.7), where it is the *layout*, not the limiter,
  spending the crest — twelve decorrelated channels sum to more loudness at
  the same peak.
- LRA falls monotonically with layout width on the same source material
  (dynamic: 12.9 → 5.6 → 3.4 LU). Decorrelated channels average each other's
  envelopes in the weighted power sum, so the wider the bed the flatter its
  measured loudness range. Any later phase reading LRA must compare like
  layouts with like.
- The hot target is where the audit's fourth finding shows: at −10 LKFS the
  dynamic programme drives 38% limiter duty and 4.7 dB peak GR in stereo, and
  PSR drops to 6.9 — under the ~8 dB guidance floor. Nothing in the current
  product warns about this. **This is the row phase 1's compliance block and
  phase 3's meters exist to surface.**

### Ceiling overshoot (finding, not a planned audit)

At the hot target the delivered true peak lands **above** the −1.0 dBTP
ceiling on two of the six renders: −0.94 dBTP, an overshoot of **+0.0636 dB**.
`lookahead_limit` reserves `_SAFETY_MARGIN_DB = 0.1` internally and dilates its
gain curve over `FIR_MARGIN_SAMPLES = 6` base samples to stop reduced samples
recombining with unreduced neighbours into fresh inter-sample peaks. Under deep,
fast-moving reduction (38% duty, 4.7 dB) the gain curve varies enough inside the
detector's own support that 6 samples of dilation plus 0.1 dB of headroom no
longer absorb the residual.

Not fixed here — phase 0 writes no production DSP, and the limiter is phase 2's
subject. The kit pins it: `test_compliance_baseline` asserts the worst overshoot
across all twelve renders stays within the limiter's own 0.1 dB safety margin,
so a phase that makes it worse fails loudly. **Phase 2 should close it**, either
by widening the dilation or by iterating the ceiling check once.

## Audit 1 — 5.1-fold loudness delta

The error bar on every Atmos compliance claim the chain currently makes: the
spec measures the 5.1 re-render, `MasteringChain` measures the full bed. Fold
per BS.775 Annex D `b₀ = 0.7071` for back→side and the project's
`k_h = 0.7071` for heights
(`docs/standards/spatial_layouts_bs775_bs2051.md`).

| programme | full bed LKFS | 5.1 fold LKFS | Δ | fold dBTP | LRA full → fold |
|---|---|---|---|---|---|
| dense | -18.00 | -18.32 | -0.32 | -10.15 | 0.1 → 0.0 |
| dynamic | -18.00 | -18.32 | -0.32 | -6.10 | 3.4 → 4.0 |
| height-only | -18.00 | -20.35 | -2.35 | -13.06 | — |

**Verdict: phase 1 stays, but its fold is a correction of a few tenths, not a
few LU, for normal content.** On both realistic programmes the fold reads
0.32 dB *quieter* than the full bed — a bed normalized to −18 delivers −18.3 to
the number distributor QC reads. That is inside most tolerances but outside
EBU R128's ±0.5 LU by half, and it is a systematic bias, not noise: both
programmes land on the same 0.32.

The height-only case bounds the worst case at **−2.35 dB**. Height-heavy
material really can fail a spec the full-bed measurement says it passes, which
is the justification phase 1 needed. Phase 1's fold-referenced number should be
the *primary* reported figure, as planned; the full-bed number stays as the
secondary diagnostic.

Note the fold *raises* LRA slightly (3.4 → 4.0 LU) — folding recorrelates
channels that were averaging each other's envelopes, the same mechanism as the
layout-width trend above.

## Audit 2 — LFE-link duck depth

`lookahead_limit` takes its envelope maximum across every channel, LFE
included, and applies one gain everywhere. Mains here sit at 0.25 peak and on
their own never limit, so every dB below is the LFE's doing. LFE is sparse
40 Hz swells, the shape a `cinema` send produces.

| LFE peak | GR peak dB | GR duty | worst mains gain dB | mains RMS change dB |
|---|---|---|---|---|
| none | 0.00 | 0.0% | +0.00 | +0.00 |
| -3 dBFS | 0.00 | 0.0% | +0.00 | +0.00 |
| +0 dBFS | 1.10 | 7.8% | -1.10 | -0.03 |
| +3 dBFS | 4.10 | 16.4% | -4.10 | -0.22 |
| +6 dBFS | 7.10 | 20.9% | -7.10 | -0.43 |

**Verdict: phase 2 is confirmed and sized.** The duck is one-for-one — every dB
the LFE exceeds the ceiling comes straight off the mains, up to 7.1 dB here,
for 21% of the programme. The RMS column shows why this is easy to miss on a
meter and obvious on headphones: averaged over the programme the mains only
drop 0.43 dB, but the reduction is concentrated in 20% of it, synchronised to
the bass, which is textbook audible pumping. `cinema` sends 50% of the low bus
to LFE, so the +3 and +6 rows are not contrived.

## Audit 3 — 96 kHz true-peak factor

| rate | Hz | f/fs | measured dBTP | exact dBTP | error dB |
|---|---|---|---|---|---|
| 48 kHz | 996 | 0.021 | +0.009 | +0.000 | +0.009 |
| 48 kHz | 5004 | 0.104 | +0.171 | +0.000 | +0.171 |
| 48 kHz | 11520 | 0.240 | +0.195 | +0.000 | +0.195 |
| 48 kHz | 21598 | 0.450 | +0.643 | +0.000 | +0.643 |
| 96 kHz | 1008 | 0.010 | +0.012 | +0.000 | +0.012 |
| 96 kHz | 4992 | 0.052 | +0.219 | +0.000 | +0.219 |
| 96 kHz | 11531 | 0.120 | +0.065 | -0.000 | +0.065 |
| 96 kHz | 21609 | 0.225 | +0.108 | +0.000 | +0.108 |
| 96 kHz | 43195 | 0.450 | +0.643 | +0.000 | +0.643 |

Reference is exact band-limited FFT interpolation of a sine with an integer
number of cycles in the buffer, so the reference is exact rather than merely
finer.

**Verdict: not a bug, nothing to fix.** `TRUE_PEAK_FIR_4X` runs at 4× at every
rate; at 96 kHz that reaches 384 kHz, above the 192 kHz the standard's table
asks for, and the standard explicitly permits higher ratios. The kernel is
specified in normalized frequency, so its error is a function of `f/fs` alone —
identical at both rates for the same fraction (+0.643 dB at 0.45), and *lower*
at any fixed physical frequency at 96 kHz, because the tone sits at half the
fraction (21.6 kHz: +0.643 dB at 48 kHz, +0.108 dB at 96 kHz). The error is
always positive: the detector over-reads near Nyquist rather than under-reading,
so a limiter built on it errs conservative. Documented in
`docs/standards/loudness_dsp_bs1770.md`.

## Audit 4 — quantization floor

Measures the real path — float64 → libsndfile → read back — so the number is
what the export actually does.

| signal | subtype | error RMS dBFS | error vs signal dB | error / round-to-nearest RMS | DC offset (LSB) | error·signal correlation |
|---|---|---|---|---|---|---|
| programme | PCM_24 | -143.3 | -116.8 | 1.994 | -0.498 | +0.0007 |
| programme | PCM_16 | -95.1 | -68.6 | 2.000 | -0.500 | -0.0003 |
| −50 dBFS fade | PCM_24 | -143.3 | -85.5 | 1.993 | -0.498 | -0.0024 |
| −50 dBFS fade | PCM_16 | -95.1 | -37.3 | 2.000 | -0.500 | -0.0002 |

**Verdict: phase 6 is confirmed, and the mechanism is now named.** Two numbers
identify it exactly:

- Error RMS is **2.000×** the round-to-nearest ideal (`lsb/√12`), and
- the error carries a **−0.500 LSB DC offset**.

Round-to-nearest would give 1.000× and no offset. Truncation toward −∞ gives
error uniform over one LSB centred at −½ LSB, whose total RMS is
`√(1/12 + 1/4)·lsb = 2·lsb/√12`. Both depths match to three decimals. **The
writer truncates; it does not round.** Fixing only that (round before dither)
already recovers 6 dB.

For comparison, phase 6's targets: round-to-nearest reads 1.000×, TPDF dither
reads **1.414×** with the error decorrelated from the signal and no DC term.
TPDF is 3 dB noisier than truncation's variance alone but removes the offset
and the distortion; that trade is the whole point.

The −50 dBFS fade is phase 6's acceptance fixture: at 16-bit the error sits only
**37.3 dB** below the signal it is corrupting. Correlation with the signal reads
near zero on both fixtures because the test material is noise-like and the fade
sweeps through many code levels — it is not evidence of dither, and phase 6
should not use correlation alone as its acceptance metric. The DC offset and
the 2.000× ratio are the reliable discriminators.

## Effect on later phases

| Phase | Change |
|---|---|
| 1 | Confirmed, unchanged in scope. Fold correction is −0.32 dB on normal content, −2.35 dB worst case. The fold-referenced number should be primary as planned. |
| 2 | Confirmed and sized: 1 dB of LFE overshoot = 1 dB off the mains, 21% duty at +6 dBFS. **Additionally inherits the +0.0636 dB ceiling overshoot** found above. |
| 3 | Confirmed. The hot-target rows (PSR 6.9, 38% duty) are exactly what the preview has no way to show today. |
| 6 | Confirmed and sharpened: the defect is *truncation*, not merely absence of dither. Acceptance fixture and discriminating metrics are specified above. |
| 0's own audit 3 | Closed, no work. Documented in the standards doc. |

Nothing here re-scopes phases 4, 5, 7 or 8.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/mastering_restoration.md`)
  was consulted. It is concept-level and carries no measurement or dither
  guidance; nothing in it conflicts with the above. Its "limit last, measure
  after" doctrine is what the chain already does.
- No listening note: this phase changes no audio.
