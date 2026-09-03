"""Tiny Torch/MLX parity checks for the ordinary SCNet backend."""
from __future__ import annotations

import gc
import os
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_spectro")

from upmixer.separation.inference.archs.scnet import SCNet
from upmixer.separation.inference.archs.scnet_mlx import SCNetMLX
from upmixer.separation.inference.scnet_mlx import (
    SCNetMLXAdapter,
    convert_torch_to_mlx_weights,
    load_converted_weights,
)
from upmixer.separation.inference.config import ModelConfig, load_model_config
from upmixer.separation.inference.demix import demix_scnet
from upmixer.separation.inference.registry import get_model_spec


_MODEL_CONFIG = dict(
    sources=["drums", "bass", "other", "vocals"],
    audio_channels=2,
    dims=[4, 4, 4, 4],
    nfft=16,
    hop_size=4,
    win_size=16,
    band_SR=[0.25, 0.375, 0.375],
    band_stride=[1, 2, 2],
    band_kernel=[3, 2, 2],
    conv_depths=[1, 1, 1],
    compress=2,
    num_dplayer=2,
)


def _models() -> tuple[SCNet, SCNetMLX]:
    torch.manual_seed(7)
    torch_model = SCNet(**_MODEL_CONFIG).eval()
    mlx_model = SCNetMLX(**_MODEL_CONFIG)
    load_converted_weights(
        mlx_model,
        convert_torch_to_mlx_weights(torch_model.state_dict()),
    )
    return torch_model, mlx_model.eval()


def test_tiny_scnet_forward_matches_torch():
    torch_model, mlx_model = _models()
    audio = np.random.default_rng(11).standard_normal((1, 2, 64)).astype(np.float32)

    with torch.inference_mode():
        torch_output = torch_model(torch.from_numpy(audio)).numpy()
    mlx_output = mlx_model(mx.array(audio))
    mx.eval(mlx_output)
    mlx_output = np.array(mlx_output, dtype=np.float32)

    difference = mlx_output - torch_output
    reference_norm = np.linalg.norm(torch_output)
    error_norm = np.linalg.norm(difference)
    snr_db = 20.0 * np.log10(reference_norm / max(error_norm, 1e-20))
    assert mlx_output.shape == torch_output.shape
    assert np.isfinite(torch_output).all()
    assert np.isfinite(mlx_output).all()
    assert np.max(np.abs(difference)) <= 1e-3
    assert snr_db >= 60.0


def test_scnet_mlx_lstm_weights_keep_torch_shapes():
    config = dict(_MODEL_CONFIG, expand=2)
    torch.manual_seed(7)
    torch_model = SCNet(**config).eval()
    weights = convert_torch_to_mlx_weights(torch_model.state_dict())

    assert weights["separation_net.dp_0.lstm_0.forward_cell.Wx"].shape == (32, 4)
    assert weights["separation_net.dp_0.lstm_0.forward_cell.Wh"].shape == (32, 8)


@pytest.mark.parametrize("kind", ["missing", "dropped"])
def test_scnet_weight_conversion_is_strict(kind):
    torch_model, mlx_model = _models()
    weights = convert_torch_to_mlx_weights(torch_model.state_dict())
    if kind == "missing":
        weights.pop(next(iter(weights)))
    else:
        weights["unexpected.weight"] = mx.zeros((1,))

    with pytest.raises(ValueError, match="conversion incomplete"):
        load_converted_weights(mlx_model, weights)


def test_scnet_weight_conversion_rejects_shape_mismatch():
    torch_model, mlx_model = _models()
    weights = convert_torch_to_mlx_weights(torch_model.state_dict())
    key = "separation_net.dp_0.lstm_0.forward_cell.Wx"
    weights[key] = mx.zeros((1,))

    with pytest.raises(ValueError, match="shape mismatch"):
        load_converted_weights(mlx_model, weights)


def test_scnet_mlx_adapter_matches_demix_scnet(monkeypatch):
    torch_model, mlx_model = _models()
    config = ModelConfig(
        audio={"sample_rate": 44100, "chunk_size": 32, "num_channels": 2},
        model={"audio_channels": 2},
        training={"instruments": _MODEL_CONFIG["sources"], "target_instrument": None},
        inference={"dim_t": 1, "num_overlap": 2},
    )
    audio = (0.1 * np.random.default_rng(13).standard_normal((2, 48))).astype(np.float32)

    torch_stems = demix_scnet(
        torch_model,
        audio,
        config,
        torch.device("cpu"),
        chunk_size=32,
        overlap=2,
        batch_size=1,
    )
    clear_calls = 0
    clear_cache = mx.clear_cache

    def track_clear_cache():
        nonlocal clear_calls
        clear_calls += 1
        clear_cache()

    monkeypatch.setattr(mx, "clear_cache", track_clear_cache)
    mlx_stems = demix_scnet(
        SCNetMLXAdapter(mlx_model).eval(),
        audio,
        config,
        torch.device("cpu"),
        chunk_size=32,
        overlap=2,
        batch_size=1,
    )

    assert list(torch_stems) == list(mlx_stems) == _MODEL_CONFIG["sources"]
    reference = np.stack([torch_stems[name] for name in config.instruments])
    candidate = np.stack([mlx_stems[name] for name in config.instruments])
    difference = candidate - reference
    assert candidate.shape == reference.shape == (len(config.instruments), *audio.shape)
    assert np.isfinite(reference).all()
    assert np.isfinite(candidate).all()
    assert np.max(np.abs(difference)) <= 1e-3
    snr_db = 20.0 * np.log10(np.linalg.norm(reference) / max(np.linalg.norm(difference), 1e-20))
    assert snr_db >= 60.0
    assert clear_calls == 5


@pytest.mark.perf
def test_real_scnet_direct_forward_matches_torch_cpu():
    checkpoint = os.environ.get("UPMIXER_SCNET_CHECKPOINT")
    if not checkpoint:
        pytest.skip("set UPMIXER_SCNET_CHECKPOINT for real-checkpoint parity")
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.is_file():
        pytest.skip(f"SCNet checkpoint unavailable: {checkpoint_path}")

    model_filename = "model_scnet_ep_36_sdr_10.0891.ckpt"
    spec = get_model_spec(model_filename)
    config = load_model_config(spec.config_name)
    state = torch.load(str(checkpoint_path), map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]

    torch_model = SCNet(**config.model).eval()
    torch_model.load_state_dict(state)
    audio = (0.1 * np.random.default_rng(17).standard_normal((1, 2, config.sample_rate))).astype(np.float32)
    with torch.inference_mode():
        torch_output = torch_model(torch.from_numpy(audio)).numpy()
    del torch_model
    gc.collect()
    mx.clear_cache()

    mlx_model = SCNetMLX(**config.model)
    mlx_weights = convert_torch_to_mlx_weights(state)
    load_converted_weights(mlx_model, mlx_weights)
    del mlx_weights
    mlx_output = mlx_model(mx.array(audio))
    mx.eval(mlx_output)
    mlx_output = np.array(mlx_output, dtype=np.float32, copy=True)
    del mlx_model
    del state
    gc.collect()
    mx.clear_cache()

    difference = mlx_output - torch_output
    assert mlx_output.shape == torch_output.shape == (
        1,
        len(config.instruments),
        config.model["audio_channels"],
        config.sample_rate,
    )
    assert np.isfinite(torch_output).all()
    assert np.isfinite(mlx_output).all()
    assert np.max(np.abs(difference)) <= 5e-4
    snr_db = 20.0 * np.log10(np.linalg.norm(torch_output) / max(np.linalg.norm(difference), 1e-20))
    assert snr_db >= 60.0
