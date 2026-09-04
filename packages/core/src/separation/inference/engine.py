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
from collections.abc import Callable, Collection
from fractions import Fraction

import numpy as np
import torch
from scipy import signal

from . import audio_io, demix
from .config import ModelConfig
from .device import DeviceManager

_log = logging.getLogger(__name__)

_ARCH_ROFORMER = frozenset({"bs_roformer", "mel_band_roformer"})
_CROSSFADE_S = 1.0
_PITCH_SHIFT_MAX_DENOMINATOR = 64


class SeparationEngine:
    """Runs one loaded model's demix over one audio file.

    Args:
        model: Loaded Torch architecture or Torch-facing MLX adapter, already
            on ``device.torch_device`` and in eval mode.
        config: The model's parsed :class:`ModelConfig`.
        arch: Architecture family — selects the Roformer, TFC-TDF, or SCNet demix loop.
        model_filename: Original checkpoint filename, used to build output
            filenames in python-audio-separator's convention.
        device: DeviceManager the model was loaded onto.
        output_dir: Directory stems are written to.
        sample_rate: Working sample rate; the mix is resampled to this.
        batch_size: Chunks processed per forward pass. Applies to both
            TFC-TDF and Roformer models, except Roformer always forces 1
            on the CPU backend regardless of this value (see
            ``_demix_one``) to bound peak memory on low-end hosts.
        segment_size: Chunk frame count override, or ``None`` to use the
            registry's per-model ``default_chunk_samples`` sweet spot (if
            any), falling back to the model's own YAML default.
        chunk_duration_s: Long-file splitting window, or ``None`` to
            process the whole file in one pass. Bounds peak memory on
            low-end/no-GPU hosts; segments are joined with a short linear
            crossfade so window boundaries don't produce audible steps.
        overlap: Overlapping windows per demix chunk, or ``None`` for the
            arch's community-default (2 for Roformer, 8 for TFC-TDF).
        default_chunk_samples: Registry-published sweet-spot chunk length in
            samples for this checkpoint, or ``None``. Only used when
            ``segment_size`` is ``None``.
        tta: Test-time augmentation — average predictions over polarity and
            channel-swap variants for a small SDR gain at ~3x cost. Off by
            default.
        pitch_shift: Optional rescue trick for content the model handles
            poorly at its native register (e.g. sopranos, deep male vocals):
            resample the mix by this ratio before demix and back afterward,
            fully reversible. ``None`` disables it.
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
        overlap: int | None = None,
        default_chunk_samples: int | None = None,
        tta: bool = False,
        pitch_shift: float | None = None,
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
        self._overlap = overlap
        self._default_chunk_samples = default_chunk_samples
        self._tta = tta
        self._pitch_shift = pitch_shift
        self._last_parent: np.ndarray | None = None

    def separate(
        self,
        audio_path: str,
        retain_parent: bool = False,
        progress_callback: Callable[[float], None] | None = None,
        wanted: Collection[str] | None = None,
    ) -> list[str]:
        """Separate ``audio_path``, writing one WAV per stem to output_dir.

        Stems are written in the input file's level domain — the pre-demix
        peak normalization is divided back out — so they sum to the input
        rather than to an arbitrary per-stem peak.

        When ``retain_parent`` is true, :meth:`take_last_parent` exposes the
        exact resampled input in its original level domain after the run.
        ``wanted`` optionally limits which configured stems are written.
        Returns the list of written file paths.
        """
        mix = audio_io.load_audio(audio_path, self._sample_rate)
        self._last_parent = np.ascontiguousarray(mix.T) if retain_parent else None
        mix, input_scale = audio_io.normalize(mix)

        started = time.monotonic()
        audio_base = os.path.splitext(os.path.basename(audio_path))[0]
        if self._can_stream_scnet():
            paths = self._separate_scnet_stream(
                mix,
                input_scale,
                audio_base,
                wanted,
                progress_callback,
            )
            _log.debug(
                "  Engine model=%s demix=%.2fs",
                self._model_filename,
                time.monotonic() - started,
            )
            return paths

        sources = self._demix_with_chunking(mix, progress_callback)
        _log.debug(
            "  Engine model=%s demix=%.2fs",
            self._model_filename,
            time.monotonic() - started,
        )

        os.makedirs(self._output_dir, exist_ok=True)
        paths = []
        requested = None
        if self._arch == "scnet" and wanted is not None:
            requested = {str(name).casefold() for name in wanted}
        for stem_name, stem_audio in sources.items():
            if requested is not None and stem_name.casefold() not in requested:
                continue
            path = audio_io.stem_output_path(
                self._output_dir, audio_base, stem_name, self._model_filename
            )
            audio_io.write_stem(path, stem_audio / input_scale, self._sample_rate)
            paths.append(path)
        return paths

    def _can_stream_scnet(self) -> bool:
        """Return whether this run can use the bounded SCNet writer path."""
        return (
            self._arch == "scnet"
            and not self._tta
            and self._pitch_shift is None
            and self._chunk_duration_s is None
        )

    def _scnet_parameters(self) -> tuple[int, int]:
        chunk_size = (
            self._segment_size * self._config.hop_length
            if self._segment_size is not None
            else self._config.chunk_size
        )
        overlap = (
            self._overlap if self._overlap is not None else self._config.num_overlap
        )
        return chunk_size, overlap

    def _separate_scnet_stream(
        self,
        mix: np.ndarray,
        input_scale: float,
        audio_base: str,
        wanted: Collection[str] | None,
        progress_callback: Callable[[float], None] | None,
    ) -> list[str]:
        requested = (
            None if wanted is None else {str(name).casefold() for name in wanted}
        )
        stem_names = tuple(
            name
            for name in self._config.instruments
            if requested is None or name.casefold() in requested
        )
        if not stem_names:
            if progress_callback is not None:
                progress_callback(1.0)
            return []

        os.makedirs(self._output_dir, exist_ok=True)
        paths = {
            name: audio_io.stem_output_path(
                self._output_dir, audio_base, name, self._model_filename
            )
            for name in stem_names
        }
        writers: dict[str, audio_io.AtomicWavWriter] = {}
        try:
            for name, path in paths.items():
                writers[name] = audio_io.AtomicWavWriter(
                    path, self._sample_rate, mix.shape[0]
                )

            def write_frame(name: str, frame: np.ndarray) -> None:
                writers[name].write(np.asarray(frame / input_scale, dtype=np.float32))

            chunk_size, overlap = self._scnet_parameters()
            demix.demix_scnet_stream(
                self._model,
                mix,
                self._config,
                self._model_device(),
                chunk_size=chunk_size,
                overlap=overlap,
                batch_size=self._batch_size,
                wanted=stem_names,
                frame_callback=write_frame,
                progress_callback=progress_callback,
            )
            for writer in writers.values():
                writer.commit()
        except Exception:
            for writer in writers.values():
                writer.abort()
            raise
        return list(paths.values())

    def take_last_parent(self) -> np.ndarray:
        """Return and release the exact resampled input of the last run."""
        if self._last_parent is None:
            raise RuntimeError("No completed separation input is available")
        parent = self._last_parent
        self._last_parent = None
        return parent

    def _demix_with_chunking(
        self, mix: np.ndarray, progress_callback: Callable[[float], None] | None = None
    ) -> dict[str, np.ndarray]:
        n_samples = mix.shape[1]
        if self._chunk_duration_s is None:
            return (
                self._demix_one(mix)
                if progress_callback is None
                else self._demix_one(mix, progress_callback)
            )
        window = int(self._chunk_duration_s * self._sample_rate)
        if n_samples <= window:
            return (
                self._demix_one(mix)
                if progress_callback is None
                else self._demix_one(mix, progress_callback)
            )

        crossfade = min(int(_CROSSFADE_S * self._sample_rate), window // 4)
        hop = window - crossfade

        segments: list[tuple[int, int, dict[str, np.ndarray]]] = []
        start = 0
        total_segments = 1 + (n_samples - window + hop - 1) // hop
        while True:
            end = min(start + window, n_samples)
            segment_index = len(segments)
            segments.append(
                (
                    start,
                    end,
                    self._demix_one(
                        mix[:, start:end],
                        None
                        if progress_callback is None
                        else lambda fraction: progress_callback(
                            (segment_index + fraction) / total_segments
                        ),
                    ),
                )
            )
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
                    fade[:crossfade] *= np.linspace(
                        0.0, 1.0, crossfade, dtype=np.float32
                    )
                if end < n_samples:
                    fade[-crossfade:] *= np.linspace(
                        1.0, 0.0, crossfade, dtype=np.float32
                    )
                acc[:, start:end] += seg[name] * fade
                weight[start:end] += fade
            result[name] = acc / np.clip(weight, 1e-10, None)
        return result

    def _demix_one(
        self, mix: np.ndarray, progress_callback: Callable[[float], None] | None = None
    ) -> dict[str, np.ndarray]:
        if self._pitch_shift is not None:
            return self._demix_with_pitch(mix, progress_callback)
        return self._demix_with_tta(mix, progress_callback)

    def _demix_with_pitch(
        self, mix: np.ndarray, progress_callback: Callable[[float], None] | None = None
    ) -> dict[str, np.ndarray]:
        # Fake-sample-rate rescue trick: resampling by a rational factor
        # moves the mix into a register the model was trained on (pitch
        # down for sopranos/high material, up for deep vocals) and is fully
        # reversible, unlike a true pitch shift.
        n_samples = mix.shape[1]
        ratio = Fraction(self._pitch_shift).limit_denominator(
            _PITCH_SHIFT_MAX_DENOMINATOR
        )
        up, down = ratio.numerator, ratio.denominator
        shifted = signal.resample_poly(mix, up, down, axis=-1).astype(np.float32)
        stems = (
            self._demix_with_tta(shifted)
            if progress_callback is None
            else self._demix_with_tta(shifted, progress_callback)
        )
        return {
            name: demix.match_length(
                signal.resample_poly(stem, down, up, axis=-1).astype(np.float32),
                n_samples,
            )
            for name, stem in stems.items()
        }

    def _demix_with_tta(
        self, mix: np.ndarray, progress_callback: Callable[[float], None] | None = None
    ) -> dict[str, np.ndarray]:
        if not self._tta:
            return (
                self._demix_arch(mix)
                if progress_callback is None
                else self._demix_arch(mix, progress_callback)
            )
        # Averaging invertible variants (polarity, channel order) is a cheap
        # approximation of proper TTA — each variant is fed through the same
        # model and its stems un-transformed before averaging.
        variants = (
            (mix, lambda s: s),
            (-mix, lambda s: -s),
            (np.ascontiguousarray(mix[::-1]), lambda s: s[::-1]),
        )
        totals: dict[str, np.ndarray] = {}
        for index, (variant_mix, inverse) in enumerate(variants):
            callback = (
                None
                if progress_callback is None
                else lambda fraction: progress_callback(
                    (index + fraction) / len(variants)
                )
            )
            stems = (
                self._demix_arch(variant_mix)
                if callback is None
                else self._demix_arch(variant_mix, callback)
            )
            for name, stem in stems.items():
                restored = inverse(stem)
                totals[name] = totals.get(name, 0.0) + restored
        return {name: total / len(variants) for name, total in totals.items()}

    def _demix_arch(
        self, mix: np.ndarray, progress_callback: Callable[[float], None] | None = None
    ) -> dict[str, np.ndarray]:
        torch_device = self._model_device()
        if self._arch == "scnet":
            scnet_chunk_size, scnet_overlap = self._scnet_parameters()
            return demix.demix_scnet(
                self._model,
                mix,
                self._config,
                torch_device,
                chunk_size=scnet_chunk_size,
                overlap=scnet_overlap,
                batch_size=self._batch_size,
                progress_callback=progress_callback,
            )

        segment_size = self._resolved_segment_size()
        overlap = self._overlap
        if self._arch in _ARCH_ROFORMER:
            # CUDA/ROCm have isolated VRAM and an OOM-retry ladder, so they
            # use the tuned/caller batch size. Every other backend shares
            # memory with the OS and stays at 1 — batch=2 on MPS froze a real
            # M3 Pro (unified-memory pressure, not a catchable OOM).
            roformer_batch_size = self._batch_size if torch_device.type == "cuda" else 1
            return demix.demix_roformer(
                self._model,
                mix,
                self._config,
                torch_device,
                segment_size=segment_size,
                overlap=overlap if overlap is not None else 2,
                batch_size=roformer_batch_size,
                progress_callback=progress_callback,
            )
        return demix.demix_tfc_tdf(
            self._model,
            mix,
            self._config,
            torch_device,
            segment_size=segment_size,
            overlap=overlap if overlap is not None else 8,
            batch_size=self._batch_size,
            progress_callback=progress_callback,
        )

    def _model_device(self) -> torch.device:
        """Use the model's actual placement when loading selected fallbacks."""
        try:
            return next(self._model.parameters()).device
        except StopIteration:
            return self._device.torch_device

    def _resolved_segment_size(self) -> int | None:
        if self._segment_size is not None or self._default_chunk_samples is None:
            return self._segment_size
        hop = (
            self._config.stft_hop_length
            if self._arch in _ARCH_ROFORMER
            else self._config.hop_length
        )
        return round(self._default_chunk_samples / hop) + 1
