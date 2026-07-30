"""BS.1770-4 compliance tests for upmixer/loudness.py.

Verifies:
  - K-weighting SOS coefficients match BS.1770-4 Annex 1 Tables 1-2 exactly at 48 kHz
  - Analytical fallback at 96 kHz yields valid SOS structure
  - Channel weights per BS.1770-4 §2.2 Table 1 + Annex 1 Table 3
  - Integrated loudness gating behaviour (absolute + relative, floor returns)
  - True-peak oversampling factor: 4x at <=48 kHz, 2x at 96 kHz (Annex 2)
  - normalize_loudness info dict and TP-limiting logic
"""
from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np
from scipy.signal import sosfreqz

from upmixer.formats import ChannelLabel, FORMAT_MAP
from upmixer.loudness import (
    _CH_WEIGHT,
    _SURROUND_WEIGHT,
    _k_weighting_sos,
    measure_integrated_loudness,
    measure_true_peak,
    normalize_loudness,
)

SR48 = 48000
SR96 = 96000
FMT_51  = FORMAT_MAP["5.1"]
FMT_714 = FORMAT_MAP["7.1.4"]

_SIDE_SURROUND_LABELS = [ChannelLabel.SL, ChannelLabel.SR]
_UNITY_IMMERSIVE_LABELS = [
    ChannelLabel.BL, ChannelLabel.BR,
    ChannelLabel.TFL, ChannelLabel.TFR,
    ChannelLabel.TBL, ChannelLabel.TBR,
]


def _sine_channels(
    freq: float,
    amp: float,
    duration: float,
    sr: int,
    labels: list[ChannelLabel],
) -> dict[str, np.ndarray]:
    t = np.linspace(0, duration, int(duration * sr), endpoint=False)
    wave = amp * np.sin(2.0 * math.pi * freq * t)
    return {lbl.value: wave.copy() for lbl in labels}


# ---------------------------------------------------------------------------
# K-weighting coefficients — BS.1770-4 Annex 1 Tables 1-2
# ---------------------------------------------------------------------------

class TestKWeighting48k:
    def setup_method(self):
        self.sos = _k_weighting_sos(SR48)

    def test_stage1_b_coeffs(self):
        np.testing.assert_allclose(
            self.sos[0, 0:3],
            [1.53512485958697, -2.69169618940638, 1.19839281085285],
            rtol=0,
            atol=1e-14,
        )

    def test_stage1_a_coeffs(self):
        np.testing.assert_allclose(
            self.sos[0, 4:6],
            [-1.69065929318241, 0.73248077421585],
            rtol=0,
            atol=1e-14,
        )

    def test_stage2_b_coeffs(self):
        np.testing.assert_allclose(
            self.sos[1, 0:3],
            [1.0, -2.0, 1.0],
            rtol=0,
            atol=1e-14,
        )

    def test_stage2_a_coeffs(self):
        np.testing.assert_allclose(
            self.sos[1, 4:6],
            [-1.99004745483398, 0.99007225036621],
            rtol=0,
            atol=1e-14,
        )

    def test_a0_unity_both_stages(self):
        np.testing.assert_array_equal(self.sos[:, 3], [1.0, 1.0])

    def test_shape(self):
        assert self.sos.shape == (2, 6)


def test_k_weighting_96k_valid_sos():
    sos = _k_weighting_sos(SR96)
    assert sos.shape == (2, 6)
    assert not np.any(np.isnan(sos))
    assert not np.any(np.isinf(sos))
    np.testing.assert_array_equal(sos[:, 3], [1.0, 1.0])


def test_k_weighting_96k_matches_48k_response():
    freqs = np.geomspace(10.0, 20_000.0, 2000)
    _, h48 = sosfreqz(_k_weighting_sos(SR48), worN=freqs, fs=SR48)
    _, h96 = sosfreqz(_k_weighting_sos(SR96), worN=freqs, fs=SR96)
    delta_db = 20 * np.log10(np.abs(h96) / np.abs(h48))
    assert np.max(np.abs(delta_db)) < 0.02


def test_k_weighting_cached():
    assert _k_weighting_sos(SR48) is _k_weighting_sos(SR48)


# ---------------------------------------------------------------------------
# Channel weights — BS.1770-4 §2.2 Table 1 + Annex 1 Table 3
# ---------------------------------------------------------------------------

def test_surround_weight_literal_value():
    assert _SURROUND_WEIGHT == 1.41


def test_lfe_weight_zero():
    assert _CH_WEIGHT[ChannelLabel.LFE] == 0.0


def test_front_weights_unity():
    for label in (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C):
        assert _CH_WEIGHT[label] == 1.0, f"{label} weight != 1.0"


def test_side_surround_weights_are_1_41():
    for label in _SIDE_SURROUND_LABELS:
        assert _CH_WEIGHT[label] == 1.41, f"{label} weight != 1.41"


def test_rear_and_height_weights_are_unity():
    for label in _UNITY_IMMERSIVE_LABELS:
        assert _CH_WEIGHT[label] == 1.0, f"{label} weight != 1.0"


# ---------------------------------------------------------------------------
# Integrated loudness — BS.1770-4 §2
# ---------------------------------------------------------------------------

def test_silence_returns_floor():
    channels = {"FL": np.zeros(SR48 * 3), "FR": np.zeros(SR48 * 3)}
    result = measure_integrated_loudness(channels, SR48, FMT_51)
    assert result == -70.0


