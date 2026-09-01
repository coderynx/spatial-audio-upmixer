# Repository Agent Guide

Read the closest `AGENTS.md` for the package you change; it overrides this
guide. Read [Agent workflow](docs/agent_workflow.md) before a non-trivial
change. It contains the repository-wide rules that do not need to occupy every
agent's initial context.

## Workspace

| Area | Owns | Boundary |
| --- | --- | --- |
| `packages/core` | Python DSP, manifests, separation, mastering | No CLI or web code |
| `packages/dsp` | Shared Rust DSP, PyO3, WASM | No inference, IO, or orchestration |
| `apps/cli` | `upmixer` arguments and manifest runs | Public core API only |
| `apps/api` | FastAPI delivery layer | Public core API only |
| `apps/web` | React delivery layer and WASM preview glue | No DSP implementation |

Source is directly under each package's `src/`; for example,
`packages/core/src/config.py` imports as `upmixer.config`. Tests live in the
owning package's `tests/` directory (web tests are colocated).

Parameter precedence is always: CLI flags > manifest values > profile defaults
> `UpmixConfig` defaults.

## Working rules

- Preserve public imports and use a public core API across package boundaries.
- Keep comments only for non-obvious constraints, quirks, or workarounds.
  Put durable rationale in `docs/`, not source comments.
- Prefer direct code; validate at external boundaries and trust internal
  invariants. Remove dead code rather than preserving speculative flexibility.
- Keep normal source and test files under 600 lines; split by responsibility
  and retain old public imports with a re-export when necessary.
- Add focused coverage in the package that owns the changed import surface.
  Run the relevant suite; audio changes also need output verification.

## Commands

```bash
uv sync --all-packages --extra dev --extra web-dev --extra manifest
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
cd apps/web && npm test && npm run build
```

Add `--extra separation-cpu` (or `separation-gpu` on CUDA hosts) for stem
separation. Package-specific build and validation commands live in its guide.

## Task references

| Change | Read first |
| --- | --- |
| Project or manifest | [Project/manifest parity](docs/project_manifest_parity.md) |
| Separation quality | [Evaluation harness](docs/evaluation_harness.md) and `packages/core/AGENTS.md` |
| ADM/BWF | [ADM metadata](docs/standards/adm_metadata_bs2076.md) |
| Atmos delivery | [Atmos profile](docs/standards/dolby_atmos_profile.md) |
| Loudness or true peak | [Loudness DSP](docs/standards/loudness_dsp_bs1770.md) |
| Layout, LFE, or downmix | [Spatial layouts](docs/standards/spatial_layouts_bs775_bs2051.md) |
| Binaural or transaural | [Binaural](docs/standards/spatial_audio_engine.md) or [transaural](docs/standards/transaural_speakers.md) |
| Browser preview/export parity | [Parity contract](docs/contracts/preview_export_parity.md) |
| API or web UI | [Web API architecture](docs/web_api_architecture.md), [web architecture](docs/web_architecture.md), or [UI design](docs/web_ui_design.md) |

Use concise Conventional Commit subjects. A pull request records behavioral or
audio impact and the validation actually run.
