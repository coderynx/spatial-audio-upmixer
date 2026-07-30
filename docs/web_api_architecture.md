# Web API Architecture (Vertical Slices)

`apps/api` (`upmixer_web/`) is organized as **vertical slices**: each feature
owns its routes, service logic, response views, and schemas in one place,
instead of being spread across technical-layer files (`routes_*.py`,
`models.py`, `schemas.py`, `views.py`) that every feature reached into. This
document is the contract new work in `apps/api` must follow — read it before
adding an endpoint, a route, or a background job. See also
[Web architecture](web_architecture.md) for the delivery-layer overview and
the core/web boundary in `AGENTS.md`.

## Layout

```
apps/api/src/
  api.py                  # thin factory: wires shared infra, registers each slice's routes
  settings.py             # process-wide config, unowned by any single slice

  shared/                 # infrastructure every slice may depend on
    database.py           # engine/session factory, alembic upgrade, Base
    models.py              # every SQLAlchemy ORM model, one Base.metadata
    schemas.py              # ApiModel, the Pydantic response-model base
    storage.py             # ObjectStorage / AudioSource / AudioSink
    manifests.py            # manifest validation + the upmixer core boundary
    metadata.py             # audio tag/cover extraction
    separation.py            # stem-separation capability probe

  worker/                 # background dispatcher, composed from slice runners
    manager.py             # dispatch loop, concurrency control, JobPaused/JobDeleting
    subprocess.py           # child-process isolation for pipeline execution
    __init__.py              # WorkerManager = runner mixins + the manager core

  features/
    system/                 # health, configuration, stem-routing preview, artifact download
    imports/                 # upload ingestion, mastering-reference upload
    jobs/                     # job submission, control (pause/resume/delete), export execution
    projects/                  # project editing, stem preparation, peaks, reference-match
      routes.py schemas.py service.py views.py storage.py routing.py worker.py worker_peaks.py worker_reference_match.py
```

Each feature package under `features/` holds:

- `routes.py` — a `register_<feature>_routes(app, ...)` function taking its
  dependencies as explicit parameters (settings, storage, the worker manager,
  a `database_session` callable, etc.) — the existing manual
  dependency-injection style, not a container. Route bodies stay thin: look
  up an entity, call a `service.py` function, map `ValueError` (or a
  feature-specific subclass) to the right `HTTPException` status, return a
  view.
- `service.py` — the actual business logic: DB reads/writes, state
  transitions, validation. Pure Python, no FastAPI imports. State-machine
  transitions (pause/resume/retry/delete) live here, not inline in routes —
  see `features/jobs/service.py`'s `pause_job`/`resume_job`/`JobStateConflict`
  and `features/projects/service.py`'s `retry_project`/`ProjectStateConflict`
  for the pattern: a function that mutates and commits, raising a
  conflict-specific `ValueError` subclass the route catches ahead of the
  generic one.
- `views.py` — builds the feature's response schema from its ORM model(s),
  filling in URLs and any manager-derived fields (e.g. `*_pending` flags).
- `schemas.py` — the feature's Pydantic request/response models, subclassing
  `upmixer_web.shared.schemas.ApiModel` for `from_attributes` response models.
- `worker.py` / `worker_*.py` (jobs, projects only) — the runner mixin(s) the
  background dispatcher composes for that feature's long-running work. See
  **Worker composition** below.

Only `projects` additionally owns `storage.py` (`ProjectStemStorage`: the
project-isolated stem/preview/peaks/reference-match cache) and `routing.py`
(draggable-stem-position → speaker-gain conversion) — feature-specific
infrastructure that doesn't belong in `shared/` because no other slice uses
it. This mirrors the sibling-flat-module split `AGENTS.md`'s File Size Policy
already prescribes.

## Where a new endpoint goes

A new endpoint belongs to the feature it reads or mutates. Add a route to
that feature's `routes.py`, backed by a `service.py` function; add a schema
if the payload shape is new. **Do not** add a new top-level `routes_*.py`,
grow `shared/models.py` or `shared/schemas.py` with feature-specific shapes,
or put business logic directly in a route body beyond looking the entity up
and mapping errors.

A genuinely new domain (not a variation on imports/jobs/projects/system)
gets its own `features/<name>/` package with the same four-file shape,
registered in `api.py` alongside the existing `register_*_routes` calls.

## Cross-slice imports

Slices are not fully isolated — some dependencies between them are correct
and expected, not violations to eliminate:

