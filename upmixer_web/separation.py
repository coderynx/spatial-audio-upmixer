"""Web-layer checks for optional audio-separator support."""

from __future__ import annotations

import importlib.util
import logging
import platform
import sys
from pathlib import Path
from typing import Any


_INSTALL_MESSAGE = (
    "Stem separation is unavailable. Install "
    "'upmixer[separation-cpu]' for macOS or CPU; use "
    "'upmixer[separation-gpu]' only for NVIDIA CUDA."
)

_UNSUPPORTED_RUNTIME_MESSAGE = (
    "Stem separation is unavailable on Python 3.14 or newer. "
    "Use Python 3.11, 3.12, or 3.13."
)


def separation_capability(work_dir: Path) -> dict[str, Any]:
    """Probe audio-separator once without loading a separation model."""
    system = platform.system().lower()
    apple_silicon = system == "darwin" and platform.machine().lower() == "arm64"
    capability: dict[str, Any] = {
        "available": False,
        "backend": None,
        "accelerated": False,
        "accelerator_detected": apple_silicon,
        "accelerator_issue": None,
        "platform": system,
        "install_message": None,
    }
    if sys.version_info >= (3, 14):
        capability["install_message"] = _UNSUPPORTED_RUNTIME_MESSAGE
        return capability
    if importlib.util.find_spec("audio_separator") is None:
        capability["install_message"] = _INSTALL_MESSAGE
        return capability

    try:
        from audio_separator.separator import Separator

        probe_dir = work_dir / "audio-separator-probe"
        separator = Separator(
            model_file_dir=str(probe_dir / "models"),
            output_dir=str(probe_dir / "output"),
            log_level=logging.ERROR,
        )
    except Exception as exc:
        capability["install_message"] = f"Stem separation is unavailable: {exc}"
        return capability

    device = str(getattr(separator, "torch_device", "cpu")).lower()
    if device.startswith("cuda"):
        backend = "cuda"
    elif device.startswith("mps"):
        backend = "mps"
    else:
        backend = "cpu"
    capability.update(
        available=True,
        backend=backend,
        accelerated=backend in {"cuda", "mps"},
        accelerator_detected=backend in {"cuda", "mps"} or apple_silicon,
    )
    if apple_silicon and backend == "cpu":
        capability["accelerator_issue"] = (
            "Apple GPU detected, but audio-separator could not enable MPS; "
            "stem separation will use CPU."
        )
    return capability
