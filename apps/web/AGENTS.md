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

`apps/web` is a delivery layer. It contains **no DSP**: the preview runs the
shared Rust core (`packages/dsp`) as WebAssembly in
`public/dsp.worklet.js`, the same code the export pipeline runs. Do not add
filter design, convolution, level math, or acoustic constants here — put
them in the core, where both sides get them.

`src/features/projects/wasmEngine/` is the glue: it compiles the wasm, hands
stems over, and maps the project's mix onto the core's parameter block.

After any change under `packages/dsp`, rebuild the committed artifact with
`npm run build:wasm` — it is not built on install, and a stale one ships a
different algorithm to the browser. See `docs/web_architecture.md` and
`docs/contracts/preview_export_parity.md`.

Then run `npm run bench:engine`. The preview renders on the audio thread, so
every 128-frame quantum has 2.67 ms; over budget the callback starves and the
node — which is the *source* — emits silence rather than degrading. No
correctness test can see that, so the budget in
`docs/contracts/preview_export_parity.md` §4 is its own gate.
