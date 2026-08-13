"""Project lifecycle, settings, export snapshot, and state-machine operations."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer.separation.stem_plan import normalize_stems
from upmixer.formats import FORMAT_MAP, validate_delivery
from upmixer.separation.stem_router import build_stem_routing, fold_route_to_stereo
from upmixer_web.features.jobs.service import create_job
from upmixer_web.features.projects.routing import merge_scene, routing_for_scene
from upmixer_web.features.projects.storage import PREVIEW_QUALITY_LEVELS, ProjectStemStorage
from upmixer_web.shared.manifests import normalize_job_manifest
from upmixer_web.shared.models import ImportBatch, Job, MasteringReference, Project, ProjectStem, ProjectTrack


PROJECT_LOAD_OPTIONS = (
    selectinload(Project.import_batch).selectinload(ImportBatch.assets),
    selectinload(Project.tracks).selectinload(ProjectTrack.asset),
    selectinload(Project.tracks).selectinload(ProjectTrack.stems),
    selectinload(Project.exports).selectinload(Job.tracks),
    selectinload(Project.exports).selectinload(Job.artifacts),
    selectinload(Project.mastering_reference),
)

_CHILD_STEMS = {
    "Vocals": ("Lead Vocals", "Backing Vocals"),
    "Drums": ("Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"),
}

_SEPARATION_ENGINE_KEYS = (
    "stem_batch_size", "stem_segment_size", "stem_chunk_duration_s",
    "stem_model_cache_size", "stem_silence_skip", "stem_silence_threshold_db",
    "stem_silence_min_duration_s", "stem_silence_crossfade_ms", "stem_silence_pad_ms",
    "stem_bleed_reduction", "stem_phase_fix", "stem_phase_fix_low_hz",
    "stem_phase_fix_high_hz", "stem_phase_fix_scale", "stem_phase_fix_reference_model",
    "stem_debleed", "stem_debleed_model",
)

# Engine keys a per-file extraction override may set (bleed reduction is chosen
# per file at prepare time, like the stem list).
_TRACK_ENGINE_OVERRIDE_KEYS = {
    "stems",
    "stem_bleed_reduction",
    "stem_phase_fix",
    "stem_phase_fix_low_hz",
    "stem_phase_fix_high_hz",
    "stem_phase_fix_scale",
    "stem_phase_fix_reference_model",
    "stem_debleed",
    "stem_debleed_model",
}


class ProjectStateConflict(ValueError):
    """A project cannot transition from its current status."""


class TrackNotFoundError(ValueError):
    """A referenced project track does not exist."""


def _separation_settings(manifest: dict[str, Any]) -> tuple[object, ...]:
    """Resolve each separation-engine key against `UpmixConfig` defaults so a
    minimal stored manifest (only ever `engine.mode`/`engine.stems`) compares
    equal to a client save that always sends the full engine block — a
    missing key and its default value must not look like a settings change.
    An empty per-stem override dict (`stem_debleed: {}}`, the client's
    "unset" shape) is canonicalized to `None` to match the config default.
    """
    engine = manifest.get("engine")
    if not isinstance(engine, dict):
        engine = {}
    defaults = UpmixConfig()
    values: list[object] = []
    for key in _SEPARATION_ENGINE_KEYS:
        value = engine.get(key, getattr(defaults, key))
        if isinstance(value, dict) and not value:
            value = None
        values.append(value)
    return tuple(values)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _validate_track_overrides(project: Project, overrides: dict[str, Any]) -> None:
    allowed = {"engine", "mixing", "routing", "mastering", "format", "processing"}
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError(f"Unknown track override blocks: {', '.join(sorted(unknown))}")
    engine = overrides.get("engine", {})
    if engine and (not isinstance(engine, dict) or set(engine) - _TRACK_ENGINE_OVERRIDE_KEYS):
        raise ValueError(
            "Track engine overrides may only set stems and bleed-reduction settings"
        )
    if isinstance(engine, dict) and "stems" in engine:
        stems = _normalize_project_stems(engine["stems"])
        if any(stem not in project.requested_stems for stem in stems):
            raise ValueError("Track stems must be prepared project stems")
    merged = _deep_merge(project.manifest, overrides)
    normalize_job_manifest(merged)


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


def _migrate_legacy_binaural_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fold older stored shapes of the binaural render into the current one.

    Binaural has moved twice: originally a ``mixing.channel_layout: binaural``
    value with the real bed under ``mixing.binaural.bed``, then briefly an
    independent ``mixing.binaural.enabled`` flag — it is now
    ``format.type: binaural`` (a delivery format, alongside ``wav``/
    ``adm-bwf``) with ``format.binaural.profile``. Migrate in place so
    previously stored projects keep validating and round-tripping.
    """
    manifest = copy.deepcopy(manifest)
    mixing = manifest.get("mixing")
    if not isinstance(mixing, dict):
        return manifest
    legacy_binaural = mixing.pop("binaural", None)
    if not isinstance(legacy_binaural, dict):
        return manifest
    was_binaural = mixing.get("channel_layout") == "binaural" or legacy_binaural.get("enabled") is True
    if mixing.get("channel_layout") == "binaural":
        mixing["channel_layout"] = legacy_binaural.get("bed", "7.1.4")
    if not was_binaural:
        return manifest
    format_block = manifest.setdefault("format", {})
    format_block["type"] = "binaural"
    format_block["binaural"] = {"profile": legacy_binaural.get("profile", "studio")}
    return manifest


