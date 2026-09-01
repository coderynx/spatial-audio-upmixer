# Web UI Controls

Component-level contracts for `apps/web`. Read [UI design](web_ui_design.md)
first.

## General controls

- Prefer an existing UI primitive. Do not introduce a library for a small
  local interaction.
- Controls use sentence-case labels. Add explanatory text only for a real
  constraint; keep normal labels self-explanatory.
- `SegmentedControl` defaults to `h-7`, card active pill, and cross-fade.
  Use the project stage variant only for the workflow selector.

## Pots, sliders, and faders

`Pot` is for compact values that benefit from rotary precision; sliders are
for linear/range values. Both support pointer drag, keyboard arrows/page,
Home/End, and double-click reset. Wheel input acts only while focused.

`Fader` is the vertical mixer-strip control. `HorizontalFader` is the compact
row/transport control. They share the value but not the visual form. The
horizontal fader's `knobSize` also defines its track height; it has either a
value fill or live meter bars, never a separate thickness setting.

All level views import the dB mapping and zones from `lib/meterScale.ts`.
Never reimplement a meter scale. Use the single-channel yellow floor for a
mono meter and the multi-channel floor for a multi-channel meter. A live meter
shows source level independently of fader position.

## Channel strips and mixer

- `ChannelStrip` is selectable across its root; its nameplate remains a button
  for keyboard use. A second visible instance needs a distinct `subjectName`.
- Strip meter loops use `useStripMeterLoop` and `createMeterState`; peak hold
  follows the smoothed RMS bar, not instantaneous sample peak.
- Mute is `destructive`, solo is `warning`, and a strip silenced by another
  solo has distinct text, not only dimming.
- The monitor strip is separated by a 2px rule and controls monitor gain, not
  exported audio. Gain reduction sits beside its level meter.
- Each strip has independent persisted width. Use `StripResizeHandle` at its
  trailing border; it supports drag, keyboard, and double-click reset.
- The rack scrolls horizontally when needed; strips do not shrink.

Source anchor is an accented blend control, not a stem strip. Bed trim has a
fader but no meter or mute/solo. Both remain visually distinct from regular
stems.

## Project header and transport

Project identity and stage selector share the global header. Header and
transport use a three-column grid:

```
minmax(0, 1fr) auto minmax(0, 1fr)
```

This keeps the centre pod truly centred as side content changes. Stage tabs use
the `fill`/sliding primary-pill variant; Project settings is a separate
one-segment control. Hide header refresh/create actions on project-detail
routes so the workflow has room.

Show a stage-specific A/B only when it can act. The transport's leading slot
is the TrackRail reveal toggle. Put portable project download in Settings, not
the header; Delivery export creates rendered audio. Do not re-add the raw JSON
project editor to the project page.

## Track rail, timeline, and loudness

`TrackRail` is a two-level track → layout tree. The selected value is
`{ trackId, layout }`, reconciled while rendering. A track click retains its
selected layout. Layout rows are the selection target and carry
`aria-current="true"`; editing a track's layout set happens in Prepare.

Timeline rows are `64px`, with a `22px` ruler and `280px` sticky header.
The row's drag starts only from its grip so fader interaction is unaffected.
The whole project fits the timeline width; do not add horizontal zoom without
revisiting the rendering and layout contract.

Loudness is text above displays, updated from meter refs at about 10 Hz. Each
cell exposes a unit and tooltip. Out-of-spec values are `warning`; A/B match
gain appears only while a relevant bypass is active.
