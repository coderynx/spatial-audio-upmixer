"""TTA, pitch-shift, and per-model chunk-size resolution on SeparationEngine."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from upmixer.separation.inference.config import ModelConfig
from upmixer.separation.inference.device import DeviceManager
from upmixer.separation.inference.engine import SeparationEngine


def _make_config(dim_t: int = 6, hop: int = 100) -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 44100, "hop_length": hop},
        model={"stft_hop_length": hop},
        training={"instruments": ["vocals", "other"], "target_instrument": "vocals"},
        inference={"dim_t": dim_t},
    )


class _AsymmetricModel(torch.nn.Module):
    """Returns L unchanged and R zeroed -- not invariant under channel swap
    or polarity, so TTA's inverse transforms are exercised for real."""

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        out = batch.clone()
        out[:, 1, :] = 0.0
        return out


def _make_engine(**overrides) -> SeparationEngine:
    kwargs = dict(
        model=_AsymmetricModel(),
        config=_make_config(),
        arch="bs_roformer",
        model_filename="test.ckpt",
        device=DeviceManager("cpu"),
        output_dir="/tmp",
        sample_rate=44100,
        batch_size=1,
        segment_size=None,
        chunk_duration_s=None,
    )
    kwargs.update(overrides)
    return SeparationEngine(**kwargs)


def test_tta_off_matches_plain_demix():
    mix = np.random.default_rng(0).standard_normal((2, 500)).astype(np.float32)
    engine = _make_engine(tta=False)
    via_wrapper = engine._demix_one(mix)
    via_arch = engine._demix_arch(mix)
    assert via_wrapper.keys() == via_arch.keys()
    for name in via_wrapper:
        assert np.array_equal(via_wrapper[name], via_arch[name])


def test_tta_on_averages_invertible_variants():
    mix = np.ones((2, 500), dtype=np.float32)
    engine = _make_engine(tta=True)

    result = engine._demix_one(mix)["vocals"]
    # Model zeroes channel 1 (R). Averaged over identity, polarity-invert,
    # and channel-swap variants (each restored via its inverse transform),
    # R should recover roughly half the signal instead of exactly zero --
    # proof the swap variant's inverse actually ran and contributed.
    assert not np.allclose(result[1], 0.0)
    assert not np.allclose(result[1], result[0])


def test_pitch_shift_round_trip_preserves_length():
    n_samples = 4410
    mix = np.random.default_rng(1).standard_normal((2, n_samples)).astype(np.float32)
    engine = _make_engine(pitch_shift=0.75)

    stems = engine._demix_one(mix)
    for stem in stems.values():
        assert stem.shape == (2, n_samples)


def test_pitch_shift_none_skips_resampling():
    mix = np.zeros((2, 500), dtype=np.float32)
    engine_plain = _make_engine(pitch_shift=None)
    engine_pitched = _make_engine(pitch_shift=1.0)

    plain = engine_plain._demix_one(mix)["vocals"]
    pitched = engine_pitched._demix_one(mix)["vocals"]
    assert np.allclose(plain, pitched, atol=1e-5)


def test_default_chunk_samples_resolves_to_segment_size():
    engine = _make_engine(default_chunk_samples=882000, segment_size=None)
    # hop=100 here (test config) stands in for the real per-arch hop;
    # dim_t = round(samples / hop) + 1, matching demix's chunk_size formula.
    assert engine._resolved_segment_size() == round(882000 / 100) + 1


def test_explicit_segment_size_overrides_registry_default():
    engine = _make_engine(default_chunk_samples=882000, segment_size=64)
    assert engine._resolved_segment_size() == 64
