"""Named delivery targets and the 5.1-re-render measurement programme."""
from __future__ import annotations

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_integrated_loudness, measurement_programme
from upmixer.mastering import MasteringChain
from upmixer.mastering.delivery import (
    DELIVERY_TARGETS,
    DEFAULT_MAX_TP_DBTP,
    DEFAULT_TARGET_LKFS,
    resolve_delivery_target,
)

_SR = 48_000
_K = 1.0 / np.sqrt(2.0)


def _bed(layout: str, seed: int = 11, amplitude: float = 0.2) -> dict[str, np.ndarray]:
    """A decorrelated bed of `layout`, two seconds long."""
    rng = np.random.default_rng(seed)
    n = 2 * _SR
    return {
        label.value: (amplitude * rng.standard_normal(n)).astype(np.float64)
        for label in FORMAT_MAP[layout].channels
    }


class TestTargetResolution:
    def test_no_preset_keeps_the_atmos_music_defaults(self):
        target = resolve_delivery_target(UpmixConfig())
        assert target.preset is None
        assert target.target_lkfs == DEFAULT_TARGET_LKFS
        assert target.max_tp_dbtp == DEFAULT_MAX_TP_DBTP
        assert target.tolerance_lu is None

    @pytest.mark.parametrize("name", sorted(DELIVERY_TARGETS))
    def test_a_preset_supplies_both_numbers_and_its_tolerance(self, name):
        target = resolve_delivery_target(UpmixConfig(loudness_target_preset=name))
        preset = DELIVERY_TARGETS[name]
        assert target.preset == name
        assert target.target_lkfs == preset["target_lkfs"]
        assert target.max_tp_dbtp == preset["max_tp_dbtp"]
        assert target.tolerance_lu == preset["tolerance_lu"]

    def test_explicit_fields_override_the_preset_one_at_a_time(self):
        target = resolve_delivery_target(
            UpmixConfig(loudness_target_preset="ebu-r128", loudness_target_lkfs=-20.0)
        )
        assert target.target_lkfs == -20.0
        assert target.max_tp_dbtp == DELIVERY_TARGETS["ebu-r128"]["max_tp_dbtp"]
        assert target.tolerance_lu == DELIVERY_TARGETS["ebu-r128"]["tolerance_lu"]

    def test_an_unknown_preset_falls_back_to_the_defaults(self):
        target = resolve_delivery_target(UpmixConfig(loudness_target_preset="atmos"))
        assert target.preset is None
        assert target.target_lkfs == DEFAULT_TARGET_LKFS


class TestMeasurementProgramme:
    def test_a_five_one_bed_is_already_its_own_programme(self):
        bed = _bed("5.1")
        programme, fmt = measurement_programme(bed, FORMAT_MAP["5.1"])
        assert programme is bed
        assert fmt is FORMAT_MAP["5.1"]

    def test_the_fold_matches_the_hand_computed_bs775_re_render(self):
        bed = _bed("7.1.4")
        programme, fmt = measurement_programme(bed, FORMAT_MAP["7.1.4"])

        assert fmt is FORMAT_MAP["5.1"]
        assert set(programme) == {"FL", "FR", "C", "SL", "SR"}
        assert np.allclose(programme["FL"], bed["FL"] + _K * bed["TFL"])
        assert np.allclose(programme["FR"], bed["FR"] + _K * bed["TFR"])
        assert np.allclose(programme["C"], bed["C"])
        assert np.allclose(programme["SL"], bed["SL"] + _K * bed["BL"] + _K * bed["TBL"])
        assert np.allclose(programme["SR"], bed["SR"] + _K * bed["BR"] + _K * bed["TBR"])

    def test_height_only_content_reads_quieter_than_the_full_bed(self):
        """The case the fold exists for: heights carry the programme, and the
        full-bed measurement over-reads what a 5.1 re-render delivers."""
        bed = _bed("7.1.4")
        for name in bed:
            if not name.startswith("T"):
                bed[name] = np.zeros_like(bed[name])

        full = measure_integrated_loudness(bed, _SR, FORMAT_MAP["7.1.4"])
        programme, fmt = measurement_programme(bed, FORMAT_MAP["7.1.4"])
        folded = measure_integrated_loudness(programme, _SR, fmt)
        assert folded < full - 1.0


class TestChainCompliance:
    def test_an_immersive_master_lands_on_its_five_one_re_render(self):
        cfg = UpmixConfig(loudness_normalize=True, loudness_target_preset="atmos-music")
        out, result = MasteringChain(cfg).process(_bed("7.1.4"), _SR, FORMAT_MAP["7.1.4"])

        assert result.fold_referenced
        assert result.measured_lkfs == pytest.approx(-18.0, abs=0.1)
        # The bed itself is louder than its re-render, and both are reported.
        assert result.full_bed_lkfs is not None
        assert result.full_bed_lkfs > result.measured_lkfs
        programme, fmt = measurement_programme(out, FORMAT_MAP["7.1.4"])
        assert measure_integrated_loudness(programme, _SR, fmt) == pytest.approx(
            result.measured_lkfs, abs=1e-9
        )

    def test_a_five_one_master_reports_no_fold(self):
        cfg = UpmixConfig(loudness_normalize=True, loudness_target_preset="ebu-r128")
        _, result = MasteringChain(cfg).process(_bed("5.1"), _SR, FORMAT_MAP["5.1"])

        assert not result.fold_referenced
        assert result.full_bed_lkfs is None
        assert result.measured_lkfs == pytest.approx(-23.0, abs=0.1)

    def test_compliance_is_reported_against_the_preset(self):
        cfg = UpmixConfig(loudness_normalize=True, loudness_target_preset="ebu-r128")
        _, result = MasteringChain(cfg).process(_bed("5.1"), _SR, FORMAT_MAP["5.1"])

        assert result.target_preset == "ebu-r128"
        assert result.target_lkfs == -23.0
        assert result.target_tolerance_lu == 0.5
        assert result.loudness_compliant is True
        assert result.tp_compliant is True

    def test_a_target_the_gain_limit_cannot_reach_reads_non_compliant(self):
        """`loudness_max_gain_db` caps the lift, so a near-silent bed cannot
        reach a hot target — the compliance block is where that shows."""
        cfg = UpmixConfig(
            loudness_normalize=True,
            loudness_target_preset="ebu-r128",
            loudness_max_gain_db=1.0,
        )
        bed = _bed("5.1", amplitude=1e-3)
        _, result = MasteringChain(cfg).process(bed, _SR, FORMAT_MAP["5.1"])

        assert result.measured_lkfs < -23.5
        assert result.loudness_compliant is False

    def test_a_target_without_a_published_tolerance_reports_none(self):
        cfg = UpmixConfig(loudness_normalize=True, loudness_target_preset="apple-music")
        _, result = MasteringChain(cfg).process(_bed("5.1"), _SR, FORMAT_MAP["5.1"])

        assert result.target_tolerance_lu is None
        assert result.loudness_compliant is None
        assert result.tp_compliant is True
