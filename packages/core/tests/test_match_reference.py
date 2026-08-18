"""Tests for upmixer.mastering.match_reference (ReferenceMatchProcessor,
compute_reference_curve, build_curve_fir)."""
from __future__ import annotations

import numpy as np
from scipy.signal import freqz

import upmixer.mastering.match_reference  # noqa: F401 — triggers register_block_keys for mastering.match_reference
from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, SURROUND_51
from upmixer.manifest import _BLOCK_REGISTRY, _FIELD_MAP, apply_asset_job, AssetJob, parse_manifest
from upmixer.mastering.eq import _apply_fir
from upmixer.mastering.match_reference import (
    ReferenceMatchProcessor,
    build_curve_fir,
    compute_reference_curve,
)
from upmixer.mastering.match_reference.curve import (
    _band_edge_taper,
    _confidence_taper,
    _log_grid,
    _smooth_log_grid,
    _soft_clamp,
)
from upmixer.mastering.match_reference.spectrum import (
    _canonicalize_reference,
    _REFERENCE_CHANNEL_ORDER,
    weighted_power_spectrum_reference,
)
from upmixer.utils import itu_downmix_stereo


SR = 44100
N = SR * 2  # 2 s


def _sine(freq: float = 440.0, amplitude: float = 0.2, n: int = N) -> np.ndarray:
    t = np.linspace(0, n / SR, n, endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def _51_channels() -> dict[str, np.ndarray]:
    freqs = {"FL": 440, "FR": 550, "C": 660, "LFE": 60, "SL": 330, "SR": 880}
    return {k: _sine(f) for k, f in freqs.items()}


def _make_proc(**kwargs) -> ReferenceMatchProcessor:
    defaults = dict(
        reference_path="__synthetic__",
        output_fmt=SURROUND_51,
        strength=0.7,
        match_spectrum=True,
        match_rms=True,
        max_correction_db=6.0,
        sample_rate=SR,
    )
    defaults.update(kwargs)
    return ReferenceMatchProcessor(**defaults)


def _inject_ref(proc: ReferenceMatchProcessor, ref_data: np.ndarray) -> None:
    """Bypass file loading by injecting ref_data directly."""
    proc._ref_data = ref_data


def _stereo_ref(n: int = N) -> np.ndarray:
    L = _sine(440.0, amplitude=0.3, n=n)
    R = _sine(550.0, amplitude=0.3, n=n)
    return np.stack([L, R], axis=1)


def _51_ref(n: int = N) -> np.ndarray:
    freqs = [440, 550, 660, 60, 330, 880]
    cols = [_sine(f, amplitude=0.3, n=n) for f in freqs]
    return np.stack(cols, axis=1)


def _fir_response_db(fir: np.ndarray, sr: int, freq_hz: float) -> float:
    w, h = freqz(fir, worN=8192, fs=sr)
    idx = int(np.argmin(np.abs(w - freq_hz)))
    return float(20.0 * np.log10(np.abs(h[idx]) + 1e-12))


class TestReferenceMatchProcessorInit:
    def test_constructs_with_defaults(self):
        proc = _make_proc()
        assert proc._ref_data is None

    def test_strength_clamped_below(self):
        proc = _make_proc(strength=-0.5)
        assert proc._strength == 0.0

    def test_strength_clamped_above(self):
        proc = _make_proc(strength=1.5)
        assert proc._strength == 1.0

    def test_ref_data_not_loaded_on_init(self):
        proc = _make_proc()
        assert proc._ref_data is None


class TestBypass:
    def test_both_disabled_returns_original_dict(self):
        proc = _make_proc(match_spectrum=False, match_rms=False)
        channels = _51_channels()
        result = proc.process(channels)
        assert result is channels

    def test_lfe_gets_level_gain_not_bypassed(self):
        proc = _make_proc(match_rms=True, match_spectrum=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        assert "LFE" in result
        assert result["LFE"] is not channels["LFE"]

    def test_process_skips_fir_when_strength_zero(self):
        proc = _make_proc(strength=0.0, match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        for name in channels:
            np.testing.assert_array_almost_equal(result[name], channels[name])


class TestSmoothing:
    def test_attenuates_narrow_spike(self):
        grid = _log_grid(20000.0)
        values = np.zeros(len(grid))
        spike = len(grid) // 2
        values[spike] = 20.0
        smoothed = _smooth_log_grid(values, 1.0 / 3.0, 1.0 / 24.0)
        assert smoothed[spike] < 5.0, "a one-bin spike must be attenuated, not passed through"

    def test_spreads_energy_over_multiple_bins(self):
        # 1/3 octave at a 1/24-octave grid step is 8 bins of half-width; the
        # pre-fix kernel (its width measured off a mismatched linear FFT
        # grid) collapsed to a ~1-bin-wide identity, so this width is the
        # regression guard.
        grid = _log_grid(20000.0)
        values = np.zeros(len(grid))
        spike = len(grid) // 2
        values[spike] = 20.0
        smoothed = _smooth_log_grid(values, 1.0 / 3.0, 1.0 / 24.0)
        nonzero = np.where(smoothed > 0.05)[0]
        assert (nonzero[-1] - nonzero[0]) >= 8

    def test_constant_input_preserved(self):
        values = np.full(200, 3.5)
        smoothed = _smooth_log_grid(values, 1.0 / 3.0, 1.0 / 24.0)
        np.testing.assert_allclose(smoothed[10:-10], 3.5, atol=1e-6)


class TestSingleSharedCurve:
    def test_identical_inputs_produce_identical_outputs(self):
        proc = _make_proc(match_rms=False, strength=1.0)
        sig = _sine(440.0, amplitude=0.05)
        channels = {
            "FL": sig.copy(), "FR": sig.copy(),
            "C": _sine(660.0), "SL": _sine(330.0), "SR": _sine(880.0), "LFE": _sine(60.0),
        }
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        np.testing.assert_array_equal(result["FL"], result["FR"])

    def test_lr_symmetry_preserved_with_asymmetric_reference(self):
        proc = _make_proc(match_rms=False, strength=1.0)
        sig = _sine(440.0, amplitude=0.05)
        channels = {
            "FL": sig.copy(), "FR": sig.copy(),
            "C": _sine(660.0), "SL": _sine(330.0), "SR": _sine(880.0), "LFE": _sine(60.0),
        }
        # Deliberately asymmetric reference: L and R carry unrelated content.
        ref = np.stack(
            [_sine(200.0, amplitude=0.5, n=N), _sine(4000.0, amplitude=0.01, n=N)], axis=1
        )
        _inject_ref(proc, ref)
        result = proc.process(channels)
        np.testing.assert_array_equal(result["FL"], result["FR"])

    def test_lfe_not_spectrally_corrected(self):
        proc = _make_proc(match_rms=True, match_spectrum=True, strength=1.0)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        _, rms_gain_db = proc.compute_curve(channels)
        result = proc.process(channels)
        rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)
        expected_lfe = channels["LFE"].astype(np.float64) * rms_gain_lin
        np.testing.assert_array_almost_equal(result["LFE"], expected_lfe)

    def test_downmix_commutes_with_matching(self):
        """A single shared FIR is linear, so BS.775 stereo-downmixing the
        matched bed must equal downmixing the original bed and then applying
        the same correction — the property that breaks the moment
        per-channel curves diverge, since each channel would then carry a
        different filter and filtering would stop commuting with summation.
        """
        proc = _make_proc(match_rms=True, strength=1.0)
        channels = _51_channels()
        _inject_ref(proc, _51_ref())
        curve, rms_gain_db = proc.compute_curve(channels)
        rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)
        fir = build_curve_fir(curve, SR, proc._n_taps, 1.0, proc._max_db)

        matched = proc.process(channels)
        matched_l, matched_r = itu_downmix_stereo(matched)

        scaled = {name: ch.astype(np.float64) * rms_gain_lin for name, ch in channels.items()}
        pre_l, pre_r = itu_downmix_stereo(scaled)
        expected_l = _apply_fir(pre_l, fir, 1.0)
        expected_r = _apply_fir(pre_r, fir, 1.0)

        np.testing.assert_array_almost_equal(matched_l, expected_l, decimal=6)
        np.testing.assert_array_almost_equal(matched_r, expected_r, decimal=6)


class TestLevelMatching:
    def test_runs_with_stereo_ref(self):
        proc = _make_proc(match_spectrum=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        for arr in result.values():
            assert np.all(np.isfinite(arr))

    def test_louder_ref_increases_target_level(self):
        proc = _make_proc(match_spectrum=False)
        channels = {"FL": _sine(440.0, amplitude=0.1), "FR": _sine(550.0, amplitude=0.1)}
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        _inject_ref(proc, ref)
        result = proc.process(channels, lfe_key="LFE")
        assert np.sqrt(np.mean(result["FL"] ** 2)) > np.sqrt(np.mean(channels["FL"] ** 2))

    def test_quieter_ref_decreases_target_level(self):
        proc = _make_proc(match_spectrum=False)
        channels = {"FL": _sine(440.0, amplitude=0.4), "FR": _sine(550.0, amplitude=0.4)}
        ref = np.stack([_sine(440.0, amplitude=0.1), _sine(550.0, amplitude=0.1)], axis=1)
        _inject_ref(proc, ref)
        result = proc.process(channels, lfe_key="LFE")
        assert np.sqrt(np.mean(result["FL"] ** 2)) < np.sqrt(np.mean(channels["FL"] ** 2))

    def test_gain_clamped(self):
        proc = _make_proc(match_spectrum=False)
        channels = {"FL": _sine(440.0, amplitude=0.001), "FR": _sine(550.0, amplitude=0.001)}
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        _inject_ref(proc, ref)
        _, gain_db = proc.compute_curve(channels)
        assert gain_db <= 6.0 + 1e-6

    def test_applied_to_lfe(self):
        proc = _make_proc(match_spectrum=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        lfe_before = channels["LFE"].copy()
        result = proc.process(channels)
        assert not np.allclose(result["LFE"], lfe_before)

    def test_inter_channel_balance_preserved(self):
        proc = _make_proc(match_spectrum=False)
        channels = {"FL": _sine(440.0, amplitude=0.15), "FR": _sine(550.0, amplitude=0.30)}
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        _inject_ref(proc, ref)
        result = proc.process(channels, lfe_key="LFE")
        ratio_before = np.sqrt(np.mean(channels["FL"] ** 2)) / np.sqrt(np.mean(channels["FR"] ** 2))
        ratio_after = np.sqrt(np.mean(result["FL"] ** 2)) / np.sqrt(np.mean(result["FR"] ** 2))
        assert abs(ratio_before - ratio_after) < 1e-6


class TestComputeCurve:
    """compute_curve must return exactly what process() builds and applies
    internally — the web preview's server-side precompute persists the curve
    as-is instead of re-deriving the algorithm in JS."""
    def test_matches_what_process_applies(self):
        proc = _make_proc(match_rms=True, match_spectrum=True, strength=1.0)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        curve, rms_gain_db = proc.compute_curve(channels)
        fir = build_curve_fir(curve, SR, proc._n_taps, proc._strength, proc._max_db)
        rms_gain_lin = 10.0 ** (rms_gain_db / 20.0)
        expected = {}
        for name, ch in channels.items():
            scaled = ch.astype(np.float64) * rms_gain_lin
            expected[name] = scaled if name == "LFE" else _apply_fir(scaled, fir, 1.0)

        proc2 = _make_proc(match_rms=True, match_spectrum=True, strength=1.0)
        _inject_ref(proc2, _stereo_ref())
        actual = proc2.process(channels)
        for name in channels:
            np.testing.assert_array_almost_equal(actual[name], expected[name])

    def test_curve_is_strength_independent(self):
        channels = _51_channels()
        proc_full = _make_proc(strength=1.0)
        _inject_ref(proc_full, _stereo_ref())
        curve_full, _ = proc_full.compute_curve(channels)
        proc_zero = _make_proc(strength=0.0)
        _inject_ref(proc_zero, _stereo_ref())
        curve_zero, _ = proc_zero.compute_curve(channels)
        assert curve_full == curve_zero

    def test_empty_curve_when_spectrum_disabled(self):
        proc = _make_proc(match_spectrum=False, match_rms=True)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        curve, _ = proc.compute_curve(channels)
        assert curve == []

    def test_does_not_mutate_input_channels(self):
        proc = _make_proc(match_rms=True, match_spectrum=True, strength=1.0)
        channels = _51_channels()
        before = {name: arr.copy() for name, arr in channels.items()}
        _inject_ref(proc, _stereo_ref())
        proc.compute_curve(channels)
        for name, arr in channels.items():
            np.testing.assert_array_equal(arr, before[name])


class TestReferenceChannelCanonicalization:
    def test_supported_counts(self):
        assert set(_REFERENCE_CHANNEL_ORDER.keys()) == {6, 8, 10, 12}

    def test_51_order_matches_wav_convention(self):
        assert _REFERENCE_CHANNEL_ORDER[6] == (
            ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.LFE,
            ChannelLabel.SL, ChannelLabel.SR,
        )

    def test_unsupported_count_falls_back_to_nearest(self):
        ref = np.zeros((100, 4))
        data, order = _canonicalize_reference(ref)
        assert len(order) == 2  # nearest supported count to 4 is 2 (stereo)
        assert data.shape[1] == 2

    def test_12ch_reference_keeps_height_channels(self):
        # Regression: the old proxy-table selection clamped any >8ch
        # reference to the 8ch table, silently discarding height channels.
        ref = np.zeros((100, 12))
        data, order = _canonicalize_reference(ref)
        assert ChannelLabel.TBL in order and ChannelLabel.TBR in order
        assert data.shape[1] == 12


class TestComputeReferenceCurve:
    def test_breakpoints_count(self):
        target = {"FL": _sine(440.0), "FR": _sine(550.0)}
        curve = compute_reference_curve(target, _stereo_ref(), SR, 8192)
        assert len(curve) == 64

    def test_breakpoints_ascending_freq(self):
        target = {"FL": _sine(440.0), "FR": _sine(550.0)}
        curve = compute_reference_curve(target, _stereo_ref(), SR, 8192)
        freqs = [f for f, _ in curve]
        assert freqs == sorted(freqs)

    def test_gains_finite(self):
        target = {"FL": _sine(440.0), "FR": _sine(550.0)}
        curve = compute_reference_curve(target, _stereo_ref(), SR, 8192)
        assert all(np.isfinite(g) for _, g in curve)


class TestGating:
    def test_ignores_silent_lead_in(self):
        # Measures the gated power spectrum directly (rather than the
        # derived correction curve) at the two frequencies that actually
        # carry signal: the curve's confidence taper and normalization mean
        # amplify tiny near-noise-floor differences well away from real
        # content, which would make this assertion about *gating* fail for
        # reasons unrelated to gating (frame-boundary spectral leakage at
        # the hard digital silence-to-tone edge this test constructs, not
        # present in a real fade-in).
        ref_tail = _stereo_ref(n=N)
        ref_padded = np.concatenate([np.zeros((SR * 10, 2)), ref_tail], axis=0)
        freqs1, power1 = weighted_power_spectrum_reference(ref_tail, SR, 8192)
        _, power2 = weighted_power_spectrum_reference(ref_padded, SR, 8192)
        db1 = 10.0 * np.log10(power1 + 1e-20)
        db2 = 10.0 * np.log10(power2 + 1e-20)
        for target_hz in (440.0, 550.0):
            idx = int(np.argmin(np.abs(freqs1 - target_hz)))
            assert abs(db1[idx] - db2[idx]) < 1.0


class TestSoftClamp:
    def test_within_knee_unaffected(self):
        db = np.array([1.0, -1.0, 3.5])
        out = _soft_clamp(db, 6.0, knee_db=2.0)
        np.testing.assert_allclose(out, db, atol=1e-6)

    def test_asymptotes_toward_limit(self):
        out = _soft_clamp(np.array([100.0]), 6.0, knee_db=2.0)
        assert 5.9 < out[0] <= 6.0

    def test_symmetric_for_negative(self):
        pos = _soft_clamp(np.array([50.0]), 6.0)
        neg = _soft_clamp(np.array([-50.0]), 6.0)
        assert np.isclose(pos[0], -neg[0])


class TestBandEdgeTaper:
    def test_tapers_to_zero_at_20khz(self):
        grid = _log_grid(20000.0)
        flat = np.full(len(grid), 5.0)
        tapered = _band_edge_taper(flat, grid)
        assert abs(tapered[-1]) < 0.5

    def test_flat_in_middle_band(self):
        grid = _log_grid(20000.0)
        flat = np.full(len(grid), 5.0)
        tapered = _band_edge_taper(flat, grid)
        mid_mask = (grid > 200) & (grid < 10000)
        np.testing.assert_allclose(tapered[mid_mask], 5.0)


class TestConfidenceTaper:
    def test_fades_where_reference_has_no_energy(self):
        n = 100
        correction = np.full(n, 10.0)
        ref_power_db = np.full(n, -20.0)
        ref_power_db[-10:] = -120.0  # brickwalled tail, far below peak
        out = _confidence_taper(correction, ref_power_db)
        assert abs(out[-1]) < 1.0
        assert abs(out[0] - 10.0) < 1e-6


class TestBuildCurveFir:
    def test_bass_clamped_regardless_of_max_db(self):
        curve = [(f, 20.0) for f in np.logspace(np.log10(20), np.log10(20000), 64)]
        fir = build_curve_fir(curve, SR, 1023, 1.0, 24.0)
        assert _fir_response_db(fir, SR, 60.0) <= 2.0 + 1.0

    def test_max_correction_soft_clamped(self):
        curve = [(f, 20.0) for f in np.logspace(np.log10(20), np.log10(20000), 64)]
        fir = build_curve_fir(curve, SR, 1023, 1.0, 3.0)
        assert _fir_response_db(fir, SR, 1000.0) <= 3.0 + 1.0

    def test_strength_scales_curve_in_db(self):
        curve = [(f, 4.0) for f in np.logspace(np.log10(20), np.log10(20000), 64)]
        fir_full = build_curve_fir(curve, SR, 1023, 1.0, 24.0)
        fir_half = build_curve_fir(curve, SR, 1023, 0.5, 24.0)
        db_full = _fir_response_db(fir_full, SR, 1000.0)
        db_half = _fir_response_db(fir_half, SR, 1000.0)
        assert abs(db_half - db_full / 2.0) < 0.5

    def test_apply_fir_full_wet_preserves_energy_for_flat_curve(self):
        # strength is folded into the curve before FIR design; _apply_fir is
        # always called at full wet (1.0) by the processor — this is the fix
        # for the old comb-filter defect where an undelayed dry path was
        # crossfaded against a minimum-phase-delayed wet path at partial
        # strength.
        curve = [(f, 0.0) for f in np.logspace(np.log10(20), np.log10(20000), 64)]
        fir = build_curve_fir(curve, SR, 1023, 1.0, 6.0)
        sig = _sine(440.0)
        out = _apply_fir(sig, fir, 1.0)
        assert abs(np.sqrt(np.mean(out ** 2)) - np.sqrt(np.mean(sig ** 2))) < 0.05


class TestConfigFields:
    def test_default_reference_is_none(self):
        assert UpmixConfig().mastering_match_ref_path is None

    def test_default_strength(self):
        assert UpmixConfig().mastering_match_ref_strength == 0.7

    def test_default_match_spectrum(self):
        assert UpmixConfig().mastering_match_ref_spectrum is True

    def test_default_match_rms(self):
        assert UpmixConfig().mastering_match_ref_rms is True

    def test_default_max_db(self):
        assert UpmixConfig().mastering_match_ref_max_db == 6.0

    def test_old_eq_reference_field_gone(self):
        assert not hasattr(UpmixConfig(), "mastering_eq_reference")

    def test_old_eq_match_strength_gone(self):
        assert not hasattr(UpmixConfig(), "mastering_eq_match_strength")


class TestManifestMatchReferenceIntegration:
    def test_match_reference_in_registry(self):
        assert "match_reference" in _BLOCK_REGISTRY.get("mastering", {})

    def test_flat_key_path_applies(self):
        job = AssetJob(input="x", output="y",
                       config={"mastering_match_ref_path": "ref.wav"})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.mastering_match_ref_path == "ref.wav"

    def test_flat_key_strength_applies(self):
        job = AssetJob(input="x", output="y",
                       config={"mastering_match_ref_strength": 0.5})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.mastering_match_ref_strength == 0.5

    def test_flat_key_match_spectrum_applies(self):
        job = AssetJob(input="x", output="y",
                       config={"mastering_match_ref_spectrum": False})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.mastering_match_ref_spectrum is False

    def test_flat_key_match_rms_applies(self):
        job = AssetJob(input="x", output="y",
                       config={"mastering_match_ref_rms": False})
        cfg = UpmixConfig()
        apply_asset_job(cfg, job)
        assert cfg.mastering_match_ref_rms is False

    def test_nested_match_reference_section(self):
        data = {
            "version": "1.0.0",
            "mastering": {
                "match_reference": {
                    "path": "ref.wav",
                    "strength": 0.5,
                    "spectrum": True,
                    "rms": False,
                    "max_db": 8.0,
                }
            },
            "assets": [{"input": "a.flac", "output": "a.wav"}],
        }
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig()
        apply_asset_job(cfg, jobs[0])
        assert cfg.mastering_match_ref_path == "ref.wav"
        assert cfg.mastering_match_ref_strength == 0.5
        assert cfg.mastering_match_ref_spectrum is True
        assert cfg.mastering_match_ref_rms is False
        assert cfg.mastering_match_ref_max_db == 8.0

    def test_field_map_has_match_ref_entries(self):
        assert "mastering_match_ref_path" in _FIELD_MAP
        assert "mastering_match_ref_strength" in _FIELD_MAP
        assert "mastering_match_ref_spectrum" in _FIELD_MAP
        assert "mastering_match_ref_rms" in _FIELD_MAP
        assert "mastering_match_ref_max_db" in _FIELD_MAP

    def test_old_eq_reference_removed_from_field_map(self):
        assert "mastering_eq_reference" not in _FIELD_MAP
        assert "mastering_eq_match_strength" not in _FIELD_MAP
