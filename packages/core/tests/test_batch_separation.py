"""Tests for batch stem separation: separator reuse, stem cache, manifests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from upmixer.batch import BatchProcessor, resolve_batch_jobs
from upmixer.config import UpmixConfig
from upmixer.result import UpmixResult
from upmixer.separation.stem_pipeline_exec import execute_plan


def _make_wav(path: str, duration_s: float = 1.0, sr: int = 48000) -> str:
    """Write a minimal stereo WAV file for use in tests."""
    n = int(sr * duration_s)
    audio = np.zeros((n, 2), dtype=np.float32)
    sf.write(path, audio, sr)
    return path


@pytest.fixture
def two_wavs(tmp_path):
    a = _make_wav(str(tmp_path / "track01.wav"))
    b = _make_wav(str(tmp_path / "track02.wav"))
    return a, b


class TestSeparatorReuse:
    def test_separator_created_once_for_same_sr(self, two_wavs, tmp_path):
        """Model should be instantiated exactly once when sample rates match."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        from upmixer.separation.stem_plan import MODEL_PRIMARY

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
            # Trigger separator creation for same model + sample rate twice
            pipeline._get_or_create_separator(MODEL_PRIMARY, 48000)
            pipeline._get_or_create_separator(MODEL_PRIMARY, 48000)
            pipeline.close()

        assert init_call_count == 1

    def test_separator_recreated_on_sr_change(self, tmp_path):
        """Changing sample rate between files must reload the model."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        from upmixer.separation.stem_plan import MODEL_PRIMARY

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
            pipeline._get_or_create_separator(MODEL_PRIMARY, 44100)
            pipeline._get_or_create_separator(MODEL_PRIMARY, 48000)
            pipeline.close()

        assert init_call_count == 2

    def test_cpu_keeps_only_one_model_by_default(self):
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        with patch(
            "upmixer.separation.separator._detect_backend", return_value="cpu",
        ):
            pipeline = StemUpmixPipeline(UpmixConfig())
            pipeline._get_or_create_separator("first.ckpt", 48000)
            pipeline._get_or_create_separator("second.ckpt", 48000)

        assert list(pipeline._separators) == ["second.ckpt"]
        pipeline.close()

    def test_mlx_model_releases_mps_model_by_default(self):
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        scnet = "model_scnet_ep_36_sdr_10.0891.ckpt"
        with (
            patch(
                "upmixer.separation.separator._detect_backend", return_value="mps",
            ),
            patch(
                "upmixer.separation.separator._mlx_scnet_available", return_value=True,
            ),
        ):
            pipeline = StemUpmixPipeline(UpmixConfig())
            pipeline._get_or_create_separator("BS-Roformer-SW.ckpt", 48000)
            pipeline._get_or_create_separator(scnet, 48000)

        assert list(pipeline._separators) == [scnet]
        pipeline.close()

    def test_explicit_model_cache_size_retains_multiple_cpu_models(self):
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        config = UpmixConfig(stem_model_cache_size=2)
        with patch(
            "upmixer.separation.separator._detect_backend", return_value="cpu",
        ):
            pipeline = StemUpmixPipeline(config)
            pipeline._get_or_create_separator("first.ckpt", 48000)
            pipeline._get_or_create_separator("second.ckpt", 48000)

        assert list(pipeline._separators) == ["first.ckpt", "second.ckpt"]
        pipeline.close()

    def test_cpu_model_eviction_preserves_stage_intermediate(self, tmp_path):
        import shutil

        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        from upmixer.separation.stem_plan import SeparationPlan, SeparationTask

        class FakeSeparator:
            backend = "cpu"

            def __init__(self, model, **_):
                self.model = model
                self.directory = tmp_path / model
                self.directory.mkdir()

            def separate_to_file(self, audio_path, keep_on_disk, stem_overrides=None, wanted=None):
                if self.model == "first.ckpt":
                    path = self.directory / "Drums.wav"
                    sf.write(
                        path, np.zeros((32, 2), dtype=np.float32), 48000,
                    )
                    return {}, {"Drums": str(path)}
                assert Path(audio_path).exists()
                return {
                    "Kick": np.zeros((32, 2), dtype=np.float32),
                }, {}

            def close(self):
                shutil.rmtree(self.directory, ignore_errors=True)

        plan = SeparationPlan(
            tasks=[
                SeparationTask(
                    "first.ckpt", "original",
                    frozenset({"Drums"}), frozenset(),
                ),
                SeparationTask(
                    "second.ckpt", "Drums",
                    frozenset({"Kick"}), frozenset({"Kick"}),
                ),
            ],
            requested_stems=frozenset({"Kick"}),
            stems_hash="test",
        )
        source = tmp_path / "source.wav"
        sf.write(source, np.zeros((32, 2), dtype=np.float32), 48000)

        with patch(
            "upmixer.separation.stem_pipeline.StemSeparator", FakeSeparator,
        ):
            pipeline = StemUpmixPipeline(UpmixConfig())
            result = execute_plan(
                pipeline._get_or_create_separator, plan, str(source), 48000,
            )

        assert "Kick" in result
        pipeline.close()

    def test_pipeline_context_manager_closes(self, tmp_path):
        """__exit__ must call close() and clear all separators."""
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        from upmixer.separation.stem_plan import MODEL_PRIMARY

        with StemUpmixPipeline(UpmixConfig()) as p:
            p._get_or_create_separator(MODEL_PRIMARY, 48000)
            assert p._separators  # non-empty dict

        assert p._separators == {}
        assert p._separator_sr is None


class TestBatchStemCache:
    """BatchProcessor stem mode auto-enables and correctly propagates stem cache."""
    def _make_mock_pipeline(self, fake_result_fn):
        """Return a context-manager-compatible MagicMock for StemUpmixPipeline."""
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.process_file.side_effect = lambda inp, out, **_: fake_result_fn(inp, out)
        return mock

    def _fake_result(self, input_path: str, output_path: str) -> UpmixResult:
        return UpmixResult(
            input_path=input_path,
            output_path=output_path,
            input_format="Stereo",
            output_format="7.1.4 Atmos",
            input_sample_rate=48000,
            output_sample_rate=48000,
            duration_seconds=1.0,
            n_channels_in=2,
            n_channels_out=12,
            mode="stem",
        )

    def test_auto_cache_dir_when_none_configured(self, two_wavs, tmp_path):
        """stem_cache_dir must default to _DEFAULT_STEM_CACHE_DIR when not set."""
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        processor = BatchProcessor(config=UpmixConfig())
        captured: dict = {}

        def fake_pipeline_cls(config, **kwargs):
            captured["config"] = config
            return self._make_mock_pipeline(self._fake_result)

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline",
            side_effect=fake_pipeline_cls,
        ):
            processor.process(jobs)

        assert "config" in captured
        assert captured["config"].stem_cache_dir == BatchProcessor._DEFAULT_STEM_CACHE_DIR

    def test_explicit_cache_dir_not_overridden(self, two_wavs, tmp_path):
        """Explicit stem_cache_dir on config must not be replaced by the default."""
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        custom_dir = str(tmp_path / "my_stems")
        config = UpmixConfig()
        config.stem_cache_dir = custom_dir
        processor = BatchProcessor(config=config)
        captured: dict = {}

        def fake_pipeline_cls(config, **kwargs):
            captured["config"] = config
            return self._make_mock_pipeline(self._fake_result)

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline",
            side_effect=fake_pipeline_cls,
        ):
            processor.process(jobs)

        assert captured["config"].stem_cache_dir == custom_dir

    def test_original_config_not_mutated(self, two_wavs, tmp_path):
        """BatchProcessor must never mutate the caller's UpmixConfig instance."""
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        original_config = UpmixConfig()
        assert original_config.stem_cache_dir is None

        processor = BatchProcessor(config=original_config)

        def fake_pipeline_cls(config, **kwargs):
            return self._make_mock_pipeline(self._fake_result)

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline",
            side_effect=fake_pipeline_cls,
        ):
            processor.process(jobs)

        # Auto-enable must not bleed back into the caller's config object.
        assert original_config.stem_cache_dir is None


