# Phase 4 report — heights fold into the stereo downmix (2026-08-17)

Plan: `docs/plans/mixing/phase4_downmix_height_fold.md`. Baselines: `phase0_report.md`
§2a–§2c, re-judged against `phase3_report.md` §3d, which enlarged the target.

Shipped: `itu_downmix_stereo` / `itu_downmix_mono` accept TFL/TFR/TBL/TBR and fold
them at a configurable height coefficient `k_h`, default 1/√2. Both stereo paths —
the written downmix and the preview's stereo monitoring — now carry height content.

## 1. The design decision

BS.775-4 defines no height coefficient, so this is a project convention, recorded
in `docs/standards/spatial_layouts_bs775_bs2051.md` § "Height fold-down (project
convention, outside BS.775)":

```
Lo = FL + a₀·C + k_s·(SL + a₀·BL) + k_h·(TFL + k_s·TBL)
Ro = FR + a₀·C + k_s·(SR + a₀·BR) + k_h·(TFR + k_s·TBR)
Mo = a₀·(FL + FR + k_h·(TFL + TFR)) + C
     + k_s·(SL + SR + a₀·(BL + BR) + k_h·(TBL + TBR))
```

Front heights fold onto the front pair, back heights onto the surrounds and so
through `k_s` as well — the same relationship BS.775 already gives back surrounds
against sides. `k_h = 0.7071` by default (`config.height_downmix_coeff`,
`format.downmix.height_coeff`, `--downmix-height-coeff`), `0.0` reproduces the
standard's height-free matrices exactly.

`docs/standards/dolby_atmos_profile.md` was checked as the plan asks: it carries no
downmix-metadata constraint at all (no re-render or downmix coefficient anywhere in
it), so nothing there had to stay consistent with this.

## 2. Measured — phase 0's kit, measurement 2

`uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s`, 2.5 s.
§2b/§2c changed definition: phase 0 could only report the loss *if heights are
dropped*, which was the whole defect. Both now report loss with the heights
dropped (`k_h = 0`, i.e. before) and folded (default `k_h`, after), and the folded
column runs the real kernel over the routed bed rather than a formula.

### 2a. Downmix vs the direct stereo render, balanced

| Stem | offset, dropped (dB) | offset, folded (dB) | band ripple p-p (dB) | per-bin σ (dB) | worst notch rel. (dB) |
|---|---|---|---|---|---|
| Crowd | -2.27 | **-2.14** | 8.50 | 5.70 | -41.48 @ 3102 Hz |
| Other | +0.80 | +0.83 | 1.67 | 1.09 | -7.22 @ 15807 Hz |
| Crash | -1.54 | **-0.98** | 5.94 | 3.68 | -51.64 @ 9102 Hz |
| Hi-Hat | +0.45 | +0.59 | 2.96 | 1.78 | -9.89 @ 14498 Hz |
| Backing Vocals | +1.51 | +1.58 | 2.22 | 1.33 | -6.30 @ 14498 Hz |
| Drums | +2.16 | +2.16 | 0.07 | 0.04 | -0.58 @ 13806 Hz |

Phase 0's sharpest observation here was that the height-only stems (Crash, Hi-Hat,
Backing Vocals) showed **exactly zero ripple** — the downmix was spectrally the bare
front bed, the height content simply absent. That is gone: those stems now carry the
velvet-decorrelated height sends into the downmix, which is what puts 3–6 dB of
ripple into their column. That ripple is the *content arriving*, not a new defect;
it is the same aperiodic sparse-FIR ripple phase 3 §3a characterized, and it is what
the stereo render has always had.

The residual level offsets are the pan-law/level-law difference phase 0 §2a already
explained, unchanged in kind: the render path renormalizes each stem to its own
input energy, the downmix does not. Folding heights moves the two images closer for
the height-forward stems (Crash −1.54 → −0.98 dB) and leaves the rest where they
were.

### 2b. Stem energy the downmix fails to carry, per preset (7.1.4)

| Preset | mean loss, dropped (dB) | mean loss, folded (dB) | worst stem, folded (dB) | max height fraction |
|---|---|---|---|---|
| balanced | 0.34 | **0.18** | Crowd 1.11 | 0.301 |
| intimate | 0.11 | **0.06** | Crowd 0.27 | 0.106 |
| stage | 0.48 | **0.23** | Crash 1.62 | 0.621 |
| wide | 1.19 | **0.46** | Crash 2.44 | 0.861 |
| immersive | 1.99 | **0.80** | Crowd 2.78 | 0.813 |
| live | 0.76 | **0.33** | Crash 1.59 | 0.613 |

