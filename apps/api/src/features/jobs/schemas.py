"""Job response/request models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from upmixer_web.features.imports.schemas import AssetView, MasteringReferenceView
from upmixer_web.shared.schemas import ApiModel


class ArtifactView(ApiModel):
    id: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    download_url: str | None = None


class TrackView(ApiModel):
    id: str
    position: int
    status: str
    progress: float
    result: dict[str, Any] | None
    error: str | None
    asset: AssetView
    artifacts: list[ArtifactView] = Field(default_factory=list)


class DeliveryFormatView(ApiModel):
    type: str
    codec: str


class JobView(ApiModel):
    id: str
    import_id: str
    source_job_id: str | None
    project_id: str | None = None
    name: str
    status: str
    progress: float
    status_message: str
    manifest: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    tracks: list[TrackView] = Field(default_factory=list)
    artifacts: list[ArtifactView] = Field(default_factory=list)
    delivery_formats: list[DeliveryFormatView] = Field(default_factory=list)
    mastering_reference: MasteringReferenceView | None = None


class CreateJobRequest(BaseModel):
    import_id: str
    name: str = Field(min_length=1, max_length=512)
    manifest: dict[str, Any]
    mastering_reference_id: str | None = None
    start: bool = True


class CloneJobRequest(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    manifest: dict[str, Any] | None = None
    mastering_reference_id: str | None = None
    start: bool = True


class JobActionResponse(BaseModel):
    id: str
    status: str
