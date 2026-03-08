import argparse

from upmixer.config import UpmixConfig
from upmixer.pipeline import UpmixPipeline


def main():
    parser = argparse.ArgumentParser(
        description="Stereo to multichannel audio upmixer"
    )
    parser.add_argument("input", help="Input stereo audio file (WAV/FLAC)")
    parser.add_argument("output", help="Output multichannel WAV file")
    parser.add_argument(
        "--format",
        choices=["5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"],
        default="5.1",
        help="Output channel format (default: 5.1)",
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
    if args.fft_size is not None:
        config.fft_size = args.fft_size
        config.hop_size = args.fft_size // 4
    if args.no_auto_fft:
        config.auto_fft_size = False
    if args.block_size is not None:
        config.block_size = args.block_size
    if args.no_normalize:
        config.normalize_output = False

    pipeline = UpmixPipeline(config)
    pipeline.process_file(args.input, args.output)


if __name__ == "__main__":
    main()
