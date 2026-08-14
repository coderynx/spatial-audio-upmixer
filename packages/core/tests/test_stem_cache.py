"""Tests for upmixer.separation.stem_cache — StemCache."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from upmixer.separation.stem_cache import StemCache, _cache_key, _preview_tag, _stem_filename


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

    def test_path_key_overrides_resolved_path(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k1 = _cache_key(wav, "model", 44100, path_key="stable-id")
        k2 = _cache_key(wav, "model", 44100, path_key="stable-id")
        assert k1 == k2

    def test_path_key_ignores_where_the_file_actually_lives(self, tmp_path):
        """The whole point of path_key: two different resolved input paths
        (e.g. the same asset materialized under a relocated data directory)
        produce the same key. mtime/size are excluded from a path_key entry's
        key, so even a re-materialization that does not preserve mtime hits."""
        import os
        import shutil

        wav_a = tmp_path / "a" / "x.wav"
        wav_a.parent.mkdir()
        _write_dummy_wav(str(wav_a))
        wav_b = tmp_path / "b" / "x.wav"
        wav_b.parent.mkdir()
        shutil.copy(wav_a, wav_b)  # does NOT preserve mtime, unlike copy2
        os.utime(wav_b, (os.stat(wav_b).st_atime, os.stat(wav_b).st_mtime + 120))
        k1 = _cache_key(str(wav_a), "model", 44100, path_key="stable-id")
        k2 = _cache_key(str(wav_b), "model", 44100, path_key="stable-id")
        assert k1 == k2

    def test_path_key_key_independent_of_mtime(self, tmp_path):
        """A path_key entry's key must not change when only the source mtime
        drifts — this is what lets a project export reuse prepared stems."""
        import os

        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        before = _cache_key(wav, "model", 44100, path_key="stable-id")
        stat = os.stat(wav)
        os.utime(wav, (stat.st_atime, stat.st_mtime + 3600))
        after = _cache_key(wav, "model", 44100, path_key="stable-id")
        assert before == after

    def test_no_path_key_still_binds_mtime(self, tmp_path):
        """Without path_key the legacy behavior stands: mtime is part of the key."""
        import os

        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        before = _cache_key(wav, "model", 44100)
        stat = os.stat(wav)
        os.utime(wav, (stat.st_atime, stat.st_mtime + 3600))
        after = _cache_key(wav, "model", 44100)
        assert before != after

    def test_different_path_key_different_key(self, tmp_path):
        wav = str(tmp_path / "x.wav")
        _write_dummy_wav(wav)
        k1 = _cache_key(wav, "model", 44100, path_key="track-1")
        k2 = _cache_key(wav, "model", 44100, path_key="track-2")
        assert k1 != k2


class TestStemFilename:
    def test_simple(self):
        assert _stem_filename("Vocals") == "Vocals.wav"

    def test_zone_tagged(self):
        assert _stem_filename("Vocals@front") == "Vocals__front.wav"

    def test_no_at_sign(self):
        name = _stem_filename("Bass")
        assert "@" not in name
        assert name.endswith(".wav")


class TestStemCacheInit:
    def test_creates_dir(self, tmp_path):
        cache_dir = str(tmp_path / "nested" / "cache")
        StemCache(cache_dir)
        assert Path(cache_dir).exists()

    def test_existing_dir_ok(self, tmp_path):
        cache = StemCache(str(tmp_path))
        assert cache is not None


class TestStemCacheSaveLoad:
    def test_legacy_entry_remains_readable(self, tmp_path):
        pytest.importorskip("soundfile")
        from upmixer.separation.stem_cache import _legacy_cache_key

        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        root = tmp_path / "cache"
        cache = StemCache(str(root))
        cache.save(wav, "model", 44100, _make_stems(), 44100)
        current = root / _cache_key(wav, "model", 44100)
        legacy = root / _legacy_cache_key(wav, "model", 44100)
        current.rename(legacy)

        result = cache.load(wav, "model", 44100)
        assert result is not None
        assert set(result[0]) == set(_make_stems())

    def test_atomic_save_leaves_no_temp_files(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache_dir = tmp_path / "cache"
        cache = StemCache(str(cache_dir))
        cache.save(wav, "model", 44100, _make_stems(), 44100)
        assert not list(cache_dir.rglob("*.tmp*"))

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

    def test_path_key_load_survives_mtime_drift(self, tmp_path):
        """A project export re-materializes the source (fresh mtime); a
        path_key entry saved at preparation must still load."""
        pytest.importorskip("soundfile")
        import os

        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100, path_key="project:p:track:t")
        stat = os.stat(wav)
        os.utime(wav, (stat.st_atime, stat.st_mtime + 300))

        result = cache.load(wav, "model", 44100, path_key="project:p:track:t")
        assert result is not None
        assert set(result[0].keys()) == set(stems.keys())

    def test_path_key_recovers_legacy_mtime_keyed_entry(self, tmp_path):
        """An entry written before mtime/size left the path_key formula must
        still load — an already-prepared project must not re-separate on export."""
        sf = pytest.importorskip("soundfile")
        import hashlib
        import json
        import os

        from upmixer.separation.stem_cache import (
            _CACHE_SCHEMA, _engine_version, _preview_tag, _stem_filename,
        )

        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav, n=4096)
        root = tmp_path / "cache"
        root.mkdir()
        pk = "project:p:track:t"
        stems = _make_stems()
        stat = os.stat(wav)
        tag = _preview_tag(False, None, None)
        silence_tag = "skip=True|thr=-90.0|min=2.000|xfade=10.0|pad=200.0"
        legacy_raw = (
            f"v{_CACHE_SCHEMA}|{pk}|{stat.st_mtime:.6f}|{stat.st_size}|model|"
            f"44100|{tag}|{silence_tag}|engine={_engine_version()}"
        )
        legacy_key = hashlib.sha256(legacy_raw.encode()).hexdigest()[:20]
        entry = root / legacy_key
        entry.mkdir()
        for name, audio in stems.items():
            sf.write(str(entry / _stem_filename(name)), audio, 44100, subtype="PCM_24")
        (entry / "metadata.json").write_text(json.dumps({
            "cache_schema": _CACHE_SCHEMA, "engine_version": _engine_version(),
            "input_path": wav, "path_key": pk, "mtime": round(stat.st_mtime, 6),
            "size": stat.st_size, "stems_hash": "model", "sep_sr": 44100,
            "stem_keys": list(stems), "silence_skip": True,
            "silence_threshold_db": -90.0, "silence_min_duration_s": 2.0,
            "silence_crossfade_ms": 10.0, "silence_pad_ms": 200.0,
        }))
        os.utime(wav, (stat.st_atime, stat.st_mtime + 50))  # re-materialized

        cache = StemCache(str(root))
        result = cache.load(wav, "model", 44100, path_key=pk)
        assert result is not None
        assert set(result[0].keys()) == set(stems.keys())
        assert cache.load(wav, "model", 44100, path_key="project:other:track:x") is None
        assert cache.load(wav, "other-model", 44100, path_key=pk) is None

    def test_path_key_load_invalidates_on_size_change(self, tmp_path):
        """A genuinely different source (changed size) must still cold-miss."""
        pytest.importorskip("soundfile")

        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav, n=4096)
        cache = StemCache(str(tmp_path / "cache"))
        cache.save(wav, "model", 44100, _make_stems(), 44100, path_key="project:p:track:t")
        _write_dummy_wav(wav, n=20000)  # different content and size

        assert cache.load(wav, "model", 44100, path_key="project:p:track:t") is None

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

    def test_output_is_float32(self, tmp_path):
        pytest.importorskip("soundfile")
        wav = str(tmp_path / "src.wav")
        _write_dummy_wav(wav)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(wav, "model", 44100, stems, 44100)

        loaded_stems, _ = cache.load(wav, "model", 44100)
        for arr in loaded_stems.values():
            assert arr.dtype == np.float32

    def test_path_key_survives_relocated_input_path(self, tmp_path):
        """Reproduces the web project-export bug: stems separated while the
        asset resolved under one absolute path (e.g. before a data-dir move)
        must still be found once the same asset resolves under a different
        absolute path, as long as callers key by a stable identity."""
        pytest.importorskip("soundfile")
        import shutil

        old_dir = tmp_path / "old-data-dir"
        old_dir.mkdir()
        old_wav = old_dir / "src.wav"
        _write_dummy_wav(str(old_wav), n=4096)
        cache = StemCache(str(tmp_path / "cache"))
        stems = _make_stems()
        cache.save(str(old_wav), "model", 44100, stems, 44100, path_key="project:p1:track:t1")

        new_dir = tmp_path / "new-data-dir"
        new_dir.mkdir()
        new_wav = new_dir / "src.wav"
        shutil.copy2(old_wav, new_wav)  # copy2 preserves mtime, as a real file move would

        result = cache.load(str(new_wav), "model", 44100, path_key="project:p1:track:t1")
        assert result is not None
        loaded_stems, _ = result
        assert set(loaded_stems.keys()) == set(stems.keys())


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
