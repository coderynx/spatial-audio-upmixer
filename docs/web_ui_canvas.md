# Web UI Canvas

Canvas contracts for `apps/web`. Read [UI design](web_ui_design.md) first.

## Instrument displays

`HazeView`, `ElevationView`, `StereoPanoramaView`, and `ChannelMeters` are
instrument displays. They use `lib/canvasTheme.ts` and remain dark in both app
themes. Add their literals there, not inline.

`spatialCanvas.ts` owns the lifecycle shared by the three spatial instruments
and `SceneView`: device-pixel sizing, resize coalescing, frame scheduling,
wake, and post-playback settling. Each view supplies its projection, drawing,
and speaker hit targets; it must not recreate that lifecycle.

Use the shared `plotField` → `plotFieldCore` field gradient and a faint blue
wash across the full canvas, never only the padded plot area. Motion trails
paint the field with alpha. Keep the wash low enough that stem hues remain
distinct; verify with pixel sampling.

`meterScale.ts` owns `levelToDb`, `dbToY`, thresholds, and peak-hold state.
Hosts may select their tick density and `MeterPalette`, but not a different dB
mapping, zone threshold, or peak rule. `ChannelMeters` uses the blue Level
Meter palette; strip and compact faders use the green/yellow strip palette.

## Themed working canvases

Timeline lanes and mixer-strip meters are normal application surfaces that
happen to draw with canvas. Read resolved CSS tokens through `useThemeTokens`;
do not put chrome colours into `canvasTheme.ts`. Fixed meter zone colours are
the exception because their colour has audio meaning.

Timeline rules:

- Draw region blocks and mirrored min/max envelopes, not bare traces.
- A muted region is dimmed; a non-selected region dims when another is
  selected. Preserve transients by taking each display column's extrema.
- Render the playhead on a cheap overlay canvas driven by a ref/rAF loop.
  Lanes redraw only when their data or presentation changes.
- Envelopes come precomputed from the API. State missing/backfill conditions
  explicitly; do not decode audio in the browser to create them.

`HazeView`, `ElevationView`, `StereoPanoramaView`, `ChannelMeters`,
`TimelineView`, `MixerView`, and `Transport` are memoized. Pass stable props so
playback does not re-render the page.

## Verification

Canvas updates must be checked in both themes. Sample pixels in a live render
when changing gradients, meter zones, or stem hues; a downscaled screenshot is
not sufficient evidence.
