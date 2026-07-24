"""Public API request and response models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AssetView(ApiModel):
    id: str
    position: int
    filename: str
    relative_path: str
    size_bytes: int
    title: str | None
    artist: str | None
    album: str | None
    release_date: date | None
    track_number: int | None
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None
    audio_url: str | None = None


class ImportView(ApiModel):
    id: str
    kind: str
    title: str | None
    artist: str | None
    release_date: date | None
    cover_url: str | None = None
    created_at: datetime
    assets: list[AssetView]


class MasteringReferenceView(ApiModel):
    id: str
    filename: str
    size_bytes: int
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None


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


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    workers: int


class StemView(ApiModel):
    id: str
    stem_key: str
    sample_rate: int
    channels: int
    size_bytes: int
    audio_url: str | None = None
    preview_url: str | None = None


class ProjectTrackView(ApiModel):
    id: str
    position: int
    status: str
    progress: float
    manifest_overrides: dict[str, Any] = Field(default_factory=dict)
    scene_overrides: dict[str, Any] = Field(default_factory=dict)
    source_preview_relative_path: str | None = None
    source_preview_url: str | None = None
    error: str | None
    asset: AssetView
    stems: list[StemView] = Field(default_factory=list)


class ProjectView(ApiModel):
    id: str
    import_id: str
    name: str
    status: str
    progress: float
    status_message: str
    manifest: dict[str, Any]
    scene: dict[str, Any]
    requested_stems: list[str]
    prepared_stems: list[str]
    stem_generation: int
    revision: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    tracks: list[ProjectTrackView] = Field(default_factory=list)
    exports: list[JobView] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    import_id: str
    name: str = Field(min_length=1, max_length=512)
    manifest: dict[str, Any]
    scene: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectSettingsRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    manifest: dict[str, Any]
    scene: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectTrackSettingsRequest(BaseModel):
    manifest_overrides: dict[str, Any] = Field(default_factory=dict)
    scene_overrides: dict[str, Any] = Field(default_factory=dict)


class ExpandProjectStemsRequest(BaseModel):
    stems: list[str] = Field(min_length=1)


class ResolveStemRoutingRequest(BaseModel):
    stems: list[str] = Field(min_length=1)
    channel_layout: str
    preset: str = "balanced"
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
