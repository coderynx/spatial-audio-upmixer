"""Rotary embedding parity: every backend path must match the library."""
import pytest

torch = pytest.importorskip("torch")
RotaryEmbedding = pytest.importorskip("rotary_embedding_torch").RotaryEmbedding

from upmixer.separation.inference.archs import rope


@pytest.fixture
def rotary():
    return RotaryEmbedding(dim=16)


def test_cpu_uses_library_path(rotary):
    t = torch.randn(2, 4, 8, 16)
    assert torch.equal(
        rope.rotate_queries_or_keys(rotary, t), rotary.rotate_queries_or_keys(t)
    )


def test_no_cat_path_matches_library(rotary, monkeypatch):
    """The DML branch skips apply_rotary_emb's zero-width edge concat."""
    monkeypatch.setattr(rope, "_is_dml_device", lambda device: True)
    t = torch.randn(2, 4, 8, 16)
    assert torch.allclose(
        rope.rotate_queries_or_keys(rotary, t),
        rotary.rotate_queries_or_keys(t),
        atol=1e-6,
    )


@pytest.mark.skipif(
    not torch.backends.mps.is_available(), reason="requires an MPS device"
)
def test_compiled_mps_path_matches_library(rotary):
    """The fused kernel must be bit-exact, not merely close."""
    rotary_mps = rotary.to("mps")
    t = torch.randn(2, 4, 8, 16, device="mps")
    assert torch.equal(
        rope.rotate_queries_or_keys(rotary_mps, t),
        rotary_mps.rotate_queries_or_keys(t),
    )


def test_partial_dim_rotation_falls_back(monkeypatch):
    """Head dim wider than the rotation needs the library's edge concat."""
    monkeypatch.setattr(rope, "_is_dml_device", lambda device: True)
    partial = RotaryEmbedding(dim=8)
    t = torch.randn(2, 4, 8, 16)
    assert torch.equal(
        rope.rotate_queries_or_keys(partial, t), partial.rotate_queries_or_keys(t)
    )
