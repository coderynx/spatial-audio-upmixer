"""Project lifecycle, settings, export snapshot, and state-machine operations."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from upmixer.codecs import DEFAULT_CODEC
from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, migrate_format_block, parse_manifest
from upmixer.separation.stem_plan import normalize_stems
from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_router import build_stem_routing
from upmixer_web.features.jobs.service import create_job
from upmixer_web.features.projects.layouts import (
    delivery_codec_for_layout,
    delivery_type_for_layout,
    migrate_legacy_binaural_shape,
    normalize_layout_mix,
    track_layouts,
    track_prepare_overrides,
)
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
    "stem_drum_remask", "stem_drum_remask_alpha",
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


def _normalized_track_layout_block(
    project: Project, layout: str, overrides: dict[str, Any]
) -> dict[str, Any]:
    """Validate one layout's override block and pin it to that layout.

    The same checks a track override has always had, plus the layout work the
    project manifest already did for itself — which track overrides never ran,
    so a per-track two-channel layout used to keep an unfolded multichannel
    routing.
    """
    block = copy.deepcopy(overrides)
    _validate_track_overrides(project, block)
    normalize_layout_mix(block, layout, list(project.requested_stems))
    normalize_job_manifest(_deep_merge(project.manifest, block))
    return block


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
    migrated = migrate_format_block(migrate_legacy_binaural_shape(manifest))
    migrated_mixing = migrated.setdefault("mixing", {})
    migrated_format = migrated.setdefault("format", {})
    if isinstance(migrated_mixing, dict) and isinstance(migrated_format, dict):
        layout = str(migrated_mixing.setdefault("channel_layout", "7.1.4"))
        migrated_format["type"] = delivery_type_for_layout(
            layout, str(migrated_format.get("type", "multichannel"))
        )
        migrated_format["codec"] = delivery_codec_for_layout(
            layout,
            migrated_format["type"],
            str(migrated_format.get("codec", DEFAULT_CODEC)),
            str(migrated_format.get("subtype", UpmixConfig.output_subtype)),
        )
    normalized = normalize_job_manifest(migrated)
    engine = normalized.setdefault("engine", {})
    engine["mode"] = "stem"
    stems = _normalize_project_stems(engine.get("stems") or [])
    engine["stems"] = stems
    mixing = normalized.setdefault("mixing", {})
    if isinstance(mixing.get("stem_solo"), str):
        mixing["stem_solo"] = [mixing["stem_solo"]]
    mixing["spatial"] = {"profile": "balanced", "intensity": 0.0, "preanalyze": False}
    mixing["stem_source_anchor_strength"] = mixing.get("stem_source_anchor_strength", 0.0)
    format_block = normalized.setdefault("format", {})
    format_block.setdefault("type", "multichannel")
    format_block.setdefault("codec", DEFAULT_CODEC)
    binaural = format_block.setdefault("binaural", {})
    binaural.setdefault("profile", "studio")
    transaural = format_block.setdefault("transaural", {})
    transaural.setdefault("profile", "stereo")
    normalize_layout_mix(normalized, str(mixing.setdefault("channel_layout", "7.1.4")), stems)
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

    project_layout = str(project.manifest.get("mixing", {}).get("channel_layout", "7.1.4"))
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
            position=start_position + offset,
            layout_overrides={layout: _normalized_track_layout_block(project, layout, overrides)},
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


def _track_or_raise(project: Project, track_id: str) -> ProjectTrack:
    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise TrackNotFoundError("Project track not found")
    return track


def _seed_layout_block(project: Project, layout: str, source: dict[str, Any]) -> dict[str, Any]:
    """Start a new layout from the track's existing mix, minus the one thing
    that cannot cross layouts: `stem_routing` is keyed by speaker name, so it
    is dropped and rebuilt for the new layout's own channel set rather than
    carried over half-valid."""
    seed = copy.deepcopy(source)
    mixing = seed.get("mixing")
    if isinstance(mixing, dict):
        mixing.pop("stem_routing", None)
    return _normalized_track_layout_block(project, layout, seed)


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
        else _seed_layout_block(project, layout, source)
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
        layout: _normalized_track_layout_block(project, layout, manifest_overrides),
    }
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

    tracks_snapshot: dict[str, dict[str, Any]] = {}
    for track in tracks:
        overrides = copy.deepcopy(track.layout_overrides.get(layout, {}))
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
