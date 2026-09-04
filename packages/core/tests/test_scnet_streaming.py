"""Parity and output-contract tests for bounded SCNet overlap-add."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

torch = pytest.importorskip("torch")

from upmixer.separation.inference import demix  # noqa: E402
from upmixer.separation.inference.config import ModelConfig  # noqa: E402
from upmixer.separation.inference.device import DeviceManager  # noqa: E402
from upmixer.separation.inference.engine import SeparationEngine  # noqa: E402
from upmixer.separation.inference.scnet_mlx import load_model  # noqa: E402


_INSTRUMENTS = ["drums", "bass", "other", "vocals"]


def _config() -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 44100, "chunk_size": 16, "num_channels": 2},
        model={"audio_channels": 2},
        training={"instruments": _INSTRUMENTS, "target_instrument": None},
        inference={"dim_t": 1, "num_overlap": 2},
    )


class _PatternModel(torch.nn.Module):
    """Deterministic model that exposes call and padding order."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        call = self.calls
        self.calls += 1
        return torch.stack(
            [batch * (index + 1) + call for index in range(len(_INSTRUMENTS))],
            dim=1,
        )


@pytest.mark.parametrize("n_samples", [5, 16, 40, 55])
def test_scnet_streaming_matches_in_memory_for_padding_and_chunk_edges(n_samples):
    config = _config()
    mix = (np.random.default_rng(n_samples).random((2, n_samples)) - 0.5).astype(
        np.float32
    )

    expected = demix.demix_scnet(
        _PatternModel(),
        mix,
        config,
        torch.device("cpu"),
        batch_size=2,
    )
    chunks = {name: [] for name in config.instruments}
    names = demix.demix_scnet_stream(
        _PatternModel(),
        mix,
        config,
        torch.device("cpu"),
        batch_size=2,
        frame_callback=lambda name, frame: chunks[name].append(frame.copy()),
    )

    assert names == tuple(config.instruments)
    for name in config.instruments:
        actual = np.concatenate(chunks[name], axis=-1)
        np.testing.assert_array_equal(actual, expected[name])


def test_scnet_streaming_accumulates_only_wanted_stems():
    config = _config()
    mix = np.random.default_rng(20).standard_normal((2, 40)).astype(np.float32)
    expected = demix.demix_scnet(
        _PatternModel(), mix, config, torch.device("cpu"), batch_size=2
    )
    chunks: dict[str, list[np.ndarray]] = {}
    names = demix.demix_scnet_stream(
        _PatternModel(),
        mix,
        config,
        torch.device("cpu"),
        batch_size=2,
        wanted={"Bass", "Drums"},
        frame_callback=lambda name, frame: chunks.setdefault(name, []).append(
            frame.copy()
        ),
    )

    assert names == ("drums", "bass")
    assert set(chunks) == {"drums", "bass"}
    for name in names:
        np.testing.assert_array_equal(
            np.concatenate(chunks[name], axis=-1), expected[name]
        )


def test_engine_streaming_publishes_selected_float32_wavs_atomically(tmp_path: Path):
    config = _config()
    source = tmp_path / "source.wav"
    mix = np.random.default_rng(21).standard_normal((2, 40)).astype(np.float32)
    sf.write(source, mix.T, config.sample_rate, subtype="FLOAT")
    output_dir = tmp_path / "stems"
    engine = SeparationEngine(
        model=_PatternModel(),
        config=config,
        arch="scnet",
        model_filename="model.ckpt",
        device=DeviceManager("cpu"),
        output_dir=str(output_dir),
        sample_rate=config.sample_rate,
        batch_size=2,
        segment_size=None,
        chunk_duration_s=None,
    )

    paths = engine.separate(str(source), wanted={"Bass", "Drums"})

    assert [Path(path).name for path in paths] == [
        "source_(drums)_model.wav",
        "source_(bass)_model.wav",
    ]
    assert not list(output_dir.glob("*.tmp"))
    for path in paths:
        rendered, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        assert sample_rate == config.sample_rate
        assert rendered.shape == (len(mix.T), 2)
        assert rendered.dtype == np.float32
        assert np.isfinite(rendered).all()


def test_engine_defers_non_scnet_wanted_filter_until_canonical_mapping(
    monkeypatch, tmp_path: Path
):
    config = _config()
    source = tmp_path / "source.wav"
    mix = np.zeros((2, 16), dtype=np.float32)
    sf.write(source, mix.T, config.sample_rate, subtype="FLOAT")
    engine = SeparationEngine(
        model=_PatternModel(),
        config=config,
        arch="mel_band_roformer",
        model_filename="model.ckpt",
        device=DeviceManager("cpu"),
        output_dir=str(tmp_path / "stems"),
        sample_rate=config.sample_rate,
        batch_size=1,
        segment_size=None,
        chunk_duration_s=None,
    )
    monkeypatch.setattr(
        engine,
        "_demix_with_chunking",
        lambda _mix, _callback: {"instrumental": mix},
    )

    paths = engine.separate(str(source), wanted={"_deux_inst"})

    assert [Path(path).name for path in paths] == ["source_(instrumental)_model.wav"]


@pytest.mark.perf
def test_real_scnet_stream_matches_legacy_overlap_add():
    checkpoint = os.environ.get("UPMIXER_SCNET_CHECKPOINT")
    if not checkpoint:
        pytest.skip("set UPMIXER_SCNET_CHECKPOINT for real-checkpoint parity")
    checkpoint_path = Path(checkpoint)
    model_filename = "model_scnet_ep_36_sdr_10.0891.ckpt"
    if checkpoint_path.name != model_filename or not checkpoint_path.is_file():
        pytest.skip(f"SCNet checkpoint unavailable at registered path: {checkpoint}")

    model, config = load_model(model_filename, str(checkpoint_path.parent))
    mix = (
        0.01 * np.random.default_rng(22).standard_normal((2, config.chunk_size + 1))
    ).astype(np.float32)
    expected = demix.demix_scnet(model, mix, config, torch.device("cpu"), batch_size=1)
    chunks: dict[str, list[np.ndarray]] = {}

    names = demix.demix_scnet_stream(
        model,
        mix,
        config,
        torch.device("cpu"),
        batch_size=1,
        wanted={"Bass", "Drums"},
        frame_callback=lambda name, frame: chunks.setdefault(name, []).append(
            frame.copy()
        ),
    )

    assert names == ("drums", "bass")
    for name in names:
        np.testing.assert_array_equal(
            np.concatenate(chunks[name], axis=-1), expected[name]
        )
