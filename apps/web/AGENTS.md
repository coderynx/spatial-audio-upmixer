# Web Agent Guide

Read [UI design](../../docs/web_ui_design.md) before visual work and
[web architecture](../../docs/web_architecture.md) before delivery or preview
work. This package is a delivery layer: DSP lives in `packages/dsp`; the
preview only connects project state to the shared WASM parameter block.

Use `index.css` tokens in both themes. Do not write component colour literals;
only `src/lib/canvasTheme.ts` (instrument displays) and `src/lib/stems.ts`
(stem identity) may contain them.

After a `packages/dsp` change, run `npm run build:wasm` and
`npm run bench:engine`. The committed artifact must match the Python binding,
and preview work must retain the audio-thread budget.
