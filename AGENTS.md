# Repository Guidelines

## Project Structure & Module Organization

`upmixer/` contains the Python package and CLI entry point. The realtime pipeline lives in `pipeline.py`; stem-based processing is under `separation/`. DSP analysis, channel routing, file I/O, mastering, and layout conversion are grouped in `analysis/`, `routing/`, `io/`, `mastering/`, and `upmix/`. Keep public compatibility shims (`mastering_comp.py`, `mastering_bass.py`, and `mastering_eq.py`) intact. Tests mirror features in `tests/test_*.py`, while `examples/` holds runnable YAML and JSON manifests. Standards notes and repository rules live in `.claude/specs/` and `.claude/rules/`.

## Build, Test, and Development Commands

- `python3 -m pip install -e ".[dev]"` installs the package, CLI, pytest, and development extras.
- `python3 -m pip install -e ".[dev,separation-cpu]"` also enables CPU stem separation.
- `python3 -m pytest -q` runs the complete test suite.
- `python3 -m pytest tests/test_pipeline.py -q` runs one module; add `-k "test_name"` to select a case.
- `python3 -m pytest -m perf -s` runs opt-in performance and real-model checks.
- `upmixer input.wav output.wav --format 7.1.4 --mode realtime` exercises the installed CLI locally.

Setuptools is configured through `pyproject.toml`; build distributions with `python3 -m build` when the `build` package is installed.

## Coding Style & Naming Conventions

Target Python 3.11+ and use four-space indentation, standard type hints, `snake_case` functions and variables, and `PascalCase` classes. Match the existing import grouping and remove unused imports. No formatter or linter is configured, so keep changes PEP 8-aligned and locally consistent. Public modules, classes, and functions retain docstrings. Avoid explanatory inline comments; reserve comments for non-obvious DSP, model, or standards constraints. Prefer direct implementations over speculative abstractions or compatibility aliases.

## Testing Guidelines

Use pytest and place new coverage beside related tests as `test_<feature>.py` with `test_<behavior>` functions. Reuse fixtures from `tests/conftest.py`; do not add test-only helpers to production code. Mark benchmarks with `@pytest.mark.perf`. Run the full suite before and after substantive changes, with zero regressions.

## Commit & Pull Request Guidelines

History follows concise Conventional Commit subjects such as `feat: add stem silence-skip support`, `fix: comply with broadcast specs`, and `perf: optimize stem separation`. Use an imperative, scoped summary and keep each commit focused. Pull requests should explain behavior and DSP/output impact, list validation commands, link relevant issues, and call out optional dependencies or manifest changes. Include sample CLI output or generated-audio verification details when results change; screenshots are only useful for documentation or visual plots.
