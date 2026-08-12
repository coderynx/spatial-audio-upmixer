# Web UI Design Specification

Binding reference for `apps/web/`. Any change that adds a page, a control, or a
visual state follows this document. It records the decisions already
implemented, so new work stays coherent with what exists rather than
re-deriving a look per feature.

The target is the Apple pro-app idiom used by Logic Pro and Final Cut Pro for
iPad: Apple system colours, Apple radii, dense controls, and a full-viewport
panel anatomy. Both light and dark are first-class — neither is an
afterthought skin of the other.

Scope is presentation only. `docs/web_architecture.md` governs the delivery
boundary; the no-core-change-for-a-web-visual-concern and
`canvasTheme.ts`-is-the-only-literal-colour rules live in `apps/web/AGENTS.md`
and still hold here.

## 1. Principles

1. **No dead space.** Every region of the viewport carries information or
   controls. A page that renders a centered column with empty margins is
   wrong regardless of how it is styled.
2. **The viewport is the page.** Content fills exactly one screen and never
   scrolls at page level. Individual regions scroll themselves.
3. **Panels butt, they do not float.** Regions are separated by 1px rules,
   not by gutters and drop shadows. Floating is reserved for genuinely
   transient surfaces (dialogs, overlay chips).
4. **Separators over shadows.** Elevation is expressed by a border and a fill
   change. `shadow-sm` appears only on a raised segmented-control segment.
5. **Density over padding.** Controls are 24–28px tall with 11–13px type.
   Adding whitespace is not how to make something readable.
6. **Semantic colour only.** Colour states carry meaning (success / warning /
   destructive / primary). Decorative colour is confined to stem identity and
   the canvas displays.

## 2. Tokens

All colours come from CSS custom properties in `apps/web/src/index.css`, consumed
through the Tailwind mappings in `apps/web/tailwind.config.ts`. Never write a
literal colour in a component — the only sanctioned literals live in two
modules:

- `apps/web/src/lib/canvasTheme.ts` (§7) — the instrument-display palette.
- `apps/web/src/lib/stems.ts` — `stemColors`, the per-stem identity hues. §1.6
  permits stem identity as decorative colour, and these are consumed through
  `getStemColor` by both canvas surfaces (Haze, Elevation, Timeline lanes) and
  DOM chrome (stem rows, mixer nameplates). They are Tailwind-palette hues
  rather than Apple system colours on purpose: they must stay mutually
  distinguishable across a dozen stems, which the small system palette cannot
  do. Add a new stem's colour there, never inline.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `background` | `#F2F2F7` | `#131316` | Workspace substrate |
| `card` | `#FFFFFF` | `#1C1C1E` | Chrome: toolbars, rails, panels, status bar |
| `popover` | `#FFFFFF` | `#2C2C2E` | Menus, dropdowns |
| `muted` | `240 12% 94%` | `#2C2C2E` | Recessed tracks (segmented control) |
| `secondary` | `240 8% 91%` | `#3A3A3C` | Control rest state, filled inputs |
| `accent` | `240 8% 88%` | `#48484A` | Control hover |
| `border` / `input` | `240 2% 82%` | `#38383A` | Every separator and field edge |
| `foreground` | `#000000` | `#FFFFFF` | Primary label |
| `muted-foreground` | `240 2% 45%` | `#98989E` | Secondary label, section headers |
| `primary` | `#007AFF` | `#0A84FF` | systemBlue — selection, primary action |
| `destructive` | `#FF3B30` | `#FF453A` | systemRed — failure, mute, delete |
| `success` | `#34C759` | `#30D158` | systemGreen — completion, play, toggle on |
| `warning` | `#FF9500` | `#FF9F0A` | systemOrange — attention, solo, loop |
| `ring` | `#007AFF` | `#0A84FF` | Focus ring |

Dark values are Apple's named system colours, with one deliberate exception:
`background` is a `#131316` charcoal rather than Apple's pure-black
systemBackground. Pure black made the workspace substrate read as a hole
behind the chrome instead of a surface under it. The light greys between
`background` and `border` are not named Apple colours, so they are listed as
the HSL triples that `index.css` actually declares.

