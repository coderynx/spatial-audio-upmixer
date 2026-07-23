"""Tests for isolating pipeline processing in a child process."""
from __future__ import annotations

import os
import queue
import signal
import time

import pytest

from upmixer.config import UpmixConfig
from upmixer_web import job_subprocess
from upmixer_web.job_subprocess import JobSubprocess, WorkItem, _run_work_items


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


class _FakeEvent:
    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True


class _FakeResult:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def to_dict(self) -> dict:
        return {"tag": self.tag}


class _FakePipeline:
    calls: list[str] = []

    def __init__(self, config=None, custom_routing=None) -> None:
        self.config = config

    def process_file(self, input_path, output_path, input_format_override=None, progress_callback=None):
        _FakePipeline.calls.append(input_path)
        if progress_callback is not None:
            progress_callback("working", 0.5)
        if input_path == "boom.wav":
            raise RuntimeError("synthetic failure")
        return _FakeResult(input_path)

    def close(self) -> None:
        pass


def _item(track_id: str, input_path: str, mode: str = "stem") -> WorkItem:
    return WorkItem(
        track_id=track_id,
        mode=mode,
        input_path=input_path,
        output_path=f"{input_path}.out",
        config=UpmixConfig(),
    )


def test_run_work_items_emits_progress_and_done(monkeypatch):
    monkeypatch.setattr(job_subprocess, "StemUpmixPipeline", _FakePipeline)
    monkeypatch.setattr(job_subprocess, "UpmixPipeline", _FakePipeline)
    _FakePipeline.calls = []

    items = [_item("t1", "a.wav"), _item("t2", "b.wav")]
    q = _FakeQueue()
    _run_work_items(items, q, _FakeEvent())

    kinds = [event[0] for event in q.items]
    assert kinds == ["progress", "track_done", "progress", "track_done", "job_done"]
    assert q.items[1] == ("track_done", "t1", {"tag": "a.wav"})
    assert q.items[3] == ("track_done", "t2", {"tag": "b.wav"})


def test_run_work_items_reports_track_error(monkeypatch):
    monkeypatch.setattr(job_subprocess, "StemUpmixPipeline", _FakePipeline)
    monkeypatch.setattr(job_subprocess, "UpmixPipeline", _FakePipeline)

    items = [_item("t1", "boom.wav")]
    q = _FakeQueue()
    _run_work_items(items, q, _FakeEvent())

    assert q.items == [
        ("progress", "t1", "working", 0.5),
        ("track_error", "t1", "synthetic failure"),
    ]


def _kill_self_target(items, progress_queue, cancel_event) -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def _emit_then_finish_target(items, progress_queue, cancel_event) -> None:
    for item in items:
        progress_queue.put(("progress", item.track_id, "halfway", 0.5))
        progress_queue.put(("track_done", item.track_id, {"tag": item.track_id}))
    progress_queue.put(("job_done",))


def _block_until_cancelled_target(items, progress_queue, cancel_event) -> None:
    while not cancel_event.is_set():
        time.sleep(0.05)


def test_job_subprocess_crash_is_reported_and_does_not_kill_parent(monkeypatch):
    monkeypatch.setattr(job_subprocess, "_run_work_items", _kill_self_target)
    proc = JobSubprocess([_item("t1", "a.wav")])
    proc.start()

    events = list(proc.events(poll_interval=0.1))
    proc.stop()

    assert events
    kind, message = events[-1]
    assert kind == "crashed"
    assert "SIGKILL" in message or "exit code -9" in message


def test_job_subprocess_relays_progress_and_completion(monkeypatch):
    monkeypatch.setattr(job_subprocess, "_run_work_items", _emit_then_finish_target)
    proc = JobSubprocess([_item("t1", "a.wav"), _item("t2", "b.wav")])
    proc.start()

    events = list(proc.events(poll_interval=0.1))
    proc.stop()

    assert ("progress", "t1", "halfway", 0.5) in events
    assert ("track_done", "t1", {"tag": "t1"}) in events
    assert ("progress", "t2", "halfway", 0.5) in events
    assert ("track_done", "t2", {"tag": "t2"}) in events
    assert events[-1] == ("job_done",)


def test_job_subprocess_stop_terminates_blocked_child(monkeypatch):
    monkeypatch.setattr(job_subprocess, "_run_work_items", _block_until_cancelled_target)
    proc = JobSubprocess([_item("t1", "a.wav")])
    proc.start()

    saw_timeout = False
    for event in proc.events(poll_interval=0.1):
        if event is None:
            saw_timeout = True
            break

    assert saw_timeout
    proc.stop(grace_seconds=2.0)
    proc._process.join(timeout=1.0)
    assert proc._process.exitcode is not None