- **`projects` routes depend on `jobs`.** Creating a project, saving its
  settings, and exporting it all attach/validate a `MasteringReference` and
  render a `Job` — `features/projects/routes.py` imports
  `job_mastering_reference` from `features.jobs.service` and `job_view` from
  `features.jobs.views`.
- **`jobs`' worker runner depends on `projects`.** An export job carries a
  `project_snapshot` (custom stem routing, manifest overrides), so
  `features/jobs/worker.py`'s `JobRunnerMixin` imports `get_project` from
  `features.projects.service` and `merge_scene`/`routing_for_scene` from
  `features.projects.routing`.
- **Every slice may depend on `shared/`.** `shared/` must never import from
  `features/`.
- **`features/system/service.py`'s `configuration_schema` depends on
  `features.projects.storage`** (for the preview-quality choices it
  surfaces) — the one place a "system" concern reaches into a feature's
  internals, because the configuration endpoint is a cross-feature
  aggregator by nature.

If you find yourself adding a new cross-slice import, prefer the direction
that already exists here (routes may depend on another slice's `service`/
`views`; a runner may depend on another slice's `service`/domain module)
over introducing a new shared module for something only two slices use.

## Worker composition

`upmixer_web.worker.WorkerManager` is not one class in one file — it is
composed from a dispatcher core plus one runner mixin per feature that has
background work:

```python
class WorkerManager(JobRunnerMixin, ProjectRunnerMixin, PeaksMixin, ReferenceMatchMixin, _ManagerCore):
    ...
```

- `worker/manager.py`'s `_ManagerCore` owns the parts every kind of
  background work shares: the dispatch loop, bounded concurrency, and the
  cooperative cancellation checks (`_control`/`_control_project`, raising
  `JobPaused`/`JobDeleting`). It knows nothing about jobs or projects
  specifically — `_submit_available` just calls `self._run_job`/
  `self._run_project`, which the mixins provide.
- `features/jobs/worker.py`'s `JobRunnerMixin` provides `_run_job` and the
  rest of durable-job execution (progress updates, bundling, deletion).
- `features/projects/worker.py`'s `ProjectRunnerMixin` provides `_run_project`
  (stem preparation). `features/projects/worker_peaks.py`'s `PeaksMixin` and
  `features/projects/worker_reference_match.py`'s `ReferenceMatchMixin`
  provide the two coalescing background-executor schedulers projects also
  need (`schedule_peaks`, `schedule_reference_match`).

A mixin reads and writes attributes set up by `_ManagerCore.__init__`/
`start`/`stop` (`sessions`, `source`, `sink`, `work_root`, `stem_cache_dir`,
`project_stems`, `storage`, `_lock`, the executors) rather than taking them as
constructor arguments — the same convention the original `PeaksMixin`/
`ReferenceMatchMixin` established. Adding background work for a new feature
means adding a new mixin there and composing it into `WorkerManager` in
`worker/__init__.py`, not growing an existing mixin with an unrelated
feature's logic.

### Why `worker/__init__.py` imports need care

`worker/__init__.py` imports each feature's runner mixin, which means
importing `upmixer_web.worker` transitively imports every feature's `routes.py`
(Python always runs a package's `__init__.py` before any of its submodules,
so importing `features.jobs.worker` first runs `features/jobs/__init__.py`,
which imports `features/jobs/routes.py`). Any `routes.py` that imports
`WorkerManager` at module level for a type hint would therefore cycle back
into a partially-initialized `upmixer_web.worker`. Both `features/jobs/routes.py`
and `features/projects/routes.py` avoid this with a `TYPE_CHECKING`-guarded
import:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from upmixer_web.worker import WorkerManager
```

`from __future__ import annotations` (PEP 563) means the `manager: WorkerManager`
parameter annotation is a string and is never evaluated at runtime, so the
guard is safe. Keep this pattern for any new slice whose `routes.py` needs
`WorkerManager` for a type hint.

## ORM models stay shared

Every ORM model lives in `shared/models.py` on one `Base`, rather than being
split per feature. `Job`, `Project`, and `MasteringReference` have real
cross-feature foreign keys (`Job.project_id`, `Job.mastering_reference_id`,
`Project.mastering_reference_id`) and a single `Base.metadata` is what
alembic autogenerate and `migrations/env.py` need to see the whole schema at
once. Splitting models per feature would need one metadata per slice or a
manual merge step for migrations, for no isolation benefit — every slice
already freely reads and writes rows owned by other slices' domains (a job
belongs to an import; a project's export creates a job). Response schemas
(`features/*/schemas.py`) are the layer that actually varies per feature and
is worth splitting.
