"""Lifecycle and failure checks for the path-based SCNet worker."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import numpy as np
import pytest

from upmixer.separation.inference import scnet_worker


class _FakeDevice:
    def empty_cache(self) -> None:
        pass


class _FakeSpec:
    arch = "scnet"
    default_chunk_samples = None


class _FakeEngine:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = Path(output_dir)
        self.parent = np.ones((4, 2), dtype=np.float32)

    def separate(
        self,
        audio_path: str,
        retain_parent: bool = False,
        progress_callback=None,
        wanted=None,
    ) -> list[str]:
        if Path(audio_path).name == "fail.wav":
            raise ValueError("fake inference failed")
        if Path(audio_path).name == "slow.wav":
            time.sleep(0.25)
        if Path(audio_path).name == "progress_slow.wav":
            for fraction in (0.2, 0.4, 0.6, 0.8):
                if progress_callback is not None:
                    progress_callback(fraction)
                time.sleep(0.05)
        if progress_callback is not None:
            progress_callback(0.25)
            progress_callback(1.0)
        output = self.output_dir / "mix_(Vocals)_fake.wav"
        output.write_bytes(b"fake")
        return [str(output)]

    def take_last_parent(self) -> np.ndarray:
        return self.parent


def _fake_state(_settings):
    return scnet_worker._WorkerState(
        model=object(),
        config=object(),
        spec=_FakeSpec(),
        device=_FakeDevice(),
    )


def _fake_engine(state, _settings, output_dir):
    return _FakeEngine(output_dir)


def _fork_context_or_skip():
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("fork is required to inject the fake child runtime")
    return multiprocessing.get_context("fork")


def _worker(monkeypatch, tmp_path):
    monkeypatch.setattr(scnet_worker, "_build_worker_state", _fake_state)
    monkeypatch.setattr(scnet_worker, "_new_engine", _fake_engine)
    return scnet_worker.SCNetWorker(
        "model.ckpt",
        str(tmp_path / "models"),
        context=_fork_context_or_skip(),
        force_in_process=False,
    )


def _request(worker, input_path, output_dir, **kwargs):
    return worker.separate(
        str(input_path),
        str(output_dir),
        sample_rate=44100,
        batch_size=1,
        segment_size=None,
        chunk_duration_s=None,
        overlap=None,
        tta=False,
        pitch_shift=None,
        **kwargs,
    )


def test_worker_reuses_child_for_progress_and_atomic_outputs(monkeypatch, tmp_path):
    worker = _worker(monkeypatch, tmp_path)
    output_dir = tmp_path / "outputs"
    input_path = tmp_path / "input.wav"
    input_path.touch()
    progress: list[float] = []
    try:
        first = _request(
            worker, input_path, output_dir, progress_callback=progress.append
        )
        pid = worker._process.pid
        second = _request(worker, input_path, output_dir)

        assert worker._process.pid == pid
        assert progress == [0.25, 1.0]
        assert all(Path(path).is_file() for path in first + second)
        assert len(list(output_dir.glob("scnet-*"))) == 2
    finally:
        worker.close()
    assert not worker.is_alive


def test_worker_reports_failure_and_remains_reusable(monkeypatch, tmp_path):
    worker = _worker(monkeypatch, tmp_path)
    output_dir = tmp_path / "outputs"
    try:
        with pytest.raises(RuntimeError, match="fake inference failed"):
            _request(worker, tmp_path / "fail.wav", output_dir)
        assert worker.is_alive
        paths = _request(worker, tmp_path / "ok.wav", output_dir)
        assert all(Path(path).is_file() for path in paths)
        assert not list(output_dir.glob(".scnet-*"))
    finally:
        worker.close()


def test_worker_timeout_terminates_child_and_cleans_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(scnet_worker, "SCNET_INACTIVITY_TIMEOUT_S", 0.1)
    worker = _worker(monkeypatch, tmp_path)
    output_dir = tmp_path / "outputs"
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            _request(worker, tmp_path / "slow.wav", output_dir)
        assert not worker.is_alive
        assert not list(output_dir.glob(".scnet-*"))
        assert not list(output_dir.glob("scnet-*"))
    finally:
        worker.close()


def test_worker_progress_resets_inactivity_watchdog(monkeypatch, tmp_path):
    monkeypatch.setattr(scnet_worker, "SCNET_INACTIVITY_TIMEOUT_S", 0.1)
    worker = _worker(monkeypatch, tmp_path)
    output_dir = tmp_path / "outputs"
    try:
        _request(worker, tmp_path / "warmup.wav", output_dir)
        events: list[float] = []
        paths = _request(
            worker,
            tmp_path / "progress_slow.wav",
            output_dir,
            progress_callback=events.append,
        )
        assert events
        assert all(Path(path).is_file() for path in paths)
    finally:
        worker.close()


def test_daemon_uses_in_process_worker(monkeypatch, tmp_path):
    worker = scnet_worker.SCNetWorker(
        "model.ckpt", str(tmp_path / "models"), force_in_process=True
    )
    monkeypatch.setattr(scnet_worker, "_build_worker_state", _fake_state)
    monkeypatch.setattr(scnet_worker, "_new_engine", _fake_engine)
    output_dir = tmp_path / "outputs"
    try:
        paths = _request(worker, tmp_path / "input.wav", output_dir)
        assert worker._process is None
        assert all(Path(path).is_file() for path in paths)
    finally:
        worker.close()
    assert not worker.is_alive


def test_worker_retains_parent_without_sending_array_over_pipe(monkeypatch, tmp_path):
    worker = _worker(monkeypatch, tmp_path)
    output_dir = tmp_path / "outputs"
    try:
        _request(worker, tmp_path / "input.wav", output_dir, retain_parent=True)
        parent = worker.take_last_parent(str(output_dir))
        np.testing.assert_array_equal(parent, np.ones((4, 2), dtype=np.float32))
        assert not list(output_dir.glob("scnet-parent-*.npy"))
    finally:
        worker.close()


def test_stem_separator_routes_mlx_scnet_through_persistent_worker(
    monkeypatch, tmp_path
):
    from upmixer.separation.separator import StemSeparator

    calls: list[dict] = []
    workers: list[object] = []

    class _FakeWorker:
        backend = "mlx"

        def __init__(self, model, model_dir):
            self.model = model
            self.model_dir = model_dir
            self.closed = False
            workers.append(self)

        def separate(self, _audio_path, _output_dir, **kwargs):
            calls.append(kwargs)
            return []

        def close(self):
            self.closed = True

    monkeypatch.setattr("upmixer.separation.separator._detect_backend", lambda: "mps")
    monkeypatch.setattr(
        "upmixer.separation.separator._mlx_scnet_available", lambda: True
    )
    monkeypatch.setattr("upmixer.separation.separator.SCNetWorker", _FakeWorker)
    separator = StemSeparator(
        model="model_scnet_ep_36_sdr_10.0891.ckpt",
        model_dir=str(tmp_path / "models"),
    )
    try:
        wanted = frozenset({"Bass"})
        separator._separate_paths(str(tmp_path / "input.wav"), wanted=wanted)
        separator._separate_paths(str(tmp_path / "input.wav"), wanted=wanted)
    finally:
        separator.close()

    assert len(workers) == 1
    assert len(calls) == 2
    assert all(call["wanted"] == wanted for call in calls)
    assert workers[0].closed
