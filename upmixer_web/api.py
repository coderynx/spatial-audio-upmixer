"""FastAPI application and versioned external API."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
from upmixer_web.imports import ingest_mastering_reference, ingest_uploads
from upmixer_web.jobs import clone_job, create_job, get_job, list_jobs
from upmixer_web.manifests import (
    configuration_schema,
    ensure_stem_separation_available,
)
from upmixer_web.models import Artifact, ImportBatch, Job, MasteringReference, MediaAsset
from upmixer_web.separation import separation_capability
from upmixer_web.schemas import (
    CloneJobRequest,
    CreateJobRequest,
    HealthResponse,
    ImportView,
    JobActionResponse,
    JobView,
    MasteringReferenceView,
)
from upmixer_web.settings import Settings
from upmixer_web.storage import LocalObjectStorage, StorageAudioSink, StorageAudioSource
from upmixer_web.worker import WorkerManager


def _import_view(batch: ImportBatch, root_path: str = "") -> ImportView:
    view = ImportView.model_validate(batch)
    if batch.cover_key:
        view.cover_url = f"{root_path}/api/v1/imports/{batch.id}/cover"
    for asset in view.assets:
        asset.audio_url = (
            f"{root_path}/api/v1/imports/{batch.id}/assets/{asset.id}/audio"
        )
    return view


def _job_view(job: Job, root_path: str = "") -> JobView:
    view = JobView.model_validate(job)
    artifact_urls = {
        artifact.id: f"{root_path}/api/v1/artifacts/{artifact.id}/download"
        for artifact in job.artifacts
    }
    for artifact in view.artifacts:
        artifact.download_url = artifact_urls[artifact.id]
    for track in view.tracks:
        track.asset.audio_url = (
            f"{root_path}/api/v1/imports/{job.import_id}/assets/"
            f"{track.asset.id}/audio"
        )
        for artifact in track.artifacts:
            artifact.download_url = artifact_urls[artifact.id]
    return view


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

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(workers=settings.worker_count)

    @app.get("/api/v1/configuration", tags=["system"])
    def get_configuration() -> dict:
        return configuration_schema(stem_capability)

    @app.post("/api/v1/imports", response_model=ImportView, status_code=status.HTTP_201_CREATED, tags=["imports"])
    def create_import(
        files: list[UploadFile] = File(...),
        relative_paths: list[str] = Form(default=[]),
        session: Session = Depends(database_session),
    ) -> ImportView:
        try:
            batch = ingest_uploads(
                session,
                storage,
                settings.data_dir / "work",
                files,
                relative_paths,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _import_view(batch, settings.root_path)

    @app.get("/api/v1/imports/{import_id}", response_model=ImportView, tags=["imports"])
    def read_import(import_id: str, session: Session = Depends(database_session)) -> ImportView:
        batch = session.get(ImportBatch, import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        return _import_view(batch, settings.root_path)

    @app.post(
        "/api/v1/imports/{import_id}/mastering-references",
        response_model=MasteringReferenceView,
        status_code=status.HTTP_201_CREATED,
        tags=["imports"],
    )
    def create_mastering_reference(
        import_id: str,
        file: UploadFile = File(...),
        session: Session = Depends(database_session),
    ) -> MasteringReferenceView:
        batch = session.get(ImportBatch, import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        try:
            reference = ingest_mastering_reference(
                session, storage, settings.data_dir / "work", batch, file
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return MasteringReferenceView.model_validate(reference)

    def job_mastering_reference(
        session: Session,
        import_batch: ImportBatch,
        reference_id: str | None,
    ) -> MasteringReference | None:
        if reference_id is None:
            return None
        reference = session.get(MasteringReference, reference_id)
        if not reference or reference.import_id != import_batch.id:
            raise ValueError("Mastering reference does not belong to this import")
        return reference

    @app.get("/api/v1/imports/{import_id}/cover", tags=["imports"])
    def read_cover(import_id: str, session: Session = Depends(database_session)) -> FileResponse:
        batch = session.get(ImportBatch, import_id)
        if not batch or not batch.cover_key:
            raise HTTPException(status_code=404, detail="Cover not found")
        path = storage.local_path(batch.cover_key)
        content_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(path, media_type=content_type or "image/jpeg")

    @app.get(
        "/api/v1/imports/{import_id}/assets/{asset_id}/audio",
        tags=["imports"],
    )
    def read_source_audio(
        import_id: str,
        asset_id: str,
        session: Session = Depends(database_session),
    ) -> FileResponse:
        asset = session.get(MediaAsset, asset_id)
        if not asset or asset.import_id != import_id:
            raise HTTPException(status_code=404, detail="Audio asset not found")
        path = storage.local_path(asset.storage_key)
        content_type, _ = mimetypes.guess_type(asset.filename)
        return FileResponse(path, media_type=content_type or "application/octet-stream")

    @app.post("/api/v1/jobs", response_model=JobView, status_code=status.HTTP_201_CREATED, tags=["jobs"])
    def submit_job(request: CreateJobRequest, session: Session = Depends(database_session)) -> JobView:
        batch = session.get(ImportBatch, request.import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        try:
            if request.start:
                ensure_stem_separation_available(request.manifest, stem_capability)
            reference = job_mastering_reference(
                session, batch, request.mastering_reference_id
            )
            job = create_job(
                session,
                batch,
                request.name,
                request.manifest,
                request.start,
                mastering_reference=reference,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if request.start:
            manager.notify()
        return _job_view(job, settings.root_path)

    @app.get("/api/v1/jobs", response_model=list[JobView], tags=["jobs"])
    def read_jobs(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: Session = Depends(database_session),
    ) -> list[JobView]:
        return [_job_view(job, settings.root_path) for job in list_jobs(session, limit, offset)]

    @app.get("/api/v1/jobs/{job_id}", response_model=JobView, tags=["jobs"])
    def read_job(job_id: str, session: Session = Depends(database_session)) -> JobView:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_view(job, settings.root_path)

    @app.post("/api/v1/jobs/{job_id}/pause", response_model=JobActionResponse, tags=["jobs"])
    def pause_job(job_id: str, session: Session = Depends(database_session)) -> JobActionResponse:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status == "queued":
            job.status = "paused"
            for track in job.tracks:
                if track.status == "queued":
                    track.status = "paused"
        elif job.status == "running":
            job.status = "pause_requested"
        elif job.status not in {"paused", "pause_requested"}:
            raise HTTPException(status_code=409, detail=f"Cannot pause {job.status} job")
        job.status_message = "Pause requested"
        session.commit()
        return JobActionResponse(id=job.id, status=job.status)

    @app.post("/api/v1/jobs/{job_id}/resume", response_model=JobActionResponse, tags=["jobs"])
    def resume_job(job_id: str, session: Session = Depends(database_session)) -> JobActionResponse:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"paused", "failed"}:
            raise HTTPException(status_code=409, detail=f"Cannot resume {job.status} job")
        try:
            ensure_stem_separation_available(job.manifest, stem_capability)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job.status = "queued"
        job.error = None
        job.status_message = "Waiting for worker"
        for track in job.tracks:
            if track.status != "completed":
                track.status = "queued"
                track.error = None
        session.commit()
        manager.notify()
        return JobActionResponse(id=job.id, status=job.status)

    @app.post("/api/v1/jobs/{job_id}/clone", response_model=JobView, status_code=status.HTTP_201_CREATED, tags=["jobs"])
    def remix_job(job_id: str, request: CloneJobRequest, session: Session = Depends(database_session)) -> JobView:
        source = get_job(session, job_id)
        if not source:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            if request.start:
                ensure_stem_separation_available(
                    request.manifest or source.manifest,
                    stem_capability,
                )
            reference_id = (
                request.mastering_reference_id
                if "mastering_reference_id" in request.model_fields_set
                else source.mastering_reference_id
            )
            reference = job_mastering_reference(
                session, source.import_batch, reference_id
            )
            job = clone_job(
                session,
                source,
                request.name,
                request.manifest,
                request.start,
                reference,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if request.start:
            manager.notify()
        return _job_view(job, settings.root_path)

    @app.delete("/api/v1/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["jobs"])
    def delete_job(job_id: str, session: Session = Depends(database_session)) -> Response:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in {"running", "pause_requested"}:
            job.status = "deleting"
            job.status_message = "Stopping worker before deletion"
            session.commit()
        else:
            session.close()
            manager.delete_now(job_id)
        manager.notify()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/jobs/{job_id}/events", tags=["jobs"])
    async def job_events(job_id: str) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            previous = ""
            while True:
                with sessions() as session:
                    job = get_job(session, job_id)
                    if not job:
                        yield "event: deleted\ndata: {}\n\n"
                        break
                    payload = _job_view(job, settings.root_path).model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    yield f"data: {encoded}\n\n"
                    previous = encoded
                if payload["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/artifacts/{artifact_id}/download", tags=["artifacts"])
    def download_artifact(artifact_id: str, session: Session = Depends(database_session)) -> FileResponse:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(
            storage.local_path(artifact.storage_key),
            media_type=artifact.content_type,
            filename=artifact.filename,
        )

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
