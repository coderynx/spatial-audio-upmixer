# apps/api Agent Guide

Global conventions (comment policy, file size, code style, testing, commits)
live in the root `AGENTS.md` — this file covers only what's specific to the
web API. Read [Web API architecture](../../docs/web_api_architecture.md) for
the full vertical-slice layout and cross-slice import rules before adding an
endpoint, route, or background job; read
[Web architecture](../../docs/web_architecture.md) for the delivery-layer
overview.

## Core boundary

`apps/api` is a delivery layer over `packages/core`, not a place to add DSP
behavior. Keep web-specific state, APIs, capability checks, and error
presentation here. Web workers call documented `upmixer` pipelines and
manifest APIs; never import private `upmixer` symbols for web behavior. Do
not change `packages/core` for a web feature unless a small, independently
justified public API change is necessary.

Stem inference is an in-core PyTorch engine (see `packages/core/AGENTS.md`).
Web code must not directly import or control Torch, model classes, device
objects, or other inference-framework internals — call the public
`StemSeparator`/`StemUpmixPipeline` surface only; actual jobs continue
through `StemUpmixPipeline`.

## Vertical slices

`apps/api/src/` is organized as vertical slices
(`features/<name>/{routes,service,views,schemas}`, plus shared `shared/`
infrastructure and a composed `worker/`) rather than by technical layer. Add
a new endpoint to the feature slice it reads or mutates — not a new
top-level `routes_*.py` or a shared `models.py`/`schemas.py` grab-bag. See
`docs/web_api_architecture.md` for the layout, cross-slice import rules, and
the worker-mixin composition pattern (`WorkerManager` is composed from a
dispatcher core plus one runner mixin per feature with background work).
