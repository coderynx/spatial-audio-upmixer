"""Command-line interface for upmixer.

Priority order for all parameters
-----------------------------------
CLI flags  >  manifest values  >  UpmixConfig defaults

Usage
-----
# Positional args (classic mode)
upmixer input.wav output.wav --format 7.1.2 --mode stem

# Manifest-driven (all params in a file)
upmixer --manifest job.yaml

# Mixed: manifest provides defaults, CLI flags override
upmixer --manifest job.yaml input.flac output_override.wav --format 7.1.4
"""

import logging
import sys
from pathlib import Path

_log = logging.getLogger("upmixer")

from upmixer.cli.args import build_parser
from upmixer.cli.flags import _apply_cli_flags, _apply_resource_limits, _parse_key_value_pairs  # noqa: F401
from upmixer.cli.manifest_run import _run_manifest_assets
from upmixer.config import UpmixConfig
from upmixer.pipeline import UpmixPipeline

# Import-time side effect: registers manifest block keys. MasteringChain only
# imports these lazily inside process(), so without this a fresh process
# rejects mastering.*/routing.* manifest fields as "Unknown manifest field".
import upmixer.mastering.bass  # noqa: F401
import upmixer.mastering.compressor  # noqa: F401
import upmixer.mastering.eq  # noqa: F401
import upmixer.mastering.match_reference  # noqa: F401
import upmixer.routing.channel_router  # noqa: F401


def main() -> None:
    parser = build_parser()

    if "--manifest-keys" in sys.argv:
        from upmixer.manifest import list_manifest_keys
        print("\nValid manifest keys (key → UpmixConfig attribute):\n")
        for mk, desc in list_manifest_keys().items():
            print(f"  {mk:<30}  {desc}")
        print()
        sys.exit(0)

    args = parser.parse_args()

    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet or args.json:
        log_level = logging.WARNING
    else:
        log_level = logging.INFO

    logging.basicConfig(level=log_level, format="%(message)s", stream=sys.stderr)

    config = UpmixConfig()

    sample_rate_set = args.output_sample_rate is not None

    if args.manifest is not None:
        from upmixer.manifest import (
            load_manifest, validate_manifest, parse_manifest, ManifestError,
        )
        try:
            _raw = load_manifest(args.manifest)
            validate_manifest(_raw)
        except ManifestError as exc:
            parser.error(str(exc))
        _meta, _asset_jobs = parse_manifest(_raw)
        _run_manifest_assets(_asset_jobs, _meta, args, parser)
        return

    _apply_cli_flags(config, args, sample_rate_set)

    mode = args.mode or "realtime"
    _apply_resource_limits(args.cpu_priority, mode)
    stem_model_dir = args.stem_model_dir or None
    input_format   = args.input_format   or None

    batch_inputs = args.inputs
    batch_dir    = args.batch_dir
    output_dir   = args.output_dir
    is_batch = bool(batch_inputs or batch_dir)

    if is_batch:
        from upmixer.batch import BatchProcessor, resolve_batch_jobs

        if not output_dir:
            parser.error("Batch mode requires --output-dir.")

        output_ext = ".wav"  # ADM-BWF uses WAV container; always .wav
        try:
            jobs = resolve_batch_jobs(
                input_paths=None,
                batch_dir=batch_dir,
                output_dir=output_dir,
                output_ext=output_ext,
                explicit_jobs=None,
                batch_inputs=batch_inputs,
                recursive=args.recursive,
                include_patterns=args.include,
                output_template=args.output_template,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if not jobs:
            if batch_dir:
                parser.error(
                    f"No input files found in '{batch_dir}'. "
                    "Make sure the path exists and contains .wav or .flac files."
                )
            else:
                parser.error("No input files found for batch processing.")

        workers = args.batch_workers or 1
        processor = BatchProcessor(
            config=config,
            mode=mode,
            stem_model_dir=stem_model_dir,
            workers=workers,
            progress_callback=lambda done, total, path: (
                _log.info("[%d/%d] %s", done + 1, total, path) if path else None
            ),
            overwrite=args.overwrite,
            resume=args.resume,
            state_file=args.state_file or str(Path(output_dir) / ".upmixer-state.json"),
        )
        try:
            if args.dry_run:
                from upmixer.execution import preflight_job
                plans = [preflight_job(j.input_path, j.output_path, config, j.input_format_override) for j in jobs]
                if args.json:
                    import json
                    print(json.dumps({"jobs": plans}, indent=2))
                else:
                    for plan in plans:
                        print(f"READY: {plan['input']} -> {plan['output']}")
                return
            batch_result = processor.process(jobs)
        except ValueError as exc:
            parser.error(str(exc))

        for fail in batch_result.failed:
            _log.error("FAILED: %s — %s", fail["input"], fail["error"])

        if args.json:
            print(batch_result.to_json())
        else:
            _log.info(
                "Batch complete: %d/%d succeeded in %.1fs",
                len(batch_result.jobs), len(jobs), batch_result.wall_time_s,
            )
        if args.report:
            from upmixer.execution import write_report
            write_report(args.report, {
                "planned": [
                    {"input": job.input_path, "output": job.output_path}
                    for job in jobs
                ],
                **batch_result.to_dict(),
            })
        if batch_result.failed:
            raise SystemExit(1)

    else:
        input_path  = args.input
        output_path = args.output

        if not input_path:
            parser.error(
                "input file is required. "
                "Pass it as a positional argument or use --inputs / --batch-dir "
                "for batch processing.  For manifest-driven jobs use --manifest."
            )
        if not output_path:
            parser.error(
                "output file is required. "
                "Pass it as a positional argument or set 'output' in the manifest."
            )

        from upmixer.execution import PreflightError, RunState, preflight_job, write_report
        try:
            plan = preflight_job(input_path, output_path, config, input_format)
        except PreflightError as exc:
            parser.error(str(exc))
        state = RunState.load(args.state_file or f"{output_path}.upmixer-state.json")
        output_exists = Path(output_path).exists()
        if output_exists and args.resume and state and state.matches(plan):
            summary = {"skipped": [{"input": input_path, "output": output_path, "reason": "resume"}]}
            if args.json:
                import json
                print(json.dumps(summary, indent=2))
            if args.report:
                write_report(args.report, summary)
            return
        if output_exists and not args.overwrite:
            parser.error(f"Output already exists: {output_path}. Use --overwrite or --resume.")
        if args.dry_run:
            if args.json:
                import json
                print(json.dumps({"jobs": [plan]}, indent=2))
            else:
                print(f"READY: {input_path} -> {output_path}")
            return

        if mode == "stem":
            from upmixer.separation.stem_pipeline import StemUpmixPipeline
            stem_pipeline = StemUpmixPipeline(
                config=config,
                model_dir=stem_model_dir,
            )
            result = stem_pipeline.process_file(
                input_path, output_path,
                input_format_override=input_format,
            )
        else:
            pipeline = UpmixPipeline(config)
            result = pipeline.process_file(
                input_path, output_path,
                input_format_override=input_format,
            )

        if args.json:
            print(result.to_json())
        if state is not None:
            state.record(plan, result)
        if args.report:
            write_report(args.report, {
                "planned": [{"input": input_path, "output": output_path}],
                "jobs": [result.to_dict()],
                "failed": [],
                "skipped": [],
            })


if __name__ == "__main__":
    main()
