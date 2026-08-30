"""Tests for upmixer.manifest — asset overrides, job application, registries."""
from __future__ import annotations

import pytest

import upmixer.mastering.bass  # noqa: F401
import upmixer.mastering.chain  # noqa: F401
import upmixer.mastering.compressor  # noqa: F401
# Import domain modules so their register_block_keys calls execute before tests.
import upmixer.mastering.eq  # noqa: F401
import upmixer.mastering.match_reference  # noqa: F401
import upmixer.separation.stem_router  # noqa: F401
from upmixer.config import UpmixConfig
from upmixer.manifest import (
    _BLOCK_REGISTRY,
    _FIELD_MAP,
    AssetJob,
    ManifestError,
    apply_asset_job,
    list_manifest_keys,
    migrate_manifest,
    parse_manifest,
    manifest_parameter_schema,
    validate_manifest,
)

from manifest_helpers import _minimal


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
                    "engine": {"mode": "stem"},
                },
            ],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].engine.get("mode") == "stem"
        assert jobs[1].engine.get("mode") == "stem"

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

    def test_mixing_stem_source_anchor_strength(self):
        data = _minimal(mixing={"stem_source_anchor_strength": 0.35})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.stem_source_anchor_strength == pytest.approx(0.35)

    def test_mixing_stem_routing_and_enabled(self):
        data = _minimal(mixing={
            "stem_routing": {"Vocals": {"C": 0.8, "TFL": 0.1}},
            "stem_enabled": {"Vocals": False},
            "stem_solo": ["Bass"],
        })
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])

        assert cfg.stem_routing == {"Vocals": {"C": 0.8, "TFL": 0.1}}
        assert cfg.stem_enabled == {"Vocals": False}
        assert cfg.stem_solo == ["Bass"]

    def test_mixing_stem_routing_rejects_invalid_channel(self):
        data = _minimal(mixing={"stem_routing": {"Vocals": {"nope": 1.0}}})

        with pytest.raises(ManifestError, match="channel"):
            validate_manifest(data)

    def test_format_block_output_type(self):
        data = _minimal(format={"type": "adm-bwf", "subtype": "PCM_24"})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.output_type == "adm-bwf"
        assert cfg.output_subtype == "PCM_24"

    def test_downmix_derives_sibling_output(self):
        data = _minimal(
            [{"input": "a.flac", "output": "masters/a.wav"}],
            format={"downmix": {"enabled": True, "surround_coeff": 0.5}},
        )
        _, jobs = parse_manifest(data)
        assert jobs[0].config["downmix_output"] == "masters/a_stereo.wav"
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.downmix_enabled is True
        assert cfg.surround_downmix_coeff == pytest.approx(0.5)

    def test_downmix_height_coeff_applies_and_is_bounded(self):
        data = _minimal(format={"downmix": {"enabled": True, "height_coeff": 0.0}})
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.height_downmix_coeff == pytest.approx(0.0)

        with pytest.raises(ManifestError, match="at most"):
            validate_manifest(
                _minimal(format={"downmix": {"enabled": True, "height_coeff": 1.5}})
            )

    def test_rejects_unknown_known_block_field(self):
        data = _minimal(mixing={"channel_layout": "5.1", "typo": True})
        with pytest.raises(ManifestError, match="Unknown manifest field"):
            validate_manifest(data)

    def test_rejects_invalid_downmix_coefficient(self):
        data = _minimal(format={"downmix": {"enabled": True, "surround_coeff": 0.25}})
        with pytest.raises(ManifestError, match="unsupported value"):
            validate_manifest(data)


class TestListManifestKeys:
    def test_returns_dict(self):
        assert isinstance(list_manifest_keys(), dict)

    def test_format_present(self):
        assert "format.type" in list_manifest_keys()

    def test_all_registered_paths_present(self):
        keys = list_manifest_keys()
        for parameter in manifest_parameter_schema():
            assert parameter["path"] in keys

    def test_engine_params_present(self):
        keys = list_manifest_keys()
        for k in ("engine.mode", "engine.stems", "engine.stem_model_dir", "engine.input_format"):
            assert k in keys

    def test_mastering_flat_keys_present(self):
        keys = list_manifest_keys()
        for k in [
            "mastering.eq.profile",
            "mastering.eq.strength",
            "mastering.compressor.profile",
            "mastering.bass.profile",
            "mastering.match_reference.path",
        ]:
            assert k in keys, f"Missing key '{k}'"

    def test_old_eq_match_strength_not_in_field_map(self):
        assert "mastering_eq_match_strength" not in _FIELD_MAP


class TestBlockRegistry:
    def test_core_blocks_registered(self):
        for block in ("engine", "format", "mixing", "processing"):
            assert block in _BLOCK_REGISTRY

    def test_routing_registered_by_stem_router(self):
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


