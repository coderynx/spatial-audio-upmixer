"""Tests for the chain head and the pre-limiter soft clipper.

The DSP itself is pinned in `packages/dsp`'s `unit_mastering_head_clip.rs`;
what is checked here is the chain wiring — that each stage is off unless its
flag says otherwise, that it lands in the contracted position, and that its
manifest block reaches the config fields the chain reads.
"""
from __future__ import annotations

import numpy as np
import pytest

import upmixer.mastering.clip  # noqa: F401
import upmixer.mastering.head  # noqa: F401
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.manifest import parse_manifest, validate_manifest
from upmixer.mastering import MasteringChain

SR = 48_000


def _bed(offset: float = 0.0, amplitude: float = 0.2, n: int = SR) -> dict[str, np.ndarray]:
    """A 5.1 bed of 440 Hz, optionally sitting on a DC offset."""
    t = np.arange(n) / SR
    sig = amplitude * np.sin(2 * np.pi * 440 * t) + offset
    return {name: sig.copy() for name in [label.value for label in FORMAT_MAP["5.1"].channels]}


def _tail(channel: np.ndarray) -> np.ndarray:
    return channel[SR // 2:]


def chain_output(cfg: UpmixConfig, channels: dict[str, np.ndarray]):
    return MasteringChain(cfg).process(channels, SR, FORMAT_MAP["5.1"])


class TestChainHead:
    def test_disabled_leaves_the_offset_alone(self):
        out, _ = chain_output(UpmixConfig(loudness_normalize=False), _bed(offset=0.25))
        assert _tail(out["FL"]).mean() == pytest.approx(0.25, abs=1e-3)

    def test_enabled_removes_dc_from_every_channel(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_highpass_enabled=True)
        out, _ = chain_output(cfg, _bed(offset=0.25))
        assert _tail(out["FL"]).mean() == pytest.approx(0.0, abs=1e-3)
        assert _tail(out["LFE"]).mean() == pytest.approx(0.0, abs=1e-3)

    def test_lfe_keeps_the_sub_content_the_mains_lose(self):
        n = SR
        t = np.arange(n) / SR
        sub = 0.4 * np.sin(2 * np.pi * 10 * t)
        channels = {name: sub.copy() for name in [label.value for label in FORMAT_MAP["5.1"].channels]}
        cfg = UpmixConfig(loudness_normalize=False, mastering_highpass_enabled=True)
        out, _ = chain_output(cfg, channels)
        assert np.abs(_tail(out["LFE"])).max() > 0.8 * 0.4
        assert np.abs(_tail(out["FL"])).max() < 0.3 * 0.4


class TestSoftClip:
    def test_enabled_shaves_the_mains_and_leaves_lfe(self):
        cfg = UpmixConfig(
            loudness_normalize=False,
            mastering_clip_enabled=True,
            mastering_clip_db=1.0,
            loudness_max_tp=-1.0,
        )
        hot = _bed(amplitude=1.4)
        clipped, _ = chain_output(cfg, hot)
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False, loudness_max_tp=-1.0), hot)
        # The clipper's ceiling is the limiter's, so the mains come out at or
        # under it; the LFE reaches the limiter unclipped.
        assert np.abs(clipped["FL"]).max() <= 10 ** (-1.0 / 20.0) + 1e-9
        assert not np.allclose(clipped["FL"], plain["FL"])
        assert np.allclose(clipped["LFE"], plain["LFE"])


class TestManifestBlocks:
    def test_blocks_reach_the_config_fields_the_chain_reads(self):
        data = {
            "version": "1.0.0",
            "assets": [{"input": "in.wav", "output": "out.wav"}],
            "mastering": {
                "highpass": {"enabled": True, "cutoff_hz": 25.0},
                "clip": {"enabled": True, "clip_db": 0.8, "knee": 0.5},
            },
        }
        validate_manifest(data)
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig(**jobs[0].config)
        assert cfg.mastering_highpass_enabled is True
        assert cfg.mastering_highpass_hz == pytest.approx(25.0)
        assert cfg.mastering_clip_enabled is True
        assert cfg.mastering_clip_db == pytest.approx(0.8)
        assert cfg.mastering_clip_knee == pytest.approx(0.5)

    def test_both_stages_are_off_by_default(self):
        cfg = UpmixConfig()
        assert cfg.mastering_highpass_enabled is False
        assert cfg.mastering_clip_enabled is False
