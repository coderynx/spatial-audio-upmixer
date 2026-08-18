# Phase 1 report — Delivery targets and spec-correct immersive measurement

Plan: `docs/plans/mastering/phase1_delivery_targets.md`.
Date: 2026-08-18. Suite: **1174 passed / 44 deselected** — 1157/43 before,
plus this phase's 17 tests and one more perf-marked report generator.
Rust: 198 passed. Web: 255 passed, `npm run build` clean.

## What shipped

- `dsp-core/src/spatial/downmix.rs` — `FoldTo51`, the 5.1 re-render delivery
  specs measure integrated loudness on, next to the BS.775 stereo downmix it
  shares coefficients with. Memoryless, so one set of taps serves the offline
  and streaming paths; `DownmixRole::from_name` moved down from `dsp-py`,
  which was the only thing that knew the name→role mapping.
- `stream/measure.rs` + `stream/engine/analysis.rs` — the preview folds at the
  meter input for a native bed wider than 5.1, weighting the folded programme
  with `FOLD_51_WEIGHTS`. True peak stays on the delivered channels.
- `packages/core/src/mastering/delivery.py` — six named targets, each with
  loudness, ceiling and published tolerance, plus `resolve_delivery_target`.
- `config.loudness_target_preset`, and `loudness_target_lkfs` /
  `loudness_max_tp` became nullable overrides so "unset" is distinguishable
  from "set to the default" — the same shape the comp and bass profiles use.
  Manifest key `mastering.loudness.target_preset`; CLI `--loudness-preset`.
- `MasteringChain` measures and normalizes a bed wider than 5.1 on its 5.1
  re-render. `MasteringResult` / `UpmixResult` grew the compliance block
  (`target_preset`, `target_lkfs`, `target_max_tp_dbtp`,
  `target_tolerance_lu`, `loudness_compliant`, `tp_compliant`,
  `fold_referenced`, `full_bed_lkfs`), which reaches the jobs API through the
  existing per-track result dict.
- API serves `constants.delivery_targets` (the table, with tolerances) and
  `choices.delivery_targets` (the name list).
- Web: a target picker in the Loudness panel, writing the served numbers into
  the existing target/ceiling controls; a "Delivered" column on the jobs table
  showing LKFS, dBTP, the target, whether the number is fold-referenced, and
  pass/fail.

## Delivery targets

| Preset | Integrated | Ceiling | Tolerance |
|---|---|---|---|
| `atmos-music` | −18.0 LKFS | −1.0 dBTP | — |
| `netflix-atmos` | −27.0 LKFS | −2.0 dBTP | ±2 LU |
| `ebu-r128` | −23.0 LUFS | −1.0 dBTP | ±0.5 LU |
| `atsc-a85` | −24.0 LKFS | −2.0 dBTP | ±2 LU |
| `streaming-stereo` | −14.0 LUFS | −1.0 dBTP | — |
| `apple-music` | −16.0 LUFS | −1.0 dBTP | — |

Three of the six publish a tolerance; the other three publish a target
without one, and `tolerance_lu` is `None` there rather than an invented
number. The compliance block then reports the measured value and claims no
pass/fail — `loudness_compliant` is `None`, and only the ceiling is checked.

Netflix's −27 LKFS is **dialog-gated** and this chain has no dialog gate, so
the preset delivers the ungated BS.1770 integrated loudness of the fold.
Recorded as a deviation in `docs/standards/loudness_dsp_bs1770.md`, as the
plan directed.

## What the fold changes

`uv run pytest packages/core/tests/test_master_measurement.py -m perf -s`
reproduces every table below.

### Audit 1, re-run against the shipped chain

Phase 0 sized this with a hand-written fold because the chain measured the
full bed. The chain now reports both numbers itself, so the audit reads its
own pair:

| programme | full bed LKFS | 5.1 fold LKFS | Δ | fold dBTP | LRA full → fold |
|---|---|---|---|---|---|
| dense | -17.68 | -18.00 | -0.32 | -9.84 | 0.1 → 0.0 |
| dynamic | -17.68 | -18.00 | -0.32 | -5.77 | 3.4 → 4.0 |
| height-only | -15.65 | -18.00 | -2.35 | -10.71 | — |

