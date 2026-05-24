"""Tests for upmixer.manifest — new unified assets schema."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import domain modules so their register_block_keys calls execute before tests.
import upmixer.mastering.eq          # noqa: F401
import upmixer.mastering.compressor  # noqa: F401
import upmixer.mastering.bass        # noqa: F401
import upmixer.mastering.chain       # noqa: F401
import upmixer.mastering.match_reference  # noqa: F401
import upmixer.routing.channel_router  # noqa: F401

from upmixer.config import UpmixConfig
from upmixer.manifest import (
    _BLOCK_REGISTRY,
    _FIELD_MAP,
    AssetJob,
    ManifestError,
    apply_asset_job,
    list_manifest_keys,
    load_manifest,
    parse_manifest,
    validate_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(directory: str, data: dict, name: str = "job.json") -> str:
    path = str(Path(directory) / name)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_yaml(directory: str, text: str, name: str = "job.yaml") -> str:
    path = str(Path(directory) / name)
    Path(path).write_text(text, encoding="utf-8")
    return path


def _minimal(assets=None, **extra) -> dict:
    """Return a minimal valid manifest dict."""
    return {
        "version": "1.0.0",
        "assets": assets or [{"input": "in.flac", "output": "out.wav"}],
        **extra,
    }


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------

class TestLoadManifestJson:
    def test_loads_dict(self, tmp_path):
        data = _minimal()
        path = _write_json(str(tmp_path), data)
        result = load_manifest(path)
        assert result["version"] == "1.0.0"

    def test_empty_file_returns_empty_dict(self, tmp_path):
        path = str(tmp_path / "empty.json")
        Path(path).write_text("{}", encoding="utf-8")
        assert load_manifest(path) == {}

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(str(tmp_path / "missing.json"))

    def test_invalid_extension(self, tmp_path):
        path = str(tmp_path / "job.toml")
        Path(path).write_text("input = 'in.wav'", encoding="utf-8")
        with pytest.raises(ValueError, match="Unrecognised manifest extension"):
            load_manifest(path)

    def test_path_object_accepted(self, tmp_path):
        data = _minimal()
        path = tmp_path / "job.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_manifest(path)
        assert "assets" in result


class TestLoadManifestYaml:
    @pytest.fixture(autouse=True)
    def require_pyyaml(self):
        pytest.importorskip("yaml", reason="pyyaml not installed")

    def test_loads_yaml(self, tmp_path):
        yaml_text = 'version: "1.0.0"\nassets:\n  - input: in.wav\n    output: out.wav\n'
        path = _write_yaml(str(tmp_path), yaml_text)
        result = load_manifest(path)
        assert result["assets"][0]["input"] == "in.wav"

    def test_yml_extension(self, tmp_path):
        yaml_text = 'version: "1.0"\nassets:\n  - input: x.wav\n    output: y.wav\n'
        path = _write_yaml(str(tmp_path), yaml_text, name="job.yml")
        result = load_manifest(path)
        assert result["assets"][0]["output"] == "y.wav"

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        path = _write_yaml(str(tmp_path), "")
        assert load_manifest(path) == {}


# ---------------------------------------------------------------------------
# validate_manifest — version
# ---------------------------------------------------------------------------

class TestValidateManifestVersion:
    def test_valid_two_part(self):
        validate_manifest(_minimal())  # version "1.0.0"

    def test_valid_two_part_short(self):
        d = _minimal()
        d["version"] = "1.0"
        validate_manifest(d)  # two-part is accepted

    def test_valid_three_part(self):
        d = _minimal()
        d["version"] = "2.3.1"
        validate_manifest(d)

    def test_missing_version_raises(self):
        d = _minimal()
        del d["version"]
        with pytest.raises(ManifestError, match="version"):
            validate_manifest(d)

    def test_blank_version_raises(self):
        d = _minimal()
        d["version"] = ""
        with pytest.raises(ManifestError, match="version"):
            validate_manifest(d)

    def test_v_prefix_raises(self):
        d = _minimal()
        d["version"] = "v2"
        with pytest.raises(ManifestError):
            validate_manifest(d)

    def test_v_prefix_full_raises(self):
        d = _minimal()
        d["version"] = "v1.0.0"
        with pytest.raises(ManifestError):
            validate_manifest(d)

    def test_semver_with_prerelease_raises(self):
        d = _minimal()
        d["version"] = "1.0.0-beta"
        with pytest.raises(ManifestError):
            validate_manifest(d)

    def test_single_number_raises(self):
        d = _minimal()
        d["version"] = "2"
        with pytest.raises(ManifestError):
            validate_manifest(d)


# ---------------------------------------------------------------------------
# validate_manifest — assets
# ---------------------------------------------------------------------------

class TestValidateManifestAssets:
    def test_missing_assets_raises(self):
        with pytest.raises(ManifestError, match="assets"):
            validate_manifest({"version": "1.0.0"})

    def test_empty_assets_raises(self):
        with pytest.raises(ManifestError, match="assets"):
            validate_manifest({"version": "1.0.0", "assets": []})

    def test_non_list_assets_raises(self):
        with pytest.raises(ManifestError, match="assets"):
            validate_manifest({"version": "1.0.0", "assets": "oops"})

    def test_asset_missing_input_raises(self):
        with pytest.raises(ManifestError, match="input"):
            validate_manifest({
                "version": "1.0.0",
                "assets": [{"output": "out.wav"}],
            })

    def test_asset_missing_output_raises(self):
        with pytest.raises(ManifestError, match="output"):
            validate_manifest({
                "version": "1.0.0",
                "assets": [{"input": "in.flac"}],
            })

    def test_non_dict_asset_raises(self):
        with pytest.raises(ManifestError):
            validate_manifest({
                "version": "1.0.0",
                "assets": ["not_a_dict"],
            })

    def test_dir_asset_passes_validation(self):
        validate_manifest({
            "version": "1.0",
            "assets": [{"input_dir": "/in/", "output_dir": "/out/"}],
        })

    def test_asset_neither_explicit_nor_dir_raises(self):
        with pytest.raises(ManifestError, match="input_dir"):
            validate_manifest({
                "version": "1.0",
                "assets": [{"stem_cache_dir": "/tmp/"}],
            })

    def test_asset_input_dir_only_raises(self):
        with pytest.raises(ManifestError):
            validate_manifest({
                "version": "1.0",
                "assets": [{"input_dir": "/in/"}],
            })

    def test_asset_output_dir_only_raises(self):
        with pytest.raises(ManifestError):
            validate_manifest({
                "version": "1.0",
                "assets": [{"output_dir": "/out/"}],
            })


# ---------------------------------------------------------------------------
# parse_manifest — dir asset expansion
# ---------------------------------------------------------------------------

class TestDirAssetExpansion:
    def _make_data(self, asset_extra: dict | None = None, global_extra: dict | None = None) -> dict:
        asset = {"input_dir": "/album/", "output_dir": "/out/"}
        if asset_extra:
            asset.update(asset_extra)
        data: dict = {"version": "1.0", "assets": [asset]}
        if global_extra:
            data.update(global_extra)
        return data

    def test_expands_wav_and_flac(self, tmp_path):
        (tmp_path / "01_intro.flac").write_bytes(b"")
        (tmp_path / "02_main.wav").write_bytes(b"")
        (tmp_path / "notes.txt").write_bytes(b"")
        data = {"version": "1.0", "assets": [
            {"input_dir": str(tmp_path), "output_dir": "/out/"}
        ]}
        _, jobs = parse_manifest(data)
        assert len(jobs) == 2
        basenames = sorted(j.input.split("/")[-1] for j in jobs)
        assert basenames == ["01_intro.flac", "02_main.wav"]

    def test_output_paths_derived_correctly(self, tmp_path):
        (tmp_path / "track.flac").write_bytes(b"")
        data = {"version": "1.0", "assets": [
            {"input_dir": str(tmp_path), "output_dir": "/masters/"}
        ]}
        _, jobs = parse_manifest(data)
        assert jobs[0].output == "/masters/track.wav"

    def test_glob_pattern_filters(self, tmp_path):
        (tmp_path / "a.flac").write_bytes(b"")
        (tmp_path / "b.wav").write_bytes(b"")
        data = {"version": "1.0", "assets": [
            {"input_dir": str(tmp_path), "output_dir": "/out/", "glob": "*.flac"}
        ]}
        _, jobs = parse_manifest(data)
        assert len(jobs) == 1
        assert jobs[0].input.endswith("a.flac")

    def test_empty_dir_produces_no_jobs_no_crash(self, tmp_path, caplog):
        import logging
        data = {"version": "1.0", "assets": [
            {"input_dir": str(tmp_path), "output_dir": "/out/"}
        ]}
        with caplog.at_level(logging.WARNING, logger="upmixer"):
            _, jobs = parse_manifest(data)
        assert jobs == []
        assert "matched no" in caplog.text

    def test_per_dir_block_overrides_deep_merge(self, tmp_path):
        (tmp_path / "song.flac").write_bytes(b"")
        data = {
            "version": "1.0",
            "mixing": {"stem_rebalance": {"Vocals": 1.5}},
            "assets": [{
                "input_dir": str(tmp_path),
                "output_dir": "/out/",
                "mixing": {"stem_rebalance": {"Vocals": 0.0}},
            }],
        }
        _, jobs = parse_manifest(data)
        assert len(jobs) == 1
        assert jobs[0].config["stem_rebalance"]["Vocals"] == 0.0

    def test_global_config_inherited(self, tmp_path):
        (tmp_path / "song.flac").write_bytes(b"")
        data = {
            "version": "1.0",
            "engine": {"mode": "realtime"},
            "assets": [{"input_dir": str(tmp_path), "output_dir": "/out/"}],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].engine["mode"] == "realtime"

    def test_config_dicts_are_independent_per_file(self, tmp_path):
        (tmp_path / "a.flac").write_bytes(b"")
        (tmp_path / "b.flac").write_bytes(b"")
        data = {
            "version": "1.0",
            "mastering": {"loudness": {"target": -18.0, "normalize": True}},
            "assets": [{"input_dir": str(tmp_path), "output_dir": "/out/"}],
        }
        _, jobs = parse_manifest(data)
        assert len(jobs) == 2
        jobs[0].config["loudness_target"] = -23.0
        assert jobs[1].config.get("loudness_target") == -18.0

    def test_mixed_explicit_and_dir_assets(self, tmp_path):
        (tmp_path / "dir_track.flac").write_bytes(b"")
        data = {
            "version": "1.0",
            "assets": [
                {"input": "explicit.flac", "output": "explicit.wav"},
                {"input_dir": str(tmp_path), "output_dir": "/out/"},
            ],
        }
        _, jobs = parse_manifest(data)
        assert len(jobs) == 2
        assert jobs[0].input == "explicit.flac"
        assert jobs[1].input.endswith("dir_track.flac")

    def test_sorted_alphabetically(self, tmp_path):
        for name in ["c.flac", "a.flac", "b.wav"]:
            (tmp_path / name).write_bytes(b"")
        data = {"version": "1.0", "assets": [
            {"input_dir": str(tmp_path), "output_dir": "/out/"}
        ]}
        _, jobs = parse_manifest(data)
        basenames = [j.input.split("/")[-1] for j in jobs]
        assert basenames == ["a.flac", "b.wav", "c.flac"]


# ---------------------------------------------------------------------------
# parse_manifest — single asset
# ---------------------------------------------------------------------------

class TestSingleAssetParse:
    def test_basic_fields(self):
        data = _minimal([{"input": "in.flac", "output": "out.wav"}])
        _, jobs = parse_manifest(data)
        assert len(jobs) == 1
        assert jobs[0].input == "in.flac"
        assert jobs[0].output == "out.wav"

    def test_global_engine_mode(self):
        data = _minimal(engine={"mode": "stem"})
        _, jobs = parse_manifest(data)
        assert jobs[0].engine.get("mode") == "stem"

    def test_global_mastering_loudness(self):
        data = _minimal(mastering={"loudness": {"normalize": True, "target": -18.0}})
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("loudness_normalize") is True
        assert jobs[0].config.get("loudness_target") == pytest.approx(-18.0)

    def test_global_format_block(self):
        data = _minimal(format={"type": "adm-bwf", "subtype": "PCM_24", "sample_rate": 48000})
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("output_type") == "adm-bwf"
        assert jobs[0].config.get("output_subtype") == "PCM_24"
        assert jobs[0].config.get("output_sample_rate") == 48000

    def test_global_mixing_channel_layout(self):
        data = _minimal(mixing={"channel_layout": "7.1.4"})
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("format") == "7.1.4"

    def test_global_routing(self):
        data = _minimal(routing={"center_gain": 0.9, "lfe_cutoff": 80.0})
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("center_gain") == pytest.approx(0.9)
        assert jobs[0].config.get("lfe_cutoff") == pytest.approx(80.0)

    def test_processing_preview(self):
        data = _minimal(processing={"preview": True, "preview_duration": 20.0})
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("preview") is True
        assert jobs[0].config.get("preview_duration") == pytest.approx(20.0)

    def test_stem_cache_dir_shortcut(self):
        data = _minimal([{
            "input": "a.flac",
            "output": "a.wav",
            "stem_cache_dir": "/tmp/stems",
        }])
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("stem_cache_dir") == "/tmp/stems"

    def test_metadata_extracted(self):
        data = _minimal(metadata={
            "name": "My Project",
            "author": "Jane Doe",
            "description": "Test",
        })
        meta, _ = parse_manifest(data)
        assert meta is not None
        assert meta.name == "My Project"
        assert meta.author == "Jane Doe"
        assert meta.description == "Test"

    def test_no_metadata_returns_none(self):
        meta, _ = parse_manifest(_minimal())
        assert meta is None

    def test_metadata_not_in_asset_config(self):
        data = _minimal(metadata={"name": "Project"})
        _, jobs = parse_manifest(data)
        assert "name" not in jobs[0].config
        assert "metadata" not in jobs[0].config


# ---------------------------------------------------------------------------
# parse_manifest — batch with overrides
# ---------------------------------------------------------------------------

class TestBatchAssetsWithOverrides:
    def test_two_assets_both_resolved(self):
        data = {
            "version": "1.0",
            "assets": [
                {"input": "a.flac", "output": "a.wav"},
                {"input": "b.flac", "output": "b.wav"},
            ],
        }
        _, jobs = parse_manifest(data)
        assert len(jobs) == 2
        assert jobs[0].input == "a.flac"
        assert jobs[1].input == "b.flac"

    def test_global_inherited_by_all_assets(self):
        data = {
            "version": "1.0",
            "mixing": {"stem_rebalance": {"Vocals": 1.5}},
            "assets": [
                {"input": "a.flac", "output": "a.wav"},
                {"input": "b.flac", "output": "b.wav"},
            ],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].config["stem_rebalance"] == {"Vocals": 1.5}
        assert jobs[1].config["stem_rebalance"] == {"Vocals": 1.5}

    def test_asset_override_replaces_global_leaf(self):
        data = {
            "version": "1.0",
            "mixing": {"stem_rebalance": {"Vocals": 1.5}},
            "assets": [
                {"input": "a.flac", "output": "a.wav"},
                {
                    "input": "b.flac",
                    "output": "b.wav",
                    "mixing": {"stem_rebalance": {"Vocals": 0.0}},
                },
            ],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].config["stem_rebalance"]["Vocals"] == pytest.approx(1.5)
        assert jobs[1].config["stem_rebalance"]["Vocals"] == pytest.approx(0.0)

    def test_asset_override_partial_deep_merge(self):
        """Asset override only touches specified sub-keys; rest of global intact."""
        data = {
            "version": "1.0",
            "mastering": {
                "loudness": {"normalize": True, "target": -18.0, "max_tp": -1.0},
            },
            "assets": [
                {"input": "a.flac", "output": "a.wav"},
                {
                    "input": "b.flac",
                    "output": "b.wav",
                    "mastering": {"loudness": {"target": -14.0}},  # only override target
                },
            ],
        }
        _, jobs = parse_manifest(data)
        # Asset 0: global values
        assert jobs[0].config.get("loudness_normalize") is True
        assert jobs[0].config.get("loudness_target") == pytest.approx(-18.0)
        # Asset 1: target overridden, normalize + max_tp from global
        assert jobs[1].config.get("loudness_normalize") is True
        assert jobs[1].config.get("loudness_target") == pytest.approx(-14.0)
        assert jobs[1].config.get("loudness_max_tp") == pytest.approx(-1.0)

    def test_asset_can_override_engine_mode(self):
        data = {
            "version": "1.0",
            "engine": {"mode": "stem"},
            "assets": [
                {"input": "a.flac", "output": "a.wav"},
                {
                    "input": "b.flac",
                    "output": "b.wav",
                    "engine": {"mode": "realtime"},
                },
            ],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].engine.get("mode") == "stem"
        assert jobs[1].engine.get("mode") == "realtime"

    def test_stem_cache_dir_shortcut_does_not_override_other_blocks(self):
        data = {
            "version": "1.0",
            "mixing": {"stem_rebalance": {"Vocals": 1.0}},
            "assets": [{
                "input": "a.flac",
                "output": "a.wav",
                "stem_cache_dir": "/tmp/stems",
            }],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].config.get("stem_cache_dir") == "/tmp/stems"
        assert jobs[0].config.get("stem_rebalance") == {"Vocals": 1.0}


# ---------------------------------------------------------------------------
# apply_asset_job
# ---------------------------------------------------------------------------

class TestApplyAssetJob:
    def test_output_format(self):
        job = AssetJob(input="x", output="y", config={"format": "7.1.4"})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.output_format == "7.1.4"

    def test_lfe_cutoff_coerced(self):
        job = AssetJob(input="x", output="y", config={"lfe_cutoff": 100.0})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.lfe_cutoff_hz == pytest.approx(100.0)

    def test_loudness_fields(self):
        job = AssetJob(input="x", output="y", config={
            "loudness_normalize": False,
            "loudness_target": -23.0,
        })
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.loudness_normalize is False
        assert cfg.loudness_target_lkfs == pytest.approx(-23.0)

    def test_null_value_skipped(self):
        job = AssetJob(input="x", output="y", config={"lfe_cutoff": None})
        cfg = UpmixConfig()
        original = cfg.lfe_cutoff_hz
        apply_asset_job(cfg, job)
        assert cfg.lfe_cutoff_hz == original

    def test_unknown_key_warns(self, caplog):
        import logging
        job = AssetJob(input="x", output="y", config={"totally_unknown": 99})
        cfg = UpmixConfig()
        with caplog.at_level(logging.WARNING, logger="upmixer"):
            apply_asset_job(cfg, job)
        assert any("totally_unknown" in r.message for r in caplog.records)

    def test_bad_coercion_raises(self):
        job = AssetJob(input="x", output="y", config={"center_gain": "not_a_float"})
        cfg = UpmixConfig()
        with pytest.raises(ValueError, match="center_gain"):
            apply_asset_job(cfg, job)


# ---------------------------------------------------------------------------
# parse_manifest → apply_asset_job integration
# ---------------------------------------------------------------------------

class TestParseAndApplyIntegration:
    def test_full_mastering_section(self):
        data = {
            "version": "1.0.0",
            "mastering": {
                "eq": {"profile": "spatial-present", "strength": 0.7},
                "compressor": {"profile": "warm"},
                "loudness": {"normalize": False, "target": -16.0},
            },
            "assets": [{"input": "a.flac", "output": "a.wav"}],
        }
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.mastering_eq_profile == "spatial-present"
        assert cfg.mastering_eq_strength == pytest.approx(0.7)
        assert cfg.mastering_comp_profile == "warm"
        assert cfg.loudness_normalize is False
        assert cfg.loudness_target_lkfs == pytest.approx(-16.0)

    def test_bass_section(self):
        data = _minimal(mastering={"bass": {"profile": "enhance", "excite": True}})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.mastering_bass_profile == "enhance"
        assert cfg.mastering_bass_excite is True

    def test_match_reference_section(self):
        data = _minimal(mastering={"match_reference": {"path": "ref.wav", "strength": 0.5}})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.mastering_match_ref_path == "ref.wav"
        assert cfg.mastering_match_ref_strength == pytest.approx(0.5)

    def test_routing_section(self):
        data = _minimal(routing={"center_gain": 0.8, "surround_gain": 0.55})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.center_gain == pytest.approx(0.8)
        assert cfg.surround_gain == pytest.approx(0.55)

    def test_mixing_stem_rebalance(self):
        data = _minimal(mixing={"stem_rebalance": {"Vocals": 2.0, "Drums": -1.0}})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.stem_rebalance == {"Vocals": 2.0, "Drums": -1.0}

    def test_format_block_output_type(self):
        data = _minimal(format={"type": "adm-bwf", "subtype": "PCM_24"})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.output_type == "adm-bwf"
        assert cfg.output_subtype == "PCM_24"


# ---------------------------------------------------------------------------
# list_manifest_keys
# ---------------------------------------------------------------------------

class TestListManifestKeys:
    def test_returns_dict(self):
        assert isinstance(list_manifest_keys(), dict)

    def test_format_present(self):
        assert "format" in list_manifest_keys()

    def test_all_field_map_keys_present(self):
        keys = list_manifest_keys()
        for mk in _FIELD_MAP:
            assert mk in keys, f"_FIELD_MAP key '{mk}' missing from list_manifest_keys()"

    def test_engine_params_present(self):
        keys = list_manifest_keys()
        for k in ("mode", "stems", "stem_model_dir", "input_format"):
            assert k in keys

    def test_mastering_flat_keys_present(self):
        keys = list_manifest_keys()
        for k in [
            "mastering_eq_profile",
            "mastering_eq_strength",
            "mastering_comp_profile",
            "mastering_bass_profile",
            "mastering_match_ref_path",
        ]:
            assert k in keys, f"Missing key '{k}'"

    def test_old_eq_match_strength_not_in_field_map(self):
        assert "mastering_eq_match_strength" not in _FIELD_MAP


# ---------------------------------------------------------------------------
# Block registry
# ---------------------------------------------------------------------------

class TestBlockRegistry:
    def test_core_blocks_registered(self):
        for block in ("engine", "format", "mixing", "processing"):
            assert block in _BLOCK_REGISTRY

    def test_routing_registered_by_channel_router(self):
        assert "routing" in _BLOCK_REGISTRY
        assert "center_gain" in _BLOCK_REGISTRY["routing"]

    def test_mastering_registered_by_modules(self):
        assert "mastering" in _BLOCK_REGISTRY
        m = _BLOCK_REGISTRY["mastering"]
        for sub in ("eq", "compressor", "bass", "loudness", "match_reference"):
            assert sub in m, f"mastering.{sub} not registered"

    def test_register_block_adds_new_section(self):
        from upmixer.manifest import register_block
        register_block("_test_plugin", {
            "enabled": ("config", "_test_enabled"),
        })
        assert "_test_plugin" in _BLOCK_REGISTRY
        del _BLOCK_REGISTRY["_test_plugin"]  # clean up

    def test_register_block_keys_extends_section(self):
        from upmixer.manifest import register_block_keys
        register_block_keys("_test_section2", {
            "foo": ("config", "_test_foo"),
        })
        assert "_test_section2" in _BLOCK_REGISTRY
        assert "foo" in _BLOCK_REGISTRY["_test_section2"]
        del _BLOCK_REGISTRY["_test_section2"]  # clean up
