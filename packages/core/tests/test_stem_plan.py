"""Tests for stem_plan: vocabulary, resolver, and cache key contract."""
from __future__ import annotations

import hashlib

import pytest

from upmixer.separation.stem_plan import (
    DEFAULT_STEMS,
    DEUX_OUTPUT_STEMS,
    MODEL_CROWD,
    MODEL_DEUX,
    MODEL_DRUMS,
    MODEL_KARAOKE,
    MODEL_PRIMARY,
    PRIMARY_INSTRUMENTAL_STEMS,
    PRIMARY_OUTPUT_STEMS,
    SeparationPlan,
    normalize_stems,
    resolve_separation_plan,
)


class TestNormalizeStems:
    def test_lowercase_to_canonical(self):
        assert normalize_stems(["vocals"]) == ["Vocals"]

    def test_canonical_passthrough(self):
        assert normalize_stems(["Vocals"]) == ["Vocals"]

    def test_mixed_case_normalized(self):
        result = normalize_stems(["vocals", "Kick"])
        assert result == ["Vocals", "Kick"]

    def test_all_manifest_names(self):
        manifest_names = [
            "vocals", "bass", "drums", "guitar", "piano", "other",
            "kick", "snare", "toms", "hi-hat", "ride", "crash", "crowd",
            "lead-vocals", "backing-vocals",
        ]
        canonical = normalize_stems(manifest_names)
        assert len(canonical) == 15
        assert "Vocals" in canonical
        assert "Toms" in canonical
        assert "Hi-Hat" in canonical
        assert "Crowd" in canonical
        assert "Lead Vocals" in canonical
        assert "Backing Vocals" in canonical

    def test_deduplication_preserves_order(self):
        result = normalize_stems(["vocals", "vocals", "bass"])
        assert result == ["Vocals", "Bass"]

    def test_empty_list_returns_empty(self):
        assert normalize_stems([]) == []

    def test_unknown_stem_raises(self):
        with pytest.raises(ValueError, match="Unknown stem name 'trumpet'"):
            normalize_stems(["trumpet"])

    def test_unknown_stem_in_mixed_list_raises(self):
        with pytest.raises(ValueError):
            normalize_stems(["vocals", "theremin"])


