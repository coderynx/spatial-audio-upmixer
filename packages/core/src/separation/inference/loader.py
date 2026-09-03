"""Weight loading and architecture instantiation for the inference engine."""
from __future__ import annotations

import hashlib
import logging
import os
import ssl
import tempfile

import torch

from .archs.bs_roformer import BSRoformer
from .archs.mel_band_roformer import MelBandRoformer
from .archs.scnet import SCNet
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
    if spec.arch == "scnet":
        return SCNet(**config.model)
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


def _validate_weights(path: str, spec: ModelSpec) -> None:
    """Validate a checkpoint against any integrity metadata in its spec."""
    expected_size = spec.expected_size_bytes
    expected_sha256 = spec.expected_sha256
    if expected_size is None and expected_sha256 is None:
        return

    actual_size = os.path.getsize(path)
    if expected_size is not None and actual_size != expected_size:
        raise ValueError(
            f"Model weights '{spec.filename}' failed integrity check: "
            f"expected {expected_size} bytes, found {actual_size} bytes at {path}."
        )

    if expected_sha256 is not None:
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError(
                f"Model weights '{spec.filename}' failed integrity check: "
                f"expected sha256 {expected_sha256}, found {actual_sha256} at {path}."
            )


def _ensure_weights(spec: ModelSpec, model_dir: str) -> str:
    """Return the local path to the checkpoint, downloading if necessary."""
    local_path = os.path.join(model_dir, spec.filename)
    if os.path.exists(local_path):
        _validate_weights(local_path, spec)
        return local_path

    os.makedirs(model_dir, exist_ok=True)
    temp_path: str | None = None
    try:
        import shutil
        import urllib.request

        _log.info("Downloading model weights: %s from %s", spec.filename, spec.weights_url)
        with urllib.request.urlopen(spec.weights_url, context=_https_context()) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=model_dir,
                prefix=f".{spec.filename}.",
                suffix=".tmp",
                delete=False,
            ) as out_file:
                temp_path = out_file.name
                shutil.copyfileobj(response, out_file)
        _validate_weights(temp_path, spec)
        os.replace(temp_path, local_path)
        temp_path = None
        return local_path
    except ValueError:
        raise
    except Exception as exc:
        raise FileNotFoundError(
            f"Model weights '{spec.filename}' not found in {model_dir} and "
            f"automatic download failed ({exc}). Download the checkpoint "
            f"manually from {spec.weights_url} and place it at {local_path}."
        ) from exc
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _load_state_dict(path: str) -> dict:
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        # Community MSST checkpoints are plain tensor state dicts, but some
        # were pickled with a wider payload than weights_only=True accepts.
        # These are pinned checkpoint files (registry.py), not arbitrary
        # user input, so the unsafe reload is acceptable here.
        state = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(state, dict):
        for key in ("state_dict", "model_state_dict", "state"):
            nested = state.get(key)
            if isinstance(nested, dict):
                state = nested
                break
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

    if model_filename == "model_scnet_ep_36_sdr_10.0891.ckpt" and device.type == "mps":
        _log.warning(
            "SCNet XL IHF is not reliable on MPS; falling back to CPU for %s",
            model_filename,
        )
        device = torch.device("cpu")

    weights_path = _ensure_weights(spec, model_dir)
    model = _build_arch(spec, config, device)

    state = _load_state_dict(weights_path)
    # Loaded on CPU first, then moved to the target device — mirrors the
    # upstream loading order (some ops misbehave when a state dict is
    # loaded directly onto an accelerator).
    model.load_state_dict(state)
    model.to(device).eval()

    return model, config
