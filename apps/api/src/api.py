"""FastAPI application factory: wires shared infrastructure and registers
each feature slice's routes. See `docs/web_api_architecture.md`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from upmixer_web.features.imports import register_import_routes
from upmixer_web.features.jobs import register_job_routes
from upmixer_web.features.projects import register_project_routes
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.features.system import register_system_routes
from upmixer_web.settings import Settings
from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
from upmixer_web.shared.separation import separation_capability
from upmixer_web.shared.storage import LocalObjectStorage, StorageAudioSink, StorageAudioSource
from upmixer_web.worker import WorkerManager


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application with injectable settings for tests and deployments."""
    settings = settings or Settings.from_env()
    settings.prepare()
    stem_capability = separation_capability(settings.data_dir / "work")
    upgrade_database(settings.database_url)
    engine: Engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    storage = LocalObjectStorage(settings.data_dir / "objects")
    manager = WorkerManager(
        sessions=sessions,
        storage=storage,
        source=StorageAudioSource(storage),
        sink=StorageAudioSink(storage),
        work_root=settings.data_dir / "work",
        stem_cache_dir=settings.data_dir / "stem-cache",
        project_stems=ProjectStemStorage(settings.data_dir / "project-stems"),
        worker_count=settings.worker_count,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        manager.start()
        yield
        manager.stop()
        engine.dispose()

    app = FastAPI(
        title="Upmixer Web API",
        summary="Manage spatial-audio upmix jobs and album workflows.",
        version="1.0.0",
        root_path=settings.root_path,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.storage = storage
    app.state.manager = manager
    app.state.project_stems = ProjectStemStorage(settings.data_dir / "project-stems")
    app.state.stem_capability = stem_capability

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def database_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    register_system_routes(app, settings, storage, stem_capability, database_session)
    register_import_routes(app, settings, storage, database_session)
    register_job_routes(app, settings, manager, stem_capability, database_session, sessions)
    register_project_routes(app, settings, storage, manager, stem_capability, database_session, sessions)

    frontend_dir = settings.frontend_dir
    if frontend_dir and (frontend_dir / "index.html").is_file():
        assets_dir = frontend_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> Response:
            candidate = (frontend_dir / path).resolve()
            if candidate.is_relative_to(frontend_dir) and candidate.is_file():
                return FileResponse(candidate)
            html = (frontend_dir / "index.html").read_text(encoding="utf-8")
            html = html.replace(
                'name="upmixer-root-path" content=""',
                f'name="upmixer-root-path" content="{settings.root_path}"',
            )
            return HTMLResponse(html)

    return app
