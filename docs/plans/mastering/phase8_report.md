# Phase 8 report — Downmix and render QC

Plan: `docs/plans/mastering/phase8_downmix_qc.md`.
Date: 2026-08-19. Suites: Python **1232 → 1242 passed / 45 → 47 deselected**,
web **298 → 306 passed**, `npm run build` clean. Rust untouched — this phase
writes no DSP.

## What shipped

**Core (`packages/core`).**

- `mastering/foldqc.py` — `FoldMeasurement`, `FoldQC`, `measure_folds` and the
  `measure_binaural_qc` gate. One BS.1770 pass plus one true-peak pass over a
  collapsed *copy* of the mastered bed, per fold. No processing: the folds are
  built by `itu_downmix_stereo`, phase 1's `measurement_programme`
  (`FoldTo51`), and `render_binaural_delivery`, all reused as measurement
  programmes.
- `MasteringResult.folds` / `UpmixResult.folds` — a `FoldQC` with
  `native_lkfs` and three unambiguously named slots: `folds.stereo` (BS.775
  2/0 downmix), `folds.surround_51` (the 5.1 re-render), `folds.binaural`
  (the finished binaural render *of this speaker bed* — not a binaural
  delivery, which is its own mastered programme). Each carries `lkfs`,
  `tp_dbtp`, `plr_db`, `lkfs_delta_lu`, `tp_compliant`, `loudness_divergent`.
  `None` for a two-channel delivery, which has no fold to measure.
- `config.qc_measure_binaural` (`bool | None`, unset = auto), manifest key
  `mastering.qc.measure_binaural`. Unset renders the binaural QC programme for
  the `BINAURAL_BED_FORMATS` beds (5.1.4 / 7.1.2 / 7.1.4) and skips it
  everywhere else — on a stereo delivery the binaural path *is* the delivery
  and is already measured.
- `MasteringChain.process` now names the bed's own loudness once
  (`native_lkfs`) and feeds it to both `full_bed_lkfs` and the fold pass,
  instead of measuring it twice.

**API (`apps/api`).** No code change and no new endpoint, as the plan
required: `TrackView.result` is `dict[str, Any]` carrying
`UpmixResult.to_dict()`, so the nested dataclass reaches the browser the
moment core grows it. `test_web_job_folds.py` pins that it survives the JSON
column and view validation with its shape intact — nothing in core would
catch a field that failed to serialize.

**Web (`apps/web`).**

- `features/jobs/status.ts::jobFolds` — the shape assertion and the flag
  aggregation, beside the existing `jobDelivery`.
- `features/jobs/FoldTable.tsx` — the fold table on the job inspector, next to
  the phase 1 "Delivered" column. A flagged row colours the offending cell and
  the caption says the fold is reported, not corrected.
- `features/projects/LoudnessMeters.tsx::collapseModeLabel` + a leading
  **Prog** cell on the phase 3 readout: `Native bed`, `5.1 re-render`,
  `Stereo fold`, `Binaural`, `Transaural`. The preview's measurement pass
  already followed the active collapse mode; nothing said so on screen, which
  is exactly how a stereo-fold reading gets mistaken for the bed's.

**Docs.** `docs/standards/spatial_layouts_bs775_bs2051.md` gains a "Fold QC
thresholds" section next to the fold coefficients — the three programmes, the
two warning conditions, the ±1.5 LU derivation, and why the binaural row
measures the finished render. Two checklist entries. The parity contract is
untouched: the preview gained a label, not a served threshold.

## Fold tables

`uv run pytest packages/core/tests/test_master_measurement.py -m perf -s`
reproduces every table below. Programmes and beds are phase 0's, unchanged.

### dense programme, `atmos-music` defaults

| render | fold | native LKFS | fold LKFS | Δ LU | fold dBTP | PLR | flag |
|---|---|---|---|---|---|---|---|
| stereo | — | — | — | — | — | — | — |
| 5.1 | stereo | -18.00 | -18.67 | -0.67 | -7.88 | 10.8 | OK |
| 7.1.4 | stereo | -17.68 | -18.95 | -1.27 | -7.90 | 11.1 | OK |
| 7.1.4 | surround_51 | -17.68 | -18.00 | -0.32 | -9.84 | 8.2 | OK |
| 7.1.4 | binaural | -17.68 | -18.00 | -0.32 | -7.52 | 10.5 | OK |

### dynamic programme, `atmos-music` defaults

