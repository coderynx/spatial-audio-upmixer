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
| Stem → speaker bed | `stream::routing`, `routing::{sends,decorrelate,transient}` | `separation/stem_router.py` | `stream::routing` |
| Scene position → routing | `separation/stem_panner.py` | `apps/api/.../routing.py` | served with the project |
| Reference match | `match_reference::{spectrum,curve}` | `mastering/match_reference/` | `stream::master` |
| Spectral EQ | `mastering::eq` | `mastering/eq.py` | `stream::master` |
| Bus compression | `mastering::compressor` | `mastering/compressor.py` | `stream::master` |
| Bass management | `mastering::bass` | `mastering/bass.py` | `stream::master` |
| BS.1770 loudness / true peak | `loudness` | `loudness.py` | `stream::engine::measure` |
| Look-ahead limiter | `mastering::limiter` | `mastering/limiter.py` | `stream::master` |
| Ambisonic encode / HOA decode | `spatial::ambisonics` | `binaural/renderer.py` | `stream::output` |
| Voicing chain | `spatial::voicing` | `binaural/voicing.py` | `stream::output` |
| Crosstalk (transaural) | `spatial::xtc` | `crosstalk/renderer.py` | `stream::output` |
| BS.775 stereo downmix | `spatial::downmix` | `utils.py` | `stream::output` |

Placement panning is the one stage with no Rust half, and needs none: it turns
a placement into a gain map once, per stem, before any audio is touched. Both
sides consume the same map — the export from `preset_routing`, the preview from
the project payload — so the panner is shared by *being* the only one, not by
being ported. Nothing in `packages/dsp` or the worklet knows what a placement
is (ledger D34).

The stem → bed row's LFE bus is one lowpass design in
`kernels::butter::linkwitz_riley_lowpass_sos`, reached as `upmixer_dsp.lfe_lowpass`
on the export side and as `stream::routing::LfeBus` in the preview (see
`docs/standards/spatial_layouts_bs775_bs2051.md` § "LFE lowpass"). Its order is
the one LFE parameter the browser does *not* receive: `lfe_cutoff_hz` is served,
while `lfe_filter_order` is hardcoded to `4` on both sides
(`config.py`, `engineParams.ts`). Nothing user-facing writes it — no CLI flag, no
manifest key — so the pair cannot drift in the field, but change one and change
the other.

The downmix row is the one entry whose *matrix* is not shared code: the export
calls the kernel with the whole bed, while the preview mixes per speaker from a
per-channel gain pair built in `engineParams.ts::downmixGains` out of the
served `surround_downmix_coeff` / `height_downmix_coeff` / `itu_center_coeff`.
The coefficients are shared, the two-line matrix is not — change one and change
the other in the same commit (the height fold, phase 4, did).

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

Two things about that order are load-bearing rather than conventional, and
neither is obvious from the code:

- **Bass must stay after reference matching.**
  `mastering/match_reference/spectrum.py` compares a BS.1770-weighted *power*
  sum against the reference, so correlated bass spread across N channels reads
  as a ~10·log₁₀(N) dB low-frequency deficit — about 8.5 dB on a 7.1.4 floor
  bed. Unifying first would make the matcher lean on its ±2 dB sub-bass clamp
  on every render. Do not reorder these as an optimization.
- **Bass never has to compensate for what runs ahead of it.** EQ and reference
  matching each apply *one shared curve* to every non-LFE channel, and the bus
  compressor applies *one linked gain*. Identical LTI filtering and identical
  time-varying gain both commute with the LF sum — `H{Σ lowᵢ} = Σ H{lowᵢ}`,
  `g(t)·Σ lowᵢ = Σ g(t)·lowᵢ` — so nothing upstream can decorrelate the bed,
  and the Σa = 1 invariant holds whatever curve ran. The reference matcher's
  own docstring gives the reason the curve is shared: per-channel curves would
  desynchronize the inter-channel phase that BS.775 fold-down and transaural
  crosstalk cancellation depend on. `mastering::bass`'s
  `unification_commutes_with_a_shared_upstream_gain` pins this.

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

