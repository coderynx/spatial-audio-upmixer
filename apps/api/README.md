# upmixer-web

FastAPI web API for the [upmixer](../../packages/core) spatial audio upmixer. Adds interactive track and album
workflows on top of the core pipelines and manifests; the CLI (`apps/cli`) remains independent.

## Install and run

```bash
uv sync --package upmixer-web
uv run --package upmixer-web upmixer-web
```

Or with plain pip from the repository root:

```bash
python3 -m pip install -e packages/core -e apps/api
python3 -m upmixer_web
```

API documentation is served at `http://localhost:8000/api/docs`; the OpenAPI document is at
`/api/v1/openapi.json`. See [Web architecture](../../docs/web_architecture.md) for persistence, storage
interfaces, job states, endpoints, reverse-proxy setup, GPU containers, and extension boundaries.

## Configuration

Environment variables (see `.env.example` at the repository root): `UPMIXER_DATA_DIR`, `UPMIXER_DATABASE_URL`,
`UPMIXER_HOST`, `UPMIXER_PORT`, `UPMIXER_WORKERS`, `UPMIXER_ALLOWED_ORIGINS`, `UPMIXER_FRONTEND_DIR`,
`UPMIXER_ROOT_PATH`, `UPMIXER_FORWARDED_ALLOW_IPS`.

`UPMIXER_FRONTEND_DIR` points at the built `apps/web` client (`apps/web/dist`) so the API can serve it as a static
SPA alongside `/api/*` routes.

`UPMIXER_DATA_DIR` defaults to `./data` relative to the process's working directory. Run `upmixer-web` with cwd
`apps/api` (where the on-disk `data/` directory lives) or set `UPMIXER_DATA_DIR` explicitly if launching from
elsewhere.

## Database migrations

Alembic migrations live in `src/migrations/`. `upmixer_web.database.upgrade_database()` runs them automatically at
startup, resolving `script_location` relative to the installed package — no `alembic.ini` needed at runtime.

For manual/dev alembic CLI use, `alembic.ini` at this package root points `script_location` at `src/migrations`:

```bash
cd apps/api
alembic upgrade head
alembic revision -m "add some_column"
```

## Boundary with core

Import only the public `upmixer` API — `upmixer.config`, `upmixer.formats`, `upmixer.manifest`,
`upmixer.pipeline`, `upmixer.mastering.*`, `upmixer.separation.{separator,stem_pipeline,stem_plan,stem_router,stem_eq}`
— never private/underscore symbols. Web-specific state, capability checks, and error presentation stay here; core
behavior is never altered for browser-only cases.

## Testing

```bash
uv run pytest apps/api/tests -q
```
