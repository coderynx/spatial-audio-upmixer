"""Tests for the mid-plan crash checkpoint (stem_resume)."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from upmixer.separation.stem_pipeline_exec import execute_plan
from upmixer.separation.stem_plan import SeparationPlan, SeparationTask
from upmixer.separation.stem_resume import ResumeStore

SR = 48_000


class _Boom(RuntimeError):
    pass


def _plan():
    return SeparationPlan(
        tasks=[
            SeparationTask("a.ckpt", "original",
                           frozenset({"Vocals", "_inst"}), frozenset({"Vocals"})),
            SeparationTask("b.ckpt", "_inst",
                           frozenset({"Bass", "Drums"}), frozenset({"Bass", "Drums"})),
            SeparationTask("c.ckpt", "Vocals",
                           frozenset({"Vocals", "Vocals Reverb"}),
                           frozenset({"Vocals", "Vocals Reverb"})),
        ],
        requested_stems=frozenset({"Vocals", "Vocals Reverb", "Bass", "Drums"}),
        stems_hash="x",
    )


def _separator(tmp_path, runs, fail_on=None):
    """Fake separator recording every model it is asked to run."""
    emits = {
        "a.ckpt": {"Vocals": 1.0, "_inst": 2.0},
        "b.ckpt": {"Bass": 3.0, "Drums": 4.0},
        "c.ckpt": {"Vocals": 5.0, "Vocals Reverb": 6.0},
    }

    class Fake:
        backend = "cpu"

        def __init__(self, model):
            self.model = model
            self.directory = tmp_path / model
            self.directory.mkdir(parents=True, exist_ok=True)

        def separate_to_file(self, audio_path, keep_on_disk, stem_overrides=None,
                             wanted=None):
            runs.append(self.model)
            if self.model == fail_on:
                raise _Boom(self.model)
            loaded, on_disk = {}, {}
            for name, value in emits[self.model].items():
                audio = np.full((128, 2), value, dtype=np.float32)
                if name in keep_on_disk:
                    path = self.directory / f"{name}.wav"
                    sf.write(path, audio, SR, subtype="FLOAT")
                    on_disk[name] = str(path)
                else:
                    loaded[name] = audio
            return loaded, on_disk

        def close(self):
            pass

    created: dict[str, Fake] = {}
    return lambda model, _sr: created.setdefault(model, Fake(model))


def _source(tmp_path):
    path = tmp_path / "in.wav"
    sf.write(path, np.zeros((128, 2), dtype=np.float32), SR, subtype="FLOAT")
    return str(path)


def test_a_failed_run_resumes_at_the_stage_that_failed(tmp_path):
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(stem_cache_dir=str(tmp_path / "cache"))
    source = _source(tmp_path)
    plan = _plan()

    first: list[str] = []
    with pytest.raises(_Boom):
        execute_plan(
            _separator(tmp_path / "r1", first, fail_on="c.ckpt"),
            plan, source, SR, None, cfg, "run-key",
        )
    assert first == ["a.ckpt", "b.ckpt", "c.ckpt"]

    second: list[str] = []
    stems = execute_plan(
        _separator(tmp_path / "r2", second), plan, source, SR, None, cfg, "run-key"
    )
    # Only the stage that failed runs again.
    assert second == ["c.ckpt"]
    assert stems["Bass"][0, 0] == 3.0
    assert stems["Drums"][0, 0] == 4.0
    assert stems["Vocals"][0, 0] == 5.0
    assert stems["Vocals Reverb"][0, 0] == 6.0


def test_a_successful_run_leaves_no_checkpoint(tmp_path):
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(stem_cache_dir=str(tmp_path / "cache"))
    runs: list[str] = []
    execute_plan(
        _separator(tmp_path / "r", runs), _plan(), _source(tmp_path), SR,
        None, cfg, "run-key",
    )
    store = ResumeStore.open(cfg.stem_cache_dir, "run-key", SR)
    assert store.restore() is None


def test_a_checkpoint_is_not_replayed_into_a_different_run(tmp_path):
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(stem_cache_dir=str(tmp_path / "cache"))
    source = _source(tmp_path)
    with pytest.raises(_Boom):
        execute_plan(
            _separator(tmp_path / "r1", [], fail_on="c.ckpt"),
            _plan(), source, SR, None, cfg, "run-key",
        )
    runs: list[str] = []
    execute_plan(
        _separator(tmp_path / "r2", runs), _plan(), source, SR,
        None, cfg, "other-key",
    )
    assert runs == ["a.ckpt", "b.ckpt", "c.ckpt"]


def test_no_cache_dir_means_no_checkpoint(tmp_path):
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(stem_cache_dir=None)
    source = _source(tmp_path)
    with pytest.raises(_Boom):
        execute_plan(
            _separator(tmp_path / "r1", [], fail_on="c.ckpt"),
            _plan(), source, SR, None, cfg, "run-key",
        )
    runs: list[str] = []
    with pytest.raises(_Boom):
        execute_plan(
            _separator(tmp_path / "r2", runs, fail_on="c.ckpt"),
            _plan(), source, SR, None, cfg, "run-key",
        )
    assert runs == ["a.ckpt", "b.ckpt", "c.ckpt"]


def test_a_checkpoint_missing_its_audio_starts_over(tmp_path):
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(stem_cache_dir=str(tmp_path / "cache"))
    source = _source(tmp_path)
    with pytest.raises(_Boom):
        execute_plan(
            _separator(tmp_path / "r1", [], fail_on="c.ckpt"),
            _plan(), source, SR, None, cfg, "run-key",
        )
    store = ResumeStore.open(cfg.stem_cache_dir, "run-key", SR)
    next(store.root.glob("*.wav")).unlink()

    runs: list[str] = []
    execute_plan(
        _separator(tmp_path / "r2", runs), _plan(), source, SR,
        None, cfg, "run-key",
    )
    assert runs == ["a.ckpt", "b.ckpt", "c.ckpt"]
