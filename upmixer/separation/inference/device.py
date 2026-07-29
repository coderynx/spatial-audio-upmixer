"""Device placement for the in-core separation inference engine.

Backend detection (which accelerator, if any, is available) stays in
``separator.py`` (``_detect_backend``) since it also drives batch/segment
auto-tuning there; this module only turns that backend string into a torch
device and owns the actions specific to holding one (cache clearing, CPU
thread bounds).
"""
from __future__ import annotations

import gc
import os

import torch

_TORCH_DEVICE_NAMES = {"cuda": "cuda", "mps": "mps"}


class DeviceManager:
    """Owns the torch device for one backend and its cleanup/tuning actions."""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.torch_device = torch.device(_TORCH_DEVICE_NAMES.get(backend, "cpu"))
        if backend == "cpu":
            self._apply_cpu_thread_cap()

    def _apply_cpu_thread_cap(self) -> None:
        """Leave one core free on CPU-only hosts.

        A separation job otherwise saturates every core via torch's
        intra-op parallelism; on a low-end server that also runs the web
        process in the same container, that starves everything else
        sharing the box. Stability over raw throughput here, per the
        no-GPU-server requirement.
        """
        cpu_count = os.cpu_count() or 1
        torch.set_num_threads(max(1, cpu_count - 1))

    def empty_cache(self) -> None:
        """Release accelerator memory after an OOM retry or on close."""
        gc.collect()
        if self.backend == "cuda":
            torch.cuda.empty_cache()
        elif self.backend == "mps":
            torch.mps.empty_cache()