The plan's acceptance criterion — height-zone stems must no longer lose the majority
of their energy — holds: `wide`/Crash was the worst case in the whole phase, at
**8.56 dB** dropped (phase 3 §3d's number, same measurement) and **2.44 dB** folded.
`immersive`/Crash 7.29 → 2.27 dB, `immersive`/Crowd 4.33 → 2.78 dB. Per-preset
means roughly halve.

The remaining loss is `k_h` itself and is deliberate: a front height arrives at
k_h² = 0.50 of its energy, a back height at (k_h·k_s)² = 0.25, which is why Crowd —
routed to the back heights — stays worst after the fix while Crash improves most.
Folding heights at unity would make the stereo downmix *louder* than the bed it came
from; −3 dB per fold is the re-render convention and it is a knob if a project wants
otherwise.

### 2c. Per stem, balanced (7.1.4)

| Stem | height fraction | loss dropped (dB) | loss folded (dB) |
|---|---|---|---|
| Backing Vocals | 0.089 | 0.41 | 0.20 |
| Hi-Hat | 0.122 | 0.57 | 0.27 |
| Ride | 0.205 | 0.99 | 0.47 |
| Crash | 0.299 | 1.54 | 0.70 |
| Crowd | 0.301 | 1.56 | 1.11 |
| Other | 0.040 | 0.18 | 0.09 |
| Instrumental | 0.017 | 0.07 | 0.04 |
| Piano | 0.014 | 0.06 | 0.03 |
| Guitar | 0.009 | 0.04 | 0.02 |
| Toms | 0.001 | 0.00 | 0.00 |

(Stems with no height send — Lead Vocals, Vocals, Bass, Kick, Snare, Drums — are
0.000/−0.00/−0.00 and omitted.)

## 3. Parity

The kernel lands once in `dsp-core` and the PyO3 export path calls it, so the export
side has no second implementation. The preview does not call that kernel: `stream::
output` mixes per speaker from a `(left, right)` gain pair per channel, built in
`engineParams.ts::downmixGains` out of the served coefficients. That is a real
second copy of the *matrix* (not of the coefficients), and it was updated in the same
change — TFL/TFR at `k_h`, TBL/TBR at `k_h·k_s`, matching the kernel row for row.
`preview_export_parity.md` §1 now says so explicitly under the downmix row, so the
next person changing this does not update one side only. `engineParams.test.ts`
pins the four new entries.

`height_downmix_coeff` is a tunable, not structural, so unlike the velvet seeds it
*is* served: `engine_constants()` → `EngineConstants` → `engineParams.ts`, with the
fixture and `test_web_system.py`'s key set updated.

The committed `apps/web/public/wasm/upmixer_dsp.wasm` was rebuilt (`npm run
build:wasm`) and came out **byte-identical** to the committed one: the wasm engine
does not reach `spatial::downmix` except for `soft_limit`, so the new kernel arms
are dead-code-eliminated from that build. No artifact change is in this commit —
which is the correct outcome, not a skipped step (D33's failure mode was the
opposite).

## 4. Realtime budget

`npm run bench:engine`, freshly built wasm, same machine, before/after this phase:

| case | before | after |
|---|---|---|
| stereo downmix | mean 1.735 ms (0.65x), p99 7.068 ms (2.65x), worst 8.390 ms (3.15x) | mean 1.718 ms (0.64x), p99 7.020 ms (2.63x), worst 8.249 ms (3.09x) |
| native 7.1.4 + limiter | mean 1.836 ms (0.69x), p99 7.479 ms (2.80x) | mean 1.847 ms (0.69x), p99 6.870 ms (2.58x) |

Four extra gain-and-add pairs per quantum cost nothing measurable; the difference is
run-to-run noise. **The bench still FAILs**, exactly as it did before this phase and
for the same reason: mid-bass decorrelation, ledger **D33**, open and not this
phase's to fix.

## 5. Validation

- `cargo test` in `packages/dsp` → **121 lib** (+2: the height fold and the
  `k_h = 0` escape hatch) + 45 integration/golden, all pass.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1099 passed, 31 deselected** (phase 3 left 1092; +6 from `test_downmix.py`, +1
  from the manifest height-coefficient bound test).
- `cd apps/web && npm test` → 249 passed; `npm run build` → clean.
- `npm run build:wasm` → byte-identical artifact (§3).
- Measurement kit → §2.

One combined-suite run out of three showed two unrelated API failures
(`test_web_imports.py::test_mastering_reference_upload_runs_and_rejects_client_path`,
`test_web_jobs.py::test_realtime_job_completes_and_downloads`); both pass in
isolation, both pass in the api suite alone, and both passed on the two repeat
combined runs. Flaky, not caused here — but worth knowing they can flake.

## 6. Not done: the A/B listening note

The plan asks for a listening pass on one cymbal-heavy track's stereo downmix, old
vs new. **I cannot listen**, so it is outstanding. What is objectively settled: the
content is present now where it was absent (§2a's zero-ripple columns), and the
level it arrives at is a documented −3 dB per fold rather than an accident.

What a listener should check, in order: whether cymbals/air in the stereo downmix
now sit *too* forward on the height-forward presets (`wide`, `immersive`, where §2b
says the most energy was being dropped); whether `k_h = 0.7071` or something nearer
0.5 balances the downmix against the 7.1.4 render on the same track; and whether the
Crowd stem — the one still losing 1.11 dB on `balanced`, because it routes to the
back heights — sounds thin in the fold.
