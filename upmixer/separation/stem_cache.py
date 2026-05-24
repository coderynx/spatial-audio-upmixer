"""Stem separation cache — skip re-separating unchanged input files.

Cache structure on disk::

    {cache_dir}/
        {key}/
            metadata.json          # cache-key components for validation
            Vocals.wav             # per-stem PCM_24 WAV
            Bass.wav
            Drums.wav
            Other.wav
            Vocals__front.wav      # zone-tagged: '@' replaced by '__'
            ...

Cache key: SHA-256 of ``abs_path|mtime|stems_hash|sep_sr|preview_tag``
(first 20 hex chars).

``stems_hash`` is a 20-char digest of the sorted requested stem names
(from :func:`~upmixer.separation.stem_plan.resolve_separation_plan`).
Different stem selections always produce different cache entries.

``preview_tag`` encodes whether the stems are a preview slice:
``"full"`` for a complete separation, or ``"preview:{duration:.3f}@{start:.3f}"``
for a preview window.  This ensures preview stems and full stems never share
a cache entry — disabling preview after a preview run produces a cold miss
and triggers a fresh full-file separation.

Preview stems are **never written** to cache (they are short-lived test
artifacts and would waste disk space for little benefit).

Cache invalidation: any change to abs_path, mtime, stems_hash, sep_sr, or
preview window produces a different key → cold miss.

Stems are stored as float32 PCM_24 WAV (soundfile).  On load, arrays are
returned as float64 to match the rest of the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np

_log = logging.getLogger("upmixer")

_METADATA_FILE = "metadata.json"
_MTIME_TOLERANCE = 2.0


def _preview_tag(
    is_preview: bool,
    preview_duration: float | None,
    preview_start: float | None,
) -> str:
    """Return a cache-key component that encodes the preview window (or 'full')."""
    if not is_preview:
        return "full"
    dur = preview_duration if preview_duration is not None else 30.0
    start = preview_start if preview_start is not None else -1.0
    return f"preview:{dur:.3f}@{start:.3f}"


def _cache_key(
    input_path: str,
    stems_hash: str,
    sep_sr: int,
    is_preview: bool = False,
    preview_duration: float | None = None,
    preview_start: float | None = None,
) -> str:
    """Return a 20-char hex cache key for the given separation parameters.

    Preview and full-file runs always produce different keys, so a cached
    preview never masks a subsequent full-file separation.

    Args:
        stems_hash: 20-char digest from
                    :attr:`~upmixer.separation.stem_plan.SeparationPlan.stems_hash`.
                    Encodes the full set of requested stems so different stem
                    selections never share a cache entry.
    """
    abs_path = str(Path(input_path).resolve())
    mtime = os.path.getmtime(abs_path)
    tag = _preview_tag(is_preview, preview_duration, preview_start)
    raw = f"{abs_path}|{mtime:.6f}|{stems_hash}|{sep_sr}|{tag}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _stem_filename(stem_key: str) -> str:
    """Convert a stem key (possibly zone-tagged) to a safe filename.

    ``"Vocals@front"`` → ``"Vocals__front.wav"``
    """
    safe = stem_key.replace("@", "__").replace("/", "__").replace("\\", "__")
    return f"{safe}.wav"


class StemCache:
    """On-disk cache for separated stems.

    Args:
        cache_dir: Root directory for the cache.  Created if it does not exist.
    """

    def __init__(self, cache_dir: str) -> None:
        self._root = Path(cache_dir)
        self._root.mkdir(parents=True, exist_ok=True)


    def load(
        self,
        input_path: str,
        stems_hash: str,
        sep_sr: int,
        is_preview: bool = False,
        preview_duration: float | None = None,
        preview_start: float | None = None,
    ) -> tuple[dict[str, np.ndarray], int] | None:
        """Try to load cached stems for the given parameters.

        Args:
            input_path:       Original input audio file path.
            stems_hash:       20-char digest from SeparationPlan.stems_hash.
            sep_sr:           Target separation sample rate in Hz.
            is_preview:       Whether this is a preview (sliced) run.
            preview_duration: Preview window length in seconds.
            preview_start:    Preview window start in seconds (None = auto-center).

        Returns:
            ``(stems_dict, sample_rate)`` on cache hit, or ``None`` on miss.
            Stems are returned as float64 arrays shaped ``(n_samples, 2)``.
        """
        key = _cache_key(input_path, stems_hash, sep_sr, is_preview, preview_duration, preview_start)
        entry_dir = self._root / key

        if not entry_dir.exists():
            return None

        meta_path = entry_dir / _METADATA_FILE
        if not meta_path.exists():
            _log.debug("  StemCache: metadata missing for key %s", key)
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.debug("  StemCache: corrupt metadata (%s), ignoring", exc)
            return None

        try:
            current_mtime = os.path.getmtime(str(Path(input_path).resolve()))
        except OSError:
            return None
        stored_mtime = float(meta.get("mtime", 0.0))
        if abs(current_mtime - stored_mtime) > _MTIME_TOLERANCE:
            _log.debug(
                "  StemCache: mtime mismatch (stored=%.3f, current=%.3f)",
                stored_mtime, current_mtime,
            )
            return None

        try:
            import soundfile as sf  # type: ignore[import-untyped]
        except ImportError:
            _log.debug("  StemCache: soundfile not available, skipping cache")
            return None

        stems: dict[str, np.ndarray] = {}
        stem_keys: list[str] = meta.get("stem_keys", [])
        for stem_key in stem_keys:
            wav_path = entry_dir / _stem_filename(stem_key)
            if not wav_path.exists():
                _log.debug(
                    "  StemCache: missing file %s for key %s", wav_path.name, key
                )
                return None
            data, _ = sf.read(str(wav_path), dtype="float64", always_2d=True)
            stems[stem_key] = data

        if not stems:
            return None

        _log.info(
            "  StemCache: HIT — loaded %d stems from %s",
            len(stems), entry_dir,
        )
        return stems, sep_sr


    def save(
        self,
        input_path: str,
        stems_hash: str,
        sep_sr: int,
        stems: dict[str, np.ndarray],
        sample_rate: int,
        is_preview: bool = False,
        preview_duration: float | None = None,
        preview_start: float | None = None,
    ) -> None:
        """Write stems to the cache.

        Preview stems (``is_preview=True``) are never cached — they are short
        test slices that should not be served to subsequent full-file runs.

        Args:
            input_path:       Original input audio file path.
            stems_hash:       20-char digest from SeparationPlan.stems_hash.
            sep_sr:           Target separation sample rate in Hz.
            stems:            Dict stem_key → ``(n_samples, 2)`` float array.
            sample_rate:      Actual sample rate of the stems (should equal sep_sr).
            is_preview:       If ``True``, skip writing (preview stems not cached).
            preview_duration: Preview window length in seconds.
            preview_start:    Preview window start in seconds (None = auto-center).
        """
        if is_preview:
            _log.debug("  StemCache: preview mode — skipping cache write")
            return

        try:
            import soundfile as sf  # type: ignore[import-untyped]
        except ImportError:
            _log.debug("  StemCache: soundfile not available, skipping cache write")
            return

        abs_path = str(Path(input_path).resolve())
        mtime = os.path.getmtime(abs_path)
        key = _cache_key(input_path, stems_hash, sep_sr, is_preview, preview_duration, preview_start)
        entry_dir = self._root / key
        entry_dir.mkdir(parents=True, exist_ok=True)

        for stem_key, audio in stems.items():
            wav_path = entry_dir / _stem_filename(stem_key)
            arr = audio if audio.ndim == 2 else audio[:, np.newaxis]
            sf.write(str(wav_path), arr.astype(np.float32), sample_rate, subtype="PCM_24")

        meta = {
            "input_path": abs_path,
            "mtime": round(mtime, 6),
            "stems_hash": stems_hash,
            "sep_sr": sep_sr,
            "stem_keys": list(stems.keys()),
        }
        (entry_dir / _METADATA_FILE).write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        _log.info(
            "  StemCache: saved %d stems → %s",
            len(stems), entry_dir,
        )
