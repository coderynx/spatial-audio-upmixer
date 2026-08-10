# Preview ↔ Export Parity Contract

**Scope:** every DSP stage the web preview re-implements from the core
export pipeline: stem routing, spatial bed construction, mastering chain,
and (for the binaural/transaural output modes) the Spatial Audio Engine. Read
[`README.md`](README.md) first for the change protocol this contract is
bound by. The binaural-specific geometry/ambisonic/decode-filter/voicing
contract lives in
[`../standards/spatial_audio_engine.md`](../standards/spatial_audio_engine.md);
the transaural (crosstalk-cancellation) speaker-geometry/XTC/voicing
contract lives in
[`../standards/transaural_speakers.md`](../standards/transaural_speakers.md).
Both are cross-referenced, not repeated, below.

---

## 1. Pipeline map — Python stage ↔ TypeScript stage

| Stage | Python (export) | TypeScript (preview) |
|---|---|---|
| Per-stem EQ (pre-routing) | `packages/core/src/separation/stem_eq.py::StemEQ` (asset names: `STEM_EQ_FIR_ASSETS`) | `EngineConstants.stemEqFirAssets` (served, see §4) + `buildFirEqNode`, wired in `audioEngine.ts`/`useStemPreview.ts` |
| Stem → speaker-bed routing | `packages/core/src/separation/stem_router.py::StemRouter.route` | `createStemSends` in `useStemPreview.ts`, using `channelGroupGain`/`buildSurroundSend`/`buildHeightSend`/`buildDiffuseSend` from `masteringProfiles.ts` |
| Scene position → speaker-bed routing (draggable stem azimuth/elevation, used when no explicit `stem_routing` is set) | `apps/api/src/features/projects/routing.py::routing_for_scene` (nearest-3-speaker, inverse-distance, constant-power) | `routingFromAzimuthElevation` in `apps/web/src/lib/spatial.ts` — see **Ledger D19** |
| Multichannel channel derivation (non-stem path) | `packages/core/src/upmix/multichannel.py::MultichannelUpmixer` | Not previewed — multichannel pass-through input has no stem-preview path |
| Ambisonic encode (order-3 ACN/N3D) | `packages/core/src/binaural/ambisonics.py::encode_gains` | `createPositionalEncoder` (wraps `AmbiMonoEncoder`/JSAmbisonics) + `ACN12_N3D_CORRECTION` in `previewGraph.ts`, called per speaker from `useStemPreview.ts` — see `spatial_audio_engine.md` §3 |
| Virtual-loudspeaker geometry | `packages/core/src/binaural/geometry.py` | `apps/web/src/lib/spatial.ts::speakerCoordinates` — see `spatial_audio_engine.md` §2 |
| HOA decode → binaural | `packages/core/src/binaural/decoder.py` | Per-ACN `ConvolverNode` bank in `buildBinauralGraph` (`previewGraph.ts`), called from `initialize()` in `useStemPreview.ts` — see `spatial_audio_engine.md` §4 and **Ledger D10** |
| Binaural voicing chain | `packages/core/src/binaural/voicing.py::apply_voicing` | `buildVoicingChain`/`applyVoicingParams` in `masteringProfiles.ts`, wired inside `buildBinauralGraph` (`previewGraph.ts`) — see `spatial_audio_engine.md` §5 |
| Crosstalk-cancellation (transaural) | `packages/core/src/crosstalk/renderer.py::render_crosstalk` (reuses `render_binaural` "flat" + `apply_xtc` + `apply_voicing`) | `buildCrosstalkGraph` (`previewGraph.ts`, reuses `buildBinauralGraph("flat")` + a 4-convolver 2x2 XTC matrix + `buildVoicingChain`), wired inside `initialize()` (`useStemPreview.ts`) — see `transaural_speakers.md` §1 |
| Reference match (spectral + RMS) | `packages/core/src/mastering/match_reference.py::ReferenceMatchProcessor` | Server-precomputed per-channel FIR bank + RMS gain (`ReferenceMatchProcessor.compute_channel_filters`, `apps/api/src/features/projects/worker_reference_match.py::WorkerManager.prepare_reference_match`), served as a project asset and convolved via `buildFirEqNode` inside `buildMasteringGraph` (`previewGraph.ts`), before the named-EQ stage — see **Ledger D12** |
| Spectral (mastering) EQ | `packages/core/src/mastering/eq.py::SpectralShaper` (asset names: `EQ_FIR_ASSETS`) | `EngineConstants.eqFirAssets` (served, see §4) + `buildFirEqNode` in `buildMasteringGraph` (same asset scheme as stem EQ, applied post-routing) |
| Bus compression | `packages/core/src/mastering/compressor.py::BusCompressor` | Linked `DynamicsCompressorNode` detector + polled `.reduction` in `buildMasteringTopology` (`useStemPreview.ts`) |
| Bass control | `packages/core/src/mastering/bass.py::BassController` | Bass shelves/exciter/mono-maker in `buildMasteringTopology` (**Ledger D5**) |
| BS.1770 loudness normalization | `packages/core/src/loudness.py::normalize_loudness` (bed) / `render_binaural_delivery`'s own pass (collapse) | `measureOutputLoudness`/`loudnessGainFor` (`useStemPreview.ts`) — approximate, see **Tier 3**; the collapse-stage pass is now golden-diff-covered, see **Ledger D10** |
| True-peak ceiling | `normalize_loudness`'s `max_tp_dbtp` gain reduction (`packages/core/src/loudness.py`) | Second gain reduction in `apply()` (`useStemPreview.ts`), driven by `measureBufferTruePeakDbtp` (`masteringProfiles.ts`) on the same `mergePointAnalyser` window `measureOutputLoudness` reads — approximate, see **Tier 3** and **Ledger D12** |
| Look-ahead limiter (last, bed-level) | `packages/core/src/mastering/limiter.py::LookAheadLimiter` (`MasteringChain`) | Native monitoring path: `"limiter-processor"` AudioWorklet (`apps/web/public/limiter.worklet.js`), replacing `nativeSoftLimitNode` in `initialize()` (`useStemPreview.ts`) — see **Ledger D14**. Binaural/stereo-downmix path keeps the plain tanh `softLimitNode`/`buildSoftLimitCurve`, unchanged, matching `render_binaural_delivery`'s own untouched `soft_limit` call (D14) |
| ITU-R BS.775 stereo downmix | `packages/core/src/utils.py::itu_downmix_stereo` | `STEREO_DOWNMIX_GAINS` + `applyOutputMode` (`useStemPreview.ts`) |