| render | fold | native LKFS | fold LKFS | Δ LU | fold dBTP | PLR | flag |
|---|---|---|---|---|---|---|---|
| stereo | — | — | — | — | — | — | — |
| 5.1 | stereo | -18.00 | -18.63 | -0.63 | -5.27 | 13.4 | OK |
| 7.1.4 | stereo | -17.68 | -18.78 | -1.10 | -5.85 | 12.9 | OK |
| 7.1.4 | surround_51 | -17.68 | -18.00 | -0.32 | -5.77 | 12.2 | OK |
| 7.1.4 | binaural | -17.68 | -18.00 | -0.32 | -5.81 | 12.2 | OK |

The stereo row is empty on purpose: a two-channel delivery is its own stereo
programme, and the "fold" of it would be the master.

### worst cases (7.1.4)

| render | fold | native LKFS | fold LKFS | Δ LU | fold dBTP | PLR | flag |
|---|---|---|---|---|---|---|---|
| height-only | stereo | -15.65 | -19.62 | -3.98 | -9.15 | 10.5 | WARN |
| height-only | surround_51 | -15.65 | -18.00 | -2.35 | -10.71 | 7.3 | WARN |
| height-only | binaural | -15.65 | -18.00 | -2.35 | -6.69 | 11.3 | WARN |
| correlated FL/FR/SL/SR @ −5 LKFS | stereo | -5.05 | -4.23 | +0.83 | **3.55** | 7.8 | WARN |
| correlated FL/FR/SL/SR @ −5 LKFS | surround_51 | -5.05 | -5.05 | +0.00 | -1.10 | 4.0 | OK |
| correlated FL/FR/SL/SR @ −5 LKFS | binaural | -5.05 | -11.60 | -6.55 | -1.00 | 10.6 | WARN |

The second block is the finding the phase exists for. That bed is delivered
**at** the ceiling — the limiter did its job, `tp_compliant` is true on the
master — and its stereo fold lands at **+3.55 dBTP**, 4.55 dB over. The
limiter's guarantee is per channel and a fold is a linear mix:
`Lo = FL + k_s·SL` on identical channels is `1.7071·FL`, 4.65 dB of headroom
nothing in the chain ever budgeted for. The 5.1 fold of the same bed is fine
(+0.00 LU, −1.10 dBTP), because the front and side pairs it collapses stay
distinct.

## The threshold: why ±1.5 LU

| evidence | value | source |
|---|---|---|
| worst realistic fold, any layout | **1.27 LU** (7.1.4 → stereo) | tables above |
| 5.1 → stereo, both programmes | 0.63–0.67 LU | tables above |
| 7.1.4 → 5.1, systematic | 0.32 LU | phase 0 audit 1, phase 1 §"What the fold changes" |
| height-only worst case | 2.35 LU (5.1) / **3.98 LU** (stereo) | tables above, phase 0 audit 1 |
| loosest published delivery tolerance | ±2 LU (Netflix, ATSC A/85) | phase 1 §"Delivery targets" |
| tightest published delivery tolerance | ±0.5 LU (EBU R128) | phase 1 §"Delivery targets" |

±1.5 LU is the only band that satisfies both ends of that table. It sits
**0.23 LU above the worst realistic fold**, so ordinary decorrelated
programme material never warns; and **0.5 LU below the loosest published
tolerance**, so the warning fires before a fold could put the master outside
the spec it was mastered to. EBU R128's ±0.5 LU is not usable as the bound —
it is tighter than the systematic 0.32 LU 5.1-fold bias itself, so every
immersive master would warn. The plan's suggested ±1.5 LU survives contact
with the data; it is adopted, not merely inherited.

The margin at the top end is thin (1.27 against 1.5) and worth saying out
loud: a 7.1.4 mix with more energy overhead than phase 0's front-dominant
trims will cross it. That is the intended behaviour, not a false positive —
see the presets paragraph below.

## Why the binaural row measures the finished render

`render_binaural` applies no level calibration for the number of speakers it
collapses, so a twelve-channel bed sums into two ears at roughly its energy
sum:

| programme | native LKFS | raw LKFS | raw Δ | raw dBTP | delivered LKFS | delivered Δ | delivered dBTP |
|---|---|---|---|---|---|---|---|
| dense | -17.68 | -7.40 | **+10.28** | **3.08** | -18.00 | -0.32 | -7.52 |
| dynamic | -17.68 | -7.46 | **+10.22** | **4.74** | -18.00 | -0.32 | -5.81 |

Both raw columns describe the renderer's own constant, not the master: they
would read +10.2 and clip on *every* immersive export, which is a flag that
carries no information. `folds.binaural` therefore measures what
`render_binaural_delivery` produces — the artifact a listener would actually
get. That correction is capped at `BINAURAL_LOUDNESS_MAX_GAIN_DB` (6 dB)
upward, which is exactly what keeps the row informative: the correlated
fixture above reads **−6.55 LU** because its collapse landed quiet and the cap
could not bring it back.

