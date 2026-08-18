"""Tests for the wet/dry vocal split (mixing phase 12).

Covers the plan resolver's dereverb staging, the residual convention that
makes dry + wet null against the parent stem, the wet stem's routing and
placement rows, and the feature-off regression anchor.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_identity import stem_cache_identity
from upmixer.separation.stem_pipeline_exec import execute_plan
from upmixer.separation.stem_placement import STEM_ROUTING_PRESETS
from upmixer.separation.stem_plan import (
    DEFAULT_STEMS,
    MODEL_DEREVERB,
    MODEL_KARAOKE,
    MODEL_WET_DENOISE,
    WET_VOCAL_STEM,
    normalize_stems,
    resolve_separation_plan,
)
from upmixer.separation.stem_router import (
    DEFAULT_ROUTING,
    ZONE_ROUTING,
    stem_reaches_surround_height,
)

SR = 48_000


class TestPlanResolution:
    def test_split_off_leaves_plan_untouched(self):
        off = resolve_separation_plan(DEFAULT_STEMS)
        assert all(task.model != MODEL_DEREVERB for task in off.tasks)
        assert WET_VOCAL_STEM not in off.requested_stems

    def test_split_appends_dereverb_stage_on_vocals(self):
        plan = resolve_separation_plan(DEFAULT_STEMS, wet_dry_split=True)
        task = plan.tasks[-1]
        assert task.model == MODEL_DEREVERB
        assert task.input_source == "Vocals"
        assert task.output_stems == frozenset({"Vocals", WET_VOCAL_STEM})
        assert task.stem_overrides == {
            "noreverb": "Vocals", "reverb": WET_VOCAL_STEM,
        }
        assert WET_VOCAL_STEM in plan.requested_stems

    def test_split_targets_lead_vocals_when_karaoke_runs(self):
        plan = resolve_separation_plan(
            ["Lead Vocals", "Backing Vocals"], wet_dry_split=True
        )
        karaoke = [t for t in plan.tasks if t.model == MODEL_KARAOKE]
        dereverb = plan.tasks[-1]
        assert karaoke, "karaoke stage must still run"
        assert dereverb.model == MODEL_DEREVERB
        assert dereverb.input_source == "Lead Vocals"
        assert plan.tasks.index(karaoke[0]) < len(plan.tasks) - 1

    def test_requesting_the_wet_stem_enables_the_split(self):
        canonical = normalize_stems(["vocals-reverb"])
        assert canonical == [WET_VOCAL_STEM]
        plan = resolve_separation_plan(canonical)
        models = [task.model for task in plan.tasks]
        assert MODEL_DEREVERB in models
        # The parent must still be produced, even though it was not requested.
        assert plan.tasks[0].input_source == "original"

    def test_wet_denoise_runs_on_the_wet_stem_only(self):
        plan = resolve_separation_plan(
            ["Vocals"], wet_dry_split=True, wet_denoise=True
        )
        task = plan.tasks[-1]
        assert task.model == MODEL_WET_DENOISE
        assert task.input_source == WET_VOCAL_STEM
        assert task.keep_stems == frozenset({WET_VOCAL_STEM})

    def test_wet_denoise_without_the_split_is_inert(self):
        plan = resolve_separation_plan(["Vocals"], wet_denoise=True)
        assert all(task.model != MODEL_WET_DENOISE for task in plan.tasks)

    def test_combined_vocal_split_warns_about_backing_vocals(self, caplog):
        from upmixer.separation.stem_pipeline_separate import warn_combined_vocal_split

        with caplog.at_level("WARNING", logger="upmixer"):
            warn_combined_vocal_split(
                resolve_separation_plan(["Vocals"], wet_dry_split=True)
            )
            assert "backing vocals" in caplog.text
            caplog.clear()
            warn_combined_vocal_split(
                resolve_separation_plan(["Lead Vocals"], wet_dry_split=True)
            )
            assert caplog.text == ""

    def test_cache_identity_separates_split_from_plain_runs(self):
        cfg = UpmixConfig()
        plain = resolve_separation_plan(["Vocals"])
        split = resolve_separation_plan(["Vocals"], wet_dry_split=True)
        denoised = resolve_separation_plan(
            ["Vocals"], wet_dry_split=True, wet_denoise=True
        )
        identities = {
            stem_cache_identity(plan, cfg) for plan in (plain, split, denoised)
        }
        assert len(identities) == 3


class TestWetStemRouting:
    def test_wet_stem_has_a_placement_in_every_preset(self):
        for preset, placements in STEM_ROUTING_PRESETS.items():
            placement = placements[WET_VOCAL_STEM]
            assert placement.azimuth_deg == 180.0, preset
            assert placement.elevation_deg > 0.0, preset
            assert placement.lfe == 0.0, preset

    def test_wet_stem_reaches_surround_and_height_in_every_zone(self):
        fmt = FORMAT_MAP["7.1.4"]
        keys = [WET_VOCAL_STEM] + [
            f"{WET_VOCAL_STEM}@{zone}" for zone in ZONE_ROUTING
        ]
        for key in keys:
            assert stem_reaches_surround_height(key, fmt) == (True, True), key

    def test_wet_stem_never_sends_to_lfe(self):
        routes = [DEFAULT_ROUTING[WET_VOCAL_STEM]] + [
            zone[WET_VOCAL_STEM] for zone in ZONE_ROUTING.values()
        ]
        assert all("LFE" not in route for route in routes)


def _fake_dereverb_separator(tmp_path: Path, parent: np.ndarray):
    """Separator stub whose wet output is the residual of its own input."""

    class FakeSeparator:
        backend = "cpu"

        def __init__(self, model, **_):
            self.model = model
            self.directory = tmp_path / model
            self.directory.mkdir(exist_ok=True)

        def separate_to_file(self, audio_path, keep_on_disk, stem_overrides=None, wanted=None):
            if self.model == "parent.ckpt":
                path = self.directory / "Vocals.wav"
                sf.write(path, parent, SR, subtype="FLOAT")
                return {}, {"Vocals": str(path)}
            source, _ = sf.read(audio_path, dtype="float32", always_2d=True)
            dry = (source * 0.7).astype(np.float32)
            return {"Vocals": dry, WET_VOCAL_STEM: source - dry}, {}

        def close(self):
            pass

    created: dict[str, FakeSeparator] = {}

    def get_separator(model: str, _sr: int) -> FakeSeparator:
        return created.setdefault(model, FakeSeparator(model))

    return get_separator


def test_dry_and_wet_null_against_the_parent_stem(tmp_path):
    from upmixer.separation.stem_plan import SeparationPlan, SeparationTask

    rng = np.random.default_rng(0)
    parent = rng.standard_normal((4096, 2)).astype(np.float32) * 0.3
    plan = SeparationPlan(
        tasks=[
            SeparationTask("parent.ckpt", "original", frozenset({"Vocals"}), frozenset()),
            SeparationTask(
                "dereverb.ckpt", "Vocals",
                frozenset({"Vocals", WET_VOCAL_STEM}),
                frozenset({"Vocals", WET_VOCAL_STEM}),
                stem_overrides={"noreverb": "Vocals", "reverb": WET_VOCAL_STEM},
            ),
        ],
        requested_stems=frozenset({"Vocals", WET_VOCAL_STEM}),
        stems_hash="test",
    )
    source = tmp_path / "source.wav"
    sf.write(source, parent, SR, subtype="FLOAT")

    stems = execute_plan(
        _fake_dereverb_separator(tmp_path, parent), plan, str(source), SR,
    )

    assert set(stems) == {"Vocals", WET_VOCAL_STEM}
    assert np.allclose(stems["Vocals"] + stems[WET_VOCAL_STEM], parent, atol=1e-6)
    # The pre-split parent copy must not survive as a stale intermediate.
    assert not list((tmp_path / "parent.ckpt").glob("*.wav"))


def test_bundled_dereverb_config_splits_into_a_dry_stem_and_its_residual():
    torch = pytest.importorskip("torch")
    from upmixer.separation.inference.config import load_model_config
    from upmixer.separation.inference.demix import demix_roformer
    from upmixer.separation.inference.registry import get_model_spec

    config = load_model_config(get_model_spec(MODEL_DEREVERB).config_name)
    assert config.target_instrument == "noreverb"

    class HalfDry(torch.nn.Module):
        def forward(self, batch: torch.Tensor) -> torch.Tensor:
            return batch * 0.5

    rng = np.random.default_rng(1)
    mix = (rng.standard_normal((2, 8192)) * 0.2).astype(np.float32)
    sources = demix_roformer(
        HalfDry(), mix, config, torch.device("cpu"), segment_size=8, overlap=1,
    )

    assert set(sources) == {"noreverb", "reverb"}
    assert np.allclose(sources["noreverb"] + sources["reverb"], mix, atol=1e-6)
