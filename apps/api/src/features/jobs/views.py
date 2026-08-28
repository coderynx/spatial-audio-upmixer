"""Job response-view builder."""

from __future__ import annotations

from upmixer.codecs import DEFAULT_CODEC
from upmixer_web.features.jobs.schemas import DeliveryFormatView, JobView
from upmixer_web.shared.models import Job


def _delivery_formats(job: Job) -> list[DeliveryFormatView]:
    root = job.manifest.get("format", {}) if isinstance(job.manifest, dict) else {}
    root = root if isinstance(root, dict) else {}
    snapshots = (job.project_snapshot or {}).get("tracks", {})
    snapshots = snapshots if isinstance(snapshots, dict) else {}
    formats: list[DeliveryFormatView] = []
    for track in job.tracks or [None]:
        snapshot = snapshots.get(track.asset_id, {}) if track else {}
        overrides = (
            snapshot.get("manifest_overrides", {})
            if isinstance(snapshot, dict)
            else {}
        )
        override = overrides.get("format", {}) if isinstance(overrides, dict) else {}
        override = override if isinstance(override, dict) else {}
        type_ = override.get("type", root.get("type", "multichannel"))
        codec = override.get("codec", root.get("codec", DEFAULT_CODEC))
        if type_ == "wav":
            type_ = "multichannel"
        format_ = DeliveryFormatView(type=str(type_), codec=str(codec))
        if format_ not in formats:
            formats.append(format_)
    return formats


def job_view(job: Job, root_path: str = "") -> JobView:
    view = JobView.model_validate(job)
    view.delivery_formats = _delivery_formats(job)
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
