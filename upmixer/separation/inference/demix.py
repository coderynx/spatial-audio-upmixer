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


def _match_length(source: np.ndarray, n_samples: int) -> np.ndarray:
    """Trim or zero-pad ``source`` along the time axis to ``n_samples``."""
    current = source.shape[-1]
    if current == n_samples:
        return source
    if current > n_samples:
        return source[..., :n_samples]
    pad_width = [(0, 0)] * (source.ndim - 1) + [(0, n_samples - current)]
    return np.pad(source, pad_width)


@torch.no_grad()
def demix_roformer(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    segment_size: int | None,
    overlap_seconds: float = 8.0,
) -> dict[str, np.ndarray]:
    """Chunked inference for BS-Roformer / Mel-Band Roformer models.

    Args:
        mix: ``(2, n_samples)`` float32 mix, already peak-normalized.
        segment_size: STFT frames per chunk (``dim_t``). ``None`` uses the
            model's own default from the config.
        overlap_seconds: Desired step between chunk starts, in seconds of
            audio. Clamped to at most the chunk length — with the default
            (8s) and a chunk shorter than that, the loop advances one full
            chunk at a time, i.e. no actual overlap for these models.

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

    desired_step = int(overlap_seconds * config.sample_rate)
    step = chunk_size if desired_step <= 0 else min(desired_step, chunk_size)

    window = torch.tensor(signal.windows.hamming(chunk_size), dtype=torch.float32)

    num_stems = config.num_stems
    acc_shape = mix_t.shape if num_stems == 1 else (num_stems, *mix_t.shape)
    result = torch.zeros(acc_shape, dtype=torch.float32)
    counter = torch.zeros(acc_shape, dtype=torch.float32)

    for i in range(0, n_samples, step):
        part = mix_t[:, i : i + chunk_size]
        length = part.shape[-1]
        start = i
        if i + chunk_size > n_samples:
            part = mix_t[:, -chunk_size:]
            length = chunk_size
            start = result.shape[-1] - chunk_size

        out = model(part.unsqueeze(0).to(device))[0].cpu()

        safe_len = min(length, out.shape[-1], window.shape[0])
        if safe_len > 0:
            result[..., start : start + safe_len] += out[..., :safe_len] * window[:safe_len]
            counter[..., start : start + safe_len] += window[:safe_len]

    inferenced = (result / counter.clamp(min=1e-10)).numpy()

    if num_stems == 1:
        primary = _match_length(inferenced, orig_n_samples)
        return {config.target_instrument: primary, _secondary_name(config): mix - primary}

    trimmed = _match_length(inferenced, orig_n_samples) if n_samples != orig_n_samples else inferenced
    return dict(zip(config.instruments, trimmed))


@torch.no_grad()
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

    chunks = padded.unfold(1, chunk_size, hop_size).transpose(0, 1)
    num_stems = config.num_stems
    accumulated = (
        torch.zeros(num_stems, *padded.shape) if num_stems > 1 else torch.zeros_like(padded)
    )

    count = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size].to(device)
        output = model(batch)
        for single in output:
            accumulated[..., count * hop_size : count * hop_size + chunk_size] += single.cpu()
            count += 1

    inferenced = (
        accumulated[..., chunk_size - hop_size : -(pad_size + chunk_size - hop_size)] / overlap
    ).numpy()

    if num_stems > 1:
        return dict(zip(config.instruments, inferenced))

    primary = _match_length(inferenced, n_samples)
    if config.target_instrument:
        return {config.target_instrument: primary, _secondary_name(config): mix - primary}
    return {config.instruments[0]: primary}