def _delivery_type_for_layout(channel_layout: str, output_type: str) -> str:
    """Fall back to WAV when a stored delivery type cannot carry the layout.

    A project's speaker layout is its primary control — it drives routing, the
    spatial views and the preview engine — so narrowing it (7.1.4 to 5.1, or to
    stereo) retargets a delivery type the new layout cannot carry instead of
    rejecting the edit over a field the user did not touch. Explicit job
    manifests and CLI flags stay strict; ``formats.validate_delivery`` still
    rejects them.
    """
    try:
        validate_delivery(channel_layout, output_type)
    except ValueError:
        return "wav"
    return output_type


def _normalized_project_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    migrated = _migrate_legacy_binaural_shape(manifest)
    migrated_mixing = migrated.setdefault("mixing", {})
    migrated_format = migrated.setdefault("format", {})
    if isinstance(migrated_mixing, dict) and isinstance(migrated_format, dict):
        migrated_format["type"] = _delivery_type_for_layout(
            str(migrated_mixing.setdefault("channel_layout", "7.1.4")),
            str(migrated_format.get("type", "wav")),
        )
    normalized = normalize_job_manifest(migrated)
    engine = normalized.setdefault("engine", {})
    engine["mode"] = "stem"
    stems = _normalize_project_stems(engine.get("stems") or [])
    engine["stems"] = stems
    mixing = normalized.setdefault("mixing", {})
    if isinstance(mixing.get("stem_solo"), str):
        mixing["stem_solo"] = [mixing["stem_solo"]]
    mixing.setdefault("channel_layout", "7.1.4")
    if mixing["channel_layout"] not in FORMAT_MAP:
        raise ValueError("Unknown channel layout")
    mixing["spatial"] = {"profile": "balanced", "intensity": 0.0, "preanalyze": False}
    mixing["stem_source_anchor_strength"] = mixing.get("stem_source_anchor_strength", 0.0)
    format_block = normalized.setdefault("format", {})
    format_block.setdefault("type", "wav")
    binaural = format_block.setdefault("binaural", {})
    binaural.setdefault("profile", "studio")
    transaural = format_block.setdefault("transaural", {})
    transaural.setdefault("profile", "stereo")
    routing_fmt = FORMAT_MAP[mixing["channel_layout"]]
    if not mixing.get("stem_routing"):
        mixing["stem_routing"] = build_stem_routing(stems, routing_fmt)
    elif routing_fmt.n_channels == 2:
        # Folded on the way in so the client preview, which reads only the
        # manifest, and the export, which folds the built-in base route,
        # normalize over the same channel set.
        mixing["stem_routing"] = {
            stem: fold_route_to_stereo(route)
            for stem, route in mixing["stem_routing"].items()
        }
    routing = normalized.setdefault("routing", {})
    routing["content_mix_strength"] = 0.0
    normalized.setdefault("processing", {})["preview"] = False
    return normalized, stems


