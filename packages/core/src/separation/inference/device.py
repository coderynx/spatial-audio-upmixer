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
        if backend in ("cpu", "mps", "cuda"):
            self._apply_thread_cap()

    def _apply_thread_cap(self) -> None:
        """Leave one core free so host-side work doesn't starve the box.

        On CPU this bounds the whole job's intra-op parallelism. On
        MPS/CUDA it still matters: some archs fall back to CPU for
        STFT/complex-tensor work the accelerator can't do (e.g. the
        karaoke model's larger chunks on MPS), and an uncapped thread
        pool there can still saturate every core and stutter a shared
        low-end host. Stability over raw throughput, per the
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
