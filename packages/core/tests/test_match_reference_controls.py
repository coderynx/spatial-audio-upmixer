"""Mastering phase 7's reference-match realization controls: smoothing
bandwidth and the two frequency-range masks.

All three act at curve realization (:func:`build_curve_fir`), never at
analysis, so the persisted curve stays control-independent — see
``docs/plans/mastering/phase7_report.md``.
"""
from __future__ import annotations

import numpy as np
import pytest

import upmixer.mastering.match_reference  # noqa: F401 — triggers register_block_keys for mastering.match_reference
from upmixer.config import UpmixConfig
from upmixer.formats import SURROUND_51
from upmixer.manifest import ManifestError, apply_asset_job, parse_manifest
from upmixer.manifest.validate import validate_manifest
from upmixer.mastering.match_reference import ReferenceMatchProcessor, build_curve_fir
from upmixer.mastering.match_reference.curve import _log_grid, realize_curve

from test_match_reference import SR, _51_channels, _51_ref, _fir_response_db


def _make_proc(**kwargs) -> ReferenceMatchProcessor:
    return ReferenceMatchProcessor(
        reference_path="__synthetic__", output_fmt=SURROUND_51, sample_rate=SR, **kwargs
    )


class TestCurveRealizationControls:
    """Mastering phase 7's three controls, all acting at realization."""

    def _notch_curve(self) -> tuple[np.ndarray, list[tuple[float, float]]]:
        grid = _log_grid(20000.0)
        gains = np.zeros(len(grid))
        gains[int(np.argmin(np.abs(grid - 2000.0)))] = -12.0
        return grid, [(float(f), float(g)) for f, g in zip(grid, gains)]

    def test_narrow_smoothing_keeps_a_notch_a_coarse_one_erases_it(self):
        grid, curve = self._notch_curve()
        peak = int(np.argmin(np.abs(grid - 2000.0)))
        fine = realize_curve(curve, 1.0, 24.0, 1.0 / 12.0)
        coarse = realize_curve(curve, 1.0, 24.0, 1.0)
        assert fine[peak] < -2.0
        assert coarse[peak] > -0.3

    def test_smoothing_defaults_to_a_third_octave(self):
        _, curve = self._notch_curve()
        assert np.allclose(realize_curve(curve, 1.0, 24.0), realize_curve(curve, 1.0, 24.0, 1.0 / 3.0))

    def test_low_mask_leaves_the_bottom_end_alone(self):
        curve = [(float(f), 6.0) for f in _log_grid(20000.0)]
        fir = build_curve_fir(curve, SR, 1023, 1.0, 12.0, low_hz=300.0)
        assert abs(_fir_response_db(fir, SR, 80.0)) < 0.2
        assert abs(_fir_response_db(fir, SR, 150.0)) < 0.2
        assert _fir_response_db(fir, SR, 1000.0) > 5.0

    def test_high_mask_leaves_the_top_end_alone(self):
        curve = [(float(f), 6.0) for f in _log_grid(20000.0)]
        fir = build_curve_fir(curve, SR, 1023, 1.0, 12.0, high_hz=2000.0)
        assert _fir_response_db(fir, SR, 1000.0) > 5.0
        assert abs(_fir_response_db(fir, SR, 6000.0)) < 0.2

    def test_unset_masks_match_the_full_range(self):
        curve = [(float(f), 6.0) for f in _log_grid(20000.0)]
        assert np.array_equal(
            build_curve_fir(curve, SR, 1023, 1.0, 12.0),
            build_curve_fir(curve, SR, 1023, 1.0, 12.0, None, None, None),
        )

    def test_the_stored_curve_is_untouched_by_any_control(self):
        """The persisted curve is control-independent, so no knob forces a
        re-analysis (parity contract §1, ledger D13)."""
        channels = _51_channels()
        plain = _make_proc()
        plain._ref_data = _51_ref()
        knobbed = _make_proc(strength=0.2, max_correction_db=2.0,
                             smooth_octaves=1.0, low_hz=300.0, high_hz=9000.0)
        knobbed._ref_data = _51_ref()
        assert plain.compute_curve(channels) == knobbed.compute_curve(channels)

    def test_masks_ease_rather_than_step(self):
        grid = _log_grid(20000.0)
        curve = [(float(f), 6.0) for f in grid]
        gains = realize_curve(curve, 1.0, 24.0, 1.0 / 3.0, 300.0)
        eased = [g for f, g in zip(grid, gains) if 212.0 < f < 300.0]
        assert eased == sorted(eased)
        assert 0.0 < max(eased) < 6.0


class TestRealizationControlManifest:
    def test_nested_realization_controls_apply(self):
        data = {
            "version": "1.0.0",
            "mastering": {
                "match_reference": {
                    "path": "ref.wav",
                    "smooth_octaves": 0.5,
                    "low_hz": 300.0,
                    "high_hz": 9000.0,
                }
            },
            "assets": [{"input": "a.flac", "output": "a.wav"}],
        }
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.mastering_match_ref_smooth_oct == 0.5
        assert cfg.mastering_match_ref_low_hz == 300.0
        assert cfg.mastering_match_ref_high_hz == 9000.0

    def test_realization_controls_default_to_none(self):
        cfg = UpmixConfig()
        assert cfg.mastering_match_ref_smooth_oct is None
        assert cfg.mastering_match_ref_low_hz is None
        assert cfg.mastering_match_ref_high_hz is None

    @pytest.mark.parametrize("field,value", [
        ("smooth_octaves", 0.02),
        ("smooth_octaves", 2.0),
        ("low_hz", 5.0),
        ("high_hz", 30000.0),
    ])
    def test_out_of_range_realization_controls_are_rejected(self, field, value):
        data = {
            "version": "1.0.0",
            "mastering": {"match_reference": {field: value}},
            "assets": [{"input": "a.flac", "output": "a.wav"}],
        }
        with pytest.raises(ManifestError):
            validate_manifest(data)
