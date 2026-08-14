"""Tests for upmixer.separation.stem_store — PlainStemStore."""
from __future__ import annotations

import numpy as np

from upmixer.separation.stem_store import PlainStemStore


def _make_stems() -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, 4096, endpoint=False)
    sig = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return {
        "Vocals": np.column_stack([sig, sig]),
        "Drums@front": np.column_stack([sig, -sig]),
    }


def test_load_returns_none_when_directory_is_empty(tmp_path):
    store = PlainStemStore(str(tmp_path / "missing"))
    assert store.load() is None


def test_write_then_load_round_trips_stems(tmp_path):
    stems = _make_stems()
    store = PlainStemStore(str(tmp_path))
    store.write(stems, 44100, source_size=12345)

    loaded, sample_rate = store.load()
    assert sample_rate == 44100
    assert set(loaded.keys()) == set(stems.keys())
    for key, audio in stems.items():
        np.testing.assert_allclose(loaded[key], audio, atol=2e-4)


def test_load_writes_no_hash_subdirectory(tmp_path):
    store = PlainStemStore(str(tmp_path))
    store.write(_make_stems(), 44100)
    entries = {p.name for p in tmp_path.iterdir()}
    assert "stems.json" in entries
    assert "Vocals.wav" in entries
    assert "Drums__front.wav" in entries
    assert len(entries) == 3


def test_write_replaces_previous_contents(tmp_path):
    store = PlainStemStore(str(tmp_path))
    store.write({"Vocals": np.zeros((100, 2), dtype=np.float32)}, 44100)
    store.write(_make_stems(), 44100)

    loaded, _ = store.load()
    assert set(loaded.keys()) == {"Vocals", "Drums@front"}
