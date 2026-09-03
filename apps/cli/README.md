# upmixer-cli

Command-line interface for the [upmixer](../../packages/core) spatial audio upmixer. Extracted from the core
library so core stays a pure Python API with no argparse/terminal I/O.

## Install

```bash
python3 -m pip install upmixer-cli
python3 -m pip install "upmixer-cli[manifest]"        # YAML manifests
python3 -m pip install "upmixer-cli[separation-cpu]"  # CPU/MPS stem separation
python3 -m pip install "upmixer-cli[separation-gpu]"  # CUDA stem separation
python3 -m pip install "upmixer-cli[separation-mlx]"  # Apple silicon SCNet separation
```

For local development against this workspace, install from the repository root with `uv sync`.

## Usage

```bash
# Positional args (classic mode)
upmixer input.wav output.wav --format 7.1.2 --mode stem

# Manifest-driven (all params in a file)
upmixer --manifest job.yaml

# Mixed: manifest provides defaults, CLI flags override
upmixer --manifest job.yaml input.flac output_override.wav --format 7.1.4
```

Parameter precedence: CLI flags > manifest values > profile defaults > `UpmixConfig` defaults.

`upmixer --manifest-keys` lists valid manifest keys; `examples/` at the repository root has runnable YAML/JSON
manifests.

Note: the module invocation changed from `python -m upmixer` to `python -m upmixer_cli` since the CLI is now its
own package; the installed `upmixer` **command** is unchanged.

## Layout

- `src/__main__.py` — entry point (`main()`), positional/batch/manifest dispatch.
- `src/args.py` — argparse construction (`build_parser`).
- `src/flags.py` — translate parsed `Namespace` into `upmixer.config.UpmixConfig`.
- `src/manifest_run.py` — manifest-driven asset job orchestration.

See `AGENTS.md` for the scope boundary (all processing logic lives in core)
and coding conventions.

## Testing

```bash
uv run pytest apps/cli/tests -q
```
