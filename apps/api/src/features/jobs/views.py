"""Job response-view builder."""

from __future__ import annotations

from upmixer_web.features.jobs.schemas import JobView
from upmixer_web.shared.models import Job


def job_view(job: Job, root_path: str = "") -> JobView:
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
