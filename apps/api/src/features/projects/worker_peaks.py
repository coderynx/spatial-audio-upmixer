"""Waveform-peaks backfill scheduling, mixed into ``WorkerManager``."""

from __future__ import annotations

import logging

from upmixer_web.features.projects.service import get_project
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.shared.models import Project

_log = logging.getLogger("upmixer_web")


def _peaks_needs_work(project: Project | None, project_stems: ProjectStemStorage) -> bool:
    """Whether any of *project*'s tracks lacks a current waveform-peaks asset.

    Peaks are written by `catalogue_track`, so this only opens for projects
    catalogued before peaks existed, or whose stems were re-prepared at a
    newer generation.
    """
    if not project or project.status != "ready" or not project.prepared_stems:
        return False
    for track in project.tracks:
        meta = project_stems.read_track_peaks_meta(project.id, track.id)
        if not meta or meta.get("generation") != project.stem_generation:
            return True
    return False


class PeaksMixin:
    """Waveform-peaks backfill scheduling/execution methods for
    ``WorkerManager``.

    Reads/writes the host's ``sessions``, ``project_stems``, ``_lock``,
    ``_peaks_executor``, ``_peaks_pending``, and ``_peaks_running``
    attributes — set up by ``WorkerManager.__init__`` and
    ``WorkerManager.start``/``stop``.
    """

    def schedule_peaks(self, project_id: str) -> None:
        """Queue a waveform-peaks backfill for a project catalogued before
        peaks existed, coalescing repeat calls into one trailing run.

        Normal preparation writes peaks inside `catalogue_track`, so this only
        fires for pre-existing projects. It is called from the project-read
        endpoint, which the editor polls every 2s — the `_peaks_needs_work`
        gate plus the pending/running sets are what keep that from launching a
        run per poll.
        """
        if not self._peaks_executor:
            return
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not _peaks_needs_work(project, self.project_stems):
                return
        with self._lock:
            self._peaks_pending.add(project_id)
            if project_id in self._peaks_running:
                return
            self._peaks_running.add(project_id)
        self._peaks_executor.submit(self._run_peaks, project_id)

    def _run_peaks(self, project_id: str) -> None:
        while True:
            with self._lock:
                if project_id not in self._peaks_pending:
                    self._peaks_running.discard(project_id)
                    return
                self._peaks_pending.discard(project_id)
            try:
                self.prepare_peaks(project_id)
            except Exception:
                # Non-fatal: a missing peaks asset only means the timeline has
                # no waveform to draw; playback and every other surface work.
                _log.exception("Waveform peak precompute failed for project %s", project_id)

    def peaks_pending(self, project_id: str) -> bool:
        """Whether a waveform-peaks backfill is queued or running."""
        with self._lock:
            return project_id in self._peaks_pending or project_id in self._peaks_running

    def prepare_peaks(self, project_id: str) -> None:
        """Rebuild every stale track's waveform envelopes from its previews."""
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not project or project.status != "ready":
                return
            for track in project.tracks:
                meta = self.project_stems.read_track_peaks_meta(project.id, track.id)
                if meta and meta.get("generation") == project.stem_generation:
                    continue
                stems = [stem for stem in track.stems if stem.generation == project.stem_generation]
                if not stems:
                    continue
                self.project_stems.rebuild_track_peaks(track, stems, project.stem_generation)
            session.commit()
