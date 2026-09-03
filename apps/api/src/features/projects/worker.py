"""Project execution: the worker-pool runner that prepares project stems."""

from __future__ import annotations

import copy
import logging
import shutil
from contextlib import ExitStack
from datetime import datetime, timezone

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer_web.features.projects.layouts import track_prepare_overrides
from upmixer_web.features.projects.service import get_project
from upmixer_web.shared.models import Project, ProjectTrack
from upmixer_web.worker.manager import JobDeleting
from upmixer_web.worker.subprocess import JobSubprocess, WorkItem

_PROGRESS_LOG_LIMIT = 200
_log = logging.getLogger(__name__)


def _append_progress_log(project: Project, message: str, fraction: float) -> None:
    """Append one entry to a project's realtime preparation log, capped in length."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "fraction": fraction,
    }
    project.progress_log = [*project.progress_log, entry][-_PROGRESS_LOG_LIMIT:]


class ProjectRunnerMixin:
    """Project stem-preparation execution methods for ``WorkerManager``.

    Reads/writes the host's ``sessions``, ``source``, ``work_root``, and
    ``project_stems`` attributes, and calls back into
    ``self._control_project`` — all set up by
    ``upmixer_web.worker.manager._ManagerCore``. Depends on
    ``self.schedule_reference_match``, provided by ``ReferenceMatchMixin``.
    """

    def _run_project(self, project_id: str) -> None:
        """Prepare a project through the public stem pipeline and catalogue its cache."""
        work_dir = self.work_root / f"project-{project_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        job_process: JobSubprocess | None = None
        try:
            with self.sessions() as session:
                project = get_project(session, project_id)
                if not project or project.status not in {"queued", "expanding"}:
                    return
                project.status = "preparing" if not project.prepared_stems else "expanding"
                project.progress = 0.0
                project.error = None
                project.status_message = "Preparing project stems"
                project.progress_log = []
                _append_progress_log(project, project.status_message, 0.0)
                for track in project.tracks:
                    track.status = "queued"
                    track.progress = 0.0
                    track.error = None
                session.commit()
                # Read each source through the track's own asset FK, not
                # project.import_batch.assets — a project's tracks may span
                # more than one import (or none, for an empty project) once
                # assets are added incrementally.
                source_keys = [track.asset.storage_key for track in project.tracks]
                track_ids = [track.id for track in project.tracks]
                track_overrides = {
                    track.id: copy.deepcopy(track_prepare_overrides(track)) for track in project.tracks
                }
                manifest = copy.deepcopy(project.manifest)
                requested_stems = list(project.requested_stems)
                preview_quality = project.preview_quality
            _log.info("project_preparation_started project_id=%s track_count=%d", project_id, len(track_ids))

            with ExitStack() as sources:
                input_paths = [sources.enter_context(self.source.materialize(key)) for key in source_keys]
                data = copy.deepcopy(manifest)
                data.setdefault("engine", {})["mode"] = "stem"
                data["engine"]["stems"] = requested_stems
                data["assets"] = [
                    {
                        "input": str(input_path),
                        "output": str(work_dir / f"{index:02d}-prepare.wav"),
                        "stem_output_dir": str(self.project_stems.stem_dir(project_id, track_id)),
                        # core's parse_manifest deep-merges any block key here
                        # (engine/format/mixing/...) over the manifest's global
                        # blocks per AssetJob — this is what makes a track's
                        # own stems/sample_rate/subtype/channel_layout take
                        # effect during preparation rather than only at mix time.
                        **{
                            block: value
                            for block, value in track_overrides.get(track_id, {}).items()
                            if isinstance(value, dict) and value
                        },
                    }
                    for index, (input_path, track_id) in enumerate(zip(input_paths, track_ids, strict=True))
                ]
                _, asset_jobs = parse_manifest(data)

                work_items: list[WorkItem] = []
                index_by_track: dict[str, int] = {}
                for index, (track_id, asset_job) in enumerate(zip(track_ids, asset_jobs, strict=True)):
                    self._control_project(project_id)
                    with self.sessions() as session:
                        project = get_project(session, project_id)
                        track = session.get(ProjectTrack, track_id)
                        if not project or not track:
                            raise JobDeleting()
                        track.status = "running"
                        session.commit()
                    config = UpmixConfig()
                    apply_asset_job(config, asset_job)
                    config.stems = asset_job.engine.get("stems") or requested_stems
                    index_by_track[track_id] = index
                    work_items.append(WorkItem(
                        track_id=track_id,
                        mode="stem_prepare",
                        input_path=asset_job.input,
                        output_path=asset_job.output,
                        config=config,
                    ))

                if work_items:
                    job_process = JobSubprocess(work_items)
                    job_process.start()
                    for event in job_process.events():
                        if event is None:
                            self._control_project(project_id)
                            continue
                        kind = event[0]
                        if kind == "progress":
                            _, track_id, message, fraction = event
                            with self.sessions() as session:
                                project_row = session.get(Project, project_id)
                                track_row = session.get(ProjectTrack, track_id)
                                if not project_row or not track_row:
                                    raise JobDeleting()
                                track_row.progress = max(0.0, min(1.0, fraction))
                                project_row.progress = (
                                    (index_by_track[track_id] + track_row.progress) / max(1, len(track_ids))
                                )
                                stripped = message.strip()
                                project_row.status_message = stripped
                                logged = (
                                    stripped if len(track_ids) == 1
                                    else f"Track {index_by_track[track_id] + 1}/{len(track_ids)}: {stripped}"
                                )
                                _append_progress_log(project_row, logged, project_row.progress)
                                session.commit()
                        elif kind == "track_done":
                            _, track_id, _result_dict = event
                            with self.sessions() as session:
                                track = session.get(ProjectTrack, track_id)
                                if track:
                                    track.status = "ready"
                                    track.progress = 1.0
                                    session.commit()
                        elif kind in ("track_error", "crashed"):
                            message = event[-1]
                            raise RuntimeError(message)
                        elif kind == "job_done":
                            break

                with self.sessions() as session:
                    for track_id, input_path in zip(track_ids, input_paths, strict=True):
                        track = session.get(ProjectTrack, track_id)
                        if track:
                            self.project_stems.write_source_preview(track, input_path, quality=preview_quality)
                    session.commit()

            with self.sessions() as session:
                project = get_project(session, project_id)
                if not project:
                    return
                next_generation = project.stem_generation + 1
                for track in project.tracks:
                    self.project_stems.catalogue_track(session, project, track, next_generation, quality=project.preview_quality)
                project.prepared_stems = list(project.requested_stems)
                project.stem_generation = next_generation
                project.status = "ready"
                project.progress = 1.0
                project.status_message = "Project stems ready"
                project.error = None
                _append_progress_log(project, project.status_message, 1.0)
                session.commit()

            self.schedule_reference_match(project_id)
            _log.info("project_preparation_completed project_id=%s", project_id)
        except JobDeleting:
            _log.info("project_deleted project_id=%s", project_id)
            self._delete_project(project_id)
        except Exception as exc:
            _log.exception("project_preparation_failed project_id=%s", project_id)
            with self.sessions() as session:
                project = session.get(Project, project_id)
                if project:
                    project.status = "expansion_failed" if project.prepared_stems else "failed"
                    project.error = str(exc)
                    project.status_message = "Project stem preparation failed"
                    _append_progress_log(project, f"{project.status_message}: {exc}", project.progress)
                    for track in project.tracks:
                        if track.status == "running":
                            track.status = "failed"
                            track.error = str(exc)
                    session.commit()
        finally:
            if job_process is not None:
                job_process.stop()
            shutil.rmtree(work_dir, ignore_errors=True)

    def _delete_project(self, project_id: str) -> None:
        _log.info("project_deleting project_id=%s", project_id)
        self.project_stems.delete_project(project_id)
        with self.sessions() as session:
            project = session.get(Project, project_id)
            if project:
                session.delete(project)
                session.commit()

    def delete_now_project(self, project_id: str) -> None:
        self._delete_project(project_id)
