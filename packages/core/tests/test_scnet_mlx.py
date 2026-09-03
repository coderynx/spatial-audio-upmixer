"""Tiny Torch/MLX parity checks for the ordinary SCNet backend."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")
mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_spectro")

from upmixer.separation.inference.archs.scnet import SCNet
from upmixer.separation.inference.archs.scnet_mlx import SCNetMLX
from upmixer.separation.inference.scnet_mlx import (
    convert_torch_to_mlx_weights,
    load_converted_weights,
)


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
