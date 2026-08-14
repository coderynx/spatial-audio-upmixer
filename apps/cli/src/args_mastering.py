"""Mastering-related CLI arguments (EQ, compression, bass, reference match)."""

import argparse


def add_mastering_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument(
        "--mastering-comp-sidechain-hpf",
        type=float,
        default=None,
        metavar="HZ",
        help=(
            "High-pass the compressor's detector at this frequency so the "
            "bed's low end stops driving gain reduction across every channel. "
            "Recommended alongside --mastering-bass-punch."
        ),
    )

    _BASS_CHOICES = ["boost", "cut", "mono", "enhance", "deep", "cinema"]
    parser.add_argument(
        "--mastering-bass",
        choices=_BASS_CHOICES,
        default=None,
        metavar="PROFILE",
        help=(
            "Apply multichannel bass management to the master bus (optional). "
            f"Choices: {', '.join(_BASS_CHOICES)}. "
            "LFE is handled separately from the main bed. "
            "Applied after bus compression, before loudness normalization."
        ),
    )
    parser.add_argument("--mastering-bass-sub",       type=float, default=None, metavar="DB", help="Bass control: sub-bass (<80 Hz) gain in dB.")
    parser.add_argument("--mastering-bass-mid",       type=float, default=None, metavar="DB", help="Bass control: mid-bass (80–200 Hz) gain in dB.")
    parser.add_argument("--mastering-bass-unify",     type=float, default=None, metavar="HZ", help="LF unification crossover in Hz (40–120): sum every channel's low band to one bus and redistribute it.")
    parser.add_argument("--mastering-bass-spread",    choices=["front", "bed", "all"], default=None, metavar="WHERE", help="Where the unified low end goes: front (FL/FR), bed (floor channels), or all (incl. heights).")
    parser.add_argument("--mastering-bass-punch",     type=float, default=None, metavar="P",  help="Transient shaping on the LF bus (-1.0 to 1.0): positive favours attacks, negative densifies.")
    parser.add_argument("--mastering-bass-excite",    action="store_true",                    help="Enable the harmonic exciter on the unified LF bus (kept out of the LFE).")
    parser.add_argument("--mastering-bass-lfe-mode",  choices=["off", "add", "split"], default=None, metavar="MODE", help="LFE feed: off, add (downmix-safe copy), or split (energy-preserving, LF-light on fold-down).")
    parser.add_argument("--mastering-bass-lfe-send",  type=float, default=None, metavar="W",  help="LFE share of the unified LF bus (0.0–1.0).")
    parser.add_argument("--mastering-bass-lfe",       type=float, default=None, metavar="DB", help="LFE channel gain trim in dB.")

    parser.add_argument(
        "--match-reference",
        default=None,
        metavar="FILE",
        help=(
            "Apply spectral envelope + level matching against a reference "
            "audio file (mono, stereo, 5.1, 7.1, 7.1.2, or 7.1.4). Runs as "
            "mastering step 0, before preset EQ, as one shared correction "
            "curve applied to every full-range channel."
        ),
    )
    parser.add_argument(
        "--match-reference-strength",
        type=float,
        default=None,
        metavar="S",
        help="Spectral correction curve scale for reference matching (0.0–1.0, default 0.7).",
    )
    parser.add_argument(
        "--no-match-reference-spectrum",
        action="store_true",
        help="Disable per-channel spectral correction (keep RMS matching only).",
    )
    parser.add_argument(
        "--no-match-reference-rms",
        action="store_true",
        help="Disable global RMS level matching (keep spectral correction only).",
    )
    parser.add_argument(
        "--match-reference-max-db",
        type=float,
        default=None,
        metavar="DB",
        help="Maximum spectral correction magnitude in dB (default 6.0).",
    )
