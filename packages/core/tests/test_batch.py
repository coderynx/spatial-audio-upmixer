"""Tests for batch processing (upmixer.batch)."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest
import soundfile as sf

from upmixer.batch import BatchResult, resolve_batch_jobs
from upmixer.result import UpmixResult


def _make_wav(path: str, duration_s: float = 1.0, sr: int = 48000) -> str:
    """Write a minimal stereo WAV file for use in tests."""
    n = int(sr * duration_s)
    audio = np.zeros((n, 2), dtype=np.float32)
    sf.write(path, audio, sr)
    return path


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


@pytest.fixture
def two_wavs(tmp_path):
    a = _make_wav(str(tmp_path / "track01.wav"))
    b = _make_wav(str(tmp_path / "track02.wav"))
    return a, b


@pytest.fixture
def batch_dir(tmp_path):
    _make_wav(str(tmp_path / "a.wav"))
    _make_wav(str(tmp_path / "b.flac"))
    (tmp_path / "readme.txt").write_text("ignore me")
    return str(tmp_path)


class TestResolveBatchJobs:
    def test_from_input_paths(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=out_dir)
        assert len(jobs) == 2
        assert jobs[0].input_path == a
        assert jobs[1].input_path == b
        assert jobs[0].output_path == os.path.join(out_dir, "track01.wav")
        assert jobs[1].output_path == os.path.join(out_dir, "track02.wav")

    def test_from_batch_inputs(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_inputs=[a, b], output_dir=out_dir)
        assert len(jobs) == 2
        assert jobs[0].input_path == a

    def test_from_batch_dir(self, batch_dir, tmp_path):
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=batch_dir, output_dir=out_dir)
        # Only .wav and .flac — not .txt
        assert len(jobs) == 2
        exts = {os.path.splitext(j.input_path)[1] for j in jobs}
        assert exts == {".wav", ".flac"}

    def test_from_explicit_jobs(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        explicit = [
            {"input": a, "output": "/custom/out.wav"},
            {"input": b},
        ]
        jobs = resolve_batch_jobs(
            explicit_jobs=explicit, output_dir=out_dir
        )
        assert len(jobs) == 2
        assert jobs[0].output_path == "/custom/out.wav"
        assert jobs[1].output_path == os.path.join(out_dir, "track02.wav")

    def test_priority_explicit_over_input_paths(self, two_wavs, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        explicit = [{"input": a}]
        jobs = resolve_batch_jobs(
            input_paths=[a, b],
            explicit_jobs=explicit,
            output_dir=out_dir,
        )
        assert len(jobs) == 1

    def test_priority_batch_inputs_over_batch_dir(self, two_wavs, batch_dir, tmp_path):
        a, b = two_wavs
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(
            batch_inputs=[a],
            batch_dir=batch_dir,
            output_dir=out_dir,
        )
        assert len(jobs) == 1

    def test_missing_output_dir_raises(self, two_wavs):
        a, b = two_wavs
        with pytest.raises(ValueError, match="output_dir required"):
            resolve_batch_jobs(input_paths=[a, b])

    def test_empty_batch_dir_returns_empty(self, tmp_path):
        empty_dir = str(tmp_path / "empty")
        os.makedirs(empty_dir)
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=empty_dir, output_dir=out_dir)
        assert jobs == []

    def test_recursive_include_and_relative_template(self, tmp_path):
        nested = tmp_path / "disc2"
        nested.mkdir()
        _make_wav(str(nested / "song.wav"))
        _make_wav(str(tmp_path / "root.flac"))
        out_dir = tmp_path / "out"
        jobs = resolve_batch_jobs(
            batch_dir=str(tmp_path),
            output_dir=str(out_dir),
            recursive=True,
            include_patterns=["*.wav"],
            output_template="{relative_stem}.wav",
        )
        assert len(jobs) == 1
        assert jobs[0].output_path == str(out_dir / "disc2" / "song.wav")

    def test_batch_dir_with_brackets_in_path(self, tmp_path):
        """Directory names with [ ] (common in music filenames) must not break glob."""
        bracketed = tmp_path / "Album [FLAC] [16B-44.1kHz]"
        bracketed.mkdir()
        _make_wav(str(bracketed / "track01.flac"))
        _make_wav(str(bracketed / "track02.wav"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=str(bracketed), output_dir=out_dir)
        assert len(jobs) == 2

    def test_flac_only_batch_dir(self, tmp_path):
        """Directory with only .flac files (no .wav) must still be scanned."""
        a = _make_wav(str(tmp_path / "alpha.flac"))
        b = _make_wav(str(tmp_path / "beta.flac"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(batch_dir=str(tmp_path), output_dir=out_dir)
        assert len(jobs) == 2
        exts = {os.path.splitext(j.input_path)[1] for j in jobs}
        assert exts == {".flac"}

    def test_flac_input_derives_wav_output(self, tmp_path):
        """Output path for .flac input uses .wav extension by default."""
        f = _make_wav(str(tmp_path / "track.flac"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[f], output_dir=out_dir)
        assert jobs[0].output_path == os.path.join(out_dir, "track.wav")

    def test_flac_input_derives_adm_output_ext(self, tmp_path):
        """output_ext param propagates to derived output path."""
        f = _make_wav(str(tmp_path / "track.flac"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[f], output_dir=out_dir, output_ext=".adm.bwf")
        assert jobs[0].output_path == os.path.join(out_dir, "track.adm.bwf")

    def test_cross_directory_files(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        a = _make_wav(str(dir1 / "a.wav"))
        b = _make_wav(str(dir2 / "b.wav"))
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        jobs = resolve_batch_jobs(input_paths=[a, b], output_dir=out_dir)
        assert jobs[0].input_path == a
        assert jobs[1].input_path == b
        # Output names derived from basename only — no path conflict
        assert os.path.basename(jobs[0].output_path) == "a.wav"
        assert os.path.basename(jobs[1].output_path) == "b.wav"


class TestBatchResult:
    def _make_result(self) -> UpmixResult:
        return UpmixResult(
            input_path="in.wav",
            output_path="out.wav",
            input_format="Stereo",
            output_format="7.1.4 Atmos",
            input_sample_rate=48000,
            output_sample_rate=48000,
            duration_seconds=3.0,
            n_channels_in=2,
            n_channels_out=12,
            mode="realtime",
        )

    def test_to_dict_structure(self):
        br = BatchResult(jobs=[self._make_result()], failed=[], total_audio_duration_s=3.0, wall_time_s=1.5)
        d = br.to_dict()
        assert d["succeeded"] == 1
        assert d["total"] == 1
        assert len(d["jobs"]) == 1

    def test_to_json_roundtrip(self):
        import json
        br = BatchResult(jobs=[], failed=[{"input": "bad.wav", "error": "oops", "traceback": ""}], total_audio_duration_s=0.0, wall_time_s=0.5)
        j = json.loads(br.to_json())
        assert j["succeeded"] == 0
        assert j["total"] == 1
