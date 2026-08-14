# Shared DSP core (WebAssembly)

`upmixer_dsp.wasm` is `packages/dsp/crates/dsp-wasm` built for
`wasm32-unknown-unknown`. It is the *same code* the export pipeline runs
through PyO3, so the preview is not a second implementation of the DSP —
see `docs/contracts/preview_export_parity.md`.

Rebuild after any change under `packages/dsp`:

```
cd apps/web && npm run build:wasm
```

The artifact is committed, like the `hrir/` and `xtc/` filter WAVs, so a
frontend checkout builds and runs without a Rust toolchain. `dsp_core_version`
is exported alongside the DSP entry points so a stale artifact surfaces as a
version mismatch rather than as two different algorithms rendering.
