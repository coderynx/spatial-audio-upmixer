import argparse

from upmixer.config import UpmixConfig
from upmixer.formats import INPUT_FORMAT_MAP
from upmixer.pipeline import UpmixPipeline

_INPUT_FORMAT_CHOICES = sorted(INPUT_FORMAT_MAP.keys())
_OUTPUT_FORMAT_CHOICES = ["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"]


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Universal multichannel audio upmixer. "
            "Upmix mono, stereo, 5.1, or 7.1 to any higher surround/Atmos format."
        )
    )
    parser.add_argument(
        "input",
        help="Input audio file (WAV/FLAC). Supported layouts: mono, stereo, 5.0, 5.1, 7.1, 5.1.2, 7.1.2",
    )
    parser.add_argument("output", help="Output multichannel WAV file")
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
            "Useful when channel count is ambiguous (e.g. 8ch = 7.1 or 5.1.2)."
        ),
    )
    parser.add_argument(
        "--center-gain", type=float, default=None, help="Center channel gain (0-1)"
    )
    parser.add_argument(
        "--surround-gain",
        type=float,
        default=None,
        help="Surround channels gain (0-1)",
    )
    parser.add_argument(
        "--center-extraction-gain",
        type=float,
        default=None,
        help="How much mid signal goes to center (0-1, default: 0.7)",
    )
    parser.add_argument(
        "--center-attenuation",
        type=float,
        default=None,
        help="How much center is attenuated from FL/FR (0-1, default: 0.3)",
    )
    parser.add_argument(
        "--height-gain",
        type=float,
        default=None,
        help="Height channel gain for Atmos formats (0-1)",
    )
    parser.add_argument(
        "--height-mid-blend",
        type=float,
        default=None,
        help="How much mid/center signal is blended into height channels (0-1, default: 0.35)",
    )
    parser.add_argument(
        "--height-low-rolloff-gain",
        type=float,
        default=None,
        help="Sub-bass gain for height channels (0-1, default: 0.15). Higher = more bass in height.",
    )
    parser.add_argument(
        "--height-high-shelf-gain",
        type=float,
        default=None,
        help="High-frequency boost for height channels (default: 1.5). >1.0 = presence lift.",
    )
    parser.add_argument("--fft-size", type=int, default=None, help="STFT window size")
    parser.add_argument(
        "--no-auto-fft",
        action="store_true",
        help="Disable automatic FFT size scaling for high sample rates",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=None,
        help="Streaming block size in samples (default: 4096)",
    )
    parser.add_argument(
        "--no-normalize", action="store_true", help="Disable output normalization"
    )
    parser.add_argument(
        "--output-type",
        choices=["wav", "adm-bwf"],
        default="wav",
        help=(
            "Output file type. 'wav' = standard multichannel WAV. "
            "'adm-bwf' = Broadcast Wave Format with ITU-R BS.2076-2 ADM metadata "
            "for import into Logic Pro, DaVinci Resolve, Pro Tools, etc. (default: wav)"
        ),
    )
    parser.add_argument(
        "--output-sample-rate",
        type=int,
        default=None,
        metavar="HZ",
        help="Resample output to this sample rate (e.g. 48000, 96000). Default: same as input.",
    )

    args = parser.parse_args()

    config = UpmixConfig(output_format=args.format)

    if args.center_gain is not None:
        config.center_gain = args.center_gain
    if args.surround_gain is not None:
        config.surround_gain = args.surround_gain
    if args.center_extraction_gain is not None:
        config.center_extraction_gain = args.center_extraction_gain
    if args.center_attenuation is not None:
        config.center_attenuation = args.center_attenuation
    if args.height_gain is not None:
        config.height_gain = args.height_gain
    if args.height_mid_blend is not None:
        config.height_mid_blend = args.height_mid_blend
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
    config.output_type = args.output_type
    if args.output_sample_rate is not None:
        config.output_sample_rate = args.output_sample_rate

    pipeline = UpmixPipeline(config)
    pipeline.process_file(args.input, args.output, input_format_override=args.input_format)


if __name__ == "__main__":
    main()
