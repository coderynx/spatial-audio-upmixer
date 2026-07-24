"""Persistent records for imports, jobs, tracks, and artifacts."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from upmixer_web.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImportBatch(Base):
    """Uploaded single track or album awaiting job creation."""

    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512))
    release_date: Mapped[date | None] = mapped_column(Date)
    cover_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="import_batch",
        cascade="all, delete-orphan",
        order_by="MediaAsset.position",
    )
    mastering_references: Mapped[list[MasteringReference]] = relationship(
        back_populates="import_batch",
        cascade="all, delete-orphan",
    )


class MediaAsset(Base):
    """One uploaded audio source and its extracted metadata."""

    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(512))
    relative_path: Mapped[str] = mapped_column(String(1024))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    release_date: Mapped[date | None] = mapped_column(Date)
    track_number: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)

    import_batch: Mapped[ImportBatch] = relationship(back_populates="assets")
    job_tracks: Mapped[list[JobTrack]] = relationship(back_populates="asset")


class MasteringReference(Base):
    """One trusted reference track available to jobs from an import."""

    __tablename__ = "mastering_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    import_batch: Mapped[ImportBatch] = relationship(back_populates="mastering_references")
    jobs: Mapped[list[Job]] = relationship(back_populates="mastering_reference")
    projects: Mapped[list[Project]] = relationship(back_populates="mastering_reference")


class Job(Base):
    """Durable upmix request encompassing one track or an album."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"), index=True)
    mastering_reference_id: Mapped[str | None] = mapped_column(
        ForeignKey("mastering_references.id", ondelete="SET NULL"), index=True
    )
    source_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    project_revision: Mapped[int | None] = mapped_column(Integer)
    project_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    status_message: Mapped[str] = mapped_column(String(1024), default="Waiting for worker")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tracks: Mapped[list[JobTrack]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobTrack.position",
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    import_batch: Mapped[ImportBatch] = relationship()
    mastering_reference: Mapped[MasteringReference | None] = relationship(
        back_populates="jobs"
    )
    project: Mapped[Project | None] = relationship(back_populates="exports")


class Project(Base):
    """Editable web-only spatial mix project backed by one source import."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"), index=True)
    mastering_reference_id: Mapped[str | None] = mapped_column(
        ForeignKey("mastering_references.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    status_message: Mapped[str] = mapped_column(String(1024), default="Waiting for worker")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    scene: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_stems: Mapped[list[str]] = mapped_column(JSON, default=list)
    prepared_stems: Mapped[list[str]] = mapped_column(JSON, default=list)
    stem_generation: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    import_batch: Mapped[ImportBatch] = relationship()
    mastering_reference: Mapped[MasteringReference | None] = relationship(
        back_populates="projects"
    )
    tracks: Mapped[list[ProjectTrack]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectTrack.position"
    )
    stems: Mapped[list[ProjectStem]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    exports: Mapped[list[Job]] = relationship(back_populates="project")


class ProjectTrack(Base):
    """Per-track preparation state and optional project-setting overrides."""

    __tablename__ = "project_tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    manifest_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scene_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_preview_relative_path: Mapped[str | None] = mapped_column(String(1024))
    source_preview_size_bytes: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="tracks")
    asset: Mapped[MediaAsset] = relationship()
    stems: Mapped[list[ProjectStem]] = relationship(back_populates="track")


class ProjectStem(Base):
    """One catalogued stem file belonging to a project track."""

    __tablename__ = "project_stems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("project_tracks.id", ondelete="CASCADE"), index=True)
    stem_key: Mapped[str] = mapped_column(String(512))
    relative_path: Mapped[str] = mapped_column(String(1024))
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    preview_relative_path: Mapped[str | None] = mapped_column(String(1024))
    preview_size_bytes: Mapped[int | None] = mapped_column(Integer)

    project: Mapped[Project] = relationship(back_populates="stems")
    track: Mapped[ProjectTrack] = relationship(back_populates="stems")


class JobTrack(Base):
    """Execution state for one source within a job."""

    __tablename__ = "job_tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    output_key: Mapped[str | None] = mapped_column(String(1024))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="tracks")
    asset: Mapped[MediaAsset] = relationship(back_populates="job_tracks")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="track")


class Artifact(Base):
    """Downloadable output produced by a job or future encoding sink."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str | None] = mapped_column(ForeignKey("job_tracks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), default="upmix")
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="audio/wav")
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="artifacts")
    track: Mapped[JobTrack | None] = relationship(back_populates="artifacts")
