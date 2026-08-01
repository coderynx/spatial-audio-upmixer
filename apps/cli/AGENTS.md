# apps/cli Agent Guide

Global conventions (comment policy, file size, code style, testing, commits)
live in the root `AGENTS.md` — this file covers only what's specific to the
CLI.

## Scope

`apps/cli/src/` holds only argument parsing, flag application, and
manifest-run orchestration (`args.py`, `flags.py`, `manifest_run.py`,
`__main__.py`); all processing logic stays in `packages/core`. Consuming
only core's public API is the boundary — do not add DSP behavior here.

Parameter precedence: CLI flags > manifest values > profile defaults >
`UpmixConfig` defaults (see the root `AGENTS.md`).

## Usage examples

- `uv run upmixer input.wav output.wav --format 7.1.4 --mode realtime`
  exercises the installed CLI locally.
- `uv run upmixer --manifest examples/atmos_music.yaml`, `uv run upmixer
  --profile-info`, and `uv run upmixer --manifest-keys` inspect common CLI
  workflows.