Same deltas phase 0 predicted, from the other side. **The direction is worth
being explicit about: the old master shipped 0.32 dB *quiet*, not hot.** Its
full bed read −18.00, so its 5.1 re-render — the number QC reads — was
−18.32. Phase 1 raises the delivered bed by that 0.32 dB (2.35 dB on
height-only content) to put the re-render on the target.

### Compliance under two named targets

`atmos-music`, both programmes:

| programme | render | LKFS | Δ target | dBTP | LRA LU | PLR | PSR | lim GR pk | duty |
|---|---|---|---|---|---|---|---|---|---|
| dense | stereo | -18.00 | -0.00 | -7.09 | 0.1 | 10.9 | 10.9 | 0.00 | 0.0% |
| dense | 5.1 | -18.00 | +0.00 | -9.23 | 0.1 | 8.8 | 8.7 | 0.00 | 0.0% |
| dense | 7.1.4 | -18.00 | +0.00 | -9.98 | 0.0 | 8.0 | 8.0 | 0.00 | 0.0% |
| dynamic | stereo | -18.00 | -0.00 | -4.42 | 12.9 | 13.6 | 10.1 | 0.00 | 0.0% |
| dynamic | 5.1 | -18.00 | -0.00 | -5.75 | 5.6 | 12.3 | 9.8 | 0.00 | 0.0% |
| dynamic | 7.1.4 | -18.00 | +0.00 | -6.24 | 4.0 | 11.8 | 9.8 | 0.00 | 0.0% |

`streaming-stereo` (−14 LUFS / −1.0 dBTP):

| programme | render | LKFS | Δ target | dBTP | LRA LU | PLR | PSR | lim GR pk | duty |
|---|---|---|---|---|---|---|---|---|---|
| dense | stereo | -14.00 | -0.00 | -3.09 | 0.1 | 10.9 | 10.9 | 0.00 | 0.0% |
| dense | 5.1 | -14.00 | +0.00 | -5.23 | 0.1 | 8.8 | 8.7 | 0.00 | 0.0% |
| dense | 7.1.4 | -14.00 | +0.00 | -5.98 | 0.0 | 8.0 | 8.0 | 0.00 | 0.0% |
| dynamic | stereo | -14.00 | -0.00 | -1.10 | 12.9 | 12.9 | 9.4 | 0.68 | 0.2% |
| dynamic | 5.1 | -14.00 | -0.00 | -1.75 | 5.6 | 12.3 | 9.8 | 0.00 | 0.0% |
| dynamic | 7.1.4 | -14.00 | -0.00 | -2.24 | 4.0 | 11.8 | 9.8 | 0.00 | 0.0% |

Every 7.1.4 row's LKFS is the 5.1 re-render; its full-bed number sits 0.32 dB
above it. The 7.1.4 rows are the only ones that moved from phase 0's baseline
(LRA 0.1 → 0.0 and 3.4 → 4.0, PLR 7.7 → 8.0 and 11.4 → 11.8): those
statistics now come off the folded programme too, which recorrelates channels
that were averaging each other's envelopes. Phase 0 predicted exactly that
LRA shift. Stereo and 5.1 rows are unchanged to the last printed digit.

### A/B — what the change actually does to the audio

The plan asked for a note that landing on the folded number is "audibly
equivalent or better" on height-heavy material. Measured rather than
listened to, because the question has an exact answer: master the same bed
both ways, gain-match, and see what is left.

| programme | target | level Δ | gain-matched residual | limiter GR |
|---|---|---|---|---|
| dense | −18 | +0.317 dB | −321 dB | none |
| dense | −10 | +0.317 dB | −300 dB | none |
| dynamic | −18 | +0.324 dB | −304 dB | none |
| dynamic | −10 | +0.262 dB | **−40.5 dB** | 2.54 → 2.86 dB |

**Wherever the limiter does not engage the two masters are the same audio at
a different level** — the residual is numerical zero, so gain-matched they
are bit-identical and there is nothing to hear beyond 0.32 dB. That covers
every row of both compliance tables above.

Where the limiter does engage, they genuinely differ, and **not in the
direction the plan assumed**: the fold-referenced number is the quieter one,
so reaching the target takes *more* gain and the limiter does *more* work —
peak GR 2.54 → 2.86 dB, duty 10.6% → 17.0% on the dynamic programme at a hot
−10 LKFS target. That is the honest cost of delivering the loudness the
specification asks for instead of 0.32 dB under it, and it only appears at
targets hot enough to limit at all. No listening pass was run; on this
evidence one would only be informative on the hot-target row, where phase 3's
meters and phase 2's limiter work are the relevant follow-ups.

