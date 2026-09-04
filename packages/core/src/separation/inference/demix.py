"""Chunked overlap-add inference loops for the production model families.

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

from collections.abc import Callable, Collection

import numpy as np
import torch
from torch.nn import functional as F
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


def _scnet_window(size: int, fade: int) -> torch.Tensor:
    window = torch.ones(size, dtype=torch.float32)
    if fade:
        window[:fade] = torch.linspace(0.0, 1.0, fade)
        window[-fade:] = torch.linspace(1.0, 0.0, fade)
    return window


def _scnet_selected_instruments(
    config: ModelConfig, wanted: Collection[str] | None
) -> tuple[tuple[int, str], ...]:
    """Return configured SCNet stems selected by an optional canonical set."""
    if wanted is None:
        requested = None
    else:
        requested = {str(name).casefold() for name in wanted}
    return tuple(
        (index, name)
        for index, name in enumerate(config.instruments)
        if requested is None or name.casefold() in requested
    )


@torch.inference_mode()
def demix_scnet_stream(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    chunk_size: int | None = None,
    overlap: int | None = None,
    batch_size: int = 1,
    wanted: Collection[str] | None = None,
    frame_callback: Callable[[str, np.ndarray], None] | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[str, ...]:
    """Run SCNet overlap-add while emitting finalized frames in order.

    The model still returns every trained source, but only ``wanted`` sources
    are accumulated and emitted.  ``frame_callback`` receives channel-first
    float32 blocks; the final block sequence is trimmed to the unpadded input.
    """
    if mix.ndim != 2:
        raise ValueError(f"SCNet expects channel-first audio, got shape {mix.shape}")
    if not config.instruments:
        raise ValueError("SCNet config must declare at least one instrument")

    mix_t = torch.as_tensor(mix, dtype=torch.float32)
    original_length = mix_t.shape[-1]
    chunk_size = chunk_size or config.chunk_size
    overlap = overlap or config.num_overlap
    if chunk_size < 1 or overlap < 1:
        raise ValueError("SCNet chunk_size and overlap must be at least 1")
    selected = _scnet_selected_instruments(config, wanted)
    selected_names = tuple(name for _, name in selected)
    if not selected:
        if progress_callback is not None:
            progress_callback(1.0)
        return ()
    if frame_callback is None:
        raise ValueError("frame_callback is required for streaming SCNet inference")

    step = max(1, chunk_size // overlap)
    border = chunk_size - step
    fade = chunk_size // 10
    window = _scnet_window(chunk_size, fade)

    if original_length > 2 * border and border:
        mix_t = F.pad(mix_t, (border, border), mode="reflect")
        trim_start = border
    else:
        trim_start = 0
    padded_length = mix_t.shape[-1]
    trim_end = trim_start + original_length

    # The ring holds exactly one model chunk.  Once a new chunk starts, all
    # samples before that start have received their final overlap contribution.
    result = torch.zeros(
        (len(selected), mix_t.shape[0], chunk_size), dtype=torch.float32
    )
    weight = torch.zeros(chunk_size, dtype=torch.float32)
    head = 0
    buffer_start = 0

    def _ring_slice(offset: int, length: int) -> list[tuple[int, int]]:
        physical = (head + offset) % chunk_size
        first = min(length, chunk_size - physical)
        parts = [(physical, first)]
        if first < length:
            parts.append((0, length - first))
        return parts

    def _flush(end: int) -> None:
        nonlocal head, buffer_start
        if end < buffer_start or end - buffer_start > chunk_size:
            raise RuntimeError("SCNet streaming buffer advanced out of bounds")
        length = end - buffer_start
        if length == 0:
            return
        emit_start = max(buffer_start, trim_start)
        emit_end = min(end, trim_end)
        if emit_start < emit_end:
            offset = emit_start - buffer_start
            emit_length = emit_end - emit_start
            emitted = 0
            for physical, part_length in _ring_slice(offset, emit_length):
                values = (
                    result[..., physical : physical + part_length]
                    / weight[physical : physical + part_length].clamp(min=1e-10)
                ).numpy()
                for selected_index, (_, name) in enumerate(selected):
                    frame_callback(
                        name, np.asarray(values[selected_index], dtype=np.float32)
                    )
                emitted += part_length
            if emitted != emit_length:
                raise RuntimeError("SCNet streaming flush emitted an incomplete block")

        for physical, part_length in _ring_slice(0, length):
            result[..., physical : physical + part_length].zero_()
            weight[physical : physical + part_length].zero_()
        head = (head + length) % chunk_size
        buffer_start = end

    batches: list[torch.Tensor] = []
    locations: list[tuple[int, int]] = []
    position = 0
    completed = 0
    total = (padded_length + step - 1) // step
    step_size = max(1, batch_size)

    while position < padded_length:
        part = mix_t[:, position : position + chunk_size]
        segment_length = part.shape[-1]
        mode = "reflect" if segment_length > chunk_size // 2 else "constant"
        part = F.pad(part, (0, chunk_size - segment_length), mode=mode)
        batches.append(part)
        locations.append((position, segment_length))
        position += step

        if len(batches) < step_size and position < padded_length:
            continue
        estimated = model(torch.stack(batches).to(device))
        if (
            estimated.ndim != 4
            or estimated.shape[1] != len(config.instruments)
            or estimated.shape[2] != mix_t.shape[0]
        ):
            raise ValueError(
                "SCNet output must have shape (batch, instruments, channels, samples)"
            )
        for index, (start, segment_length) in enumerate(locations):
            if start > buffer_start:
                _flush(start)
            output = estimated[index, ..., :segment_length].cpu()
            if output.shape[-1] != segment_length:
                output = F.pad(output, (0, segment_length - output.shape[-1]))
            fade_window = window[:segment_length].clone()
            if start == 0:
                fade_window[:fade] = 1.0
            if start + segment_length == padded_length:
                fade_window[-fade:] = 1.0
            offset = 0
            for physical, part_length in _ring_slice(0, segment_length):
                part_fade = fade_window[offset : offset + part_length]
                for selected_index, (source_index, _) in enumerate(selected):
                    result[selected_index, ..., physical : physical + part_length] += (
                        output[source_index, ..., offset : offset + part_length]
                        * part_fade
                    )
                weight[physical : physical + part_length] += part_fade
                offset += part_length
            next_start = (
                locations[index + 1][0] if index + 1 < len(locations) else position
            )
            _flush(min(next_start, padded_length))
        completed += len(locations)
        batches.clear()
        locations.clear()
        if progress_callback is not None:
            progress_callback(min(1.0, completed / total))

    return selected_names


@torch.inference_mode()
def demix_scnet(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    chunk_size: int | None = None,
    overlap: int | None = None,
    batch_size: int = 1,
    progress_callback: Callable[[float], None] | None = None,
) -> dict[str, np.ndarray]:
    """Run SCNet's sample-domain overlap-add inference.

    SCNet emits ``(batch, sources, channels, samples)`` and uses a longer
    sample chunk than the Roformer/TFC-TDF frame-based loops.  The padding,
    fade, and border handling mirror the published MSST inference path.
    """
    if mix.ndim != 2:
        raise ValueError(f"SCNet expects channel-first audio, got shape {mix.shape}")
    if not config.instruments:
        raise ValueError("SCNet config must declare at least one instrument")

    mix_t = torch.as_tensor(mix, dtype=torch.float32)
    original_length = mix_t.shape[-1]
    chunk_size = chunk_size or config.chunk_size
    overlap = overlap or config.num_overlap
    if chunk_size < 1 or overlap < 1:
        raise ValueError("SCNet chunk_size and overlap must be at least 1")
    step = max(1, chunk_size // overlap)
    border = chunk_size - step
    fade = chunk_size // 10
    window = _scnet_window(chunk_size, fade)

    if original_length > 2 * border and border:
        mix_t = F.pad(mix_t, (border, border), mode="reflect")

    result = torch.zeros((len(config.instruments), *mix_t.shape), dtype=torch.float32)
    counter = torch.zeros_like(result)
    batches: list[torch.Tensor] = []
    locations: list[tuple[int, int]] = []
    position = 0
    completed = 0
    total = (mix_t.shape[-1] + step - 1) // step

    while position < mix_t.shape[-1]:
        part = mix_t[:, position : position + chunk_size]
        segment_length = part.shape[-1]
        mode = "reflect" if segment_length > chunk_size // 2 else "constant"
        part = F.pad(part, (0, chunk_size - segment_length), mode=mode)
        batches.append(part)
        locations.append((position, segment_length))
        position += step

        if len(batches) < max(1, batch_size) and position < mix_t.shape[-1]:
            continue
        estimated = model(torch.stack(batches).to(device))
        if (
            estimated.ndim != 4
            or estimated.shape[1] != len(config.instruments)
            or estimated.shape[2] != mix_t.shape[0]
        ):
            raise ValueError(
                "SCNet output must have shape (batch, instruments, channels, samples)"
            )
        for index, (start, segment_length) in enumerate(locations):
            output = estimated[index, ..., :segment_length].cpu()
            if output.shape[-1] != segment_length:
                output = F.pad(output, (0, segment_length - output.shape[-1]))
            fade_window = window[:segment_length].clone()
            if start == 0:
                fade_window[:fade] = 1.0
            if start + segment_length == mix_t.shape[-1]:
                fade_window[-fade:] = 1.0
            result[..., start : start + segment_length] += output * fade_window
            counter[..., start : start + segment_length] += fade_window
        completed += len(locations)
        batches.clear()
        locations.clear()
        if progress_callback is not None:
            progress_callback(min(1.0, completed / total))

    estimated = (result / counter.clamp(min=1e-10)).numpy()
    if original_length > 2 * border and border:
        estimated = estimated[..., border:-border]
    estimated = estimated[..., :original_length]
    return dict(zip(config.instruments, estimated))


@torch.inference_mode()
def demix_roformer(
    model: torch.nn.Module,
    mix: np.ndarray,
    config: ModelConfig,
    device: torch.device,
    segment_size: int | None,
    overlap: int = 2,
    batch_size: int = 1,
    progress_callback: Callable[[float], None] | None = None,
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
        if progress_callback is not None:
            progress_callback(min(1.0, (batch_start + len(batch_starts)) / len(starts)))

    inferenced = (result / counter.clamp(min=1e-10)).numpy()

    if num_stems == 1:
        primary = match_length(inferenced, orig_n_samples)
        return {
            config.target_instrument: primary,
            _secondary_name(config): mix - primary,
        }

    trimmed = (
        match_length(inferenced, orig_n_samples)
        if n_samples != orig_n_samples
        else inferenced
    )
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
    progress_callback: Callable[[float], None] | None = None,
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
        torch.zeros(num_stems, *padded.shape)
        if num_stems > 1
        else torch.zeros_like(padded)
    )

    count = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size].to(
            device, non_blocking=use_async_transfer
        )
        output = model(batch)
        for single in output:
            accumulated[..., count * hop_size : count * hop_size + chunk_size] += (
                single.cpu()
            )
            count += 1
        if progress_callback is not None:
            progress_callback(min(1.0, (start + len(batch)) / len(chunks)))

    inferenced = (
        accumulated[..., chunk_size - hop_size : -(pad_size + chunk_size - hop_size)]
        / overlap
    ).numpy()

    if num_stems > 1:
        return dict(zip(config.instruments, inferenced))

    primary = match_length(inferenced, n_samples)
    if config.target_instrument:
        return {
            config.target_instrument: primary,
            _secondary_name(config): mix - primary,
        }
    return {config.instruments[0]: primary}
