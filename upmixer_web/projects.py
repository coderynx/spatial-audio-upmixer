"""Project lifecycle, settings, and export snapshot operations."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer.separation.stem_plan import normalize_stems
from upmixer_web.jobs import create_job
from upmixer_web.manifests import normalize_job_manifest
from upmixer_web.models import ImportBatch, Job, Project, ProjectStem, ProjectTrack


PROJECT_LOAD_OPTIONS = (
    selectinload(Project.import_batch).selectinload(ImportBatch.assets),
    selectinload(Project.tracks).selectinload(ProjectTrack.asset),
    selectinload(Project.tracks).selectinload(ProjectTrack.stems),
    selectinload(Project.exports).selectinload(Job.tracks),
    selectinload(Project.exports).selectinload(Job.artifacts),
)

_CHILD_STEMS = {
    "Vocals": ("Lead Vocals", "Backing Vocals"),
    "Drums": ("Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"),
}


def _normalize_project_stems(stems: Iterable[str]) -> list[str]:
    """Keep a parent stem only when none of its detailed stems is requested."""
    normalized = normalize_stems(list(stems))
    selected = set(normalized)
    return [
        stem
        for stem in normalized
        if not (stem in _CHILD_STEMS and any(child in selected for child in _CHILD_STEMS[stem]))
    ]


def get_project(session: Session, project_id: str) -> Project | None:
    return session.scalar(select(Project).where(Project.id == project_id).options(*PROJECT_LOAD_OPTIONS))


def list_projects(session: Session, limit: int = 100, offset: int = 0) -> list[Project]:
    return list(session.scalars(
        select(Project).options(*PROJECT_LOAD_OPTIONS).order_by(Project.created_at.desc()).offset(offset).limit(limit)
    ).all())


def _normalized_project_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = normalize_job_manifest(manifest)
    engine = normalized.setdefault("engine", {})
    engine["mode"] = "stem"
    stems = _normalize_project_stems(engine.get("stems") or [])
    engine["stems"] = stems
    normalized.setdefault("processing", {})["preview"] = False
    return normalized, stems


def create_project(
    session: Session,
    import_batch: ImportBatch,
    name: str,
    manifest: dict[str, Any],
    scene: dict[str, Any],
) -> Project:
    normalized, stems = _normalized_project_manifest(manifest)
    project = Project(
        import_id=import_batch.id,
        name=name,
        manifest=normalized,
        scene=copy.deepcopy(scene),
        requested_stems=stems,
        prepared_stems=[],
    )
    session.add(project)
    session.flush()
    for asset in import_batch.assets:
        session.add(ProjectTrack(project_id=project.id, asset_id=asset.id, position=asset.position))
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_project_settings(
    session: Session,
    project: Project,
    manifest: dict[str, Any],
    scene: dict[str, Any],
    name: str | None = None,
) -> Project:
    normalized, stems = _normalized_project_manifest(manifest)
    if stems != project.requested_stems:
        raise ValueError("Use the project stem expansion action to add extraction targets")
    project.manifest = normalized
    project.scene = copy.deepcopy(scene)
    if name is not None:
        project.name = name
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_track_settings(
    session: Session,
    project: Project,
    track_id: str,
    manifest_overrides: dict[str, Any],
    scene_overrides: dict[str, Any],
) -> Project:
    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise ValueError("Project track not found")
    track.manifest_overrides = copy.deepcopy(manifest_overrides)
    track.scene_overrides = copy.deepcopy(scene_overrides)
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def expand_project_stems(session: Session, project: Project, stems: Iterable[str]) -> Project:
    requested = list(project.requested_stems)
    next_requested = _normalize_project_stems([*requested, *stems])
    added = [stem for stem in next_requested if stem not in requested]
    if not added:
        return project
    project.requested_stems = next_requested
    project.manifest = copy.deepcopy(project.manifest)
    project.manifest.setdefault("engine", {})["stems"] = next_requested
    project.status = "expanding" if project.prepared_stems else "queued"
    project.progress = 0.0
    project.error = None
    project.status_message = "Waiting to prepare additional stems"
    for track in project.tracks:
        track.status = "queued"
        track.progress = 0.0
        track.error = None
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def project_export_job(session: Session, project: Project) -> Job:
    if not project.prepared_stems or project.status not in {"ready", "expanding", "expansion_failed"}:
        raise ValueError("Project stems are not ready for export")
    manifest = copy.deepcopy(project.manifest)
    manifest.setdefault("engine", {})["stems"] = list(project.prepared_stems)
    snapshot = {
        "manifest": manifest,
        "scene": copy.deepcopy(project.scene),
        "prepared_stems": list(project.prepared_stems),
        "tracks": {
            track.id: {
                "manifest_overrides": copy.deepcopy(track.manifest_overrides),
                "scene_overrides": copy.deepcopy(track.scene_overrides),
            }
            for track in project.tracks
        },
    }
    job = create_job(session, project.import_batch, f"{project.name} export", manifest, True)
    job.project_id = project.id
    job.project_revision = project.revision
    job.project_snapshot = snapshot
    session.commit()
    return job
