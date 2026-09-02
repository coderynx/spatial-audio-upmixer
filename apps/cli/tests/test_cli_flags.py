"""CLI flag parsing and manifest-override coverage."""
from __future__ import annotations

import math

import pytest

from upmixer.config import UpmixConfig
from upmixer_cli.args import build_parser
from upmixer_cli.flags import _apply_cli_flags


def _parsed(argv: list[str]):
    parser = build_parser()
    return parser.parse_args(["in.wav", "out.wav", *argv])


def test_bass_harmonics_overrides_the_legacy_switch_and_clamps():
    config = UpmixConfig(mastering_bass_excite=True)
    args = _parsed([
        "--mastering-bass-harmonics", "1.5",
        "--mastering-bass-excite",
    ])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.mastering_bass_harmonics == 1.0


def test_bass_cli_options_override_a_manifest_bypass():
    config = UpmixConfig(mastering_bass_enabled=False)
    args = _parsed(["--mastering-bass", "deep"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.mastering_bass_enabled is True


def test_stem_lfe_sets_only_the_lfe_key_on_a_new_stem_routing():
    config = UpmixConfig()
    args = _parsed(["--stem-lfe", "Bass=0.8,Vocals=0"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_routing == {"Bass": {"LFE": 0.8}, "Vocals": {"LFE": 0.0}}


def test_stem_lfe_merges_into_existing_manifest_routing_without_clobbering_it():
    config = UpmixConfig(stem_routing={"Bass": {"FL": 0.65, "FR": 0.65, "C": 0.20, "LFE": 0.30}})
    args = _parsed(["--stem-lfe", "Bass=0.9"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_routing == {"Bass": {"FL": 0.65, "FR": 0.65, "C": 0.20, "LFE": 0.9}}


def test_stem_lfe_absent_leaves_manifest_routing_untouched():
    config = UpmixConfig(stem_routing={"Bass": {"LFE": 0.5}})
    args = _parsed([])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_routing == {"Bass": {"LFE": 0.5}}


@pytest.mark.parametrize(
    ("manifest_value", "argv", "expected"),
    [
        (False, ["--stem-bleed-reduction"], True),
        (True, ["--no-stem-bleed-reduction"], False),
        (True, [], True),
    ],
)
def test_dsp_stem_cleanup_flag_preserves_cli_precedence(manifest_value, argv, expected):
    config = UpmixConfig(stem_bleed_reduction=manifest_value)
    args = _parsed(argv)

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_bleed_reduction is expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--stem-phase-fix-low-hz", "500"],
        ["--stem-phase-fix-high-hz", "5000"],
        ["--stem-phase-fix-scale", "0.8"],
        ["--stem-phase-fix-reference-model", "model.ckpt"],
        ["--stem-debleed-model", "model.ckpt"],
    ],
)
def test_retired_stem_cleanup_flags_are_rejected(argv):
    with pytest.raises(SystemExit):
        _parsed(argv)


def test_stem_lfe_rejects_a_negative_amount():
    config = UpmixConfig()
    args = _parsed(["--stem-lfe", "Bass=-0.1"])

    with pytest.raises(SystemExit):
        _apply_cli_flags(config, args, sample_rate_set=False)


def test_stem_pan_writes_constant_power_weights_onto_a_new_routing():
    config = UpmixConfig()
    args = _parsed(["--stem-pan", "Vocals=0.5,Guitar=0"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_routing["Vocals"]["FL"] == pytest.approx(config.stem_routing["Vocals"]["FR"])
    assert config.stem_routing["Guitar"]["FR"] == pytest.approx(0.0, abs=1e-12)


def test_stem_pan_preserves_the_pair_magnitude_of_existing_routing():
    config = UpmixConfig(stem_routing={"Bass": {"FL": 0.65, "FR": 0.65, "LFE": 0.75}})
    args = _parsed(["--stem-pan", "Bass=1"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    route = config.stem_routing["Bass"]
    assert route["LFE"] == 0.75
    assert math.hypot(route["FL"], route["FR"]) == pytest.approx(math.hypot(0.65, 0.65))


def test_stem_pan_rejects_a_value_outside_the_unit_range():
    config = UpmixConfig()
    args = _parsed(["--stem-pan", "Vocals=1.5"])

    with pytest.raises(SystemExit):
        _apply_cli_flags(config, args, sample_rate_set=False)


def test_spatial_downmix_lock_and_ambient_flags_override_the_manifest():
    config = UpmixConfig(
        spatial_downmix_lock=False,
        stem_ambient_rear={"Vocals": 0.2},
        stem_ambient_height={"Vocals": 0.3},
    )
    args = _parsed([
        "--spatial-downmix-lock", "--stem-ambient-rear", "Vocals=0.8",
        "--stem-ambient-height", "Vocals=0.9", "--stem-ambient-height-crossover", "Vocals=500",
    ])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.spatial_downmix_lock is True
    assert config.stem_ambient_rear == {"Vocals": 0.8}
    assert config.stem_ambient_height == {"Vocals": 0.9}
    assert config.stem_ambient_height_crossover_hz == {"Vocals": 500.0}


def test_ambient_height_crossover_rejects_an_out_of_range_value():
    config = UpmixConfig()
    args = _parsed(["--stem-ambient-height-crossover", "Vocals=4001"])

    with pytest.raises(SystemExit):
        _apply_cli_flags(config, args, sample_rate_set=False)


def test_ambient_flags_merge_into_other_manifest_stem_values():
    config = UpmixConfig(stem_ambient_rear={"Bass": 0.2})
    args = _parsed(["--stem-ambient-rear", "Vocals=0.8"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.stem_ambient_rear == {"Bass": 0.2, "Vocals": 0.8}


def test_format_accepts_the_stereo_layout():
    config = UpmixConfig()
    args = _parsed(["--format", "stereo"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.output_format == "stereo"


@pytest.mark.parametrize("codec", ["wav_pcm", "flac", "ogg_vorbis", "ogg_opus"])
def test_output_codec_reaches_the_config(codec):
    config = UpmixConfig()
    args = _parsed(["--output-codec", codec])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.output_codec == codec


def test_output_codec_defaults_to_wav_when_unset():
    config = UpmixConfig()
    args = _parsed(["--output-type", "multichannel"])

    _apply_cli_flags(config, args, sample_rate_set=False)

    assert config.output_type == "multichannel"
    assert config.output_codec == "wav_pcm"


def test_output_codec_rejects_an_unknown_container():
    with pytest.raises(SystemExit):
        _parsed(["--output-codec", "mp3"])
