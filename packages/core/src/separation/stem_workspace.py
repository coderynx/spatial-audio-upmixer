"""Private intermediate-stem ownership for one separation-plan execution."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf

from upmixer.separation.remask import share_parent_residual
from upmixer.separation.stem_resume import ResumeStore


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_float_atomic(path: str, audio: np.ndarray, sample_rate: int) -> None:
    handle = tempfile.NamedTemporaryFile(
        suffix=".wav", prefix="upmixer_cleanup_", dir=os.path.dirname(path), delete=False,
    )
    temporary = handle.name
    handle.close()
    try:
        sf.write(temporary, audio, sample_rate, subtype="FLOAT")
        os.replace(temporary, path)
    finally:
        _discard(temporary)


def _remask_stage(
    loaded: dict[str, np.ndarray], on_disk: dict[str, str], parent_path: str,
    names: frozenset[str],
) -> None:
    children = {name: audio for name, audio in loaded.items() if name in names}
    for name in names & on_disk.keys():
        children[name], _ = sf.read(on_disk[name], dtype="float32", always_2d=True)
    if not children:
        return
    parent, sample_rate = sf.read(parent_path, dtype="float32", always_2d=True)
    for name, audio in share_parent_residual(parent, children, sample_rate).items():
        if name in on_disk:
            _write_float_atomic(on_disk[name], audio, sample_rate)
        else:
            loaded[name] = audio


def _cleanup_deux_stage(
    loaded: dict[str, np.ndarray], on_disk: dict[str, str], parent: np.ndarray,
    sample_rate: int,
) -> None:
    from upmixer.separation.stem_cleanup import apply_stem_cleanup

    children: dict[str, np.ndarray] = {}
    for name in ("Vocals", "_deux_inst"):
        if name in loaded:
            children[name] = loaded[name]
        elif name in on_disk:
            children[name], _ = sf.read(on_disk[name], dtype="float32", always_2d=True)
        else:
            raise RuntimeError(f"DSP stem cleanup requires the {name} estimate")
    vocals, instrumental = apply_stem_cleanup(
        parent, children["Vocals"], children["_deux_inst"], sample_rate
    )
    for name, audio in (("Vocals", vocals), ("_deux_inst", instrumental)):
        if name in on_disk:
            _write_float_atomic(on_disk[name], audio, sample_rate)
        else:
            loaded[name] = audio


@dataclass
class StemWorkspace:
    """Own intermediate stems, their checkpoints, and their cleanup."""

    later_inputs: frozenset[str]
    resume: ResumeStore | None
    loaded: dict[str, np.ndarray] = field(default_factory=dict)
    on_disk: dict[str, str] = field(default_factory=dict)
    completed: int = 0

    @classmethod
    def open(cls, plan, cache_dir: str | None, resume_key: str | None, sample_rate: int) -> "StemWorkspace":
        workspace = cls(
            frozenset(task.input_source for task in plan.tasks if task.input_source != "original"),
            ResumeStore.open(cache_dir, resume_key, sample_rate),
        )
        if workspace.resume is not None and (restored := workspace.resume.restore()) is not None:
            workspace.completed, workspace.loaded, workspace.on_disk = restored
        return workspace

    def input_path(self, source: str, original_path: str, stage_number: int) -> str:
        if source == "original":
            return original_path
        if source not in self.on_disk:
            available = sorted(self.on_disk) or ["(none)"]
            raise RuntimeError(
                f"Stage {stage_number} needs intermediate stem '{source}' on disk, but it was not produced by any previous stage.\n"
                f"Available on-disk stems: {available}"
            )
        return self.on_disk[source]

    def keep(self, outputs: frozenset[str]) -> frozenset[str]:
        return outputs & self.later_inputs

    def stabilize(self, paths: dict[str, str], temporary_path) -> None:
        for name, path in list(paths.items()):
            stable_path = temporary_path("upmixer_intermediate_")
            try:
                os.replace(path, stable_path)
            except OSError:
                _discard(stable_path)
                raise
            paths[name] = stable_path

    def remask(self, loaded: dict[str, np.ndarray], on_disk: dict[str, str], parent: str, names: frozenset[str]) -> None:
        _remask_stage(loaded, on_disk, parent, names)

    def cleanup_deux(self, loaded: dict[str, np.ndarray], on_disk: dict[str, str], parent: np.ndarray, sample_rate: int) -> None:
        _cleanup_deux_stage(loaded, on_disk, parent, sample_rate)

    def commit(self, loaded: dict[str, np.ndarray], on_disk: dict[str, str]) -> None:
        self.loaded.update({name: audio for name, audio in loaded.items() if not name.startswith("_")})
        for name in loaded.keys() | on_disk.keys():
            superseded = self.on_disk.pop(name, None)
            if superseded is not None and superseded != on_disk.get(name):
                _discard(superseded)
        self.on_disk.update(on_disk)

    def checkpoint(self, stage_idx: int) -> None:
        if self.resume is not None:
            self.resume.checkpoint(stage_idx, self.loaded, self.on_disk)

    def fail(self) -> None:
        checkpoint_root = os.path.abspath(str(self.resume.root)) if self.resume else None
        for path in self.on_disk.values():
            if checkpoint_root is None or not os.path.abspath(path).startswith(checkpoint_root + os.sep):
                _discard(path)

    def finish(self) -> dict[str, np.ndarray]:
        for name, path in self.on_disk.items():
            if not name.startswith("_") and name not in self.loaded:
                audio, _ = sf.read(path, dtype="float32", always_2d=True)
                self.loaded[name] = audio if audio.shape[1] > 1 else np.concatenate([audio, audio], axis=1)
        for path in self.on_disk.values():
            _discard(path)
        if self.resume is not None:
            self.resume.clear()
        return self.loaded
