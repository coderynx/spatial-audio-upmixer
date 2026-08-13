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

**The preview is first-class, not a rough approximation to be second-guessed
against the export.** A user judges whether an upmix is good — balance,
spatial placement, loudness, tone — by listening to the preview. That
judgment is only trustworthy if the preview is provably close to what export
will actually deliver. Treat preview/export divergence as a **bug**, not as
an acceptable cost of the preview being "just a preview." Where a difference
is unavoidable — the preview must respond to a control while the export
renders offline — the allowed gap is bounded and stated explicitly below
(§3), not left implicit.

The binaural geometry/ambisonic/decode-filter/voicing specification lives in
[`../standards/spatial_audio_engine.md`](../standards/spatial_audio_engine.md);
the transaural one in
[`../standards/transaural_speakers.md`](../standards/transaural_speakers.md).
Both are cross-referenced, not repeated, below. Both derive numeric
requirements from the industry standards in `docs/standards/`:
`loudness_dsp_bs1770.md` (BS.1770-5 loudness/true-peak),
`spatial_layouts_bs775_bs2051.md` (BS.775/BS.2051 layouts, LFE, downmix),
`adm_metadata_bs2076.md` and `dolby_atmos_profile.md` (ADM-BWF delivery).
Where a contracted constant exists because a standard requires it (e.g. LFE
−10 dB per BS.775-4 Annex 7, K-weighting coefficients per BS.1770-4 Annex 1),
this document cites that standard, not just the code.

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

A `stereo` layout (`FORMAT_MAP["stereo"]`, ITU-R BS.2051 System A) has no
collapse stage at all: the bed *is* two channels, so the preview stays in
native mode — limiter on, `soft_limit_threshold` 0 — and the export writes the
same FL/FR bed. The one thing that has to match is the routing table, which is
why the API stores a stereo project's `mixing.stem_routing` already folded to
FL/FR (see `docs/project_manifest_parity.md`): both sides then normalize over
the same channel set.

**Processing order is contracted** and lives in one place — reference match →
EQ → compression → bass → BS.1770 loudness → limiter *last*. Soft limiting
after loudness rather than before is deliberate; see
`packages/core/src/mastering/chain.py`'s module docstring.

The one thing that can break identity is **build provenance**: the browser
loads a committed `apps/web/public/wasm/upmixer_dsp.wasm`, not a wasm built on
install, so it can fall behind the installed `upmixer_dsp` wheel. Both bindings
export `dsp_core_version`, and the worklet reads it at startup
(`apps/web/public/dsp.worklet.js`) — that string is what to check when a
build is suspect. **Rebuild the artifact (`npm run build:wasm`) after any
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

**Changing a served constant** means editing it in its core module — the web
has no second copy to keep in sync, only a test-only mock
(`apps/web/src/features/projects/engineConstants.fixture.ts`) that stands in
for the real `GET /api/v1/configuration` response in web tests and needs no
ceremony beyond staying shaped like that response.

## 3. What can still differ

Three behaviours are genuinely different by nature, not by drift. Nothing else
should be.

| # | Difference | Bound |
|---|---|---|
| P1 | **Live-parameter latency.** The worklet renders ahead of the playhead, so a control change lands after the render horizon rather than instantly. | ~100 ms |
| P2 | **Seek warm-up.** A seek renders a discarded run-up so the filter states settle; without it the Haas-delayed sends would drop out and the compressor would re-attack. Inside the run-up the states are still converging. | 500 ms run-up; lands within 1e-6 of a straight play-through |
| P3 | **Correction latency, in two stages.** The loudness/true-peak correction is a real BS.1770 measurement. A fast pass over a handful of excerpts lands first and is what the "calibrating loudness" UI waits for; an exact pass over the whole programme then keeps running in the background and refines the gain once it lands. Both are advanced in slices from the render callback (`stream::measure`), and the transport stays gated until the fast pass lands, so the preview never plays uncorrected; between the two, it plays on the fast pass's gain. Whatever the pass is meant to measure — parameter block and filter set both — has to reach the worklet before the `measure` message does, since the pass forks the engine as it stands (D29). | Fast pass: a few seconds, advances while paused or playing. Exact pass: ~2-3 min for an eight-minute track, only advances while paused (§4) |

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

