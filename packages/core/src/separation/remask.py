"""Soft-mask re-projection of separated children onto their parent stem.

A separation model partitions its input independently per output, so part of
the parent's energy reaches no child at all (drumsep collides in the
cymbal/hi-hat region; BS-Roformer-SW leaves 0.03-0.1% broadband). Soft masks
built from the children's own magnitudes redistribute that remainder instead
of discarding it, and make the children sum back to the parent by
construction.

Two ways to apply them: :func:`reproject_stems` re-derives every child from
the parent's spectrum, and :func:`share_parent_residual` keeps the model's own
output and splits only what it left over. Both stages run the latter.
Re-projection replaces the model's waveform output with a magnitude-ratio
approximation of it, which costs real stem quality where the model is already
good — see ``docs/reports/primary_remask.md`` and
``docs/reports/drum_remask.md``.

Pure STFT-domain DSP: arrays in, arrays out, no inference and no file I/O.
"""
from __future__ import annotations

import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig

# Both re-masked models declare the same STFT config: n_fft 2048 / hop 512
# (MDX23C-DrumSep audio.*, BS-Roformer-SW model.stft_* — its audio.hop_length
# of 441 is marked "don't work" upstream and is not what the model uses).
REMASK_FFT_SIZE = 2048
REMASK_HOP_SIZE = 512

_BLOCK_SAMPLES = 1 << 19
_SILENT_DENOM = 1e-20


def _block_weights(length: int, head_fade: int, tail_fade: int) -> np.ndarray:
    """Crossfade envelope; a block pair's weights sum to exactly 1 in overlap."""
    w = np.ones(length, dtype=np.float64)
    if head_fade:
        w[:head_fade] = np.linspace(0.0, 1.0, head_fade, endpoint=False)
    if tail_fade:
        w[length - tail_fade:] = np.linspace(1.0, 0.0, tail_fade, endpoint=False)
    return w


def _child_block(
    child: np.ndarray, ch: int, start: int, length: int
) -> np.ndarray:
    """One channel of a child over [start, start+length), zero-padded if short."""
    column = child[:, ch] if ch < child.shape[1] else child[:, 0]
    segment = np.asarray(column[start : start + length], dtype=np.float64)
    if len(segment) < length:
        segment = np.pad(segment, (0, length - len(segment)))
    return segment


def _remask_block(
    parent_block: np.ndarray,
    blocks: list[np.ndarray],
    analyzer: STFTAnalyzer,
    alpha: float,
) -> list[np.ndarray]:
    """Split one parent block among the children by their soft masks."""
    spec = analyzer.forward(parent_block)
    mags = [np.abs(analyzer.forward(block)) ** alpha for block in blocks]
    denom = np.sum(mags, axis=0)
    silent = denom < _SILENT_DENOM
    safe = np.where(silent, 1.0, denom)
    equal_share = 1.0 / len(blocks)
    return [
        analyzer.inverse(
            np.where(silent, equal_share, mag / safe) * spec, len(parent_block)
        )
        for mag in mags
    ]


def reproject_stems(
    parent: np.ndarray,
    children: dict[str, np.ndarray],
    sample_rate: int,
    alpha: float = 1.0,
) -> dict[str, np.ndarray]:
    """Re-derive children as soft masks on the parent's spectrogram.

    Each child becomes ``|S_i|^alpha / sum_j |S_j|^alpha`` applied to the
    parent's complex STFT, so the returned children sum back to *parent* to
    within STFT reconstruction error.

    Args:
        parent: ``(n_samples, channels)`` parent audio.
        children: Child name -> ``(n_samples, channels)`` model output.
            Children are aligned at sample 0 and clipped or zero-padded to the
            parent's length. A child the model did not produce is simply
            absent from the mapping, and its share of the parent goes to the
            rest. A model output the caller intends to discard can still be
            passed in to hold its own share out of the kept children — drop
            its key from the result.
        sample_rate: Sample rate of parent and children.
        alpha: Mask exponent, must be > 0. ``1.0`` is a plain ratio mask;
            below 1 shares overlapping bins more evenly between children;
            large values approach the model's own near-binary partition.

    Returns:
        Child name -> ``(n_samples, channels)`` array in the parent's dtype.
        Bins where every child is silent are split equally, so parent energy
        that no child claimed is still distributed rather than dropped.

    Raises:
        ValueError: If *alpha* is not positive or *parent* is not 2-D.
    """
    if alpha <= 0.0:
        raise ValueError("re-mask alpha must be > 0")
    original = np.asarray(parent)
    if original.ndim != 2:
        raise ValueError("parent must be 2-D (n_samples, channels)")
    if not children:
        return {}

    names = sorted(children)
    for name in names:
        if np.asarray(children[name]).ndim != 2:
            raise ValueError(f"child '{name}' must be 2-D (n_samples, channels)")

    src = original.astype(np.float64, copy=False)
    n, n_ch = src.shape
    out = {name: np.zeros((n, n_ch), dtype=np.float64) for name in names}
    if n == 0:
        return {name: out[name].astype(original.dtype, copy=False) for name in names}

    analyzer = STFTAnalyzer(
        UpmixConfig(
            fft_size=REMASK_FFT_SIZE,
            hop_size=REMASK_HOP_SIZE,
            auto_fft_size=False,
        ),
        sample_rate,
    )
    fade = min(REMASK_FFT_SIZE, n)

    for ch in range(n_ch):
        for start in range(0, n, _BLOCK_SAMPLES):
            stop = min(start + _BLOCK_SAMPLES + fade, n)
            length = stop - start
            next_start = start + _BLOCK_SAMPLES
            weights = _block_weights(
                length,
                head_fade=min(fade, length) if start else 0,
                tail_fade=stop - next_start if next_start < n else 0,
            )
            blocks = [
                _child_block(np.asarray(children[name]), ch, start, length)
                for name in names
            ]
            split = _remask_block(src[start:stop, ch], blocks, analyzer, alpha)
            for name, signal in zip(names, split):
                out[name][start:stop, ch] += signal * weights

    return {name: out[name].astype(original.dtype, copy=False) for name in names}


def share_parent_residual(
    parent: np.ndarray,
    children: dict[str, np.ndarray],
    sample_rate: int,
    alpha: float = 1.0,
) -> dict[str, np.ndarray]:
    """Split the parent's unclaimed remainder among the children.

    ``parent - sum(children)`` is the content the model's own split lost; it
    is distributed by the same soft masks :func:`reproject_stems` uses and
    added to the model's output, so the children sum back to *parent* without
    the model's waveforms being replaced.

    Args:
        parent: ``(n_samples, channels)`` parent audio.
        children: Child name -> ``(n_samples, channels)`` model output.
            Anything the caller means to discard must be subtracted from
            *parent* first, or its energy is shared into the children.
        sample_rate: Sample rate of parent and children.
        alpha: Mask exponent, as in :func:`reproject_stems`.

    Returns:
        Child name -> ``(n_samples, channels)`` array, truncated to the
        shortest input.
    """
    if not children:
        return {}
    n = min(len(parent), min(len(audio) for audio in children.values()))
    trimmed = {name: np.asarray(audio)[:n] for name, audio in children.items()}
    residual = np.asarray(parent)[:n] - sum(trimmed.values())
    shares = reproject_stems(residual, trimmed, sample_rate, alpha)
    return {name: trimmed[name] + shares[name] for name in trimmed}
