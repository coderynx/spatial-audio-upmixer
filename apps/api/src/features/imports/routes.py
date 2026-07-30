"""Import and mastering-reference upload routes."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from typing import Callable

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from upmixer_web.features.imports.schemas import ImportView, MasteringReferenceView
from upmixer_web.features.imports.service import ingest_mastering_reference, ingest_uploads
from upmixer_web.features.imports.views import import_view
from upmixer_web.settings import Settings
from upmixer_web.shared.models import ImportBatch, MediaAsset
from upmixer_web.shared.storage import ObjectStorage


def register_import_routes(
    app: FastAPI,
    settings: Settings,
    storage: ObjectStorage,
    database_session: Callable[[], Iterator[Session]],
) -> None:
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
        return import_view(batch, settings.root_path)

    @app.get("/api/v1/imports/{import_id}", response_model=ImportView, tags=["imports"])
    def read_import(import_id: str, session: Session = Depends(database_session)) -> ImportView:
        batch = session.get(ImportBatch, import_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Import not found")
        return import_view(batch, settings.root_path)

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
