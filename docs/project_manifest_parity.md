# Project and Manifest Parity

## Contract

Projects are interactive manifest authors. Exports create ordinary jobs from an
immutable manifest snapshot. CLI/API clients may skip projects and submit the
same manifest blocks with their own assets.

`GET /api/v1/configuration` exposes `manifest_parameters`, the canonical
machine-readable list of all processing parameters. It includes path, type,
default, and asset-override support. `upmixer --manifest-keys` shows those
same dotted paths.

## Project Parameters

Project input is import, name, requested stems, output layout, project manifest,
project scene, optional mastering reference, and per-track manifest/scene
overrides. Editor controls cover routing preset/intensity, explicit stem speaker
matrix through front/back and floor/height sliders, stem mute/solo/gain/EQ,
source anchor, mastering, delivery, and Advanced JSON.

Stored project parameters: `name`, `import_id`, `requested_stems`, `scene`,
project `manifest`, `mastering_reference_id`, track `manifest_overrides`, and
track `scene_overrides`. Derived state is `prepared_stems`, stem generation,
revision, status/progress/error, tracks, previews, stems, and export history.

Routing preset and editor scope are UI-only authoring controls. Presets serialize
their resulting `mixing.stem_routing`; they are not runtime job parameters.

## Manifest Parameters

Structural fields are `version`, `metadata`, and `assets`. Processing fields are
the canonical paths returned by `manifest_parameters`: `engine.*`, `mixing.*`,
`routing.*`, `format.*`, `mastering.*`, and `processing.*`. Assets may override
every processing block. Their `input`, `output`, directory expansion, and cache
path fields are automation-only.

Canonical processing paths are:

- `engine.mode`, `engine.stem_model_dir`, `engine.input_format`,
  `engine.stem_cache_dir`, `engine.stem_batch_size`,
  `engine.stem_segment_size`, `engine.stem_chunk_duration_s`,
  `engine.stem_model_cache_size`, `engine.stems`, `engine.stem_silence_skip`,
  `engine.stem_silence_threshold_db`, `engine.stem_silence_min_duration_s`,
  `engine.stem_silence_crossfade_ms`, `engine.stem_silence_pad_ms`.
- `format.type`, `format.subtype`, `format.sample_rate`,
  `format.downmix.enabled`, `format.downmix.output`,
  `format.downmix.surround_coeff`.
- `mixing.channel_layout`, `mixing.stem_rebalance`, `mixing.stem_eq`,
  `mixing.stem_routing`, `mixing.stem_enabled`, `mixing.stem_solo`,
  `mixing.stem_source_anchor_strength`, `mixing.spatial.profile`,
  `mixing.spatial.intensity`, `mixing.spatial.preanalyze`, `mixing.stems`.
- `routing.center_gain`, `routing.surround_gain`, `routing.back_gain`,
  `routing.height_gain`, `routing.lfe_gain`, `routing.lfe_cutoff`,
  `routing.center_extraction_gain`, `routing.center_attenuation`,
  `routing.height_low_rolloff_gain`, `routing.height_high_shelf_gain`,
  `routing.content_mix_strength`, `routing.content_hf_analysis_hz`.
- `processing.preview`, `processing.preview_duration`, `processing.preview_start`,
  `processing.fft_size`, `processing.block_size`, `processing.normalize_output`.
- `mastering.loudness.normalize`, `mastering.loudness.target`,
  `mastering.loudness.max_tp`, `mastering.eq.profile`,
  `mastering.eq.strength`, `mastering.compressor.profile`,
  `mastering.compressor.threshold_db`, `mastering.compressor.ratio`,
  `mastering.compressor.attack_ms`, `mastering.compressor.release_ms`,
  `mastering.compressor.knee_db`, `mastering.compressor.makeup_db`,
  `mastering.bass.profile`, `mastering.bass.sub_gain_db`,
  `mastering.bass.mid_gain_db`, `mastering.bass.mono_cutoff_hz`,
  `mastering.bass.excite`, `mastering.bass.lfe_gain_db`,
  `mastering.match_reference.path`, `mastering.match_reference.strength`,
  `mastering.match_reference.spectrum`, `mastering.match_reference.rms`, and
  `mastering.match_reference.max_db`.

## Parity Matrix

| Manifest group | Project representation | Serialization/job behavior | Severity | Decision |
|---|---|---|---|---|
| `engine.mode` | Derived stem-only project | Always `stem` | Low | Project behavior |
| `engine.stems` | Create/expand targets; track Advanced subset | Canonical requested/prepared stems | Medium | Unified |
| Separation tuning | Advanced JSON; rebuild on change | Rebuilds preview cache before export | High before change | Manifest behavior |
| `mixing.stem_*` | Stem controls and Advanced JSON | Direct manifest mapping | None | Manifest behavior |
| `mixing.spatial` / `routing.content_mix_strength` | Derived explicit-routing profile | Forced deterministic project values | Medium | Project behavior |
| `mixing.stem_routing` | Position sliders, presets, Advanced matrix | Exact speaker matrix | None | Project behavior for UX |
| `routing.*` | Advanced JSON | Direct manifest mapping | High before change | Manifest behavior |
| `mastering.*` | Mastering tab and reference upload | Exported job receives trusted reference | High before change | Unified |
| `format.*` | Delivery controls and Advanced JSON | Direct mapping | None | Manifest behavior |
| `format.downmix` | Delivery toggle/coefficient | Server-managed companion artifact | High before change | Unified |
| `processing.preview*` | Unsupported | Projects use browser audition; exports full render | Low | Project behavior |
| Asset paths/cache/model paths | Server-managed | Injected by web worker | Low | Explicitly unsupported |
| Per-asset blocks | Track Advanced overrides | Deep-merged into export snapshot | High before change | Unified |

## Validation and UX

Known manifest fields are strict: exact types, finite numbers, choices, core
minimum/maximum constraints, valid stems/channels, and ADM-BWF constraints.
Registered extensions remain valid; underscore-prefixed comment fields remain
allowed. Advanced JSON is validated by the server on save.

Track overrides may set all post-separation blocks and may select a subset of
already prepared stems. They cannot change project mode, server paths, or
separation tuning; those are project-wide so preview cache and exports remain
identical.

Position sliders intentionally collapse an arbitrary speaker matrix to front/back
and floor/height. Use Advanced JSON for asymmetric or per-channel routing. LFE
is excluded from stereo downmixes under ITU-R BS.775; `0.7071` is default
surround coefficient and `0.5` is available for dense rear content.

## Follow-ups

- Add UI metadata editor if manifest metadata needs authoring beyond Advanced JSON.
- Add project-specific separation tuning controls if Advanced JSON proves too technical.
- Keep parameter catalog and this matrix updated whenever a manifest field changes.
