"""Project response/request models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from upmixer_web.features.imports.schemas import AssetView, MasteringReferenceView
from upmixer_web.features.jobs.schemas import JobView
from upmixer_web.shared.schemas import ApiModel


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
    # Waveform envelopes are served as their own binary asset rather than
    # inlined here: this view is re-serialized on every SSE tick and every
    # poll, so only the pointer and the shape metadata belong in it.
    peaks_url: str | None = None
    peaks_bins: int = 0
    peaks_stem_keys: list[str] = Field(default_factory=list)
    peaks_duration_seconds: float | None = None
    error: str | None
    asset: AssetView
    stems: list[StemView] = Field(default_factory=list)


class ReferenceMatchAssetView(BaseModel):
    """A project's server-precomputed reference-match correction curve — see
    `docs/contracts/preview_export_parity.md` Ledgers D12/D20. `fir_url` is a
    base URL the browser appends live `strength`/`max_db` query params to
    (the FIR endpoint designs the filter from the curve on demand); it is
    ``None`` when no curve is persisted. `strength`/`spectrum`/`rms`/`max_db`
    are not server state — they're read live from the project's manifest
    (`Manifest.mastering.match_reference`), not this asset. `rms_gain_db`
    still applies when spectral matching is off."""

    fir_url: str | None = None
    channels: list[str] = Field(default_factory=list)
    rms_gain_db: float = 0.0
    sample_rate: int


class ProjectView(ApiModel):
    id: str
    import_id: str | None
    name: str
    notes: str | None = None
    status: str
    progress: float
    status_message: str
    progress_log: list[dict[str, Any]] = Field(default_factory=list)
    manifest: dict[str, Any]
    scene: dict[str, Any]
    requested_stems: list[str]
    prepared_stems: list[str]
    stem_generation: int
    preview_quality: str
    revision: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    tracks: list[ProjectTrackView] = Field(default_factory=list)
    exports: list[JobView] = Field(default_factory=list)
    mastering_reference: MasteringReferenceView | None = None
    reference_match: ReferenceMatchAssetView | None = None
    reference_match_pending: bool = False
    peaks_pending: bool = False


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    notes: str | None = Field(default=None, max_length=8192)
    manifest: dict[str, Any] = Field(default_factory=dict)
    scene: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectSettingsRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    notes: str | None = None
    manifest: dict[str, Any]
    scene: dict[str, Any] = Field(default_factory=dict)
    mastering_reference_id: str | None = None
    preview_quality: str | None = None


class UpdateProjectTrackSettingsRequest(BaseModel):
    manifest_overrides: dict[str, Any] = Field(default_factory=dict)
    scene_overrides: dict[str, Any] = Field(default_factory=dict)


class ExpandProjectStemsRequest(BaseModel):
    stems: list[str] = Field(min_length=1)


class AddProjectAssetsRequest(BaseModel):
    """Adds every asset from an already-ingested import batch to a project as
    new tracks. ``per_asset_overrides`` carries per-file stems/sample-rate/
    subtype/channel-layout, keyed by the `MediaAsset.id` returned from the
    `/imports` ingestion the caller already performed."""

    import_id: str
    per_asset_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
