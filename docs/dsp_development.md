# DSP Development

Reference for `packages/dsp`. Read `packages/dsp/AGENTS.md` first for the
short set of rules that apply to every change.

## Constants and parity

Core configuration and profile tables own acoustic tuning; `engine_constants()`
delivers it to the browser. DSP functions receive those values as parameters.
Structural math constants, including BS.1770 true-peak FIR data, ambisonic
normalization, and filter-design internals, remain in Rust. Routing presets
remain in `spatial::presets` because the preview resolves them locally.

Use `f64` internally to match NumPy. FFT libraries are not bit-identical, so
parity is a per-stage tolerance contract:

| Stage | Tolerance |
| --- | ---: |
| Filter coefficients | `1e-14` |
| FIR design | `1e-10` |
| Time-domain IIR | `1e-12` |
| FFT convolution | `1e-9` |
| Scalar measurements | `1e-10` |
| Full chain | `1e-6` |

The full-chain threshold is below 24-bit quantization. `kernels/butter.rs` and
`kernels/fir_design.rs` transcribe SciPy 1.18.0 behavior. When that reference
moves, regenerate fixtures and inspect the resulting difference.

## Realtime path

Browser audio runs in 128-frame, 2.67 ms quanta. Changes to `stream/conv.rs`,
`stream/master.rs`, `stream/output.rs`, or `stream/engine/` need a rebuild and
benchmark:

```bash
cd apps/web
npm run build:wasm
npm run bench:engine
```

The streaming convolver is uniform-partitioned overlap-save and transforms the
kernel once. `RouteScalePass` and rendering share
`PreviewEngine::route_stem_block` so normalization measures the rendered path.
The mono-maker processes `MONO_STRIDE` frames using `MONO_HORIZON_MS` context;
do not reduce it to per-quantum zero-phase work.

## Fixtures and builds

`tests/golden/` contains Python-generated parameters, tolerances, and float64
buffers. Regenerate only while Python is still an independent reference:

```bash
uv run python packages/dsp/tools/dump_golden_vectors.py
cd packages/dsp && cargo test
uv run maturin develop --manifest-path packages/dsp/crates/dsp-py/Cargo.toml
```

For a clean Python extension after Rust edits, use:

```bash
uv sync --all-packages --extra dev --extra web-dev --extra manifest \
  --extra separation-cpu --reinstall-package upmixer-dsp
```

Plain `uv sync` and `uv run` can reuse the old wheel. Build the browser
artifact with `cargo build --release --target wasm32-unknown-unknown -p
upmixer-dsp-wasm` when its direct artifact is needed. Retuning presets requires
the Python reinstall and `npm run build:wasm`.
