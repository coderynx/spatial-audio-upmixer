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

Cache key: SHA-256 of
``schema|abs_path|mtime|size|inference_hash|sep_sr|preview_tag|silence_params|engine_version``
(first 20 hex chars). When a caller supplies a stable ``path_key`` (``abs_path``
becomes that key), ``mtime`` and ``size`` are dropped from the key so a source
re-materialized to a fresh path/mtime still hits — ``load`` then validates the
stored ``size`` from metadata to catch a genuinely changed source.

``inference_hash`` identifies model sequence and intermediate lineage. Requests
using the same models share entries; every stem emitted at no extra inference
cost is retained, while only requested stems proceed to mixing. Legacy
request-specific cache entries remain readable.

``preview_tag`` encodes whether the stems are a preview slice:
``"full"`` for a complete separation, or ``"preview:{duration:.3f}@{start:.3f}"``
for a preview window.  This ensures preview stems and full stems never share
a cache entry — disabling preview after a preview run produces a cold miss
and triggers a fresh full-file separation.

Preview stems are **never written** to cache (they are short-lived test
artifacts and would waste disk space for little benefit).

Cache invalidation: any change to source metadata, inference plan, engine
version, sample rate, preview window, or silence-skip parameters produces a
cold miss.

Stems are stored as PCM_24 WAV and loaded as float32 to bound pipeline RAM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

import numpy as np

_log = logging.getLogger("upmixer")

_METADATA_FILE = "metadata.json"
_MTIME_TOLERANCE = 2.0
_CACHE_SCHEMA = 3

# Bump on any change to the in-core inference engine that can change stem
# output (architecture code, demix numerics, model registry). This — not a
# third-party package version — is now what identifies "what produced this
# cached audio."
_ENGINE_VERSION = "upmixer-sep-2"


def _engine_version() -> str:
    return _ENGINE_VERSION


