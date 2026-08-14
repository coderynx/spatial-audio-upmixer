"""Overlap semantics for demix_roformer's chunked inference loop.

Regression coverage for the fix to the ``overlap_seconds`` clamp bug: the
Roformer loop previously always advanced one full chunk per step (no real
boundary blending) regardless of the requested overlap.
"""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from upmixer.separation.inference import demix
from upmixer.separation.inference.config import ModelConfig


def _make_config(dim_t: int, hop: int) -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 44100, "hop_length": hop},
        model={"stft_hop_length": hop},
        training={"instruments": ["vocals", "other"], "target_instrument": "vocals"},
        inference={"dim_t": dim_t},
    )


class _ConstantPerCallModel(torch.nn.Module):
    """Returns a distinct constant per forward call, in call order.

    With ``batch_size=1`` demix invokes forward once per chunk in the same
    left-to-right order as ``starts``, so the constant identifies which
    chunk produced a given contribution.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        value = float(self.calls)
        self.calls += 1
        return torch.full_like(batch, value)


def test_overlap_one_reproduces_no_boundary_blending():
    """overlap=1 is the pre-fix behavior: one chunk covers each region."""
    config = _make_config(dim_t=6, hop=100)  # chunk_size = 100 * 5 = 500
    mix = np.zeros((2, 1000), dtype=np.float32)
    model = _ConstantPerCallModel()

    stems = demix.demix_roformer(
        model, mix, config, torch.device("cpu"),
        segment_size=None, overlap=1, batch_size=1,
    )

    vocals = stems["vocals"]
    assert np.allclose(vocals[:, :500], 0.0)
    assert np.allclose(vocals[:, 500:1000], 1.0)


def test_overlap_two_blends_chunk_boundaries():
    """overlap=2 makes adjacent chunks share a region — real overlap-add."""
    config = _make_config(dim_t=6, hop=100)  # chunk_size = 500
    mix = np.zeros((2, 1200), dtype=np.float32)
    model = _ConstantPerCallModel()

    stems = demix.demix_roformer(
        model, mix, config, torch.device("cpu"),
        segment_size=None, overlap=2, batch_size=1,
    )

    vocals = stems["vocals"]
    # [0, 250) is covered only by chunk 0 (value 0) -- no second contributor.
    assert np.allclose(vocals[:, :250], 0.0)
    # [250, 500) is covered by both chunk 0 (value 0) and chunk 1 (value 1)
    # -- a real overlap blends them to something strictly between the two.
    blended = vocals[:, 250:500]
    assert np.all(blended > 0.0)
    assert np.all(blended < 1.0)


def test_overlap_clamps_below_one():
    """overlap=0 (or negative) must not divide by zero or produce an empty step."""
    config = _make_config(dim_t=6, hop=100)
    mix = np.zeros((2, 500), dtype=np.float32)
    model = _ConstantPerCallModel()

    stems = demix.demix_roformer(
        model, mix, config, torch.device("cpu"),
        segment_size=None, overlap=0, batch_size=1,
    )

    assert stems["vocals"].shape == (2, 500)
