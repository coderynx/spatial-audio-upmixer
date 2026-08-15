"""Soft-mask re-projection of drum kit pieces onto the parent Drums stem."""
import numpy as np
import pytest

from upmixer.separation.drum_remask import reproject_drum_pieces

SR = 44100


def _null_db(estimate: np.ndarray, reference: np.ndarray) -> float:
    return 10.0 * np.log10(
        np.sum((estimate - reference) ** 2) / np.sum(reference**2)
    )


def _kit(n_samples: int = SR * 2) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """A parent stem and six overlapping, band-limited kit-piece estimates."""
    rng = np.random.default_rng(7)
    t = np.arange(n_samples) / SR
    parent = np.zeros((n_samples, 2), dtype=np.float32)
    pieces: dict[str, np.ndarray] = {}
    for i, (name, freq) in enumerate(
        [
            ("Kick", 60.0),
            ("Snare", 220.0),
            ("Toms", 130.0),
            ("Hi-Hat", 9000.0),
            ("Ride", 7500.0),
            ("Crash", 11000.0),
        ]
    ):
        tone = np.sin(2 * np.pi * freq * t) * np.exp(-3.0 * (t % 0.5))
        piece = np.stack([tone, tone * 0.8], axis=1).astype(np.float32) * (0.3 + 0.1 * i)
        pieces[name] = piece
        parent += piece
    parent += (rng.standard_normal((n_samples, 2)) * 0.01).astype(np.float32)
    return parent, pieces


def test_pieces_sum_back_to_parent():
    parent, pieces = _kit()
    out = reproject_drum_pieces(parent, pieces, SR, alpha=1.0)
    total = sum(out.values())
    assert _null_db(total, parent) < -60.0
    assert set(out) == set(pieces)
    assert all(v.shape == parent.shape for v in out.values())


def test_reconstruction_holds_across_block_boundaries():
    parent, pieces = _kit(n_samples=(1 << 19) + 40000)
    out = reproject_drum_pieces(parent, pieces, SR, alpha=0.7)
    assert _null_db(sum(out.values()), parent) < -60.0


def test_silent_pieces_split_parent_equally():
    parent, pieces = _kit()
    silent = {name: np.zeros_like(audio) for name, audio in pieces.items()}
    out = reproject_drum_pieces(parent, silent, SR, alpha=1.0)
    assert _null_db(sum(out.values()), parent) < -60.0
    energies = [float(np.sum(v.astype(np.float64) ** 2)) for v in out.values()]
    assert max(energies) == pytest.approx(min(energies), rel=1e-6)


def test_missing_piece_gets_no_output_but_parent_is_conserved():
    parent, pieces = _kit()
    del pieces["Crash"]
    out = reproject_drum_pieces(parent, pieces, SR, alpha=1.0)
    assert "Crash" not in out
    assert _null_db(sum(out.values()), parent) < -60.0


def _shared_content(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two pieces: how much content they hold in common."""
    x = a.astype(np.float64).ravel()
    y = b.astype(np.float64).ravel()
    return float(x @ y / np.sqrt((x @ x) * (y @ y)))


def test_lower_alpha_shares_overlapping_bins_more_evenly():
    parent, pieces = _kit()
    soft = reproject_drum_pieces(parent, pieces, SR, alpha=0.5)
    hard = reproject_drum_pieces(parent, pieces, SR, alpha=4.0)
    assert _shared_content(soft["Kick"], soft["Hi-Hat"]) > _shared_content(
        hard["Kick"], hard["Hi-Hat"]
    )


def test_non_positive_alpha_is_rejected():
    parent, pieces = _kit(n_samples=4096)
    with pytest.raises(ValueError):
        reproject_drum_pieces(parent, pieces, SR, alpha=0.0)
