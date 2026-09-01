# Contributing

## Repository layout

This is a uv workspace monorepo:

- `packages/core` — the `upmixer` Python library (DSP pipelines, mastering, stem separation, binaural/transaural
  rendering, manifests). No CLI or web-specific code.
- `apps/cli` — the `upmixer` command-line interface (`upmixer_cli`), consuming core's public API only.
- `apps/api` — the `upmixer-web` FastAPI server (`upmixer_web`), consuming core's public API only.
- `apps/web` — the React/shadcn/ui client, talking to `apps/api` over `/api/*`.

Read `AGENTS.md` and the linked [agent workflow](docs/agent_workflow.md) before
making a non-trivial change. The [documentation map](docs/README.md) points to
task-specific contracts.

## Setup

```bash
uv sync --all-packages --extra dev --extra web-dev --extra manifest
```

Add `--extra separation-cpu` (or `separation-gpu` on CUDA hosts) for stem separation; see `packages/core/README.md`
for platform notes.

```bash
cd apps/web && npm install
```

## Running tests

```bash
uv run pytest packages/core/tests -q
uv run pytest apps/api/tests -q
uv run pytest apps/cli/tests -q
uv run pytest packages/core/tests -m perf -s   # opt-in performance/real-model checks
cd apps/web && npm test
```

Run the relevant suite before and after any substantive change; the full suite must pass with zero regressions.

## Commits and pull requests

Use concise Conventional Commit subjects (`feat: add stem silence-skip support`, `fix: comply with broadcast
specs`, `perf: optimize stem separation`). Keep each commit focused and imperative.

Pull requests should explain behavioral and DSP/output impact, list validation commands run, link related issues,
and call out optional dependencies or manifest changes. Include sample CLI output or generated-audio verification
when output changes.

## Code conventions

Follow the [agent workflow](docs/agent_workflow.md). Python has no configured
formatter or linter; keep it PEP 8-aligned. The frontend uses ESLint/Prettier
(`npm run lint`, `npm run format` in `apps/web`).
