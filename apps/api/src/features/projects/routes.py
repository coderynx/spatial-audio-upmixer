"""Project creation, settings, stem/peaks/reference-match assets, and event-stream routes."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from upmixer.mastering.match_reference.curve import SMOOTH_OCT_MAX, SMOOTH_OCT_MIN

from upmixer_web.features.imports.service import resolve_project_mastering_reference
from upmixer_web.features.jobs.schemas import JobView
from upmixer_web.features.jobs.views import job_view
from upmixer_web.features.projects.archive import (
    export_project_archive,
    import_project_archive,
    iter_track_stems_zip,
    safe_component,
)
from upmixer_web.features.projects.schemas import (
    AddProjectAssetsRequest,
    CreateProjectRequest,
    ExpandProjectStemsRequest,
    ExportProjectRequest,
    ProjectView,
    ProjectViewState,
    SetTrackLayoutsRequest,
    UpdateProjectSettingsRequest,
    UpdateProjectTrackSettingsRequest,
)
from upmixer_web.features.projects.service import (
    ProjectStateConflict,
    TrackNotFoundError,
    add_project_assets,
    create_empty_project,
    expand_project_stems,
    get_project,
    list_projects,
    mark_project_deleting,
    project_export_job,
    reprepare_project_stems,
    retry_project,
    set_track_layouts,
    update_project_settings,
    update_project_view_state,
    update_track_layout_settings,
)
from upmixer_web.features.projects.views import project_view
from upmixer_web.settings import Settings
from upmixer_web.shared.manifests import ensure_stem_separation_available
from upmixer_web.shared.models import ImportBatch, ProjectStem, ProjectTrack
from upmixer_web.shared.storage import ObjectStorage

if TYPE_CHECKING:
    # Deferred: upmixer_web.worker imports this module's package (via the
    # ProjectRunnerMixin it composes into WorkerManager) at import time, so a
    # runtime import here would cycle back into a partially initialized
    # upmixer_web.worker. PEP 563 (see the __future__ import above) means the
    # WorkerManager annotation below is never evaluated at runtime.
    from upmixer_web.worker import WorkerManager


def register_project_routes(
    app: FastAPI,
    settings: Settings,
    storage: ObjectStorage,
    manager: WorkerManager,
    stem_capability: object,
    database_session: Callable[[], Iterator[Session]],
    sessions: sessionmaker[Session],
) -> None:
    @app.post("/api/v1/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def create_project_route(request: CreateProjectRequest, session: Session = Depends(database_session)) -> ProjectView:
        try:
            project_manifest = dict(request.manifest)
            project_manifest["engine"] = {
                **dict(project_manifest.get("engine", {})),
                "mode": "stem",
            }
            ensure_stem_separation_available(project_manifest, stem_capability)
            project = create_empty_project(session, request.name, request.notes, request.manifest, request.scene)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.post("/api/v1/projects/{project_id}/assets", response_model=ProjectView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def add_project_assets_route(project_id: str, request: AddProjectAssetsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        batch = session.get(ImportBatch, request.import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        try:
            project = add_project_assets(session, project, batch, request.per_asset_overrides)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        manager.notify()
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.get("/api/v1/projects", response_model=list[ProjectView], tags=["projects"])
    def read_projects(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: Session = Depends(database_session),
    ) -> list[ProjectView]:
        return [project_view(project, settings.root_path, app.state.project_stems, manager) for project in list_projects(session, limit, offset)]

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectView, tags=["projects"])
    def read_project(project_id: str, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        # Backfills waveform envelopes for projects catalogued before peaks
        # existed. Safe to call on every read: `schedule_peaks` gates on
        # `_peaks_needs_work` and coalesces, so a steady-state poll is a
        # cheap metadata check, not a run.
        manager.schedule_peaks(project_id)
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.put("/api/v1/projects/{project_id}/settings", response_model=ProjectView, tags=["projects"])
    def save_project_settings(project_id: str, request: UpdateProjectSettingsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        previous_preview_quality = project.preview_quality
        try:
            reference = (
                resolve_project_mastering_reference(session, project, request.mastering_reference_id)
                if "mastering_reference_id" in request.model_fields_set
                else project.mastering_reference
            )
            project = update_project_settings(
                session, project, request.manifest, request.scene, request.name,
                notes=request.notes,
                mastering_reference=reference,
                preview_quality=request.preview_quality,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Previews are only encoded once per stem (see `catalogue_track`'s
        # if-not-exists guard), so a quality change on an already-prepared
        # project needs an explicit re-encode rather than waiting on the
        # normal pipeline to naturally pick up the new setting.
        if (
            request.preview_quality is not None
            and request.preview_quality != previous_preview_quality
            and project.prepared_stems
        ):
            app.state.project_stems.regenerate_previews(project, project.preview_quality, storage)
            session.commit()
        # Backgrounded: the mix+PSD pass is heavy and settings saves debounce
        # at 350ms, so inline would fire one full-song pass per slider tick
        # (see docs/contracts/preview_export_parity.md Ledger D12).
        manager.schedule_reference_match(project_id)
        if project.status == "queued":
            manager.notify()
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.put("/api/v1/projects/{project_id}/view-state", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"])
    def save_project_view_state(project_id: str, request: ProjectViewState, session: Session = Depends(database_session)) -> None:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        update_project_view_state(session, project, request.model_dump())

    @app.put("/api/v1/projects/{project_id}/tracks/{track_id}/layouts", response_model=ProjectView, tags=["projects"])
    def save_project_track_layouts(project_id: str, track_id: str, request: SetTrackLayoutsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project = set_track_layouts(session, project, track_id, request.layouts)
        except TrackNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.put("/api/v1/projects/{project_id}/tracks/{track_id}/layouts/{layout}/settings", response_model=ProjectView, tags=["projects"])
    def save_project_track_layout_settings(project_id: str, track_id: str, layout: str, request: UpdateProjectTrackSettingsRequest, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project = update_track_layout_settings(
                session, project, track_id, layout, request.manifest_overrides, request.scene_overrides
            )
        except TrackNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return project_view(project, settings.root_path, app.state.project_stems, manager)

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
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.post("/api/v1/projects/{project_id}/exports", response_model=JobView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def export_project(project_id: str, request: ExportProjectRequest, session: Session = Depends(database_session)) -> JobView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            job = project_export_job(session, project, app.state.project_stems, request.layout)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        manager.notify()
        return job_view(job, settings.root_path)

    @app.post("/api/v1/projects/{project_id}/retry", response_model=ProjectView, tags=["projects"])
    def retry_project_route(project_id: str, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            retry_project(session, project)
        except ProjectStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        manager.notify()
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.post("/api/v1/projects/{project_id}/stems/reprepare", response_model=ProjectView, tags=["projects"])
    def reprepare_project_stems_route(project_id: str, session: Session = Depends(database_session)) -> ProjectView:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            reprepare_project_stems(session, project)
        except ProjectStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        manager.notify()
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.get("/api/v1/projects/{project_id}/archive", tags=["projects"])
    def export_project_archive_route(
        project_id: str, background_tasks: BackgroundTasks, session: Session = Depends(database_session),
    ) -> FileResponse:
        """DAW-style "Save": a portable .upmix.zip a user can re-import later
        (here or on another server) to an identical workspace. See
        `features.projects.archive` for what's inside."""
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if not project.tracks:
            raise HTTPException(status_code=409, detail="Project has no tracks to export")
        destination = manager.work_root / f"project-archive-{uuid.uuid4().hex}.zip"
        try:
            export_project_archive(project, storage, app.state.project_stems, destination)
        except (OSError, FileNotFoundError) as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"Project archive could not be built: {exc}") from exc
        # FastAPI runs a BackgroundTasks parameter after the response is sent
        # regardless of whether the returned Response references it — no
        # need to also pass background=background_tasks to FileResponse.
        background_tasks.add_task(destination.unlink, missing_ok=True)
        safe_name = safe_component(project.name)
        return FileResponse(destination, media_type="application/zip", filename=f"{safe_name}.upmix.zip")

    @app.post("/api/v1/projects/import", response_model=ProjectView, status_code=status.HTTP_201_CREATED, tags=["projects"])
    async def import_project_route(file: UploadFile, session: Session = Depends(database_session)) -> ProjectView:
        """DAW-style "Open": reconstruct a project from a .upmix.zip written
        by the export route above."""
        staging = manager.work_root / f"project-archive-upload-{uuid.uuid4().hex}.zip"
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            with staging.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
            project = import_project_archive(session, storage, app.state.project_stems, manager.work_root, staging)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid project archive: {exc}") from exc
        finally:
            staging.unlink(missing_ok=True)
        manager.notify()
        return project_view(project, settings.root_path, app.state.project_stems, manager)

    @app.delete("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"])
    def delete_project_route(project_id: str, session: Session = Depends(database_session)) -> Response:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if mark_project_deleting(session, project):
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

    @app.get("/api/v1/projects/{project_id}/tracks/{track_id}/stems/archive", tags=["projects"])
    def read_project_track_stems_archive(
        project_id: str,
        track_id: str,
        session: Session = Depends(database_session),
    ) -> StreamingResponse:
        track = session.get(ProjectTrack, track_id)
        if not track or track.project_id != project_id:
            raise HTTPException(status_code=404, detail="Project track not found")
        if not track.stems:
            raise HTTPException(status_code=409, detail="Track has no stems to download")
        try:
            entries = [
                (app.state.project_stems.resolve(stem.relative_path), f"{safe_component(stem.stem_key)}.wav")
                for stem in track.stems
            ]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Project stem file not found") from exc
        safe_name = safe_component(track.asset.title or track.asset.filename)
        return StreamingResponse(
            iter_track_stems_zip(entries),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}-stems.zip"'},
        )

    @app.get("/api/v1/projects/{project_id}/tracks/{track_id}/peaks", tags=["projects"])
    def read_project_track_peaks(
        project_id: str,
        track_id: str,
        session: Session = Depends(database_session),
    ) -> FileResponse:
        track = session.get(ProjectTrack, track_id)
        if not track or track.project_id != project_id:
            raise HTTPException(status_code=404, detail="Project track not found")
        path = app.state.project_stems.track_peaks_path(project_id, track_id)
        if not path:
            raise HTTPException(status_code=404, detail="Waveform peaks are not available")
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/api/v1/projects/{project_id}/reference-match/{layout}/fir", tags=["projects"])
    def read_project_reference_match_fir(
        project_id: str,
        layout: str,
        strength: float = 1.0,
        max_db: float = 6.0,
        smooth_oct: float | None = None,
        low_hz: float | None = None,
        high_hz: float | None = None,
        session: Session = Depends(database_session),
    ) -> Response:
        """Design and serve one speaker layout's reference-match FIR for one
        set of user controls on demand from that layout's persisted
        correction curve — see
        `ProjectStemStorage.reference_match_fir_wav_bytes`. Cheap enough to
        call on every slider drag (see
        docs/contracts/preview_export_parity.md Ledger D21); the `v=`
        signature query param `views.py` appends is for browser cache-busting
        only and is intentionally not read here.
        """
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        strength = max(0.0, min(1.0, strength))
        max_db = max(0.0, min(24.0, max_db))
        if smooth_oct is not None:
            smooth_oct = max(SMOOTH_OCT_MIN, min(SMOOTH_OCT_MAX, smooth_oct))
        low_hz = None if low_hz is None else max(20.0, min(20000.0, low_hz))
        high_hz = None if high_hz is None else max(20.0, min(20000.0, high_hz))
        wav_bytes = app.state.project_stems.reference_match_fir_wav_bytes(
            project_id, layout, strength, max_db, smooth_oct, low_hz, high_hz,
        )
        if wav_bytes is None:
            raise HTTPException(status_code=404, detail="Reference-match FIR is not available")
        return Response(content=wav_bytes, media_type="audio/wav")

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
                    payload = project_view(project, settings.root_path, app.state.project_stems, manager).model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    yield f"data: {encoded}\n\n"
                    previous = encoded
                if payload["status"] in {"ready", "failed", "expansion_failed"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
