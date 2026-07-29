"""Cross-engine golden render diff — docs/contracts/preview_export_parity.md §5.

Verifies the export engine's mastering-chain output on a fixed,
deterministic synthetic multichannel bed, and diffs it against the browser
preview's equivalent render (via a headless Node harness — see below)
within the tolerances that document pins.

`test_python_bed_metrics_golden` pins the export engine's LKFS/true-peak/
per-channel-RMS metrics for the fixed bed, the same way
`test_mastering_golden.py` pins full-chain output — regenerate via
`REGENERATE_GOLDEN=1 python3 -m pytest tests/test_preview_export_golden.py`.

`test_cross_engine_golden_diff` is the actual contract acceptance test, and
runs in the default suite (not opt-in) — it reads the *committed*
`tests/fixtures/preview_export_golden/web_bed_metrics.json`, produced by
running `npm run golden:render` from `web/` (see
`web/scripts/render-preview-golden.mjs`) — that script bundles the real
`web/src/features/projects/previewGraph.ts` with esbuild, renders the same
deterministic bed on a real `OfflineAudioContext` via `node-web-audio-api`
(a spec-compliant native Web Audio implementation for Node), and writes the
same metrics shape `_metrics()` below produces. Re-run that command to
refresh the fixture after a web-side DSP/constant change; if the fixture is
missing entirely, this test skips with a message pointing at that command
rather than passing vacuously — regenerate the fixture instead of loosening
the assertions.

Scope: `test_python_bed_metrics_golden`/`test_cross_engine_golden_diff` cover
the EQ/compressor/bass mastering-chain stage only — exactly what
`previewGraph.ts`'s `buildMasteringGraph` implements. `_mastering_config()`
below deliberately disables loudness normalization for this reason (that bed-
level stage stays out of scope here). Building this harness surfaced two real
bugs in `previewGraph.ts`, both fixed — see Ledger D8 and D9.

`test_python_binaural_metrics_golden`/`test_cross_engine_binaural_golden_diff`
extend this to the previously-uncovered stage (Ledger D10): the mastered bed
is fed through `render_binaural_delivery` (ambisonic encode -> HOA decode ->
voicing -> BS.1770 loudness normalize -> soft-limit, at the Studio profile),
diffed against the equivalent web harness pass in
`web/scripts/render-preview-golden.mjs` (which now also bundles
`previewGraph.ts`'s extracted `buildBinauralGraph` — see Ledger D7's
successor there). Studio profile's voicing chain is all-zero/identity, which
is why Ledger D11 (the preview adding LFE after voicing, not before like
`render_binaural`) doesn't show up in this diff — see that ledger entry.
"""
from __future__ import annotations

import json
import os
import struct

import numpy as np
import pytest

from upmixer.binaural.renderer import render_binaural_delivery
from upmixer.config import UpmixConfig
from upmixer.formats import BINAURAL, FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness, measure_true_peak
from upmixer.mastering import MasteringChain

_SR = 48000
_DURATION_S = 5
_FMT = FORMAT_MAP["7.1.4"]
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "preview_export_golden")
_WEB_METRICS_PATH = os.path.join(_FIXTURE_DIR, "web_bed_metrics.json")
_WEB_BINAURAL_METRICS_PATH = os.path.join(_FIXTURE_DIR, "web_binaural_metrics.json")

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
# tests/test_preview_export_golden.py::test_python_binaural_metrics_golden -s`.
_GOLDEN_BINAURAL_LKFS_HEX = "c031ffffffffffdb"
_GOLDEN_BINAURAL_TP_HEX = "c027034c97f9bcf8"
_GOLDEN_BINAURAL_CHANNEL_RMS_HEX = {
    "FL": "3fb61cd27296a29b",
    "FR": "3fb680ff8c2b562c",
}


def _unhex(value: str) -> float:
    return struct.unpack(">d", bytes.fromhex(value))[0]


def _tohex(value: float) -> str:
    return struct.pack(">d", value).hex()


def _mastering_config() -> UpmixConfig:
    """The mastering configuration both engines must apply to the bed.

    Scoped to exactly what `web/src/features/projects/previewGraph.ts`
    currently implements: spectral EQ, bus compression, and bass control
    (incl. mono-maker via ``enhance``) on the discrete channel bed — the
    stages `buildMasteringGraph` was extracted from
    (`buildMasteringTopology`). ``loudness_normalize=False`` is deliberate:
    BS.1770 loudness normalization and the final soft-limit are a *later*,
    not-yet-extracted stage in the live preview (applied downstream of the
    binaural/stereo collapse, not inside this bed-level chain — see Ledger
    D7's "remaining scope" note in docs/contracts/preview_export_parity.md
    §5). Enabling it here would compare a Python stage the web harness
    doesn't render at all, producing a large, meaningless delta rather than
    a real regression signal.
    """
    return UpmixConfig(
        mastering_eq_profile="spatial-air",
        mastering_comp_profile="glue",
        mastering_bass_profile="enhance",
        loudness_normalize=False,
    )