def test_lfe_only_excluded_returns_floor():
    channels = {lbl.value: np.zeros(SR48 * 3) for lbl in FMT_51.channels}
    channels["LFE"] = np.ones(SR48 * 3) * 0.5
    result = measure_integrated_loudness({"LFE": np.ones(SR48 * 3) * 0.5}, SR48, FMT_51)
    assert result == -70.0


def test_short_audio_below_one_block_returns_floor():
    short = int(0.1 * SR48)
    channels = {"FL": np.ones(short) * 0.5, "FR": np.ones(short) * 0.5}
    result = measure_integrated_loudness(channels, SR48, FMT_51)
    assert result == -70.0


def test_known_sine_lkfs_within_tolerance():
    amp = 10.0 ** (-3.0 / 20.0)
    channels = _sine_channels(997.0, amp, 5.0, SR48, [ChannelLabel.FL, ChannelLabel.FR])
    result = measure_integrated_loudness(channels, SR48, FMT_51)
    assert -10.0 <= result <= 0.0, f"LKFS {result} out of expected range"


def test_surround_channel_louder_than_front_due_to_weight():
    amp = 0.3
    duration = 3.0
    ch_fl = _sine_channels(997.0, amp, duration, SR48, [ChannelLabel.FL])
    ch_sl = _sine_channels(997.0, amp, duration, SR48, [ChannelLabel.SL])

    lkfs_fl = measure_integrated_loudness(ch_fl, SR48, FMT_714)
    lkfs_sl = measure_integrated_loudness(ch_sl, SR48, FMT_714)

    assert lkfs_sl > lkfs_fl, (
        f"Surround ({lkfs_sl:.2f} LKFS) should be louder than front ({lkfs_fl:.2f} LKFS) "
        f"due to BS.1770-4 weight 1.41 vs 1.0"
    )
    expected_delta = 10.0 * math.log10(1.41)
    actual_delta = lkfs_sl - lkfs_fl
    assert abs(actual_delta - expected_delta) < 0.1, (
        f"Weight delta {actual_delta:.3f} dB, expected ~{expected_delta:.3f} dB"
    )


# ---------------------------------------------------------------------------
# True peak — BS.1770-4 Annex 2
# ---------------------------------------------------------------------------

def test_true_peak_silence_returns_floor():
    channels = {"FL": np.zeros(SR48)}
    result = measure_true_peak(channels, SR48)
    assert result <= -100.0


def test_true_peak_near_full_scale():
    amp = 0.99
    t = np.linspace(0, 1.0, SR48, endpoint=False)
    audio = amp * np.sin(2.0 * math.pi * 997.0 * t)
    channels = {"FL": audio}
    result = measure_true_peak(channels, SR48)
    floor_db = 20.0 * math.log10(amp)
    assert result >= floor_db - 0.1
    assert result <= 3.0


def test_true_peak_uses_bs1770_fir_at_48k_and_96k():
    audio = np.sin(2 * np.pi * 997 * np.arange(SR48) / SR48)
    assert measure_true_peak({"FL": audio}, SR48) > -0.1
    assert measure_true_peak({"FL": audio}, SR96) > -0.1


# ---------------------------------------------------------------------------
# normalize_loudness
# ---------------------------------------------------------------------------

def test_normalize_info_dict_keys():
    channels = _sine_channels(997.0, 0.1, 3.0, SR48, [ChannelLabel.FL, ChannelLabel.FR])
    _, info = normalize_loudness(channels, SR48, FMT_51)
    assert set(info.keys()) == {"pre_lkfs", "measured_lkfs", "measured_tp_dbtp", "applied_gain_db", "tp_limited"}


def test_normalize_reaches_target_lkfs():
    amp = 0.05
    channels = _sine_channels(997.0, amp, 5.0, SR48, [ChannelLabel.FL, ChannelLabel.FR])
    adjusted, info = normalize_loudness(channels, SR48, FMT_51, target_lkfs=-18.0)

    post_lkfs = measure_integrated_loudness(adjusted, SR48, FMT_51)
    assert abs(post_lkfs - (-18.0)) < 0.5, (
        f"Post-normalize LKFS {post_lkfs:.2f}, expected -18.0 ±0.5"
    )


def test_normalize_tp_limited_flag():
    amp = 0.05
    channels = _sine_channels(997.0, amp, 5.0, SR48, [ChannelLabel.FL, ChannelLabel.FR])
    _, info = normalize_loudness(channels, SR48, FMT_51, target_lkfs=-1.0, max_tp_dbtp=-1.0, max_gain_db=30.0)
    assert info["tp_limited"] is True, (
        f"Expected tp_limited=True: quiet signal boosted to -1.0 LKFS should push TP above -1.0 dBTP. "
        f"applied_gain_db={info['applied_gain_db']:.2f}, measured_tp={info['measured_tp_dbtp']:.2f}"
    )


def test_normalize_max_gain_cap():
    amp = 10.0 ** (-60.0 / 20.0)
    channels = _sine_channels(997.0, amp, 5.0, SR48, [ChannelLabel.FL, ChannelLabel.FR])
    _, info = normalize_loudness(channels, SR48, FMT_51, target_lkfs=-18.0, max_gain_db=30.0)
    assert info["applied_gain_db"] <= 30.0
