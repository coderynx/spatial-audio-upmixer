"""Tests for the linked dynamic EQ's chain wiring and manifest surface.

The DSP itself is pinned in `packages/dsp`'s `unit_mastering_dyneq.rs`,
including the decaying-broadband-strike case the stage was designed against;
what is checked here is that the stage is absent unless bands are given, that
it lands between the static EQ and the compressor, and that a manifest band
list survives validation and reaches the config field the chain reads.
"""
from __future__ import annotations

import numpy as np
import pytest

import upmixer.mastering.dyneq  # noqa: F401
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.manifest import ManifestError, parse_manifest, validate_manifest
from upmixer.mastering import MasteringChain

SR = 48_000

BAND = {
    "freq_hz": 3800.0,
    "q": 2.0,
    "threshold_db": -30.0,
    "ratio": 4.0,
    "attack_ms": 10.0,
    "release_ms": 150.0,
}


def _bed(freq: float = 3800.0, amplitude: float = 0.5, n: int = SR) -> dict[str, np.ndarray]:
    t = np.arange(n) / SR
    sig = amplitude * np.sin(2 * np.pi * freq * t)
    return {name: sig.copy() for name in [label.value for label in FORMAT_MAP["5.1"].channels]}


def chain_output(cfg: UpmixConfig, channels: dict[str, np.ndarray]):
    return MasteringChain(cfg).process(channels, SR, FORMAT_MAP["5.1"])


def _manifest(bands: list[dict]) -> dict:
    return {
        "version": "1.0.0",
        "assets": [{"input": "in.wav", "output": "out.wav"}],
        "mastering": {"dynamic_eq": {"bands": bands}},
    }


class TestChainWiring:
    def test_no_bands_is_the_stage_absent(self):
        cfg = UpmixConfig(loudness_normalize=False)
        assert cfg.mastering_dyneq_bands is None
        plain, _ = chain_output(cfg, _bed())
        empty, _ = chain_output(
            UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[]), _bed()
        )
        assert np.array_equal(plain["FL"], empty["FL"])

    def test_a_triggered_band_cuts_the_mains_and_leaves_lfe(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[BAND])
        cut, _ = chain_output(cfg, _bed())
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False), _bed())
        assert np.sqrt((cut["FL"] ** 2).mean()) < 0.5 * np.sqrt((plain["FL"] ** 2).mean())
        assert np.allclose(cut["LFE"], plain["LFE"])

    def test_a_band_below_its_threshold_changes_nothing(self):
        quiet = _bed(amplitude=0.001)
        cfg = UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[BAND])
        out, _ = chain_output(cfg, quiet)
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False), quiet)
        assert np.array_equal(out["FL"], plain["FL"])

    def test_it_runs_ahead_of_the_compressor(self):
        # The stage is contracted to sit before the glue: cutting the band
        # first leaves the compressor less to work against, so the bed comes
        # out louder than it would if the order were reversed.
        base = dict(loudness_normalize=False, mastering_comp_profile="transparent")
        with_dyneq, _ = chain_output(
            UpmixConfig(**base, mastering_dyneq_bands=[BAND]), _bed()
        )
        without, _ = chain_output(UpmixConfig(**base), _bed())
        assert np.abs(with_dyneq["FL"]).max() < np.abs(without["FL"]).max()


class TestManifestBlock:
    def test_bands_reach_the_config_field_the_chain_reads(self):
        data = _manifest([BAND])
        validate_manifest(data)
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig(**jobs[0].config)
        assert cfg.mastering_dyneq_bands == [BAND]

    @pytest.mark.parametrize(
        "band",
        [
            {**BAND, "freq_hz": 30000.0},
            {**BAND, "q": 0.0},
            {**BAND, "ratio": 100.0},
            {k: v for k, v in BAND.items() if k != "attack_ms"},
            {**BAND, "shape": "bell"},
        ],
    )
    def test_a_malformed_band_is_rejected(self, band):
        with pytest.raises(ManifestError):
            validate_manifest(_manifest([band]))

    def test_more_than_four_bands_is_rejected(self):
        with pytest.raises(ManifestError):
            validate_manifest(_manifest([BAND] * 5))
