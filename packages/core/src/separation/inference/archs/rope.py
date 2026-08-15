"""Rotary position embedding for the two Roformer archs.

Shared by ``bs_roformer`` and ``mel_band_roformer``, which apply rotary
embeddings identically.

Two departures from ``rotary_embedding_torch.rotate_queries_or_keys``:

- Its ``apply_rotary_emb`` concatenates (possibly empty) unrotated edge
  slices around the rotated block, and torch-directml rejects zero-sized
  tensor ops with 'The parameter is incorrect.'. These models always rotate
  the full head dimension, so the edge slices are empty and the concat is a
  no-op — the rotation is computed directly instead. (Issue #292)
- On MPS the rotation is the single largest cost in the pipeline: the
  library issues ``t * cos``, ``rotate_half(t) * sin``, the add, and the
  concat as separate kernels over a ~100 MB tensor, 48 times per chunk.
  Compiling the expression fuses it into one kernel — measured 6.5x on the
  rotation and ~1.4-1.5x end-to-end, bit-exact. See
  ``docs/plans/mlx/phase0_report.md`` §5.

CUDA and CPU keep the library path: the win was measured only on MPS, and
inductor on CPU needs a C++ toolchain that deployment targets may not have.
"""
import torch
from rotary_embedding_torch.rotary_embedding_torch import rotate_half as _rotate_half_no_cat


def _is_dml_device(device) -> bool:
    """torch-directml devices use torch's out-of-tree backend slot (privateuseone).

    Module-level so tests can patch it to exercise the DML branch on
    CPU-only machines.
    """
    return device.type == "privateuseone"


def _rope(t, freqs):
    return t * freqs.cos() + _rotate_half_no_cat(t) * freqs.sin()


_rope_compiled = torch.compile(_rope)


def rotate_queries_or_keys(rotary_embed, t):
    """Apply rotary position embedding to a query or key tensor."""
    is_dml = _is_dml_device(t.device)
    if not is_dml and t.device.type != "mps":
        return rotary_embed.rotate_queries_or_keys(t)

    seq_len = t.shape[-2]
    freqs = rotary_embed.forward(
        rotary_embed.get_seq_pos(seq_len, device=t.device, dtype=t.dtype),
        seq_len=seq_len,
    )
    if freqs.shape[-1] != t.shape[-1]:
        # Partial-dim rotation would need the edge concat — unreachable here
        # (RotaryEmbedding(dim=dim_head) rotates the full head dim).
        return rotary_embed.rotate_queries_or_keys(t)
    return _rope(t, freqs) if is_dml else _rope_compiled(t, freqs)
