"""Background worker pool for resumable upmix jobs."""

from __future__ import annotations

import shutil
import threading
import zipfile
import copy
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer_web.job_subprocess import JobSubprocess, WorkItem
from upmixer_web.jobs import get_job, reset_incomplete_jobs
from upmixer_web.manifests import materialize_manifest
from upmixer_web.models import Artifact, Job, JobTrack, Project, ProjectTrack
from upmixer_web.project_storage import ProjectStemStorage
from upmixer_web.projects import get_project
from upmixer_web.project_routing import merge_scene, routing_for_scene
from upmixer_web.storage import AudioSink, AudioSource, ObjectStorage


class JobPaused(Exception):
    pass


class JobDeleting(Exception):
    pass


class WorkerManager:
    """Polls durable state and executes jobs with bounded concurrency."""

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
        self._dispatcher: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._active: set[str] = set()
        self._lock = threading.Lock()

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
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="upmixer-dispatch", daemon=True)
        self._dispatcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._dispatcher:
            self._dispatcher.join(timeout=10)
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)

    def notify(self) -> None:
        self._wake.set()

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._submit_available()
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _submit_available(self) -> None:
        if not self._executor:
            return
        with self._lock:
            capacity = self.worker_count - len(self._active)
        if capacity <= 0:
            return
        with self.sessions() as session:
            projects = list(session.scalars(
                select(Project.id)
                .where(Project.status.in_(("queued", "expanding")))
                .order_by(Project.created_at)
                .limit(capacity)
            ))
            remaining = max(0, capacity - len(projects))
            jobs = list(session.scalars(
                select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(remaining)
            ))
        for project_id in projects:
            active_id = f"project:{project_id}"
            with self._lock:
                if active_id in self._active:
                    continue
                self._active.add(active_id)
            future = self._executor.submit(self._run_project, project_id)
            future.add_done_callback(lambda _future, value=active_id: self._finished(value))
        for job_id in jobs:
            active_id = f"job:{job_id}"
            with self._lock:
                if active_id in self._active:
                    continue
                self._active.add(active_id)
            future = self._executor.submit(self._run_job, job_id)
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
                source_keys = [asset.storage_key for asset in job.import_batch.assets]
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
                        job, job.import_batch, input_paths, work_dir, self.stem_cache_dir,
                        reference_path,
                    )
                    if job.project_id and job.project_snapshot:
                        project = get_project(session, job.project_id)
                        if not project:
                            raise JobDeleting()
                        snapshot_tracks = job.project_snapshot.get("tracks", {})
                        track_by_asset = {track.asset_id: track for track in project.tracks}
                        for asset_data, job_track in zip(manifest["assets"], job.tracks, strict=True):
                            project_track = track_by_asset.get(job_track.asset_id)
                            if not project_track:
                                raise RuntimeError("Project export source track is missing")
                            asset_data["stem_cache_dir"] = str(
                                self.project_stems.track_root(project.id, project_track.id)
                            )
                            overrides = snapshot_tracks.get(project_track.id, {}).get("manifest_overrides", {})
                            for block, value in overrides.items():
                                if isinstance(value, dict):
                                    asset_data[block] = copy.deepcopy(value)
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
                        asset_id = track.asset_id
                        track.status = "running"
                        track.error = None
                        session.commit()

                    config = UpmixConfig()
                    apply_asset_job(config, asset_job)
                    stems = asset_job.engine.get("stems")
                    if stems:
                        config.stems = stems

                    custom_routing = None
                    if (
                        mode == "stem"
                        and config.stem_routing is None
                        and job.project_id
                        and job.project_snapshot
                    ):
                        with self.sessions() as project_session:
                            project = get_project(project_session, job.project_id)
                            project_track = next(
                                (item for item in project.tracks if item.asset_id == asset_id),
                                None,
                            ) if project else None
                            if not project or not project_track:
                                raise JobDeleting()
                            overrides = job.project_snapshot.get("tracks", {}).get(project_track.id, {})
                            scene = merge_scene(
                                job.project_snapshot.get("scene", {}),
                                overrides.get("scene_overrides", {}),
                            )
                            custom_routing = routing_for_scene(scene, config)

                    item = WorkItem(
                        track_id=track_id,
                        mode=mode,
                        input_path=asset_job.input,
                        output_path=asset_job.output,
                        config=config,
                        input_format_override=asset_job.engine.get("input_format"),
                        custom_routing=custom_routing,
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
                for track in project.tracks:
                    track.status = "queued"
                    track.progress = 0.0
                    track.error = None
                session.commit()
                source_keys = [asset.storage_key for asset in project.import_batch.assets]
                track_ids = [track.id for track in project.tracks]
                manifest = copy.deepcopy(project.manifest)
                requested_stems = list(project.requested_stems)

            with ExitStack() as sources:
                input_paths = [sources.enter_context(self.source.materialize(key)) for key in source_keys]
                data = copy.deepcopy(manifest)
                data.setdefault("engine", {})["mode"] = "stem"
                data["engine"]["stems"] = requested_stems
                data["assets"] = [
                    {
                        "input": str(input_path),
                        "output": str(work_dir / f"{index:02d}-prepare.wav"),
                        "stem_cache_dir": str(self.project_stems.track_root(project_id, track_id)),
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
                        mode="stem",
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
                                project_row.status_message = message.strip()
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
                            self.project_stems.write_source_preview(track, input_path)
                    session.commit()

            with self.sessions() as session:
                project = get_project(session, project_id)
                if not project:
                    return
                next_generation = project.stem_generation + 1
                for track in project.tracks:
                    self.project_stems.catalogue_track(session, project, track, next_generation)
                project.prepared_stems = list(project.requested_stems)
                project.stem_generation = next_generation
                project.status = "ready"
                project.progress = 1.0
                project.status_message = "Project stems ready"
                project.error = None
                session.commit()
        except JobDeleting:
            self._delete_project(project_id)
        except Exception as exc:
            with self.sessions() as session:
                project = session.get(Project, project_id)
                if project:
                    project.status = "expansion_failed" if project.prepared_stems else "failed"
                    project.error = str(exc)
                    project.status_message = "Project stem preparation failed"
                    for track in project.tracks:
                        if track.status == "running":
                            track.status = "failed"
                            track.error = str(exc)
                    session.commit()
        finally:
            if job_process is not None:
                job_process.stop()
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

    def _delete_project(self, project_id: str) -> None:
        self.project_stems.delete_project(project_id)
        with self.sessions() as session:
            project = session.get(Project, project_id)
            if project:
                session.delete(project)
                session.commit()

    def delete_now_project(self, project_id: str) -> None:
        self._delete_project(project_id)
