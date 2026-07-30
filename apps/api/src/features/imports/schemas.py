"""Import and mastering-reference response/request models."""

from __future__ import annotations

from datetime import date, datetime

from upmixer_web.shared.schemas import ApiModel


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
