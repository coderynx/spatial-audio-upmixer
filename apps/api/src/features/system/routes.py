"""Health, configuration, stem-routing preview, and artifact-download routes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from upmixer_web.features.system.schemas import HealthResponse
from upmixer_web.features.system.service import configuration_schema
from upmixer_web.settings import Settings
from upmixer_web.shared.models import Artifact
from upmixer_web.shared.storage import ObjectStorage


def register_system_routes(
    app: FastAPI,
    settings: Settings,
    storage: ObjectStorage,
    stem_capability: dict,
    database_session: Callable[[], Iterator[Session]],
) -> None:
    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(workers=settings.worker_count)

    @app.get("/api/v1/configuration", tags=["system"])
    def get_configuration() -> dict:
        return configuration_schema(stem_capability)

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