**No pure black anywhere in chrome.** The dark surface order is
`background` (#131316) < `card` (#1C1C1E) < `popover`/`muted` (#2C2C2E) <
`secondary` (#3A3A3C). Canvas displays sit below all of it at `plotField`
(#070E17), which is what makes them recess. The only near-black literals that
remain are instrument displays — the canvas field and the transport LCD —
plus the dialog scrim.

Layout properties:

- `--radius: 0.625rem` (10px). Tailwind derives `rounded-lg` 10px,
  `rounded-md` 8px, `rounded-sm` 6px. A 5px inner radius is used only for the
  segment inside a segmented control.
- `--topbar-h: 2.75rem` (44px). Reference the variable; never hardcode 44px.
- Font stack leads with `-apple-system, BlinkMacSystemFont, "SF Pro Text"` so
  SF Pro renders on Apple platforms, with Inter as the cross-platform
  fallback.

### Adding a token

Add it to both `:root` and `.dark` in `index.css` **and** to
`extend.colors` in `tailwind.config.ts`. A token defined in only one palette
is a bug — dark-only styling was explicitly rejected.

## 3. Type scale

| Size | Weight | Use |
| --- | --- | --- |
| `text-[11px]` | 500–600 | Section headers (uppercase, `tracking-[.08em]`), status bar, badges, `Label` |
| `text-xs` (12px) | 400–500 | Inspector rows, table cells, secondary copy |
| `text-[13px]` | 400–500 | Default control text, buttons, inputs, nav rows |
| `text-sm` (14px) | 400 | Body copy in dialogs |
| `text-base` / `text-lg` | 600 | Card and dialog titles, metric values |

Uppercase is for **section headers only** — `PanelHeader`, `InspectorGroup`
titles, `StatusCell` labels, `MetricStrip` labels. Control labels stay
sentence case; Apple does not shout at its own form fields.

Numeric values that update or align in columns take `tabular-nums`.

## 4. Page anatomy

Every routed page composes `Workspace` from `apps/web/src/app/Workspace.tsx`:

```
┌──────────────────────────── top bar (AppShell, 44px) ────────────────────┐
├─────────┬───────────────── toolbar (44px, optional) ────────────────────┤
│         ├──────────────────────────────────┬──────────────────────────┤
│  rail   │            canvas                │        inspector         │
│ (240px) │       (fluid, min-w-0)           │         (320px)          │
│         ├──────────────────────────────────┴──────────────────────────┤
├─────────┴───────────────── status bar (32px) ────────────────────────────┤
```

- Workspace height is `calc(100vh - var(--topbar-h))` with
  `overflow-hidden`. Page-level scrollbars are a defect.
- Rail is `w-60`, inspector `w-80`; both are `hidden xl:flex` and collapse
  below the `xl` breakpoint. The canvas must remain usable without them.
- Any region whose content can exceed its height wraps in `WorkspaceScroll`.
- Sidebar is `w-56` expanded, `w-12` collapsed, nav rows `h-8`, active state
  `bg-primary/15 text-primary` (a tinted selection, not a solid block).
- The sidebar footer and the page `StatusBar` are both `h-8` so the bottom
  edge is one continuous baseline across the seam. Changing one requires
  changing the other.

Page titles live in the top bar via `useHeaderTitle`, not as an `<h1>` in the
content. Do not reintroduce page heroes — a title plus descriptive paragraph
above a card grid is the pattern this redesign removed.

### 4.1 Bottom pane

A canvas region may carry one **bottom pane** — a full-width region pinned
below its other content, holding one of a small set of mutually exclusive
working surfaces. `ProjectDetailPage`'s Timeline/Mixer pane is the reference
implementation.

- Header is `h-8` with a `border-t`, `bg-card`: a `SegmentedControl size="sm"`
  on the leading edge naming the available surfaces, a spacer, and a
  chevron `Button size="icon" h-6 w-6` on the trailing edge that collapses
  and restores the pane. The chevron carries `aria-expanded`.
- Collapsed is a real state, not a third segment: with the pane shut, no
  segment is pressed, and clicking any segment opens it on that surface.
- The choice — including collapsed — persists in `localStorage`, keyed per
  entity (`upmixer.project.{id}.pane`, height at `.pane.height`). Storage
  being unavailable costs the preference, never the view.
- **The pane is resizable.** A 2px `role="separator"` grip sits directly above
  the header: `cursor-row-resize`, a centred 8px handle bar that brightens on
  hover, pointer drag, arrow/page keys, and double-click to restore the
  default height. A pane the user cannot size is a pane that is always the
  wrong size for the track they are working on.
- **Its height is clamped against the live column height, not a constant.**
  The pane keeps a `140px` floor and always leaves `220px` of headroom for the
  displays above, so no drag can squeeze them out of existence or make the
  page scroll (§1.2), **and** a `480px` absolute roof (`PANE_MAX_HEIGHT`) on
  top of that — a large enough window makes the headroom-based ceiling alone
  too permissive, letting the pane grow tall enough to squeeze the spatial
  row thin well before that dynamic ceiling is reached. Both clamps apply on
  every drag frame, not only on release — the window can be resized while the
  pane is open — and the roof also clamps whatever height was persisted
  before it existed, so an old stored value can't reopen the row past it.
- **The pane takes its space from the display above it, and it says which.**
  It does not add height. On the project page the spatial row drops from
  `flex-[3]` to `flex-1 min-h-[180px]` when the pane opens — the row's own
  total height responds to the pane, nothing else does.
- **The spatial row's composition never changes.** It is always
  `HazeView | ElevationView | ChannelMeters`, left to right, whether the pane
  is open or collapsed — there is no pane-collapsed variant that drops
  `ElevationView` below the row or stretches `HazeView` wide. What changes
  with the pane is only the row's total height (above) and, within it, each
  display's width (below).
- **Haze and Meters are user-resizable; Elevation is magnetic.** Haze and
  Meters each sit in their own `relative shrink-0` wrapper with an explicit
  pixel `style={{ width }}`, computed as a live natural size plus a
  persisted delta (`hazeExtra`/`elevationExtra`, 0 by default) — Haze's
  natural width is the row's own live height (reproducing the square by
  default), Meters' is `METERS_DEFAULT_SHARE` (a reasonable point in its own
  `[180, 480]` range) minus `elevationExtra`. Elevation itself carries no
  stored width at all: its wrapper is a genuine `min-h-0 min-w-0 flex-1`, so
  it always renders as exactly whatever Haze and Meters leave — not an
  estimate kept in sync by hand, but a flexbox guarantee, which is what
  makes the row "magnetic": no combination of the other two displays' widths
  can ever leave a gap, because Elevation has no ceiling and always claims
  every remaining pixel. `ChannelMeters` does not manage its own width —
  a baked-in internal cap could disagree with the caller's explicit width
  and leave unabsorbed space between it and Elevation, so the caller always
  supplies the value instead.

  A `StripResizeHandle` (§6.4) sits in the Haze and Elevation wrappers (the
  one between Elevation and Meters lives on Elevation's side but drives
  `elevationExtra`, which moves *Meters'* width — dragging Elevation's own
  border still reads as "resize Elevation" to the user even though, having
  no width of its own, it's Meters' width the drag actually changes and
  Elevation's rendered size that changes as a result). Reused verbatim from
  the mixer rack's own column resize rather than a second implementation —
  its `min`/`max` props (added alongside this feature) are what let a caller
  outside `ChannelStrip.tsx` supply a different natural range than a
  fader-width strip's. Both deltas are clamped against the row's *live*
  measured size every render, the same "don't trust a stale computed value"
  rule the pane's own resize already follows, and persist per project the
  same way the pane's height does
  (`upmixer.project.{id}.columns.hazeExtra`/`.elevationExtra`).

## 5. Layout primitives

Compose these before writing bespoke layout markup. All live in
`apps/web/src/app/`.

| Primitive | Shape | Use |
| --- | --- | --- |
| `Workspace` / `WorkspaceScroll` | grid + scroll region | Page anatomy (§4) |
| `Toolbar` / `ToolbarGroup` / `ToolbarSeparator` / `ToolbarSpacer` | `h-11`, `bg-card`, `border-b` | Page-scoped controls under the top bar |
| `Panel` / `PanelHeader` / `PanelBody` | `rounded-lg border bg-card`; header `h-8` | A bounded content region with a titled header |
| `SegmentedControl` | `h-7` (`sm` `h-6`), `bg-muted` track | Mutually exclusive view or step switch |
| `StatusBar` / `StatusCell` / `StatusSeparator` / `StatusSpacer` | `h-8`, 11px | Always-on counts and machine state |
| `InspectorRow` / `InspectorGroup` | `min-h-7` label/value rows | Parameter lists in the inspector |
| `MetricStrip` | one bordered row or stacked column, `divide-x` / `divide-y` | KPI cells — never separate floating cards |
| `EmptyState` | panel-filling icon + line + action | Empty region inside a panel |

`SegmentedControl` renders `role="group"` with per-button `aria-pressed`. It
deliberately does **not** use `role="tab"`: there are no tabpanels, and the
tab role breaks both semantics and role-based test queries. Use the `Tabs`
primitive only where real tabpanels exist.

## 6. Controls

Class-level rules for `apps/web/src/components/ui/*`. Keep every export signature
and DOM shape — `ToggleField`'s nesting in particular is depended on.

- **Button** — `default h-7 px-3 text-[13px]`, `sm h-6`, `lg h-9`,
  `icon h-7 w-7`; icons `size-3.5`. Variants: `default` (primary fill),
  `secondary`, `outline`, `ghost`, `link`, plus `destructive` / `success` /
  `warning`. Flat fills, no drop shadow. Disabled is `opacity-40`.
- **Input** — `h-7`, `text-[13px]`, `bg-secondary`. Apple fields are filled,
  not hollow outlines.
- **Badge** — `rounded-md` (not a pill), `px-1.5 py-px text-[11px]`.
- **Card** — `rounded-lg border bg-card`, padding `p-3`, no shadow. Prefer
  `Panel` inside a workspace; `Card` is for free-standing list items.
- **Switch** — `h-[22px] w-[38px]`, white thumb, checked `bg-success`.
  Apple toggles are green, not accent-coloured.
- **Slider** — `h-1` track, `h-3.5 w-3.5` white thumb with a soft shadow.
- **Pot** — rotary knob for effect parameters; see §6.1.
- **Progress** — `h-1.5`, `bg-secondary` track, `rounded-full`.
- **Dialog** — `rounded-[14px]`, `w-[min(1100px,92vw)]`, overlay
  `bg-black/40 backdrop-blur-sm`. Content owns its own toolbar and pinned
  footer; do not use sticky-offset hacks for dialog actions.
- **Focus** — `focus-visible:ring-2 ring-ring/60 ring-offset-1`, applied
  through the primitive. Never remove a focus ring without replacing it.

### 6.1 Pot vs slider

`Pot` (`components/ui/pot.tsx`) is the rotary knob Logic Pro uses for
compressor and tone controls. The split is by what the number *means*, not by
where it sits:

- **Slider** for levels, targets and blends — anything read off a scale
  against a known unit. Loudness target, true-peak ceiling, EQ strength,
  match strength, per-stem gain.
- **Pot** for effect parameters tweaked by feel — compressor threshold /
  ratio / attack / release / knee / makeup, bass sub and mid gain, mono
  cutoff, LFE trim.

Rules for any new pot:

- **Sweep** is 270°, gap at the bottom: `-135° + t·270°` from 12 o'clock.
- **Origin** is inferred — a range spanning zero fills from 12 o'clock so cut
  and boost read differently; every other range fills from the left end. Pass
  `origin` explicitly only to override that.
- **Layout** is dial, value (13px `tabular-nums`), label (11px
  `muted-foreground`, sentence case, truncated with a `title`). Lay pots out
  in `grid-cols-[repeat(auto-fit,minmax(76px,1fr))]` so a knob bank reflows
  instead of wrapping raggedly. Do not put selects or toggles in that grid —
  give them their own row above it.
- **Interaction** is vertical drag (full range over 160px, Shift for quarter
  speed), arrow/page/Home/End keys, and double-click to reset. Wheel adjusts
  **only when the pot has focus**, checked against `document.activeElement`
  rather than React state, so a panel scroll is never swallowed.
- **The value arc is always `primary`**, at full stroke, in every state. A pot
  is coloured at rest the way Apple's are; colour must never be something the
  control earns by being touched.
- **Inherited values** (a profile default not yet overridden) are only
  slightly softened; the first edit takes the override and double-click
  restores the profile. State this in `aria-valuetext` too — opacity alone
  conveys nothing to assistive tech.
- **Semantics** are `role="slider"` with the full `aria-value*` set and
  `aria-orientation="vertical"`. A pot is a slider to assistive tech and to
  tests; `getAllByRole("slider")` will match both, so query by name.

### 6.2 Scrollbars

Styled globally in `index.css`, so every scroll region inherits them — never
restyle a scrollbar per component.

The macOS model: no arrows, no track chrome, just a translucent pill in a
transparent gutter. 12px gutter, 6px thumb (a 3px transparent border with
`background-clip: padding-box` insets it), fully rounded, `min-height: 28px`
so a long document never yields an unclickable sliver.

The thumb colour is `hsl(var(--foreground) / 0.22)`, rising to `0.38` on hover
and `0.55` while dragging. Deriving it from `--foreground` rather than giving
it a colour of its own means it is automatically dark-on-light and
light-on-dark, which is exactly what macOS does — do not add a dedicated
scrollbar token. `scrollbar-width: thin` and `scrollbar-color` cover Firefox;
`scrollbar-width` does not inherit, so it is set on `*`.

Scroll regions inside the always-dark canvas displays would need their own
rule, since a light-theme thumb would vanish there. None currently scroll.

### 6.3 Effect panels and control text

A group of parameters that can be switched off as a unit is a `Panel` whose
`PanelHeader` carries a `Switch` in its `actions` slot — the effect's power
button, on the **trailing edge**, matching Apple's settings rows and the
`Switch` position in `ToggleField`. `MasteringSection` is the reference
implementation.

- **The header switch replaces every other way of expressing "off".** A
  profile picker must not also carry a "None" entry, and an effect must not
  also have a standalone enable toggle in its body. One control per idea.
- Switching **off** must always be possible; only switching **on** may be
  blocked (e.g. when the profile list has not loaded). Never disable a power
  switch that is currently on.
- Off clears the underlying value, so remember the last one and restore it
  when the effect is switched back on rather than silently resetting.
- A parameter that genuinely stays live while the effect is off — true-peak
  limiting runs regardless of loudness normalization — stays enabled. Do not
  dim a control to look tidy when it is still doing something.

**A primary parameter that others depend on** gets its own full-width row
above them, not a cell in a grid alongside its dependants. Give it the rich
`Select` (`components/ui/select.tsx`, Radix) so each option can carry a mark
and a short note; title the panel beneath it after the current choice, and
render only the parameters that choice actually has.
`ProjectDeliverySection` is the reference implementation: picking Binaural
drops the stereo-downmix row, because a binaural render is already
two-channel.

Plain text pickers stay on `SelectField`'s native `<select>` — the platform
renders those better and for free. Reach for the Radix `Select` only when an
option needs more than a label. Format marks are neutral icons, never a
vendor's logo.

**Control text.** The label is the documentation. Do not add a sentence
explaining what "Match spectrum" or "Bass exciter" means. Keep prose only
where it carries information the label cannot — a constraint, a unit, or a
scope ("One WAV or FLAC, matched across every track") — and keep it to one
short line at `text-[11px] text-muted-foreground`.

### 6.4 Fader and channel strip

`Fader` (`components/ui/fader.tsx`) is the vertical channel fader. The split
against `Slider` is by **layout purpose**, not by what the number means:
`Slider` is a level read off a horizontal scale in a panel or inspector;
`Fader` is the same kind of value laid out the way a console lays it out, so a
rack of them can be compared and balanced at a glance.

Per-stem gain has **three homes, one control**: the mixer rack's strip and the
inspector's always-accessible copy of the selected stem's strip (below) are
the literal same `StemChannelStrip` component (`ChannelStrip.tsx`), not a
parallel re-implementation — the inspector never falls back to a `Slider` for
this value. That is what "one idea, two viewports" (§6.3) means taken
seriously: the two homes cannot drift apart because they are one piece of
code rendered twice. The timeline row's inline `HorizontalFader` (§6.5) is a
third rendering of the same manifest field through the same `onGain` handler,
not a fourth independent implementation — a different control shape for a
row too compact for the full strip, but still one value with one writer.

The inspector's "Stem" group orders its content **title, then position and
EQ, then the fader** — not the fader first. The section already has exactly
one place to say which stem this is (a title row: icon, name, enabled/muted),
so the strip renders with `showNameplate={false}` rather than repeating the
name a second time a few pixels below it; the fader sits last, under the
controls the mixer's own rack doesn't carry (position, EQ), because it is the
value those other controls don't need read alongside — it already has its own
home in the rack above.

- **Geometry.** 148px travel; a 22×34px cap over a 3px recessed slot, with a
  10px tick gutter down the left edge. The cap's *centre* travels the slot, so
  scale marks are offset by half a cap at both ends — a tick and a cap resting
  on it must read as the same value.
- **The slot and ticks are an instrument, not chrome.** They stay fixed
  `canvasTheme` values (`faderTick`/`stripWell`), not theme tokens, and stay
  identical in both appearances — a console's travel groove is a physical
  finish, and Logic renders it the same way in light and dark. This is the
  §7.1 exception applied to a DOM control: the *rack* around it is themed,
  the *groove* on it is not.
- **The cap is a flat plate, not a skeuomorphic knob.** No gradient, no drop
  shadow, no indent line — `rounded-[4px] border border-border bg-secondary`
  with a single `bg-foreground` grip line across its centre, the same
  `fill-secondary stroke-border` language `Pot` draws for its own knob (§6.1).
  A fader and a pot are one visual family of grip, not two: a rotary control
  drawn flat next to a vertical one drawn like hardware read as mismatched
  controls doing the same job.
- **Detent and ticks.** Pass `detent` for the rest value (0 dB unity); its tick
  runs the full gutter width in `labelStrong` while ordinary `ticks` are
  shorter and dimmer. Double-click resets to rest.
- **The tick ladder is unlabelled.** Logic prints the dB numerals once, beside
  the meter, not on both controls. A fader that repeats them is noise.
- **Interaction** matches `Pot` (§6.1): pointer drag, arrow/page/Home/End keys,
  and wheel **only when focused**, checked against `document.activeElement`
  so a strip-rack scroll is never swallowed.
- **Semantics** are `role="slider"` with `aria-orientation="vertical"` and the
  full `aria-value*` set. Pass the formatted readout as `valueText` — a raw
  number without its unit is not a readout.

A **channel strip** stacks, top to bottom: nameplate (colour-carrying stem icon
+ truncated name over a 2px stem-hue underline, click selects), the two
readouts, fader beside its meter, M/S buttons, state line. Nothing else.
Position and stem EQ stay in the inspector; putting them on the strip too would
give one idea two homes with no viewport justification.

- **Width follows the meter, not a constant.** A strip is `fader + meter +
  padding` wide, so a stereo stem's two-bar meter makes its strip exactly one
  bar wider than a mono stem's. Use `stripMeterWidth(channels)`; never hardcode
  a strip width.
- **Two readouts, side by side, above the fader.** Left is the fader's own
  value in `labelStrong`; right is the meter's held peak in `meterWarn` yellow.
  Both sit in `stripWell` slots in 10px `tabular-nums`. The peak readout
  updates at ~10Hz, not per frame — a 60Hz number is unreadable, and it is
  React state, so refreshing it per frame would re-render the whole rack that
  the canvas meters exist to avoid.
- **The rack is chrome** (§7.1): `bg-card`, strips divided by `border-r`,
  selected strip `bg-primary/10`, master strip `bg-muted/40` behind a 2px
  leading rule. Stem identity appears as hue on the icon and underline only —
  a fully tinted strip would fight the meter it contains. M/S sit in
  `stripWell` slots until lit, matching the readouts above them.
- **One meter bar per source channel.** A stereo stem gets two bars, a mono
  stem one, capped at two — a console shows a stereo channel's sides
  separately, and summing them would hide a one-sided image. The count comes
  from the stem's real channel count (`ProjectStem.channels`), and the audio
  hook taps each channel through its own analyser
  (`AudioNodeSet.meterAnalysers`), so the bars are independent measurements,
  not one measurement drawn twice.
- **Strip meters are canvas**, in two layers: the dB numeral column repaints
  only on resize, the bars every frame. Rasterizing text 60 times a second for
  a scale that never moves is the expensive mistake to avoid here.
- **The strip meter has its own scale and hue, and that is faithful, not
  drift.** `STRIP_DB_TICKS` prints Logic's channel-strip stops (3 dB steps to
  −18, then coarsening) against `DB_TICKS`' coarser field scale, and
  `STRIP_METER_PALETTE` runs green where the Level Meter runs blue — Logic
  ships both, one per host. What stays shared is everything that could
  *disagree*: the dB mapping algorithm, the zone thresholds, and the peak-hold
  behaviour. Hue and tick density are per-host presentation; a threshold is not.
- §7's "an active channel has no track" rule is a *field* rule: a meter hosted
  in chrome passes a `stripWell` slot to `drawMeterBar`, because with no dark
  field behind it an unlit bar would otherwise be invisible on a light panel.
- **Peak-hold tracks the smoothed RMS bar, never the raw sample peak.** Both
  come from `createMeterState()`; a meter that derives its own will disagree
  with every other meter, and feeding it the instantaneous peak pins the tick
  to the top of the bar on real music's crest factor.
- **Every strip owns its meter loop**, via the `useStripMeterLoop` hook
  (`useStripMeterLoop.ts`) — its own rAF callback, its own `createMeterState`,
  its own canvas. This is what makes the strip portable: a rack of 15 needs no
  shared registration mechanism to paint correctly, and neither does a single
  copy of one strip living alone in the inspector. Meters driven by React
  state, or a bespoke draw loop that doesn't go through this hook, are a
  defect — see the peak-hold rule above for why re-deriving the math forks it.
- **The inspector's copy is the same component, given a different
  `subjectName`.** The mixer strip and the inspector strip can be on screen
  at once (mixer pane open, a stem selected) and both write and read the same
  field, so without a distinct name their fader and M/S buttons would share
  one accessible name — exactly the ambiguity §8 forbids. `subjectName`
  (default: the stem's own name, correct for the rack) overrides the words
  the fader and M/S use, without touching the DSP-facing props at all; the
  inspector passes `"Selected stem"`. Any other place this component gets a
  second simultaneous instance needs the same treatment.
- **The source anchor gets a strip of its own kind, not a stem's.** It sits
  between the stem rack and the master strip, blends the pre-separation
  track back into the render rather than adjusting one stem's send, and reads
  as a mix-wide correction, not a channel — so it is visually a different
  species: an accented border and background wash instead of chrome, a
  0–100% blend readout instead of dB, no meter (there is no independent
  per-anchor audio tap to show) and no M/S (there is nothing on it to mute or
  solo). The fader hardware itself stays the ordinary fixed `canvasTheme`
  instrument — only the strip *around* it marks it as special, the same
  "rack is chrome, hardware isn't" split the rest of §6.4 already draws.
  The accent is `success`, not `primary` — `primary` is already the rack's
  selection colour (the selected stem's strip is `bg-primary/10`), and a
  second, unrelated use of the same hue immediately next to it read as the
  same state rather than a different one. Any future special-purpose strip
  needs a colour not already spoken for elsewhere in the same rack; check
  what the rack already means by a hue before picking one.
- **Every strip resizes independently**, from its own trailing border — the
  line already separating it from its neighbour (`StripResizeHandle`,
  `ChannelStrip.tsx`) — the way dragging a column border in a spreadsheet
  only moves that column, not one handle that widens the whole rack
  together. No separate grip is drawn on top of the border and no hover/
  focus highlight is shown; the border itself is the drag target — hovering
  it just swaps the cursor to `col-resize`. A strip's width is
  `stripWidth(channels) + extraWidth`, where `extraWidth` is that strip's
  own state; never share it across strips. The handle is `position:
  absolute; right: 0` inside the strip's (`relative`) root, `role="slider"`
  with `aria-orientation="horizontal"`, drag + arrow/page keys +
  double-click-to-reset — the same interaction contract as `Fader` and
  `Pot`, just on the horizontal axis; it stops click/pointerdown
  propagation so a resize never also selects the strip beneath it. Widths
  for every strip kind (stem, anchor, master) persist under one
  `localStorage` map keyed by strip id, written on drag-end/key-commit, not
  on every drag-move frame.
- **The whole strip is the select target**, not just its nameplate — a
  click anywhere on a stem strip (`StemChannelStrip`'s root) selects it,
  same as clicking the nameplate button inside it. The nameplate stays a
  `<button>` for keyboard/screen-reader access; the root `onClick` is the
  mouse convenience layered on top, matching how clicking anywhere on a
  channel strip in a hardware console or DAW selects it.
- **M is `destructive`, S is `warning`** (§ semantic colour mapping), both
  `h-5` with `aria-pressed`. A strip silenced by *someone else's* solo is a
  third state and gets its own word ("Silent" vs. "Muted") — §8 forbids
  carrying that distinction in dimming alone.
- **The master strip is separated by a 2px rule**, not a gap, and is labelled
  with what its fader actually controls. In this app that is monitor gain
  (`lib/fader.ts`'s −60 dB…unity taper), which never reaches the exported
  render — so it is labelled "Monitor" and shares one value with the Transport
  volume control. A strip whose fader *did* affect the render would be a DSP
  change bound by `docs/contracts/preview_export_parity.md`, not a UI one.
- The rack scrolls horizontally when strips overflow; strips never shrink.

### 6.5 Horizontal fader

`HorizontalFader` (`components/ui/horizontal-fader.tsx`) is the other Apple
fader idiom §1 names — Logic Pro **for iPad**'s glowing horizontal
track-volume bar, not the desktop-styled flat-plate cap `Fader` (§6.4) draws.
The two are not competing implementations of the same thing: `Fader` is the
mixer channel strip's vertical control, drawn the way desktop Logic and the
app's other flat-plate controls (`Pot`, §6.1) draw a grip. `HorizontalFader`
is for a control inline in a row too compact for a full strip — a dark pill
spanning the control's full height (not a thin centred line), a glowing
`success`-token fill or live-level bars inset from the pill's own edge by a
small fixed margin (`PILL_INSET`, 3px — content never sits flush against the
rounded cap), and a translucent grey knob the same height as the pill,
matching the reduced, touch-first idiom iPad Logic uses in exactly that
context (a per-track volume control in a row, not a full mixer strip). The
knob is `bg-foreground/35` with a `border-foreground/25` ring, not an opaque
plate — at this size (16-20px) an opaque cap the same tone as the pill hid
whatever fill or meter bar sat directly beneath it, the one part of the level
a knob sitting mid-travel would otherwise cover; translucency lets that
still read through. It has no detent tick and no inner grip line: a tick at
the knob's own position showed through the same translucency as a stray line
bisecting it, and a grip line read as noise at this size — the knob's
position is already the value.

Two usages today, both sized only via `knobSize` (which also sets the pill's
height — there is no separate track-thickness prop):

- **Transport's monitor volume** (`Transport.tsx`), larger (`knobSize={18}`,
  `w-32`) than the timeline's row instance — this control is read and
  adjusted constantly while a preview plays, so it earns a wider track for
  finer drag resolution, the same reasoning that sized up the transport
  buttons and LCD beside it (§6, control-text density notwithstanding —
  precision here outweighs the ordinary control height). Its live-level
  bars read the actual L/R signal reaching the monitor (`headphoneLevels`).
- **The timeline row's inline gain** (`TimelineView.tsx`), compact
  (`knobSize={16}`) sized to the two-line row (§7.2) — see §6.4's "three
  homes" note for how this shares its value with the mixer/inspector fader
  rather than forking it. Its live-level bars read the stem's actual level
  (`stemLevels`).

Same interaction contract as `Fader`/`Pot`: pointer drag, arrow/page/Home/End
keys, double-click to reset, wheel gated on `document.activeElement`. With no
`meterSource`, the fill is a single glowing bar to the knob's position,
reusing the themed `success` CSS variable (`shadow-[...hsl(var(--success)
/0.7)]`) rather than a new literal colour. With `meterSource`, that fill is
replaced by one live-level bar per channel (1 for mono, 2 for stereo, same
cap `StripMeter` uses) — **independent of the knob**: the bars are the
stem's or the monitor's actual playback level right now, the knob is the
gain value, and the two are deliberately not wired together, matching what
iPad Logic itself draws in this exact context (a fader set loud with silent
audio shows empty bars). The bars reuse the app's one dB scale
(`meterScale.ts`'s `levelToDb`/`dbToY`/`zoneColor`) in Logic's
green-to-yellow mixer-strip palette (`STRIP_METER_PALETTE`), not the blue
Level Meter palette `ChannelMeters` draws — same "shared math, host picks
the palette" split §7 already documents, just applied to a third host. It
skips the peak-hold tick `StripMeter` draws: at this size a tick would be
noise, and Logic's own per-track bar doesn't show one either.

**Yellow zone depends on channel count, not host.** `meterScale.ts` exports
two floors: `YELLOW_ZONE_DB` (-20, the default) for a meter that represents a
single channel in isolation — a mono stem's strip meter, a mono stem's
`HorizontalFader` — and `MULTI_CHANNEL_YELLOW_ZONE_DB` (-10) for one that
represents two or more channels together: a stereo/master strip, a stereo
stem's `HorizontalFader`, and `ChannelMeters` (always multi-channel — it is
never a single isolated reading). This is a per-*meter-instance* split, not
per-bar: a stereo strip's two bars both use the multi-channel floor even
though each bar still shows one channel's signal, because the meter as a
whole is reading two channels together. Any new meter follows the same
rule — check `bars.length`/`channels`, don't hardcode a floor.

### 6.6 Project top bar and transport bar

The project's stage tabs (the Mixing/Mastering/Delivery `SegmentedControl`)
live in the global top bar (`AppShell`'s `<header>`), beside the project's
own breadcrumb and name — the project's identity and its workflow read as
one unit, and the top bar is the one region that's *always* on screen
regardless of stage. `ProjectDetailPage` builds this combined element itself
and hands it to `useHeaderTitle`, since `HeaderSlot` only exposes a single
slot — see the memo's own comment for why folding frequently-changing state
(`activeTab`/`settingsView`) into that memo's deps is safe here (a bounded
state change per click, not a fresh element every render).

- **True centring, not leftover-space centring.** The header content is a
  three-column grid, the same `minmax(0,1fr)_auto_minmax(0,1fr)` trick
  `Transport` uses (see its own `leading` prop comment) — breadcrumb in col
  1, stage tabs in col 2 (`justify-self-center`), Project settings in col 3
  (`justify-self-end`). Equal flanking tracks are what keep col 2 pinned to
  the bar's true centre regardless of how long the project name or the
  settings segment gets; a flex row with a `flex-1` spacer would only centre
  within whatever space happened to be left over. `AppShell`'s own header
  gives the slot holding this content `flex-1` (see its own comment) so
  there's a full bar width to centre against in the first place.
- **The stage tabs are the workflow; Project settings is not one of its
  steps.** The four stages stay condensed into one `SegmentedControl` —
  that grouping *is* the point, it reads as "the sequence you follow."
  Project settings is its own one-segment `SegmentedControl` (identical
  look/press behavior, no fifth tab) in col 3, precisely so it doesn't imply
  "next step after Delivery."
- **The stage tabs alone deviate from `SegmentedControl`'s default `h-7`,
  `bg-card` active pill, and cross-fade** (the `SegmentedControl` row further
  below still documents that as the default). Passing `fill` stretches the
  control to the header's full height instead of sitting as a fixed-height
  pill inside it — this is the workflow's primary selector, the one thing on
  screen that should read as "which stage am I on" at a glance, so
  `activeClassName`/`activeTextClassName` swap its active state to
  `bg-primary`/`text-primary-foreground` instead of the card pill. It also
  passes `slideIndicator`, so the primary pill glides between segments
  instead of cross-fading in place — one continuous "selector moving," not
  four segments independently blinking. That indicator is a `fill`-only
  behavior: when `value` matches no stage (switching to Project settings,
  a sibling control), it fades out in place rather than sliding toward
  settings' own one-segment control, since there's nowhere shared to slide
  to. Project settings and every other `SegmentedControl` in the app keep
  the default fixed height, card-pill active state, and cross-fade.
- **The top bar's right side empties out to make room.** `AppShell` renders
  Refresh and the page's `onCreate` button only when their callbacks are
  supplied; `App.tsx` withholds both specifically on `/projects/:id`, since
  the stage tabs need the width. The projects *list* route keeps both.
- **`Transport`'s `leading` slot carries only the `TrackRail` reveal
  toggle** (§4 — collapsing takes the rail out of the layout entirely, so
  its own header button can't be what brings it back; this is the one place
  guaranteed to render whenever a rail-bearing stage is active).
- **"Download project" lives inside the Settings view, not the top bar.**
  It's a portable `.upmix.zip` re-importable to an identical workspace —
  distinct from the Delivery tab's "Export project", which renders a
  deliverable mix, not a re-editable project. It sits in
  `ProjectSettingsSection` below the form fields, past a divider, with its
  own one-line explanation (§6.3's "control text" rule: the label is the
  documentation everywhere *except* where a constraint needs stating) —
  gated on `project.tracks.length > 0` the same as the form fields above it
  have something to act on.
- **Manifest JSON is gone, not hidden.** The raw-JSON manifest editor
  (`AdvancedSection`) was a second, unfiltered way to edit project state
  alongside the structured `ProjectSettingsSection`/mastering/delivery forms;
  removing the entry point here removes the feature from this page. The
  component itself is unchanged and still used by `ManifestEditor.tsx`
  (job composition), so nothing there was touched.

### Semantic colour mapping

| Meaning | Token | Examples |
| --- | --- | --- |
| Primary action, selection | `primary` | Create, active nav row, slider range |
| Running, completed | `success` | Play button, completed job, switch on |
| Attention, held state | `warning` | Solo, loop, queued |
| Failure, off-air | `destructive` | Mute, delete, failed job, error text |

Solo is `warning` and mute is `destructive`, matching Logic's yellow/red — do
not swap them.

## 7. Canvas displays

`HazeView`, `ElevationView`, and `ChannelMeters` render to `<canvas>` and read
`apps/web/src/lib/canvasTheme.ts`. These surfaces stay dark in **both** app themes,
the way Logic keeps its instrument displays dark regardless of appearance.
They are the one place literal hex values are correct (alongside `stems.ts`,
§2); add new ones to `canvasTheme.ts` rather than inline. `TimelineView` and
the mixer's strip meters also draw with a canvas but are **not** instrument
displays — see §7.1.

**Field.** All three share one surface: a vertical (elevation, meters) or
radial (haze) gradient from `plotField` `#070E17` to `plotFieldCore`
`#0D1B2B`, plus a systemBlue wash (`plotShadeStrong` → `plotShade` →
transparent). This is Logic's analysis-display treatment — Channel EQ, Quick
Sampler, Beat Breaker.

Rules learned from getting this wrong:

- Gradients span the **full canvas**, not the padded plot rect. A gradient
  clamped to plot bounds leaves flat bands where the ramp stops, and a wash
  drawn as a rect draws its own hard edges.
- The wash stays faint (0.06 / 0.10). Content painted over it is
  semi-transparent, and a heavier wash drags cool-coloured stems toward the
  field's own hue until they are no longer distinguishable. Verify hue
  separation by sampling canvas pixels, not by eye.
- Views with a motion-trail fade paint the field through `globalAlpha` so
  successive frames do not flatten the gradient.

**Meters** on the field follow Logic's Level Meter: blue `meterSafe` `#3E9BC7`
up to −20 dB, `meterWarn` past it, `meterHot` above −5 dB, square-ended
columns painted straight onto the field. An active channel has **no track** —
an unlit meter is background, and the dB hairlines carry the structure. A muted
channel keeps a `well` slot inside a `destructive` frame so "off" reads
differently from "silent". Peak ticks are centred on the dB they hold.

**Meter scale.** `levelToDb`, `dbToY`, `drawMeterBar`, the zone thresholds and
`createMeterState` live in `apps/web/src/lib/meterScale.ts`, not in any one display.
Every meter in the app imports them. Re-deriving a dB scale beside a second
meter is how two meters end up disagreeing about what −20 dB looks like.

**What a host may vary, and what it may not.** `dbToY` takes a tick set and
`drawMeterBar` takes a `MeterPalette`, because Logic itself draws two different
meters: the Level Meter (blue, `DB_TICKS`) and the mixer channel strip (green,
`STRIP_DB_TICKS` — see §6.4). Tick density and hue are presentation and belong
to the host. The **zone thresholds, the dB mapping, and the peak-hold rule are
not** — those are shared, and a host that overrides one has introduced a bug,
not a style.

### 7.1 Canvas in chrome

Not every canvas is an instrument display. **A canvas belongs on the dark
field only when it is an analysis readout** — Haze, Elevation, ChannelMeters,
the transport LCD. A canvas that is really a working surface — the timeline's
lanes, a mixer strip's meter — is an ordinary panel that happens to draw with
a canvas, and it must follow the app theme in both light and dark like the
panel next to it. Putting a working surface on the blue instrument field
reads as a hole in the page rather than as depth.

Canvas has no access to CSS variables, so those surfaces read the resolved
tokens through `useThemeTokens` (`apps/web/src/lib/themeTokens.ts`), which re-reads
when the theme class on `<html>` changes. Never duplicate a token as a literal
to get it into a canvas, and never add a chrome colour to `canvasTheme.ts`.

The exception inside the exception: the **Logic Level Meter zone colours**
(`meterSafe`/`meterWarn`/`meterHot`) stay fixed in both themes wherever a meter
is drawn, chrome or field. A meter's colour means a level; it cannot change
meaning with the appearance setting.

### 7.2 Timeline

Chrome (§7.1): the lane field is `background`, the ruler `muted`, the header
column `card`, separators `border`.

- **Metrics.** Lane `64px`, ruler `22px`, header column `280px` — grown from
  a single 44px line/176px column when the row adopted iPad Logic's own
  two-line track-header shape. The header column is `sticky left-0` so lane
  names stay put while lanes scroll, and each row carries a 3px stem-colour
  left border. A row is drag handle, then a name line, then a second line of
  M/S (`h-6 w-7`, filled `bg-secondary` at rest so the button reads as a
  control even before it's toggled — not the transparent-until-active look
  the rest of the app's icon buttons use) and the inline gain
  `HorizontalFader` (§6.5, `knobSize={20}`), then the bare per-stem icon
  (`getStemIcon`, `h-6 w-6`, no background swatch) on the trailing edge —
  everything the removed stem rail once carried, in one place, at Logic's
  own proportions rather than squeezed onto one line. iPad Logic's row also
  carries a third "R" (record-arm) button; this app has no recording
  concept, so it's a two-button M/S row here, not a fabricated third control.
  The row itself is HTML5-`draggable` for reordering, but the drag only
  starts from the grip icon (`data-drag-handle`) — a plain "mousedown then
  move" reorder-drag and the fader's own pointer-drag are the same gesture,
  and the row's native drag would otherwise win the race and starve the
  fader of its pointermove events the moment a drag started anywhere else on
  the row (the name, M/S, the fader itself).
- **Ruler.** Tick step is the first of 1/2/5/10/15/30/60/120/300/600 seconds
  that keeps ticks at least 68px apart, so density holds from a 90-second demo
  to a 40-minute set. Gridlines run the full height at `border` / 0.7.
- **Regions, not bare traces.** Each lane holds one rounded region inset 3px,
  filled with the stem hue at ~22% over `card`, outlined at ~60%, with a
  tinted 11px caption bar carrying the stem name — Logic draws the arrangement
  as colour-coded blocks, and the block is what makes a dozen stacked lanes
  scannable. The waveform is the same hue at full strength inside it.
- **Waveform.** Mirrored min/max envelope about the region midline. A muted
  stem drops the whole region to 0.3 alpha rather than vanishing; a
  non-selected lane drops to 0.55 when some other stem is selected. Each
  output column takes the extremes of **every** envelope bin inside it —
  point-sampling a downsampled envelope drops exactly the transients a
  waveform exists to show.
- **Playhead** is `foreground` — deliberately neither `primary` (selection)
  nor `destructive`, because a transport position is neither. 1px line plus a
  small triangular cap in the ruler.
- **Two canvases, one of which is cheap.** Lanes redraw only when peaks, mute
  state, selection, theme or size change; the playhead has its own overlay
  canvas and is the only thing touched per frame. Position is read from
  `currentTimeRef` in the view's own rAF loop — never from `currentTime` state.
- **Envelopes are server-precomputed**, fetched once per track as one binary
  (`apps/api/src/features/projects/storage.py`). Do not compute peaks in the browser from
  decoded buffers: that adds main-thread work at exactly the moment decode is
  already saturating it. The payload also carries the track duration, so the
  ruler draws before playback has finished loading.
- **Missing envelopes are stated, not silent.** A project prepared before
  peaks existed is backfilled in the background; while that is pending the
  lanes say so, and if the asset genuinely cannot be served they say that
  instead. An empty lane with no explanation reads as a broken feature.
- **No horizontal zoom.** The whole track always fits the pane width, which is
  why the header column can be sticky and no virtualization is needed. Adding
  zoom means revisiting both.

Render loops, DSP, and memoization are out of scope for
visual work. `HazeView`, `ElevationView`, `ChannelMeters`, `TimelineView`,
`MixerView`, and `Transport` are `React.memo`'d to keep 60fps playback from
re-rendering the page: do not pass them inline object or callback props.

## 8. Accessibility

- Every icon-only control carries an `aria-label`.
- Toggles expose `aria-pressed`; filter buttons carry an explicit
  `aria-label` including their count, because concatenated text nodes produce
  an accessible name with no separator.
- Facet group names must be unique on a page — duplicated visible text makes
  role queries ambiguous for both assistive tech and tests.
- Never rely on colour alone: mute shows a red frame *and* a red label; job
  status shows a badge with text.
- Canvas displays are decorative duplicates of state available as text
  elsewhere (status bar, inspector). Keep it that way.

## 9. Verification

A visual change is not done until:

1. `cd web && npx tsc -b && npx eslint src` — zero errors.
2. `cd web && npx vitest run` — all suites pass.
3. `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` from
   the repo root — zero regressions.
4. Preview-tool check on each affected route, in **both** colour schemes and
   at desktop and tablet widths: no console errors, no page-level scrollbar
   (`scrollHeight === innerHeight`, `scrollWidth === innerWidth`), rail and
   inspector collapse cleanly below `xl`.

For canvas work, confirm rendering by sampling pixels via `javascript_tool`
rather than trusting a screenshot — the preview pane can return stale frames,
and gradient or hue regressions are not reliably visible in a downscaled
image.
