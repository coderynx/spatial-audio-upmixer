"""Job execution: the worker-pool runner for durable upmix jobs."""

from __future__ import annotations

import shutil
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer_web.features.jobs.service import get_job
from upmixer_web.shared.manifests import materialize_manifest
from upmixer_web.shared.models import Artifact, Job, JobTrack
from upmixer_web.worker.manager import JobDeleting, JobPaused
from upmixer_web.worker.subprocess import JobSubprocess, WorkItem


class JobRunnerMixin:
    """Durable-job execution methods for ``WorkerManager``.

    Reads/writes the host's ``sessions``, ``source``, ``sink``,
    ``work_root``, ``stem_cache_dir``, and ``storage`` attributes, and calls
    back into ``self._control`` — all set up by
    ``upmixer_web.worker.manager._ManagerCore``. A project export is just a
    job whose manifest already carries everything it needs (see
    ``features.projects.service.project_export_job`` and
    ``shared.manifests.materialize_manifest``); this runner never reaches
    into ``features.projects``.
    """

    def _update_progress(self, job_id: str, track_id: str, track_index: int, track_count: int, message: str, fraction: float) -> None:
        self._control(job_id)
        with self.sessions() as session:
            job = session.get(Job, job_id)
            track = session.get(JobTrack, track_id)
            if not job or not track:
                raise JobDeleting()
            track.progress = max(0.0, min(1.0, fraction))
            job.progress = (track_index + track.progress) / max(1, track_count)
            job.status_message = message.strip()
            session.commit()

    def _run_job(self, job_id: str) -> None:
        work_dir = self.work_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.sessions() as session:
                job = get_job(session, job_id)
                if not job or job.status != "queued":
                    return
                job.status = "running"
                job.started_at = job.started_at or datetime.now(timezone.utc)
                job.error = None
                job.status_message = "Preparing job"
                for track in job.tracks:
                    if track.status not in {"completed"}:
                        track.status = "queued"
                session.commit()
                track_ids = [track.id for track in job.tracks]
                source_keys = [track.asset.storage_key for track in job.tracks]
                reference_key = (
                    job.mastering_reference.storage_key
                    if job.mastering_reference is not None
                    else None
                )

            with ExitStack() as sources:
                input_paths = [
                    sources.enter_context(self.source.materialize(key))
                    for key in source_keys
                ]
                reference_path = (
                    sources.enter_context(self.source.materialize(reference_key))
                    if reference_key is not None
                    else None
                )
                with self.sessions() as session:
                    job = get_job(session, job_id)
                    if not job:
                        raise JobDeleting()
                    manifest = materialize_manifest(
                        job, input_paths, work_dir, self.stem_cache_dir,
                        reference_path,
                    )
                _, asset_jobs = parse_manifest(manifest)
                mode = asset_jobs[0].engine.get("mode", "realtime") if asset_jobs else "realtime"

                work_items: list[WorkItem] = []
                items_by_track: dict[str, WorkItem] = {}
                index_by_track: dict[str, int] = {}
                for index, (track_id, asset_job) in enumerate(zip(track_ids, asset_jobs, strict=True)):
                    self._control(job_id)
                    index_by_track[track_id] = index
                    with self.sessions() as session:
                        track = session.get(JobTrack, track_id)
                        if not track:
                            raise JobDeleting()
                        if track.status == "completed":
                            continue
                        track.status = "running"
                        track.error = None
                        session.commit()

                    config = UpmixConfig()
                    apply_asset_job(config, asset_job)
                    stems = asset_job.engine.get("stems")
                    if stems:
                        config.stems = stems

                    item = WorkItem(
                        track_id=track_id,
                        mode=mode,
                        input_path=asset_job.input,
                        output_path=asset_job.output,
                        config=config,
                        input_format_override=asset_job.engine.get("input_format"),
                    )
                    work_items.append(item)
                    items_by_track[track_id] = item

                job_process: JobSubprocess | None = None
                try:
                    if work_items:
                        job_process = JobSubprocess(work_items)
                        job_process.start()
                        for event in job_process.events():
                            if event is None:
                                self._control(job_id)
                                continue
                            kind = event[0]
                            if kind == "progress":
                                _, track_id, message, fraction = event
                                self._update_progress(
                                    job_id, track_id, index_by_track[track_id], len(track_ids), message, fraction,
                                )
                            elif kind == "track_done":
                                _, track_id, result_dict = event
                                item = items_by_track[track_id]
                                output_path = Path(item.output_path)
                                output_key = f"jobs/{job_id}/outputs/{output_path.name}"
                                _, size = self.sink.store(output_key, output_path)
                                with self.sessions() as session:
                                    track = session.get(JobTrack, track_id)
                                    if not track:
                                        raise JobDeleting()
                                    track.status = "completed"
                                    track.progress = 1.0
                                    track.output_key = output_key
                                    track.result = result_dict
                                    session.add(Artifact(
                                        job_id=job_id,
                                        track_id=track_id,
                                        kind="upmix",
                                        filename=output_path.name,
                                        content_type="audio/wav",
                                        storage_key=output_key,
                                        size_bytes=size,
                                    ))
                                    downmix_path = item.config.downmix_output_path
                                    if item.config.downmix_enabled and downmix_path and Path(downmix_path).is_file():
                                        downmix_output = Path(downmix_path)
                                        downmix_key = f"jobs/{job_id}/outputs/{downmix_output.name}"
                                        _, downmix_size = self.sink.store(downmix_key, downmix_output)
                                        session.add(Artifact(
                                            job_id=job_id,
                                            track_id=track_id,
                                            kind="downmix",
                                            filename=downmix_output.name,
                                            content_type="audio/wav",
                                            storage_key=downmix_key,
                                            size_bytes=downmix_size,
                                        ))
                                    session.commit()
                            elif kind in ("track_error", "crashed"):
                                message = event[-1]
                                raise RuntimeError(message)
                            elif kind == "job_done":
                                break

                    self._create_bundle(job_id)
                    with self.sessions() as session:
                        job = session.get(Job, job_id)
                        if job:
                            job.status = "completed"
                            job.progress = 1.0
                            job.status_message = "All outputs ready"
                            job.finished_at = datetime.now(timezone.utc)
                            session.commit()
                finally:
                    if job_process is not None:
                        job_process.stop()
        except JobPaused:
            with self.sessions() as session:
                job = session.get(Job, job_id)
                if job and job.status != "deleting":
                    job.status = "paused"
                    job.status_message = "Paused"
                    for track in job.tracks:
                        if track.status == "running":
                            track.status = "paused"
                    session.commit()
        except JobDeleting:
            self._delete_job(job_id)
        except Exception as exc:
            with self.sessions() as session:
                job = session.get(Job, job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)
                    job.status_message = "Processing failed"
                    job.finished_at = datetime.now(timezone.utc)
                    for track in job.tracks:
                        if track.status == "running":
                            track.status = "failed"
                            track.error = str(exc)
                    session.commit()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _create_bundle(self, job_id: str) -> None:
        with self.sessions() as session:
            job = get_job(session, job_id)
            if not job or len(job.tracks) < 2:
                return
            upmixes = [item for item in job.artifacts if item.kind == "upmix"]
            if len(upmixes) != len(job.tracks):
                return
            artifacts = [item for item in job.artifacts if item.kind in {"upmix", "downmix"}]
            safe_name = "".join(character if character.isalnum() or character in " -_." else "_" for character in job.name).strip() or "upmix"
            bundle_path = self.work_root / job_id / f"{safe_name}.zip"
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as archive:
                for artifact in artifacts:
                    archive.write(self.storage.local_path(artifact.storage_key), arcname=artifact.filename)
            key = f"jobs/{job_id}/bundle/{bundle_path.name}"
            _, size = self.sink.store(key, bundle_path)
            session.add(Artifact(
                job_id=job_id,
                kind="bundle",
                filename=bundle_path.name,
                content_type="application/zip",
                storage_key=key,
                size_bytes=size,
            ))
            session.commit()

    def _delete_job(self, job_id: str) -> None:
        self.storage.delete_prefix(f"jobs/{job_id}")
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job:
                session.delete(job)
                session.commit()

    def delete_now(self, job_id: str) -> None:
        self._delete_job(job_id)
