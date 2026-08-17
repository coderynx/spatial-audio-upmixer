"""Soft-mask re-projection of separated children onto their parent stem."""
import numpy as np
import pytest
import soundfile as sf

from upmixer.separation.remask import reproject_stems, share_parent_residual
from upmixer.separation.stem_pipeline_exec import _remask_stage

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
    out = reproject_stems(parent, pieces, SR, alpha=1.0)
    total = sum(out.values())
    assert _null_db(total, parent) < -60.0
    assert set(out) == set(pieces)
    assert all(v.shape == parent.shape for v in out.values())


def test_reconstruction_holds_across_block_boundaries():
    parent, pieces = _kit(n_samples=(1 << 19) + 40000)
    out = reproject_stems(parent, pieces, SR, alpha=0.7)
    assert _null_db(sum(out.values()), parent) < -60.0


def test_silent_pieces_split_parent_equally():
    parent, pieces = _kit()
    silent = {name: np.zeros_like(audio) for name, audio in pieces.items()}
    out = reproject_stems(parent, silent, SR, alpha=1.0)
    assert _null_db(sum(out.values()), parent) < -60.0
    energies = [float(np.sum(v.astype(np.float64) ** 2)) for v in out.values()]
    assert max(energies) == pytest.approx(min(energies), rel=1e-6)


def test_missing_piece_gets_no_output_but_parent_is_conserved():
    parent, pieces = _kit()
    del pieces["Crash"]
    out = reproject_stems(parent, pieces, SR, alpha=1.0)
    assert "Crash" not in out
    assert _null_db(sum(out.values()), parent) < -60.0