class TestValidateCodecDelivery:
    def test_accepts_a_wide_bed_as_ogg_vorbis(self):
        validate_manifest(
            _minimal(mixing={"channel_layout": "7.1.4"}, format={"codec": "ogg_vorbis"})
        )

    def test_rejects_flac_for_a_bed_wider_than_eight_channels(self):
        with pytest.raises(ManifestError, match="at most 8 channels"):
            validate_manifest(
                _minimal(mixing={"channel_layout": "7.1.4"}, format={"codec": "flac"})
            )

    def test_rejects_a_bit_depth_flac_cannot_carry(self):
        with pytest.raises(ManifestError, match="does not support subtype"):
            validate_manifest(
                _minimal(
                    mixing={"channel_layout": "5.1"},
                    format={"codec": "flac", "subtype": "PCM_32"},
                )
            )

    def test_rejects_opus_off_its_supported_rates(self):
        with pytest.raises(ManifestError, match="supports only"):
            validate_manifest(
                _minimal(format={"codec": "ogg_opus", "sample_rate": 44100})
            )

    def test_rejects_a_non_wav_codec_for_adm_bwf(self):
        with pytest.raises(ManifestError, match="WAV container only"):
            validate_manifest(
                _minimal(
                    mixing={"channel_layout": "7.1.2"},
                    format={"type": "adm-bwf", "codec": "flac"},
                )
            )

    def test_rejects_an_unknown_codec(self):
        with pytest.raises(ManifestError):
            validate_manifest(_minimal(format={"codec": "mp3"}))


class TestMigrateFormatBlock:
    def test_rewrites_the_legacy_wav_type_and_seeds_a_codec(self):
        migrated = migrate_manifest({"format": {"type": "wav", "subtype": "PCM_24"}})
        assert migrated["format"] == {
            "type": "multichannel", "codec": "wav_pcm", "subtype": "PCM_24",
        }

    def test_migrates_per_asset_format_overrides(self):
        migrated = migrate_manifest({
            "assets": [{"input": "a.flac", "output": "a.wav", "format": {"type": "wav"}}],
        })
        assert migrated["assets"][0]["format"] == {"type": "multichannel", "codec": "wav_pcm"}

    def test_leaves_an_explicit_codec_alone(self):
        migrated = migrate_manifest({"format": {"type": "binaural", "codec": "flac"}})
        assert migrated["format"]["codec"] == "flac"

    def test_does_not_mutate_its_input(self):
        original = {"format": {"type": "wav"}}
        migrate_manifest(original)
        assert original == {"format": {"type": "wav"}}

    def test_a_migrated_legacy_manifest_validates_and_parses(self):
        migrated = migrate_manifest(_minimal(format={"type": "wav"}))
        validate_manifest(migrated)
        _meta, jobs = parse_manifest(migrated)
        config = UpmixConfig()
        apply_asset_job(config, jobs[0])
        assert config.output_type == "multichannel"
        assert config.output_codec == "wav_pcm"

    def test_removes_retired_cleanup_fields_from_root_and_assets(self):
        retired = {
            "stem_phase_fix": {"*": True},
            "stem_phase_fix_low_hz": 500.0,
            "stem_phase_fix_high_hz": 5000.0,
            "stem_phase_fix_scale": 0.8,
            "stem_phase_fix_reference_model": "old.ckpt",
            "stem_debleed": {"Bass": True},
            "stem_debleed_model": "old.ckpt",
        }
        migrated = migrate_manifest({
            "engine": {"stem_bleed_reduction": True, **retired},
            "assets": [{
                "input": "in.wav",
                "output": "out.wav",
                "engine": {"stem_bleed_reduction": False, **retired},
            }],
        })

        assert migrated["engine"] == {"stem_bleed_reduction": True}
        assert migrated["assets"][0]["engine"] == {
            "stem_bleed_reduction": False
        }


class TestValidateStereoDelivery:
    def test_accepts_stereo_with_wav(self):
        validate_manifest(_minimal(mixing={"channel_layout": "stereo"}, format={"type": "multichannel"}))

    @pytest.mark.parametrize("output_type", ["adm-bwf", "binaural", "transaural"])
    def test_rejects_stereo_with_a_multichannel_only_delivery(self, output_type):
        with pytest.raises(ManifestError):
            validate_manifest(
                _minimal(mixing={"channel_layout": "stereo"}, format={"type": output_type})
            )

    def test_rejects_a_per_asset_override_that_breaks_the_project_layout(self):
        with pytest.raises(ManifestError):
            validate_manifest(
                _minimal(
                    assets=[{"input": "in.flac", "output": "out.wav", "format": {"type": "adm-bwf"}}],
                    mixing={"channel_layout": "stereo"},
                )
            )

    def test_rejects_an_unknown_channel_layout(self):
        with pytest.raises(ManifestError, match="unsupported value"):
            validate_manifest(_minimal(mixing={"channel_layout": "9.1.6"}))
