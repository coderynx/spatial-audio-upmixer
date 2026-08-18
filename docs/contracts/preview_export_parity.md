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

## 5. Where parity can still break

This section used to be an append-only ledger of every discrepancy found
between two hand-written implementations of the same DSP. That job ended with
the port: the algorithms are one implementation now, and D4/D9/D14/D18/D24/
D25/D26 all closed by deleting a copy rather than by fixing one.

What the port did not remove is the *plumbing* around the shared core, and
that is where every genuine parity gap found since has been. Four seams, each
with the entry that proved it:

| Seam | What can differ | Proved by |
|---|---|---|
| **Parameters** | Both sides run the same function on different inputs. The preview reads the project through `masteringProfiles.ts`/`engineParams.ts`, the export through `UpmixConfig`; a field one resolves and the other forwards by name is a real divergence with identical DSP. | D30 |
| **Build provenance** | `apps/web/public/wasm/upmixer_dsp.wasm` is a committed binary. Shared source does not mean shared build — the preview runs whatever was last compiled. | D33 |
| **Re-implementation drift** | DSP creeping back into TypeScript because a value is wanted before the engine can give it. | D34, and D3 still open |
| **Unshared wrapper stages** | Anything one side wraps around the core call — the export's chain assembly, the preview's downmix matrix and collapse-mode switching. | D32 |

**Admission rule.** A row belongs here only if the preview's output can differ
from the export's. A bug inside the shared core hits both sides equally and is
not a parity finding — it belongs in the phase report and the commit, not in
this table. D35–D40 are kept in the index below because code and reports cite
their numbers, but they are of that second kind.

### Index

Full text for D1–D22 is in git at `6a1f1ba^`; for D23 onward, at the commit
that closed each. Kept as an index because production code cites these
numbers.

| # | Finding | Status |
|---|---|---|
| D1 | `spatial_audio_engine.md` §6 claimed a cross-engine acceptance test that did not exist. | Fixed |
| D2 | Shared constants hand-duplicated with no automated cross-check. | Superseded by §2 (core serves them) |
| D3 | `estimateRouteScale` approximates `route_scale` from the routing table, not decoded buffers, and sums the whole route regardless of layout. | **Open** — see §3 |
| D4 | Preview loudness omitted K-weighting and gating and read excerpts, not the programme. | Fixed by the port |
| D5 | Bass mono-maker was missing from the preview. | Fixed by the port |
| D6 | Center/back-fold coefficient: exact `1/√2` on the backend, truncated `0.7071` on the web. | Fixed — `itu_center_coeff` split from `surround_downmix_coeff` |
| D7 | No cross-engine golden render diff existed. | Obsolete — the port removed the second engine |
| D8 | `node-web-audio-api`'s compressor reported positive reduction, turning into amplification. | Obsolete with the harness |
| D9 | Biquad realizations of the mono-maker and bass bands flipped their net effect versus zero-phase `sosfiltfilt`. | Fixed by the port |
| D10 | Golden diff covered only EQ/comp/bass. | Obsolete with D7 |
| D11 | Preview added LFE after voicing; the backend adds it before, and the BS.775 downmix must exclude it. | Fixed — `stream::output` joins LFE pre-voicing |
| D12 | Preview implemented neither reference-match EQ nor true-peak gain reduction. | Fixed by the port |
| D13 | The recompute signature hashed live preview knobs, so a slider drag forced a full reference-match recompute inline on the request thread. | Fixed |
| D14 | The look-ahead limiter existed only on the native path; collapse paths ran the old tanh saturator. | Fixed by the port |
| D15 | Transaural (crosstalk-cancelled speaker) delivery added. | Shipped — see `standards/transaural_speakers.md` |
| D16 | Constants hand-mirrored in `masteringProfiles.ts`. | Fixed — core serves them (§2) |
| D17 | Filter-asset filename maps hand-mirrored in `masteringProfiles.ts`. | Fixed — served with the rest (§2) |
| D18 | The true-peak kernel existed as two hand-synced copies. | Fixed by the port |
| D19 | `routing_for_scene` and its web mirror ranked nearest-3 speakers without ±180° wraparound, collapsing routing for stems near true back. | Fixed; the mirror later deleted (D34) |
| D20 | Transaural review against Choueiri: frequency-flat contralateral attenuation, hand-tuned regularization, and a third XTC defect. | Fixed |
| D21 | `match_reference` review: log-smoothing bin width, and LFE handling — LFE takes the level gain and skips the spectral curve. | Fixed |
| D22 | Per-stem LFE routing: implicit send amounts and explicit-empty zone routes that suppressed the fallback. | Fixed |
| D23 | The preview's `AudioContext` ran at the device rate while every shipped FIR is designed at 48 kHz. | Fixed — context pinned to 48 kHz |
| D24 | `dsp_master_bed` skipped LFE entirely for reference matching. | Fixed |
| D25 | `StreamingConvolver` re-transformed its kernel every block; the order-3 decode starved the audio thread. | Fixed — uniform-partitioned overlap-save |
| D26 | The mono-maker's zero-phase pass filtered ~9,700 samples of context per quantum. | Fixed — `stream::band::RollingBand` |
| D27 | Loudness correction measured in one blocking call inside the render callback (~57 s of frozen audio thread). | Fixed — sliced forked engine (P3) |
| D28 | The whole-programme measurement advanced only from a resumed context and reported no progress, so the calibration UI could hang. | Fixed — resume on init, two-stage measurement (P3) |
| D29 | A calibration measured whatever was in the worklet when the message landed, not what the caller had just set. | Fixed — parameter flush ordering in `DspEngineClient` |
| D30 | The preview forwarded the mastering *profile name* for bass and compression, so per-field pot overrides reached the export only. | Fixed — resolved blocks in `MasterMix`; pinned by `engineParams.test.ts` |
| D31 | Mid-bass decorrelation's zero-phase split truncated at the unifier's horizon, making output depend on block size. | Fixed — own 200 ms horizon; `stream_equivalence.rs` |
| D32 | `StemRebalancer` soft-clipped boosts above +3 dB on the export path only, while the preview applied pure linear gain. | Fixed — tanh stage deleted, both sides linear |
| D33 | The committed `.wasm` was two commits stale, so the preview ran an engine without mid-bass decorrelation. | Fixed — rebuilt; budget regained via `RollingBand` |
| D34 | `spatial.ts` carried a hand-port of the panning law. | Fixed — deleted; routing reaches the preview only as core-computed maps |
| D35 | The transient duck's per-sample detector put two `bench:engine` cases over budget at full depth. | Fixed before wiring — sub-threshold case taken before the divide |
| D36 | The multiband duck ducked cymbals *unevenly* rather than more; validated only on narrowband synthetics. | **Reverted** (`git revert 42e797f`) — `phase13_report.md` §9 |
| D37 | Haze/Elevation read the raw decoded stem, so routing-stage gain never showed; the live ducker gain would have flashed a horizon early. | Fixed — per-block duck trace read at the emit position |
| D38 | At depth exactly 1.0 a saturating onset landed on gain 0.0 — the band annihilated, not ducked. | Fixed — `DUCK_MIN_GAIN` floors at −20 dB |
| D39 | The duck's detector window sat inside ordinary crest variation, so sustained material ducked as hard as the onsets the stage exists to separate. | Fixed — threshold 2.5, span to 4.0 |
| D40 | Every duck fixture was too weak to reach the detector, so D39 was invisible to 188 passing tests. | Fixed — `hit_train_over_bed` and siblings rebuilt |
