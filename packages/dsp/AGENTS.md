# DSP Agent Guide

Read the root guide and [DSP development](../../docs/dsp_development.md).
`dsp-core` owns pure Rust DSP; `dsp-py` exposes PyO3 and `dsp-wasm` exposes a
plain C ABI for the AudioWorklet. Separation inference, IO, orchestration, and
manifests remain in core.

## Non-negotiable contracts

- Use `f64` internally. Preserve the tolerance budgets in the development
  guide and regenerate golden fixtures only from the independent Python stage.
- Acoustic tuning belongs in core configuration/profile tables and arrives as
  parameters. Structural DSP constants stay here. `spatial::presets` is the
  intentional local-preview exception.
- A `stream/` change requires `npm run build:wasm` and
  `npm run bench:engine` in `apps/web`; a 128-frame quantum has a 2.67 ms
  deadline and an overrun emits silence.
- Keep `StreamingConvolver` partitioned and transformed once. Route-scale
  measurement and rendering must share `PreviewEngine::route_stem_block`.
- After Rust edits, use the reinstall command in the development guide so
  tests do not run a cached extension. `dsp_core_version()` must stay exported
  from both bindings.
