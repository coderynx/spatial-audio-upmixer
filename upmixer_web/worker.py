"""Background worker pool for resumable upmix jobs."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
import tempfile
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
from upmixer.mastering.match_reference import ReferenceMatchProcessor
from upmixer.separation.stem_pipeline import PreMasterAbort, StemUpmixPipeline
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


_log = logging.getLogger("upmixer_web")

_PROGRESS_LOG_LIMIT = 200


def _reference_match_signature(project: Project) -> str | None:
    """Hash of everything a project's reference-match FIR asset depends on.

    Deliberately excludes live mixing edits (routing/rebalance/stem EQ/
    anchor) — the asset is a bounded Tier-3 approximation computed against a
    canonical server-rendered bed, not the browser's live-edited mix (see
    docs/contracts/preview_export_parity.md Ledger D12). Also excludes
    ``strength`` and ``rms``: both are wet/dry-blend and gate knobs applied
    live in the browser preview (`ProjectDetailPage.tsx`'s `previewMastering`)
    and never change the FIR bytes or `rms_gain_db` that
    `compute_channel_filters` produces, so hashing them only forces
    needless full-song recomputes while the strength slider is dragged.
    Returns ``None`` when no reference is attached, meaning "no asset should
    exist."
    """
    if not project.mastering_reference_id:
        return None
    manifest = project.manifest if isinstance(project.manifest, dict) else {}
    mastering = manifest.get("mastering", {}) if isinstance(manifest.get("mastering"), dict) else {}
    match = mastering.get("match_reference", {}) if isinstance(mastering.get("match_reference"), dict) else {}
    mixing = manifest.get("mixing", {}) if isinstance(manifest.get("mixing"), dict) else {}
    reference = project.mastering_reference
    payload = {
        "reference_id": project.mastering_reference_id,
        "reference_sha256": reference.sha256 if reference else None,
        "channel_layout": mixing.get("channel_layout"),
        "spectrum": match.get("spectrum"),
        "max_db": match.get("max_db"),
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _reference_match_needs_work(project: Project | None, project_stems: ProjectStemStorage) -> bool:
    """Whether `prepare_reference_match` would do anything for *project* right
    now — mirrors that method's own early-outs (see below) so
    `schedule_reference_match` can skip opening a `reference_match_pending`
    window for a call that is provably a no-op.

    `prepare_reference_match` remains the authority: it re-validates
    everything itself, since project state can change between scheduling and
    the run actually executing.
    """
    if not project:
        return False
    target_signature = _reference_match_signature(project)
    if target_signature is None:
        # No reference attached — work is needed only to clear a
        # still-existing asset from a prior reference.
        return (
            project_stems.read_reference_match_meta(project.id) is not None
            or project_stems.reference_match_fir_path(project.id) is not None
        )
    if not project.prepared_stems or not project.tracks or not project.import_batch.assets:
        return False
    existing = project_stems.read_reference_match_meta(project.id)
    return not existing or existing.get("signature") != target_signature


def _append_progress_log(project: Project, message: str, fraction: float) -> None:
    """Append one entry to a project's realtime preparation log, capped in length."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "fraction": fraction,
    }
    project.progress_log = [*project.progress_log, entry][-_PROGRESS_LOG_LIMIT:]


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
        self._refmatch_executor: ThreadPoolExecutor | None = None
        self._refmatch_pending: set[str] = set()
        self._refmatch_running: set[str] = set()

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
                project.progress_log = []
                _append_progress_log(project, project.status_message, 0.0)
                for track in project.tracks:
                    track.status = "queued"
                    track.progress = 0.0
                    track.error = None
                session.commit()
                source_keys = [asset.storage_key for asset in project.import_batch.assets]
                track_ids = [track.id for track in project.tracks]
                manifest = copy.deepcopy(project.manifest)
                requested_stems = list(project.requested_stems)
                preview_quality = project.preview_quality

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
        except JobDeleting:
            self._delete_project(project_id)
        except Exception as exc:
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

    def schedule_reference_match(self, project_id: str) -> None:
        """Queue a project's reference-match precompute on a dedicated
        single-thread executor, coalescing rapid repeat calls into one
        trailing run instead of one run per call.

        `prepare_reference_match` runs a full-song mix pass and is too heavy
        to run inline on an API request thread (see
        docs/contracts/preview_export_parity.md Ledger D12) — every settings
        save (debounced at 350ms in the browser) used to call it directly,
        pegging the CPU while a reference-match slider was being dragged.
        This schedules the work on `_refmatch_executor` instead; if a run is
        already in flight for this project, the request is recorded and
        picked up by that run's trailing check rather than starting a second
        one.

        Callers (the settings-save endpoint, stem-prep completion) always
        run after their own `session.commit()`, so the fresh session opened
        here to check `_reference_match_needs_work` sees current state. This
        check is purely an optimisation for `reference_match_pending`'s
        window — it must never open (and the caller's UI must never show
        "preparing…") for an edit that changes nothing the FIR depends on
        (see `_reference_match_signature`'s exclusions). It is not a
        correctness gate: `prepare_reference_match` re-validates everything
        itself once it actually runs.
        """
        if not self._refmatch_executor:
            return
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not _reference_match_needs_work(project, self.project_stems):
                return
        with self._lock:
            self._refmatch_pending.add(project_id)
            if project_id in self._refmatch_running:
                return
            self._refmatch_running.add(project_id)
        self._refmatch_executor.submit(self._run_reference_match, project_id)

    def _run_reference_match(self, project_id: str) -> None:
        while True:
            with self._lock:
                if project_id not in self._refmatch_pending:
                    self._refmatch_running.discard(project_id)
                    return
                self._refmatch_pending.discard(project_id)
            try:
                self.prepare_reference_match(project_id)
            except Exception:
                # Non-fatal: a stale or missing reference-match asset just
                # means the preview falls back to no reference-match EQ —
                # it must not fail project preparation or the settings save.
                _log.exception(
                    "Reference-match precompute failed for project %s", project_id
                )

    def reference_match_pending(self, project_id: str) -> bool:
        """Whether a reference-match recompute is queued or running for
        *project_id* — surfaced to the API so the frontend keeps polling
        until the async asset lands."""
        with self._lock:
            return project_id in self._refmatch_pending or project_id in self._refmatch_running

    def prepare_reference_match(self, project_id: str) -> None:
        """Recompute a project's server-side reference-match FIR asset if its
        signature has drifted since the last compute; a cheap no-op
        otherwise.

        Runs in the caller's thread rather than a :class:`JobSubprocess`:
        stems are already cached (``process_file`` skips separation), so no
        Torch/ONNX inference — the reason jobs need child-process crash
        isolation — happens here. Safe to call after every project stem
        preparation and every settings save; only a signature mismatch
        triggers the actual mix + PSD-match pass.
        """
        with self.sessions() as session:
            project = get_project(session, project_id)
            if not project:
                return
            target_signature = _reference_match_signature(project)
            if target_signature is None:
                self.project_stems.clear_reference_match(project_id)
                return
            if not project.prepared_stems or not project.tracks or not project.import_batch.assets:
                return
            existing = self.project_stems.read_reference_match_meta(project_id)
            if existing and existing.get("signature") == target_signature:
                return
            reference = project.mastering_reference
            if reference is None:
                return
            manifest = copy.deepcopy(project.manifest)
            requested_stems = list(project.requested_stems)
            track_id = project.tracks[0].id
            source_key = project.import_batch.assets[0].storage_key
            reference_key = reference.storage_key

        with ExitStack() as sources:
            input_path = sources.enter_context(self.source.materialize(source_key))
            reference_path = sources.enter_context(self.source.materialize(reference_key))

            with tempfile.TemporaryDirectory() as tmp_dir:
                data = copy.deepcopy(manifest)
                data.setdefault("engine", {})["mode"] = "stem"
                data["engine"]["stems"] = requested_stems
                data["assets"] = [{
                    "input": str(input_path),
                    "output": str(Path(tmp_dir) / "refmatch-prepare.wav"),
                    "stem_cache_dir": str(self.project_stems.track_root(project_id, track_id)),
                }]
                _, asset_jobs = parse_manifest(data)
                asset_job = asset_jobs[0]
                config = UpmixConfig()
                apply_asset_job(config, asset_job)
                config.stems = asset_job.engine.get("stems") or requested_stems

                captured: dict[str, object] = {}

                def _capture_and_abort(channels, sr, _output_fmt) -> None:
                    processor = ReferenceMatchProcessor(
                        reference_path=str(reference_path),
                        strength=config.mastering_match_ref_strength,
                        match_spectrum=config.mastering_match_ref_spectrum,
                        match_rms=config.mastering_match_ref_rms,
                        max_correction_db=config.mastering_match_ref_max_db,
                        sample_rate=sr,
                    )
                    fir_by_channel, rms_gain_db = processor.compute_channel_filters(channels)
                    captured["fir_by_channel"] = fir_by_channel
                    captured["rms_gain_db"] = rms_gain_db
                    captured["sample_rate"] = sr
                    raise PreMasterAbort()

                pipeline = StemUpmixPipeline(config=config)
                try:
                    pipeline.process_file(
                        str(input_path), asset_job.output,
                        pre_master_hook=_capture_and_abort,
                    )
                except PreMasterAbort:
                    pass
                finally:
                    pipeline.close()

        if "fir_by_channel" not in captured:
            return
        self.project_stems.write_reference_match(
            project_id,
            captured["fir_by_channel"],
            captured["rms_gain_db"],
            captured["sample_rate"],
            target_signature,
            config.mastering_match_ref_strength,
            config.mastering_match_ref_spectrum,
            config.mastering_match_ref_rms,
        )

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
