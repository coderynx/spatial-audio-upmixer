# Phase 1 — MLX backend scaffold + weight conversion

Read `docs/plans/mlx/README.md` first for context and ground rules.

## Goal

Create the `inference/mlx/` package skeleton, the backend-selection
plumbing (opt-in only, default off), and the checkpoint conversion utility
(torch `.ckpt` → safetensors). No model math yet — after this phase the MLX
backend can be selected, load converted weights into a dict of `mx.array`,
and raise a clear "arch not yet implemented" error.

## Context

- Backend detection lives in `packages/core/src/separation/separator.py`
  (`_detect_backend`, line ~22) and returns `"cuda" | "mps" | "cpu"`; the
  `DeviceManager` in `inference/device.py` turns that string into a torch
  device. `StemSeparator.backend` (line ~272) is the public capability
  string — apps/api and apps/web read it, so its value set is a soft
  contract; check `grep -rn "backend" apps/api/src apps/web/src` before
  extending it.
- Model loading: `inference/loader.py` (`load_model`) resolves a filename
  through `inference/registry.py` (`MODEL_REGISTRY`, 14 checkpoints),
  downloads via `_ensure_weights`, unpickles with `_load_state_dict`
  (handles both bare state dicts and `{"state_dict": ...}` wrappers, plus a
  `weights_only=False` fallback for wide-pickled community checkpoints).
- Engine construction happens in `separator.py` (~line 305) which passes the
  loaded model into `SeparationEngine` (`inference/engine.py`).
- MLX must stay an optional dependency; core must import cleanly without it.

## Steps

1. Add a `separation-mlx` extra to `packages/core/pyproject.toml` mirroring
   the `separation-cpu` extra's package set plus `mlx>=0.30` and
   `safetensors`. Update `packages/core/README.md`'s platform notes with one
   line (Apple silicon only).
2. Create `packages/core/src/separation/inference/mlx/` with:
   - `__init__.py` — exports the public loader/engine entry points.
   - `weights.py` — conversion: reuse `loader._load_state_dict` to read the
     torch checkpoint (torch stays available on dev machines; conversion is
     a one-time-per-checkpoint step), map tensors to numpy, write
     `<model_dir>/mlx/<checkpoint-stem>.safetensors` plus a small JSON
     sidecar recording source filename and sha256 of the source checkpoint.
     Loading: if the safetensors file exists and the sidecar sha matches,
     load with `mx.load`; otherwise convert on first use. Key names are
     preserved verbatim at this stage — per-arch key remapping belongs to
     phases 3-5.
   - `device.py` — the MLX counterpart of `DeviceManager`: memory limit via
     `mx.metal.set_memory_limit` (leave headroom on unified memory; start
     with 75% of `mx.metal.device_info()['max_recommended_working_set_size']`),
     and a `clear_cache()` using `mx.metal.clear_cache()`.
3. Backend selection: extend `_detect_backend` to return `"mlx"` when (a)
   platform is Apple silicon, (b) `importlib.util.find_spec("mlx")` is not
   None, and (c) opt-in env var `UPMIXER_MLX=1` is set. Default stays MPS —
   flipping the default is phase 6. Keep the function importable without
   torch or mlx installed (current behavior: guarded imports).
4. In `separator.py`, route `backend == "mlx"` to an MLX engine factory in
   `inference/mlx/`. Until phases 3-5 land, that factory raises
   `NotImplementedError("MLX backend does not support arch '<arch>' yet")` —
   but do the routing now so later phases only register archs.
5. Tests in `packages/core/tests/test_inference_mlx_scaffold.py`:
   - `_detect_backend` returns `"mlx"` only with the env var + module
     present (monkeypatch both; must not require real mlx).
   - Conversion round-trip on a tiny synthetic state dict (build with
     torch, convert, `mx.load`, compare values) — skip test with
     `pytest.importorskip("mlx")`.
   - Sidecar sha mismatch triggers reconversion.
   - Core imports cleanly with mlx absent (simulate via
     `monkeypatch.setitem(sys.modules, ...)` or a subprocess `-c "import
     upmixer"` check).
6. Full suite green: `uv run pytest packages/core/tests apps/api/tests
   apps/cli/tests -q`.

## Out of scope

- Any arch implementation or demix loop.
- Changing the default backend.
- apps/api or apps/web changes (backend string handling there is phase 6 if
  needed at all).

## Done when

- `UPMIXER_MLX=1` + mlx installed → `StemSeparator(...).backend == "mlx"`
  and a separation attempt fails with the explicit NotImplementedError.
- Without the env var or without mlx, behavior is byte-identical to today.
- Conversion produces a safetensors file that `mx.load` reads back with
  values equal to the torch state dict.
- Full suite green.
