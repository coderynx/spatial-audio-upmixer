"""Golden metric pins for the mastering chain, reference matching, and
binaural delivery on a fixed, deterministic synthetic multichannel bed.

`test_python_bed_metrics_golden` pins the mastering chain's LKFS/true-peak/
per-channel-RMS output the same way `test_mastering_golden.py` pins full-chain
output — regenerate via `REGENERATE_GOLDEN=1 python3 -m pytest
tests/test_render_metrics_golden.py`.

`test_python_reference_match_metrics_golden` extends that to reference
matching as mastering step 0 (`packages/core/src/mastering/match_reference/`).

`test_python_binaural_metrics_golden` extends it to `render_binaural_delivery`
(ambisonic encode -> HOA decode -> voicing -> BS.1770 loudness normalize ->
soft-limit, at the Studio profile) on the mastered bed.
"""
from __future__ import annotations

import os
import struct
import tempfile

import numpy as np
import pytest
import soundfile as sf

from upmixer.binaural.renderer import render_binaural_delivery
from upmixer.config import UpmixConfig
from upmixer.formats import BINAURAL, FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness, measure_true_peak
from upmixer.mastering import MasteringChain

_SR = 48000
_DURATION_S = 5
_FMT = FORMAT_MAP["7.1.4"]

# Pinned as hex-packed doubles (like test_mastering_golden.py) to avoid
# float-repr ambiguity across regenerations.
_GOLDEN_LKFS_HEX = "c01afd710da2745e"
_GOLDEN_TP_HEX = "c022fa89bc8f700c"
_GOLDEN_CHANNEL_RMS_HEX = {
    "BL": "3fc10c130a7555ad",
    "BR": "3fc1250e90d5763a",
    "C": "3fc0afa8a2085f32",
    "FL": "3fbd8ba1f30a1037",
    "FR": "3fc061d7ff607b53",
    "LFE": "3fc540ea676bbfbf",
    "SL": "3fc1363d912b56ce",
    "SR": "3fc143071bdcb9bb",
    "TBL": "3fc16a5d900fa852",
    "TBR": "3fc177f478f3e43f",
    "TFL": "3fc14da43c414263",
    "TFR": "3fc15c0658410d9d",
}

# Regenerate via `REGENERATE_GOLDEN=1 python3 -m pytest
# tests/test_render_metrics_golden.py::test_python_reference_match_metrics_golden -s`.
_GOLDEN_REFMATCH_LKFS_HEX = "c0262a4aa3758190"
_GOLDEN_REFMATCH_TP_HEX = "c0289991dd142ada"
_GOLDEN_REFMATCH_CHANNEL_RMS_HEX = {
    "BL": "3fb2c09119ca7b5e",
    "BR": "3fb2d74b030d4948",
    "C": "3fb4edaeb65cfa8d",
    "FL": "3fb2b42fe238c0af",
    "FR": "3fc0c52aaddbf420",
    "LFE": "3fb55a08e16149e3",
    "SL": "3fb2ec9f6a5aed8e",
    "SR": "3fb37410307e3ba5",
    "TBL": "3fb332283496f2c0",
    "TBR": "3fb3317359e3c213",
    "TFL": "3fb3cb1f6ccfb9c6",
    "TFR": "3fb31a5e9d71ed6c",
}

# Regenerate via `REGENERATE_GOLDEN=1 python3 -m pytest
# tests/test_render_metrics_golden.py::test_python_binaural_metrics_golden -s`.
_GOLDEN_BINAURAL_LKFS_HEX = "c03200000000001c"
_GOLDEN_BINAURAL_TP_HEX = "c026d2368a3b80c0"
_GOLDEN_BINAURAL_CHANNEL_RMS_HEX = {
    "FL": "3fb661491b33a6f0",
    "FR": "3fb6711655bd520e",
}


def _unhex(value: str) -> float:
    return struct.unpack(">d", bytes.fromhex(value))[0]


def _tohex(value: float) -> str:
    return struct.pack(">d", value).hex()


def _mastering_config() -> UpmixConfig:
    """Spectral EQ, bus compression, and bass control (incl. mono-maker via
    ``enhance``) on the discrete channel bed. ``loudness_normalize=False``:
    BS.1770 loudness normalization and the final soft-limit belong to the
    later collapse stage (`_binaural_config`), not this bed-level chain.
    """
    return UpmixConfig(
        mastering_eq_profile="spatial-air",
        mastering_comp_profile="glue",
        mastering_bass_profile="enhance",
        loudness_normalize=False,
    )