class TestResolveSeparationPlan:
    def test_bass_subset_shares_inference_cache_identity_with_full(self):
        """A subset that still needs the primary stage shares its cascade shape."""
        bass = resolve_separation_plan(["Bass"])
        full = resolve_separation_plan(DEFAULT_STEMS)
        assert bass.stems_hash != full.stems_hash
        assert bass.inference_hash == full.inference_hash

    def test_vocals_only_skips_primary_stage(self):
        """Vocals-only comes entirely from deux; the primary stage is skipped."""
        vocals = resolve_separation_plan(["Vocals"])
        full = resolve_separation_plan(DEFAULT_STEMS)
        assert len(vocals.tasks) == 1
        assert vocals.tasks[0].model == MODEL_DEUX
        assert not any(t.model == MODEL_PRIMARY for t in vocals.tasks)
        assert vocals.inference_hash != full.inference_hash

    def test_extra_stage_changes_inference_cache_identity(self):
        drums = resolve_separation_plan(["Drums"])
        drum_subs = resolve_separation_plan(["Kick"])
        assert drums.inference_hash != drum_subs.inference_hash

    def test_deux_prestage_feeds_primary(self):
        """deux's instrumental residual, not the raw mix, feeds the primary model."""
        plan = resolve_separation_plan(DEFAULT_STEMS)
        assert len(plan.tasks) == 2
        deux_task, primary_task = plan.tasks
        assert deux_task.model == MODEL_DEUX
        assert deux_task.output_stems == DEUX_OUTPUT_STEMS
        assert primary_task.model == MODEL_PRIMARY
        assert primary_task.input_source == "_deux_inst"
        assert "Vocals" not in primary_task.keep_stems

    def test_primary_output_stems_excludes_vocals(self):
        """Regression: primary's own Vocals output must never be advertised.

        Both deux and primary produce a stem tagged "Vocals" (primary's is a
        vocals-free residual, since its input is already deux's instrumental).
        If primary's output_stems included "Vocals" too, execute_plan's
        later_inputs/keep_on_disk logic would write both to the same
        "Vocals" key in its on-disk intermediate dict, and primary — running
        second — would silently clobber deux's real Vocals before karaoke
        ever reads it. This must hold whether or not karaoke also runs.
        """
        plan = resolve_separation_plan(DEFAULT_STEMS)
        primary_task = next(t for t in plan.tasks if t.model == MODEL_PRIMARY)
        assert primary_task.output_stems == PRIMARY_INSTRUMENTAL_STEMS
        assert "Vocals" not in primary_task.output_stems

        vocal_plan = resolve_separation_plan(["Lead Vocals", "Backing Vocals", "Bass"])
        primary_task = next(t for t in vocal_plan.tasks if t.model == MODEL_PRIMARY)
        assert "Vocals" not in primary_task.output_stems

    def test_deux_prestage_alters_inference_hash(self):
        """The deux pre-stage must change cache identity vs. the pre-cascade shape."""
        old_identity = f"{MODEL_PRIMARY}:original:{','.join(sorted(PRIMARY_OUTPUT_STEMS))}:"
        old_hash = hashlib.sha256(old_identity.encode()).hexdigest()[:20]
        plan = resolve_separation_plan(DEFAULT_STEMS)
        assert plan.inference_hash != old_hash

    def test_default_6stem_two_tasks(self):
        """deux supplies Vocals; primary supplies the rest, fed by deux's residual."""
        plan = resolve_separation_plan(DEFAULT_STEMS)
        assert len(plan.tasks) == 2
        deux_task, primary_task = plan.tasks
        assert deux_task.model == MODEL_DEUX
        assert deux_task.input_source == "original"
        assert deux_task.keep_stems == frozenset({"Vocals"})
        assert primary_task.model == MODEL_PRIMARY
        assert primary_task.input_source == "_deux_inst"
        assert primary_task.keep_stems == frozenset(
            {"Bass", "Drums", "Guitar", "Piano", "Other"}
        )

    def test_empty_input_uses_defaults(self):
        plan = resolve_separation_plan([])
        assert len(plan.tasks) == 2
        assert plan.tasks[0].model == MODEL_DEUX
        assert plan.tasks[1].model == MODEL_PRIMARY
        assert plan.requested_stems == frozenset(DEFAULT_STEMS)

    def test_crowd_plus_drum_subs_four_stages(self):
        """crowd → deux → primary → drumsep."""
        canonical = normalize_stems(["vocals", "crowd", "kick", "snare"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 4

        stage0, stage1, stage2, stage3 = plan.tasks
        assert stage0.model == MODEL_CROWD
        assert stage0.input_source == "original"
        assert "Crowd" in stage0.keep_stems

        assert stage1.model == MODEL_DEUX
        assert stage1.input_source == "_crowd_other"
        assert "Vocals" in stage1.keep_stems
        assert "Crowd" not in stage1.keep_stems  # Crowd came from Stage 0

        assert stage2.model == MODEL_PRIMARY
        assert stage2.input_source == "_deux_inst"

        assert stage3.model == MODEL_DRUMS
        assert stage3.input_source == "Drums"
        assert stage3.keep_stems == frozenset({"Kick", "Snare"})

    def test_drum_subs_only_three_stages(self):
        """No crowd → deux → primary → drumsep."""
        canonical = normalize_stems(["kick", "hi-hat"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 3

        deux_task, primary_task, drumsep_task = plan.tasks
        assert deux_task.model == MODEL_DEUX
        assert deux_task.input_source == "original"
        assert primary_task.model == MODEL_PRIMARY
        assert primary_task.input_source == "_deux_inst"
        # "Drums" not in requested_stems — kept only as intermediate
        assert "Drums" not in plan.requested_stems

        assert drumsep_task.model == MODEL_DRUMS
        assert drumsep_task.input_source == "Drums"
        assert drumsep_task.keep_stems == frozenset({"Kick", "Hi-Hat"})

    def test_drum_sub_stems_replace_parent_in_final_mix(self):
        """Parent Drums must not be mixed with its derived sub-stems."""
        canonical = normalize_stems(["drums", "kick"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 3

        *_, drumsep_task = plan.tasks
        assert "Drums" not in plan.requested_stems
        assert drumsep_task.keep_stems == frozenset({"Kick"})

    def test_backing_vocals_only_two_stages(self):
        """backing-vocals alone runs deux then karaoke — primary is skipped."""
        canonical = normalize_stems(["backing-vocals"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 2

        stage1, stage2 = plan.tasks
        assert stage1.model == MODEL_DEUX
        assert stage1.input_source == "original"

        assert stage2.model == MODEL_KARAOKE
        assert stage2.input_source == "Vocals"
        assert stage2.keep_stems == frozenset({"Backing Vocals"})
        assert "Vocals" not in plan.requested_stems

    def test_lead_vocals_only_two_stages(self):
        canonical = normalize_stems(["lead-vocals"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 2

        karaoke = plan.tasks[-1]
        assert karaoke.model == MODEL_KARAOKE
        assert karaoke.input_source == "Vocals"
        assert karaoke.keep_stems == frozenset({"Lead Vocals"})
        assert karaoke.output_stems == frozenset({"Lead Vocals", "Backing Vocals"})
        assert plan.requested_stems == frozenset({"Lead Vocals"})

    def test_vocal_sub_stems_replace_parent_in_final_mix(self):
        canonical = normalize_stems([
            "vocals", "lead-vocals", "backing-vocals",
        ])
        plan = resolve_separation_plan(canonical)

        karaoke = plan.tasks[-1]
        assert karaoke.keep_stems == frozenset({"Lead Vocals", "Backing Vocals"})
        assert "Vocals" not in plan.requested_stems
        assert plan.requested_stems == frozenset({"Lead Vocals", "Backing Vocals"})

    def test_crowd_plus_backing_vocals_three_stages(self):
        """crowd → deux (on residual) → karaoke; primary is skipped."""
        canonical = normalize_stems(["crowd", "backing-vocals"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 3

        stage0, stage1, stage2 = plan.tasks
        assert stage0.model == MODEL_CROWD
        assert stage1.model == MODEL_DEUX
        assert stage1.input_source == "_crowd_other"
        assert stage2.model == MODEL_KARAOKE
        assert stage2.input_source == "Vocals"

    def test_lead_and_backing_vocals_share_inference_identity(self):
        lead = resolve_separation_plan(["Lead Vocals"])
        backing = resolve_separation_plan(["Backing Vocals"])
        paired = resolve_separation_plan(["Lead Vocals", "Backing Vocals"])
        assert lead.inference_hash == backing.inference_hash == paired.inference_hash

    def test_crowd_only_single_stage(self):
        """Only Crowd requested → Stage 0 only; no primary or drumsep."""
        plan = resolve_separation_plan(["Crowd"])
        assert len(plan.tasks) == 1
        assert plan.tasks[0].model == MODEL_CROWD
        assert plan.tasks[0].keep_stems == frozenset({"Crowd"})

    def test_stems_hash_is_20_chars(self):
        plan = resolve_separation_plan(["Vocals", "Bass"])
        assert len(plan.stems_hash) == 20

    def test_different_stem_sets_different_hash(self):
        plan_a = resolve_separation_plan(["Vocals", "Bass"])
        plan_b = resolve_separation_plan(["Vocals", "Drums"])
        assert plan_a.stems_hash != plan_b.stems_hash

    def test_same_stem_sets_same_hash(self):
        plan_a = resolve_separation_plan(["Bass", "Vocals"])
        plan_b = resolve_separation_plan(["Vocals", "Bass"])
        assert plan_a.stems_hash == plan_b.stems_hash  # order-independent

    def test_intermediate_drums_not_in_requested(self):
        """When only drum sub-stems are requested, Drums itself is NOT
        in requested_stems (it is an intermediate only)."""
        plan = resolve_separation_plan(["Kick", "Snare", "Hi-Hat"])
        assert "Drums" not in plan.requested_stems
        assert frozenset({"Kick", "Snare", "Hi-Hat"}) <= plan.requested_stems

    def test_plan_is_separation_plan_instance(self):
        plan = resolve_separation_plan(["Vocals"])
        assert isinstance(plan, SeparationPlan)

    def test_deux_stage_input_uses_crowd_other_when_crowd_present(self):
        """deux must read from _crowd_other, not original, when crowd was requested."""
        plan = resolve_separation_plan(["Vocals", "Crowd"])
        deux_task = next(t for t in plan.tasks if t.model == MODEL_DEUX)
        assert deux_task.input_source == "_crowd_other"

    def test_primary_stage_input_uses_deux_instrumental_when_crowd_present(self):
        """primary must read deux's residual even when crowd also ran."""
        plan = resolve_separation_plan(["Bass", "Crowd"])
        primary_task = next(t for t in plan.tasks if t.model == MODEL_PRIMARY)
        assert primary_task.input_source == "_deux_inst"

    def test_first_stage_reads_original_without_crowd(self):
        plan = resolve_separation_plan(["Vocals", "Bass"])
        assert plan.tasks[0].input_source == "original"


class TestStemCacheKeyContract:
    """Verify that the cache key function accepts stems_hash instead of model."""
    def _make_dummy_file(self, tmp_path):
        """Write a tiny dummy WAV so getmtime() works."""
        import numpy as np
        import soundfile as sf
        p = tmp_path / "dummy.wav"
        sf.write(str(p), np.zeros((100, 2), dtype="float32"), 44100, subtype="PCM_16")
        return str(p)

    def test_same_hash_produces_same_key(self, tmp_path):
        from upmixer.separation.stem_cache import _cache_key
        path = self._make_dummy_file(tmp_path)
        key1 = _cache_key(path, "abc12345", 44100)
        key2 = _cache_key(path, "abc12345", 44100)
        assert key1 == key2

    def test_different_hash_produces_different_key(self, tmp_path):
        from upmixer.separation.stem_cache import _cache_key
        path = self._make_dummy_file(tmp_path)
        key1 = _cache_key(path, "abc12345", 44100)
        key2 = _cache_key(path, "xyz98765", 44100)
        assert key1 != key2

    def test_key_is_20_chars(self, tmp_path):
        from upmixer.separation.stem_cache import _cache_key
        path = self._make_dummy_file(tmp_path)
        key = _cache_key(path, "somehash", 48000)
        assert len(key) == 20


def test_stem_cache_identity_changes_for_inference_overrides():
    from upmixer.config import UpmixConfig
    from upmixer.separation.stem_identity import stem_cache_identity

    plan = resolve_separation_plan(["Vocals", "Bass"])
    default_identity = stem_cache_identity(plan, UpmixConfig())
    tuned_identity = stem_cache_identity(
        plan,
        UpmixConfig(
            stem_batch_size=1,
            stem_segment_size=128,
            stem_chunk_duration_s=300.0,
        ),
    )
    overlap_identity = stem_cache_identity(
        plan, UpmixConfig(stem_overlap=8)
    )
    tta_identity = stem_cache_identity(
        plan, UpmixConfig(stem_tta=True)
    )
    pitch_identity = stem_cache_identity(
        plan, UpmixConfig(stem_pitch_shift=0.75)
    )

    assert stem_cache_identity(
        plan, UpmixConfig(stem_primary_remask=False)
    ) == plan.inference_hash
    assert tuned_identity != default_identity
    assert overlap_identity != default_identity
    assert tta_identity != default_identity
    assert pitch_identity != default_identity
    assert len({overlap_identity, tta_identity, pitch_identity, tuned_identity}) == 4


def test_stem_cache_identity_changes_for_dsp_stem_cleanup():
    from upmixer.config import UpmixConfig
    from upmixer.separation.stem_identity import stem_cache_identity

    deux_plan = resolve_separation_plan(["Vocals"])
    raw = stem_cache_identity(deux_plan, UpmixConfig())
    cleaned = stem_cache_identity(
        deux_plan, UpmixConfig(stem_bleed_reduction=True)
    )
    crowd_plan = resolve_separation_plan(["Crowd"])

    assert raw == deux_plan.inference_hash
    assert cleaned != raw
    assert stem_cache_identity(
        crowd_plan, UpmixConfig(stem_bleed_reduction=True)
    ) == crowd_plan.inference_hash


def test_stem_cache_identity_changes_for_remask():
    from upmixer.config import UpmixConfig
    from upmixer.separation.stem_identity import stem_cache_identity

    drum_plan = resolve_separation_plan(["Kick", "Snare"])
    bass_plan = resolve_separation_plan(["Bass"])
    vocals_plan = resolve_separation_plan(["Vocals"])

    both_off = UpmixConfig(stem_drum_remask=False, stem_primary_remask=False)
    assert stem_cache_identity(drum_plan, both_off) == drum_plan.inference_hash
    assert len(
        {
            stem_cache_identity(drum_plan, UpmixConfig()),
            stem_cache_identity(drum_plan, UpmixConfig(stem_drum_remask=False)),
            stem_cache_identity(drum_plan, UpmixConfig(stem_primary_remask=False)),
            stem_cache_identity(drum_plan, both_off),
        }
    ) == 4
    # Each pass only counts for plans that run its own model stage.
    assert stem_cache_identity(
        bass_plan, UpmixConfig(stem_drum_remask=False)
    ) == stem_cache_identity(bass_plan, UpmixConfig())
    assert stem_cache_identity(vocals_plan, UpmixConfig()) == vocals_plan.inference_hash


def _fake_multi_model_separator(tmp_path):
    """Separator stub emitting exactly what each real checkpoint emits.

    The point is primary: its config lists ``vocals`` among its instruments,
    so it writes a Vocals file even when fed a vocals-free residual.
    """
    import numpy as np
    import soundfile as sf

    emits = {
        MODEL_DEUX: {"Vocals": 1.0, "_deux_inst": 2.0},
        MODEL_PRIMARY: {
            "Bass": 3.0, "Drums": 4.0, "Guitar": 5.0,
            "Piano": 6.0, "Other": 7.0, "Vocals": 99.0,
        },
        "c.ckpt": {"Vocals": 8.0},
    }

    class FakeSeparator:
        backend = "cpu"

        def __init__(self, model):
            self.model = model
            self.directory = tmp_path / model.replace("/", "_")
            self.directory.mkdir(exist_ok=True)

        def separate_to_file(
            self, audio_path, keep_on_disk, stem_overrides=None, wanted=None
        ):
            loaded, on_disk = {}, {}
            for name, value in emits[self.model].items():
                if wanted is not None and name not in wanted:
                    continue
                audio = np.full((256, 2), value, dtype=np.float32)
                if name in keep_on_disk:
                    path = self.directory / f"{name}.wav"
                    sf.write(path, audio, 48_000, subtype="FLOAT")
                    on_disk[name] = str(path)
                else:
                    loaded[name] = audio
            return loaded, on_disk

        def close(self):
            pass

    created: dict[str, FakeSeparator] = {}
    return lambda model, _sr: created.setdefault(model, FakeSeparator(model))


def test_primary_vocals_leftover_never_replaces_the_real_vocals(tmp_path):
    """Regression: the primary model emits its own vocals-free Vocals file.

    Excluding it from the task's ``output_stems`` is not enough on its own —
    the separator names stems from the files the model wrote, so the leftover
    used to overwrite deux's Vocals in both the loaded dict and the on-disk
    intermediate map. On a plan with a later Vocals consumer that surfaced as
    "Stage N needs intermediate stem 'Vocals' on disk"; without one it
    silently swapped the vocal stem for a vocals-free residual.
    """
    import numpy as np
    import soundfile as sf

    from upmixer.separation.stem_pipeline_exec import execute_plan
    from upmixer.separation.stem_plan import SeparationTask

    plan = SeparationPlan(
        tasks=[
            SeparationTask(MODEL_DEUX, "original", DEUX_OUTPUT_STEMS,
                           frozenset({"Vocals"})),
            SeparationTask(MODEL_PRIMARY, "_deux_inst",
                           PRIMARY_INSTRUMENTAL_STEMS,
                           PRIMARY_INSTRUMENTAL_STEMS),
            SeparationTask("c.ckpt", "Vocals", frozenset({"Vocals"}),
                           frozenset({"Vocals"})),
        ],
        requested_stems=frozenset({"Vocals", "Bass"}),
        stems_hash="x",
    )
    source = tmp_path / "in.wav"
    sf.write(source, np.zeros((256, 2), dtype=np.float32), 48_000, subtype="FLOAT")

    stems = execute_plan(
        _fake_multi_model_separator(tmp_path), plan, str(source), 48_000
    )

    # The final vocal stage ran, which it cannot do if primary ate its input.
    assert stems["Vocals"][0, 0] == 8.0
    assert 99.0 not in {float(v[0, 0]) for v in stems.values()}


def test_primary_vocals_leftover_is_dropped_with_no_later_consumer(tmp_path):
    """The silent half of the same bug: no Vocals consumer, wrong audio out."""
    import numpy as np
    import soundfile as sf

    from upmixer.separation.stem_pipeline_exec import execute_plan
    from upmixer.separation.stem_plan import SeparationTask

    plan = SeparationPlan(
        tasks=[
            SeparationTask(MODEL_DEUX, "original", DEUX_OUTPUT_STEMS,
                           frozenset({"Vocals"})),
            SeparationTask(MODEL_PRIMARY, "_deux_inst",
                           PRIMARY_INSTRUMENTAL_STEMS,
                           frozenset({"Bass"})),
        ],
        requested_stems=frozenset({"Vocals", "Bass"}),
        stems_hash="x",
    )
    source = tmp_path / "in.wav"
    sf.write(source, np.zeros((256, 2), dtype=np.float32), 48_000, subtype="FLOAT")

    stems = execute_plan(
        _fake_multi_model_separator(tmp_path), plan, str(source), 48_000
    )
    assert stems["Vocals"][0, 0] == 1.0