Both `UpmixPipeline` and `StemUpmixPipeline` share one `MasteringChain`
instance (`packages/core/src/mastering/chain.py`) and one `render_binaural_delivery`
call, so the export side of every row above is centralized in core; the
preview side is the parity-critical surface.

**Processing order is itself contracted** (Tier 1): reference match → EQ →
compression → bass control → BS.1770 loudness → soft-limit *last*. Soft
limiting after loudness (not before) is deliberate — see
`packages/core/src/mastering/chain.py`'s module docstring — and the preview graph
must build its nodes in the same order. Reference match is now implemented
in the preview (not ordering-only) — see **Ledger D12**.

---

## 2. Parity tiers

- **Tier 1 — bit-for-bit.** Scalar constants, tables, and file assets: both
  sides must carry the exact same numeric value or byte-identical file.
  Drift here is always a bug. The tunable numeric constants are single-sourced
  from core and served to the web (§4), so there is nothing to drift; file
  assets stay byte-identical by build provenance.
- **Tier 2 — parameter-level only.** The DSP *realization* may legitimately
  differ (SciPy `sosfilt` IIR vs. Web Audio `BiquadFilterNode`/`Convolver
  Node`/`DynamicsCompressorNode`) as long as every parameter feeding it is
  Tier 1. Applies to: bass shelves/exciter, height/surround send EQ,
  bus compressor, binaural voicing chain, mastering FIR EQ (the FIR itself
  is Tier 1 — see below).
- **Tier 3 — bounded-audible approximation.** The preview knowingly computes
  a different, cheaper algorithm than the export. Listed explicitly in §5
  with a numeric tolerance; anything not listed there must be Tier 1 or 2.

The mastering/stem EQ FIRs are a special case: the web does not
re-synthesize the filter from the profile curve, it fetches the *actual*
minimum-phase FIR `packages/core/src/mastering/eq.py::_build_fir` computed
(`scripts/build_eq_filters.py` calls that function directly and ships the
WAV under `apps/web/public/eq_fir/`), so the convolution itself is Tier 1, not
an approximation of the curve.

The reference-match FIR asset is the same special case, computed per-project
instead of built once at build time: `apps/api/src/features/projects/
worker_reference_match.py::WorkerManager.prepare_reference_match` calls
`ReferenceMatchProcessor.compute_channel_
filters` — the exact function `process()` uses internally — against a
server-rendered bed, and ships those real FIRs plus the real RMS gain to the
browser (`GET /api/v1/projects/{id}/reference-match/fir`). The convolution
and RMS gain are Tier 1 by asset identity; what *is* an approximation (Tier
3, see **Ledger D12**) is the bed the FIR was computed against — a canonical
server render of the project's default manifest, not the browser's live-
edited mix.

---

## 3. Canonical constants catalog

One row per constant shared between export and preview. The "TS" column
names the field of the web's `EngineConstants` object (`masteringProfiles.ts`)
the value lands in — served from core, not a hardcoded mirror (§4). The
symbol names below are historical (the former hardcoded constants); the
values and tiers are the contract.