def _deterministic_bed(sr: int, duration_s: float, fmt) -> dict[str, np.ndarray]:
    """Fixed, reproducible synthetic multichannel bed.

    Each channel carries a distinct multi-tone signal (channel-index keyed, at
    incommensurate frequency ratios so it isn't a single pure tone), not
    identical across channels, so a channel-swap or per-channel gain bug shows
    up as a per-channel RMS mismatch instead of being masked.
    """
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    channels: dict[str, np.ndarray] = {}
    for i, label in enumerate(fmt.channels):
        base_freq = 110.0 * (i + 1)
        signal = (
            0.20 * np.sin(2 * np.pi * base_freq * t)
            + 0.05 * np.sin(2 * np.pi * base_freq * 2.37 * t + 0.7)
            + 0.03 * np.sin(2 * np.pi * base_freq * 5.11 * t + 1.3)
            + 0.02 * np.sin(2 * np.pi * base_freq * 11.03 * t + 2.1)
        )
        channels[label.value] = signal.astype(np.float64)
    return channels


def _metrics(channels: dict[str, np.ndarray], sr: int, fmt) -> dict:
    return {
        "measured_lkfs": measure_integrated_loudness(channels, sr, fmt),
        "measured_tp_dbtp": measure_true_peak(channels, sr),
        "channel_rms": {
            name: float(np.sqrt(np.mean(np.square(ch)))) for name, ch in channels.items()
        },
    }


def _mastered_bed_channels() -> dict[str, np.ndarray]:
    channels = _deterministic_bed(_SR, _DURATION_S, _FMT)
    mastered, _result = MasteringChain(_mastering_config()).process(channels, _SR, _FMT)
    return mastered


def _render_python_bed() -> dict:
    return _metrics(_mastered_bed_channels(), _SR, _FMT)


def _deterministic_reference(sr: int, duration_s: float) -> np.ndarray:
    """Fixed, reproducible synthetic stereo reference file — deliberately a
    distinct tonal balance from `_deterministic_bed` (more high-frequency
    energy, less low) so `ReferenceMatchProcessor` computes a genuinely
    non-trivial correction curve, not a near-zero one.
    """
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    left = (
        0.05 * np.sin(2 * np.pi * 220.0 * t)
        + 0.15 * np.sin(2 * np.pi * 2200.0 * t + 0.4)
        + 0.10 * np.sin(2 * np.pi * 6000.0 * t + 1.1)
    )
    right = (
        0.05 * np.sin(2 * np.pi * 233.0 * t)
        + 0.15 * np.sin(2 * np.pi * 2337.0 * t + 0.9)
        + 0.10 * np.sin(2 * np.pi * 6100.0 * t + 1.7)
    )
    return np.stack([left, right], axis=1).astype(np.float64)


def _reference_match_config(reference_path: str) -> UpmixConfig:
    """Same bed-stage config as `_mastering_config`, plus reference matching
    as mastering step 0 — fixed strength/max_db so the golden fixture is
    reproducible."""
    cfg = _mastering_config()
    cfg.mastering_match_ref_path = reference_path
    cfg.mastering_match_ref_strength = 0.6
    cfg.mastering_match_ref_spectrum = True
    cfg.mastering_match_ref_rms = True
    cfg.mastering_match_ref_max_db = 6.0
    return cfg


def _mastered_bed_with_reference_channels() -> dict[str, np.ndarray]:
    channels = _deterministic_bed(_SR, _DURATION_S, _FMT)
    reference = _deterministic_reference(_SR, _DURATION_S)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        reference_path = tmp.name
    try:
        sf.write(reference_path, reference, _SR, subtype="FLOAT")
        mastered, _result = MasteringChain(_reference_match_config(reference_path)).process(channels, _SR, _FMT)
    finally:
        os.unlink(reference_path)
    return mastered


def _render_python_reference_match() -> dict:
    return _metrics(_mastered_bed_with_reference_channels(), _SR, _FMT)


def _binaural_config() -> UpmixConfig:
    """Config for `render_binaural_delivery`'s own collapse-stage pass.

    Studio profile, -18 LKFS target, 0.95 peak-limit threshold — the defaults
    with no per-project loudness/profile override. Deliberately a separate
    `UpmixConfig` from `_mastering_config()`'s (which stays
    `loudness_normalize=False`, scoped to the bed-only stage above) — this is
    the later, independent collapse-stage loudness pass.
    """
    return UpmixConfig(loudness_normalize=True)


