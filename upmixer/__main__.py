import argparse

from upmixer.config import UpmixConfig
from upmixer.formats import INPUT_FORMAT_MAP
from upmixer.pipeline import UpmixPipeline
from upmixer.separation.separator import DEFAULT_MODEL

_INPUT_FORMAT_CHOICES = sorted(INPUT_FORMAT_MAP.keys())
_OUTPUT_FORMAT_CHOICES = ["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"]


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Universal multichannel audio upmixer. "
            "Upmix mono, stereo, or any surround format to a higher channel layout. "
            "Supported inputs: mono, stereo, 5.0, 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2."
        )
    )
    parser.add_argument(
        "input",
        help="Input audio file (WAV/FLAC)",
    )
    parser.add_argument("output", help="Output multichannel audio file")
    parser.add_argument(
        "--format",
        choices=_OUTPUT_FORMAT_CHOICES,
        default="5.1",
        help="Output channel format (default: 5.1)",
    )
    parser.add_argument(
        "--input-format",
        choices=_INPUT_FORMAT_CHOICES,
        default=None,
        metavar="FMT",
        help=(
            "Override auto-detected input format. "
            f"Choices: {', '.join(_INPUT_FORMAT_CHOICES)}. "
            "Required when channel count is ambiguous (8ch = 7.1 or 5.1.2; 10ch = 7.1.2 or 5.1.4)."
        ),
    )

    # --- Processing mode ---
    parser.add_argument(
        "--mode",
        choices=["realtime", "stem"],
        default="realtime",
        help=(
            "Processing mode. "
            "'realtime' (default): coherence-based STFT pipeline, works on any input, low latency. "
            "'stem': source-separation pipeline — separates instruments then places each in 3D space. "
            "Requires: pip install 'audio-separator[cpu]'. Only supports mono/stereo input."
        ),
    )
    parser.add_argument(
        "--stem-model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=(
            f"audio-separator model for stem mode (default: {DEFAULT_MODEL}). "
            "4-stem models (drums/bass/vocals/other) give best spatial placement. "
            "Models are auto-downloaded on first use."
        ),
    )
    parser.add_argument(
        "--stem-model-dir",
        default=None,
        metavar="DIR",
        help="Directory to cache downloaded separation models (default: ~/.cache/upmixer-models).",
    )

    # --- Gain controls ---
    parser.add_argument(
        "--center-gain", type=float, default=None,
        help="Center channel output gain (default: 0.85)",
    )
    parser.add_argument(
        "--surround-gain", type=float, default=None,
        help="Side surround channel gain (default: 0.6)",
    )
    parser.add_argument(
        "--back-gain", type=float, default=None,
        help="Rear back channel gain for 7.1 formats (default: 0.55)",
    )
    parser.add_argument(
        "--height-gain", type=float, default=None,
        help="Height channel gain for Atmos formats (default: 0.55)",
    )
    parser.add_argument(
        "--lfe-gain", type=float, default=None,
        help="LFE channel gain (default: 0.5)",
    )

    # --- Center extraction ---
    parser.add_argument(
        "--center-extraction-gain", type=float, default=None,
        help="How much mid signal goes to center channel (default: 0.85)",
    )
    parser.add_argument(
        "--center-attenuation", type=float, default=None,
        help="How much center-panned content is attenuated from FL/FR (default: 0.5)",
    )

    # --- LFE ---
    parser.add_argument(
        "--lfe-cutoff", type=float, default=None, metavar="HZ",
        help="LFE low-pass cutoff frequency in Hz (default: 120)",
    )

    # --- Height EQ ---
    parser.add_argument(
        "--height-low-rolloff-gain", type=float, default=None,
        help="Sub-bass gain for height channels, 0=full rolloff 1=flat (default: 0.15)",
    )
    parser.add_argument(
        "--height-high-shelf-gain", type=float, default=None,
        help="High-frequency presence boost for height channels, >1.0=lift (default: 1.5)",
    )

    # --- STFT / processing ---
    parser.add_argument("--fft-size", type=int, default=None, help="STFT window size")
    parser.add_argument(
        "--no-auto-fft", action="store_true",
        help="Disable automatic FFT size scaling for high sample rates",
    )
    parser.add_argument(
        "--block-size", type=int, default=None,
        help="Streaming block size in samples (default: 4096)",
    )

    # --- Output ---
    parser.add_argument(
        "--no-normalize", action="store_true", help="Disable output energy normalization",
    )
    parser.add_argument(
        "--no-content-mix", action="store_true",
        help="Disable content-aware stem mixing (use static routing tables only)",
    )
    parser.add_argument(
        "--content-mix-strength", type=float, default=None, metavar="S",
        help="Content-aware mixing strength 0.0–1.0 (default: 1.0)",
    )
    parser.add_argument(
        "--no-loudness-normalize", action="store_true",
        help=(
            "Disable ITU-R BS.1770-4 loudness normalization "
            "(Dolby DEE compliance, default: enabled)"
        ),
    )
    parser.add_argument(
        "--loudness-target", type=float, default=None, metavar="LKFS",
        help="Target integrated loudness in LKFS (default: -24.0 for Dolby Atmos)",
    )
    parser.add_argument(
        "--output-type",
        choices=["wav", "adm-bwf"],
        default="wav",
        help=(
            "Output file format. 'wav' = standard multichannel WAV. "
            "'adm-bwf' = Broadcast Wave with ITU-R BS.2076-2 ADM metadata "
            "for Logic Pro, DaVinci Resolve, Pro Tools, etc. (default: wav)"
        ),
    )
    parser.add_argument(
        "--output-subtype",
        choices=["PCM_16", "PCM_24", "PCM_32"],
        default=None,
        help="Output bit depth (default: PCM_24)",
    )
    parser.add_argument(
        "--output-sample-rate", type=int, default=None, metavar="HZ",
        help="Resample output to this sample rate (e.g. 48000, 96000). Default: same as input.",
    )

    args = parser.parse_args()

    config = UpmixConfig(output_format=args.format)

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
    if args.no_content_mix:
        config.content_aware_mixing = False
    if args.content_mix_strength is not None:
        config.content_mix_strength = max(0.0, min(1.0, args.content_mix_strength))
    if args.no_loudness_normalize:
        config.loudness_normalize = False
    if args.loudness_target is not None:
        config.loudness_target_lkfs = args.loudness_target
    config.output_type = args.output_type
    if args.output_subtype is not None:
        config.output_subtype = args.output_subtype
    if args.output_sample_rate is not None:
        config.output_sample_rate = args.output_sample_rate

    if args.mode == "stem":
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        stem_pipeline = StemUpmixPipeline(
            config=config,
            model=args.stem_model,
            model_dir=args.stem_model_dir,
        )
        stem_pipeline.process_file(args.input, args.output, input_format_override=args.input_format)
    else:
        pipeline = UpmixPipeline(config)
        pipeline.process_file(args.input, args.output, input_format_override=args.input_format)


if __name__ == "__main__":
    main()
