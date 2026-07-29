"""Orchestrates one full separation run: load, demix, write stems to disk.

Mirrors python-audio-separator's file-based contract (an audio path in,
output WAV file paths out) so ``separator.py`` only needs to swap what
builds the "separator" object — stem-name parsing and the pipeline's
disk-chaining in ``stem_pipeline.py`` are unchanged.
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np
import torch

from . import audio_io, demix
from .config import ModelConfig
from .device import DeviceManager

_log = logging.getLogger("upmixer")

_ARCH_ROFORMER = frozenset({"bs_roformer", "mel_band_roformer"})
_CROSSFADE_S = 1.0


class SeparationEngine:
    """Runs one loaded model's demix over one audio file.

    Args:
        model: Loaded architecture, already on ``device.torch_device`` and
            in eval mode (see ``loader.load_model``).
        config: The model's parsed :class:`ModelConfig`.
        arch: Architecture family — selects the Roformer or TFC-TDF demix loop.
        model_filename: Original checkpoint filename, used to build output
            filenames in python-audio-separator's convention.
        device: DeviceManager the model was loaded onto.
        output_dir: Directory stems are written to.
        sample_rate: Working sample rate; the mix is resampled to this.
        batch_size: Chunks processed per forward pass. Applies to both
            TFC-TDF and Roformer models, except Roformer always forces 1
            on the CPU backend regardless of this value (see
            ``_demix_one``) to bound peak memory on low-end hosts.
        segment_size: Chunk frame count override, or ``None`` for the
            model's own default.
        chunk_duration_s: Long-file splitting window, or ``None`` to
            process the whole file in one pass. Bounds peak memory on
            low-end/no-GPU hosts; segments are joined with a short linear
            crossfade so window boundaries don't produce audible steps.
    """

    def __init__(
        self,
        model: torch.nn.Module,
        config: ModelConfig,
        arch: str,
        model_filename: str,
        device: DeviceManager,
        output_dir: str,
        sample_rate: int,
        batch_size: int,
        segment_size: int | None,
        chunk_duration_s: float | None,
    ) -> None:
        self._model = model
        self._config = config
        self._arch = arch
        self._model_filename = model_filename
        self._device = device
        self._output_dir = output_dir
        self._sample_rate = sample_rate
        self._batch_size = batch_size
        self._segment_size = segment_size
        self._chunk_duration_s = chunk_duration_s

    def separate(self, audio_path: str) -> list[str]:
        """Separate ``audio_path``, writing one WAV per stem to output_dir.

        Returns the list of written file paths.
        """
        mix = audio_io.load_audio(audio_path, self._sample_rate)
        mix = audio_io.normalize(mix)

        started = time.monotonic()
        sources = self._demix_with_chunking(mix)
        _log.debug(
            "  Engine model=%s demix=%.2fs", self._model_filename, time.monotonic() - started
        )

        audio_base = os.path.splitext(os.path.basename(audio_path))[0]
        os.makedirs(self._output_dir, exist_ok=True)
        paths = []
        for stem_name, stem_audio in sources.items():
            path = audio_io.stem_output_path(
                self._output_dir, audio_base, stem_name, self._model_filename
            )
            audio_io.write_stem(path, stem_audio, self._sample_rate)
            paths.append(path)
        return paths

    def _demix_with_chunking(self, mix: np.ndarray) -> dict[str, np.ndarray]:
        n_samples = mix.shape[1]
        if self._chunk_duration_s is None:
            return self._demix_one(mix)
        window = int(self._chunk_duration_s * self._sample_rate)
        if n_samples <= window:
            return self._demix_one(mix)

        crossfade = min(int(_CROSSFADE_S * self._sample_rate), window // 4)
        hop = window - crossfade

        segments: list[tuple[int, int, dict[str, np.ndarray]]] = []
        start = 0
        while True:
            end = min(start + window, n_samples)
            segments.append((start, end, self._demix_one(mix[:, start:end])))
            if end == n_samples:
                break
            start += hop

        stem_names = segments[0][2].keys()
        result: dict[str, np.ndarray] = {}
        for name in stem_names:
            acc = np.zeros((2, n_samples), dtype=np.float32)
            weight = np.zeros(n_samples, dtype=np.float32)
            for start, end, seg in segments:
                length = end - start
                fade = np.ones(length, dtype=np.float32)
                if start > 0:
                    fade[:crossfade] *= np.linspace(0.0, 1.0, crossfade, dtype=np.float32)
                if end < n_samples:
                    fade[-crossfade:] *= np.linspace(1.0, 0.0, crossfade, dtype=np.float32)
                acc[:, start:end] += seg[name] * fade
                weight[start:end] += fade
            result[name] = acc / np.clip(weight, 1e-10, None)
        return result

    def _demix_one(self, mix: np.ndarray) -> dict[str, np.ndarray]:
        if self._arch in _ARCH_ROFORMER:
            # Roformer chunks span 8-11s of audio; batching them multiplies
            # peak activation memory linearly. CUDA/ROCm have isolated VRAM
            # and a proven OOM-retry ladder, so they use the auto-tuned or
            # caller-supplied batch size. Every other backend (CPU, MPS,
            # CoreML-as-CPU) shares memory with the OS and stays at 1 —
            # confirmed the hard way: batch=2 on MPS for the 11s karaoke
            # chunk froze a real M3 Pro (unified-memory pressure, not a
            # catchable OOM exception).
            roformer_batch_size = (
                self._batch_size if self._device.torch_device.type == "cuda" else 1
            )
            return demix.demix_roformer(
                self._model,
                mix,
                self._config,
                self._device.torch_device,
                segment_size=self._segment_size,
                batch_size=roformer_batch_size,
            )
        return demix.demix_tfc_tdf(
            self._model,
            mix,
            self._config,
            self._device.torch_device,
            segment_size=self._segment_size,
            batch_size=self._batch_size,
        )
