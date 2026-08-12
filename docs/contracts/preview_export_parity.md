# Preview ↔ Export Parity Contract

**Scope:** every DSP stage the browser preview and the export pipeline both
run — stem routing, the mastering chain, reference matching, and the
binaural/transaural/stereo collapse.

This document used to specify how two independent implementations of that DSP
were kept in agreement. There is only one implementation now: the Rust core in
[`packages/dsp`](../../packages/dsp/AGENTS.md), reached through PyO3 from
`packages/core` and through WebAssembly from the browser worklet. Most of what
this contract governed — matching constants, matching filter realizations,
matching stage order — is no longer possible to get wrong.

What remains is this file's subject: the things that *can* still differ — in
behaviour and in timing — and the checks that catch them.

Read [`README.md`](README.md) first for the change protocol. The binaural
geometry/ambisonic/decode-filter/voicing specification lives in
[`../standards/spatial_audio_engine.md`](../standards/spatial_audio_engine.md);
the transaural one in
[`../standards/transaural_speakers.md`](../standards/transaural_speakers.md).
Both are cross-referenced, not repeated, below.

---

## 1. Algorithm identity

Every stage below is one function, called from both sides:

| Stage | Core module | Export entry | Preview entry |
|---|---|---|---|
| Per-stem EQ | `spatial`/`kernels::fir_design` | `separation/stem_eq.py` | `stream::routing` |
| Stem → speaker bed | `stream::routing`, `routing::sends` | `separation/stem_router.py` | `stream::routing` |
| Scene position → routing | `routing::scene` | `apps/api/.../routing.py` | served with the project |
| Reference match | `match_reference::{spectrum,curve}` | `mastering/match_reference/` | `stream::master` |
| Spectral EQ | `mastering::eq` | `mastering/eq.py` | `stream::master` |
| Bus compression | `mastering::compressor` | `mastering/compressor.py` | `stream::master` |
| Bass control | `mastering::bass` | `mastering/bass.py` | `stream::master` |
| BS.1770 loudness / true peak | `loudness` | `loudness.py` | `stream::engine::measure` |
| Look-ahead limiter | `mastering::limiter` | `mastering/limiter.py` | `stream::master` |
| Ambisonic encode / HOA decode | `spatial::ambisonics` | `binaural/renderer.py` | `stream::output` |
| Voicing chain | `spatial::voicing` | `binaural/voicing.py` | `stream::output` |
| Crosstalk (transaural) | `spatial::xtc` | `crosstalk/renderer.py` | `stream::output` |
| BS.775 stereo downmix | `spatial::downmix` | `utils.py` | `stream::output` |

**Processing order is contracted** and lives in one place — reference match →
EQ → compression → bass → BS.1770 loudness → limiter *last*. Soft limiting
after loudness rather than before is deliberate; see
`packages/core/src/mastering/chain.py`'s module docstring.

The one thing that can break identity is **build provenance**: the browser
loads a committed `apps/web/public/wasm/upmixer_dsp.wasm`, not a wasm built on
install, so it can fall behind the installed `upmixer_dsp` wheel. Both bindings
export `dsp_core_version`, and the golden render (§5) fails if the two builds
disagree numerically. **Rebuild the artifact (`npm run build:wasm`) after any
change under `packages/dsp`.**

## 2. Constants

The tunable acoustic constants are owned by `packages/core/src/config.py` plus
the profile tables, and served to the browser by
`apps/api/src/features/system/service.py::engine_constants()` as the
`constants` block of `GET /api/v1/configuration`. The web normalizes them
through `resolveEngineConstants` and passes them into the core as parameters;
the core has no default of its own for any of them.

Served alongside the scalars: the FIR/filter asset name maps (`EQ_FIR_ASSETS`,
`STEM_EQ_FIR_ASSETS`, `DECODE_FILTER_SET`, `XTC_FILTER_SET`) and, since the
core performs the ambisonic encode, `speaker_directions` — read straight from
`binaural/geometry.py` so the browser never re-derives an angle.

Constants that live in Rust are the ones that were already duplicated and are
structural rather than tunable: the BS.1770 true-peak FIR, the ACN/N3D
normalization, and the filter-design internals.

**Changing a served constant** means editing it in its core module, then
updating `apps/web/src/features/projects/engineConstants.fixture.ts` (the
test-only copy that feeds the web suite) and re-running the golden render.

## 3. What can still differ

Three behaviours are genuinely different by nature, not by drift. Nothing else
should be.

| # | Difference | Bound |
|---|---|---|
| P1 | **Live-parameter latency.** The worklet renders ahead of the playhead, so a control change lands after the render horizon rather than instantly. | ~100 ms |
| P2 | **Seek warm-up.** A seek renders a discarded run-up so the filter states settle; without it the Haas-delayed sends would drop out and the compressor would re-attack. Inside the run-up the states are still converging. | 500 ms run-up; lands within 1e-6 of a straight play-through |
| P3 | **Correction staleness.** The loudness/true-peak correction is measured per output mode and profile. While a re-measure is in flight the previous gain holds. | One measurement pass |

