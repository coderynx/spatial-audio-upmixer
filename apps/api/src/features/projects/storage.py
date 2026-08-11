"""Project-owned storage and catalogue helpers for separated stems."""

from __future__ import annotations

import io
import json
import math
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from sqlalchemy import delete
from sqlalchemy.orm import Session

from upmixer_web.shared.models import Project, ProjectStem, ProjectTrack

REFERENCE_MATCH_META_FILENAME = "reference_match.json"

PEAKS_FILENAME = "peaks.bin"
PEAKS_META_FILENAME = "peaks.json"
PEAKS_SCHEMA = 1

PEAK_BINS = 4096
"""Waveform envelope resolution per stem, fixed rather than per-second so every
stem of a track shares one bin grid and the binary payload is a plain
rectangular array the browser can slice without parsing a header. A timeline
lane is at most ~1200 CSS px wide showing the whole track, so this covers a 2x
device-pixel-ratio display with headroom."""

PREVIEW_SAMPLE_RATE = 44100
"""Full audible bandwidth: the mix preview drives HRTF spatialization, and a
sub-Nyquist rate here would audibly dull it below the final master's output.
This remains the "high" preview quality tier's rate — see
`PREVIEW_QUALITY_LEVELS` for the lighter, user-selectable tiers."""

_PREVIEW_VORBIS_COMPRESSION_LEVEL = 0.3
"""Low compression (high VBR quality): keeps the proxy near-transparent so
size savings come from lossy coding, not from cutting audible bandwidth.
This is the "high" preview quality tier's compression level."""

PREVIEW_QUALITY_LEVELS: dict[str, tuple[int, float]] = {
    "low": (32000, 0.05),
    "medium": (44100, 0.15),
    "high": (PREVIEW_SAMPLE_RATE, _PREVIEW_VORBIS_COMPRESSION_LEVEL),
}
"""Preview encode presets as (sample_rate, Vorbis VBR compression_level),
trading browser fetch/decode time against the preview's audible fidelity —
this only affects the client-side preview proxy, never the delivered master."""

DEFAULT_PREVIEW_QUALITY = "high"

_PREVIEW_WRITE_CHUNK_SECONDS = 5


