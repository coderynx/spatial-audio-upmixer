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

import argparse
import logging
import sys

from upmixer.config import UpmixConfig
from upmixer.formats import INPUT_FORMAT_MAP
from upmixer.pipeline import UpmixPipeline
from upmixer.separation.separator import DEFAULT_MODEL

_INPUT_FORMAT_CHOICES = sorted(INPUT_FORMAT_MAP.keys())
_OUTPUT_FORMAT_CHOICES = ["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"]


def _apply_cli_flags(config: UpmixConfig, args: argparse.Namespace, sample_rate_set: bool) -> None:
    """Apply explicitly-set CLI flags to config.

    Only non-None values are applied so manifest defaults are preserved for
    flags the user did not supply.  ``sample_rate_set`` indicates whether
    ``--output-sample-rate`` was given on the command line (needed to avoid
    clobbering a manifest-set sample rate).
    """
    if args.format is not None:
        config.output_format = args.format
    if args.center_gain is not None:
        config.center_gain = args.center_gain
    if args.surround_gain is not None:
        config.surround_gain = args.surround_gain
    if args.back_gain is not None:
        config.back_gain = args.back_gain
    if args.height_gain is not None:
        config.height_gain = args.height_gain
    if args.lfe_gain is not None:
        config.lfe_gain = args.lfe_gain
    if args.center_extraction_gain is not None:
        config.center_extraction_gain = args.center_extraction_gain
    if args.center_attenuation is not None:
        config.center_attenuation = args.center_attenuation
    if args.lfe_cutoff is not None:
        config.lfe_cutoff_hz = args.lfe_cutoff
    if args.height_low_rolloff_gain is not None:
        config.height_low_rolloff_gain = args.height_low_rolloff_gain
    if args.height_high_shelf_gain is not None:
        config.height_high_shelf_gain = args.height_high_shelf_gain
    if args.fft_size is not None:
        config.fft_size = args.fft_size
        config.hop_size = args.fft_size // 4
    if args.no_auto_fft:
        config.auto_fft_size = False
    if args.block_size is not None:
        config.block_size = args.block_size
    if args.no_normalize:
        config.normalize_output = False
    if args.content_mix_strength is not None:
        config.content_mix_strength = max(0.0, min(1.0, args.content_mix_strength))
    if args.no_loudness_normalize:
        config.loudness_normalize = False
    if args.loudness_target is not None:
        config.loudness_target_lkfs = args.loudness_target
    if args.output_type is not None:
        config.output_type = args.output_type
    elif not args.manifest:
        # No manifest → default to wav
        config.output_type = "wav"
    if args.output_subtype is not None:
        config.output_subtype = args.output_subtype
    if sample_rate_set:
        config.output_sample_rate = args.output_sample_rate
    if args.downmix_surround_coeff is not None:
        config.surround_downmix_coeff = args.downmix_surround_coeff
    if args.downmix_output is not None:
        config.downmix_output_path = args.downmix_output
    if args.preview:
        config.preview = True
    if args.preview_duration is not None:
        config.preview_duration_s = args.preview_duration
    if args.preview_start is not None:
        config.preview_start_s = args.preview_start
    # Mastering — EQ shaping
    if args.mastering_eq is not None:
        config.mastering_eq_profile = args.mastering_eq
    if args.mastering_eq_strength is not None:
        config.mastering_eq_strength = max(0.0, min(1.0, args.mastering_eq_strength))
    # Mastering — bus compressor
    if args.mastering_comp is not None:
        config.mastering_comp_profile = args.mastering_comp
    if args.mastering_comp_threshold is not None:
        config.mastering_comp_threshold_db = args.mastering_comp_threshold
    if args.mastering_comp_ratio is not None:
        config.mastering_comp_ratio = args.mastering_comp_ratio
    if args.mastering_comp_attack is not None:
        config.mastering_comp_attack_ms = args.mastering_comp_attack
    if args.mastering_comp_release is not None:
        config.mastering_comp_release_ms = args.mastering_comp_release
    if args.mastering_comp_makeup is not None:
        config.mastering_comp_makeup_db = args.mastering_comp_makeup
    # Mastering — bass control
    if args.mastering_bass is not None:
        config.mastering_bass_profile = args.mastering_bass
    if args.mastering_bass_sub is not None:
        config.mastering_bass_sub_gain_db = args.mastering_bass_sub
    if args.mastering_bass_mid is not None:
        config.mastering_bass_mid_gain_db = args.mastering_bass_mid
    if args.mastering_bass_mono_cutoff is not None:
        config.mastering_bass_mono_cutoff_hz = args.mastering_bass_mono_cutoff
    if args.mastering_bass_excite:
        config.mastering_bass_excite = True
    if args.mastering_bass_lfe is not None:
        config.mastering_bass_lfe_gain_db = args.mastering_bass_lfe
    # Mastering — EQ from reference
    if args.mastering_eq_reference is not None:
        config.mastering_eq_reference = args.mastering_eq_reference
    if args.mastering_eq_match_strength is not None:
        config.mastering_eq_match_strength = max(0.0, min(1.0, args.mastering_eq_match_strength))
    # Mixing — stem rebalance
    if args.stem_rebalance is not None:
        config.stem_rebalance = _parse_key_value_pairs(args.stem_rebalance, float)
    if args.stem_rebalance_profile is not None:
        from upmixer.separation.stem_rebalance import REBALANCE_PROFILES
        if args.stem_rebalance_profile not in REBALANCE_PROFILES:
            raise SystemExit(
                f"Unknown stem rebalance profile '{args.stem_rebalance_profile}'. "
                f"Valid choices: {sorted(REBALANCE_PROFILES.keys())}"
            )
        # stem_rebalance dict overrides profile; profile only applied if no manual dict
        if config.stem_rebalance is None:
            config.stem_rebalance = REBALANCE_PROFILES[args.stem_rebalance_profile]
    # Mixing — per-stem EQ
    if args.stem_eq is not None:
        config.stem_eq_profiles = _parse_key_value_pairs(args.stem_eq, str)
    # Mixing — stem cache
    if args.stem_cache_dir is not None:
        config.stem_cache_dir = args.stem_cache_dir