def _deterministic_bed(sr: int, duration_s: float, fmt) -> dict[str, np.ndarray]:
    """Fixed, reproducible synthetic multichannel bed.

    Each channel carries a distinct multi-tone signal (channel-index keyed,
    at incommensurate frequency ratios so it isn't a single pure tone) —
    deliberately **not** RNG-based noise: this bed must be regenerated
    bit-for-bit identically by the web harness's JS port
    (web/scripts/render-preview-golden.mjs::deterministicBed), and matching
    a NumPy PCG64 bitstream in JS is impractical, whereas `Math.sin`/`math.sin`
    of the same double input agree to float precision on both sides — far
    inside this module's dB-scale tolerances. Distinct per-channel content
    (not identical across channels) means a channel-swap or per-channel gain
    bug shows up as a per-channel RMS mismatch instead of being masked.
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


def _binaural_config() -> UpmixConfig:
    """Config for `render_binaural_delivery`'s own collapse-stage pass.

    Defaults (Studio profile, -18 LKFS target, 0.95 peak-limit threshold)
    match what `useStemPreview.ts`'s `apply()` computes for binaural output
    with no per-project loudness/profile override — the same assumption
    `web/scripts/render-preview-golden.mjs`'s binaural stage hardcodes.
    Deliberately a separate `UpmixConfig` from `_mastering_config()`'s (which
    stays `loudness_normalize=False`, scoped to the bed-only stage above) —
    this is the later, independent collapse-stage loudness pass.
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


def test_cross_engine_golden_diff():
    """The actual preview/export parity acceptance test (§5 tolerances).

    Reads ``tests/fixtures/preview_export_golden/web_bed_metrics.json``,
    produced by ``npm run golden:render`` (see
    ``web/scripts/render-preview-golden.mjs``) — regenerate it if this test
    fails on a change to the bed, the mastering config, or the preview
    graph, rather than loosening these assertions.
    """
    if not os.path.exists(_WEB_METRICS_PATH):
        pytest.skip(
            f"{_WEB_METRICS_PATH} not found — run `npm run golden:render` "
            "from web/ to generate it (see web/scripts/render-preview-golden.mjs). "
            "See this module's docstring and docs/contracts/preview_export_parity.md §5."
        )
    python_metrics = _render_python_bed()
    with open(_WEB_METRICS_PATH) as f:
        web_metrics = json.load(f)

    lkfs_delta = abs(python_metrics["measured_lkfs"] - web_metrics["measured_lkfs"])
    tp_delta = abs(python_metrics["measured_tp_dbtp"] - web_metrics["measured_tp_dbtp"])
    assert lkfs_delta <= 1.0, f"Integrated LKFS delta {lkfs_delta:.2f} exceeds the 1.0 LU contract threshold"
    assert tp_delta <= 1.0, f"True-peak delta {tp_delta:.2f} dBTP exceeds the 1.0 dBTP contract threshold"

    for name, python_rms in python_metrics["channel_rms"].items():
        web_rms = web_metrics["channel_rms"].get(name)
        assert web_rms is not None, f"web fixture missing channel {name}"
        if python_rms < 1e-9:
            continue
        ratio_db = 20.0 * np.log10(max(web_rms, 1e-12) / python_rms)
        assert abs(ratio_db) <= 3.0, (
            f"channel {name} RMS delta {ratio_db:+.1f} dB exceeds the 3 dB contract threshold"
        )


def test_python_binaural_metrics_golden():
    """Pins `render_binaural_delivery`'s output on the mastered bed (Ledger D10)."""
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


def test_cross_engine_binaural_golden_diff():
    """The binaural-collapse parity acceptance test (§5 tolerances, Ledger D10).

    Reads ``tests/fixtures/preview_export_golden/web_binaural_metrics.json``,
    produced by ``npm run golden:render`` (see the binaural stage
    ``web/scripts/render-preview-golden.mjs`` adds after its existing bed
    render) — regenerate it if this test fails on a change to the bed, the
    mastering config, the Spatial Audio Engine profile, or either engine's
    binaural graph, rather than loosening these assertions.
    """
    if not os.path.exists(_WEB_BINAURAL_METRICS_PATH):
        pytest.skip(
            f"{_WEB_BINAURAL_METRICS_PATH} not found — run `npm run golden:render` "
            "from web/ to generate it (see web/scripts/render-preview-golden.mjs). "
            "See this module's docstring and docs/contracts/preview_export_parity.md §5."
        )
    python_metrics = _render_python_binaural()
    with open(_WEB_BINAURAL_METRICS_PATH) as f:
        web_metrics = json.load(f)

    lkfs_delta = abs(python_metrics["measured_lkfs"] - web_metrics["measured_lkfs"])
    tp_delta = abs(python_metrics["measured_tp_dbtp"] - web_metrics["measured_tp_dbtp"])
    assert lkfs_delta <= 1.0, f"Integrated LKFS delta {lkfs_delta:.2f} exceeds the 1.0 LU contract threshold"
    assert tp_delta <= 1.0, f"True-peak delta {tp_delta:.2f} dBTP exceeds the 1.0 dBTP contract threshold"

    for name, python_rms in python_metrics["channel_rms"].items():
        web_rms = web_metrics["channel_rms"].get(name)
        assert web_rms is not None, f"web fixture missing channel {name}"
        if python_rms < 1e-9:
            continue
        ratio_db = 20.0 * np.log10(max(web_rms, 1e-12) / python_rms)
        assert abs(ratio_db) <= 3.0, (
            f"channel {name} RMS delta {ratio_db:+.1f} dB exceeds the 3 dB contract threshold"
        )


if __name__ == "__main__":
    os.environ.setdefault("REGENERATE_GOLDEN", "1")
    test_python_bed_metrics_golden()