def _shared_content(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two pieces: how much content they hold in common."""
    x = a.astype(np.float64).ravel()
    y = b.astype(np.float64).ravel()
    return float(x @ y / np.sqrt((x @ x) * (y @ y)))


def test_lower_alpha_shares_overlapping_bins_more_evenly():
    parent, pieces = _kit()
    soft = reproject_stems(parent, pieces, SR, alpha=0.5)
    hard = reproject_stems(parent, pieces, SR, alpha=4.0)
    assert _shared_content(soft["Kick"], soft["Hi-Hat"]) > _shared_content(
        hard["Kick"], hard["Hi-Hat"]
    )


def test_non_positive_alpha_is_rejected():
    parent, pieces = _kit(n_samples=4096)
    with pytest.raises(ValueError):
        reproject_stems(parent, pieces, SR, alpha=0.0)


def _instrumental(n_samples: int = SR * 2) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """An instrumental parent and the five instrument estimates split from it."""
    rng = np.random.default_rng(11)
    t = np.arange(n_samples) / SR
    parent = np.zeros((n_samples, 2), dtype=np.float32)
    children: dict[str, np.ndarray] = {}
    for name, freq in [
        ("Bass", 80.0),
        ("Drums", 200.0),
        ("Guitar", 1200.0),
        ("Piano", 440.0),
        ("Other", 3000.0),
    ]:
        tone = np.sin(2 * np.pi * freq * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
        child = np.stack([tone, tone * 0.9], axis=1).astype(np.float32) * 0.2
        children[name] = child
        parent += child
    parent += (rng.standard_normal((n_samples, 2)) * 0.02).astype(np.float32)
    return parent, children


def _split_short_of_parent() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Instrument estimates that miss part of their parent, as the model does."""
    parent, children = _instrumental()
    lossy = {name: (audio * 0.98).astype(np.float32) for name, audio in children.items()}
    return parent, lossy


def test_shared_residual_makes_instrument_stems_sum_to_the_parent():
    parent, children = _split_short_of_parent()
    assert _null_db(sum(children.values()), parent) > -40.0
    out = share_parent_residual(parent, children, SR, alpha=1.0)
    assert _null_db(sum(out.values()), parent) < -60.0


def test_shared_residual_keeps_the_model_output():
    """Each stem moves only by its share of the remainder, not wholesale."""
    parent, children = _split_short_of_parent()
    out = share_parent_residual(parent, children, SR, alpha=1.0)
    remainder_db = _null_db(sum(children.values()), parent)
    for name, audio in out.items():
        assert _null_db(audio, children[name]) < remainder_db + 6.0


def test_shared_residual_rejects_bad_alpha():
    parent, children = _split_short_of_parent()
    with pytest.raises(ValueError):
        share_parent_residual(parent, children, SR, alpha=-1.0)


def test_holding_a_child_out_of_the_parent_keeps_it_out_of_the_rest():
    parent, children = _split_short_of_parent()
    residue = children["Other"] * 0.5
    shared = share_parent_residual(
        (parent - residue).astype(np.float32), children, SR, alpha=1.0
    )
    assert _null_db(sum(shared.values()) + residue, parent) < -60.0


def test_remask_stage_rewrites_children_kept_on_disk(tmp_path):
    """Drums leaves the primary stage on disk, so drumsep sees the shared one."""
    parent, children = _split_short_of_parent()
    parent_path = str(tmp_path / "parent.wav")
    sf.write(parent_path, parent, SR, subtype="FLOAT")
    drums_path = str(tmp_path / "drums.wav")
    sf.write(drums_path, children["Drums"], SR, subtype="FLOAT")

    loaded = {name: audio for name, audio in children.items() if name != "Drums"}
    loaded["Vocals"] = np.zeros_like(parent)
    on_disk = {"Drums": drums_path}
    _remask_stage(loaded, on_disk, parent_path, frozenset(children))

    shared_drums, _ = sf.read(drums_path, dtype="float32", always_2d=True)
    assert not np.allclose(shared_drums, children["Drums"])
    total = shared_drums + sum(loaded[name] for name in children if name != "Drums")
    assert _null_db(total, parent) < -60.0
    assert np.array_equal(loaded["Vocals"], np.zeros_like(parent))


def test_kit_remask_composes_with_the_shared_primary_stage():
    """Kit pieces sum to the shared Drums, which sums into the parent."""
    parent, children = _split_short_of_parent()
    instruments = share_parent_residual(parent, children, SR, alpha=1.0)
    drums = instruments["Drums"]
    _, pieces = _kit(len(drums))
    kit = share_parent_residual(drums, pieces, SR, alpha=1.0)
    assert _null_db(sum(kit.values()), drums) < -60.0
    whole = sum(kit.values()) + sum(
        audio for name, audio in instruments.items() if name != "Drums"
    )
    assert _null_db(whole, parent) < -60.0


def test_kit_pieces_keep_their_model_waveforms():
    """Re-deriving the pieces from the parent measured worse on every metric
    than sharing the remainder — docs/reports/drum_remask.md."""
    drums, pieces = _kit()
    lossy = {name: (audio * 0.97).astype(np.float32)
             for name, audio in pieces.items()}
    shared = share_parent_residual(drums, lossy, SR, alpha=1.0)
    projected = reproject_stems(drums, lossy, SR, alpha=1.0)
    for name in pieces:
        assert _null_db(shared[name], lossy[name]) < _null_db(
            projected[name], lossy[name]
        )


def test_both_stages_share_their_parent_remainder():
    from upmixer.config import UpmixConfig
    from upmixer.separation.stem_pipeline_exec import _remasks
    from upmixer.separation.stem_plan import MODEL_DRUMS, MODEL_PRIMARY

    cfg = UpmixConfig()
    assert _remasks(cfg, MODEL_PRIMARY)
    assert _remasks(cfg, MODEL_DRUMS)
    assert not _remasks(UpmixConfig(stem_primary_remask=False), MODEL_PRIMARY)
    assert not _remasks(UpmixConfig(stem_drum_remask=False), MODEL_DRUMS)
    assert not _remasks(None, MODEL_PRIMARY)
