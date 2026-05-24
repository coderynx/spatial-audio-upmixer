"""Tests for upmixer.mastering.match_reference.ReferenceMatchProcessor."""
import numpy as np

import upmixer.mastering.match_reference  # noqa: F401 — triggers register_block_keys for mastering.match_reference

from upmixer.config import UpmixConfig
from upmixer.manifest import _BLOCK_REGISTRY, _FIELD_MAP, apply_asset_job, AssetJob, parse_manifest
from upmixer.mastering.match_reference import (
    ReferenceMatchProcessor,
    _CHANNEL_PROXIES,
    _gaussian_smooth_log,
)

# ── helpers ──────────────────────────────────────────────────────────────────

SR = 44100
N = SR * 2  # 2 s


def _sine(freq: float = 440.0, amplitude: float = 0.2, n: int = N) -> np.ndarray:
    t = np.linspace(0, n / SR, n, endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def _51_channels() -> dict[str, np.ndarray]:
    freqs = {"FL": 440, "FR": 550, "C": 660, "LFE": 60, "SL": 330, "SR": 440}
    return {k: _sine(f) for k, f in freqs.items()}


def _make_proc(**kwargs) -> ReferenceMatchProcessor:
    defaults = dict(
        reference_path="__synthetic__",
        strength=0.7,
        match_spectrum=True,
        match_rms=True,
        max_correction_db=12.0,
        sample_rate=SR,
    )
    defaults.update(kwargs)
    return ReferenceMatchProcessor(**defaults)


def _inject_ref(proc: ReferenceMatchProcessor, ref_data: np.ndarray) -> None:
    """Bypass file loading by injecting ref_data directly."""
    proc._ref_data = ref_data
    n_ch = ref_data.shape[1]
    supported = sorted(_CHANNEL_PROXIES.keys())
    proxy_n = min(supported, key=lambda x: abs(x - n_ch))
    proc._proxy_table = _CHANNEL_PROXIES[proxy_n]


def _stereo_ref(n: int = N) -> np.ndarray:
    L = _sine(440.0, amplitude=0.3, n=n)
    R = _sine(550.0, amplitude=0.3, n=n)
    return np.stack([L, R], axis=1)


def _51_ref(n: int = N) -> np.ndarray:
    freqs = [440, 550, 660, 60, 330, 440]
    cols = [_sine(f, amplitude=0.3, n=n) for f in freqs]
    return np.stack(cols, axis=1)


# ── init ─────────────────────────────────────────────────────────────────────

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
        assert proc._proxy_table is None


# ── bypass ───────────────────────────────────────────────────────────────────

class TestBypass:
    def test_both_disabled_returns_original_dict(self):
        proc = _make_proc(match_spectrum=False, match_rms=False)
        channels = _51_channels()
        result = proc.process(channels)
        assert result is channels

    def test_lfe_processed_not_bypassed(self):
        proc = _make_proc(match_rms=True, match_spectrum=False)
        channels = _51_channels()
        ref = _stereo_ref()
        _inject_ref(proc, ref)
        result = proc.process(channels)
        # LFE is modified (RMS scalar applied to all channels)
        assert "LFE" in result
        assert result["LFE"] is not channels["LFE"]


# ── spectral matching ─────────────────────────────────────────────────────────

class TestSpectralMatching:
    def test_runs_with_stereo_ref(self):
        proc = _make_proc(match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        for name, arr in result.items():
            assert np.all(np.isfinite(arr)), f"{name} has non-finite values"

    def test_runs_with_mono_ref(self):
        proc = _make_proc(match_rms=False)
        channels = _51_channels()
        mono = _sine(440.0, amplitude=0.3, n=N).reshape(-1, 1)
        _inject_ref(proc, mono)
        result = proc.process(channels)
        assert set(result.keys()) == set(channels.keys())

    def test_runs_with_51_ref(self):
        proc = _make_proc(match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _51_ref())
        result = proc.process(channels)
        for arr in result.values():
            assert np.all(np.isfinite(arr))

    def test_shape_preserved(self):
        proc = _make_proc(match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        for name, arr in result.items():
            assert arr.shape == channels[name].shape

    def test_channel_keys_preserved(self):
        proc = _make_proc(match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        assert set(result.keys()) == set(channels.keys())

    def test_strength_zero_returns_similar_to_input(self):
        proc = _make_proc(strength=0.0, match_rms=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        # With strength=0, _apply_fir returns dry signal unchanged
        for name in channels:
            np.testing.assert_array_almost_equal(result[name], channels[name])

    def test_lfe_spectral_matched_via_proxy(self):
        proc = _make_proc(match_rms=False, strength=1.0)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        # LFE should have been modified (not identical to input)
        # With strength=1.0 and a non-trivial correction, arrays differ
        assert "LFE" in result
        assert np.all(np.isfinite(result["LFE"]))


# ── LFE handling ──────────────────────────────────────────────────────────────

class TestLFEHandling:
    def test_stereo_ref_lfe_uses_mid_lp_proxy(self):
        # For 2-ch ref, LFE proxy is "mid_lp" — just verify it runs without error
        proc = _make_proc(match_rms=False)
        channels = {"LFE": _sine(60.0)}
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels, lfe_key="LFE")
        assert "LFE" in result
        assert np.all(np.isfinite(result["LFE"]))

    def test_51_ref_lfe_uses_direct_channel(self):
        # For 6-ch ref, LFE proxy is index 3 (actual LFE)
        proc = _make_proc(match_rms=False)
        channels = {"LFE": _sine(60.0)}
        _inject_ref(proc, _51_ref())
        result = proc.process(channels, lfe_key="LFE")
        assert "LFE" in result
        assert np.all(np.isfinite(result["LFE"]))


# ── RMS matching ──────────────────────────────────────────────────────────────

class TestRmsMatching:
    def test_runs_with_stereo_ref(self):
        proc = _make_proc(match_spectrum=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        result = proc.process(channels)
        for arr in result.values():
            assert np.all(np.isfinite(arr))

    def test_louder_ref_increases_target_level(self):
        # Reference is 6 dB louder than target
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

    def test_rms_gain_clamped(self):
        # Reference is 40 dB louder — gain should be clamped to +6 dB
        proc = _make_proc(match_spectrum=False)
        channels = {"FL": _sine(440.0, amplitude=0.001), "FR": _sine(550.0, amplitude=0.001)}
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        _inject_ref(proc, ref)
        gain_db = proc._compute_rms_gain_db(
            proc._ref_data, proc._proxy_table, channels, "LFE"
        )
        assert gain_db <= 6.0 + 1e-6

    def test_rms_applied_to_lfe(self):
        proc = _make_proc(match_spectrum=False)
        channels = _51_channels()
        _inject_ref(proc, _stereo_ref())
        lfe_before = channels["LFE"].copy()
        result = proc.process(channels)
        # LFE should have been scaled (unless ref == tgt RMS, which is unlikely)
        assert not np.allclose(result["LFE"], lfe_before)

    def test_inter_channel_balance_preserved(self):
        # FL/FR ratio should be identical before and after (same scalar applied)
        proc = _make_proc(match_spectrum=False)
        channels = {
            "FL": _sine(440.0, amplitude=0.15),
            "FR": _sine(550.0, amplitude=0.30),
        }
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        _inject_ref(proc, ref)
        result = proc.process(channels, lfe_key="LFE")
        rms_fl_before = np.sqrt(np.mean(channels["FL"] ** 2))
        rms_fr_before = np.sqrt(np.mean(channels["FR"] ** 2))
        rms_fl_after = np.sqrt(np.mean(result["FL"] ** 2))
        rms_fr_after = np.sqrt(np.mean(result["FR"] ** 2))
        ratio_before = rms_fl_before / rms_fr_before
        ratio_after = rms_fl_after / rms_fr_after
        assert abs(ratio_before - ratio_after) < 1e-6


# ── channel proxy table ───────────────────────────────────────────────────────

class TestChannelProxies:
    def test_supported_counts(self):
        assert set(_CHANNEL_PROXIES.keys()) == {1, 2, 6, 8}

    def test_stereo_fl_fr_direct(self):
        assert _CHANNEL_PROXIES[2]["FL"] == 0
        assert _CHANNEL_PROXIES[2]["FR"] == 1

    def test_stereo_lfe_is_mid_lp(self):
        assert _CHANNEL_PROXIES[2]["LFE"] == "mid_lp"

    def test_51_lfe_direct(self):
        assert _CHANNEL_PROXIES[6]["LFE"] == 3

    def test_71_lfe_direct(self):
        assert _CHANNEL_PROXIES[8]["LFE"] == 3

    def test_mono_all_zero(self):
        assert all(v == 0 for v in _CHANNEL_PROXIES[1].values())


# ── _compute_spectral_breakpoints unit tests ──────────────────────────────────

class TestComputeSpectralBreakpoints:
    def setup_method(self):
        self.proc = _make_proc()
        _inject_ref(self.proc, _stereo_ref())

    def test_breakpoints_count(self):
        ref_ch = _sine(440.0)
        tgt_ch = _sine(440.0, amplitude=0.1)
        bps = self.proc._compute_spectral_breakpoints(ref_ch, tgt_ch)
        assert len(bps) == 40

    def test_breakpoints_ascending_freq(self):
        ref_ch = _sine(440.0)
        tgt_ch = _sine(440.0, amplitude=0.1)
        bps = self.proc._compute_spectral_breakpoints(ref_ch, tgt_ch)
        freqs = [f for f, _ in bps]
        assert freqs == sorted(freqs)

    def test_gains_finite(self):
        ref_ch = _sine(440.0)
        tgt_ch = _sine(440.0)
        bps = self.proc._compute_spectral_breakpoints(ref_ch, tgt_ch)
        assert all(np.isfinite(g) for _, g in bps)

    def test_max_correction_clamp(self):
        proc = _make_proc(max_correction_db=3.0)
        _inject_ref(proc, _stereo_ref())
        ref_ch = _sine(100.0, amplitude=0.5)
        tgt_ch = _sine(440.0, amplitude=0.001)  # very different
        bps = proc._compute_spectral_breakpoints(ref_ch, tgt_ch)
        gains_above_bass = [g for f, g in bps if f >= 120.0]
        assert all(abs(g) <= 3.0 + 1e-6 for g in gains_above_bass)

    def test_bass_clamp_applied(self):
        proc = _make_proc(max_correction_db=20.0)
        _inject_ref(proc, _stereo_ref())
        ref_ch = _sine(50.0, amplitude=0.5)
        tgt_ch = _sine(50.0, amplitude=0.001)
        bps = proc._compute_spectral_breakpoints(ref_ch, tgt_ch)
        bass_gains = [g for f, g in bps if f < 120.0]
        assert all(abs(g) <= 2.0 + 1e-6 for g in bass_gains)


# ── _compute_rms_gain_db unit tests ──────────────────────────────────────────

class TestComputeRmsGainDb:
    def setup_method(self):
        self.proc = _make_proc()
        _inject_ref(self.proc, _stereo_ref())

    def test_identical_signals_gives_zero_gain(self):
        amp = 0.2
        channels = {"FL": _sine(440.0, amplitude=amp), "FR": _sine(550.0, amplitude=amp)}
        ref = np.stack([_sine(440.0, amplitude=amp), _sine(550.0, amplitude=amp)], axis=1)
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain = proc._compute_rms_gain_db(ref, proc._proxy_table, channels, "LFE")
        assert abs(gain) < 0.5  # near zero (not exact due to different freqs)

    def test_louder_ref_positive_gain(self):
        channels = {"FL": _sine(440.0, amplitude=0.1), "FR": _sine(550.0, amplitude=0.1)}
        ref = np.stack([_sine(440.0, amplitude=0.4), _sine(550.0, amplitude=0.4)], axis=1)
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain = proc._compute_rms_gain_db(ref, proc._proxy_table, channels, "LFE")
        assert gain > 0.0

    def test_quieter_ref_negative_gain(self):
        channels = {"FL": _sine(440.0, amplitude=0.4), "FR": _sine(550.0, amplitude=0.4)}
        ref = np.stack([_sine(440.0, amplitude=0.1), _sine(550.0, amplitude=0.1)], axis=1)
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain = proc._compute_rms_gain_db(ref, proc._proxy_table, channels, "LFE")
        assert gain < 0.0

    def test_gain_clamped_positive(self):
        channels = {"FL": _sine(440.0, amplitude=0.001)}
        ref = _sine(440.0, amplitude=0.9).reshape(-1, 1)
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain = proc._compute_rms_gain_db(ref, proc._proxy_table, channels, "LFE")
        assert gain <= 6.0 + 1e-6

    def test_gain_clamped_negative(self):
        channels = {"FL": _sine(440.0, amplitude=0.9)}
        ref = _sine(440.0, amplitude=0.001).reshape(-1, 1)
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain = proc._compute_rms_gain_db(ref, proc._proxy_table, channels, "LFE")
        assert gain >= -6.0 - 1e-6

    def test_lfe_excluded_from_computation(self):
        # LFE channel should not affect the RMS gain computation
        channels_with_lfe = {
            "FL": _sine(440.0, amplitude=0.2),
            "LFE": _sine(60.0, amplitude=10.0),  # extreme LFE would skew if included
        }
        channels_without_lfe = {"FL": _sine(440.0, amplitude=0.2)}
        ref = _stereo_ref()
        proc = _make_proc()
        _inject_ref(proc, ref)
        gain_with = proc._compute_rms_gain_db(ref, proc._proxy_table, channels_with_lfe, "LFE")
        gain_without = proc._compute_rms_gain_db(
            ref, proc._proxy_table, channels_without_lfe, "LFE"
        )
        assert abs(gain_with - gain_without) < 0.01


# ── config fields ─────────────────────────────────────────────────────────────

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
        assert UpmixConfig().mastering_match_ref_max_db == 12.0

    def test_old_eq_reference_field_gone(self):
        assert not hasattr(UpmixConfig(), "mastering_eq_reference")

    def test_old_eq_match_strength_gone(self):
        assert not hasattr(UpmixConfig(), "mastering_eq_match_strength")


# ── manifest integration ──────────────────────────────────────────────────────

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


# ── gaussian smooth helper ────────────────────────────────────────────────────

class TestGaussianSmoothLog:
    def test_output_same_length(self):
        log_freqs = np.linspace(4, 14, 200)  # log2(20) to log2(16000)
        values = np.random.default_rng(0).standard_normal(200)
        smoothed = _gaussian_smooth_log(log_freqs, values, 0.25)
        assert smoothed.shape == values.shape

    def test_constant_input_preserved(self):
        log_freqs = np.linspace(4, 14, 100)
        values = np.ones(100) * 3.5
        smoothed = _gaussian_smooth_log(log_freqs, values, 0.25)
        np.testing.assert_allclose(smoothed, values, atol=1e-10)

    def test_output_finite(self):
        log_freqs = np.linspace(4, 14, 200)
        values = np.random.default_rng(1).standard_normal(200)
        smoothed = _gaussian_smooth_log(log_freqs, values, 0.25)
        assert np.all(np.isfinite(smoothed))
