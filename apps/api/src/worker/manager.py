"""Background worker pool dispatcher core.

Polls durable job/project state and dispatches bounded-concurrency work to
per-feature runner mixins (``JobRunnerMixin`` in
``upmixer_web.features.jobs.worker``, ``ProjectRunnerMixin`` in
``upmixer_web.features.projects.worker``) composed onto this core by
``upmixer_web.worker.WorkerManager``. This module owns only the
dispatch/control protocol shared by both kinds of work, not job- or
project-specific execution.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from upmixer_web.features.jobs.service import reset_incomplete_jobs
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.shared.models import Job, Project
from upmixer_web.shared.storage import AudioSink, AudioSource, ObjectStorage


class JobPaused(Exception):
    pass


class JobDeleting(Exception):
    pass


class _ManagerCore:
    """Shared dispatch loop, concurrency control, and cooperative cancellation
    checks. Runner mixins call back into ``self._control``/``self._control_project``
    and are dispatched via ``self._run_job``/``self._run_project``, which they
    provide.
    """

    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: ObjectStorage,
        source: AudioSource,
        sink: AudioSink,
        work_root: Path,
        stem_cache_dir: Path,
        project_stems: ProjectStemStorage,
        worker_count: int,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.source = source
        self.sink = sink
        self.work_root = work_root
        self.stem_cache_dir = stem_cache_dir
        self.project_stems = project_stems
        self.worker_count = worker_count
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._refmatch_executor: ThreadPoolExecutor | None = None
        self._refmatch_pending: set[str] = set()
        self._refmatch_running: set[str] = set()
        self._peaks_executor: ThreadPoolExecutor | None = None
        self._peaks_pending: set[str] = set()
        self._peaks_running: set[str] = set()

    def start(self) -> None:
        with self.sessions() as session:
            reset_incomplete_jobs(session)
            for project in session.scalars(select(Project).where(Project.status.in_(("preparing", "expanding")))):
                project.status = "expanding" if project.prepared_stems else "queued"
                project.status_message = "Recovered after service restart"
                for track in project.tracks:
                    if track.status == "running":
                        track.status = "queued"
            session.commit()
        self._executor = ThreadPoolExecutor(max_workers=self.worker_count, thread_name_prefix="upmixer-job")
        self._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upmixer-refmatch")
        self._peaks_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upmixer-peaks")
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="upmixer-dispatch", daemon=True)
        self._dispatcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._dispatcher:
            self._dispatcher.join(timeout=10)
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
        if self._refmatch_executor:
            self._refmatch_executor.shutdown(wait=False, cancel_futures=True)
        if self._peaks_executor:
            self._peaks_executor.shutdown(wait=False, cancel_futures=True)

    def notify(self) -> None:
        self._wake.set()

    def pause_dispatch(self) -> None:
        """Stop dispatching new queued work application-wide; in-flight work
        runs to completion."""
        self._paused.set()

    def resume_dispatch(self) -> None:
        self._paused.clear()
        self._wake.set()

    def is_dispatch_paused(self) -> bool:
        return self._paused.is_set()

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._submit_available()
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _submit_available(self) -> None:
        if not self._executor or self._paused.is_set():
            return
        with self._lock:
            capacity = self.worker_count - len(self._active)
        if capacity <= 0:
            return
        # Merge both queues by creation time so a long-running project prepare
        # cannot starve export jobs (or vice versa) indefinitely on a single
        # worker — whichever was queued first gets the next free slot.
        with self.sessions() as session:
            projects = list(session.execute(
                select(Project.id, Project.created_at)
                .where(Project.status.in_(("queued", "expanding")))
            ))
            jobs = list(session.execute(
                select(Job.id, Job.created_at).where(Job.status == "queued")
            ))
        candidates = sorted(
            [("project", pid, created_at) for pid, created_at in projects]
            + [("job", jid, created_at) for jid, created_at in jobs],
            key=lambda item: item[2],
        )[:capacity]
        for kind, item_id, _created_at in candidates:
            active_id = f"{kind}:{item_id}"
            with self._lock:
                if active_id in self._active:
                    continue
                self._active.add(active_id)
            target = self._run_project if kind == "project" else self._run_job
            future = self._executor.submit(target, item_id)
            future.add_done_callback(lambda _future, value=active_id: self._finished(value))

    def _finished(self, active_id: str) -> None:
        with self._lock:
            self._active.discard(active_id)
        self._wake.set()

    def _control(self, job_id: str) -> None:
        with self.sessions() as session:
            status = session.scalar(select(Job.status).where(Job.id == job_id))
        if status in {"pause_requested", "paused"}:
            raise JobPaused()
        if status == "deleting" or status is None:
            raise JobDeleting()
        if self._stop.is_set():
            raise JobPaused()

    def _control_project(self, project_id: str) -> None:
        with self.sessions() as session:
            status = session.scalar(select(Project.status).where(Project.id == project_id))
        if status == "deleting" or status is None:
            raise JobDeleting()