### Channel-group gains, LFE, cutoffs — `packages/core/src/config.py::UpmixConfig`

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Center gain | 0.85 | `center_gain` | `CENTER_GAIN` | 1 |
| Surround gain | 0.6 | `surround_gain` | `SURROUND_GAIN` | 1 |
| Back gain | 0.55 | `back_gain` | `BACK_GAIN` | 1 |
| Height gain | 0.55 | `height_gain` | `HEIGHT_GAIN` | 1 |
| LFE gain (−10 dB, BS.775-4 Annex 7) | 0.31622776601683794 | `lfe_gain` | `LFE_GAIN` | 1 |
| LFE lowpass cutoff | 120 Hz | `lfe_cutoff_hz` | `LFE_LOWPASS_HZ` | 1 |
| Surround bass cutoff (pre-Haas highpass) | 250 Hz | `surround_bass_cutoff_hz` | `SURROUND_BASS_CUTOFF_HZ` | 2 |
| Height low-rolloff freq / gain | 150 Hz / 0.15 | `height_low_rolloff_hz`/`height_low_rolloff_gain` | `HEIGHT_LOW_ROLLOFF_HZ`/`HEIGHT_LOW_ROLLOFF_GAIN` | 2 |
| Height crossover / high-shelf gain | 3000 Hz / 1.5 | `height_crossover_hz`/`height_high_shelf_gain` | `HEIGHT_CROSSOVER_HZ`/`HEIGHT_HIGH_SHELF_GAIN` | 2 |
| Soft-limit threshold (binaural/stereo path only — see **Ledger D14**) | 0.95 | `peak_limit_threshold` | `SOFT_LIMIT_THRESHOLD` | 1 |
| Look-ahead limiter look-ahead window (native path — **Ledger D14**) | 5.0 ms | `limiter_lookahead_ms` | `LIMITER_LOOKAHEAD_MS` | 1 |
| Look-ahead limiter release time (native path — **Ledger D14**) | 50.0 ms | `limiter_release_ms` | `LIMITER_RELEASE_MS` | 1 |
| Loudness target | −18.0 LKFS | `loudness_target_lkfs` | *(not mirrored — see Tier 3 loudness note)* | 3 |
| Loudness max gain | 30.0 dB | `loudness_max_gain_db` | `LOUDNESS_MAX_GAIN_DB` | 1 |
| Surround downmix coefficient (configurable) | 0.7071 | `surround_downmix_coeff` | `SURROUND_DOWNMIX_COEFF` (`masteringProfiles.ts`) | 1 |
| Center/back-fold downmix coefficient (fixed) | 1/√2 ≈ 0.70710678 | `packages/core/src/utils.py::_ITU_C_COEFF` | `ITU_CENTER_COEFF` (`masteringProfiles.ts`) | 1 |

### Diffuse/Haas sends — `packages/core/src/separation/stem_router.py`, `packages/core/src/utils.py::diffuse_send`

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Diffuse send blend | 0.55 | `diffuse_send` default `blend` | `DIFFUSE_SEND_BLEND` | 2 |
| Surround Haas delay L / R | 31 ms / 37 ms | `route()` literals | `SURROUND_HAAS_MS` | 2 |
| Height Haas delay L / R | 23 ms / 29 ms | `route()` literals | `HEIGHT_HAAS_MS` | 2 |
| Per-stem route-energy normalization | `route_scale = sqrt(input_energy / routed_energy)` | `StemRouter.route` | `estimateRouteScale` (approximation) | 3 |

### Bus compressor profiles — `packages/core/src/mastering/compressor.py::COMP_PROFILES`

| Profile | threshold_db | ratio | attack_ms | release_ms | knee_db | makeup_db | Tier |
|---|---|---|---|---|---|---|---|
| `transparent` | −22.0 | 1.5 | 30.0 | 300.0 | 9.0 | 0.0 | 2 |
| `glue` | −18.0 | 2.0 | 20.0 | 200.0 | 6.0 | 0.0 | 2 |
| `warm` | −15.0 | 2.0 | 40.0 | 400.0 | 12.0 | 0.0 | 2 |

(TS mirror: `COMP_PROFILES` in `masteringProfiles.ts`.)

### Bass profiles — `packages/core/src/mastering/bass.py::BASS_PROFILES`

| Profile | sub_gain_db | mid_gain_db | mono_cutoff_hz | excite | lfe_gain_db | Tier |
|---|---|---|---|---|---|---|
| `boost` | 2.0 | 1.0 | null | false | 1.5 | 2 |
| `cut` | −2.5 | −1.5 | null | false | −1.0 | 2 |
| `mono` | 0.0 | 0.0 | 100.0 | false | 0.0 | 2 |
| `enhance` | 1.5 | 0.5 | 80.0 | true | 1.0 | 2 |

