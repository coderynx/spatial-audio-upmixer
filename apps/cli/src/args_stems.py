"""Stem-separation CLI arguments (routing, caching, inference tuning)."""

import argparse

from .args_types import positive_float, positive_int


def add_stem_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument(
        "--stem-lfe",
        default=None,
        metavar="STEM=AMOUNT[,...]",
        help=(
            "Per-stem LFE send amount, 0.0-1.0 (stem mode only). "
            "Format: 'Bass=0.8,Vocals=0'. Overrides only the LFE weight of "
            "each stem's spatial routing, leaving the rest of its route "
            "untouched."
        ),
    )
    parser.add_argument(
        "--stem-pan",
        default=None,
        metavar="STEM=VALUE[,...]",
        help=(
            "Per-stem left/right pan, 0.0 = hard left, 0.5 = centre, "
            "1.0 = hard right (stem mode only). Format: 'Vocals=0.5,Guitar=0.3'. "
            "Overrides only the FL/FR pair of each stem's spatial routing, "
            "preserving the pair's combined magnitude."
        ),
    )
    parser.add_argument(
        "--spatial-downmix-lock",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Keep each routed stem's BS.775 stereo fold equal to its input pair (default: disabled).",
    )
    parser.add_argument(
        "--stem-object-mode",
        default=None,
        metavar="STEM=MODE[,...]",
        help="Object feed mode per stem: linked-stereo or mono.",
    )
    parser.add_argument(
        "--stem-ambient-rear",
        default=None,
        metavar="STEM=AMOUNT[,...]",
        help="Per-stem ambient send to surrounds, 0.0-1.0 (stem mode only).",
    )
    parser.add_argument(
        "--stem-ambient-height",
        default=None,
        metavar="STEM=AMOUNT[,...]",
        help="Per-stem ambient send to heights, 0.0-1.0 (stem mode only).",
    )
    parser.add_argument(
        "--stem-ambient-height-crossover",
        default=None,
        metavar="STEM=HZ[,...]",
        help="Per-stem ambient height crossover, 500-4000 Hz (stem mode only).",
    )

    parser.add_argument(
        "--stem-cache-dir",
        default=None,
        metavar="DIR",
        help=(
            "Cache separated stems to this directory (stem mode only). "
            "On subsequent runs with the same input file, model plan, and sample "
            "rate the cached stems are loaded directly, skipping re-separation. "
            "Legacy cache entries remain readable."
        ),
    )

    parser.add_argument(
        "--stem-batch-size",
        type=lambda value: positive_int(value, "--stem-batch-size"),
        default=None,
        metavar="N",
        help=(
            "Full-precision inference batch size (stem mode only). "
            "Default: auto-select from accelerator and free memory."
        ),
    )

    parser.add_argument(
        "--stem-segment-size",
        type=lambda value: positive_int(value, "--stem-segment-size"),
        default=None,
        metavar="N",
        help=(
            "MDXC inference segment size (stem mode only). Smaller values "
            "reduce CPU RAM use. Default: auto-select from VM memory."
        ),
    )

    parser.add_argument(
        "--stem-chunk-duration-s",
        type=lambda value: positive_float(value, "--stem-chunk-duration-s"),
        default=None,
        metavar="SECONDS",
        help=(
            "Split long separator inputs into bounded-memory chunks. "
            "Default: auto-select for low-memory CPU inference."
        ),
    )

    parser.add_argument(
        "--stem-model-cache-size",
        type=lambda value: positive_int(value, "--stem-model-cache-size"),
        default=None,
        metavar="N",
        help=(
            "Maximum resident separator models. Default: one on CPU, "
            "unlimited on accelerators."
        ),
    )

    parser.add_argument(
        "--stem-silence-skip",
        action=argparse.BooleanOptionalAction,
        default=None,
        dest="stem_silence_skip",
        help=(
            "Skip separator on silent regions of each stem zone (stem mode only). "
            "Detects contiguous silent runs and only processes active audio, "
            "then stitches results back with a short crossfade. "
            "Default: enabled (--stem-silence-skip)."
        ),
    )

    parser.add_argument(
        "--stem-silence-threshold-db",
        type=float,
        default=None,
        metavar="DB",
        help=(
            "Peak threshold in dBFS below which a window is considered silent. "
            "Default: -90.0 dBFS."
        ),
    )

    parser.add_argument(
        "--stem-silence-min-duration-s",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Minimum silent run duration in seconds.  Silent gaps shorter than "
            "this are merged into the surrounding active span. "
            "Default: 2.0 s."
        ),
    )

    parser.add_argument(
        "--stem-silence-crossfade-ms",
        type=float,
        default=None,
        metavar="MS",
        help=(
            "Linear fade length in milliseconds applied at each active/silent "
            "boundary to prevent clicks. Default: 10.0 ms."
        ),
    )

    parser.add_argument(
        "--stem-silence-pad-ms",
        type=float,
        default=None,
        metavar="MS",
        help=(
            "Padding in milliseconds added to both ends of each active span so "
            "the separator has musical context near transient boundaries. "
            "Default: 200.0 ms."
        ),
    )
    parser.add_argument(
        "--stem-source-anchor-strength",
        type=float,
        default=None,
        metavar="FLOAT",
        help=(
            "Blend stem content with original native source pairs in stem-mode output "
            "(0.0 to 1.0). Default: 1.0."
        ),
    )

    parser.add_argument(
        "--stem-ensemble",
        action=argparse.BooleanOptionalAction,
        default=None,
        dest="stem_ensemble",
        help=(
            "Average the fixed primary and SCNet separation models for Bass/Drums. "
            "Downloads another model and runs slower."
        ),
    )

    parser.add_argument(
        "--stem-bleed-reduction",
        action=argparse.BooleanOptionalAction,
        default=None,
        dest="stem_bleed_reduction",
        help="Enable DSP stem cleanup on separated stems (stem mode only). Default: disabled.",
    )

    parser.add_argument(
        "--stem-drum-remask",
        action=argparse.BooleanOptionalAction,
        default=None,
        dest="stem_drum_remask",
        help=(
            "Share the remainder the kit-piece split leaves on the parent "
            "Drums stem back over the pieces, so they sum to it (stem mode "
            "only). Default: enabled."
        ),
    )

    parser.add_argument(
        "--stem-primary-remask",
        action=argparse.BooleanOptionalAction,
        default=None,
        dest="stem_primary_remask",
        help=(
            "Share the remainder the Bass/Drums/Guitar/Piano/Other split "
            "leaves on its input instrumental back over those stems, so they "
            "sum to it (stem mode only). Default: enabled."
        ),
    )
