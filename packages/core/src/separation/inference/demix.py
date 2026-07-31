"""Chunked overlap-add inference loops for the two production model families.

Adapted from python-audio-separator's ``MDXCSeparator.demix`` (MIT license),
targeting the vendored archs in ``inference/archs/`` and this engine's
``ModelConfig``. Both loops accumulate in torch on CPU, so results do not
depend on the inference device.

For single-target models (``config.target_instrument`` set, e.g. the crowd
and karaoke models), the arch's ``forward`` squeezes away the stem
dimension entirely (see ``MelBandRoformer.forward``, ``num_stems == 1``
branch) — its output is a plain ``(2, samples)`` waveform, not a
single-element stem axis. The accumulator is sized to match that squeeze
directly, and the untrained second stem is recovered as the residual
against the original mix, matching upstream's ``orig_mix - primary`` rule.
"""
from __future__ import annotations

import numpy as np
import torch
from scipy import signal

from .config import ModelConfig


def _secondary_name(config: ModelConfig) -> str:
    return next(n for n in config.instruments if n != config.target_instrument)


def match_length(source: np.ndarray, n_samples: int) -> np.ndarray:
    """Trim or zero-pad ``source`` along the time axis to ``n_samples``."""
    current = source.shape[-1]
    if current == n_samples:
        return source
    if current > n_samples:
        return source[..., :n_samples]
    pad_width = [(0, 0)] * (source.ndim - 1) + [(0, n_samples - current)]
    return np.pad(source, pad_width)