def _legacy_cache_key(
    input_path: str,
    stems_hash: str,
    sep_sr: int,
    is_preview: bool = False,
    preview_duration: float | None = None,
    preview_start: float | None = None,
    silence_skip: bool = True,
    silence_threshold_db: float = -90.0,
    silence_min_duration_s: float = 2.0,
    silence_crossfade_ms: float = 10.0,
    silence_pad_ms: float = 200.0,
    path_key: str | None = None,
) -> str:
    """Return pre-v2 key for backward-compatible cache reads."""
    abs_path = path_key if path_key is not None else str(Path(input_path).resolve())
    tag = _preview_tag(is_preview, preview_duration, preview_start)
    silence_tag = (
        f"skip={silence_skip}"
        f"|thr={silence_threshold_db:.1f}"
        f"|min={silence_min_duration_s:.3f}"
        f"|xfade={silence_crossfade_ms:.1f}"
        f"|pad={silence_pad_ms:.1f}"
    )
    # A path_key caller owns a stable logical identity, so mtime — which
    # changes whenever the source is re-materialized (relocation, copy,
    # object-store download) — must stay out of the key; staleness is
    # validated against stored size/mtime in load() instead.
    if path_key is not None:
        raw = f"{abs_path}|{stems_hash}|{sep_sr}|{tag}|{silence_tag}"
    else:
        mtime = os.path.getmtime(str(Path(input_path).resolve()))
        raw = f"{abs_path}|{mtime:.6f}|{stems_hash}|{sep_sr}|{tag}|{silence_tag}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


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
    silence_skip: bool = True,
    silence_threshold_db: float = -90.0,
    silence_min_duration_s: float = 2.0,
    silence_crossfade_ms: float = 10.0,
    silence_pad_ms: float = 200.0,
    path_key: str | None = None,
) -> str:
    """Return a 20-char hex cache key for the given separation parameters.

    Preview and full-file runs always produce different keys, so a cached
    preview never masks a subsequent full-file separation.  Silence-skip
    parameters are included so changing the threshold or durations invalidates
    any previously cached stems.

    Args:
        stems_hash: 20-char inference-plan digest. The argument name remains
                    for source compatibility with cache v1 callers.
        path_key:   Stable identity to hash instead of ``input_path``'s
                    resolved filesystem path. A caller that already isolates
                    one cache entry per logical source (e.g. one directory per
                    project track) can pass a deployment-independent identity
                    here so relocating the data directory or working directory
                    doesn't orphan the cache; ``mtime``/``size`` staleness
                    checks still read the real file at ``input_path``.
    """
    resolved = str(Path(input_path).resolve())
    abs_path = path_key if path_key is not None else resolved
    tag = _preview_tag(is_preview, preview_duration, preview_start)
    silence_tag = (
        f"skip={silence_skip}"
        f"|thr={silence_threshold_db:.1f}"
        f"|min={silence_min_duration_s:.3f}"
        f"|xfade={silence_crossfade_ms:.1f}"
        f"|pad={silence_pad_ms:.1f}"
    )
    # A path_key caller owns a stable logical identity, so mtime/size — which
    # change whenever the source is re-materialized — are excluded from the key.
    # load() still validates them from stored metadata to catch a real change.
    if path_key is not None:
        raw = (
            f"v{_CACHE_SCHEMA}|{abs_path}|{stems_hash}|"
            f"{sep_sr}|{tag}|{silence_tag}|engine={_engine_version()}"
        )
    else:
        stat = os.stat(resolved)
        raw = (
            f"v{_CACHE_SCHEMA}|{abs_path}|{stat.st_mtime:.6f}|{stat.st_size}|{stems_hash}|"
            f"{sep_sr}|{tag}|{silence_tag}|engine={_engine_version()}"
        )
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

    def _find_path_key_entry(
        self,
        path_key: str,
        stems_hash: str,
        sep_sr: int,
        silence_skip: bool,
        silence_threshold_db: float,
        silence_min_duration_s: float,
        silence_crossfade_ms: float,
        silence_pad_ms: float,
    ) -> Path | None:
        """Find a cached entry for ``path_key`` by scanning stored metadata.

        Recovers entries written under an older key formula (mtime/size once
        formed part of the key) by matching the output-affecting components
        recorded in each entry's metadata. Preview entries are never written,
        so only ``full`` entries exist to match here.
        """
        for meta_path in self._root.glob(f"*/{_METADATA_FILE}"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                meta.get("path_key") == path_key
                and meta.get("stems_hash") == stems_hash
                and meta.get("sep_sr") == sep_sr
                and bool(meta.get("silence_skip", True)) == silence_skip
                and float(meta.get("silence_threshold_db", -90.0)) == silence_threshold_db
                and float(meta.get("silence_min_duration_s", 2.0)) == silence_min_duration_s
                and float(meta.get("silence_crossfade_ms", 10.0)) == silence_crossfade_ms
                and float(meta.get("silence_pad_ms", 200.0)) == silence_pad_ms
            ):
                return meta_path.parent
        return None

    def load(
        self,
        input_path: str,
        stems_hash: str,
        sep_sr: int,
        is_preview: bool = False,
        preview_duration: float | None = None,
        preview_start: float | None = None,
        silence_skip: bool = True,
        silence_threshold_db: float = -90.0,
        silence_min_duration_s: float = 2.0,
        silence_crossfade_ms: float = 10.0,
        silence_pad_ms: float = 200.0,
        path_key: str | None = None,
    ) -> tuple[dict[str, np.ndarray], int] | None:
        """Try to load cached stems for the given parameters.

        Args:
            input_path:             Original input audio file path.
            stems_hash:             Inference-plan digest (v1-compatible name).
            sep_sr:                 Target separation sample rate in Hz.
            is_preview:             Whether this is a preview (sliced) run.
            preview_duration:       Preview window length in seconds.
            preview_start:          Preview window start in seconds.
            silence_skip:           Whether silence-skip was enabled.
            silence_threshold_db:   Silence threshold used during separation.
            silence_min_duration_s: Minimum silent run duration used.
            silence_crossfade_ms:   Crossfade length used at span boundaries.
            silence_pad_ms:         Span padding used.
            path_key:               Stable identity to key the cache entry by,
                                     in place of ``input_path``'s resolved path.

        Returns:
            ``(stems_dict, sample_rate)`` on cache hit, or ``None`` on miss.
            Stems are returned as float32 arrays shaped ``(n_samples, 2)``.
        """
        key = _cache_key(
            input_path, stems_hash, sep_sr,
            is_preview, preview_duration, preview_start,
            silence_skip, silence_threshold_db, silence_min_duration_s,
            silence_crossfade_ms, silence_pad_ms, path_key,
        )
        entry_dir = self._root / key
        if not entry_dir.exists():
            legacy_key = _legacy_cache_key(
                input_path, stems_hash, sep_sr,
                is_preview, preview_duration, preview_start,
                silence_skip, silence_threshold_db, silence_min_duration_s,
                silence_crossfade_ms, silence_pad_ms, path_key,
            )
            entry_dir = self._root / legacy_key
            if not entry_dir.exists():
                # Entries written before mtime/size left the path_key formula
                # (see _cache_key) are keyed by a hash this build no longer
                # computes. The cache root is the caller's per-track directory,
                # so scan its handful of entries for a metadata match instead of
                # forcing an already-prepared project to re-separate.
                scanned = self._find_path_key_entry(
                    path_key, stems_hash, sep_sr,
                    silence_skip, silence_threshold_db, silence_min_duration_s,
                    silence_crossfade_ms, silence_pad_ms,
                ) if path_key is not None else None
                if scanned is None:
                    return None
                entry_dir = scanned

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
            current_stat = os.stat(str(Path(input_path).resolve()))
        except OSError:
            return None
        if path_key is not None:
            # Identity is the caller's stable path_key, not the file's mtime
            # (which drifts on re-materialization). Validate size — a changed
            # size means the logical source's content actually differs.
            stored_size = meta.get("size")
            if stored_size is not None and int(stored_size) != current_stat.st_size:
                _log.debug(
                    "  StemCache: size mismatch (stored=%s, current=%d)",
                    stored_size, current_stat.st_size,
                )
                return None
        else:
            stored_mtime = float(meta.get("mtime", 0.0))
            if abs(current_stat.st_mtime - stored_mtime) > _MTIME_TOLERANCE:
                _log.debug(
                    "  StemCache: mtime mismatch (stored=%.3f, current=%.3f)",
                    stored_mtime, current_stat.st_mtime,
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
            data, _ = sf.read(str(wav_path), dtype="float32", always_2d=True)
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
        silence_skip: bool = True,
        silence_threshold_db: float = -90.0,
        silence_min_duration_s: float = 2.0,
        silence_crossfade_ms: float = 10.0,
        silence_pad_ms: float = 200.0,
        path_key: str | None = None,
    ) -> None:
        """Write stems to the cache.

        Preview stems (``is_preview=True``) are never cached — they are short
        test slices that should not be served to subsequent full-file runs.

        Args:
            input_path:             Original input audio file path.
            stems_hash:             20-char digest from SeparationPlan.stems_hash.
            sep_sr:                 Target separation sample rate in Hz.
            stems:                  Dict stem_key → ``(n_samples, 2)`` float array.
            sample_rate:            Actual sample rate of the stems.
            is_preview:             If ``True``, skip writing.
            preview_duration:       Preview window length in seconds.
            preview_start:          Preview window start in seconds.
            silence_skip:           Whether silence-skip was enabled.
            silence_threshold_db:   Silence threshold used during separation.
            silence_min_duration_s: Minimum silent run duration used.
            silence_crossfade_ms:   Crossfade length used at span boundaries.
            silence_pad_ms:         Span padding used.
            path_key:               Stable identity to key the cache entry by,
                                     in place of ``input_path``'s resolved path.
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
        source_stat = os.stat(abs_path)
        mtime = source_stat.st_mtime
        key = _cache_key(
            input_path, stems_hash, sep_sr,
            is_preview, preview_duration, preview_start,
            silence_skip, silence_threshold_db, silence_min_duration_s,
            silence_crossfade_ms, silence_pad_ms, path_key,
        )
        entry_dir = self._root / key
        entry_dir.mkdir(parents=True, exist_ok=True)

        for stem_key, audio in stems.items():
            wav_path = entry_dir / _stem_filename(stem_key)
            arr = audio if audio.ndim == 2 else audio[:, np.newaxis]
            temp_handle = tempfile.NamedTemporaryFile(
                dir=entry_dir, prefix=f".{wav_path.stem}.", suffix=".tmp.wav",
                delete=False,
            )
            temp_path = Path(temp_handle.name)
            temp_handle.close()
            try:
                sf.write(
                    str(temp_path), arr.astype(np.float32, copy=False), sample_rate,
                    subtype="PCM_24",
                )
                os.replace(temp_path, wav_path)
            finally:
                temp_path.unlink(missing_ok=True)

        meta = {
            "cache_schema": _CACHE_SCHEMA,
            "engine_version": _engine_version(),
            "input_path": abs_path,
            "path_key": path_key,
            "mtime": round(mtime, 6),
            "size": source_stat.st_size,
            "stems_hash": stems_hash,
            "sep_sr": sep_sr,
            "stem_keys": list(stems.keys()),
            "silence_skip": silence_skip,
            "silence_threshold_db": silence_threshold_db,
            "silence_min_duration_s": silence_min_duration_s,
            "silence_crossfade_ms": silence_crossfade_ms,
            "silence_pad_ms": silence_pad_ms,
        }
        meta_path = entry_dir / _METADATA_FILE
        temp_meta = entry_dir / f".{_METADATA_FILE}.tmp"
        temp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(temp_meta, meta_path)
        _log.info(
            "  StemCache: saved %d stems → %s",
            len(stems), entry_dir,
        )