The height send's elevation EQ is served in four parts —
`height_low_rolloff_hz` / `height_low_rolloff_gain` /
`height_crossover_hz` / `height_high_shelf_gain` — plus the directional band
added in phase 7, `height_directional_band_hz` /
`height_directional_band_gain`. Its Q is not served: `routing::sends`'s
`DIRECTIONAL_BAND_Q` is structural, one design shared by the offline send,
the streaming send, and the STFT height mask (which reads the whole chain's
magnitude on the bin grid through `upmixer_dsp.elevation_response` rather than
approximating any of it — see `docs/plans/mixing/phase7_mask_parity_report.md`).
A band gain of exactly 1.0 skips the section on both sides, so the default
voicing is bit-identical to the pre-band one.

Phase 11 added one more send scalar, `stem_transient_duck`: the depth of the
transient/sustain split that both diffuse sends run on their input, before
the filters and the velvet line. Its time constants and ratio floor are not
served — `routing::transient`'s `DUCK_ATTACK_MS` / `DUCK_RELEASE_MS` /
`DUCK_REFERENCE_MS` / `DUCK_THRESHOLD_RATIO` / `DUCK_FULL_RATIO` are
structural, one detector shared by `transient_duck` offline and
`StemRouteState`'s ducker in the preview. A depth of exactly 0.0, which is
the default, returns the send input untouched on both sides.

Phase 13 split that detector across three bands without adding a knob:
`MultibandDucker` runs one `TransientDucker` per band over a Linkwitz-Riley
crossover at `DUCK_BAND_LOW_HZ` / `DUCK_BAND_HIGH_HZ`, both structural like
the time constants. `depth` remains the only value on the wire, and the
bands are the low-passes plus their subtractive complements, so at unity gain
they sum back to the input and depth 0.0 stays the untouched path on both
sides.

One ducker serves both send pairs in the streaming path where the offline
path calls `transient_duck` once per pair. That is not a divergence: the
detector's state depends only on the stem's input, so a single trajectory is
what each of the offline calls independently reproduces —
`stream::routing`'s `ducked_sends_match_the_offline_duck_then_shape_order`
pins it against the offline order, blocked ragged.

