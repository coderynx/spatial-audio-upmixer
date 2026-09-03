"""Functional gates for the fixed primary-stem ensemble."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.separation.stem_pipeline_exec import execute_plan
from upmixer.separation.stem_plan import (
    MODEL_DEUX,
    MODEL_DRUMS,
    MODEL_PRIMARY,
    MODEL_SCNET,
    PRIMARY_INSTRUMENTAL_STEMS,
    resolve_separation_plan,
)

SR = 48_000


def _source(tmp_path: Path, length: int = 32) -> str:
    path = tmp_path / "source.wav"
    sf.write(path, np.zeros((length, 2), dtype=np.float32), SR, subtype="FLOAT")
    return str(path)


class _FakeSeparator:
    backend = "cpu"

    def __init__(self, model: str, root: Path, calls: list[tuple]) -> None:
        self.model = model
        self.root = root
        self.calls = calls
        self.root.mkdir(parents=True, exist_ok=True)

    def separate_to_file(
        self,
        audio_path: str,
        keep_on_disk: frozenset[str],
        stem_overrides: dict[str, str] | None = None,
        wanted: frozenset[str] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, str]]:
        self.calls.append((self.model, audio_path, keep_on_disk, wanted))
        if self.model == MODEL_DEUX:
            values = {"Vocals": 1.0, "_deux_inst": 2.0}
        elif self.model == MODEL_PRIMARY:
            values = {
                name: float(index)
                for index, name in enumerate(
                    sorted(PRIMARY_INSTRUMENTAL_STEMS), start=2
                )
            }
        elif self.model == MODEL_SCNET:
            values = {"Bass": 20.0, "Drums": 30.0, "Vocals": 99.0, "Other": 98.0}
        elif self.model == MODEL_DRUMS:
            values = {"Kick": 40.0, "Snare": 41.0}
        else:
            raise AssertionError(self.model)

        loaded: dict[str, np.ndarray] = {}
        on_disk: dict[str, str] = {}
        for name, value in values.items():
            if wanted is not None and name not in wanted:
                continue
            audio = np.full((32, 2), value, dtype=np.float32)
            if name in keep_on_disk:
                path = self.root / f"{self.model.replace('/', '_')}-{name}.wav"
                sf.write(path, audio, SR, subtype="FLOAT")
                on_disk[name] = str(path)
            else:
                loaded[name] = audio
        return loaded, on_disk

    def close(self) -> None:
        pass


def _get_fake(tmp_path: Path, calls: list[tuple], fail: str | None = None):
    created: dict[str, _FakeSeparator] = {}

    def get(model: str, _sample_rate: int) -> _FakeSeparator:
        if model == fail:
            raise RuntimeError(f"failed {model}")
        return created.setdefault(model, _FakeSeparator(model, tmp_path / model, calls))

    return get


def test_plan_ensemble_closure_only_targets_bass_and_drums():
    for stems, expected in (
        (["Bass"], frozenset({"Bass"})),
        (["Drums"], frozenset({"Drums"})),
        (["Kick"], frozenset({"Drums"})),
    ):
        plan = resolve_separation_plan(stems, stem_ensemble=True)
        primary = next(task for task in plan.tasks if task.model == MODEL_PRIMARY)
        assert primary.ensemble_models == (MODEL_SCNET,)
        assert primary.ensemble_stems == expected

    for stems in (["Guitar"], ["Vocals"]):
        plan = resolve_separation_plan(stems, stem_ensemble=True)
        assert not any(task.ensemble_models for task in plan.tasks)


def test_disabled_plan_and_cache_identity_are_unchanged():
    from upmixer.separation.stem_identity import stem_cache_identity

    plain = resolve_separation_plan(["Bass", "Drums"])
    explicit_off = resolve_separation_plan(["Bass", "Drums"], False)
    assert plain == explicit_off
    assert stem_cache_identity(plain, UpmixConfig(stem_primary_remask=False)) == (
        plain.inference_hash
    )


def test_enabled_plan_and_cache_identity_include_ensemble_details():
    from upmixer.separation.stem_identity import stem_cache_identity

    disabled = resolve_separation_plan(["Bass", "Drums"])
    enabled = resolve_separation_plan(["Bass", "Drums"], stem_ensemble=True)
    assert enabled.inference_hash != disabled.inference_hash
    assert enabled.ensemble_algorithm == "avg_wave"
    assert enabled.ensemble_models == (MODEL_SCNET,)
    assert enabled.ensemble_stems == frozenset({"Bass", "Drums"})
    assert stem_cache_identity(
        enabled, UpmixConfig(stem_ensemble=True, stem_primary_remask=False)
    ) != stem_cache_identity(
        disabled, UpmixConfig(stem_primary_remask=False)
    )


def test_average_preserves_sw_non_ensemble_stems_and_ignores_partner_extras(tmp_path):
    calls: list[tuple] = []
    plan = resolve_separation_plan(["Bass", "Drums", "Guitar", "Piano", "Other"], True)
    stems = execute_plan(
        _get_fake(tmp_path, calls),
        plan,
        _source(tmp_path),
        SR,
        cfg=UpmixConfig(stem_primary_remask=False),
    )

    assert stems["Bass"].dtype == np.float32
    assert np.all(stems["Bass"] == 11.0)
    assert np.all(stems["Drums"] == 16.5)
    assert np.all(stems["Guitar"] == 4.0)
    assert np.all(stems["Piano"] == 6.0)
    assert np.all(stems["Other"] == 5.0)
    primary_path = next(path for model, path, *_ in calls if model == MODEL_PRIMARY)
    partner_path = next(path for model, path, *_ in calls if model == MODEL_SCNET)
    assert primary_path == partner_path


@pytest.mark.parametrize("bad", ["shape", "finite"])
def test_invalid_partner_output_fails_before_stage_commit(tmp_path, bad, monkeypatch):
    calls: list[tuple] = []
    plan = resolve_separation_plan(["Bass"], True)
    get = _get_fake(tmp_path, calls)
    original = _FakeSeparator.separate_to_file

    def malformed(self, audio_path, keep_on_disk, stem_overrides=None, wanted=None):
        loaded, on_disk = original(
            self, audio_path, keep_on_disk, stem_overrides, wanted
        )
        if self.model == MODEL_SCNET:
            if bad == "shape":
                loaded["Bass"] = loaded["Bass"][:16]
            else:
                loaded["Bass"][0, 0] = np.nan
        return loaded, on_disk

    monkeypatch.setattr(_FakeSeparator, "separate_to_file", malformed)
    with pytest.raises(ValueError):
        execute_plan(
            get, plan, _source(tmp_path), SR,
            cfg=UpmixConfig(stem_primary_remask=False),
        )


def test_ensembled_drums_are_materialized_for_drumsep(tmp_path, monkeypatch):
    calls: list[tuple] = []
    drumsep_inputs: list[np.ndarray] = []
    plan = resolve_separation_plan(["Kick"], True)
    get = _get_fake(tmp_path, calls)
    original = _FakeSeparator.separate_to_file

    def inspect_drumsep(self, audio_path, keep_on_disk, stem_overrides=None, wanted=None):
        if self.model == MODEL_DRUMS:
            drumsep_inputs.append(sf.read(audio_path, dtype="float32", always_2d=True)[0])
        return original(self, audio_path, keep_on_disk, stem_overrides, wanted)

    monkeypatch.setattr(_FakeSeparator, "separate_to_file", inspect_drumsep)
    stems = execute_plan(
        get,
        plan,
        _source(tmp_path),
        SR,
        cfg=UpmixConfig(stem_primary_remask=False, stem_drum_remask=False),
    )

    assert np.all(stems["Kick"] == 40.0)
    assert len(drumsep_inputs) == 1
    assert np.all(drumsep_inputs[0] == 16.5)


def test_primary_remask_runs_once_after_fusion(tmp_path, monkeypatch):
    calls: list[tuple] = []
    observed: list[np.ndarray] = []

    def remask(parent, children, _sample_rate):
        observed.append(children["Bass"].copy())
        return children

    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline_exec.share_parent_residual", remask
    )
    plan = resolve_separation_plan(["Bass"], True)
    stems = execute_plan(
        _get_fake(tmp_path, calls), plan, _source(tmp_path), SR,
        cfg=UpmixConfig(),
    )
    assert len(observed) == 1
    assert np.all(observed[0] == 11.0)
    assert np.all(stems["Bass"] == 11.0)


def test_partner_failure_does_not_leave_intermediates_or_final_stems(tmp_path):
    calls: list[tuple] = []
    plan = resolve_separation_plan(["Kick"], True)
    with pytest.raises(RuntimeError):
        execute_plan(
            _get_fake(tmp_path, calls, fail=MODEL_SCNET),
            plan,
            _source(tmp_path),
            SR,
            cfg=UpmixConfig(stem_primary_remask=False),
        )
    primary_files = list((tmp_path / MODEL_PRIMARY).glob("*.wav"))
    assert not primary_files
