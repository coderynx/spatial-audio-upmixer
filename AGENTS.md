# Repository Agent Guide

## Workspace

This is a uv workspace monorepo with four packages. Each has its own nested
`AGENTS.md` for package-specific conventions — read the one for the package
you're changing; it takes precedence over this file for that package's own
detail, per the [agents.md](https://agents.md/) "closest file wins"
convention.

- `packages/core` (`upmixer/`) — the Python library: DSP pipelines, mastering,
  stem separation, binaural/transaural rendering, manifests. No CLI or
  web-specific code lives here. See `packages/core/AGENTS.md`.
- `apps/cli` (`upmixer_cli/`) — the `upmixer` command-line interface,
  consuming only core's public API. See `apps/cli/AGENTS.md`.
- `apps/api` (`upmixer_web/`) — the `upmixer-web` FastAPI server, consuming
  only core's public API. See `apps/api/AGENTS.md`.
- `apps/web` — the React/shadcn/ui client, talking to `apps/api` over
  `/api/*`. See `apps/web/AGENTS.md`.

Each package's source lives directly under its own `src/` (e.g.
`packages/core/src/config.py`, imported as `upmixer.config` via a
`package-dir` remap — there is no `packages/core/src/upmixer/` directory on
disk). `examples/` contains runnable YAML and JSON manifests; tests mirror
features in each package's own `tests/` directory (`packages/core/tests/
test_*.py`, `apps/api/tests/test_*.py`, `apps/cli/tests/test_*.py`).

Parameter precedence, everywhere it applies: CLI flags > manifest values >
profile defaults > `UpmixConfig` defaults.

## Comment Policy

Keep public module, class, and function docstrings intact.

Comments are allowed **only** for: non-obvious DSP or standards constraints,
model-specific quirks, necessary workarounds/hacks, or genuinely strange
behavior — and must stay a few lines at most. No walls of text.

Forbidden: comments that restate what the code already says, bare
section-label banners, architecture/design/rationale prose, TODO/FIXME, and
commented-out code.

Architecture, design rationale, and cross-system parity notes do not belong
in comments. Move them to `docs/` (repo-specific architecture and contracts,
e.g. `docs/web_architecture.md`, `docs/contracts/`) or to
`~/Projects/upmixer-knowledge/` when the content is separation-model,
technique, or mastering/restoration domain intelligence (see Knowledge Base
below). When trimming such a comment, first make sure its substance exists in
the right doc, then leave at most a one-line pointer (e.g. `// see
docs/contracts/preview_export_parity.md §1`).

Exemptions: `# noqa: F401` / `# type: ignore` pragmas; the three public
re-export shim modules, which retain their `# noqa: F401` imports; and
vendored code under `packages/core/src/separation/inference/archs/`, which
tracks upstream and keeps its own comment style as-is.

## File Size Policy

Soft target: ~400 lines per file. Hard cap: ~600 lines — split above this
unless the file is a named exemption below.

Exemptions (must still try to stay reasonable, but not required to split):
vendored code (`packages/core/src/separation/inference/archs/`),
generated/data files, and a file that is genuinely one cohesive
class/dataclass/constant table with no separable concern.

Named long-but-cohesive files (checked when this rule was written; don't
re-split without a new reason): `apps/web/src/features/projects/
previewGraph.ts`, `apps/web/src/features/projects/masteringProfiles.ts`,
`packages/core/src/pipeline.py`, `packages/core/src/separation/separator.py`,
`packages/core/src/separation/stem_router.py`,
`packages/core/src/mastering/match_reference.py`.

How to split: mirror the project's existing per-responsibility
decomposition — a package `__init__.py` re-exporting the original public
names (like `mastering/`, `separation/`, `binaural/`, `crosstalk/`), or
sibling flat modules matching an existing prefix convention (like
`apps/api/src`'s `project_storage.py`/`project_routing.py`). Preserve every
existing import path either via re-export or a compatibility shim (see the
`mastering_comp.py`/`mastering_bass.py`/`mastering_eq.py` shim pattern).
Applies to `packages/core/src/`, `apps/api/src/`, `apps/cli/src/`,
`apps/web/src/`, and each package's `tests/`.

## Code Conventions

Target Python 3.11+ and use four-space indentation, standard type hints,
`snake_case` functions and variables, and `PascalCase` classes. Match the
existing import grouping, keep every import used, and remove imports when
removing features. No formatter or linter is configured; keep changes
PEP 8-aligned and locally consistent.

Prefer direct implementations over speculative abstractions, feature flags,
hypothetical error handling, or compatibility aliases. Validate at system
boundaries (user input, external APIs, and file I/O) and trust internal
invariants. New modules must be imported by production code or have a
documented public-API purpose. Remove unused functions, classes, constants,
parameters, branches, and modules; before deleting uncertain code, search
across all four packages' source and tests (`packages/core`, `apps/cli`,
`apps/api`, `apps/web`).

