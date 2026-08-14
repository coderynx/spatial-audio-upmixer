"""Project archive: a portable .upmix.zip Save/Open, mirroring a DAW's
project file — export bundles a project's settings, source audio, and
prepared stem cache; import reconstructs an identical workspace with newly
minted ids, on this server or another one."""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from upmixer_web.features.projects.service import _normalized_project_manifest, get_project
from upmixer_web.features.projects.storage import REFERENCE_MATCH_META_SUFFIX, ProjectStemStorage
from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectStem, ProjectTrack
from upmixer_web.shared.storage import ObjectStorage

ARCHIVE_FORMAT_VERSION = 2
MANIFEST_FILENAME = "project.json"


def safe_component(name: str) -> str:
    """Sanitize a stem key or track/project name into one path-safe zip
    entry filename."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_.@ " else "_" for ch in name)
    return cleaned or "stem"


def _archived_reference_match(
    manifest: dict[str, Any], project_manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Read archived reference-match curves as ``{layout: meta}``. A format-1
    archive stored one unkeyed curve; it belongs to the project's layout."""
    stored = manifest.get("reference_match")
    if not isinstance(stored, dict) or not stored:
        return {}
    if "curve" not in stored:
        return {layout: meta for layout, meta in stored.items() if isinstance(meta, dict)}
    layout = project_manifest.get("mixing", {}).get("channel_layout", "7.1.4")
    return {layout: stored}


