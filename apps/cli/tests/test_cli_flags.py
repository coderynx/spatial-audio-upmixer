"""CLI coverage for --stem-lfe flag parsing and merge-not-clobber behavior."""
from __future__ import annotations

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