## Commands

- `uv sync --all-packages --extra dev --extra web-dev --extra manifest`
  installs all three Python packages (core, CLI, API), pytest, and
  development extras into one workspace `.venv`. Add `--extra
  separation-cpu` (or `separation-gpu` on CUDA hosts) to `packages/core`'s
  sync to enable stem separation — see `packages/core/README.md` for
  platform notes.
- `uv run pytest packages/core/tests -q`, `uv run pytest apps/api/tests -q`,
  and `uv run pytest apps/cli/tests -q` run each package's suite; add
  `-k "test_name"` to select a test.
- `uv run pytest packages/core/tests -m perf -s` runs opt-in performance and
  real-model checks; `-k eval` scopes that to the separation evaluation
  harness.
- `cd apps/web && npm install && npm run dev` runs the frontend; `npm run
  build` and `npm test` for build/vitest.

Each Python package builds via setuptools (see its `pyproject.toml`); the
workspace root `pyproject.toml` has no installable code of its own, only
`[tool.uv.workspace]` members.

## Testing and Change Validation

Place coverage in the owning package's `tests/` directory
(`packages/core/tests`, `apps/api/tests`, `apps/cli/tests`) as
`test_<feature>.py` with `test_<behavior>` functions — pick the package by
import surface, not by which change prompted the test. Reuse fixtures from
`packages/core/tests/conftest.py` (core) or `apps/api/tests/conftest.py`
(API, e.g. `web_client`); fixtures must be referenced by at least one test.
Do not add test-only helpers to production code. Mark benchmarks with
`@pytest.mark.perf`.

Run `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q`
before and after substantive changes. The full suite must pass with zero
regressions. When a change affects audio output, also run the relevant CLI
or focused tests and report generated-audio verification details.

## Project reference

Consult these documents when dealing with project and manifests:

[Project manifest parity](docs/project_manifest_parity.md) for project and
manifest parity.

[Objective separation evaluation harness](docs/evaluation_harness.md) for the
SDR/fullness/bleedless metric definitions, corpus format, and
deterministic-settings contract in `packages/core/src/eval/`. Consult before
changing separation models, ensembling, or phase-fix/debleed passes — no
separation-quality change should ship without a report generated by this
harness.

## Standards References

Consult the relevant neutral project reference before changing code governed
by an audio delivery standard:

- [ADM metadata and ITU-R BS.2076](docs/standards/adm_metadata_bs2076.md) for
  ADM-BWF XML and metadata.
- [Dolby Atmos Master ADM Profile](docs/standards/dolby_atmos_profile.md) for
  Atmos delivery constraints.
- [Loudness DSP and ITU-R BS.1770](docs/standards/loudness_dsp_bs1770.md) for
  loudness and true-peak behavior.
- [Spatial layouts and ITU-R BS.775/BS.2051](docs/standards/spatial_layouts_bs775_bs2051.md)
  for speaker layouts, labels, LFE, and downmixes.
- [Spatial Audio Engine (binaural rendering)](docs/standards/spatial_audio_engine.md)
  for the `binaural` rendering pass, Studio/Listening/Flat profiles, and the
  core/web parity contract.
- [Transaural Speaker Rendering (crosstalk cancellation)](docs/standards/transaural_speakers.md)
  for the `transaural` rendering pass, its five speaker-geometry profiles,
  and the XTC filter-design contract.

## Knowledge Base

An external, independently versioned knowledge base lives at
`~/Projects/upmixer-knowledge/` (sibling git repo, not part of this
repository — read it directly by absolute path with `Read`/`Grep`; it is not
fetched or synced automatically). See `packages/core/AGENTS.md` for exactly
when to consult it (model registry changes, ensembling/bleed/phase work,
mastering/restoration stages) and the roadmap pointer.

## Commits and Pull Requests

Use concise Conventional Commit subjects such as `feat: add stem
silence-skip support`, `fix: comply with broadcast specs`, or `perf:
optimize stem separation`. Keep each commit focused and imperative.

Pull requests should explain behavioral and DSP/output impact, list
validation commands, link related issues, and call out optional
dependencies or manifest changes. Include sample CLI output or
generated-audio verification when output changes; screenshots are useful
only for documentation or visual plots.
