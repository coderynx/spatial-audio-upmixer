"""Tests for upmixer.separation.stem_cache — StemCache."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from upmixer.separation.stem_cache import StemCache, _cache_key, _preview_tag, _stem_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stems(n: int = 4096) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float64)
    return {
        "Vocals": np.column_stack([sig, sig * 0.9]),
        "Bass":   np.column_stack([sig * 0.5, sig * 0.5]),
        "Drums":  np.column_stack([sig * 0.7, sig * 0.7]),
        "Other":  np.column_stack([sig * 0.3, sig * 0.2]),
    }


def _make_zone_stems(n: int = 4096) -> dict[str, np.ndarray]:
    t = np.linspace(0, 1, n, endpoint=False)
    sig = 0.2 * np.sin(2 * np.pi * 220 * t).astype(np.float64)
    return {
        "Vocals@front":   np.column_stack([sig, sig]),
        "Bass@front":     np.column_stack([sig, sig]),
        "Drums@surround": np.column_stack([sig, sig]),
    }


def _write_dummy_wav(path: str, n: int = 4096, sr: int = 44100) -> None:
    sf = pytest.importorskip("soundfile")
    arr = np.zeros((n, 2), dtype=np.float32)
    sf.write(path, arr, sr, subtype="PCM_24")


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_same_params_same_key(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k1 = _cache_key(wav, "model", 44100)
        k2 = _cache_key(wav, "model", 44100)
        assert k1 == k2

    def test_different_model_different_key(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k1 = _cache_key(wav, "htdemucs", 44100)
        k2 = _cache_key(wav, "htdemucs_ft", 44100)
        assert k1 != k2

    def test_different_sr_different_key(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k1 = _cache_key(wav, "model", 44100)
        k2 = _cache_key(wav, "model", 48000)
        assert k1 != k2

    def test_key_is_20_chars(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        assert len(_cache_key(wav, "model", 44100)) == 20

    def test_key_is_hex(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        key = _cache_key(wav, "model", 44100)
        int(key, 16)  # raises ValueError if not hex


# ---------------------------------------------------------------------------
# _stem_filename
# ---------------------------------------------------------------------------

class TestStemFilename:
    def test_simple(self):
        assert _stem_filename("Vocals") == "Vocals.wav"

    def test_zone_tagged(self):
        assert _stem_filename("Vocals@front") == "Vocals__front.wav"

    def test_no_at_sign(self):
        name = _stem_filename("Bass")
        assert "@" not in name
        assert name.endswith(".wav")


# ---------------------------------------------------------------------------
# StemCache construction
# ---------------------------------------------------------------------------

class TestStemCacheInit:
    def test_creates_dir(self, tmp_path):
        cache_dir = str(tmp_path / "nested" / "cache")
        StemCache(cache_dir)
        assert Path(cache_dir).exists()

    def test_existing_dir_ok(self, tmp_path):
        cache = StemCache(str(tmp_path))
        assert cache is not None


# ---------------------------------------------------------------------------
# StemCache.save / load round-trip
# ---------------------------------------------------------------------------

class TestStemCacheSaveLoad:
    def test_save_creates_wav_files(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        from upmixer.separation.stem_cache import _cache_key
        key = _cache_key(wav, "model", 44100)
        entry_dir = tmp_path / "cache" / key
        assert entry_dir.exists()
        for stem_key in stems:
            assert (entry_dir / _stem_filename(stem_key)).exists()

    def test_save_creates_metadata(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        from upmixer.separation.stem_cache import _cache_key, _METADATA_FILE
        key = _cache_key(wav, "model", 44100)
        meta_path = tmp_path / "cache" / key / _METADATA_FILE
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert "stem_keys" in meta
        assert set(meta["stem_keys"]) == set(stems.keys())

    def test_roundtrip_simple_stems(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav, n=4096)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        result = cache.load(wav, "model", 44100)
        assert result is not None
        loaded_stems, sr = result
        assert sr == 44100
        assert set(loaded_stems.keys()) == set(stems.keys())

    def test_roundtrip_values_close(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        loaded_stems, _ = cache.load(wav, "model", 44100)
        for name in stems:
            # PCM_24 → ~144 dB dynamic range → error < 1e-6
            np.testing.assert_allclose(
                loaded_stems[name], stems[name], atol=1e-4,
                err_msg=f"Stem '{name}' not preserved through cache round-trip"
            )

    def test_roundtrip_zone_tagged_stems(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_zone_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        loaded_stems, _ = cache.load(wav, "model", 44100)
        assert set(loaded_stems.keys()) == set(stems.keys())

    def test_output_is_float64(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        loaded_stems, _ = cache.load(wav, "model", 44100)
        for arr in loaded_stems.values():
            assert arr.dtype == np.float64


# ---------------------------------------------------------------------------
# StemCache.load — cache miss cases
# ---------------------------------------------------------------------------

class TestStemCacheMiss:
    def test_empty_cache_returns_none(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        assert cache.load(wav, "model", 44100) is None

    def test_different_model_miss(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "modelA", 44100, stems, 44100)

        assert cache.load(wav, "modelB", 44100) is None

    def test_different_sr_miss(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        assert cache.load(wav, "model", 48000) is None

    def test_missing_metadata_returns_none(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        # Delete metadata to simulate corruption
        from upmixer.separation.stem_cache import _cache_key, _METADATA_FILE
        key = _cache_key(wav, "model", 44100)
        (tmp_path / "cache" / key / _METADATA_FILE).unlink()

        assert cache.load(wav, "model", 44100) is None

    def test_mtime_change_invalidates(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        # Force mtime change beyond tolerance (write new content)
        _write_dummy_wav(wav)
        # Force mtime to differ by more than tolerance
        new_mtime = os.path.getmtime(wav) + 10.0
        os.utime(wav, (new_mtime, new_mtime))

        assert cache.load(wav, "model", 44100) is None


# ---------------------------------------------------------------------------
# StemCache preview isolation
# ---------------------------------------------------------------------------

class TestStemCachePreview:
    def test_preview_key_differs_from_full(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        full_key    = _cache_key(wav, "model", 44100, is_preview=False)
        preview_key = _cache_key(wav, "model", 44100, is_preview=True, preview_duration=30.0)
        assert full_key != preview_key

    def test_different_preview_durations_differ(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k30 = _cache_key(wav, "model", 44100, is_preview=True, preview_duration=30.0)
        k60 = _cache_key(wav, "model", 44100, is_preview=True, preview_duration=60.0)
        assert k30 != k60

    def test_preview_tag_full(self):
        assert _preview_tag(False, None, None) == "full"

    def test_preview_tag_encodes_duration(self):
        tag = _preview_tag(True, 30.0, None)
        assert tag.startswith("preview:")
        assert "30.000" in tag

    def test_preview_tag_encodes_start(self):
        tag = _preview_tag(True, 30.0, 60.0)
        assert "60.000" in tag

    def test_save_preview_skips_write(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100, is_preview=True)
        # Cache dir should be empty — no entry written
        entries = list((tmp_path / "cache").iterdir())
        assert entries == []

    def test_preview_save_not_visible_to_full_load(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        # Save preview stems (should be silently skipped)
        cache.save(wav, "model", 44100, stems, 44100, is_preview=True)
        # Full-file load → must be a miss
        assert cache.load(wav, "model", 44100, is_preview=False) is None

    def test_full_save_not_visible_to_preview_load(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100, is_preview=False)
        # Preview load → different key → miss
        assert cache.load(wav, "model", 44100, is_preview=True, preview_duration=30.0) is None
