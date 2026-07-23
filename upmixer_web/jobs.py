"""Job lifecycle operations shared by API routes and workers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer_web.manifests import normalize_job_manifest
from upmixer_web.models import ImportBatch, Job, MasteringReference, JobTrack


JOB_LOAD_OPTIONS = (
    selectinload(Job.import_batch).selectinload(ImportBatch.assets),
    selectinload(Job.tracks).selectinload(JobTrack.asset),
    selectinload(Job.tracks).selectinload(JobTrack.artifacts),
    selectinload(Job.artifacts),
    selectinload(Job.mastering_reference),
)


def get_job(session: Session, job_id: str) -> Job | None:
    """Load a complete job graph for API serialization or execution."""
    return session.scalar(
        select(Job).where(Job.id == job_id).options(*JOB_LOAD_OPTIONS)
    )


def create_job(
    session: Session,
    import_batch: ImportBatch,
    name: str,
    manifest: dict,
    start: bool,
    source_job_id: str | None = None,
    mastering_reference: MasteringReference | None = None,
) -> Job:
    """Create durable job and per-track state."""
    normalized = normalize_job_manifest(manifest)
    job = Job(
        import_id=import_batch.id,
        mastering_reference=mastering_reference,
        source_job_id=source_job_id,
        name=name,
        manifest=normalized,
        status="queued" if start else "paused",
        status_message="Waiting for worker" if start else "Ready to start",
    )
    session.add(job)
    session.flush()
    for asset in import_batch.assets:
        session.add(JobTrack(
            job_id=job.id,
            asset_id=asset.id,
            position=asset.position,
            status="queued" if start else "paused",
        ))
    session.commit()
    return get_job(session, job.id)  # type: ignore[return-value]


def clone_job(
    session: Session,
    source: Job,
    name: str | None,
    manifest: dict | None,
    start: bool,
    mastering_reference: MasteringReference | None,
) -> Job:
    """Create a remix job sharing source files and global stem cache."""
    return create_job(
        session=session,
        import_batch=source.import_batch,
        name=name or f"{source.name} remix",
        manifest=manifest or source.manifest,
        start=start,
        source_job_id=source.id,
        mastering_reference=mastering_reference,
    )


def list_jobs(session: Session, limit: int = 100, offset: int = 0) -> list[Job]:
    """Return newest jobs with related tracks and artifacts."""
    return list(session.scalars(
        select(Job)
        .options(*JOB_LOAD_OPTIONS)
        .order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all())


def reset_incomplete_jobs(session: Session) -> None:
    """Make interrupted jobs safe to resume after process restart."""
    for job in session.scalars(select(Job).where(Job.status.in_(("running", "pause_requested")))):
        job.status = "queued"
        job.status_message = "Recovered after service restart"
        for track in job.tracks:
            if track.status == "running":
                track.status = "queued"
    session.commit()
