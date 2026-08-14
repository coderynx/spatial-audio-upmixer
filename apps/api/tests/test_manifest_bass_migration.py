"""The stored-manifest rename behind `mastering.bass.mono_cutoff_hz` → `unify_hz`.

A project persisted before LF unification carries the old key, and the client
sends the whole stored block back when the panel is enabled — so a stale key
surfaces as `Unknown manifest field` rather than as anything bass-shaped.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from upmixer.manifest import list_manifest_keys, validate_manifest
from upmixer.mastering.bass import BASS_PROFILES

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src/migrations/versions/e4b7d2f81c95_rename_bass_mono_cutoff_to_unify.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("bass_unify_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def _manifest(bass: dict) -> dict:
    return {
        "version": "1.0",
        "mastering": {"bass": bass},
        "assets": [{"input": "in.wav", "output": "out.wav"}],
    }


def _stored_block(**overrides) -> dict:
    block = {
        "profile": "mono",
        "sub_gain_db": None,
        "mid_gain_db": None,
        "mono_cutoff_hz": None,
        "excite": False,
        "lfe_gain_db": None,
    }
    block.update(overrides)
    return block


class TestBassManifestKeys:
    def test_the_old_key_is_gone_from_the_schema(self):
        assert "mastering.bass.mono_cutoff_hz" not in list_manifest_keys()

    def test_every_profile_field_is_a_manifest_key(self):
        """A profile field with no manifest key cannot be overridden; a
        manifest key with no profile field fails to resolve."""
        keys = {
            k.removeprefix("mastering.bass.")
            for k in list_manifest_keys()
            if k.startswith("mastering.bass.")
        }
        for name, profile in BASS_PROFILES.items():
            assert set(profile) <= keys, f"profile '{name}' has unroutable fields"
        assert keys == set(next(iter(BASS_PROFILES.values()))) | {"profile"}

    def test_a_stale_block_is_rejected(self):
        """The failure this migration exists to prevent."""
        with pytest.raises(Exception, match="Unknown manifest field"):
            validate_manifest(_manifest({"mono_cutoff_hz": 100.0}))

    def test_the_migrated_block_validates(self, migration):
        block = _stored_block(mono_cutoff_hz=100.0)
        migration.rename_to_unify(block)
        validate_manifest(_manifest(block))


class TestWalksEveryStoredShape:
    """The block is reached through four different column shapes; indexing a
    fixed path fixes one of them and leaves the panel broken on the rest."""

    def test_a_bare_manifest(self, migration):
        payload = _manifest(_stored_block(mono_cutoff_hz=90.0))
        assert migration.walk(payload, migration.rename_to_unify) is True
        assert payload["mastering"]["bass"]["unify_hz"] == 90.0

    def test_a_per_layout_override_map(self, migration):
        """`project_tracks.layout_overrides` keys the block by layout name."""
        payload = {
            "7.1.4": {"mastering": {"bass": _stored_block(mono_cutoff_hz=80.0)}},
            "stereo": {"mastering": {"bass": _stored_block()}},
        }
        assert migration.walk(payload, migration.rename_to_unify) is True
        assert payload["7.1.4"]["mastering"]["bass"]["unify_hz"] == 80.0
        assert payload["stereo"]["mastering"]["bass"]["unify_hz"] is None
        for layout in payload.values():
            assert "mono_cutoff_hz" not in layout["mastering"]["bass"]

    def test_a_project_snapshot_with_nested_tracks(self, migration):
        """`jobs.project_snapshot` nests blocks under a list of tracks."""
        payload = {
            "manifest": _manifest(_stored_block()),
            "tracks": [
                {"layout_overrides": {"stereo": {"mastering": {"bass": _stored_block()}}}},
                {"layout_overrides": {}},
            ],
        }
        assert migration.walk(payload, migration.rename_to_unify) is True
        blocks = [
            payload["manifest"]["mastering"]["bass"],
            payload["tracks"][0]["layout_overrides"]["stereo"]["mastering"]["bass"],
        ]
        for block in blocks:
            assert "mono_cutoff_hz" not in block
            assert "unify_hz" in block

    def test_unrelated_json_is_left_alone(self, migration):
        payload = {"scene": {"tracks": [{"x": 0.5, "y": -0.2}]}, "zoom": 1.0}
        before = json.dumps(payload, sort_keys=True)
        assert migration.walk(payload, migration.rename_to_unify) is False
        assert json.dumps(payload, sort_keys=True) == before


class TestRenameToUnify:
    def test_a_null_cutoff_becomes_a_null_crossover(self, migration):
        block = _stored_block()
        assert migration.rename_to_unify(block) is True
        assert "mono_cutoff_hz" not in block
        assert block["unify_hz"] is None
        # Unification stays off, so the spread is left to the profile.
        assert "spread" not in block

    def test_a_real_cutoff_is_pinned_to_the_front_spread(self, migration):
        """Front-collapse is the closest equivalent of the pairwise
        mono-maker; the new `bed` default would spread instead."""
        block = _stored_block(mono_cutoff_hz=100.0)
        assert migration.rename_to_unify(block) is True
        assert block["unify_hz"] == 100.0
        assert block["spread"] == "front"

    def test_other_fields_survive(self, migration):
        block = _stored_block(sub_gain_db=2.5, excite=True)
        migration.rename_to_unify(block)
        assert block["sub_gain_db"] == 2.5
        assert block["excite"] is True

    def test_an_already_migrated_block_is_untouched(self, migration):
        block = {"profile": "deep", "unify_hz": 90.0}
        assert migration.rename_to_unify(block) is False
        assert block == {"profile": "deep", "unify_hz": 90.0}

    def test_the_downgrade_round_trips_the_cutoff(self, migration):
        block = _stored_block(mono_cutoff_hz=80.0)
        migration.rename_to_unify(block)
        assert migration.rename_to_mono_cutoff(block) is True
        assert block == _stored_block(mono_cutoff_hz=80.0)
