"""Manifest-driven asset processing for the ``upmixer`` CLI."""

import argparse
import logging
from pathlib import Path

from upmixer_cli.flags import _apply_cli_flags, _apply_resource_limits
from upmixer.config import UpmixConfig
from upmixer.pipeline import UpmixPipeline

_log = logging.getLogger("upmixer")


def _run_manifest_assets(asset_jobs, meta, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Process all assets resolved from a manifest file.

    Applies per-asset config deep-merged with CLI flag overrides.  In stem
    mode the separator model is loaded once and reused across all assets.
    """
    from upmixer.manifest import apply_asset_job

    if not asset_jobs:
        parser.error("Manifest contains no assets to process.")

    if meta:
        parts = [p for p in (meta.name, meta.author) if p]
        if parts:
            _log.info("Manifest: %s", " — ".join(parts))
        if meta.description:
            _log.info("  %s", meta.description)

    first_engine = asset_jobs[0].engine
    mode = args.mode or first_engine.get("mode", "realtime")
    _apply_resource_limits(args.cpu_priority, mode)
    stem_model_dir = args.stem_model_dir or first_engine.get("stem_model_dir", None)
    n = len(asset_jobs)

    def _build_cfg(job):
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        _apply_cli_flags(cfg, args, args.output_sample_rate is not None)
        return cfg

    def _apply_per_asset_stems(cfg, job):
        """Propagate per-asset stems from engine block into cfg.stems."""
        from upmixer.separation.stem_plan import normalize_stems as _normalize
        asset_stems = job.engine.get("stems")
        if asset_stems:
            cfg.stems = _normalize(asset_stems)
        elif args.stems and cfg.stems is None:
            raw = [s.strip() for s in args.stems.split(",") if s.strip()]
            cfg.stems = _normalize(raw)

    from upmixer.execution import PreflightError, RunState, preflight_job, write_report

    state = RunState.load(args.state_file or f"{args.manifest}.upmixer-state.json")
    prepared = []
    skipped: list[dict] = []
    seen_outputs: set[str] = set()
    for job in asset_jobs:
        cfg = _build_cfg(job)
        _apply_per_asset_stems(cfg, job)
        input_fmt = args.input_format or job.engine.get("input_format")
        try:
            plan = preflight_job(job.input, job.output, cfg, input_fmt)
        except PreflightError as exc:
            parser.error(str(exc))
        output_key = str(Path(job.output).resolve())
        if output_key in seen_outputs:
            parser.error(f"Multiple manifest assets resolve to output: {job.output}")
        seen_outputs.add(output_key)
        if Path(job.output).exists() and args.resume and state.matches(plan):
            skipped.append({"input": job.input, "output": job.output, "reason": "resume"})
        elif Path(job.output).exists() and not args.overwrite:
            parser.error(f"Output already exists: {job.output}. Use --overwrite or --resume.")
        else:
            prepared.append((job, cfg, input_fmt, plan))

    if args.dry_run:
        dry_report = {"jobs": [item[3] for item in prepared], "skipped": skipped, "failed": []}
        if args.json:
            import json
            print(json.dumps(dry_report, indent=2))
        else:
            for plan in dry_report["jobs"]:
                print(f"READY: {plan['input']} -> {plan['output']}")
            for skipped_job in skipped:
                print(f"SKIP:  {skipped_job['input']} -> {skipped_job['output']} (resume)")
        if args.report:
            write_report(args.report, dry_report)
        return

    report: dict = {
        "planned": [{"input": item[0].input, "output": item[0].output} for item in prepared],
        "jobs": [],
        "skipped": skipped,
        "failed": [],
    }
    if not prepared:
        if args.report:
            write_report(args.report, report)
        return

    n = len(prepared)
    if mode == "stem":
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        first_cfg = prepared[0][1]
        with StemUpmixPipeline(
            config=first_cfg,
            model_dir=stem_model_dir,
        ) as pipeline:
            for i, (job, cfg, input_fmt, plan) in enumerate(prepared):
                _log.info("[%d/%d] %s", i + 1, n, job.input)
                pipeline.config = cfg
                try:
                    result = pipeline.process_file(job.input, job.output, input_format_override=input_fmt)
                    state.record(plan, result)
                    report["jobs"].append(result.to_dict())
                    if args.json:
                        print(result.to_json())
                except Exception as exc:
                    _log.error("FAILED: %s — %s", job.input, exc)
                    report["failed"].append({"input": job.input, "output": job.output, "error": str(exc)})
    else:
        for i, (job, cfg, input_fmt, plan) in enumerate(prepared):
            _log.info("[%d/%d] %s", i + 1, n, job.input)
            pipeline_rt = UpmixPipeline(cfg)
            try:
                result = pipeline_rt.process_file(job.input, job.output, input_format_override=input_fmt)
                state.record(plan, result)
                report["jobs"].append(result.to_dict())
                if args.json:
                    print(result.to_json())
            except Exception as exc:
                _log.error("FAILED: %s — %s", job.input, exc)
                report["failed"].append({"input": job.input, "output": job.output, "error": str(exc)})

    if args.report:
        write_report(args.report, report)
    if report["failed"]:
        raise SystemExit(1)