A mix edit (mute, solo, a fader, a mastering toggle) reaches the engine
through `update_params`/`dsp_engine_set_params`, not through P2's seek —
it only retunes the stages whose parameters actually moved, keeping the
playhead and both look-ahead queues, so it carries none of P2's 500 ms
run-up and is held to the same render budget above rather than a looser one
(`bench-preview-engine.mjs`'s `mixEditPlaying` case). P1's ~100 ms is what a
mix edit costs the *listener* — the look-ahead already rendered under the
old parameters has to drain first; this budget is what it costs the
*audio thread* to make the switch, which is what actually risks silence if
missed.

A loudness measurement (P3) is the one other thing competing for the quantum,
and its two stages are budgeted differently. The **exact** whole-programme
pass advances only while paused, where it may use most of the budget because
the render is not: playing already spends ~0.25x on average, and both the
render and the measurement have periodic look-ahead strides that overrun the
quantum when they land in the same callback. The **fast** excerpt pass also
advances while paused, in a much larger slice — deliberately past the
deadline, since the node's output is already silent while paused and an
overrun there costs a dropped silent callback, not a glitch — and, in a small
slice, while playing, so pressing play immediately after load doesn't stall
it; that shared-quantum case is not silence-safe, so its budget only tolerates
an occasional single-quantum click on the heaviest configuration, bounded to
the fast pass's few-second window (`bench-preview-engine.mjs`'s
`measuringFast` / `measuringPlaying` cases). A pass is kept across playback,
so it resumes rather than restarts.

**Run this after any change to `packages/dsp`'s streaming path.** A change that
is numerically perfect and 3× too slow is a change that ships silence.

## 5. Discrepancy ledger

Rows are kept after they are fixed so the history of what was found stays
visible. D1–D22 predate the Rust port and are preserved in git history at
`6a1f1ba^`; the entries below are the ones that still describe live behaviour
or that the port itself resolved.

| # | Discrepancy | Status |
|---|---|---|
| D3 | `estimateRouteScale` approximates `route_scale` from the routing table, not decoded-buffer energy. It also sums every channel in the route regardless of the layout, so a route wider than the layout reads as a level error rather than an approximation. | Open — see §3. Neutralized for `stereo` layouts, where the API stores the routing already folded to FL/FR. |
| D4 | The preview's loudness omitted K-weighting and gating and read a few excerpts, not the whole programme. | Fixed by the port: `stream::engine::measure` runs the real BS.1770 measurement over the whole render. |
| D9 | Biquad realizations of the mono-maker and bass bands flipped their net effect versus the backend's zero-phase `sosfiltfilt`. | Fixed by the port: there is one implementation, and the mono-maker's zero-phase pass survives streaming via a bounded horizon. |
| D14 | The look-ahead limiter existed only on the native path, with the collapse paths on the old tanh saturator. | Fixed by the port; the split now follows the export exactly — limiter on native, soft limit on the collapse paths. |
| D18 | The true-peak kernel existed as two hand-synced copies. | Fixed by the port: one table, in the core. |
| D23 | The preview's `AudioContext` ran at the device rate while every shipped FIR is designed at 48 kHz, so the taps were reinterpreted at whatever rate the OS gave. | Fixed: the context is pinned to 48 kHz. |
| D25 | `StreamingConvolver` re-transformed its kernel on every block, so the order-3 decode alone ran at 1.4x realtime and the audio thread starved — correct samples, delivered too late, heard as silence. | Fixed: uniform-partitioned overlap-save, kernel transformed once. Guarded by the §4 budget. |
| D26 | The mono-maker's zero-phase pass filtered ~9,700 samples of context to emit each 128-frame quantum, ~75x redundant. | Fixed: it advances in 512-frame strides, which is where the §4 mean and p99 both sit inside budget. |
| D27 | The loudness correction was measured in a single blocking call inside the render callback — ~57 s of frozen audio thread for an eight-minute track, on load and on every output-mode switch. | Fixed: `stream::measure` advances a forked engine in slices, with streaming BS.1770 meters pinned bit-identical to the offline ones. The correction now arrives late (P3) instead of stopping the audio. |
| D24 | `dsp_master_bed` skipped LFE entirely for reference matching; LFE should take the level gain and skip only the spectral curve (D21). | Fixed. The streaming engine was already correct. |
| D29 | A calibration measured whatever was in the worklet's engine when the `measure` message landed, which was not what the caller had just set: `updateParams` coalesces to one post per animation frame while every other message posts immediately, and a profile switch fired its filter-set fetch *after* asking for the measurement. So the monitoring level a profile settled on depended on which profile preceded it and on whether a fetch won the race — different on every switch and every reload. | Fixed: `DspEngineClient` flushes the pending parameter block ahead of any other message, `retuneVoicing`/`retuneCrosstalkVoicing` load the profile's filter set before recalibrating, and a switch back to a mode whose measurement an in-flight pass is about to overwrite re-measures instead of claiming it is calibrated. |
| D28 | The whole-programme measurement D27 introduced (§ P3) advanced only while paused and only from a `resume()`d `AudioContext`; a fresh context starts suspended and the worklet never registered its own progress callback, so the "calibrating loudness" UI could hang indefinitely with no feedback, and at best took minutes on an eight-minute track. | Fixed: the context resumes on init (with a pointer-gesture fallback for autoplay policy), the worklet's progress reaches the UI, and measurement runs in two stages — a fast excerpt pass clears the UI in seconds, then the exact whole-programme pass keeps refining the gain in the background (§ P3). |
