"""Batch processing for upmixer.

Processes multiple audio files through a single pipeline instance, reusing
loaded models (e.g. the stem separation neural network) across all files.

Usage — CLI:
    # Multiple files from any directories
    upmixer track1.wav /other/dir/track2.flac --output-dir /out/ --mode stem

    # Whole directory scan
    upmixer --batch-dir /albums/ok-computer/ --output-dir /out/ --mode stem

    # Manifest with cross-directory file list
    upmixer --manifest batch.yaml

Usage — library:
    from upmixer.batch import BatchProcessor, BatchJob, resolve_batch_jobs

    jobs = resolve_batch_jobs(
        input_paths=["/dir1/a.wav", "/dir2/b.flac"],
        output_dir="/out/",
    )
    result = BatchProcessor(config, mode="stem").process(jobs)
    print(result.to_json())
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from upmixer.config import UpmixConfig
from upmixer.execution import PreflightError, RunState, preflight_job
from upmixer.result import UpmixResult

_log = logging.getLogger("upmixer")


@dataclass
class BatchJob:
    """A single file to process within a batch."""

    input_path: str
    output_path: str
    input_format_override: str | None = None


@dataclass
class BatchResult:
    """Summary of a completed batch run."""

    jobs: list[UpmixResult] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    total_audio_duration_s: float = 0.0
    wall_time_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "jobs": [r.to_dict() for r in self.jobs],
            "failed": self.failed,
            "skipped": self.skipped,
            "total_audio_duration_s": self.total_audio_duration_s,
            "wall_time_s": self.wall_time_s,
            "succeeded": len(self.jobs),
            "total": len(self.jobs) + len(self.failed) + len(self.skipped),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def resolve_batch_jobs(
    input_paths: list[str] | None = None,
    batch_dir: str | None = None,
    output_dir: str | None = None,
    output_ext: str = ".wav",
    explicit_jobs: list[dict] | None = None,
    batch_inputs: list[str] | None = None,
    recursive: bool = False,
    include_patterns: list[str] | None = None,
    output_template: str = "{stem}{ext}",
) -> list[BatchJob]:
    """Build a list of BatchJobs from various input sources.

    Priority: explicit_jobs > batch_inputs > input_paths > batch_dir.

    Args:
        input_paths: Arbitrary file paths from CLI positional args.
        batch_dir: Directory to scan for *.wav and *.flac files.
        output_dir: Base directory for derived output paths.
        output_ext: Extension for derived output filenames (default: ".wav").
        explicit_jobs: List of {input, output?} dicts from manifest batch.jobs.
        batch_inputs: List of file paths from manifest batch.inputs.

    Returns:
        Ordered list of BatchJob instances.
    """
    def _derive_output(input_path: str) -> str:
        if not output_dir:
            raise ValueError(
                f"output_dir required to derive output path for: {input_path}"
            )
        basename = os.path.basename(input_path)
        stem = os.path.splitext(basename)[0]
        relative = os.path.relpath(input_path, batch_dir) if batch_dir else basename
        relative_stem = os.path.splitext(relative)[0]
        try:
            rendered = output_template.format(
                stem=stem, name=basename, ext=output_ext, relative_stem=relative_stem,
            )
        except KeyError as exc:
            raise ValueError(
                f"Unknown output template field {exc}. Valid: stem, name, ext, relative_stem."
            ) from exc
        rendered_path = os.path.normpath(rendered)
        if os.path.isabs(rendered_path) or rendered_path.startswith(".." + os.sep):
            raise ValueError("output_template must resolve inside output_dir")
        return os.path.join(output_dir, rendered_path)

    if explicit_jobs:
        jobs: list[BatchJob] = []
        for entry in explicit_jobs:
            inp = entry.get("input") or entry.get("input_path")
            if not inp:
                raise ValueError(f"Batch job entry missing 'input': {entry}")
            out = entry.get("output") or entry.get("output_path") or _derive_output(inp)
            jobs.append(BatchJob(
                input_path=inp,
                output_path=out,
                input_format_override=entry.get("input_format"),
            ))
        return jobs

    file_list: list[str] | None = batch_inputs or input_paths

    if file_list:
        return [
            BatchJob(input_path=p, output_path=_derive_output(p))
            for p in file_list
        ]

    if batch_dir:
        safe_dir = glob.escape(batch_dir)
        patterns = include_patterns or ["*.wav", "*.flac"]
        all_files: list[str] = []
        for pattern in patterns:
            search = os.path.join(safe_dir, "**", pattern) if recursive else os.path.join(safe_dir, pattern)
            all_files.extend(glob.glob(search, recursive=recursive))
        all_files = sorted(set(all_files), key=lambda path: os.path.relpath(path, batch_dir))
        return [
            BatchJob(input_path=p, output_path=_derive_output(p))
            for p in all_files
        ]

    return []


def _realtime_worker(args: tuple) -> UpmixResult:
    """Top-level function for ProcessPoolExecutor workers (must be picklable)."""
    input_path, output_path, input_fmt, config_dict = args
    from upmixer.config import UpmixConfig
    from upmixer.pipeline import UpmixPipeline
    cfg = UpmixConfig(**config_dict)
    pipeline = UpmixPipeline(cfg)
    return pipeline.process_file(input_path, output_path, input_format_override=input_fmt)


class BatchProcessor:
    """Processes a list of BatchJobs through a single pipeline instance.

    Stem mode: Sequential. The neural network model is loaded once on the first
    file and reused for all subsequent files — the primary performance benefit
    for album-sized batches. Separated stems are cached to disk so that
    re-runs of the same batch (e.g. after adjusting loudness settings) skip
    the slow separation step entirely. Cache dir defaults to
    ``~/.cache/upmixer-stems``; override via ``config.stem_cache_dir`` or
    ``--stem-cache-dir``. The separator is released when done.

    Realtime mode: Sequential (workers=1) or parallel via ProcessPoolExecutor
    (workers>1). Uses processes (not threads) to avoid GIL + numpy memory
    contention.

    Args:
        config: Per-file processing configuration.
        mode: "realtime" (STFT coherence) or "stem" (source separation).
        stem_model_dir: Override model cache directory.
        workers: Parallel workers for realtime mode (ignored in stem mode).
        progress_callback: Called as (done, total, current_input_path) before
            each file starts.
    """

    def __init__(
        self,
        config: UpmixConfig,
        mode: str = "realtime",
        stem_model_dir: str | None = None,
        workers: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
        overwrite: bool = True,
        resume: bool = False,
        state_file: str | None = None,
    ) -> None:
        self._config = config
        self._mode = mode
        self._stem_model_dir = stem_model_dir
        self._workers = max(1, workers)
        self._progress = progress_callback
        self._overwrite = overwrite
        self._resume = resume
        self._state = RunState.load(state_file) if state_file else None

    def process(self, jobs: list[BatchJob]) -> BatchResult:
        """Run all jobs and return a BatchResult."""
        t0 = time.monotonic()
        result = BatchResult()
        planned, skipped = self._preflight(jobs)
        result.skipped.extend(skipped)
        total = len(jobs)

        if self._mode == "stem":
            processed = self._process_stem(planned, total)
        else:
            processed = self._process_realtime(planned, total)

        result.jobs.extend(processed.jobs)
        result.failed.extend(processed.failed)
        result.total_audio_duration_s = processed.total_audio_duration_s
        result.wall_time_s = time.monotonic() - t0
        return result

    def _preflight(self, jobs: list[BatchJob]) -> tuple[list[BatchJob], list[dict]]:
        """Validate every job before any output is created."""
        seen: set[str] = set()
        planned: list[BatchJob] = []
        skipped: list[dict] = []
        for job in jobs:
            plan = preflight_job(
                job.input_path, job.output_path, self._config, job.input_format_override,
            )
            output = str(Path(job.output_path).resolve())
            if output in seen:
                raise PreflightError(f"Multiple batch jobs resolve to output: {job.output_path}")
            seen.add(output)
            exists = Path(job.output_path).exists()
            if exists and self._resume and self._state and self._state.matches(plan):
                skipped.append({"input": job.input_path, "output": job.output_path, "reason": "resume"})
            elif exists and not self._overwrite:
                raise PreflightError(
                    f"Output already exists: {job.output_path}. Use --overwrite or --resume."
                )
            else:
                planned.append(job)
        return planned, skipped

    def _record_state(self, job: BatchJob, result: UpmixResult) -> None:
        if self._state is None:
            return
        plan = preflight_job(job.input_path, job.output_path, self._config, job.input_format_override)
        self._state.record(plan, result)

    _DEFAULT_STEM_CACHE_DIR: str = os.path.join(
        os.path.expanduser("~"), ".cache", "upmixer-stems"
    )

    def _process_stem(self, jobs: list[BatchJob], total: int) -> BatchResult:
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        if self._config.stem_cache_dir:
            effective_config = self._config
        else:
            cache_dir = self._DEFAULT_STEM_CACHE_DIR
            _log.info("  Stem cache: auto-enabled at %s", cache_dir)
            effective_config = replace(self._config, stem_cache_dir=cache_dir)

        result = BatchResult()
        with StemUpmixPipeline(
            config=effective_config,
            model_dir=self._stem_model_dir,
        ) as pipeline:
            for done, job in enumerate(jobs):
                if self._progress:
                    self._progress(done, total, job.input_path)
                _log.info("[%d/%d] %s", done + 1, total, job.input_path)
                try:
                    r = pipeline.process_file(
                        job.input_path,
                        job.output_path,
                        input_format_override=job.input_format_override,
                    )
                    result.jobs.append(r)
                    self._record_state(job, r)
                    result.total_audio_duration_s += r.duration_seconds
                except Exception as exc:
                    _log.error("FAILED: %s — %s", job.input_path, exc)
                    result.failed.append({
                        "input": job.input_path,
                        "output": job.output_path,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    })
        if self._progress:
            self._progress(total, total, "")
        return result

    def _process_realtime(self, jobs: list[BatchJob], total: int) -> BatchResult:
        from upmixer.pipeline import UpmixPipeline

        result = BatchResult()

        if self._workers == 1:
            pipeline = UpmixPipeline(self._config)
            for done, job in enumerate(jobs):
                if self._progress:
                    self._progress(done, total, job.input_path)
                _log.info("[%d/%d] %s", done + 1, total, job.input_path)
                try:
                    r = pipeline.process_file(
                        job.input_path,
                        job.output_path,
                        input_format_override=job.input_format_override,
                    )
                    result.jobs.append(r)
                    self._record_state(job, r)
                    result.total_audio_duration_s += r.duration_seconds
                except Exception as exc:
                    _log.error("FAILED: %s — %s", job.input_path, exc)
                    result.failed.append({
                        "input": job.input_path,
                        "output": job.output_path,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    })
            if self._progress:
                self._progress(total, total, "")
        else:
            # Parallel: each worker process constructs its own pipeline.
            # Use config.__dict__ for pickling (UpmixConfig is a plain dataclass).
            from dataclasses import asdict as _asdict
            config_dict = _asdict(self._config)
            work_items = [
                (job.input_path, job.output_path, job.input_format_override, config_dict)
                for job in jobs
            ]
            done_count = 0
            job_map = {i: jobs[i] for i in range(len(jobs))}
            with ProcessPoolExecutor(max_workers=self._workers) as ex:
                futures = {ex.submit(_realtime_worker, item): i for i, item in enumerate(work_items)}
                for fut in as_completed(futures):
                    idx = futures[fut]
                    job = job_map[idx]
                    done_count += 1
                    if self._progress:
                        self._progress(done_count, total, job.input_path)
                    try:
                        r = fut.result()
                        result.jobs.append(r)
                        self._record_state(job, r)
                        result.total_audio_duration_s += r.duration_seconds
                    except Exception as exc:
                        _log.error("FAILED: %s — %s", job.input_path, exc)
                        result.failed.append({
                            "input": job.input_path,
                            "output": job.output_path,
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        })

        return result
