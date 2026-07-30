# upmixer (core)

Core library for converting mono, stereo, and existing surround audio into higher-channel-count spatial beds. Provides
content-aware spatial processing, optional neural stem separation, and a shared mastering chain, exposed as a Python
API consumed by [`apps/cli`](../../apps/cli) and [`apps/api`](../../apps/api).

## Installation

```bash
python3 -m pip install upmixer
python3 -m pip install "upmixer[separation-cpu]"   # CPU/MPS stem separation
python3 -m pip install "upmixer[separation-gpu]"   # CUDA stem separation
python3 -m pip install "upmixer[manifest]"         # YAML manifest support
```

For local development against this workspace, install from the repository root with `uv sync` (see the root
`README.md`).

## Public API

```python
from upmixer import UpmixConfig, UpmixPipeline, StreamingProcessor, UpmixResult, FORMAT_MAP
from upmixer.separation.stem_pipeline import StemUpmixPipeline
```

- `UpmixPipeline` — realtime/file coherence-based STFT pipeline (mono/stereo input, multichannel pass-through).
- `StemUpmixPipeline` — instrument-stem separation, per-stem analysis/routing, mix, then mastering.
- Both finish through `upmixer.mastering.chain.MasteringChain` (spectral EQ, bus compression, bass control, BS.1770
  loudness normalization, true-peak limiting, soft limiting).
- `upmixer.config.UpmixConfig`, `upmixer.formats` (channel layouts), `upmixer.manifest` (YAML/JSON job manifests),
  `upmixer.batch` / `upmixer.execution` (batch orchestration, preflight, resumable state, reporting) are consumed
  directly by `apps/cli`.

Keep the public compatibility shims `mastering_comp.py`, `mastering_bass.py`, and `mastering_eq.py` intact.

## Modules

`analysis/`, `decomposition/`, `routing/`, `io/`, `mastering/`, `separation/` (incl. the in-core PyTorch inference
engine under `separation/inference/`), `manifest/`, `binaural/` (HOA binaural rendering), `crosstalk/` (transaural
XTC rendering), `eval/` (objective separation evaluation harness), `upmix/`.

## Testing

```bash
uv run pytest packages/core/tests -q
uv run pytest packages/core/tests -m perf -s   # opt-in performance/real-model checks
```

See the repository root `AGENTS.md` for coding conventions, comment policy, and standards references.