def _render_python_binaural() -> dict:
    stereo, _result = render_binaural_delivery(_mastered_bed_channels(), _FMT, _SR, _binaural_config())
    return _metrics(stereo, _SR, BINAURAL)


def test_python_bed_metrics_golden():
    metrics = _render_python_bed()

    if os.environ.get("REGENERATE_GOLDEN"):
        print(f'_GOLDEN_LKFS_HEX = "{_tohex(metrics["measured_lkfs"])}"')
        print(f'_GOLDEN_TP_HEX = "{_tohex(metrics["measured_tp_dbtp"])}"')
        print("_GOLDEN_CHANNEL_RMS_HEX = {")
        for name in sorted(metrics["channel_rms"]):
            print(f'    "{name}": "{_tohex(metrics["channel_rms"][name])}",')
        print("}")
        pytest.skip("Printed regenerated golden values — paste them in and rerun.")

    assert metrics["measured_lkfs"] == pytest.approx(_unhex(_GOLDEN_LKFS_HEX))
    assert metrics["measured_tp_dbtp"] == pytest.approx(_unhex(_GOLDEN_TP_HEX))
    for name, rms in metrics["channel_rms"].items():
        assert rms == pytest.approx(_unhex(_GOLDEN_CHANNEL_RMS_HEX[name])), (
            f"channel {name} RMS drifted from its golden value"
        )


def test_python_reference_match_metrics_golden():
    """Pins the bed-stage output with reference matching prepended as
    mastering step 0."""
    metrics = _render_python_reference_match()

    if os.environ.get("REGENERATE_GOLDEN"):
        print(f'_GOLDEN_REFMATCH_LKFS_HEX = "{_tohex(metrics["measured_lkfs"])}"')
        print(f'_GOLDEN_REFMATCH_TP_HEX = "{_tohex(metrics["measured_tp_dbtp"])}"')
        print("_GOLDEN_REFMATCH_CHANNEL_RMS_HEX = {")
        for name in sorted(metrics["channel_rms"]):
            print(f'    "{name}": "{_tohex(metrics["channel_rms"][name])}",')
        print("}")
        pytest.skip("Printed regenerated golden values — paste them in and rerun.")

    assert metrics["measured_lkfs"] == pytest.approx(_unhex(_GOLDEN_REFMATCH_LKFS_HEX))
    assert metrics["measured_tp_dbtp"] == pytest.approx(_unhex(_GOLDEN_REFMATCH_TP_HEX))
    for name, rms in metrics["channel_rms"].items():
        assert rms == pytest.approx(_unhex(_GOLDEN_REFMATCH_CHANNEL_RMS_HEX[name])), (
            f"channel {name} RMS drifted from its golden value"
        )


def test_python_binaural_metrics_golden():
    """Pins `render_binaural_delivery`'s output on the mastered bed."""
    metrics = _render_python_binaural()

    if os.environ.get("REGENERATE_GOLDEN"):
        print(f'_GOLDEN_BINAURAL_LKFS_HEX = "{_tohex(metrics["measured_lkfs"])}"')
        print(f'_GOLDEN_BINAURAL_TP_HEX = "{_tohex(metrics["measured_tp_dbtp"])}"')
        print("_GOLDEN_BINAURAL_CHANNEL_RMS_HEX = {")
        for name in sorted(metrics["channel_rms"]):
            print(f'    "{name}": "{_tohex(metrics["channel_rms"][name])}",')
        print("}")
        pytest.skip("Printed regenerated golden values — paste them in and rerun.")

    assert metrics["measured_lkfs"] == pytest.approx(_unhex(_GOLDEN_BINAURAL_LKFS_HEX))
    assert metrics["measured_tp_dbtp"] == pytest.approx(_unhex(_GOLDEN_BINAURAL_TP_HEX))
    for name, rms in metrics["channel_rms"].items():
        assert rms == pytest.approx(_unhex(_GOLDEN_BINAURAL_CHANNEL_RMS_HEX[name])), (
            f"channel {name} RMS drifted from its golden value"
        )


if __name__ == "__main__":
    os.environ.setdefault("REGENERATE_GOLDEN", "1")
    test_python_bed_metrics_golden()