Constants that live in Rust are the ones that were already duplicated and are
structural rather than tunable: the BS.1770 true-peak FIR, the ACN/N3D
normalization, the filter-design internals, and the surround/height
decorrelator tap sets (`routing::decorrelate`'s `VELVET_*`). The decorrelator
is not a tunable: its two seeds *are* the filters, and the fold-down property
both zone pairs are built on holds only while both sides come from the same
draw — a wire round-trip could only introduce a value that breaks it.
`packages/core` reads the same constants back through the PyO3 binding
(`upmixer.utils.SURROUND_VELVET_SEED` / `HEIGHT_VELVET_SEED`), so neither
side has a copy.

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
| P1 | **Live-parameter latency.** The worklet renders ahead of the playhead, so a control change lands after the render horizon rather than instantly. The horizon is whichever zero-phase stage is active and reaches furthest: the LF unifier's 100 ms, or mid-bass decorrelation's 300 ms (its 4th-order 100-300 Hz band-pass needs the longer context — see `spatial_layouts_bs775_bs2051.md`). | ~100 ms; ~300 ms with `mastering_bass_decorrelate` above 0 |
| P2 | **Seek warm-up.** A seek renders a discarded run-up so the filter states settle; without it the Haas-delayed sends would drop out and the compressor would re-attack. Inside the run-up the states are still converging. | 500 ms run-up; lands within 1e-6 of a straight play-through |
| P3 | **Correction latency, in two stages.** The loudness/true-peak correction is a real BS.1770 measurement. A fast pass over a handful of excerpts lands first and is what the "calibrating loudness" UI waits for; an exact pass over the whole programme then keeps running in the background and refines the gain once it lands. Both are advanced in slices from the render callback (`stream::measure`), and the transport stays gated until the fast pass lands, so the preview never plays uncorrected; between the two, it plays on the fast pass's gain. Whatever the pass is meant to measure — parameter block and filter set both — has to reach the worklet before the `measure` message does, since the pass forks the engine as it stands (D29). | Fast pass: a few seconds, advances while paused or playing. Exact pass: ~2-3 min for an eight-minute track, only advances while paused (§4) |

Switching speaker layout is deliberately **not** a fourth entry. Selecting a
different layout in the tracks panel rebuilds the preview from scratch — new
`AudioContext`, new worklet node, stems re-decoded, loudness re-measured —
rather than reaching the engine as a parameter change. An `AudioWorkletNode`'s
`outputChannelCount` is fixed at construction, so a native-output layout
change cannot reuse the node; and because the rebuild is total, there is no
window in which the preview renders one layout's mix through another's
topology. The cost is latency on a user action, not a parity gap.

Two former Tier-3 gaps are **closed**: the preview's loudness is now the real
BS.1770 measurement over the whole render rather than an excerpt-sampled
estimate (ledger D4), and its true peak uses the standard's own kernel rather
than a 32-tap approximation.

One remains open: `estimateRouteScale`
(`apps/web/src/features/projects/masteringProfiles.ts`) still approximates
`StemRouter.route`'s normalization from the routing table rather than the
decoded stems (ledger D3). The core could compute it exactly — it owns the
stems — but only on a debounce, since it needs a full pass per routing change.
Since phase 9 that normalization is loudness-domain (BS.1770 channel weights
over K-weighted, gated per-channel power) rather than raw energy; the estimate
carries the channel weights, which it can evaluate from the table alone, but
not the K-weighting, which needs the buffers.

## 4. Realtime budget

The preview has one constraint the export does not: the worklet renders on the
audio thread, so a 128-frame quantum has **2.67 ms** at 48 kHz. Overrunning it
does not degrade gracefully — this node is the *source*, so a starved callback
emits silence. Nothing in §1–§3 can detect that: the samples are correct, they
just arrive too late to be heard.

`apps/web/scripts/bench-preview-engine.mjs` (`npm run bench:engine`) renders the
worst case we ship — full 7.1.4 bed, nine stems, order-3 decode, whole
mastering chain — one engine per process, and fails the build over budget.
Stages that ship default-off are benched *on* (`decorrelate: 1`,
`stem_transient_duck: 1`): the budget question is what the stage costs when a
user reaches for it, not what the default costs:

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
| D3 | `estimateRouteScale` approximates `route_scale` from the routing table, not decoded-buffer energy. It also sums every channel in the route regardless of the layout, so a route wider than the layout reads as a level error rather than an approximation. | Open — see §3. Neutralized for `stereo` layouts, where the API stores each layout's routing already folded to FL/FR (`_normalize_layout_mix`, applied per layout block now that a track carries one mix per layout), and a layout added to a track has its routing rebuilt for that layout's own channel set rather than inherited wider. Narrowed by phase 9: the export scalar is now BS.1770-weighted (K-weighted, gated per-channel power × channel weight), and the estimate applies the same +1.5 dB side-surround weight — the residual gap is the K-weighting of the send chains, worth up to ~3.2 LU on a fully surround-routed stem (`phase9_report.md` §2). |
| D4 | The preview's loudness omitted K-weighting and gating and read a few excerpts, not the whole programme. | Fixed by the port: `stream::engine::measure` runs the real BS.1770 measurement over the whole render. |
| D9 | Biquad realizations of the mono-maker and bass bands flipped their net effect versus the backend's zero-phase `sosfiltfilt`. | Fixed by the port: there is one implementation, and the mono-maker's zero-phase pass survives streaming via a bounded horizon. |
| D14 | The look-ahead limiter existed only on the native path, with the collapse paths on the old tanh saturator. | Fixed by the port; the split now follows the export exactly — limiter on native, soft limit on the collapse paths. |
| D18 | The true-peak kernel existed as two hand-synced copies. | Fixed by the port: one table, in the core. |
| D23 | The preview's `AudioContext` ran at the device rate while every shipped FIR is designed at 48 kHz, so the taps were reinterpreted at whatever rate the OS gave. | Fixed: the context is pinned to 48 kHz. |
| D25 | `StreamingConvolver` re-transformed its kernel on every block, so the order-3 decode alone ran at 1.4x realtime and the audio thread starved — correct samples, delivered too late, heard as silence. | Fixed: uniform-partitioned overlap-save, kernel transformed once. Guarded by the §4 budget. |
| D26 | The mono-maker's zero-phase pass filtered ~9,700 samples of context to emit each 128-frame quantum, ~75x redundant. | Fixed: it advanced in 512-frame strides, and now runs on the `stream::band::RollingBand` D33 introduced, which drops the redundancy to the backward pass's warm-up alone and spreads that across renders. |
| D27 | The loudness correction was measured in a single blocking call inside the render callback — ~57 s of frozen audio thread for an eight-minute track, on load and on every output-mode switch. | Fixed: `stream::measure` advances a forked engine in slices, with streaming BS.1770 meters pinned bit-identical to the offline ones. The correction now arrives late (P3) instead of stopping the audio. |
| D24 | `dsp_master_bed` skipped LFE entirely for reference matching; LFE should take the level gain and skip only the spectral curve (D21). | Fixed. The streaming engine was already correct. |
| D29 | A calibration measured whatever was in the worklet's engine when the `measure` message landed, which was not what the caller had just set: `updateParams` coalesces to one post per animation frame while every other message posts immediately, and a profile switch fired its filter-set fetch *after* asking for the measurement. So the monitoring level a profile settled on depended on which profile preceded it and on whether a fetch won the race — different on every switch and every reload. | Fixed: `DspEngineClient` flushes the pending parameter block ahead of any other message, `retuneVoicing`/`retuneCrosstalkVoicing` load the profile's filter set before recalibrating, and a switch back to a mode whose measurement an in-flight pass is about to overwrite re-measures instead of claiming it is calibrated. |
| D30 | The preview forwarded only the mastering *profile name* for bass and compression (`audioEngine.ts` read `mastering.bass.profile` and `engineParams.ts` re-read the preset from the served constants), so the per-field pot overrides the UI already exposed never reached the worklet. Moving any bass or compressor pot changed the export while the preview kept playing the bare preset. | Fixed: `resolveBassParams`/`resolveCompParams` merge the profile with the project's overrides in `masteringProfiles.ts`, and `MasterMix` now carries resolved blocks rather than names. `engineParams.test.ts::forwards per-field overrides, not just the profile preset` pins it. |
| D31 | Mid-bass decorrelation's zero-phase band split truncated at the unifier's 100 ms horizon, making the preview's output depend on render block size at ~1e-8. | Fixed before shipping: the stage carries its own horizon, sized from the band-pass's measured impulse response — 300 ms while both passes were truncated, 200 ms since D33 made the forward pass exact (measured 5e-12 against the offline pass, where 100 ms gives 1.3e-6). Covered by `stream_equivalence.rs`. |
| D32 | Per-stem rebalance gain was not the same operation on both sides: the export path's `StemRebalancer` applied `tanh(x/0.95)*0.95` to the whole stem whenever a boost exceeded +3 dB, while the preview (`stream::engine`) applied `rebalance_db` as pure smoothed linear gain. Any fader past +3 dB in the mixer previewed clean and exported with odd-harmonic distortion (−48.8 dB THD on a −20 dBFS sine at +6 dB, −10.9 dB at +12 dB on a hot stem). | Fixed by deleting the tanh stage: both sides are now linear gain, and overload protection stays where it already was, on the mastering chain's look-ahead true-peak limiter over the routed bed. `test_stem_rebalance.py::test_large_boost_is_exactly_linear` / `::test_large_boost_thd_at_numerical_floor` pin it. |
| D33 | The committed `apps/web/public/wasm/upmixer_dsp.wasm` was last rebuilt at `8da41d5`, two commits before `4548970` added mid-bass decorrelation — so the preview has been running an engine without that stage while the export applies it, the §1 build-provenance risk realized. Rebuilding the artifact also re-opens the §4 budget: with `decorrelate: 1` the current engine benches mean 0.72x / p99 2.6x / worst 2.8x of the deadline against budgets of 0.4x / 1.0x / 1.5x. | Fixed. The artifact was rebuilt in the phase 3 commit; the budget overrun that exposed is closed by `stream::band::RollingBand`, which gives both zero-phase band splits the D25/D26 treatment. The forward pass now carries its state, so each sample is filtered once instead of re-filtered per block, and the anticausal backward pass — the one part that genuinely needs a warm-up — is computed a chunk at a time and sliced across the renders that consume the previous chunk, so no quantum pays for a whole warm-up. With `decorrelate: 1` the same worst case benches **mean 0.28x / p99 0.69x / worst 1.20x** against 0.4x / 1.0x / 1.5x; every `bench:engine` case passes, including the two measurement cases that were over p99 with the stage switched off. |
| D34 | `apps/web/src/lib/spatial.ts` carried `routingFromAzimuthElevation`, a hand-port of `placement_route` for scene-positioned stems with no resolved routing yet — a second implementation of a panning law, in the layer that is meant to hold no DSP. It tracked the raised-cosine panner; the moment phase 10 replaced that panner it would have previewed a placement the export cannot produce. | Fixed by deleting it, along with the `speakerAzimuthElevation`/`positionToAzimuthElevation` helpers that existed only to feed it. Routing reaches the preview only as maps the core computed (`routing_for_scene` → project payload); a stem with none resolved yet gets an empty map, as one with no scene position already did. |
| D28 | The whole-programme measurement D27 introduced (§ P3) advanced only while paused and only from a `resume()`d `AudioContext`; a fresh context starts suspended and the worklet never registered its own progress callback, so the "calibrating loudness" UI could hang indefinitely with no feedback, and at best took minutes on an eight-minute track. | Fixed: the context resumes on init (with a pointer-gesture fallback for autoplay policy), the worklet's progress reaches the UI, and measurement runs in two stages — a fast excerpt pass clears the UI in seconds, then the exact whole-programme pass keeps refining the gain in the background (§ P3). |
| D35 | Phase 11's transient duck reaches the audio thread as a per-sample detector on every stem's send input, and its first cut put two `bench:engine` cases over budget at full depth — `measuring (exact, paused)` at p99 1.00x and `measuring (fast excerpt, playing)` at p99 1.77x against 1.0x/1.5x. The cost was a division on the per-sample dependency chain, evaluated whether or not the sample was an onset. | Fixed before wiring, per D33's lesson. The sub-threshold case is taken before the divide, which is algebraically the same score and skips it for the overwhelming majority of samples; the send buffers also reserve rather than growing. Every case is back inside budget at full depth (binaural mean 0.31x / p99 0.87x / worst 1.37x), and at the shipped default of 0.0 the stage is skipped entirely and the numbers match the phase 8 baseline. Benched on rather than off — see §4. |
| D36 | Phase 13 replaced the duck's single detector with three behind a Linkwitz-Riley crossover. The budget question was measured and answered (2-3% of the audio-thread mean, no case changing verdict), but the **audible** question was not: on real cymbal stems at depth 1.0 the split moves the low→high spectral tilt 23.6 dB on a crash and 11.2 dB on a ride, against broadband's 10.0 and 3.6 — it ducks unevenly rather than more, and a cymbal's per-band decay rates make three detectors diverge through the tail. Reported by ear within hours of shipping. | **Reverted** (`git revert 42e797f`); `routing::transient` is the phase 11 broadband ducker and both bindings are rebuilt. The lesson is a corpus one: every synthetic case used to validate the split was narrowband (continuous band-limited noise, a steady 9 kHz sine, band-limited bursts), and none could produce the per-band envelope divergence that is the whole failure mode. A multiband stage cannot be validated on signals whose bands carry the same envelope. See `docs/plans/mixing/phase13_report.md` §9. |