## Preview parity

The preview folds through the same `FoldTo51`, applied a block at a time at
the meter input.  `unit_stream_engine.rs`'s
`an_immersive_bed_measures_its_five_one_re_render` pins the sliced streaming
pass against the blocking `PreviewEngine::measure` to 1e-9 and asserts the
folded number differs from the unfolded one, so a fold that silently stopped
being applied would fail rather than pass quietly.

One pre-existing parity gap closed on the way: the preview measured a native
bed with unity weights on every channel, counting LFE into the BS.1770 sum
and under-weighting the side surrounds by 1.5 dB. A 5.1 preview therefore
calibrated to a different number than the export delivered.
`audioEngine.ts::measureWeights` now sends the layout's real weights.
Recorded in `docs/contracts/preview_export_parity.md` §3.

### Realtime budget

`npm run bench:engine`, medians of three runs on the reference machine:

| row | before | after |
|---|---|---|
| measuring (fast excerpt, playing) | mean 1.463 ms | mean 1.484 ms |
| measuring (exact, paused) | mean 1.870 ms | mean 1.877 ms |
| native 7.1.4 + limiter | mean 0.734 ms | mean 0.727 ms |

The fold costs about **1.5% on the measurement path** and nothing on the
render path, which is what a five-channel accumulate over an already-rendered
block should cost.

**The bench reports FAIL on `measuring (fast excerpt, playing)` — and did
before this phase too.** On this machine that row reads p99 1.69–1.74x and
worst 1.93–2.02x of the deadline both with the pre-phase-1 wasm and with the
new one, so it is a pre-existing overrun, not a regression introduced here.
The `worst` column on the measurement rows is noisy on this hardware (3.1 to
10.6 ms across runs on both sides). Flagged, not fixed: nothing in this phase
touches the excerpt scheduler, and phase 3 is the one that reopens the
preview measurement path.

## Notes and deviations

- **Golden values moved.** `test_mastering_golden.py`'s 7.1.4 channel RMS
  values drop 1.17 dB. That fixture puts full-level content in every height
  and back channel, so its fold delta is far larger than realistic material's
  0.32 dB. Regenerated, with the reason recorded in the file's docstring.
- **`loudness.target` / `max_tp` were not renamed**, as the plan required.
  They became nullable, which is invisible to anything that sets them: the
  web writes both on every manifest, the CLI flag still writes the target,
  and unset now means "defer to the preset, then to the Atmos Music pair".
- **The jobs schema was not given a typed compliance model.** `TrackView.result`
  is already `dict[str, Any]` carrying `UpmixResult.to_dict()`, so the block
  reaches the API the moment the dataclass grows the fields. Typing it would
  have meant either enumerating every unrelated result field in pydantic or
  splitting the payload; the web reads the block through `jobDelivery`, which
  is where the shape is asserted.
- **The 5.1 fold coefficients are not served to the browser**, unlike the
  stereo downmix's. They build the programme a specification names rather
  than a monitoring choice, so both sides read `FoldTo51`'s own constants.
  Recorded in the parity contract §1.
- Knowledge base (`~/Projects/upmixer-knowledge/techniques/mastering_restoration.md`)
  was consulted. It carries no delivery-target or measurement-programme
  guidance and nothing in it conflicts with the above.

## Effect on later phases

| Phase | Change |
|---|---|
| 2 | Unchanged in scope, with one new number: at hot targets the fold-referenced gain raises limiter duty (10.6% → 17.0% on the dynamic programme at −10 LKFS), so the LFE-link fix has slightly more traffic to get right. The +0.0636 dB ceiling overshoot phase 0 handed it is unmoved. |
| 3 | The compliance block phase 1 computes is what the preview meters should display; `MasteringResult`'s `target_*` fields are the reference lines. Also owns the pre-existing `measuring (fast excerpt, playing)` bench overrun above. |
| 8 | The fold now exists as shared code (`FoldTo51`), so downmix QC can measure the 5.1 re-render alongside the stereo fold without writing a second one. |

Nothing here re-scopes phases 4, 5, 6 or 7.