## What the numbers say about the current presets

On decorrelated, front-dominant material the shipped defaults are safe with
room to spare: every fold of both phase 0 programmes lands inside ±1.27 LU and
6.6 dB under the ceiling. Nothing in the current mastering presets needs to
move, and no preset change is proposed here.

The exposure is in the **mix**, in two distinct ways, and only one of them
implicates the mixing plan.

1. **Height energy.** The stereo fold's divergence scales directly with how
   much of the programme lives overhead: 0.32 LU for the phase 0 trims (heights
   at 0.30–0.35), 3.98 LU when the bed is heights alone. The routing presets
   are not far from the pessimistic end for some stems —
   `docs/standards/spatial_layouts_bs775_bs2051.md` records Crash at **0.86 of
   its routed energy overhead** on `wide`. A percussion-forward mix on `wide`
   is therefore a realistic candidate to cross ±1.5 LU on the stereo fold, and
   the mitigation is a routing/send decision, not a mastering one. **This is
   the mixing plan's territory**: mixing phase 4 (`phase4_downmix_height_fold`)
   made the render and downmix paths agree that heights are audible in stereo,
   which is what makes the fold measurable at all — but agreeing on `k_h` does
   not make a height-heavy mix fold flat, and **nothing in mixing phases 0–13
   measures the loudness consequence** — that plan's downmix check is a null
   test, not a loudness one. The gap belongs to the mixing plan, not to a new
   mastering phase; this phase hands it a metric
   (`folds.stereo.lkfs_delta_lu`), a threshold, and the observation that
   height-forward presets are where it will bite.
2. **Fold correlation.** The over-ceiling case is not about heights at all — it
   is correlated in-phase content across FL/FR and SL/SR, which the diffuse
   send is specifically designed to break up (mixing plan finding 1: the
   single-tap delay blend "combs again on any BS.775 fold-down"). The
   `diffuse_send` work already shipped in mixing phases 1–3 reduces exactly
   this correlation, so the fixture here is a synthetic worst case rather than
   an indictment of the sends. It is worth re-running the fold pass against a
   real `wide` mix before drawing any conclusion about send depth.

No mastering-side correction is proposed for either. A fold-referenced
re-limiter would trade a measurable problem in one delivery for an invisible
one in the delivered bed, which is why the plan put it out of scope and why
this phase warns and stops.

## What the measurement costs

Median of three, 30 s of 48 kHz programme, reference machine, `dense`:

| layout | mastering chain (no binaural QC) | fold pass alone | + binaural QC | binaural render alone |
|---|---|---|---|---|
| 5.1 | 0.824 s | 0.052 s | 0.439 s | 0.387 s |
| 7.1.4 | 1.481 s | 0.183 s | 0.716 s | 0.533 s |

The cheap half is genuinely cheap: the stereo downmix and the 5.1 re-render
together cost **0.183 s on a 7.1.4 bed, 12% of the mastering chain and 0.6% of
the programme's own duration**. They are memoryless matrix sums followed by
the same K-weighting pass phase 0 already runs, and the 5.1 fold is measured
by the chain regardless — the marginal cost of `folds.surround_51` is one
extra true-peak pass.

The binaural QC render is the expensive one, as the plan predicted: **0.533 s,
about 3× the rest of the fold pass and 36% of the mastering chain on 7.1.4**,
because it is a full order-3 HOA encode, a 16-channel decode convolution and
the profile voicing chain. In absolute terms it is still 1.8% of the
programme's duration, and mastering is a small fraction of an export dominated
by stem separation — but it is the one part of this phase worth a gate, and it
has one (`qc_measure_binaural`). Default on for immersive layouts, off for
stereo, per the plan.

## Validation

```
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                    # 1242 passed / 47 deselected
                                    # (1232 / 45 before)
uv run pytest packages/core/tests/test_master_measurement.py -m perf -s
                                    # 8 passed, tables above
cd apps/web && npm test && npm run build
                                    # 306 passed (298 before), build clean
```

New coverage:

