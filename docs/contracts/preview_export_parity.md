# Preview ↔ Export Parity Contract

**Scope:** every DSP stage the web preview re-implements from the core
export pipeline: stem routing, spatial bed construction, mastering chain,
and (for the binaural output mode) the Spatial Audio Engine. Read
[`README.md`](README.md) first for the change protocol this contract is
bound by. The binaural-specific geometry/ambisonic/decode-filter/voicing
contract lives in
[`../standards/spatial_audio_engine.md`](../standards/spatial_audio_engine.md)
and is cross-referenced, not repeated, below.

---

## 1. Pipeline map — Python stage ↔ TypeScript stage

| Stage | Python (export) | TypeScript (preview) |
|---|---|---|
| Per-stem EQ (pre-routing) | `upmixer/separation/stem_eq.py::StemEQ` | `STEM_EQ_FIR_ASSETS` + `buildFirEqNode` in `masteringProfiles.ts`, wired in `useStemPreview.ts` |
| Stem → speaker-bed routing | `upmixer/separation/stem_router.py::StemRouter.route` | `createStemSends` in `useStemPreview.ts`, using `channelGroupGain`/`buildSurroundSend`/`buildHeightSend`/`buildDiffuseSend` from `masteringProfiles.ts` |
| Multichannel channel derivation (non-stem path) | `upmixer/upmix/multichannel.py::MultichannelUpmixer` | Not previewed — multichannel pass-through input has no stem-preview path |
| Ambisonic encode (order-3 ACN/N3D) | `upmixer/binaural/ambisonics.py::encode_gains` | `createPositionalEncoder` (wraps `AmbiMonoEncoder`/JSAmbisonics) + `ACN12_N3D_CORRECTION` in `previewGraph.ts`, called per speaker from `useStemPreview.ts` — see `spatial_audio_engine.md` §3 |
| Virtual-loudspeaker geometry | `upmixer/binaural/geometry.py` | `web/src/lib/spatial.ts::speakerCoordinates` — see `spatial_audio_engine.md` §2 |
| HOA decode → binaural | `upmixer/binaural/decoder.py` | Per-ACN `ConvolverNode` bank in `buildBinauralGraph` (`previewGraph.ts`), called from `initialize()` in `useStemPreview.ts` — see `spatial_audio_engine.md` §4 and **Ledger D10** |
| Binaural voicing chain | `upmixer/binaural/voicing.py::apply_voicing` | `buildVoicingChain`/`applyVoicingParams` in `masteringProfiles.ts`, wired inside `buildBinauralGraph` (`previewGraph.ts`) — see `spatial_audio_engine.md` §5 |
| Spectral (mastering) EQ | `upmixer/mastering/eq.py::SpectralShaper` | `EQ_FIR_ASSETS` + `buildFirEqNode` in `masteringProfiles.ts` (same asset scheme as stem EQ, applied post-routing) |
| Bus compression | `upmixer/mastering/compressor.py::BusCompressor` | Linked `DynamicsCompressorNode` detector + polled `.reduction` in `buildMasteringTopology` (`useStemPreview.ts`) |
| Bass control | `upmixer/mastering/bass.py::BassController` | Bass shelves/exciter/mono-maker in `buildMasteringTopology` (**Ledger D5**) |
| BS.1770 loudness normalization | `upmixer/loudness.py::normalize_loudness` (bed) / `render_binaural_delivery`'s own pass (collapse) | `measureOutputLoudness`/`loudnessGainFor` (`useStemPreview.ts`) — approximate, see **Tier 3**; the collapse-stage pass is now golden-diff-covered, see **Ledger D10** |
| Soft limiting (last) | `upmixer/utils.py::soft_limit` | `buildSoftLimitCurve` (`masteringProfiles.ts`), applied last in `buildMasteringTopology` |
| ITU-R BS.775 stereo downmix | `upmixer/utils.py::itu_downmix_stereo` | `STEREO_DOWNMIX_GAINS` + `applyOutputMode` (`useStemPreview.ts`) |

Both `UpmixPipeline` and `StemUpmixPipeline` share one `MasteringChain`
instance (`upmixer/mastering/chain.py`) and one `render_binaural_delivery`
call, so the export side of every row above is centralized in core; the
preview side is the parity-critical surface.

**Processing order is itself contracted** (Tier 1): reference match → EQ →
compression → bass control → BS.1770 loudness → soft-limit *last*. Soft
limiting after loudness (not before) is deliberate — see
`upmixer/mastering/chain.py`'s module docstring — and the preview graph
must build its nodes in the same order.

---

## 2. Parity tiers

- **Tier 1 — bit-for-bit.** Scalar constants, tables, and file assets: both
  sides must carry the exact same numeric value or byte-identical file.
  Drift here is always a bug. Covered by the signature mechanism (§4).
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

---

## 3. Canonical constants catalog

One row per constant shared between export and preview. "TS" column gives
the export/mirror symbol in `web/src/features/projects/masteringProfiles.ts`
unless noted. All verified in sync as of this contract's signature (§4).

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
| Soft-limit threshold | 0.95 | `peak_limit_threshold` | `SOFT_LIMIT_THRESHOLD` | 1 |
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
| True-peak oversample | 4× (≤48 kHz), 2× (96 kHz) | 3 (not ported to preview) |

The preview's `measureOutputLoudness` (`useStemPreview.ts`) has no
K-weighting or gating, and measures a single ~1s window near playback start
rather than the whole track — see **Tier 3 tolerance** in §5. This is the
largest bounded gap in the contract; tightening it (full BS.1770 in-browser,
whole-file measurement) would move these rows to Tier 1/2.

