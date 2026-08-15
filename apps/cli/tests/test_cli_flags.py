"""CLI coverage for --stem-lfe/--stem-pan parsing and merge-not-clobber behavior."""
from __future__ import annotations

import math

import pytest

from upmixer.config import UpmixConfig
from upmixer_cli.args import build_parser
from upmixer_cli.flags import _apply_cli_flags


def _parsed(argv: list[str]):
    parser = build_parser()
    return parser.parse_args(["in.wav", "out.wav", *argv])


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