- `packages/core/tests/test_fold_qc.py` (9 tests). Two are the goldens the
  plan named:
  - **`test_a_height_only_bed_folds_to_stereo_at_the_height_coefficient`** —
    a 7.1.4 bed carrying nothing but TFL/TFR. Every other term of the 2/0
    matrix is zero, so the fold is `Lo = k_h·TFL` exactly, both channels keep
    BS.1770 unity weight, and the delta is `20·log10(0.7071)` = **−3.01 LU**
    by hand. Asserted to 0.02 LU. The companion test does the same for the 5.1
    re-render, which folds TFL onto FL at the same coefficient and lands on the
    same number from four measured channels down to two.
  - **`test_the_stereo_fold_of_correlated_surround_content_flags_over_the_ceiling`**
    — correlated in-phase content in FL/FR + SL/SR at a target hot enough for
    the limiter to park the bed on the ceiling. Asserts the master itself is
    `tp_compliant`, the stereo fold is not, and the gap is
    `20·log10(1 + k_s)` = 4.65 dB.
  - Plus: a front-dominant decorrelated bed stays inside the threshold and
    raises no flag; a stereo delivery reports `folds is None`; the binaural
    gate is on for 7.1.4/5.1.4, off for 5.1/stereo, and overridable both ways;
    and `UpmixResult.to_dict()` flattens the nested dataclass.
- `apps/api/tests/test_web_job_folds.py` — a real mastered result through
  `json.dumps` and `TrackView.model_validate`, asserting the block's key set
  and that `binaural` is `None` when gated off.
- `apps/web`: `status.test.ts` (3 tests on `jobFolds` — nothing to report,
  measured folds in delivery order, and both flag conditions),
  `FoldTable.test.tsx` (2), `LoudnessMeters.test.ts` (2 on
  `collapseModeLabel`), and one more render test that the readout names its
  programme and changes it with the output mode.
- `test_master_measurement.py` gains `test_fold_qc_tables` and
  `test_audit_binaural_qc_programme`, both perf-marked — the two new
  deselected tests in the count above.

No phase 0/1 table moved: every compliance, audit 1–4 row reproduces to the
last printed digit, which is the check that a measurement-only phase changed
no audio.

## No listening pass, and none owed

This phase changes no audio at all — the delivered bed is bit-identical, and
every fold is measured on a copy. There is nothing to listen to. The listening
passes phases 3 and 6 owe are unaffected and still owed.

## Notes and deviations

- **The jobs schema was still not given a typed compliance model**, matching
  phase 1's decision. Typing `folds` would mean either enumerating every
  unrelated result field in pydantic or splitting the payload; the shape is
  asserted at both ends instead (`test_web_job_folds.py` on the API side,
  `status.test.ts` on the web side).
- **`folds.surround_51` is redundant with `measured_lkfs` on a fold-referenced
  master** — it re-measures the programme the compliance number already came
  from, and its Δ reproduces phase 0 audit 1. Kept anyway: it is the row that
  makes the table readable as a set, its true peak is genuinely new (the
  compliance block measures TP on the delivered bed, not the fold), and the
  duplicate BS.1770 pass over five channels is ~30 ms.
- **`native_lkfs` is the delivered bed's own loudness**, not `measured_lkfs`.
  On a 7.1.4 master those differ by the 0.32 LU fold bias, and referencing the
  bed is what makes `folds.surround_51`'s delta reproduce the phase 0 audit
  rather than reading a structural zero.
- **No CLI flag** for `qc_measure_binaural`. The gate is a QC cost switch, not
  a delivery parameter; it is reachable from `UpmixConfig` and from
  `mastering.qc.measure_binaural` in a manifest, and no CLI surface asked for
  it.
- **`schema.py::_FIELD_MAP` needed the new key too.** Registering a manifest
  block key is not enough on its own — `manifest_parameter_schema` resolves
  every leaf through `_FIELD_MAP` and raises `KeyError` at import of the
  parameter schema, which surfaced as 13 unrelated-looking failures across
  `test_manifest_assets.py`, `test_manifest_bass_migration.py` and
  `test_web_system.py`. Worth knowing for the next block that gets added.
- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It carries no downmix, fold or QC
  guidance — its entries stop at the processing stages themselves — so nothing
  in it conflicts with or informed the above. Its "limit last, measure after"
  doctrine is what this phase extends: it now measures after the limiter on
  the folds too.
- No new Python, JS or Rust dependency. No Rust change, so no
  `npm run build:wasm` and no `npm run bench:engine` — the preview's audio
  thread is untouched, and the only web addition is one static label.
- File sizes: new `foldqc.py` 176, `FoldTable.tsx` 52, `test_fold_qc.py` 194,
  `test_web_job_folds.py` 91. Grown: `chain.py` 491 → 509,
  `test_master_measurement.py` 430 → 535, `status.ts` 57 → 98,
  `LoudnessMeters.tsx` 284 → 305, `JobsPage.tsx` 249 → 255. All inside the
  ~600-line hard cap; nothing needed splitting.