def _write_preview(source: Path, destination: Path, *, sample_rate: int, compression_level: float) -> np.ndarray:
    """Encode an OGG Vorbis preview proxy at the given quality preset.

    Returns the encoded (post-resample) audio so a caller that also needs the
    samples — waveform peak generation — reuses this read instead of opening
    the file a second time.
    """
    audio, source_rate = sf.read(str(source), always_2d=True)
    if source_rate != sample_rate:
        divisor = math.gcd(source_rate, sample_rate)
        audio = resample_poly(audio, sample_rate // divisor, source_rate // divisor, axis=0)
    # libsndfile's OGG/Vorbis encoder needs stack proportional to the whole
    # buffer when written via a single sf.write() call, which overflows the
    # thread stack (SIGBUS/SIGSEGV) for long tracks. Writing in fixed-size
    # chunks keeps its per-call stack use bounded regardless of track length.
    chunk_frames = sample_rate * _PREVIEW_WRITE_CHUNK_SECONDS
    with sf.SoundFile(
        str(destination), "w",
        samplerate=sample_rate, channels=audio.shape[1],
        format="OGG", subtype="VORBIS",
        compression_level=compression_level,
    ) as handle:
        for start in range(0, len(audio), chunk_frames):
            handle.write(audio[start:start + chunk_frames])
    return audio


def _compute_peaks(audio: np.ndarray) -> np.ndarray:
    """Reduce audio to a fixed-width (min, max) envelope as signed bytes."""
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    frames = len(mono)
    if frames == 0:
        return np.zeros((PEAK_BINS, 2), dtype=np.int8)
    edges = np.linspace(0, frames, PEAK_BINS + 1).astype(np.int64)
    # `reduceat` indexes samples directly, so a clip shorter than PEAK_BINS
    # frames would produce out-of-range starts; clamping repeats the final
    # sample across the remaining bins instead of raising.
    starts = np.minimum(edges[:-1], frames - 1)
    low = np.minimum.reduceat(mono, starts)
    high = np.maximum.reduceat(mono, starts)
    scaled = np.clip(np.stack([low, high], axis=1), -1.0, 1.0) * 127.0
    return np.round(scaled).astype(np.int8)


class ProjectStemStorage:
    """Keep web project stems isolated from the global CLI cache."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def track_root(self, project_id: str, track_id: str) -> Path:
        path = self.root / project_id / track_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def stem_dir(self, project_id: str, track_id: str) -> Path:
        """The plain stem store directory for one track — no cache identity,
        directly written/read by `upmixer.separation.stem_store.PlainStemStore`."""
        return self.track_root(project_id, track_id) / "stems"

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
        quality: str = DEFAULT_PREVIEW_QUALITY,
    ) -> list[ProjectStem]:
        """Replace a track's stem rows from its plain stem store."""
        entry = self.stem_dir(project.id, track.id)
        try:
            metadata = json.loads((entry / "stems.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Project stem preparation completed without a readable stem store") from exc
        sample_rate = int(metadata["sample_rate"])
        # The store also holds "free" parent stems a model emitted en route to
        # a requested child (see stem_pipeline.py); filter to requested_stems
        # so those don't double-count as playable tracks.
        requested = set(project.requested_stems)
        stem_keys = [
            str(item) for item in metadata["stem_keys"]
            if str(item).split("@", 1)[0] in requested
        ]
        rows: list[ProjectStem] = []
        peaks: list[np.ndarray] = []
        duration_seconds = 0.0
        for stem_key in stem_keys:
            filename = stem_key.replace("@", "__").replace("/", "__").replace("\\", "__") + ".wav"
            path = entry / filename
            if not path.is_file():
                raise RuntimeError(f"Project stem store is missing {filename}")
            info = sf.info(str(path))
            duration_seconds = max(duration_seconds, info.duration)
            preview_path = path.with_suffix(".preview.ogg")
            if not preview_path.is_file():
                sample_rate_hz, compression_level = PREVIEW_QUALITY_LEVELS[quality]
                audio = _write_preview(path, preview_path, sample_rate=sample_rate_hz, compression_level=compression_level)
            else:
                audio, _ = sf.read(str(preview_path), always_2d=True)
            peaks.append(_compute_peaks(audio))
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
        self.write_track_peaks(track, stem_keys, peaks, generation, duration_seconds)
        return rows

    def write_track_peaks(
        self,
        track: ProjectTrack,
        stem_keys: list[str],
        peaks: list[np.ndarray],
        generation: int,
        duration_seconds: float,
    ) -> None:
        """Persist a track's waveform envelopes as one binary block per track.

        Stem blocks are stored back to back in `stem_keys` order and the order
        is repeated in the sidecar, so the browser slices the payload by index
        without the binary needing a header of its own.
        """
        directory = self.track_root(track.project_id, track.id)
        stacked = (
            np.concatenate(peaks, axis=0) if peaks
            else np.zeros((0, 2), dtype=np.int8)
        )
        (directory / PEAKS_FILENAME).write_bytes(stacked.astype(np.int8).tobytes())
        (directory / PEAKS_META_FILENAME).write_text(json.dumps({
            "schema": PEAKS_SCHEMA,
            "bins": PEAK_BINS,
            "generation": generation,
            "duration_seconds": duration_seconds,
            "stems": stem_keys,
        }), encoding="utf-8")
        track.peaks_relative_path = str((directory / PEAKS_FILENAME).relative_to(self.root))
        track.peaks_duration_seconds = duration_seconds

    def read_track_peaks_meta(self, project_id: str, track_id: str) -> dict | None:
        path = self.root / project_id / track_id / PEAKS_META_FILENAME
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return meta if meta.get("schema") == PEAKS_SCHEMA else None

    def track_peaks_path(self, project_id: str, track_id: str) -> Path | None:
        path = self.root / project_id / track_id / PEAKS_FILENAME
        return path if path.is_file() else None

    def rebuild_track_peaks(self, track: ProjectTrack, stems: list[ProjectStem], generation: int) -> None:
        """Backfill a track's peaks from its already-encoded preview proxies.

        Used for projects catalogued before peaks existed: reading the OGG
        proxies costs an order of magnitude less I/O than the full-rate stem
        WAVs, and an envelope drawn a few pixels tall does not need the extra
        fidelity.
        """
        stem_keys: list[str] = []
        peaks: list[np.ndarray] = []
        duration_seconds = 0.0
        for stem in stems:
            path = self.resolve(stem.preview_relative_path or stem.relative_path)
            audio, rate = sf.read(str(path), always_2d=True)
            if rate:
                duration_seconds = max(duration_seconds, len(audio) / rate)
            stem_keys.append(stem.stem_key)
            peaks.append(_compute_peaks(audio))
        self.write_track_peaks(track, stem_keys, peaks, generation, duration_seconds)

    def write_source_preview(self, track: ProjectTrack, source: Path, quality: str = DEFAULT_PREVIEW_QUALITY) -> None:
        """Create the compressed original-track proxy used by project preview."""
        destination = self.track_root(track.project_id, track.id) / "source.preview.ogg"
        if not destination.is_file():
            sample_rate_hz, compression_level = PREVIEW_QUALITY_LEVELS[quality]
            _write_preview(source, destination, sample_rate=sample_rate_hz, compression_level=compression_level)
        track.source_preview_relative_path = str(destination.relative_to(self.root))
        track.source_preview_size_bytes = destination.stat().st_size

    def regenerate_previews(self, project: Project, quality: str, asset_storage) -> None:
        """Re-encode every already-catalogued preview at a newly chosen
        quality tier, overwriting the existing proxy files in place.

        Called when a project's `preview_quality` setting changes after
        stems are already prepared — `catalogue_track`/`write_source_preview`
        only encode once (skip if the file exists), so a quality change needs
        this explicit re-encode rather than relying on the normal pipeline.
        """
        sample_rate_hz, compression_level = PREVIEW_QUALITY_LEVELS[quality]
        for stem in project.stems:
            if not stem.preview_relative_path:
                continue
            source_path = self.resolve(stem.relative_path)
            destination = self.root / stem.preview_relative_path
            _write_preview(source_path, destination, sample_rate=sample_rate_hz, compression_level=compression_level)
            stem.preview_size_bytes = destination.stat().st_size
        for track in project.tracks:
            if not track.source_preview_relative_path:
                continue
            source_path = asset_storage.local_path(track.asset.storage_key)
            destination = self.root / track.source_preview_relative_path
            _write_preview(source_path, destination, sample_rate=sample_rate_hz, compression_level=compression_level)
            track.source_preview_size_bytes = destination.stat().st_size

    def reference_match_dir(self, project_id: str) -> Path:
        path = self.root / project_id / "reference_match"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_reference_match(
        self,
        project_id: str,
        curve: list[tuple[float, float]],
        channels: list[str],
        rms_gain_db: float,
        sample_rate: int,
        n_taps: int,
        signature: str,
    ) -> None:
        """Persist a project's server-precomputed reference-match correction
        curve and level gain.

        `curve` is exactly what `ReferenceMatchProcessor.compute_curve`
        returns — strength/max_db-independent (see
        docs/contracts/preview_export_parity.md Ledger D21), unlike the
        per-channel FIR bank this replaced. No FIR is written to disk:
        `reference_match_fir_wav_bytes` designs one on demand per request, so
        `strength`/`max_db` are live browser knobs with no recompute. Empty
        `curve`/`channels` when spectral matching is disabled; `rms_gain_db`
        still applies.
        """
        directory = self.reference_match_dir(project_id)
        meta = {
            "signature": signature,
            "sample_rate": sample_rate,
            "n_taps": n_taps,
            "curve": [[f, g] for f, g in curve],
            "channels": channels,
            "rms_gain_db": rms_gain_db,
        }
        (directory / REFERENCE_MATCH_META_FILENAME).write_text(
            json.dumps(meta), encoding="utf-8"
        )

    def read_reference_match_meta(self, project_id: str) -> dict | None:
        path = self.root / project_id / "reference_match" / REFERENCE_MATCH_META_FILENAME
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def reference_match_fir_wav_bytes(
        self, project_id: str, strength: float, max_correction_db: float,
    ) -> bytes | None:
        """Render the reference-match FIR for one ``(strength,
        max_correction_db)`` pair from the persisted curve, as WAV bytes
        ready to serve.

        Cheap: ``build_curve_fir`` only scales/clamps the persisted curve and
        runs ``firwin2``/``minimum_phase`` (memoized) — no spectral analysis,
        no pipeline run. Returns ``None`` when no curve is persisted
        (spectral matching disabled, or the reference hasn't been analysed
        yet).
        """
        meta = self.read_reference_match_meta(project_id)
        if not meta or not meta.get("curve"):
            return None
        from upmixer.mastering.match_reference import build_curve_fir

        curve = [(float(f), float(g)) for f, g in meta["curve"]]
        fir = build_curve_fir(
            curve, meta["sample_rate"], meta.get("n_taps", 1023), strength, max_correction_db,
        )
        buffer = io.BytesIO()
        sf.write(buffer, fir.astype(np.float32), meta["sample_rate"], format="WAV", subtype="FLOAT")
        return buffer.getvalue()

    def clear_reference_match(self, project_id: str) -> None:
        shutil.rmtree(self.root / project_id / "reference_match", ignore_errors=True)
