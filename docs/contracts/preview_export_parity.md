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
| Per-stem EQ (pre-routing) | `upmixer/separation/stem_eq.py::StemEQ` (asset names: `STEM_EQ_FIR_ASSETS`) | `EngineConstants.stemEqFirAssets` (served, see §4) + `buildFirEqNode`, wired in `audioEngine.ts`/`useStemPreview.ts` |
| Stem → speaker-bed routing | `upmixer/separation/stem_router.py::StemRouter.route` | `createStemSends` in `useStemPreview.ts`, using `channelGroupGain`/`buildSurroundSend`/`buildHeightSend`/`buildDiffuseSend` from `masteringProfiles.ts` |
| Multichannel channel derivation (non-stem path) | `upmixer/upmix/multichannel.py::MultichannelUpmixer` | Not previewed — multichannel pass-through input has no stem-preview path |
| Ambisonic encode (order-3 ACN/N3D) | `upmixer/binaural/ambisonics.py::encode_gains` | `createPositionalEncoder` (wraps `AmbiMonoEncoder`/JSAmbisonics) + `ACN12_N3D_CORRECTION` in `previewGraph.ts`, called per speaker from `useStemPreview.ts` — see `spatial_audio_engine.md` §3 |
| Virtual-loudspeaker geometry | `upmixer/binaural/geometry.py` | `web/src/lib/spatial.ts::speakerCoordinates` — see `spatial_audio_engine.md` §2 |
| HOA decode → binaural | `upmixer/binaural/decoder.py` | Per-ACN `ConvolverNode` bank in `buildBinauralGraph` (`previewGraph.ts`), called from `initialize()` in `useStemPreview.ts` — see `spatial_audio_engine.md` §4 and **Ledger D10** |
| Binaural voicing chain | `upmixer/binaural/voicing.py::apply_voicing` | `buildVoicingChain`/`applyVoicingParams` in `masteringProfiles.ts`, wired inside `buildBinauralGraph` (`previewGraph.ts`) — see `spatial_audio_engine.md` §5 |
| Crosstalk-cancellation (transaural) | `upmixer/crosstalk/renderer.py::render_crosstalk` (reuses `render_binaural` "flat" + `apply_xtc` + `apply_voicing`) | `buildCrosstalkGraph` (`previewGraph.ts`, reuses `buildBinauralGraph("flat")` + a 4-convolver 2x2 XTC matrix + `buildVoicingChain`), wired inside `initialize()` (`useStemPreview.ts`) — see `transaural_speakers.md` §1 |
| Reference match (spectral + RMS) | `upmixer/mastering/match_reference.py::ReferenceMatchProcessor` | Server-precomputed per-channel FIR bank + RMS gain (`ReferenceMatchProcessor.compute_channel_filters`, `upmixer_web/worker.py::WorkerManager.prepare_reference_match`), served as a project asset and convolved via `buildFirEqNode` inside `buildMasteringGraph` (`previewGraph.ts`), before the named-EQ stage — see **Ledger D12** |
| Spectral (mastering) EQ | `upmixer/mastering/eq.py::SpectralShaper` (asset names: `EQ_FIR_ASSETS`) | `EngineConstants.eqFirAssets` (served, see §4) + `buildFirEqNode` in `buildMasteringGraph` (same asset scheme as stem EQ, applied post-routing) |
| Bus compression | `upmixer/mastering/compressor.py::BusCompressor` | Linked `DynamicsCompressorNode` detector + polled `.reduction` in `buildMasteringTopology` (`useStemPreview.ts`) |
| Bass control | `upmixer/mastering/bass.py::BassController` | Bass shelves/exciter/mono-maker in `buildMasteringTopology` (**Ledger D5**) |
| BS.1770 loudness normalization | `upmixer/loudness.py::normalize_loudness` (bed) / `render_binaural_delivery`'s own pass (collapse) | `measureOutputLoudness`/`loudnessGainFor` (`useStemPreview.ts`) — approximate, see **Tier 3**; the collapse-stage pass is now golden-diff-covered, see **Ledger D10** |
| True-peak ceiling | `normalize_loudness`'s `max_tp_dbtp` gain reduction (`upmixer/loudness.py`) | Second gain reduction in `apply()` (`useStemPreview.ts`), driven by `measureBufferTruePeakDbtp` (`masteringProfiles.ts`) on the same `mergePointAnalyser` window `measureOutputLoudness` reads — approximate, see **Tier 3** and **Ledger D12** |
| Look-ahead limiter (last, bed-level) | `upmixer/mastering/limiter.py::LookAheadLimiter` (`MasteringChain`) | Native monitoring path: `"limiter-processor"` AudioWorklet (`web/public/limiter.worklet.js`), replacing `nativeSoftLimitNode` in `initialize()` (`useStemPreview.ts`) — see **Ledger D14**. Binaural/stereo-downmix path keeps the plain tanh `softLimitNode`/`buildSoftLimitCurve`, unchanged, matching `render_binaural_delivery`'s own untouched `soft_limit` call (D14) |
| ITU-R BS.775 stereo downmix | `upmixer/utils.py::itu_downmix_stereo` | `STEREO_DOWNMIX_GAINS` + `applyOutputMode` (`useStemPreview.ts`) |

