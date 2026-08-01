# apps/web Agent Guide

Global conventions (comment policy, file size, code style, testing, commits)
live in the root `AGENTS.md` — this file covers only what's specific to the
web client.

## Visual specification

`apps/web` follows a fixed visual specification. Read
[Web UI design specification](../../docs/web_ui_design.md) before adding a
page, control, or visual state, and follow its tokens, layout primitives, and
control sizes rather than introducing new ones.

Colours come from the `index.css` tokens (`apps/web/src/index.css`), in both
light and dark — never write a literal colour in a component. The only
sanctioned literals live in `apps/web/src/lib/canvasTheme.ts` (instrument
displays) and `apps/web/src/lib/stems.ts` (per-stem identity hues).

## Core boundary

`apps/web` is a delivery layer, not a place to reimplement or alter DSP
behavior. See `docs/web_architecture.md` and `docs/contracts/` for how the
browser preview mirrors the core export pipeline — any change to a DSP
constant or stage the preview re-implements is bound by
`docs/contracts/preview_export_parity.md`, not just this file.
