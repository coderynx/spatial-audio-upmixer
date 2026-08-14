"""Job submission, control, and event-stream routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from upmixer_web.features.imports.service import resolve_mastering_reference
from upmixer_web.features.jobs.schemas import CloneJobRequest, CreateJobRequest, JobActionResponse, JobView
from upmixer_web.features.jobs.service import (
    JobStateConflict,
    clone_job,
    create_job,
    get_job,
    list_jobs,
    mark_job_deleting,
    pause_job,
    resume_job,
)
from upmixer_web.features.jobs.views import job_view
from upmixer_web.settings import Settings
from upmixer_web.shared.manifests import ensure_stem_separation_available
from upmixer_web.shared.models import ImportBatch, Job

if TYPE_CHECKING:
    # Deferred: upmixer_web.worker imports this module's package (via the
    # JobRunnerMixin it composes into WorkerManager) at import time, so a
    # runtime import here would cycle back into a partially initialized
    # upmixer_web.worker. PEP 563 (see the __future__ import above) means the
    # WorkerManager annotation below is never evaluated at runtime.
    from upmixer_web.worker import WorkerManager


def register_job_routes(
    app: FastAPI,
    settings: Settings,
    manager: WorkerManager,
    stem_capability: object,
    database_session: Callable[[], Iterator[Session]],
    sessions: sessionmaker[Session],
) -> None:
    @app.post("/api/v1/jobs", response_model=JobView, status_code=status.HTTP_201_CREATED, tags=["jobs"])
    def submit_job(request: CreateJobRequest, session: Session = Depends(database_session)) -> JobView:
        batch = session.get(ImportBatch, request.import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        try:
            if request.start:
                ensure_stem_separation_available(request.manifest, stem_capability)
            reference = resolve_mastering_reference(
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
        return job_view(job, settings.root_path)

    @app.get("/api/v1/jobs", response_model=list[JobView], tags=["jobs"])
    def read_jobs(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: Session = Depends(database_session),
    ) -> list[JobView]:
        return [job_view(job, settings.root_path) for job in list_jobs(session, limit, offset)]

    @app.get("/api/v1/jobs/{job_id}", response_model=JobView, tags=["jobs"])
    def read_job(job_id: str, session: Session = Depends(database_session)) -> JobView:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_view(job, settings.root_path)

    @app.post("/api/v1/jobs/{job_id}/pause", response_model=JobActionResponse, tags=["jobs"])
    def pause_job_route(job_id: str, session: Session = Depends(database_session)) -> JobActionResponse:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            pause_job(session, job)
        except JobStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobActionResponse(id=job.id, status=job.status)

    @app.post("/api/v1/jobs/{job_id}/resume", response_model=JobActionResponse, tags=["jobs"])
    def resume_job_route(job_id: str, session: Session = Depends(database_session)) -> JobActionResponse:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            resume_job(session, job, stem_capability)
        except JobStateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
            reference = resolve_mastering_reference(
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
        return job_view(job, settings.root_path)

    @app.delete("/api/v1/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["jobs"])
    def delete_job_route(job_id: str, session: Session = Depends(database_session)) -> Response:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if mark_job_deleting(session, job):
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
                    payload = job_view(job, settings.root_path).model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    yield f"data: {encoded}\n\n"
                    previous = encoded
                if payload["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
