#!/usr/bin/env python3
"""Generates static FIR assets for the mastering-bus and per-stem EQ profiles.

Dev-only tool — not imported by production code. The backend
(``upmixer/mastering/eq.py``, ``upmixer/separation/stem_eq.py``) computes
these same minimum-phase FIRs dynamically at runtime via ``_build_fir`` —
this script calls those exact functions and writes the result as mono WAV
assets to ``web/public/eq_fir/`` so the browser preview convolves against
the *actual* backend-computed impulse response (via a ``ConvolverNode``)
instead of approximating the curve with a cascade of biquad filters. The
backend itself does not read these files; it keeps computing the FIR live,
which is the source of truth this script calls into.

Usage:
    python3 scripts/build_eq_filters.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upmixer.mastering.eq import EQ_PROFILES  # noqa: E402
from upmixer.mastering.eq import _build_fir as build_master_fir  # noqa: E402
from upmixer.separation.stem_eq import STEM_EQ_PROFILES  # noqa: E402
from upmixer.separation.stem_eq import _build_fir as build_stem_fir  # noqa: E402

SAMPLE_RATE = 48_000
MASTER_N_TAPS = 1023
STEM_N_TAPS = 511

WEB_OUT_DIR = ROOT / "web" / "public" / "eq_fir"


def main() -> None:
    WEB_OUT_DIR.mkdir(parents=True, exist_ok=True)

    for profile in EQ_PROFILES:
        ir = build_master_fir(profile, SAMPLE_RATE, MASTER_N_TAPS)
        path = WEB_OUT_DIR / f"master_{profile}.wav"
        sf.write(str(path), ir, SAMPLE_RATE, subtype="FLOAT")
        print(f"  wrote {path.relative_to(ROOT)}  ({len(ir)} taps)")

    for profile in STEM_EQ_PROFILES:
        ir = build_stem_fir(profile, SAMPLE_RATE, STEM_N_TAPS)
        path = WEB_OUT_DIR / f"stem_{profile}.wav"
        sf.write(str(path), ir, SAMPLE_RATE, subtype="FLOAT")
        print(f"  wrote {path.relative_to(ROOT)}  ({len(ir)} taps)")


if __name__ == "__main__":
    main()
