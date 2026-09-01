# Core Agent Guide

Read the root guide and [agent workflow](../../docs/agent_workflow.md). This
package owns library behavior only: no CLI or web-specific code.

## Map and boundaries

`src/` contains `config.py`, `formats.py`, `manifest/`, `analysis/`, `io/`,
`mastering/`, `separation/`, `binaural/`, `crosstalk/`, and `eval/`.
`StemUpmixPipeline` is the separation → analysis/routing → mix → mastering
pipeline; it finishes through `MasteringChain`. Keep the public
`mastering_comp.py`, `mastering_bass.py`, and `mastering_eq.py` shims.

Inference internals stay under `src/separation/inference/`. CLI and web code
may use only `StemSeparator` or `StemUpmixPipeline`; they must not control
Torch, model classes, or devices directly.

## Quality-changing work

Read `~/Projects/upmixer-knowledge/README.md` before changing model registry or
stem plans, ensembling, bleed/phase processing, mastering, or restoration.
Use its `models/`, `techniques/`, and `roadmap.md` references as applicable.
If the sibling repository is unavailable, say so; do not invent its contents.

No separation-quality change ships without a report from the
[evaluation harness](../../docs/evaluation_harness.md).