class TestBatchProcessorSequencing:
    def _make_mock_pipeline(self, process_file_side_effect):
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.process_file.side_effect = process_file_side_effect
        return mock

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
            mode="stem",
        )

    def _run(self, jobs, side_effect, **processor_kwargs):
        processor = BatchProcessor(config=UpmixConfig(), **processor_kwargs)
        pipeline = self._make_mock_pipeline(side_effect)
        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline",
            return_value=pipeline,
        ):
            return processor.process(jobs)

    def test_sequential_two_files(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        result = self._run(jobs, lambda inp, out, **_: self._fake_result(inp, out))

        assert len(result.jobs) == 2
        assert len(result.failed) == 0
        assert result.total_audio_duration_s == pytest.approx(2.0)

    def test_partial_failure_continues(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        call_count = 0

        def side_effect(inp, out, **_):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated failure")
            return self._fake_result(inp, out)

        result = self._run(jobs, side_effect)

        assert len(result.failed) == 1
        assert len(result.jobs) == 1
        assert result.failed[0]["error"] == "simulated failure"

    def test_progress_callback_invoked(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=str(out_dir))

        calls = []
        self._run(
            jobs,
            lambda inp, out, **_: self._fake_result(inp, out),
            progress_callback=lambda done, total, path: calls.append((done, total)),
        )

        assert len(calls) >= 2


class TestManifestBatch:
    """Verify that the new assets-based schema produces correct batch jobs."""
    def test_multi_asset_manifest_produces_multiple_jobs(self):
        from upmixer.manifest import parse_manifest, validate_manifest

        data = {
            "version": "1.0.0",
            "engine": {"mode": "stem"},
            "assets": [
                {"input": "/albums/a.wav", "output": "/out/a.wav"},
                {"input": "/albums/b.wav", "output": "/out/b.wav"},
                {"input": "/albums/c.flac", "output": "/out/c.wav"},
            ],
        }
        validate_manifest(data)
        _, jobs = parse_manifest(data)
        assert len(jobs) == 3
        assert jobs[0].input == "/albums/a.wav"
        assert jobs[2].output == "/out/c.wav"

    def test_engine_mode_propagated_to_all_assets(self):
        from upmixer.manifest import parse_manifest

        data = {
            "version": "1.0",
            "engine": {"mode": "stem"},
            "assets": [
                {"input": "a.wav", "output": "a_out.wav"},
                {"input": "b.wav", "output": "b_out.wav"},
            ],
        }
        _, jobs = parse_manifest(data)
        assert all(j.engine.get("mode") == "stem" for j in jobs)

    def test_global_config_inherited_by_all_assets(self):
        from upmixer.manifest import parse_manifest

        data = {
            "version": "1.0",
            "mastering": {"loudness": {"target": -18.0}},
            "assets": [
                {"input": "a.wav", "output": "a.wav"},
                {"input": "b.wav", "output": "b.wav"},
            ],
        }
        _, jobs = parse_manifest(data)
        for j in jobs:
            assert j.config.get("loudness_target") == pytest.approx(-18.0)

    def test_per_asset_output_paths(self):
        from upmixer.manifest import parse_manifest

        data = {
            "version": "1.0",
            "assets": [
                {"input": "x.flac", "output": "/masters/x.wav"},
                {"input": "y.flac", "output": "/masters/y.wav"},
            ],
        }
        _, jobs = parse_manifest(data)
        assert jobs[0].output == "/masters/x.wav"
        assert jobs[1].output == "/masters/y.wav"
