"""Background worker pool for resumable upmix jobs."""

from __future__ import annotations

import shutil
import threading
import zipfile
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer.pipeline import UpmixPipeline
from upmixer.separation.stem_pipeline import StemUpmixPipeline
from upmixer_web.jobs import get_job, reset_incomplete_jobs
from upmixer_web.manifests import materialize_manifest
from upmixer_web.models import Artifact, Job, JobTrack
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
        worker_count: int,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.source = source
        self.sink = sink
        self.work_root = work_root
        self.stem_cache_dir = stem_cache_dir
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
            ids = list(session.scalars(
                select(Job.id)
                .where(Job.status == "queued")
                .order_by(Job.created_at)
                .limit(capacity)
            ))
        for job_id in ids:
            with self._lock:
                if job_id in self._active:
                    continue
                self._active.add(job_id)
            future = self._executor.submit(self._run_job, job_id)
            future.add_done_callback(lambda _future, value=job_id: self._finished(value))

    def _finished(self, job_id: str) -> None:
        with self._lock:
            self._active.discard(job_id)
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
                _, asset_jobs = parse_manifest(manifest)
                mode = asset_jobs[0].engine.get("mode", "realtime") if asset_jobs else "realtime"
                stem_pipeline: StemUpmixPipeline | None = None
                if mode == "stem":
                    stem_pipeline = StemUpmixPipeline()
                try:
                    for index, (track_id, asset_job) in enumerate(zip(track_ids, asset_jobs, strict=True)):
                        self._control(job_id)
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
                        callback = lambda message, fraction, ti=index, tid=track_id: self._update_progress(
                            job_id, tid, ti, len(track_ids), message, fraction
                        )
                        if mode == "stem":
                            assert stem_pipeline is not None
                            stem_pipeline.config = config
                            result = stem_pipeline.process_file(
                                asset_job.input,
                                asset_job.output,
                                input_format_override=asset_job.engine.get("input_format"),
                                progress_callback=callback,
                            )
                        else:
                            result = UpmixPipeline(config).process_file(
                                asset_job.input,
                                asset_job.output,
                                input_format_override=asset_job.engine.get("input_format"),
                                progress_callback=callback,
                            )

                        output_path = Path(asset_job.output)
                        output_key = f"jobs/{job_id}/outputs/{output_path.name}"
                        _, size = self.sink.store(output_key, output_path)
                        with self.sessions() as session:
                            track = session.get(JobTrack, track_id)
                            if not track:
                                raise JobDeleting()
                            track.status = "completed"
                            track.progress = 1.0
                            track.output_key = output_key
                            track.result = result.to_dict()
                            session.add(Artifact(
                                job_id=job_id,
                                track_id=track_id,
                                kind="upmix",
                                filename=output_path.name,
                                content_type="audio/wav",
                                storage_key=output_key,
                                size_bytes=size,
                            ))
                            session.commit()

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
                    if stem_pipeline:
                        stem_pipeline.close()
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
            artifacts = [item for item in job.artifacts if item.kind == "upmix"]
            if len(artifacts) != len(job.tracks):
                return
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
