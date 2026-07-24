# Repository Agent Guide

## Project Overview

`upmixer/` is the Python package and CLI entry point for stereo, multichannel, and stem-based spatial upmixing. `examples/` contains runnable YAML and JSON manifests; tests mirror features in `tests/test_*.py`.

Two pipelines share a mastering chain:

- `UpmixPipeline` in `upmixer/pipeline.py` is the realtime/file pipeline. Stereo or mono input is processed through coherence-based STFT analysis, direct/ambient decomposition, routing, and mastering. Multichannel input uses `MultichannelUpmixer` for pass-through and channel derivation.
- `StemUpmixPipeline` in `upmixer/separation/stem_pipeline.py` separates zone audio into instrument stems, analyzes and routes each stem, mixes them, and then masters the result.

Both pipelines finish with `MasteringChain` in `upmixer/mastering/chain.py`: spectral EQ, bus compression, bass control, BS.1770 loudness normalization, true-peak limiting, and soft limiting.

Key modules include `config.py` (`UpmixConfig`), `formats.py` (channel definitions), `manifest.py` (YAML/JSON jobs), `analysis/`, `decomposition/`, `routing/`, `io/`, `mastering/`, and `upmix/`. Keep the public compatibility shims `mastering_comp.py`, `mastering_bass.py`, and `mastering_eq.py` intact.

Parameter precedence is: CLI flags > manifest values > profile defaults > `UpmixConfig` defaults.

## Web Architecture Boundary

`upmixer_web/` and `web/` are delivery layers over existing package and CLI. Keep web-specific state, APIs, UI behavior, capability checks, and error presentation there. Web workers call documented `upmixer` pipelines and manifest APIs; never import private `upmixer` symbols for web behavior.

Do not change `upmixer/` for web feature unless small, independently justified public API change is necessary. Do not move web concerns into core package, alter core behavior for browser-only cases, or patch third-party internals from core code.

Stem inference is provided by `audio-separator`. Web code must not directly import or control Torch, ONNX Runtime, CUDA, MPS, CoreML, model classes, or other inference-framework internals. Let `audio-separator` choose accelerator and use its Python API only for web capability reporting; actual jobs continue through `StemUpmixPipeline`.

## Commands

- `python3 -m pip install -e ".[dev]"` installs the package, CLI, pytest, and development extras.
- `python3 -m pip install -e ".[dev,separation-cpu]"` also enables CPU stem separation.
- `python3 -m pytest -q` runs the complete suite.
- `python3 -m pytest tests/test_pipeline.py -q` runs one module; add `-k "test_name"` to select a test.
- `python3 -m pytest -m perf -s` runs opt-in performance and real-model checks.
- `upmixer input.wav output.wav --format 7.1.4 --mode realtime` exercises the installed CLI locally.
- `upmixer --manifest examples/atmos_music.yaml`, `upmixer --profile-info`, and `upmixer --manifest-keys` inspect common CLI workflows.

Setuptools is configured through `pyproject.toml`; build distributions with `python3 -m build` when the `build` package is installed.

## Code Conventions

Target Python 3.11+ and use four-space indentation, standard type hints, `snake_case` functions and variables, and `PascalCase` classes. Match the existing import grouping, keep every import used, and remove imports when removing features. No formatter or linter is configured; keep changes PEP 8-aligned and locally consistent.

Prefer direct implementations over speculative abstractions, feature flags, hypothetical error handling, or compatibility aliases. Validate at system boundaries (user input, external APIs, and file I/O) and trust internal invariants. New modules must be imported by production code or have a documented public-API purpose. Remove unused functions, classes, constants, parameters, branches, and modules; before deleting uncertain code, search both `upmixer/` and `tests/`.

Keep public module, class, and function docstrings intact. Avoid explanatory inline comments. Use comments only for non-obvious DSP or standards constraints, model-specific quirks, or necessary workarounds. Do not add TODO/FIXME comments or commented-out code. The three public re-export shim modules are the intentional exception to ordinary unused-import rules and retain their `# noqa: F401` imports.

## Testing and Change Validation

Place coverage beside related tests as `test_<feature>.py` with `test_<behavior>` functions. Reuse fixtures from `tests/conftest.py`; fixtures must be referenced by at least one test. Do not add test-only helpers to production code. Mark benchmarks with `@pytest.mark.perf`.

Run `python3 -m pytest -q` before and after substantive changes. The full suite must pass with zero regressions. When a change affects audio output, also run the relevant CLI or focused tests and report generated-audio verification details.

## Project reference

Consult these documents when dealing with project and manifests:

[Project manifest parity](docs/project_manifest_parity.md) for project and manifest parity.

## Standards References

Consult the relevant neutral project reference before changing code governed by an audio delivery standard:

- [ADM metadata and ITU-R BS.2076](docs/standards/adm_metadata_bs2076.md) for ADM-BWF XML and metadata.
- [Dolby Atmos Master ADM Profile](docs/standards/dolby_atmos_profile.md) for Atmos delivery constraints.
- [Loudness DSP and ITU-R BS.1770](docs/standards/loudness_dsp_bs1770.md) for loudness and true-peak behavior.
- [Spatial layouts and ITU-R BS.775/BS.2051](docs/standards/spatial_layouts_bs775_bs2051.md) for speaker layouts, labels, LFE, and downmixes.

## Commits and Pull Requests

Use concise Conventional Commit subjects such as `feat: add stem silence-skip support`, `fix: comply with broadcast specs`, or `perf: optimize stem separation`. Keep each commit focused and imperative.

Pull requests should explain behavioral and DSP/output impact, list validation commands, link related issues, and call out optional dependencies or manifest changes. Include sample CLI output or generated-audio verification when output changes; screenshots are useful only for documentation or visual plots.
