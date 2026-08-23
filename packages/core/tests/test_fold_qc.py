"""Post-limiter fold QC (mastering phase 8).

The delivered bed is never touched by this pass, so every test here checks a
*measurement*: that a fold's loudness matches the BS.775 arithmetic it is
built from, that correlated content summing over the ceiling post-fold raises
a flag, and that the binaural QC render is gated by layout.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.mastering.chain import MasteringChain
from upmixer.mastering.foldqc import FOLD_DIVERGENCE_LU, measure_binaural_qc

_SR = 48_000
_DUR_S = 8
_SEED = 20260819


def _noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    signal = rng.standard_normal(n)
    return 0.4 * signal / np.max(np.abs(signal))


def _master(bed: dict[str, np.ndarray], layout: str, **overrides):
    cfg = UpmixConfig(output_format=layout, **overrides)
    return MasteringChain(cfg).process(bed, _SR, FORMAT_MAP[layout])


def _height_bed() -> dict[str, np.ndarray]:
    """A 7.1.4 bed carrying nothing but the front height pair."""
    n = _DUR_S * _SR
    bed = {label.value: np.zeros(n) for label in FORMAT_MAP["7.1.4"].channels}
    bed["TFL"] = _noise(n, _SEED)
    bed["TFR"] = _noise(n, _SEED + 1)
    return bed


def test_a_height_only_bed_folds_to_stereo_at_the_height_coefficient() -> None:
    """Hand-computed BS.775 arithmetic, with every other term zeroed.

    With only TFL/TFR carrying signal, the 2/0 downmix is ``Lo = k_h·TFL`` and
    ``Ro = k_h·TFR``.  Both channels carry BS.1770 unity weight before and
    after, so the fold's integrated loudness is exactly ``20·log10(k_h)`` —
    −3.01 LU at the default ``k_h = 0.7071``.
    """
    _, result = _master(_height_bed(), "7.1.4")

    expected = 20.0 * math.log10(0.7071)
    assert result.folds is not None
    assert result.folds.stereo is not None
    assert result.folds.stereo.lkfs_delta_lu == pytest.approx(expected, abs=0.02)


def test_a_height_only_bed_folds_to_five_one_at_the_same_coefficient() -> None:
    """The 5.1 re-render folds TFL onto FL at the same ``k_h``, so it lands on
    the same −3.01 LU — from four measured channels down to two."""
    _, result = _master(_height_bed(), "7.1.4")

    assert result.folds is not None
    assert result.folds.surround_51 is not None
    assert result.folds.surround_51.lkfs_delta_lu == pytest.approx(
        20.0 * math.log10(0.7071), abs=0.02
    )


def test_the_stereo_fold_of_correlated_surround_content_flags_over_the_ceiling() -> None:
    """Correlated in-phase content in SL/SR and FL/FR sums past the ceiling.

    The limiter guarantees the ceiling per channel on the delivered bed; the
    fold is a linear mix, so ``Lo = FL + k_s·SL`` on identical channels is
    ``1.7071·FL`` — 4.65 dB of headroom the limiter never budgeted for.
    """
    n = _DUR_S * _SR
    shared = _noise(n, _SEED)
    bed = {label.value: np.zeros(n) for label in FORMAT_MAP["5.1"].channels}
    for name in ("FL", "FR", "SL", "SR"):
        bed[name] = shared.copy()

    # Hot enough for the limiter to park the bed on the ceiling, which is the
    # only state in which the fold's own headroom is the question.
    _, result = _master(bed, "5.1", loudness_target_lkfs=-5.0)

    assert result.tp_compliant is True
    assert result.folds is not None
    assert result.folds.stereo is not None
    assert result.folds.stereo.tp_compliant is False
    assert result.folds.stereo.tp_dbtp - result.measured_tp_dbtp == pytest.approx(
        20.0 * math.log10(1.0 + 0.7071), abs=0.1
    )


def test_a_decorrelated_bed_folds_inside_the_divergence_threshold() -> None:
    """A front-dominant decorrelated bed folds within the documented tolerance.

    The trims are the phase 0 test programme's, so this pins the threshold
    against the material it was chosen from
    (``docs/plans/mastering/phase8_report.md``).
    """
    trim = {
        "FL": 1.0, "FR": 1.0, "C": 0.7,
        "SL": 0.5, "SR": 0.5, "BL": 0.4, "BR": 0.4,
        "TFL": 0.35, "TFR": 0.35, "TBL": 0.3, "TBR": 0.3,
    }
    n = _DUR_S * _SR
    fmt = FORMAT_MAP["7.1.4"]
    bed = {
        label.value: trim[label.value] * _noise(n, _SEED + i)
        for i, label in enumerate(fmt.channels)
        if label.value != "LFE"
    }
    bed["LFE"] = np.zeros(n)

    _, result = _master(bed, "7.1.4")

    assert result.folds is not None
    for name, fold in result.folds.measurements().items():
        assert abs(fold.lkfs_delta_lu) <= FOLD_DIVERGENCE_LU, name
        assert fold.loudness_divergent is False, name
    assert result.folds.flagged() is False


def test_a_stereo_delivery_has_no_fold_to_measure() -> None:
    n = _DUR_S * _SR
    bed = {"FL": _noise(n, _SEED), "FR": _noise(n, _SEED + 1)}

    _, result = _master(bed, "stereo")

    assert result.folds is None


def test_the_binaural_qc_render_is_gated_by_layout() -> None:
    """On by default for the height-bearing beds a binaural delivery is valid
    for, off elsewhere, and overridable either way."""
    cfg = UpmixConfig()
    assert measure_binaural_qc(cfg, FORMAT_MAP["7.1.4"]) is True
    assert measure_binaural_qc(cfg, FORMAT_MAP["5.1.4"]) is True
    assert measure_binaural_qc(cfg, FORMAT_MAP["5.1"]) is False
    assert measure_binaural_qc(cfg, FORMAT_MAP["stereo"]) is False
    assert measure_binaural_qc(UpmixConfig(qc_measure_binaural=False), FORMAT_MAP["7.1.4"]) is False
    assert measure_binaural_qc(UpmixConfig(qc_measure_binaural=True), FORMAT_MAP["5.1"]) is True


def test_the_binaural_gate_keeps_the_render_out_of_the_result() -> None:
    _, result = _master(_height_bed(), "7.1.4", qc_measure_binaural=False)

    assert result.folds is not None
    assert result.folds.binaural is None
    assert "binaural" not in result.folds.measurements()


def test_the_binaural_qc_render_measures_the_finished_headphone_artifact() -> None:
    _, result = _master(_height_bed(), "7.1.4")

    assert result.folds is not None
    assert result.folds.binaural is not None
    assert result.folds.binaural.tp_compliant is True
    assert result.folds.binaural.plr_db > 0.0


def test_the_folds_block_survives_result_serialization() -> None:
    """``UpmixResult.to_dict`` is what the jobs API carries, so the nested
    dataclass has to flatten to plain JSON types."""
    from upmixer.result import UpmixResult

    _, mastering = _master(_height_bed(), "7.1.4")
    payload = UpmixResult(
        input_path="in.wav",
        output_path="out.wav",
        input_format="Stereo",
        output_format="7.1.4",
        input_sample_rate=_SR,
        output_sample_rate=_SR,
        duration_seconds=float(_DUR_S),
        n_channels_in=2,
        n_channels_out=12,
        mode="stem",
        **mastering.delivery_fields(),
    ).to_dict()

    assert set(payload["folds"]) == {
        "native_lkfs",
        "stereo",
        "surround_51",
        "binaural",
    }
    assert payload["folds"]["stereo"]["tp_compliant"] is True
    assert isinstance(payload["folds"]["stereo"]["lkfs_delta_lu"], float)
