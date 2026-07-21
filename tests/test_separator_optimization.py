"""Backend-aware, full-precision stem separator optimization tests."""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

from upmixer.separation.separator import (
    StemSeparator,
    _SUCCESSFUL_BATCHES,
    _automatic_batch_size,
    _is_oom_error,
)


def test_cpu_batch_is_one():
    assert _automatic_batch_size("cpu") == 1


def test_apple_accelerator_batch_is_two():
    assert _automatic_batch_size("mps") == 2
    assert _automatic_batch_size("coreml") == 2


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


def test_cpu_oom_is_not_retried():
    separator = StemSeparator(model="model.ckpt", batch_size=4)
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
            raise AssertionError("CPU OOM must propagate")


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
    captured = {}

    class FakeAudioSeparator:
        def __init__(
            self, model_file_dir, output_dir, output_format, sample_rate,
            normalization_threshold, log_level, use_soundfile=False,
            use_autocast=True, mdxc_params=None,
        ):
            captured.update(
                use_soundfile=use_soundfile,
                use_autocast=use_autocast,
                mdxc_params=mdxc_params,
                sample_rate=sample_rate,
            )

        def load_model(self, model_filename):
            captured["model"] = model_filename

    package = types.ModuleType("audio_separator")
    module = types.ModuleType("audio_separator.separator")
    module.Separator = FakeAudioSeparator
    package.separator = module

    with patch.dict(
        sys.modules,
        {"audio_separator": package, "audio_separator.separator": module},
    ):
        separator = StemSeparator(
            model="model.ckpt", model_dir=str(tmp_path),
            sample_rate=96000, batch_size=4,
        )
        separator._get_separator()
        separator.close()

    assert captured == {
        "use_soundfile": True,
        "use_autocast": False,
        "mdxc_params": {"batch_size": 4},
        "sample_rate": 96000,
        "model": "model.ckpt",
    }
