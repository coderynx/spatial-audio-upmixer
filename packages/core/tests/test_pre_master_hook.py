"""Tests for StemUpmixPipeline.process_file's pre_master_hook / PreMasterAbort.

Covers the plumbing the web project's reference-match precompute relies on
(see apps/api/src/worker.py::WorkerManager.prepare_reference_match): the hook
must see the exact pre-mastering channel bed, and raising PreMasterAbort must
stop the run before mastering/writing without leaving a partial output file.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.stem_pipeline import PreMasterAbort, StemUpmixPipeline

_EXEC_PLAN = "upmixer.separation.stem_pipeline_separate.execute_plan"

SR = 48_000


def _sine(n: int, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    t = np.linspace(0, n / SR, n, endpoint=False)
    ch = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    return np.column_stack([ch, ch])


def _write_source(path: str, n: int = SR * 2) -> None:
    sf.write(path, _sine(n), SR, subtype="FLOAT")


def _fake_execute_plan(get_separator, plan, sep_path, sep_sr, stage_callback=None):
    """Constant stems shaped like the real separator's output, no model needed."""
    audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
    n = len(audio)
    return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}


def _make_pipeline(**cfg_kwargs) -> StemUpmixPipeline:
    cfg = UpmixConfig(stems=["Vocals"], output_format="5.1", **cfg_kwargs)
    return StemUpmixPipeline(cfg)


class TestPreMasterHook:
    def test_hook_receives_pre_mastering_bed(self, tmp_path):
        pipeline = _make_pipeline()
        source = str(tmp_path / "in.wav")
        _write_source(source)
        output = str(tmp_path / "out.wav")
        captured: dict[str, object] = {}

        def hook(channels, sr, output_fmt):
            captured["channels"] = channels
            captured["sr"] = sr
            captured["output_fmt"] = output_fmt

        with patch(_EXEC_PLAN, side_effect=_fake_execute_plan):
            result = pipeline.process_file(source, output, pre_master_hook=hook)
        pipeline.close()

        assert "channels" in captured
        assert captured["output_fmt"] is FORMAT_MAP["5.1"]
        expected_keys = {label.value for label in FORMAT_MAP["5.1"].channels}
        assert set(captured["channels"].keys()) == expected_keys
        for arr in captured["channels"].values():
            assert np.all(np.isfinite(arr))
        # Mastering still ran (hook didn't abort) — output was written normally.
        assert result is not None

    def test_abort_stops_before_mastering_and_write(self, tmp_path):
        pipeline = _make_pipeline()
        source = str(tmp_path / "in.wav")
        _write_source(source)
        output = str(tmp_path / "out.wav")

        def hook(channels, sr, output_fmt):
            raise PreMasterAbort()

        with patch(_EXEC_PLAN, side_effect=_fake_execute_plan):
            try:
                pipeline.process_file(source, output, pre_master_hook=hook)
                raised = False
            except PreMasterAbort:
                raised = True
        pipeline.close()

        assert raised
        import os
        assert not os.path.exists(output)

    def test_no_hook_behaves_as_before(self, tmp_path):
        pipeline = _make_pipeline()
        source = str(tmp_path / "in.wav")
        _write_source(source)
        output = str(tmp_path / "out.wav")

        with patch(_EXEC_PLAN, side_effect=_fake_execute_plan):
            result = pipeline.process_file(source, output)
        pipeline.close()

        assert result is not None
        import os
        assert os.path.exists(output)
