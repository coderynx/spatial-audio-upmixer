"""Response-view builders and small ORM helpers shared across API route groups."""

from __future__ import annotations

from sqlalchemy.orm import Session

from upmixer_web.models import ImportBatch, Job, MasteringReference, Project
from upmixer_web.project_storage import ProjectStemStorage
from upmixer_web.schemas import ImportView, JobView, ProjectView, ReferenceMatchAssetView
from upmixer_web.worker import WorkerManager


def _import_view(batch: ImportBatch, root_path: str = "") -> ImportView:
    view = ImportView.model_validate(batch)
    if batch.cover_key:
        view.cover_url = f"{root_path}/api/v1/imports/{batch.id}/cover"
    for asset in view.assets:
        asset.audio_url = (
            f"{root_path}/api/v1/imports/{batch.id}/assets/{asset.id}/audio"
        )
    return view


def _job_view(job: Job, root_path: str = "") -> JobView:
    view = JobView.model_validate(job)
    artifact_urls = {
        artifact.id: f"{root_path}/api/v1/artifacts/{artifact.id}/download"
        for artifact in job.artifacts
    }
    for artifact in view.artifacts:
        artifact.download_url = artifact_urls[artifact.id]
    for track in view.tracks:
        track.asset.audio_url = (
            f"{root_path}/api/v1/imports/{job.import_id}/assets/"
            f"{track.asset.id}/audio"
        )
        for artifact in track.artifacts:
            artifact.download_url = artifact_urls[artifact.id]
    return view


def _project_view(
    project: Project, root_path: str = "", project_stems: ProjectStemStorage | None = None,
    manager: WorkerManager | None = None,
) -> ProjectView:
    view = ProjectView.model_validate(project)
    if manager is not None:
        view.reference_match_pending = manager.reference_match_pending(project.id)
        view.peaks_pending = manager.peaks_pending(project.id)
    stem_by_id = {stem.id: stem for stem in project.stems}
    for track in view.tracks:
        track.asset.audio_url = (
            f"{root_path}/api/v1/imports/{project.import_id}/assets/{track.asset.id}/audio"
        )
        track.source_preview_url = (
            f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/source-preview"
        )
        peaks_meta = project_stems.read_track_peaks_meta(project.id, track.id) if project_stems else None
        if peaks_meta:
            # Versioned by the stem generation the envelopes were built from,
            # same cache-busting convention as `fir_url` below — the route
            # itself ignores the query param.
            track.peaks_url = (
                f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/peaks"
                f"?v={peaks_meta.get('generation', 0)}"
            )
            track.peaks_bins = peaks_meta.get("bins", 0)
            track.peaks_stem_keys = peaks_meta.get("stems", [])
            track.peaks_duration_seconds = peaks_meta.get("duration_seconds")
        for stem in track.stems:
            base_url = (
                f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/"
                f"stems/{stem.id}/audio"
            )
            stem.audio_url = base_url
            if stem_by_id[stem.id].preview_relative_path:
                stem.preview_url = f"{base_url}?quality=preview"
    meta = project_stems.read_reference_match_meta(project.id) if project_stems else None
    if meta:
        fir_url = None
        if meta.get("channels"):
            fir_url = f"{root_path}/api/v1/projects/{project.id}/reference-match/fir"
            # Signature-versioned so the browser's fir_url-keyed decode cache
            # busts on a real recompute; the route itself ignores this param.
            if meta.get("signature"):
                fir_url = f"{fir_url}?v={meta['signature']}"
        view.reference_match = ReferenceMatchAssetView(
            fir_url=fir_url,
            channels=meta.get("channels", []),
            rms_gain_db=meta.get("rms_gain_db", 0.0),
            strength=meta.get("strength", 0.0),
            spectrum=meta.get("spectrum", False),
            rms=meta.get("rms", False),
            sample_rate=meta.get("sample_rate", 0),
        )
    return view


def job_mastering_reference(
    session: Session,
    import_batch: ImportBatch,
    reference_id: str | None,
) -> MasteringReference | None:
    if reference_id is None:
        return None
    reference = session.get(MasteringReference, reference_id)
    if not reference or reference.import_id != import_batch.id:
        raise ValueError("Mastering reference does not belong to this import")
    return reference
