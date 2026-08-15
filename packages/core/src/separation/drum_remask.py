"""Soft-mask re-projection of drum kit pieces onto the parent Drums stem.

The drumsep model partitions its input near-binary, so overlapping kit pieces
collide in the cymbal/hi-hat region and part of the parent's 6-12 kHz energy
reaches no piece at all. Re-deriving each piece as a ratio mask on the parent's
own complex spectrogram redistributes that residual instead of discarding it,
and makes the pieces sum back to the parent by construction.

Pure STFT-domain DSP: arrays in, arrays out, no inference and no file I/O.
"""
from __future__ import annotations

import numpy as np

from upmixer.analysis.stft import STFTAnalyzer
from upmixer.config import UpmixConfig

# Matches the drumsep model's own STFT config (configs/MDX23C-DrumSep-*.yaml).
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


def _piece_block(
    piece: np.ndarray, ch: int, start: int, length: int
) -> np.ndarray:
    """One channel of a piece over [start, start+length), zero-padded if short."""
    column = piece[:, ch] if ch < piece.shape[1] else piece[:, 0]
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
    """Split one parent block among the pieces by their soft masks."""
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


def reproject_drum_pieces(
    parent: np.ndarray,
    pieces: dict[str, np.ndarray],
    sample_rate: int,
    alpha: float = 1.0,
) -> dict[str, np.ndarray]:
    """Re-derive kit pieces as soft masks on the parent Drums spectrogram.

    Each piece becomes ``|S_i|^alpha / sum_j |S_j|^alpha`` applied to the
    parent's complex STFT, so the returned pieces sum back to *parent* to
    within STFT reconstruction error.

    Args:
        parent: ``(n_samples, channels)`` parent Drums audio.
        pieces: Piece name -> ``(n_samples, channels)`` model output. Pieces
            are aligned at sample 0 and clipped or zero-padded to the parent's
            length. A piece the model did not produce is simply absent from
            the mapping, and its share of the parent goes to the rest.
        sample_rate: Sample rate of parent and pieces.
        alpha: Mask exponent, must be > 0. ``1.0`` is a plain ratio mask;
            below 1 shares overlapping bins more evenly between pieces; large
            values approach the model's own near-binary partition.

    Returns:
        Piece name -> ``(n_samples, channels)`` array in the parent's dtype.
        Bins where every piece is silent are split equally, so parent energy
        that no piece claimed is still distributed rather than dropped.

    Raises:
        ValueError: If *alpha* is not positive or *parent* is not 2-D.
    """
    if alpha <= 0.0:
        raise ValueError("drum re-mask alpha must be > 0")
    original = np.asarray(parent)
    if original.ndim != 2:
        raise ValueError("parent must be 2-D (n_samples, channels)")
    if not pieces:
        return {}

    names = sorted(pieces)
    for name in names:
        if np.asarray(pieces[name]).ndim != 2:
            raise ValueError(f"piece '{name}' must be 2-D (n_samples, channels)")

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
                _piece_block(np.asarray(pieces[name]), ch, start, length)
                for name in names
            ]
            split = _remask_block(src[start:stop, ch], blocks, analyzer, alpha)
            for name, signal in zip(names, split):
                out[name][start:stop, ch] += signal * weights

    return {name: out[name].astype(original.dtype, copy=False) for name in names}
