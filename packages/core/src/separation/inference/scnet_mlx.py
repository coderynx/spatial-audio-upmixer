"""Lazy MLX loader and exact Torch-to-MLX conversion for ordinary SCNet.

Adapted from openmirlab/scnet-infer (MIT, copyright 2026 openmirlab
contributors), revision ``a5437e37c8b942baf74529f35a719aa70dfa9bdc``.
The converted state-dict names follow starrytong/SCNet revision
``e0e3f4037dad3fc9437499051e73aed466bd2766`` and MSST revision
``83d495dfc81b2ede9bc62f4209619f8bdfd14995``.  This attribution covers
source code only; checkpoint licensing is not asserted here.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np


_CONV_MODULE_INDEX = {
    "0": "norm1",
    "1": "conv1",
    "3": "conv2",
    "4": "norm2",
    "6": "conv3",
}
_CONV1D_KEYS = frozenset(("conv1", "conv2", "conv3"))


def _to_numpy(value: Any) -> np.ndarray:
    try:
        value = value.detach().cpu().numpy()
    except AttributeError:
        value = np.asarray(value)
    return np.asarray(value, dtype=np.float32)


def _convert_trunk_key(key: str):
    match = re.match(r"^encoder\.(\d+)\.SDlayer\.convs\.(\d+)\.(weight|bias)$", key)
    if match:
        stage, band, suffix = match.groups()
        kind = "conv2d" if suffix == "weight" else None
        return f"encoder_{stage}.SDlayer.band_{band}.conv.{suffix}", kind

    match = re.match(
        r"^encoder\.(\d+)\.conv_modules\.(\d+)\.layers\.(\d+)\.(\d+)\.(weight|bias)$",
        key,
    )
    if match:
        stage, module, layer, index, suffix = match.groups()
        target = _CONV_MODULE_INDEX.get(index)
        if target is None:
            return None
        kind = "conv1d" if suffix == "weight" and target in _CONV1D_KEYS else None
        return f"encoder_{stage}.conv_module_{module}.layers_{layer}.{target}.{suffix}", kind

    match = re.match(r"^encoder\.(\d+)\.globalconv\.(weight|bias)$", key)
    if match:
        stage, suffix = match.groups()
        kind = "conv2d" if suffix == "weight" else None
        return f"encoder_{stage}.globalconv.conv.{suffix}", kind

    match = re.match(r"^decoder\.(\d+)\.0\.conv\.(weight|bias)$", key)
    if match:
        stage, suffix = match.groups()
        kind = "conv2d" if suffix == "weight" else None
        return f"decoder_fusion_{stage}.conv.conv.{suffix}", kind

    match = re.match(r"^decoder\.(\d+)\.1\.convtrs\.(\d+)\.(weight|bias)$", key)
    if match:
        stage, band, suffix = match.groups()
        kind = "convtranspose2d" if suffix == "weight" else None
        return f"decoder_su_{stage}.band_{band}.conv.{suffix}", kind
    return None


def _reshape(kind: str | None, value: np.ndarray) -> np.ndarray:
    if kind == "conv2d":
        return np.transpose(value, (0, 2, 3, 1))
    if kind == "convtranspose2d":
        return np.transpose(value, (1, 2, 3, 0))
    if kind == "conv1d":
        return np.transpose(value, (0, 2, 1))
    return value


def _convert_separation_key(key: str):
    match = re.match(
        r"^separation_net\.dp_modules\.(\d+)\.norm_layers\.([01])\.(weight|bias)$",
        key,
    )
    if match:
        layer, side, suffix = match.groups()
        return f"separation_net.dp_{layer}.norm_{side}.norm.{suffix}", None

    match = re.match(
        r"^separation_net\.dp_modules\.(\d+)\.linear_layers\.([01])\.(weight|bias)$",
        key,
    )
    if match:
        layer, side, suffix = match.groups()
        return f"separation_net.dp_{layer}.linear_{side}.{suffix}", None
    return None


def _lstm_weight_target(key: str):
    match = re.match(
        r"^separation_net\.dp_modules\.(\d+)\.lstm_layers\.([01])\.weight_(ih|hh)_l0(_reverse)?$",
        key,
    )
    if not match:
        return None
    layer, side, which, reverse = match.groups()
    direction = "backward_cell" if reverse else "forward_cell"
    param = "Wx" if which == "ih" else "Wh"
    return f"separation_net.dp_{layer}.lstm_{side}.{direction}.{param}"


def _lstm_bias_target(key: str):
    match = re.match(
        r"^separation_net\.dp_modules\.(\d+)\.lstm_layers\.([01])\.bias_(ih|hh)_l0(_reverse)?$",
        key,
    )
    if not match:
        return None
    layer, side, which, reverse = match.groups()
    direction = "backward_cell" if reverse else "forward_cell"
    return f"separation_net.dp_{layer}.lstm_{side}.{direction}.bias", which


def convert_torch_to_mlx_weights(
    state_dict: dict[str, Any], family: str = "scnet"
) -> dict[str, Any]:
    """Convert an exact ordinary-SCNet Torch state dict to MLX tensors."""
    if family != "scnet":
        raise ValueError(f"MLX loader supports only ordinary SCNet, got {family!r}")

    import mlx.core as mx

    converted: dict[str, Any] = {}
    biases: dict[str, dict[str, np.ndarray]] = {}
    unknown: list[str] = []
    for key, value in state_dict.items():
        trunk = _convert_trunk_key(key)
        if trunk is not None:
            target, kind = trunk
            converted[target] = mx.array(_reshape(kind, _to_numpy(value)))
            continue

        separation = _convert_separation_key(key)
        if separation is not None:
            target, kind = separation
            converted[target] = mx.array(_reshape(kind, _to_numpy(value)))
            continue

        lstm_weight = _lstm_weight_target(key)
        if lstm_weight is not None:
            converted[lstm_weight] = mx.array(_to_numpy(value))
            continue

        lstm_bias = _lstm_bias_target(key)
        if lstm_bias is not None:
            target, component = lstm_bias
            if component in biases.setdefault(target, {}):
                raise ValueError(f"duplicate SCNet LSTM bias component {key!r}")
            biases[target][component] = _to_numpy(value)
            continue

        unknown.append(key)

    if unknown:
        sample = ", ".join(sorted(unknown)[:5])
        raise ValueError(f"SCNet weight conversion dropped {len(unknown)} tensor(s): {sample}")

    for target, parts in biases.items():
        if set(parts) != {"ih", "hh"}:
            raise ValueError(f"SCNet LSTM bias pair incomplete for {target}")
        converted[target] = mx.array(parts["ih"] + parts["hh"])
    return converted


def load_converted_weights(model, mlx_weights: dict[str, Any]) -> None:
    """Load weights only when model and conversion keys match exactly."""
    from mlx.utils import tree_flatten

    model_parameters = dict(tree_flatten(model.parameters()))
    model_keys = set(model_parameters)
    weight_keys = set(mlx_weights)
    unmatched = sorted(model_keys - weight_keys)
    dropped = sorted(weight_keys - model_keys)
    shape_mismatches = sorted(
        (
            key,
            tuple(model_parameters[key].shape),
            tuple(mlx_weights[key].shape),
        )
        for key in model_keys & weight_keys
        if tuple(model_parameters[key].shape) != tuple(mlx_weights[key].shape)
    )
    if unmatched or dropped or shape_mismatches:
        details = []
        if unmatched:
            details.append(f"{len(unmatched)} model parameters unmatched (e.g. {', '.join(unmatched[:5])})")
        if dropped:
            details.append(f"{len(dropped)} converted tensors dropped (e.g. {', '.join(dropped[:5])})")
        if shape_mismatches:
            sample = "; ".join(
                f"{key}: model {model_shape} != converted {weight_shape}"
                for key, model_shape, weight_shape in shape_mismatches[:5]
            )
            details.append(f"{len(shape_mismatches)} parameter shape mismatches (e.g. {sample})")
        raise ValueError("MLX SCNet weight conversion incomplete: " + ", ".join(details))
    model.load_weights(list(mlx_weights.items()), strict=False)


class SCNetMLXAdapter:
    """Callable Torch-facing adapter around a resident ``SCNetMLX`` model."""

    def __init__(self, model):
        self._model = model

    @property
    def model(self):
        return self._model

    def __call__(self, batch):
        import mlx.core as mx
        import torch

        if not isinstance(batch, torch.Tensor):
            raise TypeError("SCNet MLX adapter expects a CPU torch.Tensor")
        if batch.device.type != "cpu":
            raise ValueError("SCNet MLX adapter expects a CPU torch.Tensor")
        audio = batch.detach().to(dtype=torch.float32).contiguous().numpy()
        output = None
        try:
            output = self._model(mx.array(np.ascontiguousarray(audio, dtype=np.float32)))
            mx.eval(output)
            result = torch.from_numpy(np.array(output, dtype=np.float32, copy=True))
        finally:
            output = None
            mx.clear_cache()
        return result

    def eval(self):
        self._model.eval()
        return self

    def parameters(self):
        return iter(())


def load_model(model_filename: str, model_dir: str):
    """Load the registered SCNet checkpoint through the MLX backend."""
    from .config import load_model_config
    from .loader import _ensure_weights, _load_state_dict
    from .registry import get_model_spec

    spec = get_model_spec(model_filename)
    if spec.arch != "scnet":
        raise ValueError(f"MLX SCNet loader accepts only 'scnet', got {spec.arch!r}")

    from .archs.scnet_mlx import SCNetMLX

    config = load_model_config(spec.config_name)
    weights_path = _ensure_weights(spec, model_dir)
    state = _load_state_dict(weights_path)
    if not isinstance(state, dict):
        raise TypeError(f"SCNet checkpoint did not contain a state dict: {weights_path}")
    model = SCNetMLX(**config.model)
    load_converted_weights(model, convert_torch_to_mlx_weights(state))
    return SCNetMLXAdapter(model.eval()), config


__all__ = [
    "SCNetMLXAdapter",
    "convert_torch_to_mlx_weights",
    "load_converted_weights",
    "load_model",
]
