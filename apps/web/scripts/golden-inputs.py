"""Emit everything the browser-side golden render needs, as one JSON blob.

The harness (`render-preview-golden.mjs`) loads the shipped wasm and renders;
it does not own any DSP constant or filter asset. Those all come from here, so
the two sides of the comparison cannot drift into using different inputs — the
whole point of the render being a build-provenance check rather than a
re-derivation.

Run indirectly via `npm run golden:render`.
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np


def main() -> int:
    from upmixer.binaural.decoder import load_decode_filter_set
    from upmixer.binaural.renderer import BINAURAL_LOUDNESS_MAX_GAIN_DB
    from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
    from upmixer.binaural.profiles import DECODE_FILTER_SET, VOICING_PARAMS, resolve_profile
    from upmixer.config import UpmixConfig
    from upmixer.formats import FORMAT_MAP
    from upmixer.mastering.bass import (
        BASS_PROFILES, EXCITE_BLEND, EXCITE_DRIVE, MID_CUTOFF_HZ, SUB_CUTOFF_HZ,
    )
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.mastering.eq import _build_fir

    sample_rate = 48000
    fmt = FORMAT_MAP["7.1.4"]
    cfg = UpmixConfig()
    studio = resolve_profile("studio")
    decode = load_decode_filter_set(DECODE_FILTER_SET[studio], sample_rate)
    voicing = VOICING_PARAMS[studio]

    # The reference-match FIR is per-project, so it has no shipped asset; the
    # Python side exports one under REGENERATE_GOLDEN. Absent, the harness
    # skips that stage rather than inventing a filter.
    fixtures = pathlib.Path(__file__).resolve().parents[3] / (
        "packages/core/tests/fixtures/preview_export_golden"
    )
    reference = None
    fir_path = fixtures / "reference_match_fir.wav"
    meta_path = fixtures / "reference_match_meta.json"
    if fir_path.exists() and meta_path.exists():
        import soundfile as sf

        taps, _ = sf.read(str(fir_path), dtype="float64", always_2d=True)
        reference = {
            "fir": taps[:, 0].tolist(),
            "gain": 10.0 ** (json.loads(meta_path.read_text())["rms_gain_db"] / 20.0),
        }

    payload = {
        "sample_rate": sample_rate,
        "channels": [label.value for label in fmt.channels],
        "eq_fir": _build_fir("spatial-air", sample_rate, 1023).tolist(),
        "reference": reference,
        "compressor": COMP_PROFILES["glue"],
        "bass": {
            **BASS_PROFILES["enhance"],
            "sub_cutoff_hz": SUB_CUTOFF_HZ,
            "mid_cutoff_hz": MID_CUTOFF_HZ,
            "excite_blend": EXCITE_BLEND,
            "excite_drive": EXCITE_DRIVE,
        },
        "collapse": {
            "directions": [
                [
                    SPEAKER_AZIMUTH_ELEVATION[label].azimuth_rad
                    if label in SPEAKER_AZIMUTH_ELEVATION else 0.0,
                    SPEAKER_AZIMUTH_ELEVATION[label].elevation_rad
                    if label in SPEAKER_AZIMUTH_ELEVATION else 0.0,
                ]
                for label in fmt.channels
            ],
            "lfe_index": [label.value for label in fmt.channels].index("LFE"),
            "lfe_gain": cfg.lfe_gain,
            "lfe_cutoff_hz": cfg.lfe_cutoff_hz,
            "lfe_filter_order": cfg.lfe_filter_order,
            "n_taps": int(decode.taps.shape[-1]),
            "decode_taps": np.ascontiguousarray(decode.taps).reshape(-1).tolist(),
            "delivery": {
                "target_lkfs": voicing.loudness_target_lkfs or cfg.loudness_target_lkfs,
                "max_tp_dbtp": cfg.loudness_max_tp,
                "max_gain_db": min(cfg.loudness_max_gain_db, BINAURAL_LOUDNESS_MAX_GAIN_DB),
                "soft_limit_threshold": cfg.peak_limit_threshold,
            },
            "voicing": {
                "crossfeed_amount": voicing.crossfeed_amount,
                "crossfeed_cutoff_hz": voicing.crossfeed_cutoff_hz,
                "bass_shelf_hz": voicing.bass_shelf_hz,
                "bass_shelf_gain_db": voicing.bass_shelf_gain_db,
                "air_shelf_hz": voicing.air_shelf_hz,
                "air_shelf_gain_db": voicing.air_shelf_gain_db,
                "presence_hz": voicing.presence_hz,
                "presence_gain_db": voicing.presence_gain_db,
                "presence_q": voicing.presence_q,
                "stereo_widen": voicing.stereo_widen,
            },
        },
    }
    json.dump(payload, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
