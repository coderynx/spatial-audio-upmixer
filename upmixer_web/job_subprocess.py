"""Runs pipeline processing in an isolated child process.

Stem separation (Torch/ONNX via ``audio-separator``) and mastering both run
inside :meth:`~upmixer.pipeline.UpmixPipeline.process_file` /
:meth:`~upmixer.separation.stem_pipeline.StemUpmixPipeline.process_file`.
Native crashes (OS OOM-kill, CUDA/MPS driver crashes, segfaults) in that code
are not catchable Python exceptions, so running it in-process would take
down the whole web server. This module runs it in a child process instead,
so only the offending job fails.
"""
from __future__ import annotations

import multiprocessing
import queue
import signal
from dataclasses import dataclass
from typing import Literal

from upmixer.config import UpmixConfig
from upmixer.pipeline import UpmixPipeline
from upmixer.separation.stem_pipeline import StemUpmixPipeline

_CTX = multiprocessing.get_context("spawn")


@dataclass
class WorkItem:
    """One track's processing work, picklable across the process boundary."""

    track_id: str
    mode: Literal["stem", "realtime"]
    input_path: str
    output_path: str
    config: UpmixConfig
    input_format_override: str | None = None
    custom_routing: dict[str, dict[str, float]] | None = None


def _describe_exit(exitcode: int) -> str:
    if exitcode < 0:
        try:
            name = signal.Signals(-exitcode).name
        except ValueError:
            name = f"signal {-exitcode}"
        return (
            f"Job subprocess terminated unexpectedly ({name}, exit code {exitcode}) "
            "— likely an out-of-memory kill or native crash"
        )
    return f"Job subprocess exited unexpectedly with code {exitcode}"


def _run_work_items(items: list[WorkItem], progress_queue, cancel_event) -> None:
    """Child-process entrypoint: process each item, reporting events on the queue."""
    stem_pipeline: StemUpmixPipeline | None = None
    try:
        for item in items:
            if cancel_event.is_set():
                return

            def _callback(message: str, fraction: float, tid: str = item.track_id) -> None:
                progress_queue.put(("progress", tid, message, fraction))

            try:
                if item.mode == "stem":
                    if item.custom_routing is not None:
                        pipeline: StemUpmixPipeline = StemUpmixPipeline(
                            config=item.config, custom_routing=item.custom_routing,
                        )
                    else:
                        if stem_pipeline is None:
                            stem_pipeline = StemUpmixPipeline(config=item.config)
                        stem_pipeline.config = item.config
                        pipeline = stem_pipeline
                    try:
                        result = pipeline.process_file(
                            item.input_path,
                            item.output_path,
                            input_format_override=item.input_format_override,
                            progress_callback=_callback,
                        )
                    finally:
                        if pipeline is not stem_pipeline:
                            pipeline.close()
                else:
                    result = UpmixPipeline(item.config).process_file(
                        item.input_path,
                        item.output_path,
                        input_format_override=item.input_format_override,
                        progress_callback=_callback,
                    )
            except Exception as exc:
                progress_queue.put(("track_error", item.track_id, str(exc)))
                return

            progress_queue.put(("track_done", item.track_id, result.to_dict()))
        progress_queue.put(("job_done",))
    finally:
        if stem_pipeline is not None:
            stem_pipeline.close()


class JobSubprocess:
    """Supervises a child process running a batch of :class:`WorkItem`.

    Uses the ``spawn`` start method explicitly (not the platform default) to
    avoid fork-unsafety with CUDA/threaded parent state.
    """

    def __init__(self, items: list[WorkItem]) -> None:
        self._queue = _CTX.Queue()
        self._cancel_event = _CTX.Event()
        self._process = _CTX.Process(
            target=_run_work_items,
            args=(items, self._queue, self._cancel_event),
            daemon=True,
        )

    def start(self) -> None:
        self._process.start()

    def events(self, poll_interval: float = 1.0):
        """Yield parsed events from the child.

        Yields ``None`` on each poll timeout with no event, so the caller can
        run cooperative control checks between events. Yields
        ``("crashed", message)`` if the child process exits without ever
        reporting ``("job_done",)``.
        """
        while True:
            try:
                event = self._queue.get(timeout=poll_interval)
            except queue.Empty:
                if not self._process.is_alive():
                    self._process.join(timeout=5.0)
                    exitcode = self._process.exitcode
                    if exitcode not in (0, None):
                        yield ("crashed", _describe_exit(exitcode))
                    return
                yield None
                continue
            yield event
            if event[0] == "job_done":
                return

    def stop(self, grace_seconds: float = 5.0) -> None:
        """Request cancellation and ensure the child process is not left running."""
        self._cancel_event.set()
        if not self._process.is_alive():
            return
        self._process.terminate()
        self._process.join(timeout=grace_seconds)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=grace_seconds)
