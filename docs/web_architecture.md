# Upmixer Web Architecture

The web application is an additional delivery surface around the existing `upmixer` package. The CLI remains independent and the web worker calls the same public pipelines and manifest parser.

## Components

- `upmixer_web/` exposes a versioned FastAPI API and OpenAPI document.
- `web/` is a React and shadcn/ui client. It does not contain processing logic.
- SQLAlchemy persists imports, jobs, per-track progress, and artifacts. SQLite is the default; install the `web-postgres` extra and supply a PostgreSQL URL without changing repositories or models.
- `ObjectStorage`, `AudioSource`, and `AudioSink` isolate blob access. The first implementation uses local disk. An S3 implementation can materialize sources into worker scratch space and upload sink outputs without changing job orchestration.
- `WorkerManager` recovers interrupted jobs, bounds processing concurrency, and cooperatively pauses running pipelines through their progress callbacks.

## Durable state

Job states are `queued`, `running`, `pause_requested`, `paused`, `completed`, `failed`, and `deleting`. Completed track records are retained during resume, so album jobs continue at the first incomplete track after a pause or service restart.

Source files live under `imports/{import_id}` and outputs under `jobs/{job_id}`. Stem separation uses one shared cache root. Existing `StemCache` keys include source identity, separation plan, sample rate, preview settings, and silence-skip settings. A remix job points at the same imported assets and cache root, so compatible stems are reused while routing and mastering parameters can change freely.

Deleting a job removes its outputs and database records. Shared source imports and stem cache entries remain because other jobs may reference them. Future storage management can add reference-counted import and cache eviction without changing job deletion semantics.

## API

Interactive docs are served at `/api/docs`; the OpenAPI document is `/api/v1/openapi.json`.

- `POST /api/v1/imports` accepts one or more multipart files and matching `relative_paths`. ZIP files are expanded with path and size checks.
- `GET /api/v1/imports/{id}` returns album metadata and track order.
- `GET /api/v1/imports/{id}/assets/{asset_id}/audio` streams an imported source for browser audition and seeking.
- `GET /api/v1/configuration` returns manifest choices and runtime stem-separation capability.
- `POST /api/v1/jobs` creates a job from an import and a CLI-compatible manifest.
- `GET /api/v1/jobs` and `GET /api/v1/jobs/{id}` return durable state.
- `GET /api/v1/jobs/{id}/events` streams state changes as server-sent events.
- `POST /api/v1/jobs/{id}/pause` and `/resume` control execution.
- `POST /api/v1/jobs/{id}/clone` creates a stem-cache-backed remix.
- `DELETE /api/v1/jobs/{id}` removes a job and its outputs.
- `GET /api/v1/artifacts/{id}/download` downloads a track output or album ZIP.

## Extension boundaries

Dolby Encoding Engine integration belongs after `StorageAudioSink`. A future encoder sink can consume WAV or ADM-BWF artifacts, emit stream-ready artifacts, and attach them to the same job. Webhooks should subscribe to committed job transitions rather than pipeline callbacks. Object storage should implement `ObjectStorage`; external library or upload sources should implement `AudioSource`.

## Reverse proxy

Uvicorn trusts only `UPMIXER_FORWARDED_ALLOW_IPS`. Set it to the proxy address or network, not `*`, in exposed deployments. Set `UPMIXER_ROOT_PATH` when the proxy publishes the application beneath a path prefix. The frontend uses same-origin relative API URLs.

## Local development

Use Python 3.11, 3.12, or 3.13 for web stem jobs. Install Python web dependencies with `python3 -m pip install -e ".[dev,web,web-dev,separation-cpu]"`, then run `python3 -m upmixer_web`. The CPU extra also enables MPS acceleration on supported Apple Silicon Macs; reserve `separation-gpu` for NVIDIA CUDA hosts. In `web/`, install packages and run `npm run dev`. Vite proxies `/api` to the backend.

For a GPU container, run `docker compose up --build`. The Compose configuration requests all available NVIDIA GPUs and persists database, imports, cache, and outputs in the `upmixer-data` volume.