Two former Tier-3 gaps are **closed**: the preview's loudness is now the real
BS.1770 measurement over the whole render rather than an excerpt-sampled
estimate (ledger D4), and its true peak uses the standard's own kernel rather
than a 32-tap approximation.

One remains open: `estimateRouteScale`
(`apps/web/src/features/projects/masteringProfiles.ts`) still approximates
`StemRouter.route`'s energy normalization from the routing table rather than
the decoded stems (ledger D3). The core could compute it exactly — it owns the
stems — but only on a debounce, since it needs a full pass per routing change.

## 4. Realtime budget

The preview has one constraint the export does not: the worklet renders on the
audio thread, so a 128-frame quantum has **2.67 ms** at 48 kHz. Overrunning it
does not degrade gracefully — this node is the *source*, so a starved callback
emits silence. Nothing in §1–§3 can detect that: the samples are correct, they
just arrive too late to be heard.

`apps/web/scripts/bench-preview-engine.mjs` (`npm run bench:engine`) renders the
worst case we ship — full 7.1.4 bed, nine stems, order-3 decode, whole
mastering chain — one engine per process, and fails the build over budget:

| Metric | Budget |
|---|---|
| Mean per quantum | ≤ 0.4 × deadline |
| p99 | ≤ 1.0 × deadline |
| Worst steady-state | ≤ 1.5 × deadline |

The first render of a play or seek fills both look-ahead queues from cold
(~30 ms); it is reported but not budgeted.

**Run this after any change to `packages/dsp`'s streaming path.** A change that
is numerically perfect and 3× too slow is a change that ships silence.

## 5. Golden render

`packages/core/tests/test_preview_export_golden.py` renders a fixed
deterministic bed through both builds and compares. It runs in the default
suite. Six tests: three Python pins (reproducibility) and three cross-build
diffs (bed, reference match, binaural collapse).

The browser side is `apps/web/scripts/render-preview-golden.mjs`, which loads
the shipped wasm directly — no Web Audio, no separate measurement code. Its
constants and filter assets come from `scripts/golden-inputs.py`, which reads
them from the modules that own them, so the two sides cannot render with
different inputs.

| Metric | Threshold |
|---|---|
| Per-channel RMS | ≤ 3 dB |
| Integrated LKFS | ≤ 1.0 LU |
| True peak | ≤ 1.0 dBTP |

These thresholds are inherited from when the two sides were different
implementations. They are now far looser than what actually holds — the two
builds agree to floating-point noise — and exist as a backstop against a stale
wasm rather than as a description of the expected difference. **A failure here
means the artifact is out of date, or a genuine bug landed in the core.**

Regenerate with `npm run golden:render` from `apps/web/`, then run the Python
test. The reference-match stage needs its gitignored fixture pair, produced by
`REGENERATE_GOLDEN=1 uv run pytest
packages/core/tests/test_preview_export_golden.py`; without it that stage is
skipped rather than silently compared against stale numbers.

## 6. Discrepancy ledger

Rows are kept after they are fixed so the history of what was found stays
visible. D1–D22 predate the Rust port and are preserved in git history at
`6a1f1ba^`; the entries below are the ones that still describe live behaviour
or that the port itself resolved.

| # | Discrepancy | Status |
|---|---|---|
| D3 | `estimateRouteScale` approximates `route_scale` from the routing table, not decoded-buffer energy. | Open — see §3. |
| D4 | The preview's loudness omitted K-weighting and gating and read a few excerpts, not the whole programme. | Fixed by the port: `stream::engine::measure` runs the real BS.1770 measurement over the whole render. |
| D9 | Biquad realizations of the mono-maker and bass bands flipped their net effect versus the backend's zero-phase `sosfiltfilt`. | Fixed by the port: there is one implementation, and the mono-maker's zero-phase pass survives streaming via a bounded horizon. |
| D14 | The look-ahead limiter existed only on the native path, with the collapse paths on the old tanh saturator. | Fixed by the port; the split now follows the export exactly — limiter on native, soft limit on the collapse paths. |
| D18 | The true-peak kernel existed as two hand-synced copies. | Fixed by the port: one table, in the core. |
| D23 | The preview's `AudioContext` ran at the device rate while every shipped FIR is designed at 48 kHz, so the taps were reinterpreted at whatever rate the OS gave. | Fixed: the context is pinned to 48 kHz. |
| D25 | `StreamingConvolver` re-transformed its kernel on every block, so the order-3 decode alone ran at 1.4x realtime and the audio thread starved — correct samples, delivered too late, heard as silence. | Fixed: uniform-partitioned overlap-save, kernel transformed once. Guarded by the §4 budget. |
| D26 | The mono-maker's zero-phase pass filtered ~9,700 samples of context to emit each 128-frame quantum, ~75x redundant. | Fixed: it advances in 512-frame strides, which is where the §4 mean and p99 both sit inside budget. |
| D24 | `dsp_master_bed` skipped LFE entirely for reference matching; LFE should take the level gain and skip only the spectral curve (D21). | Fixed — caught by the golden render's reference-match stage. The streaming engine was already correct. |
