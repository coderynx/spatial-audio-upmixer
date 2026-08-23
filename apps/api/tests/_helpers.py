"""Shared non-fixture helpers for the ``apps/api`` test suite."""

import io
import queue
import threading
from unittest.mock import patch

import numpy as np
import soundfile as sf

from upmixer.separation.stem_store import PlainStemStore
from upmixer_web.worker.subprocess import _run_work_items


def _wav_bytes(frequency: float = 440.0) -> bytes:
    sample_rate = 48_000
    samples = np.arange(4_800) / sample_rate
    audio = np.column_stack([
        0.1 * np.sin(2 * np.pi * frequency * samples),
        0.1 * np.sin(2 * np.pi * (frequency + 2.0) * samples),
    ])
    output = io.BytesIO()
    sf.write(output, audio, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def _seed_prepared_stems(project_stems, project_id, track_id, stems, sample_rate=48_000):
    """Populate a track's plain stem store, as a real prepare_stems pass
    would — the reference-match precompute reads straight from this store
    (see worker_reference_match.py) and never runs separation itself."""
    PlainStemStore(str(project_stems.stem_dir(project_id, track_id))).write(stems, sample_rate)


def _fake_execute_plan(get_separator, plan, sep_path, sep_sr, stage_callback=None,
                       cfg=None, resume_key=None):
    """Stand in for real separation: every requested stem is the source itself."""
    audio, _ = sf.read(str(sep_path), dtype="float32", always_2d=True)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    scale = np.float32(1.0 / max(1, len(plan.requested_stems)))
    return {name: audio * scale for name in plan.requested_stems}


class InProcessJobRun:
    """Drop-in for ``JobSubprocess`` that runs the work items in a thread.

    End-to-end job tests need the routing/mastering/delivery chain, not the
    separation models, and monkeypatching cannot reach a spawned child.
    """

    def __init__(self, items) -> None:
        self._items = items
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        with patch(
            "upmixer.separation.stem_pipeline_exec.execute_plan",
            side_effect=_fake_execute_plan,
        ):
            _run_work_items(self._items, self._queue, self._cancel)

    def start(self) -> None:
        self._thread.start()

    def events(self, poll_interval: float = 1.0):
        while True:
            try:
                event = self._queue.get(timeout=poll_interval)
            except queue.Empty:
                if not self._thread.is_alive():
                    return
                yield None
                continue
            yield event
            if event[0] == "job_done":
                return

    def stop(self, grace_seconds: float = 5.0) -> None:
        self._cancel.set()
        self._thread.join(timeout=grace_seconds)
