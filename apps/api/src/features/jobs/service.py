"""Job lifecycle and state-machine operations used by API routes and the worker."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer_web.shared.manifests import ensure_stem_separation_available, normalize_job_manifest
from upmixer_web.shared.models import ImportBatch, Job, JobTrack, MasteringReference, MediaAsset


JOB_LOAD_OPTIONS = (
    selectinload(Job.import_batch).selectinload(ImportBatch.assets),
    selectinload(Job.tracks).selectinload(JobTrack.asset),
    selectinload(Job.tracks).selectinload(JobTrack.artifacts),
    selectinload(Job.artifacts),
    selectinload(Job.mastering_reference),
)


class JobStateConflict(ValueError):
    """A job cannot transition from its current status."""


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
    assets: Iterable[MediaAsset] | None = None,
) -> Job:
    """Create durable job and per-track state.

    ``import_batch`` anchors the job's required FK; ``assets`` (default:
    ``import_batch.assets``) is what actually gets cloned into `JobTrack`
    rows. A project export passes the project's own tracks' assets, which
    may span more than one import once assets are added to a project
    incrementally — they no longer necessarily match ``import_batch.assets``.
    """
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
    for position, asset in enumerate(assets if assets is not None else import_batch.assets):
        session.add(JobTrack(
            job_id=job.id,
            asset_id=asset.id,
            position=position,
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


def pause_job(session: Session, job: Job) -> Job:
    """Transition a job toward paused, mirroring its current lifecycle stage."""
    if job.status == "queued":
        job.status = "paused"
        for track in job.tracks:
            if track.status == "queued":
                track.status = "paused"
    elif job.status == "running":
        job.status = "pause_requested"
    elif job.status not in {"paused", "pause_requested"}:
        raise JobStateConflict(f"Cannot pause {job.status} job")
    job.status_message = "Pause requested"
    session.commit()
    return job


def resume_job(session: Session, job: Job, stem_capability: dict) -> Job:
    """Requeue a paused or failed job, re-validating stem-separation support."""
    if job.status not in {"paused", "failed"}:
        raise JobStateConflict(f"Cannot resume {job.status} job")
    ensure_stem_separation_available(job.manifest, stem_capability)
    job.status = "queued"
    job.error = None
    job.status_message = "Waiting for worker"
    for track in job.tracks:
        if track.status != "completed":
            track.status = "queued"
            track.error = None
    session.commit()
    return job


def mark_job_deleting(session: Session, job: Job) -> bool:
    """Mark an in-flight job for worker-side teardown, or signal the caller
    to delete it immediately.

    Returns ``True`` when the job is idle and the caller should delete it
    right away (via ``WorkerManager.delete_now``); ``False`` when it is
    in-flight and has been flagged ``deleting`` for the worker to tear down.
    """
    if job.status in {"running", "pause_requested"}:
        job.status = "deleting"
        job.status_message = "Stopping worker before deletion"
        session.commit()
        return False
    return True
