"""Composed background worker pool: dispatcher core plus per-feature runner
mixins. See ``upmixer_web.worker.manager`` for the shared dispatch/control
protocol and ``docs/web_api_architecture.md`` for the composition contract.
"""

from __future__ import annotations

from upmixer_web.features.jobs.worker import JobRunnerMixin
from upmixer_web.features.projects.worker import ProjectRunnerMixin
from upmixer_web.features.projects.worker_reference_match import ReferenceMatchMixin
from upmixer_web.worker.manager import JobDeleting, JobPaused, _ManagerCore


class WorkerManager(JobRunnerMixin, ProjectRunnerMixin, ReferenceMatchMixin, _ManagerCore):
    """Polls durable state and executes jobs and projects with bounded concurrency."""


__all__ = ["WorkerManager", "JobPaused", "JobDeleting"]
