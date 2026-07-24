"""Project-owned storage and catalogue helpers for separated stems."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import soundfile as sf
from scipy.signal import resample_poly
from sqlalchemy import delete
from sqlalchemy.orm import Session

from upmixer_web.models import Project, ProjectStem, ProjectTrack

PREVIEW_SAMPLE_RATE = 44100
"""Full audible bandwidth: the mix preview drives HRTF spatialization, and a
sub-Nyquist rate here would audibly dull it below the final master's output."""

_PREVIEW_VORBIS_COMPRESSION_LEVEL = 0.3
"""Low compression (high VBR quality): keeps the proxy near-transparent so
size savings come from lossy coding, not from cutting audible bandwidth."""


_PREVIEW_WRITE_CHUNK_FRAMES = PREVIEW_SAMPLE_RATE * 5


def _write_preview(source: Path, destination: Path) -> None:
    """Encode a near-transparent OGG Vorbis proxy for fast preview playback."""
    audio, sample_rate = sf.read(str(source), always_2d=True)
    if sample_rate != PREVIEW_SAMPLE_RATE:
        divisor = math.gcd(sample_rate, PREVIEW_SAMPLE_RATE)
        audio = resample_poly(audio, PREVIEW_SAMPLE_RATE // divisor, sample_rate // divisor, axis=0)
    # libsndfile's OGG/Vorbis encoder needs stack proportional to the whole
    # buffer when written via a single sf.write() call, which overflows the
    # thread stack (SIGBUS/SIGSEGV) for long tracks. Writing in fixed-size
    # chunks keeps its per-call stack use bounded regardless of track length.
    with sf.SoundFile(
        str(destination), "w",
        samplerate=PREVIEW_SAMPLE_RATE, channels=audio.shape[1],
        format="OGG", subtype="VORBIS",
        compression_level=_PREVIEW_VORBIS_COMPRESSION_LEVEL,
    ) as handle:
        for start in range(0, len(audio), _PREVIEW_WRITE_CHUNK_FRAMES):
            handle.write(audio[start:start + _PREVIEW_WRITE_CHUNK_FRAMES])


class ProjectStemStorage:
    """Keep web project stems isolated from the global CLI cache."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def track_root(self, project_id: str, track_id: str) -> Path:
        path = self.root / project_id / track_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def delete_project(self, project_id: str) -> None:
        shutil.rmtree(self.root / project_id, ignore_errors=True)

    def resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def catalogue_track(
        self,
        session: Session,
        project: Project,
        track: ProjectTrack,
        generation: int,
    ) -> list[ProjectStem]:
        """Replace a track's stem rows from its newest valid cache entry."""
        root = self.track_root(project.id, track.id)
        candidates: list[tuple[float, Path, dict]] = []
        for metadata_path in root.glob("*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                keys = metadata.get("stem_keys")
                if isinstance(keys, list) and keys:
                    candidates.append((metadata_path.stat().st_mtime, metadata_path.parent, metadata))
            except (OSError, ValueError, TypeError):
                continue
        if not candidates:
            raise RuntimeError("Project stem preparation completed without a readable stem cache")
        _, entry, metadata = max(candidates, key=lambda item: item[0])
        sample_rate = int(metadata["sep_sr"])
        stem_keys = [str(item) for item in metadata["stem_keys"]]
        rows: list[ProjectStem] = []
        for stem_key in stem_keys:
            filename = stem_key.replace("@", "__").replace("/", "__").replace("\\", "__") + ".wav"
            path = entry / filename
            if not path.is_file():
                raise RuntimeError(f"Project stem cache is missing {filename}")
            info = sf.info(str(path))
            preview_path = path.with_suffix(".preview.ogg")
            if not preview_path.is_file():
                _write_preview(path, preview_path)
            rows.append(ProjectStem(
                project_id=project.id,
                track_id=track.id,
                stem_key=stem_key,
                relative_path=str(path.relative_to(self.root)),
                sample_rate=info.samplerate or sample_rate,
                channels=info.channels,
                size_bytes=path.stat().st_size,
                generation=generation,
                preview_relative_path=str(preview_path.relative_to(self.root)),
                preview_size_bytes=preview_path.stat().st_size,
            ))
        session.execute(delete(ProjectStem).where(ProjectStem.track_id == track.id))
        session.add_all(rows)
        return rows

    def write_source_preview(self, track: ProjectTrack, source: Path) -> None:
        """Create the compressed original-track proxy used by project preview."""
        destination = self.track_root(track.project_id, track.id) / "source.preview.ogg"
        if not destination.is_file():
            _write_preview(source, destination)
        track.source_preview_relative_path = str(destination.relative_to(self.root))
        track.source_preview_size_bytes = destination.stat().st_size
