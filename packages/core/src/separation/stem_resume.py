"""Crash checkpoint for a separation plan.

The stem cache (``stem_cache.py``) is all-or-nothing: it stores a finished
separation, so a run that dies at the last of five model stages re-runs all
five next time. This module keeps the intermediate stems of the stages that
*did* finish, so the next run picks up where the failure was.

Nothing is written while a run is healthy — the checkpoint is taken in the
exception path and cleared as soon as the plan completes. A resumed run
therefore costs one directory read; a successful run costs nothing.

Checkpoints live under ``{stem_cache_dir}/partial/{key}/``, keyed by the same
identity the stem cache uses (source file, inference plan, sample rate,
preview window, silence parameters, engine version) plus the zone or span
tag, so a checkpoint can never be replayed into a run it was not taken from.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

_log = logging.getLogger(__name__)

_STATE_FILE = "state.json"
_SCHEMA = 1


def _stem_filename(name: str) -> str:
    safe = name.replace("@", "__").replace("/", "__").replace("\\", "__")
    return f"{safe}.wav"


class ResumeStore:
    """Stage-boundary checkpoint for one :func:`execute_plan` call."""

    def __init__(self, root: Path, key: str, sample_rate: int) -> None:
        self.root = root
        self.key = key
        self.sample_rate = sample_rate

    @classmethod
    def open(
        cls, cache_dir: str | None, key: str | None, sample_rate: int
    ) -> "ResumeStore | None":
        """Return a store, or ``None`` when resume is not configured."""
        if not cache_dir or not key:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        return cls(Path(cache_dir) / "partial" / digest, key, sample_rate)

    def restore(self) -> tuple[int, dict[str, np.ndarray], dict[str, str]] | None:
        """Return ``(stages_completed, loaded, on_disk)`` from a checkpoint.

        ``None`` when there is no checkpoint, or when the one on disk does not
        match this run or is incomplete.
        """
        state_path = self.root / _STATE_FILE
        if not state_path.is_file():
            return None
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            _log.warning("  Resume: unreadable checkpoint at %s — ignoring", self.root)
            return None
        if state.get("schema") != _SCHEMA or state.get("key") != self.key:
            return None

        loaded: dict[str, np.ndarray] = {}
        on_disk: dict[str, str] = {}
        for name in state.get("disk", []):
            path = self.root / _stem_filename(name)
            if not path.is_file():
                _log.warning(
                    "  Resume: checkpoint is missing '%s' — starting over", name
                )
                return None
            on_disk[name] = str(path)
        for name in state.get("loaded", []):
            path = self.root / _stem_filename(name)
            if not path.is_file():
                _log.warning(
                    "  Resume: checkpoint is missing '%s' — starting over", name
                )
                return None
            audio, _ = sf.read(str(path), dtype="float32", always_2d=True)
            loaded[name] = audio
        return int(state.get("stages_completed", 0)), loaded, on_disk

    def checkpoint(
        self,
        stages_completed: int,
        loaded: dict[str, np.ndarray],
        on_disk: dict[str, str],
    ) -> None:
        """Persist the stems held at a stage boundary. Never raises."""
        if stages_completed <= 0:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for name, audio in loaded.items():
                sf.write(
                    str(self.root / _stem_filename(name)),
                    audio,
                    self.sample_rate,
                    subtype="FLOAT",
                )
            for name, path in on_disk.items():
                target = self.root / _stem_filename(name)
                if os.path.abspath(path) != os.path.abspath(target):
                    shutil.copyfile(path, target)
            (self.root / _STATE_FILE).write_text(
                json.dumps(
                    {
                        "schema": _SCHEMA,
                        "key": self.key,
                        "stages_completed": stages_completed,
                        "loaded": sorted(loaded),
                        "disk": sorted(on_disk),
                    }
                )
            )
        except OSError as exc:
            _log.warning("  Resume: could not write the checkpoint: %s", exc)
            return
        _log.info(
            "  Resume: checkpointed %d completed stage(s) to %s — "
            "re-run to continue from there",
            stages_completed,
            self.root,
        )

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