Both `UpmixPipeline` and `StemUpmixPipeline` share one `MasteringChain`
instance (`upmixer/mastering/chain.py`) and one `render_binaural_delivery`
call, so the export side of every row above is centralized in core; the
preview side is the parity-critical surface.

**Processing order is itself contracted** (Tier 1): reference match → EQ →
compression → bass control → BS.1770 loudness → soft-limit *last*. Soft
limiting after loudness (not before) is deliberate — see
`upmixer/mastering/chain.py`'s module docstring — and the preview graph
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
minimum-phase FIR `upmixer/mastering/eq.py::_build_fir` computed
(`scripts/build_eq_filters.py` calls that function directly and ships the
WAV under `web/public/eq_fir/`), so the convolution itself is Tier 1, not
an approximation of the curve.

The reference-match FIR asset is the same special case, computed per-project
instead of built once at build time: `upmixer_web/worker.py::WorkerManager
.prepare_reference_match` calls `ReferenceMatchProcessor.compute_channel_
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

### Channel-group gains, LFE, cutoffs — `upmixer/config.py::UpmixConfig`

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
| Center/back-fold downmix coefficient (fixed) | 1/√2 ≈ 0.70710678 | `upmixer/utils.py::_ITU_C_COEFF` | `ITU_CENTER_COEFF` (`masteringProfiles.ts`) | 1 |

### Diffuse/Haas sends — `upmixer/separation/stem_router.py`, `upmixer/utils.py::diffuse_send`

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Diffuse send blend | 0.55 | `diffuse_send` default `blend` | `DIFFUSE_SEND_BLEND` | 2 |
| Surround Haas delay L / R | 31 ms / 37 ms | `route()` literals | `SURROUND_HAAS_MS` | 2 |
| Height Haas delay L / R | 23 ms / 29 ms | `route()` literals | `HEIGHT_HAAS_MS` | 2 |
| Per-stem route-energy normalization | `route_scale = sqrt(input_energy / routed_energy)` | `StemRouter.route` | `estimateRouteScale` (approximation) | 3 |

### Bus compressor profiles — `upmixer/mastering/compressor.py::COMP_PROFILES`

| Profile | threshold_db | ratio | attack_ms | release_ms | knee_db | makeup_db | Tier |
|---|---|---|---|---|---|---|---|
| `transparent` | −22.0 | 1.5 | 30.0 | 300.0 | 9.0 | 0.0 | 2 |
| `glue` | −18.0 | 2.0 | 20.0 | 200.0 | 6.0 | 0.0 | 2 |
| `warm` | −15.0 | 2.0 | 40.0 | 400.0 | 12.0 | 0.0 | 2 |

(TS mirror: `COMP_PROFILES` in `masteringProfiles.ts`.)

### Bass profiles — `upmixer/mastering/bass.py::BASS_PROFILES`

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

### Loudness (BS.1770-5) — `upmixer/loudness.py`

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

### Reference match — `upmixer/mastering/match_reference.py`

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

These constants live only in `upmixer/mastering/match_reference.py` — the
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
| Binaural collapse loudness ceiling | 6.0 dB | `BINAURAL_LOUDNESS_MAX_GAIN_DB` (`upmixer/binaural/renderer.py`) | `BINAURAL_LOUDNESS_MAX_GAIN_DB` | 1 |

### Transaural — see `transaural_speakers.md` for the full speaker-geometry/XTC-regularization/voicing table. Cross-cutting constant repeated here because it also gates delivery gain-staging:

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Crosstalk collapse loudness ceiling | 6.0 dB | `CROSSTALK_LOUDNESS_MAX_GAIN_DB` (`upmixer/crosstalk/renderer.py`) | `CROSSTALK_LOUDNESS_MAX_GAIN_DB` | 1 |

### Channel layouts / formats — `upmixer/formats.py`

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
(`upmixer/config.py` plus the mastering/routing/voicing profile tables) and
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
the ACN-12 1/√7 correction, the true-peak kernel taps). The worklet true-peak
kernel + safety margin stay pinned bit-for-bit by `limiterWorklet.test.ts`.

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

`tests/test_preview_export_golden.py` renders a fixed deterministic input
through both engines at the same output layout/profile and compares. The
two cross-engine diff tests (`test_cross_engine_golden_diff`,
`test_cross_engine_binaural_golden_diff`) run in the **default** suite
(`python3 -m pytest -q`, no marker) against the committed web fixtures —
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
`tests/test_mastering_golden.py`'s convention. A threshold breach means
either a real bug (fix it) or an approximation that has grown wider than
believed (tighten the approximation or, if the gap is now understood and
acceptable, revise the threshold here explicitly — never silently).

### Implementation status

**Both halves are built and the acceptance test passes.**

- **Export (Python) side** — `tests/test_preview_export_golden.py`: a fixed
  deterministic synthetic multichannel bed (`_deterministic_bed`, a
  multi-tone signal, deliberately not RNG-based noise — see its docstring),
  rendered through the real `MasteringChain` with a config scoped to
  exactly what the web side implements (EQ, compression, bass incl.
  mono-maker; loudness normalization is intentionally **off** — see below).
  Its metrics are pinned as a golden fixture
  (`test_python_bed_metrics_golden`, regenerate via `REGENERATE_GOLDEN=1`).
- **Preview (web) side** — `web/src/features/projects/previewGraph.ts` is
  the framework-free extraction of `useStemPreview.ts`'s
  `buildMasteringTopology` (same EQ → compressor → bass/mono-maker graph,
  parameterized over any `BaseAudioContext` instead of only a live
  `AudioContext`); `useStemPreview.ts` now calls it instead of inlining the
  logic. `web/scripts/render-preview-golden.mjs` drives it headless:
  bundles `previewGraph.ts` with esbuild (so it runs under plain Node),
  builds the *same* deterministic bed via a hand-ported JS formula (matching
  a NumPy RNG bitstream across languages isn't practical, so the bed has no
  RNG to begin with — both sides compute the identical multi-tone signal),
  renders it on a real `OfflineAudioContext` from
  [`node-web-audio-api`](https://github.com/ircam-ismm/node-web-audio-api)
  (a spec-compliant native Web Audio implementation for Node — not a
  browser, but not a re-implementation of the graph either), measures
  BS.1770-flavored loudness/true-peak/RMS, and writes
  `tests/fixtures/preview_export_golden/web_bed_metrics.json`.
- `test_cross_engine_golden_diff` reads that fixture and asserts the
  tolerances above; it runs by default. Run `npm run golden:render` (from
  `web/`) to refresh the committed fixture after a web-side DSP/constant
  change, then `python3 -m pytest tests/test_preview_export_golden.py`.

**Scope note (bed stage):** `test_cross_engine_golden_diff` covers the
channel-bed mastering chain (EQ → compressor → bass/mono-maker) —
exactly what `buildMasteringGraph` implements. `_mastering_config()`'s
`loudness_normalize=False` is deliberate — bed-level BS.1770 loudness and the
bed-level soft-limit stay out of scope for *this* test, since neither is
implemented in the preview at the bed level, and enabling it here would
compare a Python stage the web harness doesn't render at all.

**Binaural collapse stage (Ledger D10, now covered):**
`web/src/features/projects/previewGraph.ts` also exports `buildBinauralGraph`
— the framework-free extraction of `useStemPreview.ts`'s `initialize()`
ambisonic-encode → HOA-decode → voicing plumbing (the per-speaker
`AmbiMonoEncoder`s stay owned by the caller, same division as
`buildMasteringGraph`'s `channelPorts`). `render-preview-golden.mjs`'s
binaural stage feeds the same mastered bed through it (Studio profile),
adds LFE, measures the one-shot pre-gain LKFS the same way
`measureOutputLoudness` does, applies `loudnessGainFor`'s capped gain, and
runs a real `WaveShaperNode` (`oversample: "4x"`) soft-limit — mirroring
`render_binaural_delivery` stage-for-stage — writing
`tests/fixtures/preview_export_golden/web_binaural_metrics.json`.
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
harness's pre-D12 behavior. `tests/test_pre_master_hook.py` and
`tests/test_match_reference.py`'s `TestComputeChannelFilters` cover the
server-side reference-match plumbing and algorithm respectively;
`test_worker_prepare_reference_match_computes_and_serves_fir`
(`tests/test_web.py`) covers the asset precompute/signature/serve path
end-to-end. None of these are cross-engine signal-level diffs the way §5's
table above is — extending the harness to cover both is future work.

---

## 6. Discrepancy ledger

| # | Discrepancy | Status |
|---|---|---|
| D1 | `spatial_audio_engine.md` §6 claimed a cross-engine reference-render acceptance test existed; it did not. | Fixed — §6 now points here and at `test_preview_export_golden.py`. |
| D2 | Shared constants were hand-duplicated with no automated cross-check beyond `VOICING_PARAMS.listening` + the ACN-12 factor. | Fixed by the signature mechanism (§4) covering the full Tier-1 set above. |
| D3 | `estimateRouteScale` approximates the backend's energy-based `route_scale` from the route table alone, not the actual decoded-buffer energy. | Open — Tier 3, no numeric threshold assigned yet pending golden-diff data; tighten if `test_preview_export_golden.py` shows audible drift. |
| D4 | Preview loudness (`measureOutputLoudness`) omits K-weighting and gating, and measures only a single ~1s window near the start of playback rather than the whole file. | Open — Tier 3, bounded by the 1.0 LU threshold in §5. The whole-track-vs-window gap was confirmed non-trivial on a real mixed track (manual export/preview A/B, not this suite): the same window measured directly in the delivered export file was ~0.3 LU off its own whole-track target from natural song dynamics alone, leaving a further ~0.4-0.6 LU unexplained by windowing — likely the Tier-2/3 realization gaps below stacking up. |
| D5 | Bass mono-maker (`mono_cutoff_hz`) was not implemented in the preview graph — `mono`/`enhance` bass profiles behaved differently in preview vs. export. | Fixed — `buildMasteringTopology` (`useStemPreview.ts`) now cross-couples each `MONO_MAKER_STEREO_PAIRS` pair through paired lowpass filters and swaps each side's low band for the shared mono band, matching `BassController.process`'s mono-maker identity. DSP realization (biquad vs. `sosfiltfilt`) is Tier 2, same gap as the rest of this chain. |
| D6 | `upmixer/utils.py::itu_downmix_stereo`'s center-channel coefficient and back→side fold are always the exact `_ITU_C_COEFF = 1/√2 ≈ 0.70710678`, independent of the user-configurable `surround_coeff`. The web's `STEREO_DOWNMIX_GAINS` (`useStemPreview.ts`) used the same truncated `0.7071` for both roles, conflating the fixed center coefficient with the configurable surround coefficient (magnitude ≈1e-5, ~−98 dB relative — inaudible, but a real bit-for-bit Tier-1 mismatch). | Fixed — `masteringProfiles.ts` now exports `ITU_CENTER_COEFF = 1/Math.sqrt(2)` (exact) separately from `SURROUND_DOWNMIX_COEFF = 0.7071` (the configurable one); `useStemPreview.ts`'s `STEREO_DOWNMIX_GAINS` uses each in its correct role. |
| D7 | The golden cross-engine render diff (§5) that would actually *prove* preview/export equivalence at the signal level did not exist — only its Python half did. | Fixed for the mastering-chain scope — `previewGraph.ts` (extracted graph builder) + `web/scripts/render-preview-golden.mjs` (Node/`node-web-audio-api` headless harness) + `test_cross_engine_golden_diff` now render both engines and compare real measured metrics; the test passes on real numbers, not a loosened/faked assertion. Extended to the binaural-collapse loudness stage — see **D10**. |
| D8 | `node-web-audio-api`'s `DynamicsCompressorNode.reduction` returned small **positive** values for a sub-threshold signal instead of the spec-mandated `<= 0`, which the linked-compressor gain math (`10**(reduction/20)`) turned into unwanted amplification — found via the golden-diff harness (compressor-only bisection showed RMS *increasing* above the unprocessed baseline, which a spec-compliant attenuator can never do). | Fixed — `previewGraph.ts::applyCompressorReduction` now clamps to `Math.min(0, reduction)` before applying it. This is a defensive fix in the shipped preview code itself (not harness-only): real browsers are expected to already return `<= 0`, but clamping costs nothing and guards against any implementation that doesn't. |
| D9 | The bass mono-maker's paired lowpass filters used a single `BiquadFilterNode` (default `Q`, single-pass) to stand in for the backend's `butter(2, ...)` applied via `sosfiltfilt` (zero-phase — forward+backward, i.e. a squared/steeper magnitude response) — found via the golden-diff harness: on decorrelated per-channel test content, the extra energy the single-pass filter leaked near the cutoff was enough to flip the mono-maker's net level effect from a slight cut (backend) to a slight boost (preview). The bass sub/mid stage separately used native `BiquadFilterNode` "lowshelf"/"peaking" types instead of the backend's additive-lowpass-band identity (`(ch - band) + band*gain_lin`), a different frequency-response shape than what `elevation_eq`/height-send filters elsewhere in this same file already correctly use. | Fixed — added `BUTTERWORTH_Q = 1/√2` (`masteringProfiles.ts`), applied to the mono-maker's now-cascaded (two-stage, approximating the zero-phase magnitude response) lowpass pair and to the surround/height send highpass filters; rewrote the bass sub/mid stage as `buildAdditiveBandGain` (`previewGraph.ts`), matching `_apply_band_gain`'s topology exactly instead of using native shelf/peak filter types. Cross-engine per-channel RMS delta on the golden bed dropped from over 5 dB to within the 3 dB threshold. |
| D10 | The golden diff (§5) covered only the EQ/compressor/bass mastering-chain stage. BS.1770 loudness normalization, the final soft-limit, and the binaural/ambisonic collapse were not exercised by any cross-engine render comparison — confirmed as a real, non-trivial gap by a manual export/preview A/B on a real track (not automated, see D4), which found deltas well outside this contract's tolerances. | Fixed for the binaural-collapse loudness/soft-limit stage — `buildBinauralGraph` (`previewGraph.ts`, extracted from `useStemPreview.ts`'s `initialize()`) + the binaural stage `render-preview-golden.mjs` adds (ambisonic encode → HOA decode → voicing → one-shot loudness gain → real oversampled soft-limit) + `test_python_binaural_metrics_golden`/`test_cross_engine_binaural_golden_diff` (mirroring `render_binaural_delivery`) now cover this at the Studio profile, passing on real measured LKFS/true-peak/per-ear-RMS numbers. Still open: (1) the bed-level loudness/soft-limit stage (before binaural collapse) remains untested, since the preview doesn't implement it at the bed level at all; (2) the 1/3-octave spectral-difference check has no test yet — see the Implementation status note above; (3) only the Studio profile is exercised, so Listening's non-identity voicing chain (and Ledger D11's fix, which only matters where voicing is non-identity) isn't. |
| D11 | The live preview added LFE to the binaural mix *after* the voicing chain (at `mergePoint`, alongside the gated binaural/stereo signal), but the backend's `render_binaural` adds LFE to left/right *before* `apply_voicing` runs. At the Studio/Flat profiles this was numerically inert (voicing is all-zero/identity there), which is why building the D10 harness at Studio profile didn't surface it — but at the Listening profile (the only one with a non-identity voicing chain: crossfeed, shelves, presence, widen), LFE would be crossfed/shelved/widened in the backend and would not be in the preview, a real signal difference for any content with LFE energy. Separately, the web's `mergePoint` LFE addition also applied uniformly to the *stereo* (BS.775 downmix) output mode, whereas BS.775 excludes LFE from the downmix entirely — a second, related gap for that mode. | Fixed. `buildBinauralGraph` (`previewGraph.ts`) now exposes `preVoicing` — the pre-voicing stereo merge point (the decode stage's `decodeMerger`) — alongside the existing post-voicing `output`. `audioEngine.ts`'s `initialize()` sums the LFE bus into `preVoicing`'s two inputs instead of `mergePoint`, so LFE now flows through the voicing chain exactly like the backend's `left = left + lfe; right = right + lfe` ahead of `apply_voicing`. Since the stereo (BS.775) and native paths don't route through this binaural graph at all, wiring LFE only at `preVoicing` (not also at `mergePoint`) naturally excludes it from the stereo downmix too, fixing the second half of this discrepancy for free. `render-preview-golden.mjs` updated to match (`binaural.preVoicing` instead of `binaural.output`); the Studio-profile golden fixture changed only at floating-point noise level (~1e-7 relative), confirming the ordering really was numerically inert there as this entry always claimed — Listening profile and stereo-downmix LFE handling still have no golden-diff coverage to verify against, same gap as before. |
| D12 | Every existing project ran `mastering.eq.profile: null` with `mastering.match_reference` active (server-side export applies `ReferenceMatchProcessor`), but the web preview never implemented reference-matching EQ at all — only named EQ profiles reached `buildMasteringGraph`, and `MasterPreview` had no `match_reference` field. Separately, the preview also never implemented true-peak limiting — `normalize_loudness`'s `max_tp_dbtp` gain reduction had no preview-side mirror. | Fixed. Reference match: `ReferenceMatchProcessor.compute_channel_filters` (new method, `upmixer/mastering/match_reference.py`) exposes the real per-channel FIRs + RMS gain `process()` builds internally; `StemUpmixPipeline.process_file` gained an optional `pre_master_hook` (+ `PreMasterAbort`) so `upmixer_web/worker.py::WorkerManager.prepare_reference_match` can capture the pre-mastering bed from a real (cache-hit, no-inference) pipeline run without paying for mastering/write. The result is persisted per-project (`ProjectStemStorage.write_reference_match`, signature-gated on reference id/layout/match-params — deliberately *not* on live mixing edits, since the bed is a canonical server render, not the browser's live-edited mix) and served via `GET /api/v1/projects/{id}/reference-match/fir`. The preview loads it into per-channel `ConvolverNode`s in `buildMasteringGraph` (`previewGraph.ts`), before the named-EQ stage, plus a global RMS gain node (including LFE, wired separately in `useStemPreview.ts` since LFE bypasses `buildMasteringGraph`) — see §1's new pipeline-map row and §3's "Reference match" section. True peak: `measureBufferTruePeakDbtp` (`masteringProfiles.ts`, extracted from and now shared with `render-preview-golden.mjs`'s own true-peak measurement, deleting that duplicate) measures the same `mergePointAnalyser` window `measureOutputLoudness` already reads; `apply()` (`useStemPreview.ts`) applies a second gain reduction — mirroring `normalize_loudness`'s two-stage correction — gated on the same `normalize` flag as the loudness correction, matching the backend folding both into one `if cfg.loudness_normalize:` block. Both fixes are bounded Tier-3 approximations (server-canonical-bed FIR; bounded-window true-peak) — see §2/§3 for exactly which parts are Tier 1 (the FIR bytes and RMS gain themselves) vs. Tier 3 (what bed/window they were computed against). |

| D13 | `WorkerManager.prepare_reference_match`'s recompute signature (introduced in D12) hashed `strength` and `rms` alongside `spectrum`/`max_db`/reference/layout — but `strength` (wet/dry blend) and `rms` (gate) are live preview-only knobs applied in the browser (`ProjectDetailPage.tsx`'s `previewMastering`) that never change the FIR bytes or `rms_gain_db` `compute_channel_filters` produces. Since `MasteringSection.tsx`'s "Spectral match strength" is a slider, every drag flipped the signature. Compounding this, `save_project_settings` called `prepare_reference_match` — a full-song mix pass — *inline* on the API request thread. With settings saves debounced at only 350ms in the browser, dragging the slider launched one full mix pass per tick, pegging the backend at 130-150% CPU sustained (reported as a real-world stutter, confirmed via Activity Monitor). | Fixed. Signature (`_reference_match_signature`, `upmixer_web/worker.py`) no longer hashes `strength`/`rms` — only `reference_id`/`reference_sha256`/`channel_layout`/`spectrum`/`max_db`, the inputs that actually change the asset. Separately, `WorkerManager` gained a dedicated single-thread `_refmatch_executor` and `schedule_reference_match`/`_run_reference_match`, which coalesce rapid repeat calls for the same project into one trailing recompute instead of one run per call, and `reference_match_pending` reports the queued-or-running state. `save_project_settings` (`api.py`) and the post-prepare hook in `_run_project` now call `schedule_reference_match` instead of running `prepare_reference_match` inline — the request returns immediately. `ProjectView.reference_match_pending` (`schemas.py`) surfaces this to the frontend, which keeps its 2s poll and SSE stream open (`ProjectDetailPage.tsx`) while a recompute is pending so `project.reference_match` refreshes once the background pass lands. |

| D14 | Roadmap Phase 1.1 replaced `MasteringChain`'s bed-level final stage — the memoryless `soft_limit` tanh saturator plus `normalize_loudness`'s separate scalar True-Peak gain step — with `upmixer/mastering/limiter.py::LookAheadLimiter`, a linked look-ahead brickwall limiter, to fix audible ISP overshoot the old scalar approach let through. Because a look-ahead limiter changes output level/character versus a memoryless saturator (louder average level from tighter True-Peak margins, different transient behavior), leaving the preview on the old `softLimitNode` for every mode would have been an audible, not just numeric, preview/export mismatch. | Fixed for the native (discrete multichannel bed) monitoring path — the one preview mode that actually mirrors `MasteringChain`'s bed output with no further collapse/downmix. `web/public/limiter.worklet.js` (a new `"limiter-processor"` AudioWorklet — the first in this codebase) ports the same algorithm as a genuinely causal streaming processor: linked cross-channel 4x-oversampled detection (reusing `masteringProfiles.ts`'s existing `_upsampleTruePeak4x` kernel, not `limiter.py`'s exact BS.1770 FIR — Tier 2), a monotonic-deque sliding-window minimum (the streaming equivalent of `limiter.py`'s single `scipy.ndimage.minimum_filter1d` call) covering both the look-ahead window and the FIR-kernel dilation margin in one pass, then the same fast-attack/slow-release smoothing. Unlike the offline backend (which needs no output latency — see `limiter.py`'s module docstring), the worklet is genuinely real-time and therefore introduces a real, constant ~5ms delay line while active — a normal, expected cost of real-time look-ahead limiting. Wired into `initialize()` (`useStemPreview.ts`) in place of `nativeSoftLimitNode`, with a same-tanh-WaveShaper fallback if `audioWorklet.addModule` fails to load. `LIMITER_LOOKAHEAD_MS`/`LIMITER_RELEASE_MS` (`masteringProfiles.ts`) are Tier 1, added to `contract_signature()`/`contractSignature()` (§4). The binaural/stereo-downmix monitoring path (`softLimitNode`, shared) deliberately keeps the plain tanh `soft_limit` unchanged — it mirrors `render_binaural_delivery`'s own untouched `soft_limit` call and the CLI downmix path's scalar True-Peak correction (`pipeline.py`/`stem_pipeline.py`), neither of which Phase 1.1 touched. Not yet exercised by the golden diff (§5): the native path isn't in that harness's scope at all today (only the bed EQ/comp/bass stage and the binaural-collapse stage are), so this AudioWorkletNode's behavior is covered by its own manual validation (impulse/near-Nyquist-tone/dense-noise synthetic signals, causal-vs-whole-buffer equivalence) during development and by `useStemPreview.test.tsx`'s wiring tests, not a cross-engine signal-level diff — extending the golden harness to the native path is future work, same open item `test_preview_export_golden.py`'s scope note already flags for the bed-level limiter generally. |

| D15 | New feature, not a discrepancy: the Spatial Audio Engine gained a second delivery target, `transaural` (crosstalk-cancelled stereo speaker playback), alongside the existing `binaural` (headphone) target — see `transaural_speakers.md`. Recorded here because the parity mechanism this contract defines had to be extended to a whole new stage, not just a new constant. | Implemented core↔web in one pass, no gap opened. Core: `upmixer/crosstalk/` (renderer reuses `render_binaural(profile="flat")` + a new 2x2 XTC FIR matrix + the existing `apply_voicing`), `scripts/build_crosstalk_filters.py` bakes the XTC filter assets from the same parametric head model (`upmixer/binaural/head_model.py`, promoted out of the binaural build script so both targets share one model) with a frequency-dependent Tikhonov-regularized inverse. Web: `buildCrosstalkGraph` (`previewGraph.ts`) reuses `buildBinauralGraph("flat")` internally, adds a 4-convolver 2x2 XTC matrix, then the shared `buildVoicingChain` — wired as a fourth parallel gated bus in `initialize()`/`applyOutputMode` (`audioEngine.ts`) alongside binaural/stereo/native, same "always built, gate picks which reaches the destination" pattern the other three already use. `CROSSTALK_LOUDNESS_MAX_GAIN_DB` added to both `canonical_constants()`/`canonicalConstants()` (§3, §4). One accepted cost from reusing the always-eager-build pattern: the crosstalk graph's internal "flat" HRIR decode now fetches unconditionally on every `initialize()`, regardless of `outputMode` (previously only the selected `spatialProfile`'s 4-part set fetched) — instant switching into transaural mode was judged worth this, matching how binaural/stereo/native are already all eagerly built today. Not yet covered by the golden-diff harness (§5) — same open item as binaural's Listening profile (D10): only a hand-rolled objective crosstalk-suppression/coloration check (`tests/test_crosstalk.py`) exists on the Python side so far, no cross-engine render comparison. |

| D16 | Not a discrepancy: the Tier-1 tunable DSP constants (§3) were hand-mirrored in `masteringProfiles.ts` and kept honest by a live value cross-check (`contract.py`/`contract.ts` → `web_constants.json` fixture → `test_contract_parity.py`), so every tuning change was a two-sided edit plus a fixture re-dump. Reducing that duplication ahead of a possible future Rust port. | Made core the single owner: `apps/api` `engine_constants()` (`features/system/service.py`) reads the values straight from their core source modules and serves them as the `constants` block of `GET /api/v1/configuration`. The web fetches them once at bootstrap, normalizes via `resolveEngineConstants` (`masteringProfiles.ts`) into an `EngineConstants` object, and threads it into the graph builders (`previewGraph.ts`/`audioEngine.ts`); the value literals and the whole cross-check (`contract.py`, `contract.ts`, `dump-constants.mjs`, the fixture, `test_contract_parity.py`) were deleted — nothing to diff when there is one source. Scope: the tunable acoustic constants only (comp/bass profiles, gains, cutoffs, haas, loudness ceilings, limiter times, binaural + transaural voicing params). Kept web-local (never drift): structural/math constants (`BUTTERWORTH_Q`, ambisonic order, ACN-12 1/√7, true-peak kernel) and asset filenames. Parity of these constants is now structural (one source); the end-to-end golden render diff (§5) still guards actual output, fed the shared test fixture `engineConstants.fixture.ts`. See §4. |

| D17 | Not a discrepancy: extending D16's served-constants pattern. Four profile→asset-filename maps (`EQ_FIR_ASSETS`, `STEM_EQ_FIR_ASSETS`, `DECODE_FILTER_SET`, `XTC_FILTER_SET`) were still hand-mirrored in `masteringProfiles.ts`. `DECODE_FILTER_SET`/`XTC_FILTER_SET` already existed identically in core (`binaural/profiles.py`, `crosstalk/profiles.py`), and the `master_`/`stem_` EQ naming was duplicated between the TS literals and `scripts/build_eq_filters.py` — so a served name could silently drift from the shipped WAVs. | Made core the single owner. The `master_`/`stem_` naming is now centralized as `EQ_FIR_ASSETS` (`mastering/eq.py`) / `STEM_EQ_FIR_ASSETS` (`separation/stem_eq.py`), which `build_eq_filters.py` and `engine_constants()` both consume — the build script and the endpoint can no longer disagree. `engine_constants()` serves all four maps (the decode/XTC ones keyed by enum `.value`); the web drops the literals and reads `EngineConstants.eqFirAssets`/`.stemEqFirAssets`/`.decodeFilterSet`/`.xtcFilterSet` (fixture `engineConstants.fixture.ts` carries them for the golden harness). The physical WAVs stay web-side under `apps/web/public/{eq_fir,hrir,xtc}` — only the names moved. New backend tests: `test_configuration_serves_filter_asset_maps` (served == core sources) and `test_served_filter_assets_have_shipped_wavs` (every served basename has a shipped WAV — the safety net replacing the old hand-sync). See §4. |

Add new rows here when a discrepancy is found; do not delete resolved rows,
mark them fixed so the history of what was found and corrected stays
visible.
