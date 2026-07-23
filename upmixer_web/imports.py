"""Upload ingestion, ZIP expansion, and album preview creation."""

from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import UploadFile
from sqlalchemy.orm import Session

from upmixer_web.metadata import AUDIO_SUFFIXES, COVER_NAMES, find_directory_cover, read_audio_metadata
from upmixer_web.models import ImportBatch, MasteringReference, MediaAsset
from upmixer_web.storage import ObjectStorage


MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024 * 1024


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.name:
        raise ValueError(f"Unsafe upload path: {value}")
    return normalized


def _extract_zip(path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("ZIP contains too many entries")
        for member in members:
            if member.is_dir():
                continue
            relative = _safe_relative_path(member.filename)
            suffix = relative.suffix.lower()
            if suffix not in AUDIO_SUFFIXES and relative.name.lower() not in COVER_NAMES:
                continue
            total += member.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIP expanded size exceeds limit")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return extracted


def ingest_uploads(
    session: Session,
    storage: ObjectStorage,
    work_root: Path,
    files: list[UploadFile],
    relative_paths: list[str],
) -> ImportBatch:
    """Store uploaded files, extract metadata, and return an album preview."""
    if not files:
        raise ValueError("At least one file is required")
    if relative_paths and len(relative_paths) != len(files):
        raise ValueError("relative_paths must match uploaded files")

    import_id = str(uuid.uuid4())
    staging = work_root / f"import-{import_id}"
    staging.mkdir(parents=True)
    staged: list[Path] = []
    relative_by_path: dict[Path, PurePosixPath] = {}
    try:
        for index, upload in enumerate(files):
            relative = _safe_relative_path(
                relative_paths[index] if relative_paths else (upload.filename or f"upload-{index}")
            )
            destination = staging.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            if destination.suffix.lower() == ".zip":
                archive_dir = staging / f"archive-{index}"
                archive_dir.mkdir()
                for extracted in _extract_zip(destination, archive_dir):
                    staged.append(extracted)
                    relative_by_path[extracted] = PurePosixPath(*extracted.relative_to(archive_dir).parts)
            else:
                staged.append(destination)
                relative_by_path[destination] = relative

        audio_paths = [path for path in staged if path.suffix.lower() in AUDIO_SUFFIXES]
        if not audio_paths:
            raise ValueError("No supported WAV or FLAC audio files found")

        cover_path = find_directory_cover(staged)
        batch = ImportBatch(
            id=import_id,
            kind="album" if len(audio_paths) > 1 else "track",
            title=None,
            artist=None,
            release_date=None,
        )
        session.add(batch)

        metadata_rows = []
        for source in audio_paths:
            metadata_rows.append((source, read_audio_metadata(source)))
        metadata_rows.sort(
            key=lambda item: (
                item[1].track_number if item[1].track_number is not None else 1_000_000,
                str(relative_by_path[item[0]]).lower(),
            )
        )

        embedded_cover = next((meta for _, meta in metadata_rows if meta.embedded_cover), None)
        if cover_path:
            cover_suffix = cover_path.suffix.lower()
            cover_key = f"imports/{import_id}/cover{cover_suffix}"
            storage.put_file(cover_key, cover_path)
            batch.cover_key = cover_key
        elif embedded_cover and embedded_cover.embedded_cover:
            suffix = ".png" if embedded_cover.embedded_cover_type == "image/png" else ".jpg"
            cover_key = f"imports/{import_id}/cover{suffix}"
            storage.put_stream(cover_key, io.BytesIO(embedded_cover.embedded_cover))
            batch.cover_key = cover_key

        for position, (source, metadata) in enumerate(metadata_rows):
            relative = relative_by_path[source]
            storage_key = f"imports/{import_id}/audio/{position:04d}-{source.name}"
            with source.open("rb") as stream:
                size, digest = storage.put_stream(storage_key, stream)
            session.add(MediaAsset(
                import_id=import_id,
                position=position,
                filename=source.name,
                relative_path=str(relative),
                storage_key=storage_key,
                sha256=digest,
                size_bytes=size,
                title=metadata.title,
                artist=metadata.artist,
                album=metadata.album,
                release_date=metadata.release_date,
                track_number=metadata.track_number,
                duration_seconds=metadata.duration_seconds,
                sample_rate=metadata.sample_rate,
                channels=metadata.channels,
            ))

        albums = [meta.album for _, meta in metadata_rows if meta.album]
        artists = [meta.artist for _, meta in metadata_rows if meta.artist]
        dates = [meta.release_date for _, meta in metadata_rows if meta.release_date]
        batch.title = max(set(albums), key=albums.count) if albums else (audio_paths[0].parent.name if len(audio_paths) > 1 else metadata_rows[0][1].title)
        batch.artist = max(set(artists), key=artists.count) if artists else None
        batch.release_date = min(dates) if dates else None
        session.commit()
        session.refresh(batch)
        return batch
    except Exception:
        session.rollback()
        storage.delete_prefix(f"imports/{import_id}")
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ingest_mastering_reference(
    session: Session,
    storage: ObjectStorage,
    work_root: Path,
    import_batch: ImportBatch,
    upload: UploadFile,
) -> MasteringReference:
    """Store one validated mastering reference for an existing source import."""
    filename = upload.filename or "reference.wav"
    relative = _safe_relative_path(filename)
    if relative.suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError("Reference audio must be WAV or FLAC")

    reference_id = str(uuid.uuid4())
    staging = work_root / f"reference-{reference_id}"
    staging.mkdir(parents=True)
    source = staging / relative.name
    try:
        with source.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        metadata = read_audio_metadata(source)
        if metadata.sample_rate is None or metadata.channels is None:
            raise ValueError("Reference audio could not be read")
        storage_key = f"imports/{import_batch.id}/references/{reference_id}-{source.name}"
        with source.open("rb") as stream:
            size, digest = storage.put_stream(storage_key, stream)
        reference = MasteringReference(
            id=reference_id,
            import_id=import_batch.id,
            filename=source.name,
            storage_key=storage_key,
            sha256=digest,
            size_bytes=size,
            duration_seconds=metadata.duration_seconds,
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
        )
        session.add(reference)
        session.commit()
        session.refresh(reference)
        return reference
    except Exception:
        session.rollback()
        storage.delete_prefix(f"imports/{import_batch.id}/references/{reference_id}")
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
