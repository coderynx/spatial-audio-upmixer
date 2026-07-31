"""Backend-aware, full-precision stem separator optimization tests."""
from __future__ import annotations

from unittest.mock import patch

from upmixer.separation.inference.registry import ModelSpec
from upmixer.separation.separator import (
    MODEL_STEM_OVERRIDES,
    StemSeparator,
    _SUCCESSFUL_BATCHES,
    _automatic_batch_size,
    _automatic_cpu_tuning,
    _is_oom_error,
    _parse_stem_name,
)


def test_cpu_batch_is_one():
    assert _automatic_batch_size("cpu") == 1


def test_apple_accelerator_batch_is_two():
    assert _automatic_batch_size("mps") == 2
    assert _automatic_batch_size("coreml") == 2


def test_low_memory_cpu_uses_small_segments_and_file_chunks():
    assert _automatic_cpu_tuning("cpu", 3.5) == (64, 120.0)
    assert _automatic_cpu_tuning("cpu", 7.5) == (128, 300.0)
    assert _automatic_cpu_tuning("cpu", 10.0) == (128, 600.0)
    assert _automatic_cpu_tuning("cpu", 16.0) == (None, None)
    assert _automatic_cpu_tuning("cuda", 4.0) == (None, None)


def test_only_actual_oom_is_retryable():
    assert _is_oom_error(RuntimeError("CUDA out of memory"))
    assert not _is_oom_error(RuntimeError("invalid model configuration"))


def test_accelerator_oom_retries_with_smaller_batch():
    separator = StemSeparator(model="model.ckpt", batch_size=4)
    separator._backend = "cuda"

    class FakeSeparator:
        calls = 0

        def separate(self, _):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("CUDA out of memory")
            return ["stem.wav"]

    fake = FakeSeparator()
    with (
        patch.object(separator, "_get_separator", return_value=fake),
        patch("gc.collect"),
    ):
        assert separator._separate_paths("input.wav") == ["stem.wav"]

    assert fake.calls == 2
    assert separator._batch_size == 2


def test_cpu_oom_retries_with_smaller_segment():
    separator = StemSeparator(
        model="model.ckpt", batch_size=1,
        segment_size=128, chunk_duration_s=120.0,
    )
    separator._backend = "cpu"

    class FakeSeparator:
        calls = 0

        def separate(self, _):
            self.calls += 1
            if self.calls == 1:
                raise MemoryError("out of memory")
            return []

    fake = FakeSeparator()
    with patch.object(separator, "_get_separator", return_value=fake):
        assert separator._separate_paths("input.wav") == []

    assert fake.calls == 2
    assert separator._segment_size == 64


def test_cpu_oom_retries_with_quality_knobs_set():
    """Overlap/TTA/pitch-shift are quality knobs, not memory knobs — the OOM
    ladder must still reduce batch/segment/chunk and leave them untouched."""
    separator = StemSeparator(
        model="model.ckpt", batch_size=1,
        segment_size=128, chunk_duration_s=120.0,
        overlap=8, tta=True, pitch_shift=0.75,
    )
    separator._backend = "cpu"

    class FakeSeparator:
        calls = 0

        def separate(self, _):
            self.calls += 1
            if self.calls == 1:
                raise MemoryError("out of memory")
            return []

    fake = FakeSeparator()
    with patch.object(separator, "_get_separator", return_value=fake):
        assert separator._separate_paths("input.wav") == []

    assert fake.calls == 2
    assert separator._segment_size == 64
    assert separator._overlap == 8
    assert separator._tta is True
    assert separator._pitch_shift == 0.75


def test_cpu_oom_propagates_after_minimum_settings():
    separator = StemSeparator(
        model="model.ckpt", batch_size=1,
        segment_size=64, chunk_duration_s=60.0,
    )
    separator._backend = "cpu"

    class FakeSeparator:
        def separate(self, _):
            raise MemoryError("out of memory")

    with patch.object(separator, "_get_separator", return_value=FakeSeparator()):
        try:
            separator._separate_paths("input.wav")
        except MemoryError:
            pass
        else:
            raise AssertionError("minimum-memory CPU OOM must propagate")


def test_explicit_batch_does_not_replace_learned_auto_value():
    separator = StemSeparator(model="explicit.ckpt", batch_size=1)
    separator._backend = "cuda"

    class FakeSeparator:
        def separate(self, _):
            return []

    _SUCCESSFUL_BATCHES.pop(("explicit.ckpt", "cuda"), None)
    with patch.object(separator, "_get_separator", return_value=FakeSeparator()):
        separator._separate_paths("input.wav")
    assert ("explicit.ckpt", "cuda") not in _SUCCESSFUL_BATCHES


def test_separator_receives_full_precision_batch_options(tmp_path):
    fake_model = object()
    fake_config = object()
    captured = {}

    def fake_load_model(model_filename, device, model_dir):
        captured.update(model=model_filename, device=device, model_dir=model_dir)
        return fake_model, fake_config

    fake_spec = ModelSpec(
        filename="model.ckpt", arch="bs_roformer",
        config_name="unused", weights_url="",
        default_chunk_samples=882000,
    )

    with (
        patch(
            "upmixer.separation.inference.loader.load_model",
            side_effect=fake_load_model,
        ),
        patch(
            "upmixer.separation.inference.registry.get_model_spec",
            return_value=fake_spec,
        ),
    ):
        separator = StemSeparator(
            model="model.ckpt", model_dir=str(tmp_path),
            sample_rate=96000, batch_size=4,
            segment_size=128, chunk_duration_s=300.0,
            overlap=4, tta=True, pitch_shift=0.75,
        )
        engine = separator._get_separator()
        separator.close()

    # Weight loading receives exactly this instance's model/dir settings —
    # not audio-separator kwargs, since there is no longer a third-party
    # Separator object to configure. Full float32 precision (no autocast) is
    # now a structural property of demix.py rather than a runtime kwarg.
    assert captured["model"] == "model.ckpt"
    assert captured["model_dir"] == str(tmp_path)
    assert engine._model is fake_model
    assert engine._config is fake_config
    assert engine._arch == "bs_roformer"
    assert engine._sample_rate == 96000
    assert engine._batch_size == 4
    assert engine._segment_size == 128
    assert engine._chunk_duration_s == 300.0
    assert engine._overlap == 4
    assert engine._tta is True
    assert engine._pitch_shift == 0.75
    assert engine._default_chunk_samples == 882000


def test_karaoke_output_names_map_to_vocal_children():
    overrides = MODEL_STEM_OVERRIDES[
        "mel_band_roformer_karaoke_gabox_v2.ckpt"
    ]

    assert _parse_stem_name("song_(Lead Vocals)_karaoke.wav", overrides) == "Lead Vocals"
    assert _parse_stem_name("song_(Vocals)_karaoke.wav", overrides) == "Lead Vocals"
    assert _parse_stem_name("song_(Instrumental)_karaoke.wav", overrides) == "Backing Vocals"
