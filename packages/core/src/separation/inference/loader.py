"""Weight loading and architecture instantiation for the inference engine."""
from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

import torch

from .archs.bs_roformer import BSRoformer
from .archs.mel_band_roformer import MelBandRoformer
from .archs.tfc_tdf_v3 import TFC_TDF_net
from .config import ModelConfig, load_model_config
from .registry import ModelSpec, get_model_spec

_log = logging.getLogger("upmixer")


def _build_arch(spec: ModelSpec, config: ModelConfig, device: torch.device) -> torch.nn.Module:
    if spec.arch == "bs_roformer":
        return BSRoformer(**config.model)
    if spec.arch == "mel_band_roformer":
        return MelBandRoformer(**config.model)
    if spec.arch == "tfc_tdf_v3":
        return TFC_TDF_net(config.as_namespace(), device=device)
    raise ValueError(f"Unknown architecture '{spec.arch}'")


def _https_context() -> ssl.SSLContext:
    """Build an SSL context, preferring certifi's CA bundle if installed.

    Stdlib ``ssl`` relies on the platform's default trust store, which is
    absent on python.org's macOS "framework" builds until the bundled
    ``Install Certificates.command`` is run — a common source of
    ``CERTIFICATE_VERIFY_FAILED`` for automatic downloads. certifi is only a
    transitive dependency here, so this falls back to the (possibly broken)
    default context when it isn't installed.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _ensure_weights(spec: ModelSpec, model_dir: str) -> str:
    """Return the local path to the checkpoint, downloading if necessary."""
    local_path = os.path.join(model_dir, spec.filename)
    if os.path.exists(local_path):
        return local_path

    os.makedirs(model_dir, exist_ok=True)
    try:
        import shutil
        import urllib.request

        _log.info("Downloading model weights: %s from %s", spec.filename, spec.weights_url)
        with urllib.request.urlopen(spec.weights_url, context=_https_context()) as response:
            with open(local_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
        return local_path
    except Exception as exc:
        if os.path.exists(local_path):
            os.unlink(local_path)
        raise FileNotFoundError(
            f"Model weights '{spec.filename}' not found in {model_dir} and "
            f"automatic download failed ({exc}). Download the checkpoint "
            f"manually from {spec.weights_url} and place it at {local_path}."
        ) from exc


def _load_state_dict(path: str) -> dict:
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        # Community MSST checkpoints are plain tensor state dicts, but some
        # were pickled with a wider payload than weights_only=True accepts.
        # These are pinned checkpoint files (registry.py), not arbitrary
        # user input, so the unsafe reload is acceptable here.
        state = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state and not any(
        isinstance(v, torch.Tensor) for v in state.values()
    ):
        state = state["state_dict"]
    return state


def load_model(
    model_filename: str, device: torch.device, model_dir: str
) -> tuple[torch.nn.Module, ModelConfig]:
    """Load a registered checkpoint onto ``device``, ready for inference.

    Returns the ``nn.Module`` in eval mode plus its parsed :class:`ModelConfig`
    (needed by the demix loop for chunk sizing and stem naming).
    """
    spec = get_model_spec(model_filename)
    config = load_model_config(spec.config_name)

    weights_path = _ensure_weights(spec, model_dir)
    model = _build_arch(spec, config, device)

    state = _load_state_dict(weights_path)
    # Loaded on CPU first, then moved to the target device — mirrors the
    # upstream loading order (some ops misbehave when a state dict is
    # loaded directly onto an accelerator).
    model.load_state_dict(state)
    model.to(device).eval()

    return model, config
