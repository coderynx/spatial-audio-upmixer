"""Argument parser construction for the ``upmixer`` CLI."""

import argparse

from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP

from .args_mastering import add_mastering_args
from .args_stems import add_stem_args
from .args_types import positive_float

_INPUT_FORMAT_CHOICES = sorted(INPUT_FORMAT_MAP.keys())
_OUTPUT_FORMAT_CHOICES = list(FORMAT_MAP)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Universal multichannel audio upmixer. "
            "Upmix mono, stereo, or any surround format to a higher channel layout. "
            "Supported inputs: mono, stereo, 5.0, 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2.\n\n"
            "All parameters can be specified in a YAML/JSON manifest file "
            "(--manifest).  CLI flags always override manifest values."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help=(
            "Input audio file (WAV/FLAC). "
            "Optional when --manifest specifies an 'input' key."
        ),
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help=(
            "Output multichannel audio file. "
            "Optional when --manifest specifies an 'output' key."
        ),
    )

    parser.add_argument(
        "--manifest", "-m",
        default=None,
        metavar="FILE",
        help=(
            "YAML (.yaml/.yml) or JSON (.json) manifest file defining the "
            "upmix job.  All CLI parameters can be set in the manifest. "
            "CLI flags override manifest values. "
            "See --manifest-keys for a list of valid manifest keys."
        ),
    )
    parser.add_argument(
        "--manifest-keys",
        action="store_true",
        help="Print all valid manifest keys and their types, then exit.",
    )

    parser.add_argument(
        "--inputs",
        nargs="+",
        default=None,
        metavar="FILE",
        help=(
            "Two or more input audio files for batch processing (WAV/FLAC). "
            "Files may be from different directories. Requires --output-dir. "
            "Example: --inputs /dir1/a.wav /dir2/b.flac /dir3/c.wav"
        ),
    )
    parser.add_argument(
        "--batch-dir",
        default=None,
        metavar="DIR",
        help=(
            "Process all WAV/FLAC files in DIR (batch mode). "
            "Files are sorted by name. Requires --output-dir."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Output directory for batch mode (--inputs or --batch-dir). "
            "Output filenames are derived from input stems."
        ),
    )
    parser.add_argument(
        "--batch-workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallel workers for realtime batch mode (default: 1). "
            "Stem mode is always sequential (model reuse requires single process)."
        ),
    )
    parser.add_argument(
        "--recursive", action="store_true",
        help="Recursively scan --batch-dir instead of its top level only.",
    )
    parser.add_argument(
        "--include", action="append", default=None, metavar="GLOB",
        help="Include pattern for --batch-dir (repeatable; default: *.wav and *.flac).",
    )
    parser.add_argument(
        "--output-template", default="{stem}{ext}", metavar="TEMPLATE",
        help="Batch output name template. Fields: {stem}, {name}, {ext}, {relative_stem}.",
    )

    parser.add_argument(
        "--format",
        choices=_OUTPUT_FORMAT_CHOICES,
        default=None,
        help=(
            "Output channel format (default: 5.1, or as set by --manifest). "
            f"Choices: {', '.join(_OUTPUT_FORMAT_CHOICES)}."
        ),
    )

    parser.add_argument(
        "--input-format",
        choices=_INPUT_FORMAT_CHOICES,
        default=None,
        metavar="FMT",
        help=(
            "Override auto-detected input format. "
            f"Choices: {', '.join(_INPUT_FORMAT_CHOICES)}. "
            "Required when channel count is ambiguous (8ch = 7.1 or 5.1.2; "
            "10ch = 7.1.2 or 5.1.4)."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=["realtime", "stem"],
        default=None,
        help=(
            "Processing mode (default: realtime). "
            "'realtime': coherence-based STFT pipeline, works on any input. "
            "'stem': source-separation pipeline — separates instruments then "
            "places each in 3D space. "
            "Requires: pip install 'upmixer[separation-cpu]'."
        ),
    )
    parser.add_argument(
        "--stems",
        default=None,
        metavar="STEM[,STEM...]",
        help=(
            "Comma-separated list of stems to extract in stem mode. "
            "Valid: vocals, bass, drums, guitar, piano, other, kick, snare, "
            "toms, hi-hat, ride, crash, crowd, lead-vocals, backing-vocals. "
            "Default: vocals,bass,drums,guitar,piano,other. "
            "Example: --stems lead-vocals,backing-vocals,kick,snare,crowd"
        ),
    )
    parser.add_argument(
        "--stem-model-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory to cache downloaded separation models "
            "(default: ~/.cache/upmixer-models)."
        ),
    )

    parser.add_argument("--center-gain",           type=float, default=None, help="Center channel output gain (default: 0.85)")
    parser.add_argument("--surround-gain",         type=float, default=None, help="Side surround channel gain (default: 0.6)")
    parser.add_argument("--back-gain",             type=float, default=None, help="Rear back channel gain for 7.1 formats (default: 0.55)")
    parser.add_argument("--height-gain",           type=float, default=None, help="Height channel gain for Atmos formats (default: 0.55)")
    parser.add_argument("--lfe-gain",              type=float, default=None, help="LFE channel gain (default: 0.3162)")

    parser.add_argument("--center-extraction-gain",type=float, default=None, help="Mid signal → center channel (default: 0.85)")
    parser.add_argument("--center-attenuation",    type=float, default=None, help="Center attenuation in FL/FR (default: 0.5)")

    parser.add_argument("--lfe-cutoff",            type=float, default=None, metavar="HZ", help="LFE low-pass cutoff in Hz (default: 120)")

    parser.add_argument("--height-low-rolloff-gain",type=float, default=None, help="Sub-bass gain for height channels (default: 0.15)")
    parser.add_argument("--height-high-shelf-gain", type=float, default=None, help="HF presence boost for height channels (default: 1.5)")

    parser.add_argument("--fft-size",   type=int,  default=None, help="STFT window size")
    parser.add_argument("--no-auto-fft",action="store_true",     help="Disable automatic FFT size scaling for high sample rates")
    parser.add_argument("--block-size", type=int,  default=None, help="Streaming block size in samples (default: 4096)")

    parser.add_argument("--no-normalize", action="store_true", help="Disable output energy normalization (mixing phase)")
    parser.add_argument("--content-mix-strength", type=float, default=None, metavar="S", help="Content-aware mixing strength 0.0–1.0 (default: 1.0)")
    parser.add_argument(
        "--spatial-profile",
        choices=["auto", "balanced", "intimate", "rhythmic", "spacious", "live", "detailed"],
        default=None,
        help="Creative spatial profile (default: auto).",
    )
    parser.add_argument("--spatial-intensity", type=float, default=None, metavar="S", help="Spatial adaptation strength 0.0–1.0 (default: 1.0)")
    parser.add_argument("--no-spatial-preanalysis", action="store_true", help="Disable offline spatial analysis.")
    parser.add_argument(
        "--binaural-profile",
        choices=["studio", "listening", "flat"],
        default=None,
        help="Spatial Audio Engine profile for --output-type binaural (default: studio).",
    )
    parser.add_argument(
        "--transaural-profile",
        choices=["stereo", "smart_speaker", "car", "laptop", "phone"],
        default=None,
        help="Spatial Audio Engine profile for --output-type transaural (default: stereo).",
    )
    parser.add_argument(
        "--content-hf-analysis-hz",
        type=lambda value: positive_float(value, "--content-hf-analysis-hz"),
        default=None,
        metavar="HZ",
        help="High-frequency threshold for stem content analysis (default: 4000)",
    )
    parser.add_argument(
        "--no-loudness-normalize",
        action="store_true",
        help="Disable BS.1770-4 loudness normalization (mastering phase, default: enabled)",
    )
    parser.add_argument(
        "--loudness-target",
        type=float,
        default=None,
        metavar="LKFS",
        help="Target integrated loudness in LKFS (default: -18.0)",
    )
    parser.add_argument(
        "--limiter-lookahead",
        type=float,
        default=None,
        metavar="MS",
        help="Look-ahead limiter window in ms — how far ahead it sees oncoming peaks (default: 5.0)",
    )
    parser.add_argument(
        "--limiter-release",
        type=float,
        default=None,
        metavar="MS",
        help="Look-ahead limiter release time in ms — how fast gain recovers after a peak (default: 50.0)",
    )
    parser.add_argument(
        "--output-type",
        choices=["wav", "adm-bwf", "binaural", "transaural"],
        default=None,
        help=(
            "'wav' = standard multichannel WAV. "
            "'adm-bwf' = Dolby ADM-BWF (Logic Pro, DaVinci Resolve, Pro Tools). "
            "'binaural' = Spatial Audio Engine headphone-stereo render of --format's "
            "bed (--format must be 5.1.4, 7.1.2, or 7.1.4; see --binaural-profile). "
            "'transaural' = Spatial Audio Engine crosstalk-cancelled speaker-stereo "
            "render of --format's bed (same bed requirement; see "
            "--transaural-profile). "
            "'--format stereo' delivers WAV only. "
            "Default: 'wav' (or as set by manifest)."
        ),
    )
    parser.add_argument("--output-subtype", choices=["PCM_16", "PCM_24", "PCM_32"], default=None, help="Output bit depth (default: PCM_24)")
    parser.add_argument("--output-sample-rate", type=int, default=None, metavar="HZ", help="Resample output (e.g. 48000, 96000). Default: same as input.")
    parser.add_argument(
        "--downmix-output",
        default=None,
        metavar="PATH",
        help="Write an ITU-R BS.775-4 stereo downmix WAV alongside the multichannel output.",
    )
    parser.add_argument(
        "--downmix-surround-coeff",
        type=float,
        choices=[0.7071, 0.5, 0.0],
        default=None,
        metavar="K",
        help="ITU-R BS.775-4 Annex 8 surround coefficient k_s (default: 0.7071).",
    )

    parser.add_argument("--preview",          action="store_true", help="Process a short excerpt (default 30 s) instead of the full file.")
    parser.add_argument("--preview-duration", type=float, default=None, metavar="S", help="Preview window length in seconds (default: 30).")
    parser.add_argument("--preview-start",    type=float, default=None, metavar="S", help="Preview start time in seconds (default: auto-center).")

    add_mastering_args(parser)
    add_stem_args(parser)


    parser.add_argument(
        "--cpu-priority",
        choices=["auto", "normal", "low"],
        default="auto",
        help=(
            "Process scheduling priority and numeric-library thread use. "
            "'auto' uses full resources for stem mode and reduced resources "
            "for realtime mode. Default: auto."
        ),
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet",   "-q", action="store_true", help="Suppress all output except warnings and errors.")
    verbosity.add_argument("--verbose", "-v", action="store_true", help="Enable debug-level logging.")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary of the result to stdout when done.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and print resolved jobs without processing audio.",
    )
    output_policy = parser.add_mutually_exclusive_group()
    output_policy.add_argument(
        "--overwrite", action="store_true",
        help="Replace existing output files after preflight validation.",
    )
    output_policy.add_argument(
        "--resume", action="store_true",
        help="Skip outputs verified against the saved run state; never overwrites untracked files.",
    )
    parser.add_argument(
        "--state-file", metavar="FILE", default=None,
        help="JSON state file used to record completed jobs and support --resume.",
    )
    parser.add_argument(
        "--report", metavar="FILE", default=None,
        help="Write a JSON summary report after processing.",
    )

    return parser
