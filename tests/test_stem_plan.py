"""Tests for stem_plan: vocabulary, resolver, and cache key contract."""
from __future__ import annotations

import pytest

from upmixer.separation.stem_plan import (
    DEFAULT_STEMS,
    MODEL_CROWD,
    MODEL_DRUMS,
    MODEL_PRIMARY,
    SeparationPlan,
    normalize_stems,
    resolve_separation_plan,
)


# ── normalize_stems ────────────────────────────────────────────────────────────

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
            "kick", "snare", "hi-hat", "ride", "crash", "crowd",
        ]
        canonical = normalize_stems(manifest_names)
        assert len(canonical) == 12
        assert "Vocals" in canonical
        assert "Hi-Hat" in canonical
        assert "Crowd" in canonical

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


# ── resolve_separation_plan ────────────────────────────────────────────────────

class TestResolveSeparationPlan:
    def test_default_6stem_single_task(self):
        """No drum subs, no crowd → Stage 1 only."""
        plan = resolve_separation_plan(DEFAULT_STEMS)
        assert len(plan.tasks) == 1
        task = plan.tasks[0]
        assert task.model == MODEL_PRIMARY
        assert task.input_source == "original"
        assert "Vocals" in task.keep_stems
        assert "Drums" in task.keep_stems
        assert "Bass" in task.keep_stems

    def test_empty_input_uses_defaults(self):
        plan = resolve_separation_plan([])
        assert len(plan.tasks) == 1
        assert plan.tasks[0].model == MODEL_PRIMARY
        assert plan.requested_stems == frozenset(DEFAULT_STEMS)

    def test_crowd_plus_drum_subs_three_stages(self):
        """Stage 0 (crowd) → Stage 1 (primary) → Stage 2 (drumsep)."""
        canonical = normalize_stems(["vocals", "crowd", "kick", "snare"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 3

        stage0, stage1, stage2 = plan.tasks
        assert stage0.model == MODEL_CROWD
        assert stage0.input_source == "original"
        assert "Crowd" in stage0.keep_stems

        assert stage1.model == MODEL_PRIMARY
        assert stage1.input_source == "_crowd_other"
        assert "Vocals" in stage1.keep_stems
        assert "Crowd" not in stage1.keep_stems  # Crowd came from Stage 0

        assert stage2.model == MODEL_DRUMS
        assert stage2.input_source == "Drums"
        assert stage2.keep_stems == frozenset({"Kick", "Snare"})

    def test_drum_subs_only_two_stages(self):
        """No crowd → Stage 1 (primary) + Stage 2 (drumsep)."""
        canonical = normalize_stems(["kick", "hi-hat"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 2

        stage1, stage2 = plan.tasks
        assert stage1.model == MODEL_PRIMARY
        assert stage1.input_source == "original"
        # "Drums" not in requested_stems — kept only as intermediate
        assert "Drums" not in plan.requested_stems

        assert stage2.model == MODEL_DRUMS
        assert stage2.input_source == "Drums"
        assert stage2.keep_stems == frozenset({"Kick", "Hi-Hat"})

    def test_drums_and_sub_stems_both_stages(self):
        """User wants both Drums (whole) and Kick (sub) → Stage 1 keeps Drums,
        Stage 2 extracts Kick."""
        canonical = normalize_stems(["drums", "kick"])
        plan = resolve_separation_plan(canonical)
        assert len(plan.tasks) == 2

        stage1, stage2 = plan.tasks
        assert "Drums" in stage1.keep_stems     # final output
        assert "Drums" in plan.requested_stems  # user asked for it

        assert stage2.keep_stems == frozenset({"Kick"})

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

    def test_primary_stage_input_uses_crowd_other_when_crowd_present(self):
        """Stage 1 must read from _crowd_other, not original, when crowd was requested."""
        plan = resolve_separation_plan(["Vocals", "Crowd"])
        primary_task = next(t for t in plan.tasks if t.model == MODEL_PRIMARY)
        assert primary_task.input_source == "_crowd_other"

    def test_primary_stage_reads_original_without_crowd(self):
        plan = resolve_separation_plan(["Vocals", "Bass"])
        assert plan.tasks[0].input_source == "original"


# ── cache key contract (stem_cache) ───────────────────────────────────────────

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
