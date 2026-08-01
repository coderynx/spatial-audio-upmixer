"""Plain stem store — a caller-owned directory of separated stems.

Unlike :class:`~upmixer.separation.stem_cache.StemCache`, this has no
cache-identity key: the caller (one directory per logical source, e.g. a
project track) owns the identity, so there is nothing to hash and nothing
that can go stale when a model/engine version changes.

Directory layout::

    {stem_dir}/
        stems.json      # schema, stem_keys, sample_rate, source_size
        Vocals.wav      # per-stem PCM_24 WAV
        Bass.wav
        Vocals__front.wav   # zone-tagged: '@' replaced by '__'
        ...
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

_MANIFEST_FILE = "stems.json"
_SCHEMA = 1


def _stem_filename(stem_key: str) -> str:
    """Convert a stem key (possibly zone-tagged) to a safe filename.

    ``"Vocals@front"`` → ``"Vocals__front.wav"``
    """
    safe = stem_key.replace("@", "__").replace("/", "__").replace("\\", "__")
    return f"{safe}.wav"


class PlainStemStore:
    """Read/write a directory of separated stems with no cache identity.

    Args:
        stem_dir: Directory to read/write. Created on write if missing.
    """

    def __init__(self, stem_dir: str) -> None:
        self._root = Path(stem_dir)

    def load(self) -> tuple[dict[str, np.ndarray], int] | None:
        """Load previously written stems, or ``None`` if none are present.

        Returns:
            ``(stems_dict, sample_rate)``. Stems are float32 arrays shaped
            ``(n_samples, 2)``.
        """
        manifest_path = self._root / _MANIFEST_FILE
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        stem_keys = manifest.get("stem_keys")
        sample_rate = manifest.get("sample_rate")
        if not stem_keys or not sample_rate:
            return None

        import soundfile as sf  # type: ignore[import-untyped]

        stems: dict[str, np.ndarray] = {}
        for stem_key in stem_keys:
            wav_path = self._root / _stem_filename(stem_key)
            if not wav_path.exists():
                return None
            data, _ = sf.read(str(wav_path), dtype="float32", always_2d=True)
            stems[stem_key] = data
        if not stems:
            return None
        return stems, int(sample_rate)

    def write(
        self,
        stems: dict[str, np.ndarray],
        sample_rate: int,
        *,
        source_size: int | None = None,
    ) -> None:
        """Write stems, replacing any previous contents of this directory."""
        import soundfile as sf  # type: ignore[import-untyped]

        self._root.mkdir(parents=True, exist_ok=True)
        for stem_key, audio in stems.items():
            wav_path = self._root / _stem_filename(stem_key)
            arr = audio if audio.ndim == 2 else audio[:, np.newaxis]
            temp_path = self._root / f".{wav_path.stem}.tmp.wav"
            sf.write(str(temp_path), arr.astype(np.float32, copy=False), sample_rate, subtype="PCM_24")
            os.replace(temp_path, wav_path)

        manifest = {
            "schema": _SCHEMA,
            "stem_keys": list(stems.keys()),
            "sample_rate": sample_rate,
            "source_size": source_size,
        }
        manifest_path = self._root / _MANIFEST_FILE
        temp_manifest = self._root / f".{_MANIFEST_FILE}.tmp"
        temp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temp_manifest, manifest_path)