@torch.inference_mode()
def demix_roformer(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    segment_size: int | None,
    overlap: int = 2,
    batch_size: int = 1,
) -> dict[str, np.ndarray]:
    """Chunked inference for BS-Roformer / Mel-Band Roformer models.

    Args:
        mix: ``(2, n_samples)`` float32 mix, already peak-normalized.
        segment_size: STFT frames per chunk (``dim_t``). ``None`` uses the
            model's own default from the config.
        overlap: Overlapping windows per chunk length (community default 2;
            ``1`` disables overlap and advances one full chunk per step).
            Higher values blend chunk boundaries more but cost linearly more
            compute.
        batch_size: Chunks processed per forward pass — a memory/speed
            knob. Chunks are independent (no cross-item norm or state) and
            are always accumulated back in the same left-to-right order
            regardless of batch size, so grouping them for one forward
            call doesn't change which values land where.

    Returns:
        Canonical instrument name -> ``(2, n_samples)`` float32 array.
    """
    mix_t = torch.tensor(mix, dtype=torch.float32)
    orig_n_samples = mix_t.shape[1]

    dim_t = segment_size if segment_size is not None else config.default_segment_size
    chunk_size = config.stft_hop_length * (dim_t - 1)

    if orig_n_samples < chunk_size:
        # Model chunk length can exceed short-clip duration (e.g. these
        # models' default segment sizes span 8-11s of audio). Zero-pad to
        # one full chunk so the loop below always has real work to do, then
        # trim the result back to the original length.
        mix_t = torch.nn.functional.pad(mix_t, (0, chunk_size - orig_n_samples))
    n_samples = mix_t.shape[1]

    # Pinning overlaps each chunk's host->device copy with prior GPU work; the
    # device->host reads below stay synchronous, since non_blocking there
    # would race. CUDA/ROCm only: MPS ignores non_blocking, CPU never copies.
    use_async_transfer = device.type == "cuda"
    if use_async_transfer:
        mix_t = mix_t.pin_memory()

    step = max(1, chunk_size // max(1, overlap))

    window = torch.tensor(signal.windows.hamming(chunk_size), dtype=torch.float32)

    num_stems = config.num_stems
    acc_shape = mix_t.shape if num_stems == 1 else (num_stems, *mix_t.shape)
    result = torch.zeros(acc_shape, dtype=torch.float32)
    counter = torch.zeros(acc_shape, dtype=torch.float32)

    # Chunk start is always either the window position `i` or, for the
    # final overlapping window, pulled back to end exactly at n_samples
    # (matching the single-chunk loop this replaces) — the result slot is
    # the same offset, so one list covers both reading and placement.
    starts = [
        i if i + chunk_size <= n_samples else n_samples - chunk_size
        for i in range(0, n_samples, step)
    ]
    max_safe_len = min(chunk_size, window.shape[0])
    step_size = max(1, batch_size)

    for batch_start in range(0, len(starts), step_size):
        batch_starts = starts[batch_start : batch_start + step_size]
        batch = torch.stack([mix_t[:, s : s + chunk_size] for s in batch_starts], dim=0)
        outputs = model(batch.to(device, non_blocking=use_async_transfer))

        for s, out in zip(batch_starts, outputs):
            out = out.cpu()
            safe_len = min(max_safe_len, out.shape[-1])
            if safe_len > 0:
                result[..., s : s + safe_len] += out[..., :safe_len] * window[:safe_len]
                counter[..., s : s + safe_len] += window[:safe_len]

    inferenced = (result / counter.clamp(min=1e-10)).numpy()

    if num_stems == 1:
        primary = match_length(inferenced, orig_n_samples)
        return {config.target_instrument: primary, _secondary_name(config): mix - primary}

    trimmed = match_length(inferenced, orig_n_samples) if n_samples != orig_n_samples else inferenced
    return dict(zip(config.instruments, trimmed))


@torch.inference_mode()
def demix_tfc_tdf(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    segment_size: int | None,
    overlap: int = 8,
    batch_size: int = 1,
) -> dict[str, np.ndarray]:
    """Chunked inference for TFC-TDF v3 (MDX23C) models.

    Args:
        mix: ``(2, n_samples)`` float32 mix, already peak-normalized.
        segment_size: STFT frames per chunk (``dim_t``). ``None`` uses the
            model's own default from the config.
        overlap: Overlapping windows per chunk length (hop = chunk // overlap).
        batch_size: Chunks processed per forward pass — purely a memory/speed
            knob. Accumulation is a plain ordered sum, so the result is
            invariant to batch size.

    Returns:
        Canonical instrument name -> ``(2, n_samples)`` float32 array.
    """
    mix_t = torch.tensor(mix, dtype=torch.float32)
    n_samples = mix_t.shape[1]

    dim_t = segment_size if segment_size is not None else config.default_segment_size
    chunk_size = config.hop_length * (dim_t - 1)
    hop_size = chunk_size // overlap

    pad_size = hop_size - (n_samples - chunk_size) % hop_size
    padded = torch.cat(
        [
            torch.zeros(2, chunk_size - hop_size),
            mix_t,
            torch.zeros(2, pad_size + chunk_size - hop_size),
        ],
        dim=1,
    )

    # See demix_roformer's matching comment: pinning + non_blocking only
    # overlaps the host->device copy; the device->host read below stays
    # synchronous, so accumulation always sees completed data.
    use_async_transfer = device.type == "cuda"
    if use_async_transfer:
        padded = padded.pin_memory()

    chunks = padded.unfold(1, chunk_size, hop_size).transpose(0, 1)
    num_stems = config.num_stems
    accumulated = (
        torch.zeros(num_stems, *padded.shape) if num_stems > 1 else torch.zeros_like(padded)
    )

    count = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size].to(device, non_blocking=use_async_transfer)
        output = model(batch)
        for single in output:
            accumulated[..., count * hop_size : count * hop_size + chunk_size] += single.cpu()
            count += 1

    inferenced = (
        accumulated[..., chunk_size - hop_size : -(pad_size + chunk_size - hop_size)] / overlap
    ).numpy()

    if num_stems > 1:
        return dict(zip(config.instruments, inferenced))

    primary = match_length(inferenced, n_samples)
    if config.target_instrument:
        return {config.target_instrument: primary, _secondary_name(config): mix - primary}
    return {config.instruments[0]: primary}
