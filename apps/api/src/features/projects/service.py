"""Project lifecycle, settings, export snapshot, and state-machine operations."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer_web.features.jobs.service import create_job
from upmixer_web.features.projects.configuration import (
    normalize_project_manifest,
    normalize_project_stems,
    normalize_track_layout_block,
    preparation_manifest,
    resolve_scene_routing,
    seed_layout_block,
    separation_settings,
)
from upmixer_web.features.projects.layouts import (
    seed_balanced_mix,
    track_layouts,
    track_prepare_overrides,
)
from upmixer_web.features.projects.deletion import mark_project_deleting as _mark_project_deleting
from upmixer_web.features.projects.routing import merge_scene
from upmixer_web.features.projects.storage import PREVIEW_QUALITY_LEVELS, ProjectStemStorage
from upmixer_web.shared.models import ImportBatch, Job, MasteringReference, Project, ProjectTrack


PROJECT_LOAD_OPTIONS = (
    selectinload(Project.import_batch).selectinload(ImportBatch.assets),
    selectinload(Project.tracks).selectinload(ProjectTrack.asset),
    selectinload(Project.tracks).selectinload(ProjectTrack.stems),
    selectinload(Project.exports).selectinload(Job.tracks),
    selectinload(Project.exports).selectinload(Job.artifacts),
    selectinload(Project.mastering_reference),
)

class ProjectStateConflict(ValueError):
    """A project cannot transition from its current status."""


class TrackNotFoundError(ValueError):
    """A referenced project track does not exist."""


def get_project(session: Session, project_id: str) -> Project | None:
    return session.scalar(select(Project).where(Project.id == project_id).options(*PROJECT_LOAD_OPTIONS))


def list_projects(session: Session, limit: int = 100, offset: int = 0) -> list[Project]:
    return list(session.scalars(
        select(Project).options(*PROJECT_LOAD_OPTIONS).order_by(Project.created_at.desc()).offset(offset).limit(limit)
    ).all())


def create_empty_project(
    session: Session,
    name: str,
    notes: str | None = None,
    manifest: dict[str, Any] | None = None,
    scene: dict[str, Any] | None = None,
) -> Project:
    """Create a project with no tracks. Assets are added afterwards via
    ``add_project_assets``, one upload session at a time."""
    normalized, stems = normalize_project_manifest(manifest or {})
    project = Project(
        import_id=None,
        name=name,
        notes=notes,
        manifest=normalized,
        scene=copy.deepcopy(scene or {}),
        requested_stems=stems,
        prepared_stems=[],
        status="ready",
        status_message="No tracks yet",
    )
    session.add(project)
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def add_project_assets(
    session: Session,
    project: Project,
    import_batch: ImportBatch,
    per_asset_overrides: dict[str, dict[str, Any]] | None = None,
) -> Project:
    """Add every asset in a freshly-ingested import batch to a project as new
    tracks, queuing preparation. ``per_asset_overrides`` maps a `MediaAsset.id`
    to that track's own override blocks (stems/sample_rate/subtype/
    channel_layout), validated the same way `update_track_layout_settings`
    does. The staged `mixing.channel_layout` becomes the track's first layout;
    further layouts are added later from the Prepare tab.

    Sets the project's own ``import_id`` when this is its first import, so an
    empty project keeps one FK anchor once it has any tracks at all — later
    calls (a project's second upload session) leave it untouched.
    """
    per_asset_overrides = per_asset_overrides or {}
    start_position = max((track.position for track in project.tracks), default=-1) + 1

    # Union every asset's requested stems into the project first, so a
    # file's own stem picks don't need to already be a prepared project
    # stem before `_validate_track_overrides` (below) checks against it —
    # this is what lets per-file selection add new extraction targets.
    project.manifest = copy.deepcopy(project.manifest)
    added_stems: list[str] = []
    for overrides in per_asset_overrides.values():
        engine = overrides.get("engine", {}) if isinstance(overrides, dict) else {}
        if isinstance(engine, dict):
            added_stems.extend(engine.get("stems") or [])
    if added_stems:
        project.requested_stems = normalize_project_stems([*project.requested_stems, *added_stems])
        project.manifest.setdefault("engine", {})["stems"] = project.requested_stems

    project_layout = str(project.manifest.get("mixing", {}).get("channel_layout", "7.1.4"))
    seed_balanced_mix(project.manifest, project_layout, list(project.requested_stems))
    for offset, asset in enumerate(import_batch.assets):
        overrides = per_asset_overrides.get(asset.id, {})
        layout = str(overrides.get("mixing", {}).get("channel_layout") or project_layout)
        # Append through the relationship, not a bare session.add(...): the
        # caller's `project` was already loaded (with `tracks` selectinloaded,
        # possibly empty) before this call, and expire_on_commit=False means
        # a same-session re-query below won't refresh an already-populated
        # collection — only appending keeps the in-memory list in sync.
        project.tracks.append(ProjectTrack(
            asset_id=asset.id,
            name=asset.title or asset.filename,
            position=start_position + offset,
            layout_overrides={layout: normalize_track_layout_block(project, layout, overrides, seed_balanced=True)},
        ))
    if project.import_id is None:
        project.import_id = import_batch.id
    project.status = "expanding" if project.prepared_stems else "queued"
    project.progress = 0.0
    project.error = None
    project.status_message = "Waiting to prepare project stems"
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_project_settings(
    session: Session,
    project: Project,
    manifest: dict[str, Any],
    scene: dict[str, Any],
    name: str | None = None,
    notes: str | None = None,
    mastering_reference: MasteringReference | None = None,
    preview_quality: str | None = None,
) -> Project:
    normalized, stems = normalize_project_manifest(manifest, seed_balanced=False)
    if stems != project.requested_stems:
        raise ValueError("Use the project stem expansion action to add extraction targets")
    if preview_quality is not None and preview_quality not in PREVIEW_QUALITY_LEVELS:
        raise ValueError(f"Unknown preview quality: {preview_quality}")
    rebuild = separation_settings(project.manifest) != separation_settings(normalized)
    if rebuild:
        seed_balanced_mix(
            normalized,
            str(normalized.get("mixing", {}).get("channel_layout", "7.1.4")),
            stems,
        )
    project.manifest = normalized
    project.scene = copy.deepcopy(scene)
    if name is not None:
        project.name = name
    if notes is not None:
        project.notes = notes
    if preview_quality is not None:
        project.preview_quality = preview_quality
    project.mastering_reference = mastering_reference
    if rebuild:
        project.prepared_stems = []
        project.status = "queued"
        project.progress = 0.0
        project.error = None
        project.status_message = "Waiting to rebuild project stems"
        for track in project.tracks:
            track.status = "queued"
            track.progress = 0.0
            track.error = None
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_project_view_state(session: Session, project: Project, view_state: dict[str, Any]) -> None:
    """Timeline/monitoring preferences only — no manifest normalization, no
    separation rebuild, no revision bump. A master-fader drag must not pay
    for `update_project_settings`'s manifest diffing."""
    project.view_state = view_state
    session.commit()


def _track_or_raise(project: Project, track_id: str) -> ProjectTrack:
    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise TrackNotFoundError("Project track not found")
    return track


def set_track_layouts(
    session: Session, project: Project, track_id: str, layouts: Iterable[str]
) -> Project:
    """Replace a track's layout set. Layouts it gains are seeded from the
    track's current mix, re-placed onto the new layout's speakers; layouts it
    loses take their mix with them. A track always keeps at least one."""
    track = _track_or_raise(project, track_id)
    wanted = list(dict.fromkeys(layouts))
    if not wanted:
        raise ValueError("A track must keep at least one speaker layout")
    unknown = [layout for layout in wanted if layout not in FORMAT_MAP]
    if unknown:
        raise ValueError(f"Unknown channel layout: {', '.join(unknown)}")
    current = track.layout_overrides
    source = track_prepare_overrides(track)
    track.layout_overrides = {
        layout: copy.deepcopy(current[layout])
        if layout in current
        else seed_layout_block(project, layout, source)
        for layout in wanted
    }
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_track_layout_settings(
    session: Session,
    project: Project,
    track_id: str,
    layout: str,
    manifest_overrides: dict[str, Any],
    scene_overrides: dict[str, Any],
) -> Project:
    """Save one layout's mix on a track. Every other layout on that track is
    untouched — that separation is the point of the per-layout store."""
    track = _track_or_raise(project, track_id)
    if layout not in track_layouts(track, project):
        raise ValueError("Track does not have that speaker layout")
    track.layout_overrides = {
        **track.layout_overrides,
        layout: normalize_track_layout_block(project, layout, manifest_overrides),
    }
    track.scene_overrides = copy.deepcopy(scene_overrides)
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def update_project_track_name(session: Session, project: Project, track_id: str, name: str) -> Project:
    track = _track_or_raise(project, track_id)
    track.name = name.strip()
    if not track.name:
        raise ValueError("Track name cannot be blank")
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def expand_project_stems(session: Session, project: Project, stems: Iterable[str]) -> Project:
    requested = list(project.requested_stems)
    next_requested = normalize_project_stems([*requested, *stems])
    added = [stem for stem in next_requested if stem not in requested]
    if not added:
        return project
    project.requested_stems = next_requested
    project.manifest = copy.deepcopy(project.manifest)
    project.manifest.setdefault("engine", {})["stems"] = next_requested
    project_layout = str(project.manifest.get("mixing", {}).get("channel_layout", "7.1.4"))
    seed_balanced_mix(project.manifest, project_layout, next_requested)
    project.status = "expanding" if project.prepared_stems else "queued"
    project.progress = 0.0
    project.error = None
    project.status_message = "Waiting to prepare additional stems"
    for track in project.tracks:
        track.layout_overrides = {layout: seed_balanced_mix(copy.deepcopy(overrides), layout, next_requested) for layout, overrides in track.layout_overrides.items()}
        track.status = "queued"
        track.progress = 0.0
        track.error = None
    project.revision += 1
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def project_export_job(
    session: Session, project: Project, project_stems: ProjectStemStorage, layout: str
) -> Job:
    """Create a self-contained export job for one speaker layout, from an
    immutable project snapshot.

    One export renders one layout, so only the tracks that actually have
    ``layout`` take part — the job stays one asset per `JobTrack`.

    The job needs nothing from `features.projects` at run time: each track's
    resolved stem-routing and manifest overrides are baked into
    `job.project_snapshot["tracks"]` (keyed by asset id, shared between
    `ProjectTrack` and the cloned `JobTrack`) here, and `stem_input_dir`
    points the job straight at the project's own already-separated stems —
    see `shared.manifests.materialize_manifest`, which reads this snapshot as
    plain data.
    """
    if not project.prepared_stems or project.status not in {"ready", "expanding", "expansion_failed"}:
        raise ValueError("Project stems are not ready for export")
    if not project.tracks:
        raise ValueError("Project has no tracks to export")
    tracks = [track for track in project.tracks if layout in track_layouts(track, project)]
    if not tracks:
        raise ValueError(f"No track uses the {layout} speaker layout")
    manifest = copy.deepcopy(project.manifest)
    manifest.setdefault("engine", {})["stems"] = list(project.prepared_stems)
    manifest.setdefault("mixing", {})["channel_layout"] = layout
    manifest.setdefault("mastering", {}).setdefault("qc", {}).setdefault(
        "measure_binaural", False
    )

    tracks_snapshot: dict[str, dict[str, Any]] = {}
    for track in tracks:
        overrides = copy.deepcopy(track.layout_overrides.get(layout, {}))
        scene = merge_scene(project.scene, track.scene_overrides)
        routing = resolve_scene_routing(manifest, overrides, project.prepared_stems, scene)
        if routing:
            mixing_override = dict(overrides.get("mixing", {}))
            mixing_override.setdefault("stem_routing", routing)
            overrides["mixing"] = mixing_override
        tracks_snapshot[track.asset_id] = {
            "manifest_overrides": overrides,
            "stem_input_dir": str(project_stems.stem_dir(project.id, track.id)),
        }

    # A project's tracks may not all share project.import_batch (or it may be
    # None, for an empty-created project) once assets are added incrementally
    # — Job.import_id only needs an anchor for its FK, so any track's own
    # import batch will do; the actual JobTracks are cloned from `assets=`.
    anchor_import = project.import_batch or tracks[0].asset.import_batch
    job = create_job(
        session, anchor_import, f"{project.name} export ({layout})", manifest, True,
        mastering_reference=project.mastering_reference,
        assets=[track.asset for track in tracks],
    )
    job.project_id = project.id
    job.project_revision = project.revision
    job.project_snapshot = {"tracks": tracks_snapshot}
    session.commit()
    return job


def retry_project(session: Session, project: Project) -> Project:
    """Requeue a failed project preparation, mirroring its current stage."""
    if project.status not in {"failed", "expansion_failed"}:
        raise ProjectStateConflict("Project is not retryable")
    project.status = "expanding" if project.prepared_stems else "queued"
    project.progress = 0.0
    project.error = None
    project.status_message = "Waiting for worker"
    for track in project.tracks:
        track.status = "queued"
        track.progress = 0.0
        track.error = None
    session.commit()
    return project


def reprepare_project_stems(
    session: Session,
    project: Project,
    stems: Iterable[str] | None = None,
    stem_bleed_reduction: bool | None = None,
    stem_ensemble: bool | None = None,
) -> Project:
    """Force a full stem re-separation for a project that already has
    prepared stems, optionally replacing its extraction targets, cleanup, and
    ensemble setting for every track.

    Unlike `retry_project` (only for a failed run), this is for a `ready`
    project whose on-disk stems now miss the separation engine's cache
    identity — e.g. a separation-model/registry change (see
    ~/Projects/upmixer-knowledge/roadmap.md's "cache-identity misses" standing
    risk) landed after this project's stems were prepared. `_run_project`
    always re-separates every requested track wholesale, so re-queuing it is
    enough to repopulate the cache under the current engine version.
    """
    if project.status in {"preparing", "expanding", "queued", "deleting"}:
        raise ProjectStateConflict("Project stem preparation is already in progress")
    if not project.tracks:
        raise ProjectStateConflict("Project has no tracks to prepare")
    requested_stems = normalize_project_stems(stems) if stems is not None else list(project.requested_stems)
    if not requested_stems:
        raise ValueError("Select at least one stem")
    engine = project.manifest.get("engine", {})
    cleanup = (
        stem_bleed_reduction
        if stem_bleed_reduction is not None
        else bool(engine.get("stem_bleed_reduction", UpmixConfig().stem_bleed_reduction))
    )
    ensemble = (
        stem_ensemble
        if stem_ensemble is not None
        else bool(engine.get("stem_ensemble", UpmixConfig().stem_ensemble))
    )
    layout = str(project.manifest.get("mixing", {}).get("channel_layout", "7.1.4"))
    project.manifest = preparation_manifest(
        project.manifest, requested_stems, cleanup, layout, ensemble
    )
    project.requested_stems = requested_stems
    project.status = "expanding" if project.prepared_stems else "queued"
    project.progress = 0.0
    project.error = None
    project.status_message = "Waiting for worker"
    for track in project.tracks:
        track.layout_overrides = {
            layout: preparation_manifest(overrides, requested_stems, cleanup, layout)
            for layout, overrides in track.layout_overrides.items()
        }
        track.status = "queued"
        track.progress = 0.0
        track.error = None
    project.revision += 1
    session.commit()
    return project


def mark_project_deleting(session: Session, project: Project) -> bool:
    """Retain the project-service deletion transition import path."""
    return _mark_project_deleting(session, project)