def _parse_key_value_pairs(s: str, value_type: type) -> dict:
    """Parse ``"Key1=val1,Key2=val2"`` into a typed dict.

    Used for ``--stem-rebalance`` and ``--stem-eq`` CLI arguments.

    Examples::

        _parse_key_value_pairs("Vocals=+2.0,Drums=-1.0", float)
        # → {"Vocals": 2.0, "Drums": -1.0}

        _parse_key_value_pairs("Vocals=vocal-presence", str)
        # → {"Vocals": "vocal-presence"}
    """
    result: dict = {}
    for pair in s.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise SystemExit(
                f"Invalid key=value pair in '{s}'. "
                "Expected format: 'Key1=val1,Key2=val2'."
            )
        k, v = pair.split("=", 1)
        result[k.strip()] = value_type(v.strip())
    return result


def _apply_resource_limits(cpu_priority: str) -> None:
    import os
    if cpu_priority == "low":
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass  # Windows has no os.nice
    try:
        import torch
        n = max(1, (os.cpu_count() or 4) // 2)
        torch.set_num_threads(n)
    except ImportError:
        pass
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(limits=max(1, (os.cpu_count() or 4) // 2))
    except ImportError:
        pass  # soft dep — skip silently


def main() -> None:
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

    # ── Positional args (optional when --manifest provides them) ──────────────
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

    # ── Manifest ──────────────────────────────────────────────────────────────
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

    # ── Batch processing ──────────────────────────────────────────────────────
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

    # ── Output format ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--format",
        choices=_OUTPUT_FORMAT_CHOICES,
        default=None,
        help=(
            "Output channel format (default: 5.1, or as set by --manifest). "
            f"Choices: {', '.join(_OUTPUT_FORMAT_CHOICES)}."
        ),
    )

    # ── Input format ──────────────────────────────────────────────────────────
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

    # ── Processing mode ───────────────────────────────────────────────────────
    parser.add_argument(
        "--mode",
        choices=["realtime", "stem"],
        default=None,
        help=(
            "Processing mode (default: realtime). "
            "'realtime': coherence-based STFT pipeline, works on any input. "
            "'stem': source-separation pipeline — separates instruments then "
            "places each in 3D space. "
            "Requires: pip install 'audio-separator[cpu]'."
        ),
    )
    parser.add_argument(
        "--stem-model",
        default=None,
        metavar="MODEL",
        help=(
            f"audio-separator model for stem mode (default: {DEFAULT_MODEL}). "
            "Models are auto-downloaded on first use."
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

    # ── Gain controls ─────────────────────────────────────────────────────────
    parser.add_argument("--center-gain",           type=float, default=None, help="Center channel output gain (default: 0.85)")
    parser.add_argument("--surround-gain",         type=float, default=None, help="Side surround channel gain (default: 0.6)")
    parser.add_argument("--back-gain",             type=float, default=None, help="Rear back channel gain for 7.1 formats (default: 0.55)")
    parser.add_argument("--height-gain",           type=float, default=None, help="Height channel gain for Atmos formats (default: 0.55)")
    parser.add_argument("--lfe-gain",              type=float, default=None, help="LFE channel gain (default: 0.5)")

    # ── Center extraction ─────────────────────────────────────────────────────
    parser.add_argument("--center-extraction-gain",type=float, default=None, help="Mid signal → center channel (default: 0.85)")
    parser.add_argument("--center-attenuation",    type=float, default=None, help="Center attenuation in FL/FR (default: 0.5)")

    # ── LFE ───────────────────────────────────────────────────────────────────
    parser.add_argument("--lfe-cutoff",            type=float, default=None, metavar="HZ", help="LFE low-pass cutoff in Hz (default: 120)")

    # ── Height EQ ─────────────────────────────────────────────────────────────
    parser.add_argument("--height-low-rolloff-gain",type=float, default=None, help="Sub-bass gain for height channels (default: 0.15)")
    parser.add_argument("--height-high-shelf-gain", type=float, default=None, help="HF presence boost for height channels (default: 1.5)")

    # ── STFT / processing ─────────────────────────────────────────────────────
    parser.add_argument("--fft-size",   type=int,  default=None, help="STFT window size")
    parser.add_argument("--no-auto-fft",action="store_true",     help="Disable automatic FFT size scaling for high sample rates")
    parser.add_argument("--block-size", type=int,  default=None, help="Streaming block size in samples (default: 4096)")

    # ── Output ────────────────────────────────────────────────────────────────
    parser.add_argument("--no-normalize", action="store_true", help="Disable output energy normalization (mixing phase)")
    parser.add_argument("--content-mix-strength", type=float, default=None, metavar="S", help="Content-aware mixing strength 0.0–1.0 (default: 1.0)")
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
        "--output-type",
        choices=["wav", "adm-bwf"],
        default=None,
        help=(
            "'wav' = standard multichannel WAV. "
            "'adm-bwf' = Broadcast Wave + ITU-R BS.2076-2 ADM metadata "
            "(Logic Pro, DaVinci Resolve, Pro Tools). "
            "Default: 'wav' (or as set by manifest)."
        ),
    )
    parser.add_argument("--output-subtype", choices=["PCM_16", "PCM_24", "PCM_32"], default=None, help="Output bit depth (default: PCM_24)")
    parser.add_argument("--output-sample-rate", type=int, default=None, metavar="HZ", help="Resample output (e.g. 48000, 96000). Default: same as input.")

    # ── ITU-R BS.775-4 stereo downmix ─────────────────────────────────────────
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

    # ── Preview ───────────────────────────────────────────────────────────────
    parser.add_argument("--preview",          action="store_true", help="Process a short excerpt (default 30 s) instead of the full file.")
    parser.add_argument("--preview-duration", type=float, default=None, metavar="S", help="Preview window length in seconds (default: 30).")
    parser.add_argument("--preview-start",    type=float, default=None, metavar="S", help="Preview start time in seconds (default: auto-center).")

    # ── Mastering: EQ shaping ─────────────────────────────────────────────────
    _EQ_CHOICES = ["spatial-transparent", "spatial-air", "spatial-warm", "spatial-present", "atmos-streaming"]
    parser.add_argument(
        "--mastering-eq",
        choices=_EQ_CHOICES,
        default=None,
        metavar="PROFILE",
        help=(
            "Apply a predefined tonal EQ curve to the master bus (optional). "
            f"Choices: {', '.join(_EQ_CHOICES)}. "
            "LFE is always bypassed. "
            "See --manifest-keys for YAML equivalent."
        ),
    )
    parser.add_argument(
        "--mastering-eq-strength",
        type=float,
        default=None,
        metavar="S",
        help="EQ wet/dry blend: 0.0 = bypass, 1.0 = full effect (default: 1.0).",
    )

    # ── Mastering: bus compressor ─────────────────────────────────────────────
    _COMP_CHOICES = ["transparent", "glue", "warm"]
    parser.add_argument(
        "--mastering-comp",
        choices=_COMP_CHOICES,
        default=None,
        metavar="PROFILE",
        help=(
            "Apply a cosmetic glue compressor to the master bus (optional). "
            f"Choices: {', '.join(_COMP_CHOICES)}. "
            "LFE is always bypassed. Applied before loudness normalization."
        ),
    )
    parser.add_argument("--mastering-comp-threshold", type=float, default=None, metavar="DB",  help="Override compressor threshold in dBFS.")
    parser.add_argument("--mastering-comp-ratio",     type=float, default=None, metavar="R",   help="Override compressor ratio (e.g. 2.0 for 2:1).")
    parser.add_argument("--mastering-comp-attack",    type=float, default=None, metavar="MS",  help="Override compressor attack time in ms.")
    parser.add_argument("--mastering-comp-release",   type=float, default=None, metavar="MS",  help="Override compressor release time in ms.")
    parser.add_argument("--mastering-comp-makeup",    type=float, default=None, metavar="DB",  help="Override compressor makeup gain in dB.")

    # ── Mastering: bass control ───────────────────────────────────────────────
    _BASS_CHOICES = ["boost", "cut", "mono", "enhance"]
    parser.add_argument(
        "--mastering-bass",
        choices=_BASS_CHOICES,
        default=None,
        metavar="PROFILE",
        help=(
            "Apply multichannel bass control to the master bus (optional). "
            f"Choices: {', '.join(_BASS_CHOICES)}. "
            "LFE is handled separately from the main bed. "
            "Applied after bus compression, before loudness normalization."
        ),
    )
    parser.add_argument("--mastering-bass-sub",          type=float, default=None, metavar="DB", help="Bass control: sub-bass (<80 Hz) gain in dB.")
    parser.add_argument("--mastering-bass-mid",          type=float, default=None, metavar="DB", help="Bass control: mid-bass (80–200 Hz) gain in dB.")
    parser.add_argument("--mastering-bass-mono-cutoff",  type=float, default=None, metavar="HZ", help="Bass mono-maker: sum L/R below this frequency (Hz).")
    parser.add_argument("--mastering-bass-excite",       action="store_true",                    help="Enable bass harmonic exciter (tanh waveshaping on sub-bass band).")
    parser.add_argument("--mastering-bass-lfe",          type=float, default=None, metavar="DB", help="LFE channel gain trim in dB.")

    # ── Mastering: EQ from reference (Match EQ) ───────────────────────────────
    parser.add_argument(
        "--mastering-eq-reference",
        default=None,
        metavar="FILE",
        help=(
            "Derive a per-channel EQ profile from a reference audio file and "
            "apply it to the master bus (overrides --mastering-eq). "
            "The reference may be mono through 7.1.4; missing channels are "
            "estimated from available ones. "
            "For best results use a reference with the same channel count as "
            "the target format."
        ),
    )
    parser.add_argument(
        "--mastering-eq-reference-save",
        default=None,
        metavar="PATH",
        help="Save the generated EQ profile to a YAML or JSON file for reuse.",
    )
    parser.add_argument(
        "--mastering-eq-match-strength",
        type=float,
        default=None,
        metavar="S",
        help=(
            "EQ match curve intensity: scales gain_dB values before FIR design "
            "(0.0 = flat, 1.0 = full reference curve). Default: 0.5. "
            "Semantically different from --mastering-eq-strength (wet/dry blend) — "
            "gain scaling is more predictable for broad spectral shapes."
        ),
    )
    parser.add_argument(
        "--generate-eq-profile",
        action="store_true",
        help=(
            "Analyse --mastering-eq-reference, save the EQ profile to "
            "--mastering-eq-reference-save, then exit (no upmix performed)."
        ),
    )

    # ── Mixing: stem rebalance ────────────────────────────────────────────────
    parser.add_argument(
        "--stem-rebalance",
        default=None,
        metavar="KEY=DB[,...]",
        help=(
            "Per-stem gain adjustments before spatial routing (stem mode only). "
            "Format: 'Vocals=+2.0,Drums=-1.0'. "
            "Applied after separation, before content-aware routing."
        ),
    )
    parser.add_argument(
        "--stem-rebalance-profile",
        default=None,
        metavar="PROFILE",
        help=(
            "Apply a predefined stem rebalance preset. "
            "Choices: vocal-forward, instrumental, bass-heavy, balanced. "
            "Overridden by --stem-rebalance if both are given."
        ),
    )

    # ── Mixing: per-stem EQ ───────────────────────────────────────────────────
    parser.add_argument(
        "--stem-eq",
        default=None,
        metavar="STEM=PROFILE[,...]",
        help=(
            "Per-stem EQ applied before spatial routing (stem mode only). "
            "Format: 'Vocals=vocal-presence,Bass=bass-warmth'. "
            "Valid profiles: vocal-presence, vocal-warmth, bass-warmth, "
            "bass-cut, drums-punch, other-air, flat."
        ),
    )

    # ── Mixing: stem cache ────────────────────────────────────────────────────
    parser.add_argument(
        "--stem-cache-dir",
        default=None,
        metavar="DIR",
        help=(
            "Cache separated stems to this directory (stem mode only). "
            "On subsequent runs with the same input file, model, and sample "
            "rate the cached stems are loaded directly, skipping re-separation. "
            "Key: SHA-256(abs_path|mtime|model|sample_rate)."
        ),
    )

    # ── Resource limits ───────────────────────────────────────────────────────
    parser.add_argument(
        "--cpu-priority",
        choices=["normal", "low"],
        default="low",
        help=(
            "Process scheduling priority. 'low' calls os.nice(10) and caps "
            "numpy/torch thread counts to half the logical CPU count, preventing "
            "the app from saturating the system during heavy processing. "
            "Default: low."
        ),
    )

    # ── Verbosity ─────────────────────────────────────────────────────────────
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet",   "-q", action="store_true", help="Suppress all output except warnings and errors.")
    verbosity.add_argument("--verbose", "-v", action="store_true", help="Enable debug-level logging.")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary of the result to stdout when done.")

    # ── Early exits (before full parse) ──────────────────────────────────────
    if "--manifest-keys" in sys.argv:
        from upmixer.manifest import list_manifest_keys
        print("\nValid manifest keys (key → UpmixConfig attribute):\n")
        for mk, desc in list_manifest_keys().items():
            print(f"  {mk:<30}  {desc}")
        print()
        sys.exit(0)

    args = parser.parse_args()

    _apply_resource_limits(args.cpu_priority)

    # ── Logging setup ─────────────────────────────────────────────────────────
    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet or args.json:
        log_level = logging.WARNING
    else:
        log_level = logging.INFO

    logging.basicConfig(level=log_level, format="%(message)s", stream=sys.stderr)

    # ── Build config ──────────────────────────────────────────────────────────
    # Start with UpmixConfig defaults, then apply in priority order:
    #   manifest fields  <  CLI flags
    config = UpmixConfig()

    # Track whether --output-sample-rate was explicitly given (vs. None default)
    sample_rate_set = args.output_sample_rate is not None

    # ── 1. Load and apply manifest ────────────────────────────────────────────
    manifest_data: dict = {}
    manifest_job: dict = {}
    if args.manifest is not None:
        from upmixer.manifest import load_manifest, apply_manifest
        manifest_data = load_manifest(args.manifest)
        manifest_job = apply_manifest(config, manifest_data)
        if not sample_rate_set and "output_sample_rate" in manifest_data:
            sample_rate_set = True  # manifest set it; don't clobber with None

    # ── 2. Apply CLI flags (override manifest) ────────────────────────────────
    _apply_cli_flags(config, args, sample_rate_set)

    # ── 3a. --generate-eq-profile standalone tool ─────────────────────────────
    if getattr(args, "generate_eq_profile", False):
        ref_path = getattr(args, "mastering_eq_reference", None)
        save_path = getattr(args, "mastering_eq_reference_save", None)
        if not ref_path:
            parser.error(
                "--generate-eq-profile requires --mastering-eq-reference FILE"
            )
        if not save_path:
            parser.error(
                "--generate-eq-profile requires --mastering-eq-reference-save PATH"
            )
        from upmixer.mastering.eq_match import EQMatcher
        import soundfile as _sf  # type: ignore[import-untyped]
        _info = _sf.info(ref_path)
        sr = _info.samplerate
        from upmixer.formats import FORMAT_MAP, detect_input_format
        import numpy as _np
        _data, _sr = _sf.read(ref_path, dtype="float64", always_2d=True)
        n_ch = _data.shape[1]
        # Derive channel names from standard layout or use generic names
        _std = {1: ["M"], 2: ["FL","FR"], 6: ["FL","FR","C","LFE","SL","SR"],
                8: ["FL","FR","C","LFE","BL","BR","SL","SR"],
                10: ["FL","FR","C","LFE","SL","SR","BL","BR","TFL","TFR"],
                12: ["FL","FR","C","LFE","SL","SR","BL","BR","TFL","TFR","TBL","TBR"]}
        chs = _std.get(n_ch, [f"CH{i}" for i in range(n_ch)])
        matcher = EQMatcher(sr)
        bps = matcher.analyze(ref_path, chs)
        matcher.save_profile(bps, save_path)
        print(f"EQ profile saved to: {save_path}")
        sys.exit(0)

    # ── 4. Resolve mode and stem params ──────────────────────────────────────
    mode = args.mode or manifest_job.get("mode", "realtime")
    stem_model     = args.stem_model     or manifest_job.get("stem_model",     DEFAULT_MODEL)
    stem_model_dir = args.stem_model_dir or manifest_job.get("stem_model_dir", None)
    input_format   = args.input_format   or manifest_job.get("input_format",   None)

    # ── 5. Detect batch vs single-file mode ───────────────────────────────────
    batch_inputs  = args.inputs or manifest_job.get("batch_inputs")
    batch_dir     = args.batch_dir or manifest_job.get("batch_dir")
    output_dir    = args.output_dir or manifest_job.get("batch_output_dir")
    batch_jobs_manifest = manifest_job.get("batch_jobs")
    is_batch = bool(batch_inputs or batch_dir or batch_jobs_manifest)

    if is_batch:
        # ── Batch mode ────────────────────────────────────────────────────────
        from upmixer.batch import BatchProcessor, resolve_batch_jobs

        if not output_dir and not batch_jobs_manifest:
            parser.error(
                "Batch mode requires --output-dir. "
                "Alternatively, set 'output' per job in the manifest batch.jobs list."
            )

        jobs = resolve_batch_jobs(
            input_paths=None,
            batch_dir=batch_dir,
            output_dir=output_dir,
            output_ext=".wav",
            explicit_jobs=batch_jobs_manifest,
            batch_inputs=batch_inputs,
        )
        if not jobs:
            parser.error("No input files found for batch processing.")

        workers = args.batch_workers or manifest_job.get("batch_workers") or 1
        processor = BatchProcessor(
            config=config,
            mode=mode,
            stem_model=stem_model,
            stem_model_dir=stem_model_dir,
            workers=workers,
            progress_callback=lambda done, total, path: (
                _log.info("[%d/%d] %s", done + 1, total, path) if path else None
            ),
        )
        batch_result = processor.process(jobs)

        for fail in batch_result.failed:
            _log.error("FAILED: %s — %s", fail["input"], fail["error"])

        if args.json:
            print(batch_result.to_json())
        else:
            _log.info(
                "Batch complete: %d/%d succeeded in %.1fs",
                len(batch_result.jobs), len(jobs), batch_result.wall_time_s,
            )

    else:
        # ── Single-file mode (unchanged behaviour) ────────────────────────────
        input_path  = args.input  or manifest_job.get("input")
        output_path = args.output or manifest_job.get("output")

        if not input_path:
            parser.error(
                "input file is required. "
                "Pass it as a positional argument, set 'input' in the manifest, "
                "or use --inputs / --batch-dir for batch processing."
            )
        if not output_path:
            parser.error(
                "output file is required. "
                "Pass it as a positional argument or set 'output' in the manifest."
            )

        if mode == "stem":
            from upmixer.separation.stem_pipeline import StemUpmixPipeline
            stem_pipeline = StemUpmixPipeline(
                config=config,
                model=stem_model,
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

        # ── Optional: save EQ reference profile after single-file run ─────────
        ref_save = getattr(args, "mastering_eq_reference_save", None)
        if ref_save and config.mastering_eq_reference is not None:
            from upmixer.mastering.eq_match import EQMatcher
            from upmixer.formats import FORMAT_MAP
            fmt = FORMAT_MAP.get(config.output_format)
            ch_names = list(fmt.channels) if fmt else []
            if ch_names:
                matcher = EQMatcher(
                    result.sample_rate if hasattr(result, "sample_rate")
                    else (config.output_sample_rate or 48000)
                )
                bps = matcher.analyze(config.mastering_eq_reference, ch_names)
                matcher.save_profile(bps, ref_save)
                print(f"EQ profile saved to: {ref_save}", file=sys.stderr)

        if args.json:
            print(result.to_json())


if __name__ == "__main__":
    main()
