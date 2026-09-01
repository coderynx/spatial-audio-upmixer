# Web UI Design

Binding visual contract for `apps/web`. Use this for page-level decisions,
[UI controls](web_ui_controls.md) for component contracts, and
[UI canvas](web_ui_canvas.md) for visualizations. Architecture and preview/DSP
rules live in [web architecture](web_architecture.md).

## Rules

1. Fill the viewport with useful content. Page-level scrollbars and centered
   card grids with unused margins are defects.
2. Keep one-screen workspaces. Regions with overflow use their own scroll
   container.
3. Panels meet at 1px separators; shadows are reserved for transient surfaces
   and the raised segmented-control segment.
4. Use dense controls: normally 24–28px tall with 11–13px type.
5. Use colour semantically. Decorative colour is reserved for stem identity
   and instrument displays.
6. Support light and dark equally. A new token exists in both themes.

## Tokens

Use CSS custom properties from `src/index.css` through Tailwind mappings.
Never add a component colour literal. The only exceptions are
`src/lib/canvasTheme.ts` for instrument displays and `src/lib/stems.ts` for
stem identity hues.

| Token | Purpose |
| --- | --- |
| `background` | Workspace substrate |
| `card` | Toolbars, rails, panels, status bar |
| `popover` | Menus and dropdowns |
| `muted`, `secondary`, `accent` | Recessed, resting, and hover controls |
| `border`, `input` | Separators and field edges |
| `foreground`, `muted-foreground` | Primary and secondary text |
| `primary` | Selection and primary action |
| `success` | Playing, completed, enabled |
| `warning` | Solo, queued, held attention |
| `destructive` | Mute, delete, failure |
| `ring` | Focus indicator |

When adding a token, update `:root`, `.dark`, and `tailwind.config.ts`.
`--radius` is 10px and `--topbar-h` is 56px; reference the variables rather
than their resolved values. Keep numeric displays `tabular-nums`.

Type scale: `11px` uppercase only for section headers; `12px` for secondary
copy; `13px` for controls; `14px` for dialog body; `base`/`lg` semibold for
titles and metric values. Labels use sentence case.

## Workspace anatomy

Every routed page composes `Workspace`:

```
top bar (56px)
optional toolbar (44px)
rail (240px) | fluid canvas | inspector (320px)
status bar (32px)
```

- Workspace uses `calc(100vh - var(--topbar-h))` and `overflow-hidden`.
- Rails are `hidden xl:flex`; the canvas must still work when either is gone.
- Use `WorkspaceScroll` for overflowing region content.
- Sidebar is `w-56` expanded / `w-12` collapsed; nav rows are `h-8` and use
  `bg-primary/15 text-primary` when selected.
- Put page titles in `useHeaderTitle`, never a content hero.

A canvas region may have one bottom pane. It has an `h-8` card header,
segmented surface selector, and an `aria-expanded` collapse button. Collapse is
a state, not a third segment. The project Timeline/Mixer pane is the reference.

## Selection and status

| Meaning | Token | Examples |
| --- | --- | --- |
| Primary action or selection | `primary` | Create, active row, range |
| Running or completed | `success` | Play, completed job, switch on |
| Attention or held state | `warning` | Solo, loop, queued |
| Failure or off-air | `destructive` | Mute, delete, failed job |

Solo is `warning`; mute is `destructive`. Do not swap them.

## Accessibility and verification

- Icon-only controls need `aria-label`; toggles need `aria-pressed`.
- Give repeated facet groups unique accessible names.
- Never rely on colour alone. Canvas duplicates state available as text.
- Run `npm test` and `npm run build` for web changes.
- Check affected routes in both themes and desktop/tablet widths: no console
  errors, no page-level overflow, and rails/inspector collapse below `xl`.

For canvas changes, sample rendered pixels; screenshots can hide stale frames
and subtle gradient or hue regressions.
