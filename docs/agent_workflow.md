# Agent Workflow

Repository-wide rules for agents and contributors. Package guides add only the
rules that differ locally.

## Before editing

1. Read the closest `AGENTS.md` and the task reference named in the root guide.
2. Trace callers before changing shared behavior; fix the shared cause rather
   than a single symptom.
3. Search all packages and tests before removing uncertain code.

## Code and comments

Target Python 3.11+. Use four-space indentation, standard type hints,
`snake_case` functions/variables, and `PascalCase` classes. Match local import
grouping; remove unused imports and code.

Keep public module, class, and function docstrings. Comments are only for
non-obvious DSP or standards constraints, model quirks, necessary workarounds,
or genuinely surprising behavior. Keep them short. Do not add restatements,
section banners, TODO/FIXME notes, commented-out code, or architecture prose.

Put repository contracts in `docs/`. Put separation-model, ensembling,
mastering, or restoration technique knowledge in the external
`~/Projects/upmixer-knowledge/` repository when the relevant package guide
requires it. `# noqa: F401` and `# type: ignore` pragmas are allowed; vendored
`packages/core/src/separation/inference/archs/` keeps upstream comment style.

## File boundaries

Aim for roughly 400 lines and split normal source or test files above 600.
Generated/data files, vendored code, and a genuinely cohesive class, dataclass,
or constant table may exceed that limit.

Split by an existing responsibility boundary. Preserve import paths with a
package re-export or a compatibility shim. Do not re-split these coherent files
without a new reason: `apps/web/src/features/projects/masteringProfiles.ts`,
`packages/core/src/pipeline.py`, `packages/core/src/separation/separator.py`,
`packages/core/src/separation/stem_router.py`, and
`packages/core/src/mastering/match_reference.py`.

## Tests and validation

Place Python tests in the owning package as `test_<feature>.py` with
`test_<behavior>` functions. Reuse existing fixtures; do not add test-only
production helpers. Mark benchmarks with `@pytest.mark.perf`.

Run the affected suite during implementation. Before a substantive Python
change, run:

```bash
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
```

Run `uv run pytest packages/core/tests -m perf -s` for opt-in performance or
real-model checks. For audio-output changes, also run focused CLI or package
tests and report what output was verified. For web work, run `npm test` and
`npm run build` in `apps/web`; on macOS also run `npm run tauri:build`. CI
builds the desktop bundle on every change.

## Review and delivery

Use a focused Conventional Commit subject such as `feat: add stem silence-skip
support` or `fix: comply with broadcast specs`. Pull requests state the
behavioral and DSP/output impact, validation commands, relevant issue, and any
optional dependency or manifest change. Include generated-audio verification
when output changes.
