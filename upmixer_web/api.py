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
from upmixer_web.models import Artifact, ImportBatch, Job, MasteringReference, MediaAsset, Project, ProjectStem, ProjectTrack
from upmixer_web.project_storage import ProjectStemStorage
from upmixer_web.projects import (
    create_project,
    expand_project_stems,
    get_project,
    list_projects,
    project_export_job,
    update_project_settings,
    update_track_settings,
)
from upmixer_web.separation import separation_capability
from upmixer_web.schemas import (
    CloneJobRequest,
    CreateJobRequest,
    HealthResponse,
    ImportView,
    JobActionResponse,
    JobView,
    MasteringReferenceView,
    CreateProjectRequest,
    ExpandProjectStemsRequest,
    ProjectView,
    ResolveStemRoutingRequest,
    UpdateProjectSettingsRequest,
    UpdateProjectTrackSettingsRequest,
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


def _project_view(project: Project, root_path: str = "") -> ProjectView:
    view = ProjectView.model_validate(project)
    stem_by_id = {stem.id: stem for stem in project.stems}
    for track in view.tracks:
        track.asset.audio_url = (
            f"{root_path}/api/v1/imports/{project.import_id}/assets/{track.asset.id}/audio"
        )
        track.source_preview_url = (
            f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/source-preview"
        )
        for stem in track.stems:
            base_url = (
                f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/"
                f"stems/{stem.id}/audio"
            )
            stem.audio_url = base_url
            if stem_by_id[stem.id].preview_relative_path:
                stem.preview_url = f"{base_url}?quality=preview"
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

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(workers=settings.worker_count)

    @app.get("/api/v1/configuration", tags=["system"])
    def get_configuration() -> dict:
        return configuration_schema(stem_capability)

    @app.post("/api/v1/stem-routing/resolve", tags=["system"])
    def resolve_stem_routing(request: ResolveStemRoutingRequest) -> dict[str, dict[str, float]]:
        from upmixer.formats import FORMAT_MAP
        from upmixer.separation.stem_plan import normalize_stems
        from upmixer.separation.stem_router import build_stem_routing

        if request.channel_layout not in FORMAT_MAP:
            raise HTTPException(status_code=422, detail="Unknown channel layout")
        try:
            stems = normalize_stems(request.stems)
            return build_stem_routing(
                stems, FORMAT_MAP[request.channel_layout], request.preset,
                request.intensity,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    @app.post("/api/v1/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def create_project_route(request: CreateProjectRequest, session: Session = Depends(database_session)) -> ProjectView:
        batch = session.get(ImportBatch, request.import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        try:
            project_manifest = dict(request.manifest)
            project_manifest["engine"] = {
                **dict(project_manifest.get("engine", {})),
                "mode": "stem",
            }
            ensure_stem_separation_available(project_manifest, stem_capability)
            reference = job_mastering_reference(
                session, batch, request.mastering_reference_id
            )
            project = create_project(
                session, batch, request.name, request.manifest, request.scene,
                mastering_reference=reference,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        manager.notify()
        return _project_view(project, settings.root_path)

    @app.get("/api/v1/projects", response_model=list[ProjectView], tags=["projects"])
    def read_projects(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: Session = Depends(database_session),
    ) -> list[ProjectView]:
        return [_project_view(project, settings.root_path) for project in list_projects(session, limit, offset)]

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectView, tags=["projects"])
    def read_project(project_id: str, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return _project_view(project, settings.root_path)

    @app.put("/api/v1/projects/{project_id}/settings", response_model=ProjectView, tags=["projects"])
    def save_project_settings(project_id: str, request: UpdateProjectSettingsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            reference = (
                job_mastering_reference(session, project.import_batch, request.mastering_reference_id)
                if "mastering_reference_id" in request.model_fields_set
                else project.mastering_reference
            )
            project = update_project_settings(
                session, project, request.manifest, request.scene, request.name,
                mastering_reference=reference,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if project.status == "queued":
            manager.notify()
        return _project_view(project, settings.root_path)

    @app.put("/api/v1/projects/{project_id}/tracks/{track_id}/settings", response_model=ProjectView, tags=["projects"])
    def save_project_track_settings(project_id: str, track_id: str, request: UpdateProjectTrackSettingsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project = update_track_settings(session, project, track_id, request.manifest_overrides, request.scene_overrides)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _project_view(project, settings.root_path)

    @app.post("/api/v1/projects/{project_id}/stems", response_model=ProjectView, tags=["projects"])
    def add_project_stems(project_id: str, request: ExpandProjectStemsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project = expand_project_stems(session, project, request.stems)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        manager.notify()
        return _project_view(project, settings.root_path)

    @app.post("/api/v1/projects/{project_id}/exports", response_model=JobView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def export_project(project_id: str, session: Session = Depends(database_session)) -> JobView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            job = project_export_job(session, project)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        manager.notify()
        return _job_view(job, settings.root_path)

    @app.post("/api/v1/projects/{project_id}/retry", response_model=ProjectView, tags=["projects"])
    def retry_project(project_id: str, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.status not in {"failed", "expansion_failed"}:
            raise HTTPException(status_code=409, detail="Project is not retryable")
        project.status = "expanding" if project.prepared_stems else "queued"
        project.progress = 0.0
        project.error = None
        project.status_message = "Waiting for worker"
        for track in project.tracks:
            track.status = "queued"
            track.progress = 0.0
            track.error = None
        session.commit()
        manager.notify()
        return _project_view(project, settings.root_path)

    @app.delete("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"])
    def delete_project(project_id: str, session: Session = Depends(database_session)) -> Response:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.status in {"preparing", "expanding"}:
            project.status = "deleting"
            project.status_message = "Stopping worker before deletion"
            session.commit()
        else:
            session.close()
            manager.delete_now_project(project_id)
        manager.notify()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/projects/{project_id}/tracks/{track_id}/stems/{stem_id}/audio", tags=["projects"])
    def read_project_stem(
        project_id: str,
        track_id: str,
        stem_id: str,
        quality: str = Query("full", pattern="^(full|preview)$"),
        session: Session = Depends(database_session),
    ) -> FileResponse:
        stem = session.get(ProjectStem, stem_id)
        if not stem or stem.project_id != project_id or stem.track_id != track_id:
            raise HTTPException(status_code=404, detail="Project stem not found")
        if quality == "preview" and stem.preview_relative_path:
            relative_path, media_type = stem.preview_relative_path, "audio/ogg"
        else:
            relative_path, media_type = stem.relative_path, "audio/wav"
        try:
            path = app.state.project_stems.resolve(relative_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Project stem file not found") from exc
        return FileResponse(path, media_type=media_type)

    @app.get("/api/v1/projects/{project_id}/tracks/{track_id}/source-preview", tags=["projects"])
    def read_project_source_preview(
        project_id: str,
        track_id: str,
        session: Session = Depends(database_session),
    ) -> FileResponse:
        track = session.get(ProjectTrack, track_id)
        if not track or track.project_id != project_id:
            raise HTTPException(status_code=404, detail="Project source preview not found")
        try:
            path = app.state.project_stems.resolve(track.source_preview_relative_path or "")
        except FileNotFoundError:
            try:
                app.state.project_stems.write_source_preview(
                    track, storage.local_path(track.asset.storage_key),
                )
                session.commit()
                path = app.state.project_stems.resolve(track.source_preview_relative_path or "")
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=503, detail="Project source preview is unavailable") from exc
        return FileResponse(path, media_type="audio/ogg")

    @app.get("/api/v1/projects/{project_id}/events", tags=["projects"])
    async def project_events(project_id: str) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            previous = ""
            while True:
                with sessions() as session:
                    project = get_project(session, project_id)
                    if not project:
                        yield "event: deleted\ndata: {}\n\n"
                        break
                    payload = _project_view(project, settings.root_path).model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    yield f"data: {encoded}\n\n"
                    previous = encoded
                if payload["status"] in {"ready", "failed", "expansion_failed"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

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