Other bass constants: sub cutoff 80 Hz (`SUB_CUTOFF_HZ`), mid cutoff 200 Hz
(`MID_CUTOFF_HZ`), exciter blend 0.15 / drive 3.0 (`EXCITE_BLEND`,
`EXCITE_DRIVE`), mono-maker stereo pairs FL/FR, SL/SR, BL/BR, TFL/TFR,
TBL/TBR (`STEREO_PAIRS` in `bass.py` / `MONO_MAKER_STEREO_PAIRS` in
`masteringProfiles.ts`). All Tier 2 — mono-maker is ported (Ledger D5,
`buildMasteringTopology`'s mono-maker block); its cross-channel coupling is
built from `BiquadFilterNode` lowpasses, an approximation of the backend's
`sosfiltfilt` zero-phase filtering for buffers over 15 samples, same
DSP-realization gap already accepted for the rest of this chain (§2 Tier 2).

### Loudness (BS.1770-5) — `packages/core/src/loudness.py`

| Constant | Value | Tier |
|---|---|---|
| K-weighting Stage 1 SOS (48 kHz) | `[1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585]` | 3 (not ported — preview loudness is approximate) |
| K-weighting Stage 2 SOS (48 kHz) | `[1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]` | 3 |
| Surround channel weight | 1.41 (+1.5 dB, BS.1770-5 Annex 3 Table 5) | 3 |
| LFE channel weight | 0.0 (excluded) | 3 |
| LKFS offset | −0.691 | 3 |
| Gating block / hop | 400 ms / 100 ms | 3 |
| Absolute / relative gate | −70 LKFS / −10 LU | 3 |
| True-peak oversample | 4× (≤48 kHz), 2× (96 kHz) | 3 — preview uses a 32-tap windowed-sinc 4× oversample (`measureBufferTruePeakDbtp`, `masteringProfiles.ts`), not the standard's exact kernel |

The preview's `measureOutputLoudness` (`useStemPreview.ts`) has no
K-weighting or gating, and measures a single ~1s window near playback start
rather than the whole track — see **Tier 3 tolerance** in §5. This is the
largest bounded gap in the contract; tightening it (full BS.1770 in-browser,
whole-file measurement) would move these rows to Tier 1/2. `apply()`'s
true-peak safety net (see §1's "True-peak ceiling" row and **Ledger D12**)
reads the same window, so it inherits this same bounded-window caveat — it
protects against the level the mastered signal reaches in that window, not
a whole-file peak.

### Reference match — `packages/core/src/mastering/match_reference.py`

| Constant | Value | Tier |
|---|---|---|
| FIR taps | 1023 | *(server-only — the FIR is shipped as a computed asset, see §2, not re-derived in TS)* |
| Welch FFT length | 8192 | *(server-only)* |
| Spectral breakpoints | 40, log-spaced 20 Hz–Nyquist | *(server-only)* |
| Spectral smoothing | 0.25 octave (Gaussian) | *(server-only)* |
| Normalization band | 80–8000 Hz | *(server-only)* |
| Sub-bass correction clamp | ±2.0 dB below 120 Hz | *(server-only)* |
| RMS gain clamp | ±6.0 dB | *(server-only)* |
| Default strength / max correction | 0.7 / 12.0 dB | *(server-only — live-editable via the manifest, see below)* |

These constants live only in `packages/core/src/mastering/match_reference.py` — the
web never re-derives the algorithm, only convolves with the FIR bytes the
server already computed (§2), so none of them are served as engine constants
(§4), the same rationale as the loudness K-weighting rows above. `strength`,
`spectrum`, and `rms` *are* read live from the manifest by the preview
(`ProjectDetailPage.tsx`'s `previewMastering`) — they gate/blend the
server-computed FIR and RMS gain without needing a recompute, since neither
the FIR shape nor the RMS gain value depends on them (`process()` builds the
FIR independent of `strength`, then blends wet/dry at apply time — the
preview does the same).

### Binaural — see `spatial_audio_engine.md` for the full geometry/SH/decode-filter/voicing table. Cross-cutting constant repeated here because it also gates delivery gain-staging:

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Binaural collapse loudness ceiling | 6.0 dB | `BINAURAL_LOUDNESS_MAX_GAIN_DB` (`packages/core/src/binaural/renderer.py`) | `BINAURAL_LOUDNESS_MAX_GAIN_DB` | 1 |

### Transaural — see `transaural_speakers.md` for the full speaker-geometry/XTC-regularization/voicing table. Cross-cutting constant repeated here because it also gates delivery gain-staging:

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Crosstalk collapse loudness ceiling | 6.0 dB | `CROSSTALK_LOUDNESS_MAX_GAIN_DB` (`packages/core/src/crosstalk/renderer.py`) | `CROSSTALK_LOUDNESS_MAX_GAIN_DB` | 1 |

### Channel layouts / formats — `packages/core/src/formats.py`

Out of scope for the served engine constants (§4): these already reach the
web only by runtime fetch. Listed here for completeness, parity enforced by
the separate mechanism in `docs/project_manifest_parity.md`.

| Constant | Value | How web gets it |
|---|---|---|
| `BINAURAL_BED_FORMATS` | `("5.1.4", "7.1.2", "7.1.4")` | `GET /api/v1/configuration` |
| `TRANSAURAL_BED_FORMATS` | `("5.1.4", "7.1.2", "7.1.4")` | `GET /api/v1/configuration` |
| `SURROUND_714` channel order | FL, FR, C, LFE, **BL, BR, SL, SR**, TFL, TFR, TBL, TBR (back before side — differs from 7.1/7.1.2 order) | `GET /api/v1/configuration` `layout_channels["7.1.4"]`; `ProjectDetailPage.tsx` sources the order solely from that value (empty list until config loads, no client-side copy) |
| Per-layout channel sets | `FORMAT_MAP` | `GET /api/v1/configuration` `layout_channels` |

---

## 4. Single source of truth for the constants (§3)

The web preview holds **no** hardcoded copy of the Tier-1 tunable DSP
constants in §3. They are owned solely by the core engine
(`packages/core/src/config.py` plus the mastering/routing/voicing profile tables) and
served to the browser at bootstrap:

- `apps/api/src/features/system/service.py::engine_constants()` reads every
  value straight from its real core source module (never re-typed) and
  returns them as the `constants` block of `GET /api/v1/configuration`.
- The web fetches that block once (`api.getConfiguration()`), normalizes it
  through `resolveEngineConstants` (`masteringProfiles.ts`) into an
  `EngineConstants` object, and threads it into the preview graph builders
  (`previewGraph.ts` / `audioEngine.ts`). The "TS" column in §3 names the
  field of that object (formerly a hardcoded `masteringProfiles.ts`
  constant of the same name).

Because there is exactly one source, there is nothing to cross-check: the
former `contract.py` / `contract.ts` / `dump-constants.mjs` /
`web_constants.json` / `test_contract_parity.py` value-diff mechanism has
been **removed**. Parity of these constants is now structural (one source)
rather than tested. What still verifies the *end-to-end* result is the
golden render diff (§5), which renders the real web graph and diffs it
against the core engine.

The FIR/filter asset-name maps are also served the same way (Ledger **D17**):
`engine_constants()` reads them from their core sources — `EQ_FIR_ASSETS`
(`mastering/eq.py`), `STEM_EQ_FIR_ASSETS` (`separation/stem_eq.py`),
`DECODE_FILTER_SET` (`binaural/profiles.py`), `XTC_FILTER_SET`
(`crosstalk/profiles.py`) — and the web consumes them as
`EngineConstants.eqFirAssets` / `.stemEqFirAssets` / `.decodeFilterSet` /
`.xtcFilterSet`. The physical WAVs still ship web-side under
`apps/web/public/{eq_fir,hrir,xtc}`; only the names are served. The
`master_`/`stem_` EQ naming is single-sourced in core so
`scripts/build_eq_filters.py` and the endpoint agree, and
`test_served_filter_assets_have_shipped_wavs` guards that every served name
has a shipped WAV.

Constants the web still owns locally (not served — these never drift in
practice): structural/mathematical values (`BUTTERWORTH_Q`, `AMBISONIC_ORDER`,
the ACN-12 1/√7 correction, the true-peak kernel taps). The true-peak kernel
now has one web implementation — `masteringProfiles.ts::buildTruePeakKernel` —
which `audioEngine.ts` computes once and passes into the limiter worklet as
`processorOptions.truePeakKernel` data; the worklet holds no kernel copy. The
ceiling safety margin is served from core (`_SAFETY_MARGIN_DB`,
`limiter.py`) and passed the same way. See **Ledger D18**.

### Changing a served constant

Edit the value in its core source module only — it flows to the web at the
next fetch, with no TS edit and no fixture regeneration. Then update the
constants catalog (§3) to describe the new value and re-run the golden render
diff (§5). One test-only follow-up: the web fixture
`apps/web/src/features/projects/engineConstants.fixture.ts` (used by vitest
and the golden harness for render input) carries a copy of these values;
update it to match — the golden diff fails if it drifts from core.

---

## 5. Golden-render tolerance thresholds

`packages/core/tests/test_preview_export_golden.py` renders a fixed deterministic input
through both engines at the same output layout/profile and compares. The
two cross-engine diff tests (`test_cross_engine_golden_diff`,
`test_cross_engine_binaural_golden_diff`) run in the **default** suite
(`uv run pytest packages/core/tests -q`, no marker) against the committed web fixtures —
they are the everyday audible-parity gate, not an opt-in check. The two
`*_metrics_golden` pin tests (Python-only reproducibility) are likewise
unmarked and always run.

| Metric | Threshold | Rationale |
|---|---|---|
| Per-channel RMS delta (mastered, non-binaural bed) | ≤ 3 dB per channel | Tier-2 filter realization differences (IIR/`sosfilt(filt)` vs. biquad) should stay well below audibility |
| Integrated LKFS delta (bed and binaural collapse) | ≤ 1.0 LU | Bounds the Tier-3 approximate-loudness gap (§3); tighten as the web measurer improves |
| True-peak delta (bed and binaural collapse) | ≤ 1.0 dBTP | Web measurer is an approximate oversampled peak, not the standard's exact kernel; bounds that gap |
| Per-ear RMS delta (binaural collapse, FL/FR) | ≤ 3 dB per ear | Same rationale as the bed's per-channel check, applied to the collapsed stereo output — see `test_cross_engine_binaural_golden_diff` |
| Binaural spectral difference (1/3-octave bands, 100 Hz–10 kHz) | ≤ 3 dB per band | Not yet exercised — the binaural golden diff (Ledger D10) covers integrated LKFS/true-peak/per-ear RMS but not a per-band spectral comparison; see Implementation status |

Fixtures and golden metrics are regenerated via `REGENERATE_GOLDEN=1`
(Python side) / `npm run golden:render` (web side), mirroring
`packages/core/tests/test_mastering_golden.py`'s convention. A threshold breach means
either a real bug (fix it) or an approximation that has grown wider than
believed (tighten the approximation or, if the gap is now understood and
acceptable, revise the threshold here explicitly — never silently).

### Implementation status

**Both halves are built and the acceptance test passes.**

- **Export (Python) side** — `packages/core/tests/test_preview_export_golden.py`: a fixed
  deterministic synthetic multichannel bed (`_deterministic_bed`, a
  multi-tone signal, deliberately not RNG-based noise — see its docstring),
  rendered through the real `MasteringChain` with a config scoped to
  exactly what the web side implements (EQ, compression, bass incl.
  mono-maker; loudness normalization is intentionally **off** — see below).
  Its metrics are pinned as a golden fixture
  (`test_python_bed_metrics_golden`, regenerate via `REGENERATE_GOLDEN=1`).
- **Preview (web) side** — `apps/web/src/features/projects/previewGraph.ts` is
  the framework-free extraction of `useStemPreview.ts`'s
  `buildMasteringTopology` (same EQ → compressor → bass/mono-maker graph,
  parameterized over any `BaseAudioContext` instead of only a live
  `AudioContext`); `useStemPreview.ts` now calls it instead of inlining the
  logic. `apps/web/scripts/render-preview-golden.mjs` drives it headless:
  bundles `previewGraph.ts` with esbuild (so it runs under plain Node),
  builds the *same* deterministic bed via a hand-ported JS formula (matching
  a NumPy RNG bitstream across languages isn't practical, so the bed has no
  RNG to begin with — both sides compute the identical multi-tone signal),
  renders it on a real `OfflineAudioContext` from
  [`node-web-audio-api`](https://github.com/ircam-ismm/node-web-audio-api)
  (a spec-compliant native Web Audio implementation for Node — not a
  browser, but not a re-implementation of the graph either), measures
  BS.1770-flavored loudness/true-peak/RMS, and writes
  `packages/core/tests/fixtures/preview_export_golden/web_bed_metrics.json`.
- `test_cross_engine_golden_diff` reads that fixture and asserts the
  tolerances above; it runs by default. Run `npm run golden:render` (from
  `apps/web/`) to refresh the committed fixture after a web-side DSP/constant
  change, then `uv run pytest packages/core/tests/test_preview_export_golden.py`.

**Scope note (bed stage):** `test_cross_engine_golden_diff` covers the
channel-bed mastering chain (EQ → compressor → bass/mono-maker) —
exactly what `buildMasteringGraph` implements. `_mastering_config()`'s
`loudness_normalize=False` is deliberate — bed-level BS.1770 loudness and the
bed-level soft-limit stay out of scope for *this* test, since neither is
implemented in the preview at the bed level, and enabling it here would
compare a Python stage the web harness doesn't render at all.

**Binaural collapse stage (Ledger D10, now covered):**
`apps/web/src/features/projects/previewGraph.ts` also exports `buildBinauralGraph`
— the framework-free extraction of `useStemPreview.ts`'s `initialize()`
ambisonic-encode → HOA-decode → voicing plumbing (the per-speaker
`AmbiMonoEncoder`s stay owned by the caller, same division as
`buildMasteringGraph`'s `channelPorts`). `render-preview-golden.mjs`'s
binaural stage feeds the same mastered bed through it (Studio profile),
adds LFE, measures the one-shot pre-gain LKFS the same way
`measureOutputLoudness` does, applies `loudnessGainFor`'s capped gain, and
runs a real `WaveShaperNode` (`oversample: "4x"`) soft-limit — mirroring
`render_binaural_delivery` stage-for-stage — writing
`packages/core/tests/fixtures/preview_export_golden/web_binaural_metrics.json`.
`test_python_binaural_metrics_golden` pins the Python side's equivalent
`render_binaural_delivery` call (`loudness_normalize=True` this time — this
*is* the stage under test); `test_cross_engine_binaural_golden_diff` diffs
the two against the LKFS/true-peak/per-ear-RMS thresholds above. The Studio
profile's voicing chain is all-zero/identity, so this diff can't distinguish
LFE-before-voicing from LFE-after-voicing either way — it wouldn't have
caught Ledger D11 when that was a live bug, and can't catch a regression of
its fix now — see that entry.

**Still open:** the 1/3-octave spectral-difference check has no test behind
it yet — the LKFS/true-peak/RMS metrics above are scalar summaries, not a
per-band comparison, so a spectral-shape mismatch that happens to preserve
overall loudness and peak could still slip through. Extending
`measureIntegratedLkfs`'s sibling functions (both sides) to also emit
per-band energy is the natural next step.

Building this harness surfaced two real bugs, both fixed (Ledger **D8**,
**D9**), and one open discrepancy discovered while extending it to the
binaural stage (Ledger **D11**) — the harness keeps doing exactly what it
was built for.

**Not yet exercised by the golden diff:** the live preview's true-peak
safety net (§1's "True-peak ceiling" row, **Ledger D12**) is not wired into
`render-preview-golden.mjs`'s binaural-collapse stage — that stage still
only applies `loudnessGainFor`'s gain before soft-limit, matching the
harness's pre-D12 behavior. `packages/core/tests/test_pre_master_hook.py` and
`packages/core/tests/test_match_reference.py`'s `TestComputeChannelFilters` cover the
server-side reference-match plumbing and algorithm respectively;
`test_worker_prepare_reference_match_computes_and_serves_fir`
(`apps/api/tests/test_web_projects_reference_match.py`) covers the asset precompute/signature/serve path
end-to-end. None of these are cross-engine signal-level diffs the way §5's
table above is — extending the harness to cover both is future work.

---

## 6. Discrepancy ledger

Each row: what was found, the fix (file/constant), and any residual caveat. Do not delete resolved rows — mark them fixed so the history of what was found and corrected stays visible.

| # | Discrepancy | Status |
|---|---|---|
| D1 | `spatial_audio_engine.md` §6 claimed a cross-engine reference-render acceptance test existed; it did not. | Fixed — §6 now points here and at `test_preview_export_golden.py`. |
| D2 | Shared constants were hand-duplicated with no automated cross-check beyond 2 values (`VOICING_PARAMS.listening`, the ACN-12 factor). | Fixed by the signature mechanism (§4) covering the full Tier-1 set above. |
| D3 | `estimateRouteScale` approximates `route_scale` from the route table, not decoded-buffer energy. | Open — Tier 3, no threshold assigned yet; tighten if the golden diff shows drift. |
| D4 | `measureOutputLoudness` omits K-weighting/gating and measures a ~1s window, not the whole file. | Open — Tier 3, bounded by §5's 1.0 LU threshold. A manual A/B on a real track found ~0.3 LU from windowing alone plus ~0.4-0.6 LU unexplained (likely Tier-2/3 gaps stacking). |
| D5 | Bass mono-maker (`mono_cutoff_hz`) wasn't implemented in the preview. | Fixed — `buildMasteringTopology` cross-couples `MONO_MAKER_STEREO_PAIRS` via paired lowpass, matching `BassController`'s mono-maker. Biquad vs. `sosfiltfilt` realization gap stays Tier 2. |
| D6 | Backend's center/back-fold coefficient is exact `1/√2`, independent of the configurable `surround_coeff`; web used truncated `0.7071` for both roles (~-98 dB mismatch, inaudible but Tier-1). | Fixed — `ITU_CENTER_COEFF` (exact) split from `SURROUND_DOWNMIX_COEFF` (configurable) in `masteringProfiles.ts`. |
| D7 | No golden cross-engine render diff existed — only the Python half. | Fixed for the mastering-chain scope: `previewGraph.ts` + `apps/web/scripts/render-preview-golden.mjs` (Node/`node-web-audio-api`) + `test_cross_engine_golden_diff` render both engines and compare real metrics. Extended to binaural — see D10. |
| D8 | `node-web-audio-api`'s `DynamicsCompressorNode.reduction` returned positive values for a sub-threshold signal, which the linked-gain math turned into amplification (found via compressor-only bisection: RMS increased above baseline). | Fixed — `applyCompressorReduction` clamps to `Math.min(0, reduction)`, shipped defensively (not harness-only). |
| D9 | Mono-maker's single-pass `BiquadFilterNode` (vs. backend's zero-phase `butter(2)`+`sosfiltfilt`) flipped its net effect from cut to boost on decorrelated content; bass sub/mid separately used native shelf/peak filter types instead of the backend's additive-lowpass-band identity. | Fixed — `BUTTERWORTH_Q = 1/√2` cascaded two-stage lowpass for the mono-maker + `buildAdditiveBandGain` (`previewGraph.ts`) matching `_apply_band_gain`'s topology. Cross-engine RMS delta dropped from >5 dB to within the 3 dB threshold. |
| D10 | Golden diff covered only EQ/comp/bass — loudness normalization, final soft-limit, and binaural collapse were untested (confirmed a real gap by manual A/B). | Fixed for the binaural-collapse stage: `buildBinauralGraph` + a binaural stage in `render-preview-golden.mjs` + `test_python_binaural_metrics_golden`/`test_cross_engine_binaural_golden_diff`, Studio profile only. Still open: bed-level loudness/soft-limit stage untested; 1/3-octave spectral check has no test; Listening profile's non-identity voicing untested. |
| D11 | Preview added LFE after voicing (`mergePoint`); backend adds it before voicing (`render_binaural`). Numerically inert at Studio/Flat (all-zero voicing, so D10 didn't catch it) but would diverge at Listening; the same `mergePoint` LFE also leaked into the stereo (BS.775) downmix, which excludes LFE by spec. | Fixed — `buildBinauralGraph` exposes `preVoicing`; `audioEngine.ts` sums LFE there instead, fixing the stereo-downmix leak for free. Studio golden fixture moved only ~1e-7 (confirms prior inertness). Listening profile and stereo LFE handling still lack golden-diff coverage. |
| D12 | Two gaps: (1) every real project ran with `match_reference` active server-side, but the preview never implemented reference-match EQ at all; (2) preview had no mirror of `normalize_loudness`'s true-peak gain reduction. | Fixed. `ReferenceMatchProcessor.compute_channel_filters` (`packages/core/src/mastering/match_reference.py`) exposes the real per-channel FIRs + RMS gain; a `pre_master_hook` on `StemUpmixPipeline.process_file` lets `apps/api/src/features/projects/worker_reference_match.py::WorkerManager.prepare_reference_match` capture the pre-mastering bed without a full mastering/write pass; result persisted per-project and served via `GET /api/v1/projects/{id}/reference-match/fir`; preview convolves it in `buildMasteringGraph` before the named-EQ stage. True peak: `measureBufferTruePeakDbtp` (shared with the golden harness) feeds a second gain-reduction stage in `apply()`, mirroring `normalize_loudness`'s two-stage correction. Both fixes are Tier-3 bounded approximations (server-canonical-bed FIR; bounded-window true-peak). |
| D13 | The recompute signature hashed `strength`/`rms` — live preview-only knobs that don't change the FIR bytes — so every slider drag triggered a full recompute; `save_project_settings` also ran `prepare_reference_match` (a full mix pass) inline on the request thread, so a 350ms-debounced slider pegged the backend at 130-150% CPU sustained. | Fixed — signature drops `strength`/`rms`; a dedicated single-thread executor + `schedule_reference_match` coalesce repeat calls into one trailing recompute; `save_project_settings` and the post-prepare hook call `schedule_reference_match` instead of running inline; `reference_match_pending` surfaces the state to the frontend. |
| D14 | `MasteringChain`'s bed-level final stage moved from a memoryless `soft_limit` tanh + scalar true-peak gain to `LookAheadLimiter` (fixes audible ISP overshoot; changes output level/character vs. the old saturator) — leaving the preview on the old node for every mode would be an audible mismatch, not just numeric. | Fixed for the native (discrete bed) monitoring path only — the one mode that mirrors bed output with no further collapse. `apps/web/public/limiter.worklet.js` (first AudioWorklet in this codebase) ports the algorithm as a causal streaming processor: 4x-oversampled linked detection (reusing the existing true-peak kernel — Tier 2, not `limiter.py`'s exact FIR), a monotonic-deque sliding-window minimum covering both the lookahead window and the FIR dilation margin, then the same attack/release smoothing. Introduces a real ~5ms delay (expected cost of real-time lookahead), with a tanh-WaveShaper fallback if the worklet fails to load. `LIMITER_LOOKAHEAD_MS`/`LIMITER_RELEASE_MS` added as Tier-1 served constants. The binaural/stereo-downmix path deliberately keeps the old tanh `soft_limit` unchanged, matching the backend's own untouched call there. Not exercised by the golden diff (native path out of scope) — validated manually (impulse/tone/noise signals) plus `useStemPreview.test.tsx` wiring tests. |
| D15 | New feature, not a discrepancy: added `transaural` (crosstalk-cancelled speaker) as a second delivery target alongside `binaural`. | Implemented core+web together: core `packages/core/src/crosstalk/` reuses `render_binaural(profile="flat")` + a new 2x2 XTC FIR matrix + existing `apply_voicing`, filters baked by `scripts/build_crosstalk_filters.py` from the shared `head_model.py` with a Tikhonov-regularized inverse; web `buildCrosstalkGraph` reuses `buildBinauralGraph("flat")` + a 4-convolver XTC matrix + the shared voicing chain, wired as a fourth parallel gated bus. `CROSSTALK_LOUDNESS_MAX_GAIN_DB` added to the served constants (§3/§4). Accepted cost: the crosstalk graph's internal flat-HRIR decode now fetches unconditionally on every `initialize()` regardless of output mode. Not yet covered by the golden diff — only the Python-side objective check (`packages/core/tests/test_crosstalk.py`) exists, same open item as binaural's Listening profile (D10). |
| D16 | Not a discrepancy: constants were hand-mirrored in `masteringProfiles.ts`, kept honest by a live cross-check (`contract.py`/`contract.ts` → fixture → `test_contract_parity.py`) — every tuning change needed a two-sided edit. | Made core the single owner: `engine_constants()` (`apps/api/src/features/system/service.py`) serves the values via `GET /api/v1/configuration`; web normalizes them through `resolveEngineConstants`; the old cross-check machinery was deleted (nothing to diff with one source). Scope: tunable acoustic constants only (comp/bass profiles, gains, cutoffs, haas, loudness ceilings, limiter times, binaural+transaural voicing). Structural/math constants (`BUTTERWORTH_Q`, ambisonic order, ACN-12 1/√7, true-peak kernel) and asset filenames stay web-local. |
| D17 | Not a discrepancy: extends D16's served-constants pattern to four filename maps (`EQ_FIR_ASSETS`, `STEM_EQ_FIR_ASSETS`, `DECODE_FILTER_SET`, `XTC_FILTER_SET`) that were still hand-mirrored in `masteringProfiles.ts`, risking silent drift from the shipped WAVs. | Centralized the EQ naming in `mastering/eq.py`/`separation/stem_eq.py`, consumed by both `build_eq_filters.py` and `engine_constants()`; web reads all four from `EngineConstants`. New tests: `test_configuration_serves_filter_asset_maps` (served == core) and `test_served_filter_assets_have_shipped_wavs` (every served name has a shipped WAV). |
| D18 | Not a discrepancy: de-duplicated a web-side copy. The true-peak kernel existed as two hand-synced files (`masteringProfiles.ts::buildTruePeakKernel` and `apps/web/public/truePeakKernel.js`, kept bit-for-bit by a test since a worklet can't import the app bundle); the worklet also hardcoded `SAFETY_MARGIN_DB = 0.1`, duplicating core's `limiter.py::_SAFETY_MARGIN_DB`. | Fixed — `audioEngine.ts` computes the kernel once via `buildTruePeakKernel()` and passes it to the worklet as `processorOptions.truePeakKernel`; `truePeakKernel.js` deleted; `_SAFETY_MARGIN_DB` now served by `engine_constants()` as `safety_margin_db`. Tests now assert the kernel's numeric properties instead of cross-copy equality. No output drift — golden diff stays byte-identical. |
| D19 | `routing_for_scene` (`routing.py`) and its web mirror `routingFromAzimuthElevation` (`spatial.ts`) ranked nearest-3 speakers by a plain linear azimuth difference with no ±180° wraparound. `BL`/`TBL` sit at +135°, `BR`/`TBR` at −135°, so a stem placed near true back (azimuth ≈180°) computed the far rear speaker as ~315° away and dropped it from the nearest-3, collapsing routing onto 3 same-side channels — audible as a level drop plus a left/right image pull for back-positioned (and, less severely, top-positioned) stems in binaural/transaural output. Reachable from both preview (`audioEngine.ts`) and export (baked into the job snapshot via `service.py::_resolve_track_routing`). | Fixed — both sides now compute azimuth distance through a shared `_angular_distance`/`angularDistance` helper that wraps the delta to ±180° before combining with the elevation delta. New tests pin the invariant this restores: `apps/api/tests/test_project_routing.py` and `apps/web/src/lib/spatial.test.ts` (rear routes to both rear channels, left/right symmetry, constant-power at every azimuth, wraparound at out-of-range azimuth). |
