"""Tests for StemUpmixPipeline.prepare_stems (separation-only, no routing/master).

Project preparation (apps/api worker, mode="stem_prepare") only needs stems
separated and cached for a later mix/export; it must not run routing or
mastering or write an output file.
"""
from __future__ import annotations

import os

import numpy as np
import soundfile as sf
from unittest.mock import patch

from upmixer.config import UpmixConfig
from upmixer.separation.stem_pipeline import StemUpmixPipeline

SR = 48_000


def _sine(n: int, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    t = np.linspace(0, n / SR, n, endpoint=False)
    ch = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    return np.column_stack([ch, ch])


def _fake_execute_plan(plan, sep_path, sep_sr, stage_callback=None):
    audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
    n = len(audio)
    return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}


def test_prepare_stems_skips_routing_and_mastering(tmp_path):
    cfg = UpmixConfig(stems=["Vocals"], output_format="5.1")
    pipeline = StemUpmixPipeline(cfg)
    source = str(tmp_path / "in.wav")
    sf.write(source, _sine(SR), SR, subtype="FLOAT")
    messages: list[str] = []

    with patch.object(pipeline, "_execute_plan", side_effect=_fake_execute_plan):
        result = pipeline.prepare_stems(
            source, progress_callback=lambda m, f: messages.append(m)
        )
    pipeline.close()

    assert result.mode == "stem"
    assert result.stems == ["Vocals"]
    assert result.n_channels_out == 0
    assert result.output_path == ""
    assert result.measured_lkfs is None
    joined = " ".join(messages)
    assert "Routing" not in joined
    assert "Mastering" not in joined


def test_prepare_stems_runs_bleed_reduction(tmp_path):
    cfg = UpmixConfig(
        stems=["Vocals", "Other"],
        output_format="7.1.4",
        stem_bleed_reduction=True,
        stem_debleed={"*": True},  # opt into debleed (off by default)
    )
    pipeline = StemUpmixPipeline(cfg)
    source = str(tmp_path / "in.wav")
    sf.write(source, _sine(SR), SR, subtype="FLOAT")
    models: list[str] = []

    def _fake_separate_array(model, audio, in_sr, sep_sr):
        models.append(model)
        n = len(audio)
        return {
            "Instrumental": np.full((n, 2), 0.3, dtype=np.float32),
            "Vocals": np.zeros((n, 2), dtype=np.float32),
        }

    with patch.object(pipeline, "_execute_plan", side_effect=_fake_execute_plan), patch.object(
        pipeline, "_separate_array", side_effect=_fake_separate_array
    ):
        result = pipeline.prepare_stems(source)
    pipeline.close()

    assert result.mode == "stem"
    assert "Other" in result.stems
    # Other routes to surround/height, so both passes run inference.
    assert cfg.stem_phase_fix_reference_model in models
    assert cfg.stem_debleed_model in models


def test_prepare_stems_writes_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cfg = UpmixConfig(stems=["Vocals"], output_format="5.1", stem_cache_dir=str(cache_dir))
    pipeline = StemUpmixPipeline(cfg)
    source = str(tmp_path / "in.wav")
    sf.write(source, _sine(SR), SR, subtype="FLOAT")

    with patch.object(pipeline, "_execute_plan", side_effect=_fake_execute_plan):
        pipeline.prepare_stems(source)
    pipeline.close()

    assert cache_dir.exists()
    assert any(os.scandir(cache_dir))
