"""Level-domain invariants of SeparationEngine's write path.

The pre-demix peak normalization must be divided back out of every stem, so
stems stay in the input's level domain regardless of how loud the input was.
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

torch = pytest.importorskip("torch")

from upmixer.separation.inference.config import ModelConfig
from upmixer.separation.inference.device import DeviceManager
from upmixer.separation.inference.engine import SeparationEngine


def _make_config() -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 44100, "hop_length": 100},
        model={"stft_hop_length": 100},
        training={"instruments": ["vocals", "other"], "target_instrument": None},
        inference={"dim_t": 6},
    )


def _make_engine(output_dir: str) -> SeparationEngine:
    engine = SeparationEngine(
        model=torch.nn.Identity(),
        config=_make_config(),
        arch="bs_roformer",
        model_filename="test.ckpt",
        device=DeviceManager("cpu"),
        output_dir=output_dir,
        sample_rate=44100,
        batch_size=1,
        segment_size=None,
        chunk_duration_s=None,
    )
    # Stand-in for a real checkpoint: a linear split that sums back to its
    # input, so any level error the engine introduces is the only thing the
    # assertions below can see.
    engine._demix_arch = lambda mix: {
        "vocals": mix * 0.25,
        "other": mix * 0.75,
    }
    return engine


def _write_source(path, audio: np.ndarray) -> str:
    sf.write(str(path), audio.T, 44100, subtype="FLOAT")
    return str(path)


def _separate(tmp_path, name: str, audio: np.ndarray) -> dict[str, np.ndarray]:
    source = _write_source(tmp_path / f"{name}.wav", audio)
    out_dir = tmp_path / f"{name}_out"
    paths = _make_engine(str(out_dir)).separate(source)
    stems = {}
    for path in paths:
        data, _ = sf.read(path, dtype="float32", always_2d=True)
        stems[path] = data.T
    return stems


def _loud_mix(n_samples: int = 2000) -> np.ndarray:
    rng = np.random.default_rng(0)
    mix = rng.standard_normal((2, n_samples)).astype(np.float32)
    return (mix / np.abs(mix).max() * 1.073).astype(np.float32)


def test_stems_sum_to_input_level(tmp_path):
    mix = _loud_mix()
    stems = _separate(tmp_path, "loud", mix)

    total = sum(stems.values())
    assert np.allclose(total, mix, atol=1e-5)


def test_halving_input_halves_output_exactly(tmp_path):
    mix = _loud_mix()
    full = _separate(tmp_path, "full", mix)
    half = _separate(tmp_path, "half", (mix * 0.5).astype(np.float32))

    assert len(full) == len(half) == 2
    for full_path, half_path in zip(sorted(full), sorted(half)):
        assert np.allclose(half[half_path], full[full_path] * 0.5, atol=1e-6)


def test_retains_exact_resampled_parent_in_restored_level_domain(tmp_path):
    from upmixer.separation.inference.audio_io import load_audio

    input_rate = 48_000
    t = np.arange(input_rate, dtype=np.float32) / input_rate
    audio = np.stack([
        1.05 * np.sin(2 * np.pi * 440.0 * t),
        0.95 * np.sin(2 * np.pi * 660.0 * t),
    ]).astype(np.float32)
    source = tmp_path / "native-rate.wav"
    sf.write(source, audio.T, input_rate, subtype="FLOAT")
    engine = _make_engine(str(tmp_path / "out"))

    engine.separate(str(source), retain_parent=True)
    parent = engine.take_last_parent()

    assert np.array_equal(parent, load_audio(str(source), 44_100).T)
    assert np.max(np.abs(parent)) > 0.9
    with pytest.raises(RuntimeError, match="No completed separation input"):
        engine.take_last_parent()