def _track_layout_overrides(
    track_data: dict[str, Any], project_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Read a track's per-layout mixes, folding a format-1 archive's single
    ``manifest_overrides`` block onto the layout it was mixed for."""
    layouts = track_data.get("layout_overrides")
    if isinstance(layouts, dict) and layouts:
        return layouts
    overrides = track_data.get("manifest_overrides") or {}
    mixing = overrides.get("mixing") if isinstance(overrides.get("mixing"), dict) else {}
    layout = mixing.get("channel_layout") or project_manifest.get("mixing", {}).get("channel_layout", "7.1.4")
    return {layout: {**overrides, "mixing": {**mixing, "channel_layout": layout}}}


def export_project_archive(
    project: Project,
    storage: ObjectStorage,
    project_stems: ProjectStemStorage,
    destination: Path,
) -> None:
    """Write *project* as a portable archive to *destination*.

    Every entry path referenced by a track/stem is recorded in the manifest
    JSON rather than re-derived by convention on import, so renaming this
    function's own layout later can't desync from a reader written against
    an older version — only ``ARCHIVE_FORMAT_VERSION`` needs to bump for an
    actual breaking change.
    """
    manifest_tracks: list[dict[str, Any]] = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, track in enumerate(project.tracks):
            track_dir = f"tracks/{index:03d}"
            asset = track.asset
            asset_entry = f"{track_dir}/source/{asset.filename}"
            archive.write(storage.local_path(asset.storage_key), asset_entry)

            stems_meta: list[dict[str, Any]] = []
            for stem in track.stems:
                stem_name = safe_component(stem.stem_key)
                stem_entry = f"{track_dir}/stems/{stem_name}.wav"
                archive.write(project_stems.resolve(stem.relative_path), stem_entry)
                preview_entry = None
                if stem.preview_relative_path:
                    preview_entry = f"{track_dir}/stems/{stem_name}.preview.ogg"
                    archive.write(project_stems.resolve(stem.preview_relative_path), preview_entry)
                stems_meta.append({
                    "stem_key": stem.stem_key,
                    "sample_rate": stem.sample_rate,
                    "channels": stem.channels,
                    "audio_entry": stem_entry,
                    "preview_entry": preview_entry,
                })

            peaks_entry = None
            if track.peaks_relative_path:
                peaks_entry = f"{track_dir}/peaks.bin"
                archive.write(project_stems.resolve(track.peaks_relative_path), peaks_entry)
                meta_path = (project_stems.root / track.peaks_relative_path).with_name("peaks.json")
                if meta_path.is_file():
                    archive.write(meta_path, f"{track_dir}/peaks.json")

            source_preview_entry = None
            if track.source_preview_relative_path:
                source_preview_entry = f"{track_dir}/source_preview.ogg"
                archive.write(project_stems.resolve(track.source_preview_relative_path), source_preview_entry)

            manifest_tracks.append({
                "position": track.position,
                "layout_overrides": track.layout_overrides,
                "scene_overrides": track.scene_overrides,
                "asset": {
                    "filename": asset.filename,
                    "relative_path": asset.relative_path,
                    "title": asset.title,
                    "artist": asset.artist,
                    "album": asset.album,
                    "release_date": asset.release_date.isoformat() if asset.release_date else None,
                    "track_number": asset.track_number,
                    "duration_seconds": asset.duration_seconds,
                    "sample_rate": asset.sample_rate,
                    "channels": asset.channels,
                    "entry": asset_entry,
                },
                "stems": stems_meta,
                "peaks_entry": peaks_entry,
                "peaks_duration_seconds": track.peaks_duration_seconds,
                "source_preview_entry": source_preview_entry,
            })

        reference_meta = None
        reference = project.mastering_reference
        if reference is not None:
            reference_entry = f"mastering_reference/{reference.filename}"
            archive.write(storage.local_path(reference.storage_key), reference_entry)
            reference_meta = {
                "filename": reference.filename,
                "duration_seconds": reference.duration_seconds,
                "sample_rate": reference.sample_rate,
                "channels": reference.channels,
            }

        # The reference-match asset is now just a JSON curve (see
        # `ProjectStemStorage.write_reference_match`) — no separate binary
        # blob to bundle, embed the meta dicts directly in the manifest, one
        # per speaker layout the project holds a curve for.
        reference_match_meta = {
            layout: project_stems.read_reference_match_meta(project.id, layout)
            for layout in project_stems.reference_match_layouts(project.id)
        }

        manifest = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "project": {
                "name": project.name,
                "notes": project.notes,
                "manifest": project.manifest,
                "scene": project.scene,
                "view_state": project.view_state,
                "requested_stems": project.requested_stems,
                "prepared_stems": project.prepared_stems,
                "stem_generation": project.stem_generation,
                "preview_quality": project.preview_quality,
            },
            "mastering_reference": reference_meta,
            "reference_match": reference_match_meta,
            "tracks": manifest_tracks,
        }
        archive.writestr(MANIFEST_FILENAME, json.dumps(manifest))


class _ChunkSink:
    """zipfile writes here; the generator below drains it between reads. No
    tell()/seek(), so ZipFile falls back to streaming mode with per-entry
    data descriptors — lets a track's stems zip stream to the client without
    buffering the whole archive on disk or in memory."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def iter_track_stems_zip(entries: list[tuple[Path, str]]) -> Iterator[bytes]:
    """Stream a zip of *entries* (source path, entry name) as it is written.
    Caller must have already resolved and stat-checked every path — a
    missing file here would surface mid-body, after the 200 is committed."""
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as bundle:
        for path, name in entries:
            info = zipfile.ZipInfo(name)
            info.file_size = path.stat().st_size
            with bundle.open(info, "w") as target, path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    target.write(chunk)
                    if data := sink.drain():
                        yield data
            if data := sink.drain():
                yield data
    if data := sink.drain():
        yield data


def _extract(archive: zipfile.ZipFile, entry: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(entry) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    return destination


def import_project_archive(
    session: Session,
    storage: ObjectStorage,
    project_stems: ProjectStemStorage,
    work_root: Path,
    archive_path: Path,
) -> Project:
    """Reconstruct a project from a ``.upmix.zip`` written by
    ``export_project_archive``. Mints new ids throughout — the result is a
    functionally identical workspace, not a literal restore of the original
    database rows (which may not even exist on this server)."""
    staging = work_root / f"project-import-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    project: Project | None = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(MANIFEST_FILENAME))
            archive_version = manifest.get("format_version")
            if archive_version not in (1, ARCHIVE_FORMAT_VERSION):
                raise ValueError(f"Unsupported project archive version: {archive_version}")
            project_data = manifest["project"]
            tracks_data = manifest.get("tracks", [])

            import_batch = ImportBatch(
                kind="album" if len(tracks_data) > 1 else "track",
                title=project_data.get("name"),
            )
            session.add(import_batch)
            session.flush()

            project = Project(
                import_id=import_batch.id,
                name=project_data["name"],
                notes=project_data.get("notes"),
                manifest=_normalized_project_manifest(project_data["manifest"])[0],
                scene=project_data.get("scene", {}),
                view_state=project_data.get("view_state", {}),
                requested_stems=project_data.get("requested_stems", []),
                prepared_stems=project_data.get("prepared_stems", []),
                stem_generation=project_data.get("stem_generation", 0),
                preview_quality=project_data.get("preview_quality", "high"),
                status="ready",
                status_message="Imported project",
            )
            session.add(project)
            session.flush()

            reference_meta = manifest.get("mastering_reference")
            if reference_meta:
                extracted = _extract(
                    archive, f"mastering_reference/{reference_meta['filename']}",
                    staging / "reference" / reference_meta["filename"],
                )
                storage_key = f"imports/{import_batch.id}/references/{uuid.uuid4().hex}-{reference_meta['filename']}"
                with extracted.open("rb") as stream:
                    size, digest = storage.put_stream(storage_key, stream)
                reference = MasteringReference(
                    import_id=import_batch.id, filename=reference_meta["filename"],
                    storage_key=storage_key, sha256=digest, size_bytes=size,
                    duration_seconds=reference_meta.get("duration_seconds"),
                    sample_rate=reference_meta.get("sample_rate"), channels=reference_meta.get("channels"),
                )
                session.add(reference)
                session.flush()
                project.mastering_reference_id = reference.id

            for index, track_data in enumerate(tracks_data):
                asset_data = track_data["asset"]
                extracted_asset = _extract(archive, asset_data["entry"], staging / asset_data["entry"])
                storage_key = f"imports/{import_batch.id}/audio/{index:04d}-{asset_data['filename']}"
                with extracted_asset.open("rb") as stream:
                    size, digest = storage.put_stream(storage_key, stream)
                asset = MediaAsset(
                    import_id=import_batch.id, position=index,
                    filename=asset_data["filename"],
                    relative_path=asset_data.get("relative_path", asset_data["filename"]),
                    storage_key=storage_key, sha256=digest, size_bytes=size,
                    title=asset_data.get("title"), artist=asset_data.get("artist"), album=asset_data.get("album"),
                    release_date=date.fromisoformat(asset_data["release_date"]) if asset_data.get("release_date") else None,
                    track_number=asset_data.get("track_number"),
                    duration_seconds=asset_data.get("duration_seconds"),
                    sample_rate=asset_data.get("sample_rate"), channels=asset_data.get("channels"),
                )
                session.add(asset)
                session.flush()

                track = ProjectTrack(
                    asset_id=asset.id, position=track_data.get("position", index),
                    status="ready", progress=1.0,
                    layout_overrides=_track_layout_overrides(track_data, project.manifest),
                    scene_overrides=track_data.get("scene_overrides", {}),
                )
                project.tracks.append(track)
                session.flush()

                track_root = project_stems.track_root(project.id, track.id)
                for stem_data in track_data.get("stems", []):
                    stem_dest = _extract(archive, stem_data["audio_entry"], track_root / Path(stem_data["audio_entry"]).name)
                    preview_relative = None
                    preview_size = None
                    if stem_data.get("preview_entry"):
                        preview_dest = _extract(
                            archive, stem_data["preview_entry"], track_root / Path(stem_data["preview_entry"]).name,
                        )
                        preview_relative = str(preview_dest.relative_to(project_stems.root))
                        preview_size = preview_dest.stat().st_size
                    session.add(ProjectStem(
                        project_id=project.id, track_id=track.id, stem_key=stem_data["stem_key"],
                        relative_path=str(stem_dest.relative_to(project_stems.root)),
                        sample_rate=stem_data["sample_rate"], channels=stem_data["channels"],
                        size_bytes=stem_dest.stat().st_size, generation=project.stem_generation,
                        preview_relative_path=preview_relative, preview_size_bytes=preview_size,
                    ))

                if track_data.get("peaks_entry"):
                    peaks_dest = _extract(archive, track_data["peaks_entry"], track_root / "peaks.bin")
                    meta_entry = track_data["peaks_entry"].replace("peaks.bin", "peaks.json")
                    if meta_entry in archive.namelist():
                        (track_root / "peaks.json").write_bytes(archive.read(meta_entry))
                    track.peaks_relative_path = str(peaks_dest.relative_to(project_stems.root))
                    track.peaks_duration_seconds = track_data.get("peaks_duration_seconds")

                if track_data.get("source_preview_entry"):
                    preview_dest = _extract(archive, track_data["source_preview_entry"], track_root / "source.preview.ogg")
                    track.source_preview_relative_path = str(preview_dest.relative_to(project_stems.root))
                    track.source_preview_size_bytes = preview_dest.stat().st_size

            for layout, layout_meta in _archived_reference_match(manifest, project.manifest).items():
                rm_dir = project_stems.reference_match_dir(project.id)
                (rm_dir / f"{layout}{REFERENCE_MATCH_META_SUFFIX}").write_text(
                    json.dumps(layout_meta), encoding="utf-8"
                )

        session.commit()
        return get_project(session, project.id)  # type: ignore[return-value]
    except Exception:
        # Capture the id before rollback: Session.rollback() expires every
        # instance in the session, and re-reading an expired attribute on a
        # row that was never actually committed raises ObjectDeletedError,
        # which would replace the real failure with a confusing new one.
        orphaned_id = project.id if project is not None else None
        session.rollback()
        if orphaned_id is not None:
            project_stems.delete_project(orphaned_id)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