def create_empty_project(
    session: Session,
    name: str,
    notes: str | None = None,
    manifest: dict[str, Any] | None = None,
    scene: dict[str, Any] | None = None,
) -> Project:
    """Create a project with no tracks. Assets are added afterwards via
    ``add_project_assets``, one upload session at a time."""
    normalized, stems = _normalized_project_manifest(manifest or {})
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
    to that track's own `manifest_overrides` (stems/sample_rate/subtype/
    channel_layout), validated the same way `update_track_settings` does.

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
    added_stems: list[str] = []
    for overrides in per_asset_overrides.values():
        engine = overrides.get("engine", {}) if isinstance(overrides, dict) else {}
        if isinstance(engine, dict):
            added_stems.extend(engine.get("stems") or [])
    if added_stems:
        project.requested_stems = _normalize_project_stems([*project.requested_stems, *added_stems])
        project.manifest = copy.deepcopy(project.manifest)
        project.manifest.setdefault("engine", {})["stems"] = project.requested_stems
        # New stem picks need their own routing entry immediately, not just
        # at project creation — otherwise a freshly separated stem has no
        # `stem_routing` key, every channel send resolves to zero, and it
        # stays silent until a routing preset happens to touch the dict.
        mixing = project.manifest.setdefault("mixing", {})
        stem_routing = mixing.setdefault("stem_routing", {})
        missing_stems = [stem for stem in project.requested_stems if stem not in stem_routing]
        if missing_stems:
            routing_fmt = FORMAT_MAP[mixing.get("channel_layout", "7.1.4")]
            stem_routing.update(build_stem_routing(missing_stems, routing_fmt))

    for offset, asset in enumerate(import_batch.assets):
        overrides = per_asset_overrides.get(asset.id, {})
        if overrides:
            _validate_track_overrides(project, overrides)
        # Append through the relationship, not a bare session.add(...): the
        # caller's `project` was already loaded (with `tracks` selectinloaded,
        # possibly empty) before this call, and expire_on_commit=False means
        # a same-session re-query below won't refresh an already-populated
        # collection — only appending keeps the in-memory list in sync.
        project.tracks.append(ProjectTrack(
            asset_id=asset.id,
            position=start_position + offset,
            manifest_overrides=copy.deepcopy(overrides),
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
    normalized, stems = _normalized_project_manifest(manifest)
    if stems != project.requested_stems:
        raise ValueError("Use the project stem expansion action to add extraction targets")
    if preview_quality is not None and preview_quality not in PREVIEW_QUALITY_LEVELS:
        raise ValueError(f"Unknown preview quality: {preview_quality}")
    rebuild = _separation_settings(project.manifest) != _separation_settings(normalized)
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


def update_track_settings(
    session: Session,
    project: Project,
    track_id: str,
    manifest_overrides: dict[str, Any],
    scene_overrides: dict[str, Any],
) -> Project:
    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise TrackNotFoundError("Project track not found")
    _validate_track_overrides(project, manifest_overrides)
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


def _resolve_track_routing(
    manifest: dict[str, Any],
    overrides: dict[str, Any],
    requested_stems: list[str],
    scene: dict[str, Any],
) -> dict[str, dict[str, float]] | None:
    """Derive the constant-power speaker routing for a track's positioned
    stems, unless the merged manifest already sets `mixing.stem_routing`
    explicitly — mirrors the precedence `StemRouter` itself applies (manifest
    routing before any caller-supplied override)."""
    probe = _deep_merge(manifest, overrides)
    probe.setdefault("engine", {})["mode"] = "stem"
    probe["engine"]["stems"] = list(requested_stems)
    probe["assets"] = [{"input": "probe-in.wav", "output": "probe-out.wav"}]
    _, asset_jobs = parse_manifest(probe)
    asset_job = asset_jobs[0]
    config = UpmixConfig()
    apply_asset_job(config, asset_job)
    if config.stem_routing is not None:
        return None
    config.stems = asset_job.engine.get("stems") or list(requested_stems)
    return routing_for_scene(scene, config) or None


def project_export_job(session: Session, project: Project, project_stems: ProjectStemStorage) -> Job:
    """Create a self-contained export job from an immutable project snapshot.

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
    manifest = copy.deepcopy(project.manifest)
    manifest.setdefault("engine", {})["stems"] = list(project.prepared_stems)

    tracks_snapshot: dict[str, dict[str, Any]] = {}
    for track in project.tracks:
        overrides = copy.deepcopy(track.manifest_overrides)
        scene = merge_scene(project.scene, track.scene_overrides)
        routing = _resolve_track_routing(manifest, overrides, project.prepared_stems, scene)
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
    anchor_import = project.import_batch or project.tracks[0].asset.import_batch
    job = create_job(
        session, anchor_import, f"{project.name} export", manifest, True,
        mastering_reference=project.mastering_reference,
        assets=[track.asset for track in project.tracks],
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


def reprepare_project_stems(session: Session, project: Project) -> Project:
    """Force a full stem re-separation for a project that already has
    prepared stems, re-running its exact current `requested_stems`.

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


def mark_project_deleting(session: Session, project: Project) -> bool:
    """Mark an in-flight project for worker-side teardown, or signal the
    caller to delete it immediately.

    Returns ``True`` when the project is idle and the caller should delete it
    right away (via ``WorkerManager.delete_now_project``); ``False`` when it
    is in-flight and has been flagged ``deleting`` for the worker to tear down.
    """
    if project.status in {"preparing", "expanding"}:
        project.status = "deleting"
        project.status_message = "Stopping worker before deletion"
        session.commit()
        return False
    return True
