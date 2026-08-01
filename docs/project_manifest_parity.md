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

The full, current list of canonical processing paths is whatever
`GET /api/v1/configuration`'s `manifest_parameters` returns (also mirrored by
`upmixer --manifest-keys`) — that response, not this document, is the
source of truth, so it is not re-enumerated here. Representative paths per
group: `engine.stems`/`engine.stem_silence_skip` (stem separation),
`format.type`/`format.binaural.profile`/`format.transaural.profile`
(delivery), `mixing.channel_layout`/`mixing.stem_routing` (spatial mix),
`routing.center_gain`/`routing.lfe_gain` (channel-group gains),
`processing.preview`/`processing.fft_size` (analysis), and
`mastering.loudness.target`/`mastering.compressor.profile`/
`mastering.match_reference.strength` (mastering chain).

## Parity Matrix

| Manifest group | Project representation | Serialization/job behavior | Severity | Decision |
|---|---|---|---|---|
| `engine.mode` | Derived stem-only project | Always `stem` | Low | Project behavior |
| `engine.stems` | Create/expand targets; track Advanced subset | Canonical requested/prepared stems | Medium | Unified |
| Separation tuning | Advanced JSON; rebuild on change | Rebuilds the project's stem store before export | High before change | Manifest behavior |
| `mixing.stem_*` | Stem controls and Advanced JSON | Direct manifest mapping | None | Manifest behavior |
| `mixing.spatial` / `routing.content_mix_strength` | Derived explicit-routing profile | Forced deterministic project values | Medium | Project behavior |
| `mixing.stem_routing` | Position sliders, presets, Advanced matrix | Exact speaker matrix | None | Project behavior for UX |
| `routing.*` | Advanced JSON | Direct manifest mapping | High before change | Manifest behavior |
| `mastering.*` | Mastering tab and reference upload | Exported job receives trusted reference | High before change | Unified |
| `format.*` | Delivery controls and Advanced JSON | Direct mapping | None | Manifest behavior |
| `format.binaural.*` | "Container" select on the Delivery tab gains a `binaural` option (disabled unless `channel_layout` is one of the binaural bed layouts) + profile select; in-preview Spatial Audio Engine picker mirrors the project value but is session-only | `type: binaural` renders `channel_layout`'s own bed through the profile's decode+voicing chain to stereo, in place of the plain multichannel bed (see [Spatial Audio Engine](standards/spatial_audio_engine.md)); routing/preview UI always keys off `channel_layout` directly | Medium | Unified |
| `format.transaural.*` | "Container" select gains a `transaural` option (disabled unless `channel_layout` is one of the transaural bed layouts) + profile select; in-preview picker (Speakers row) mirrors the project value but is session-only, same pattern as `format.binaural.*` | `type: transaural` renders `channel_layout`'s own bed through the profile's crosstalk-cancellation+voicing chain to stereo (see [Transaural Speaker Rendering](standards/transaural_speakers.md)); mutually exclusive with `format.binaural.*` (one `type` field) | Medium | Unified |
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
separation tuning; those are project-wide so the project's stem store and its
exports remain identical.

Position sliders intentionally collapse an arbitrary speaker matrix to front/back
and floor/height. Use Advanced JSON for asymmetric or per-channel routing. LFE
is excluded from stereo downmixes under ITU-R BS.775; `0.7071` is default
surround coefficient and `0.5` is available for dense rear content.

## Follow-ups

- Add UI metadata editor if manifest metadata needs authoring beyond Advanced JSON.
- Add project-specific separation tuning controls if Advanced JSON proves too technical.
- Keep parameter catalog and this matrix updated whenever a manifest field changes.