### Binaural — see `spatial_audio_engine.md` for the full geometry/SH/decode-filter/voicing table. Cross-cutting constant repeated here because it also gates delivery gain-staging:

| Constant | Value | Python | TS | Tier |
|---|---|---|---|---|
| Binaural collapse loudness ceiling | 6.0 dB | `BINAURAL_LOUDNESS_MAX_GAIN_DB` (`upmixer/binaural/renderer.py`) | `BINAURAL_LOUDNESS_MAX_GAIN_DB` | 1 |

### Channel layouts / formats — `upmixer/formats.py`

Out of scope for `contract_signature()` (§4): the web has no static copy of
these to hash against, only a runtime fetch. Listed here for completeness,
parity enforced by the separate mechanism in `docs/project_manifest_parity.md`.

| Constant | Value | How web gets it |
|---|---|---|
| `BINAURAL_BED_FORMATS` | `("5.1.4", "7.1.2", "7.1.4")` | `GET /api/v1/configuration` |
| `SURROUND_714` channel order | FL, FR, C, LFE, **BL, BR, SL, SR**, TFL, TFR, TBL, TBR (back before side — differs from 7.1/7.1.2 order) | `GET /api/v1/configuration` `layout_channels["7.1.4"]`; `ProjectDetailPage.tsx` has a hardcoded 7.1.4 fallback if config is unavailable — keep that fallback in sync by hand, it is not covered by any automated check |
| Per-layout channel sets | `FORMAT_MAP` | `GET /api/v1/configuration` `layout_channels` |

---

## 4. Contract signature

`upmixer/contract.py::contract_signature()` and `web/src/lib/
contract.ts::contractSignature()` build a normalized structure from the
Tier-1 constants above (imported from their real source modules, never
re-typed) and hash it (sha256 over sorted-key JSON).

```
PINNED SIGNATURE: d2f9e35ab9fa080f4c6b74130980d837279f7baeaf62d8c73dfbbc06678893c8
```

`tests/test_contract_parity.py` and `web/src/lib/contract.test.ts` each
assert their computed signature equals the pinned value above.

### Regenerating the signature

After a deliberate, both-sides-updated change to any Tier-1 constant:

```bash
python3 -c "from upmixer.contract import contract_signature; print(contract_signature())"
```

Paste the new value into the `PINNED SIGNATURE` line above, then update the
constants catalog (§3) to describe the new value, then re-run both
signature tests and the golden render diff (§5).

---

## 5. Golden-render tolerance thresholds

`tests/test_preview_export_golden.py` (marked `@pytest.mark.perf`, opt-in)
renders a fixed deterministic input through both engines at the same output
layout/profile and compares:

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
  tolerances above. Run `npm run golden:render` (from `web/`) to regenerate
  it, then `python3 -m pytest tests/test_preview_export_golden.py -m perf`.

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
profile's voicing chain is all-zero/identity, which is why this diff doesn't
surface Ledger D11 (LFE added before vs. after voicing) — see that entry.

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
| D10 | The golden diff (§5) covered only the EQ/compressor/bass mastering-chain stage. BS.1770 loudness normalization, the final soft-limit, and the binaural/ambisonic collapse were not exercised by any cross-engine render comparison — confirmed as a real, non-trivial gap by a manual export/preview A/B on a real track (not automated, see D4), which found deltas well outside this contract's tolerances. | Fixed for the binaural-collapse loudness/soft-limit stage — `buildBinauralGraph` (`previewGraph.ts`, extracted from `useStemPreview.ts`'s `initialize()`) + the binaural stage `render-preview-golden.mjs` adds (ambisonic encode → HOA decode → voicing → one-shot loudness gain → real oversampled soft-limit) + `test_python_binaural_metrics_golden`/`test_cross_engine_binaural_golden_diff` (mirroring `render_binaural_delivery`) now cover this at the Studio profile, passing on real measured LKFS/true-peak/per-ear-RMS numbers. Still open: (1) the bed-level loudness/soft-limit stage (before binaural collapse) remains untested, since the preview doesn't implement it at the bed level at all; (2) the 1/3-octave spectral-difference check has no test yet — see the Implementation status note above; (3) only the Studio profile is exercised, so Listening's non-identity voicing chain (and Ledger D11) isn't. |
| D11 | The live preview adds LFE to the binaural mix *after* the voicing chain (at `mergePoint`, alongside the gated binaural/stereo signal), but the backend's `render_binaural` adds LFE to left/right *before* `apply_voicing` runs. At the Studio/Flat profiles this is numerically inert (voicing is all-zero/identity there), which is why building the D10 harness at Studio profile didn't surface it — but at the Listening profile (the only one with a non-identity voicing chain: crossfeed, shelves, presence, widen), LFE would be crossfed/shelved/widened in the backend and would not be in the preview, a real signal difference for any content with LFE energy. Separately, the web's `mergePoint` LFE addition also applies uniformly to the *stereo* (BS.775 downmix) output mode, whereas BS.775 excludes LFE from the downmix entirely — a second, related gap for that mode. | Open — found while extracting `buildBinauralGraph` for D10; not fixed here to keep that extraction's diff minimal and low-risk (it's a live-hook refactor, not just new test code). Fixing means either moving the live hook's LFE add to before `buildBinauralGraph`'s voicing stage for binaural mode, or excluding it entirely for stereo mode — no golden-diff coverage exists for the Listening profile or stereo-downmix LFE handling yet to verify a fix against. |

Add new rows here when a discrepancy is found; do not delete resolved rows,
mark them fixed so the history of what was found and corrected stays
visible.
