"""Health, configuration, stem-routing preview, and artifact-download routes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from upmixer_web.features.system.schemas import (
    HealthResponse,
    ResolveStemRoutingRequest,
    SeparationDispatchState,
)
from upmixer_web.features.system.service import configuration_schema
from upmixer_web.settings import Settings
from upmixer_web.shared.models import Artifact
from upmixer_web.shared.storage import ObjectStorage

if TYPE_CHECKING:
    from upmixer_web.worker import WorkerManager


def register_system_routes(
    app: FastAPI,
    settings: Settings,
    storage: ObjectStorage,
    stem_capability: dict,
    manager: WorkerManager,
    database_session: Callable[[], Iterator[Session]],
) -> None:
    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(workers=settings.worker_count)

    @app.get("/api/v1/configuration", tags=["system"])
    def get_configuration() -> dict:
        return configuration_schema(stem_capability)

    @app.get("/api/v1/separation", response_model=SeparationDispatchState, tags=["system"])
    def get_separation_state() -> SeparationDispatchState:
        return SeparationDispatchState(paused=manager.is_dispatch_paused())

    @app.post("/api/v1/separation/pause", response_model=SeparationDispatchState, tags=["system"])
    def pause_separation() -> SeparationDispatchState:
        manager.pause_dispatch()
        return SeparationDispatchState(paused=True)

    @app.post("/api/v1/separation/resume", response_model=SeparationDispatchState, tags=["system"])
    def resume_separation() -> SeparationDispatchState:
        manager.resume_dispatch()
        return SeparationDispatchState(paused=False)

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
