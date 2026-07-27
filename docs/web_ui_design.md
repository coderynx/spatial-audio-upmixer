# Web UI Design Specification

Binding reference for `web/`. Any change that adds a page, a control, or a
visual state follows this document. It records the decisions already
implemented, so new work stays coherent with what exists rather than
re-deriving a look per feature.

The target is the Apple pro-app idiom used by Logic Pro and Final Cut Pro for
iPad: Apple system colours, Apple radii, dense controls, and a full-viewport
panel anatomy. Both light and dark are first-class — neither is an
afterthought skin of the other.

Scope is presentation only. `docs/web_architecture.md` governs the delivery
boundary, and the rule in `AGENTS.md` still holds: no `upmixer/` change for a
web visual concern.

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

All colours come from CSS custom properties in `web/src/index.css`, consumed
through the Tailwind mappings in `web/tailwind.config.ts`. Never write a
literal colour in a component — the only sanctioned literals live in
`web/src/lib/canvasTheme.ts` (§7).

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `background` | `#F2F2F7` | `#000000` | Workspace substrate |
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

Dark values are Apple's named system colours. The light greys between
`background` and `border` are not named Apple colours, so they are listed as
the HSL triples that `index.css` actually declares.

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

Every routed page composes `Workspace` from `web/src/app/Workspace.tsx`:

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

## 5. Layout primitives

Compose these before writing bespoke layout markup. All live in
`web/src/app/`.

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

Class-level rules for `web/src/components/ui/*`. Keep every export signature
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
- **Progress** — `h-1.5`, `bg-secondary` track, `rounded-full`.
- **Dialog** — `rounded-[14px]`, `w-[min(1100px,92vw)]`, overlay
  `bg-black/40 backdrop-blur-sm`. Content owns its own toolbar and pinned
  footer; do not use sticky-offset hacks for dialog actions.
- **Focus** — `focus-visible:ring-2 ring-ring/60 ring-offset-1`, applied
  through the primitive. Never remove a focus ring without replacing it.

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

`HazeView`, `ElevationView`, and `ChannelMeters` render to `<canvas>` and
read `web/src/lib/canvasTheme.ts`. These surfaces stay dark in **both** app
themes, the way Logic keeps its instrument displays dark regardless of
appearance. They are the one place literal hex values are correct; add new
ones to `canvasTheme.ts` rather than inline.

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

**Meters** follow Logic's Level Meter: blue `meterSafe` `#3E9BC7` up to
−20 dB, `meterWarn` past it, `meterHot` above −5 dB, square-ended columns
painted straight onto the field. An active channel has **no track** — an
unlit meter is background, and the dB hairlines carry the structure. A muted
channel keeps a `well` slot inside a `destructive` frame so "off" reads
differently from "silent". Peak ticks are centred on the dB they hold.

Render loops, DSP, memoization, and `previewGraph.ts` are out of scope for
visual work. `HazeView`, `ElevationView`, `ChannelMeters`, and `Transport`
are `React.memo`'d to keep 60fps playback from re-rendering the page: do not
pass them inline object or callback props.

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
3. `python3 -m pytest -q` from the repo root — zero regressions.
4. Preview-tool check on each affected route, in **both** colour schemes and
   at desktop and tablet widths: no console errors, no page-level scrollbar
   (`scrollHeight === innerHeight`, `scrollWidth === innerWidth`), rail and
   inspector collapse cleanly below `xl`.

For canvas work, confirm rendering by sampling pixels via `javascript_tool`
rather than trusting a screenshot — the preview pane can return stale frames,
and gradient or hue regressions are not reliably visible in a downscaled
image.
