# packages/core Agent Guide

Global conventions (comment policy, file size, code style, testing, commits)
live in the root `AGENTS.md` — this file covers only what's specific to the
core library. No CLI or web-specific code lives here; keep it that way.

## Layout and pipelines

Source lives directly under `src/` (e.g. `src/config.py`, imported as
`upmixer.config` via a `package-dir` remap — there is no `src/upmixer/`
directory on disk). Key modules: `config.py` (`UpmixConfig`), `formats.py`
(channel definitions), `manifest/` (YAML/JSON jobs), `analysis/`, `io/`,
`mastering/`, `separation/` (incl. the in-core PyTorch inference engine under
`separation/inference/`), `binaural/` (HOA binaural rendering), `crosstalk/`
(transaural XTC rendering), and `eval/` (objective separation evaluation
harness — see `docs/evaluation_harness.md`).

One pipeline:

- `StemUpmixPipeline` (`src/separation/stem_pipeline.py`) separates zone
  audio into instrument stems via the in-core inference engine
  (`src/separation/inference/`), analyzes and routes each stem, mixes them,
  and then masters the result.

It finishes with `MasteringChain` (`src/mastering/chain.py`): spectral EQ,
bus compression, bass control, BS.1770 loudness normalization, true-peak
limiting, and soft limiting.

Keep the public compatibility shims `mastering_comp.py`, `mastering_bass.py`,
and `mastering_eq.py` intact.

## In-core inference boundary

Stem inference (architectures, model registry, chunked demix, device
management) is an in-core PyTorch engine under `src/separation/inference/`.
Web and CLI code must not directly import or control Torch, model classes,
device objects, or other inference-framework internals — the public
`StemSeparator`/`StemUpmixPipeline` surface is the only supported entry
point (e.g. `StemSeparator(...).backend` for capability reporting); actual
jobs continue through `StemUpmixPipeline`.

## Knowledge Base

Consult `~/Projects/upmixer-knowledge/README.md` (external sibling repo, not
synced automatically — read by absolute path) before:

- adding or swapping separation models in `src/separation/stem_plan.py`
  (stem-to-model mapping) and `src/separation/inference/registry.py`
  (architecture, config, weights per checkpoint) — model registries are
  cataloged in `~/Projects/upmixer-knowledge/models/`,
- implementing ensembling, chained separation, or bleed/phase post-processing
  (`~/Projects/upmixer-knowledge/techniques/`),
- adding mastering or restoration stages
  (`~/Projects/upmixer-knowledge/techniques/mastering_restoration.md`,
  `~/Projects/upmixer-knowledge/models/restoration.md`).

The improvement roadmap lives at `~/Projects/upmixer-knowledge/roadmap.md`.
If the directory is missing (different machine/environment), say so rather
than guessing its contents.

No separation-quality change (model swap, ensembling, phase-fix/debleed)
should ship without a report from `docs/evaluation_harness.md`'s harness.
