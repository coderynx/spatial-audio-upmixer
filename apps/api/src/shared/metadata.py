"""Album discovery and audio tag extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import soundfile as sf


AUDIO_SUFFIXES = {".wav", ".flac"}
COVER_NAMES = {
    "cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg",
    "folder.png", "front.jpg", "front.jpeg", "front.png",
}


@dataclass(frozen=True)
class AudioMetadata:
    """Normalized tags and technical metadata for one track."""

    title: str | None
    artist: str | None
    album: str | None
    release_date: date | None
    track_number: int | None
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None
    embedded_cover: bytes | None = None
    embedded_cover_type: str | None = None


def _first(tags: Any, *names: str) -> str | None:
    if not tags:
        return None
    for name in names:
        value = tags.get(name)
        if value:
            if isinstance(value, list):
                value = value[0]
            return str(value).strip() or None
    return None


def _parse_track_number(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None


def _embedded_cover(audio: Any) -> tuple[bytes | None, str | None]:
    pictures = getattr(audio, "pictures", None)
    if pictures:
        picture = pictures[0]
        return bytes(picture.data), picture.mime or "image/jpeg"
    tags = getattr(audio, "tags", None)
    if tags:
        for value in tags.values():
            if value.__class__.__name__ == "APIC" and getattr(value, "data", None):
                return bytes(value.data), getattr(value, "mime", "image/jpeg")
        covers = tags.get("covr") if hasattr(tags, "get") else None
        if covers:
            cover = covers[0]
            image_format = getattr(cover, "imageformat", None)
            return bytes(cover), "image/png" if image_format == 14 else "image/jpeg"
    return None, None


def read_audio_metadata(path: Path) -> AudioMetadata:
    """Read tags when available and always attempt technical inspection."""
    title = artist = album = release = track = None
    cover = cover_type = None
    try:
        from mutagen import File
        audio = File(path, easy=False)
        if audio is not None:
            easy = File(path, easy=True)
            tags = getattr(easy, "tags", None)
            title = _first(tags, "title")
            artist = _first(tags, "albumartist", "artist")
            album = _first(tags, "album")
            release = _first(tags, "date", "year")
            track = _first(tags, "tracknumber")
            cover, cover_type = _embedded_cover(audio)
    except Exception:
        pass

    duration = sample_rate = channels = None
    try:
        info = sf.info(str(path))
        duration = info.duration
        sample_rate = info.samplerate
        channels = info.channels
    except RuntimeError:
        pass

    return AudioMetadata(
        title=title or path.stem,
        artist=artist,
        album=album,
        release_date=_parse_date(release),
        track_number=_parse_track_number(track),
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        embedded_cover=cover,
        embedded_cover_type=cover_type,
    )


def find_directory_cover(paths: list[Path]) -> Path | None:
    """Choose a conventional album-cover file from an import."""
    candidates = [path for path in paths if path.name.lower() in COVER_NAMES]
    return sorted(candidates, key=lambda path: (len(path.parts), path.name.lower()))[0] if candidates else None
