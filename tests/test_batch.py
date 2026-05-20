"""Tests for batch processing (upmixer.batch)."""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from upmixer.batch import BatchJob, BatchProcessor, BatchResult, resolve_batch_jobs
from upmixer.config import UpmixConfig
from upmixer.result import UpmixResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_wav(path: str, duration_s: float = 1.0, sr: int = 48000) -> str:
    """Write a minimal stereo WAV file for use in tests."""
    n = int(sr * duration_s)
    audio = np.zeros((n, 2), dtype=np.float32)
    sf.write(path, audio, sr)
    return path


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


@pytest.fixture
def two_wavs(tmp_path):
    a = _make_wav(str(tmp_path / "track01.wav"))
    b = _make_wav(str(tmp_path / "track02.wav"))
    return a, b


@pytest.fixture
def batch_dir(tmp_path):
    _make_wav(str(tmp_path / "a.wav"))
    _make_wav(str(tmp_path / "b.flac"))
    (tmp_path / "readme.txt").write_text("ignore me")
    return str(tmp_path)


# ── resolve_batch_jobs ────────────────────────────────────────────────────────

class TestResolveBatchJobs:
    def test_from_input_paths(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=out_dir)
        assert len(jobs) == 2
        assert jobs[0].input_path == a
        assert jobs[1].input_path == b
        assert jobs[0].output_path == os.path.join(out_dir, "track01.wav")
        assert jobs[1].output_path == os.path.join(out_dir, "track02.wav")

    def test_from_batch_inputs(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_inputs=[a, b], output_dir=out_dir)
        assert len(jobs) == 2
        assert jobs[0].input_path == a

    def test_from_batch_dir(self, batch_dir, tmp_path):
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=batch_dir, output_dir=out_dir)
        # Only .wav and .flac — not .txt
        assert len(jobs) == 2
        exts = {os.path.splitext(j.input_path)[1] for j in jobs}
        assert exts == {".wav", ".flac"}

    def test_from_explicit_jobs(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        explicit = [
            {"input": a, "output": "/custom/out.wav"},
            {"input": b},
        ]
        jobs = resolve_batch_jobs(
            explicit_jobs=explicit, output_dir=out_dir
        )
        assert len(jobs) == 2
        assert jobs[0].output_path == "/custom/out.wav"
        assert jobs[1].output_path == os.path.join(out_dir, "track02.wav")

    def test_priority_explicit_over_input_paths(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        explicit = [{"input": a}]
        jobs = resolve_batch_jobs(
            input_paths=[a, b],
            explicit_jobs=explicit,
            output_dir=out_dir,
        )
        assert len(jobs) == 1

    def test_priority_batch_inputs_over_batch_dir(self, two_wavs, batch_dir, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(
            batch_inputs=[a],
            batch_dir=batch_dir,
            output_dir=out_dir,
        )
        assert len(jobs) == 1

    def test_missing_output_dir_raises(self, two_wavs):
        a, b = two_wavs
        with pytest.raises(ValueError, match="output_dir required"):
            resolve_batch_jobs(input_paths=[a, b])

    def test_empty_batch_dir_returns_empty(self, tmp_path):
        empty_dir = str(tmp_path / "empty")
        os.makedirs(empty_dir)
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=empty_dir, output_dir=out_dir)
        assert jobs == []

    def test_cross_directory_files(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        a = _make_wav(str(dir1 / "a.wav"))
        b = _make_wav(str(dir2 / "b.wav"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=out_dir)
        assert jobs[0].input_path == a
        assert jobs[1].input_path == b
        # Output names derived from basename only — no path conflict
        assert os.path.basename(jobs[0].output_path) == "a.wav"
        assert os.path.basename(jobs[1].output_path) == "b.wav"


# ── BatchResult ───────────────────────────────────────────────────────────────

class TestBatchResult:
    def _make_result(self) -> UpmixResult:
        return UpmixResult(
            input_path="in.wav",
            output_path="out.wav",
            input_format="Stereo",
            output_format="7.1.4 Atmos",
            input_sample_rate=48000,
            output_sample_rate=48000,
            duration_seconds=3.0,
            n_channels_in=2,
            n_channels_out=12,
            mode="realtime",
        )

    def test_to_dict_structure(self):
        br = BatchResult(jobs=[self._make_result()], failed=[], total_audio_duration_s=3.0, wall_time_s=1.5)
        d = br.to_dict()
        assert d["succeeded"] == 1
        assert d["total"] == 1
        assert len(d["jobs"]) == 1

    def test_to_json_roundtrip(self):
        import json
        br = BatchResult(jobs=[], failed=[{"input": "bad.wav", "error": "oops", "traceback": ""}], total_audio_duration_s=0.0, wall_time_s=0.5)
        j = json.loads(br.to_json())
        assert j["succeeded"] == 0
        assert j["total"] == 1


# ── BatchProcessor — separator reuse ─────────────────────────────────────────

class TestSeparatorReuse:
    def test_separator_created_once_for_same_sr(self, two_wavs, tmp_path):
        """Model should be instantiated exactly once when sample rates match."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        init_call_count = 0
        original_init = __import__(
            "upmixer.separation.separator", fromlist=["StemSeparator"]
        ).StemSeparator.__init__

        def counting_init(self_inner, *args, **kwargs):
            nonlocal init_call_count
            init_call_count += 1
            original_init(self_inner, *args, **kwargs)

        with patch(
            "upmixer.separation.separator.StemSeparator.__init__",
            counting_init,
        ):
            pipeline = StemUpmixPipeline(UpmixConfig())
            # Trigger separator creation for the same sample rate twice
            pipeline._get_or_create_separator(48000)
            pipeline._get_or_create_separator(48000)
            pipeline.close()

        assert init_call_count == 1

    def test_separator_recreated_on_sr_change(self, tmp_path):
        """Changing sample rate between files must reload the model."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        init_call_count = 0
        original_init = __import__(
            "upmixer.separation.separator", fromlist=["StemSeparator"]
        ).StemSeparator.__init__

        def counting_init(self_inner, *args, **kwargs):
            nonlocal init_call_count
            init_call_count += 1
            original_init(self_inner, *args, **kwargs)

        with patch(
            "upmixer.separation.separator.StemSeparator.__init__",
            counting_init,
        ):
            pipeline = StemUpmixPipeline(UpmixConfig())
            pipeline._get_or_create_separator(44100)
            pipeline._get_or_create_separator(48000)
            pipeline.close()

        assert init_call_count == 2

    def test_pipeline_context_manager_closes(self, tmp_path):
        """__exit__ must call close() and nullify the separator."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        with StemUpmixPipeline(UpmixConfig()) as p:
            p._get_or_create_separator(48000)
            assert p._separator is not None

        assert p._separator is None
        assert p._separator_sr is None


# ── BatchProcessor — realtime mode ───────────────────────────────────────────

class TestBatchProcessorRealtime:
    def _fake_result(self, input_path: str, output_path: str) -> UpmixResult:
        return UpmixResult(
            input_path=input_path,
            output_path=output_path,
            input_format="Stereo",
            output_format="5.1",
            input_sample_rate=48000,
            output_sample_rate=48000,
            duration_seconds=1.0,
            n_channels_in=2,
            n_channels_out=6,
            mode="realtime",
        )

    def test_sequential_two_files(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        processor = BatchProcessor(config=UpmixConfig(), mode="realtime", workers=1)

        with patch("upmixer.pipeline.UpmixPipeline.process_file") as mock_pf:
            mock_pf.side_effect = lambda inp, out, **_: self._fake_result(inp, out)
            result = processor.process(jobs)

        assert len(result.jobs) == 2
        assert len(result.failed) == 0
        assert result.total_audio_duration_s == pytest.approx(2.0)

    def test_partial_failure_continues(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        processor = BatchProcessor(config=UpmixConfig(), mode="realtime", workers=1)

        call_count = 0

        def side_effect(inp, out, **_):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated failure")
            return self._fake_result(inp, out)

        with patch("upmixer.pipeline.UpmixPipeline.process_file", side_effect=side_effect):
            result = processor.process(jobs)

        assert len(result.failed) == 1
        assert len(result.jobs) == 1
        assert result.failed[0]["error"] == "simulated failure"

    def test_progress_callback_invoked(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        calls = []
        processor = BatchProcessor(
            config=UpmixConfig(),
            mode="realtime",
            workers=1,
            progress_callback=lambda done, total, path: calls.append((done, total)),
        )

        with patch("upmixer.pipeline.UpmixPipeline.process_file") as mock_pf:
            mock_pf.side_effect = lambda inp, out, **_: self._fake_result(inp, out)
            processor.process(jobs)

        assert len(calls) >= 2


# ── Manifest batch: section ───────────────────────────────────────────────────

class TestManifestBatch:
    def test_batch_section_expands_to_flat_keys(self):
        from upmixer.manifest import _expand_nested_sections

        data = {
            "mode": "stem",
            "batch": {
                "input_dir": "/albums/",
                "output_dir": "/out/",
                "workers": 2,
            },
        }
        expanded = _expand_nested_sections(data)
        assert expanded["batch_dir"] == "/albums/"
        assert expanded["batch_output_dir"] == "/out/"
        assert expanded["batch_workers"] == 2
        assert "batch" not in expanded

    def test_batch_inputs_list_preserved(self):
        from upmixer.manifest import _expand_nested_sections

        data = {
            "batch": {
                "inputs": ["/dir1/a.wav", "/dir2/b.flac"],
                "output_dir": "/out/",
            }
        }
        expanded = _expand_nested_sections(data)
        assert expanded["batch_inputs"] == ["/dir1/a.wav", "/dir2/b.flac"]

    def test_batch_jobs_list_preserved(self):
        from upmixer.manifest import _expand_nested_sections

        jobs = [{"input": "/a.wav"}, {"input": "/b.wav", "output": "/out/b.wav"}]
        data = {"batch": {"jobs": jobs, "output_dir": "/out/"}}
        expanded = _expand_nested_sections(data)
        assert expanded["batch_jobs"] == jobs

    def test_apply_manifest_returns_batch_params(self):
        from upmixer.manifest import apply_manifest

        config = UpmixConfig()
        manifest = {
            "mode": "stem",
            "batch_dir": "/albums/",
            "batch_output_dir": "/out/",
            "batch_workers": 1,
        }
        job_params = apply_manifest(config, manifest)
        assert job_params["batch_dir"] == "/albums/"
        assert job_params["batch_output_dir"] == "/out/"
        assert job_params["batch_workers"] == 1
