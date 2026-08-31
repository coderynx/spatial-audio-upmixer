import math

import numpy as np
import pytest

from upmixer.binaural.ambisonics import N_ACN_CHANNELS, encode_gains
from upmixer.binaural.decoder import decode_to_binaural, load_decode_filter_set
from upmixer.binaural.geometry import speaker_azimuth_elevation
from upmixer.binaural.head_model import synth_hrir
from upmixer.binaural.profiles import BINAURAL_PROFILES, VOICING_PARAMS, BinauralProfile, resolve_profile
from upmixer.binaural.renderer import (
    BINAURAL_LOUDNESS_MAX_GAIN_DB,
    render_binaural,
    render_binaural_delivery,
)
from upmixer.config import UpmixConfig
from upmixer.formats import BINAURAL, BINAURAL_BED_FORMATS, ChannelLabel, FORMAT_MAP
from upmixer.mastering.delivery import resolve_delivery_target


def test_encode_gains_omni_channel_is_unity():
    gains = encode_gains(0.0, 0.0)
    assert gains[0] == pytest.approx(1.0)


def test_encode_gains_front_source_has_zero_lateral_and_height_terms():
    gains = encode_gains(0.0, 0.0)
    # ACN1 (Y, lateral) and ACN2 (Z, height) must vanish dead-ahead.
    assert gains[1] == pytest.approx(0.0, abs=1e-9)
    assert gains[2] == pytest.approx(0.0, abs=1e-9)
    # ACN3 (X, front/back) must be at its positive maximum (sqrt(3)).
    assert gains[3] == pytest.approx(math.sqrt(3.0))


def test_encode_gains_returns_16_channels():
    gains = encode_gains(0.3, -0.2)
    assert gains.shape == (N_ACN_CHANNELS,)
    assert np.all(np.isfinite(gains))


def test_encode_gains_acn12_omits_n3d_sqrt7_factor():
    # ACN 12 (Y3^0) is deliberately unscaled relative to standard N3D — the
    # decode filter bank was fit as the pseudo-inverse of this exact encoder
    # (docs/standards/spatial_audio_engine.md §3). The web preview's SH
    # library uses standard N3D and scales this channel by 1/sqrt(7) to
    # match; changing this formula requires regenerating the decode filters.
    delta = 0.4
    gains = encode_gains(0.0, delta)
    sin_d = math.sin(delta)
    expected = 0.5 * sin_d * (5.0 * sin_d**2 - 3.0)
    assert gains[12] == pytest.approx(expected)


def _band_ild_db(azimuth_deg: float, sr: int, lo_hz: float, hi_hz: float) -> float:
    left, right = synth_hrir(math.radians(azimuth_deg), 0.0, sr, 256)
    n_fft = 4096
    mag_l = np.abs(np.fft.rfft(left, n_fft))
    mag_r = np.abs(np.fft.rfft(right, n_fft))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    band = (freqs >= lo_hz) & (freqs < hi_hz)
    return float(20.0 * np.log10(np.mean(mag_l[band]) / np.mean(mag_r[band])))


def test_head_shadow_ild_is_frequency_dependent():
    # A head cannot shadow wavelengths longer than itself, so the interaural
    # level difference must collapse at low frequency.
    assert abs(_band_ild_db(30.0, 48000, 100.0, 300.0)) < 1.0
    assert _band_ild_db(30.0, 48000, 2000.0, 6000.0) >= 4.0


@pytest.mark.parametrize("azimuth_deg", [30.0, 135.0])
def test_synth_hrir_is_exactly_mirror_symmetric(azimuth_deg):
    left_pos, right_pos = synth_hrir(math.radians(azimuth_deg), 0.0, 48000, 256)
    left_neg, right_neg = synth_hrir(math.radians(-azimuth_deg), 0.0, 48000, 256)
    assert np.array_equal(left_pos, right_neg)
    assert np.array_equal(right_pos, left_neg)


def test_synth_hrir_uses_the_rear_hemisphere_woodworth_branch():
    # Woodworth gives the same path difference at 45° and 135°; applying its
    # front-hemisphere equation to the rear source would instead over-delay it.
    _, right_front = synth_hrir(math.radians(45.0), 0.0, 48000, 256)
    _, right_rear = synth_hrir(math.radians(135.0), 0.0, 48000, 256)
    np.testing.assert_allclose(right_rear, right_front, atol=1e-12)


def test_synth_hrir_is_periodic_at_axial_endpoints():
    front = synth_hrir(0.0, 0.0, 48000, 256)
    full_turn = synth_hrir(2.0 * math.pi, 0.0, 48000, 256)
    rear_left = synth_hrir(math.pi, 0.0, 48000, 256)
    rear_right = synth_hrir(-math.pi, 0.0, 48000, 256)
    for actual, expected in zip(full_turn, front):
        assert np.array_equal(actual, expected)
    for actual, expected in zip(rear_left, rear_right):
        assert np.array_equal(actual, expected)
    assert np.array_equal(rear_left[0], rear_left[1])


def test_listening_voicing_params_exact():
    # Pins the web mirror's target values (masteringProfiles.ts
    # VOICING_PARAMS.listening) so a hand-sync drift like the one that
    # doubled these values on the web side is caught here too.
    params = VOICING_PARAMS[BinauralProfile.LISTENING]
    assert params.crossfeed_amount == pytest.approx(0.10)
    assert params.bass_shelf_gain_db == pytest.approx(1.0)
    assert params.air_shelf_gain_db == pytest.approx(4.0)
    assert params.presence_gain_db == pytest.approx(2.0)
    assert params.stereo_widen == pytest.approx(0.15)
    assert params.loudness_target_lkfs is None


def test_geometry_is_layout_specific():
    five_one = speaker_azimuth_elevation(FORMAT_MAP["5.1"])
    seven_one_two = speaker_azimuth_elevation(FORMAT_MAP["7.1.2"])

    assert five_one[ChannelLabel.SL].azimuth_deg == 110.0
    assert seven_one_two[ChannelLabel.SL].azimuth_deg == 90.0
    assert seven_one_two[ChannelLabel.TFL].azimuth_deg == 90.0
    assert seven_one_two[ChannelLabel.TFL].elevation_deg == 30.0
    assert ChannelLabel.LFE not in seven_one_two


def test_azimuth_elevation_front_center_is_zero():
    pos = speaker_azimuth_elevation(FORMAT_MAP["5.1"])[ChannelLabel.C]
    assert pos.azimuth_deg == pytest.approx(0.0, abs=1e-6)
    assert pos.elevation_deg == pytest.approx(0.0, abs=1e-6)


def test_azimuth_left_speaker_is_positive():
    pos = speaker_azimuth_elevation(FORMAT_MAP["5.1"])[ChannelLabel.FL]
    assert pos.azimuth_deg > 0


@pytest.mark.parametrize("profile", BINAURAL_PROFILES)
def test_load_decode_filter_set_shape(profile):
    name = {"flat": "flat_o3_decode", "studio": "studio_o3_decode", "listening": "listening_o3_decode"}[profile]
    filter_set = load_decode_filter_set(name, 48000)
    assert filter_set.taps.shape[0] == N_ACN_CHANNELS
    assert filter_set.taps.shape[1] == 2
    assert filter_set.sample_rate == 48000
    assert np.all(np.isfinite(filter_set.taps))


def test_load_decode_filter_set_resamples():
    filter_set_48k = load_decode_filter_set("flat_o3_decode", 48000)
    filter_set_44k = load_decode_filter_set("flat_o3_decode", 44100)
    assert filter_set_44k.sample_rate == 44100
    # Resampled length should scale roughly with the rate ratio.
    ratio = filter_set_44k.taps.shape[-1] / filter_set_48k.taps.shape[-1]
    assert ratio == pytest.approx(44100 / 48000, rel=0.05)


def test_decode_to_binaural_silence_in_silence_out():
    filter_set = load_decode_filter_set("flat_o3_decode", 48000)
    hoa = np.zeros((N_ACN_CHANNELS, 1000))
    left, right = decode_to_binaural(hoa, filter_set)
    assert left.shape == (1000,)
    assert np.all(left == 0.0)
    assert np.all(right == 0.0)


def test_resolve_profile_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_profile("cinema")


@pytest.mark.parametrize("bed_name", BINAURAL_BED_FORMATS)
@pytest.mark.parametrize("profile", BINAURAL_PROFILES)
def test_render_binaural_shape_and_finite(bed_name, profile):
    sr = 48000
    n = sr // 2
    bed_fmt = FORMAT_MAP[bed_name]
    rng = np.random.default_rng(42)
    channels = {label.value: rng.standard_normal(n) * 0.05 for label in bed_fmt.channels}

    left, right = render_binaural(channels, bed_fmt, sr, profile)
    assert left.shape == (n,)
    assert right.shape == (n,)
    assert np.all(np.isfinite(left))
    assert np.all(np.isfinite(right))


def test_render_binaural_silent_bed_is_silent():
    sr = 48000
    n = 4800
    bed_fmt = FORMAT_MAP["7.1.4"]
    channels = {label.value: np.zeros(n) for label in bed_fmt.channels}
    left, right = render_binaural(channels, bed_fmt, sr, "flat")
    assert np.max(np.abs(left)) == pytest.approx(0.0, abs=1e-12)
    assert np.max(np.abs(right)) == pytest.approx(0.0, abs=1e-12)


def test_listening_profile_differs_from_flat():
    sr = 48000
    n = sr
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(7)
    channels = {label.value: rng.standard_normal(n) * 0.05 for label in bed_fmt.channels}

    flat_l, flat_r = render_binaural(channels, bed_fmt, sr, "flat")
    listening_l, listening_r = render_binaural(channels, bed_fmt, sr, "listening")

    assert not np.allclose(flat_l, listening_l)
    assert not np.allclose(flat_r, listening_r)


def test_listening_output_differs_from_studio():
    # Listening's cinema tail plus its hi-fi enhancement voicing (bass/air
    # tilt, presence, wide M/S) should render audibly differently from
    # Studio's neutral monitor room + bypassed voicing.
    sr = 48000
    n = sr
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(11)
    channels = {label.value: rng.standard_normal(n) * 0.05 for label in bed_fmt.channels}

    studio_l, studio_r = render_binaural(channels, bed_fmt, sr, "studio")
    listening_l, listening_r = render_binaural(channels, bed_fmt, sr, "listening")

    assert not np.allclose(studio_l, listening_l)
    assert not np.allclose(studio_r, listening_r)


def test_listening_is_loudness_matched_to_studio():
    # Listening's enhance voicing is level-matched to Studio (no loudness
    # lift of its own — loudness_target_lkfs is None), so both deliver at the
    # same config-default target within the measurement tolerance.
    sr = 48000
    n = sr * 2
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(11)
    channels = {label.value: rng.standard_normal(n) * 0.05 for label in bed_fmt.channels}

    _, studio_result = render_binaural_delivery(channels, bed_fmt, sr, UpmixConfig(binaural_profile="studio"))
    _, listening_result = render_binaural_delivery(channels, bed_fmt, sr, UpmixConfig(binaural_profile="listening"))

    assert listening_result.measured_lkfs == pytest.approx(studio_result.measured_lkfs, abs=1.0)


def test_binaural_format_registered():
    assert BINAURAL.n_channels == 2
    assert BINAURAL.channels == (ChannelLabel.FL, ChannelLabel.FR)
    assert "binaural" not in FORMAT_MAP


def test_binaural_bed_formats_are_valid_output_formats():
    for name in BINAURAL_BED_FORMATS:
        assert name in FORMAT_MAP
        assert FORMAT_MAP[name].n_channels > 2


@pytest.mark.parametrize("profile", BINAURAL_PROFILES)
@pytest.mark.parametrize("bed_name", BINAURAL_BED_FORMATS)
def test_render_binaural_is_left_right_balanced_for_a_centered_signal(bed_name, profile):
    # Regression: the decode filter set's virtual-loudspeaker direction set
    # must be exactly mirror-symmetric (see scripts/build_binaural_filters.py
    # real_speaker_directions) or a perfectly centered/symmetric bed decodes to
    # audibly unequal L/R levels even though nothing in the mix is panned.
    sr = 48000
    n = sr
    bed_fmt = FORMAT_MAP[bed_name]
    rng = np.random.default_rng(0)
    mono = rng.standard_normal(n) * 0.1
    channels = {label.value: mono.copy() for label in bed_fmt.channels}

    left, right = render_binaural(channels, bed_fmt, sr, profile)
    left_rms = float(np.sqrt(np.mean(left**2)))
    right_rms = float(np.sqrt(np.mean(right**2)))
    assert left_rms == pytest.approx(right_rms, rel=1e-9)


@pytest.mark.parametrize("profile", BINAURAL_PROFILES)
def test_binaural_delivery_meets_true_peak_ceiling_on_hot_bed(profile):
    # Regression: soft_limit used to run BEFORE loudness normalization on the
    # raw HRTF sum, which could bake in tanh saturation and still leave the
    # normalized delivery above the -1 dBTP ceiling. The limiter now runs
    # last, after the (bounded) loudness correction.
    sr = 48000
    n = sr * 2
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(7)
    channels = {label.value: rng.standard_normal(n) * 0.9 for label in bed_fmt.channels}
    cfg = UpmixConfig(binaural_profile=profile)

    _, result = render_binaural_delivery(channels, bed_fmt, sr, cfg)

    assert result.measured_tp_dbtp <= resolve_delivery_target(cfg).max_tp_dbtp + 0.05


@pytest.mark.parametrize("profile", BINAURAL_PROFILES)
def test_binaural_delivery_upward_gain_is_bounded(profile):
    # Regression: the collapse-stage loudness pass used to allow up to
    # +30 dB of upward gain (the general mastering ceiling), which could
    # crank a quiet collapse well past the already-mastered bed's level.
    # It is now capped small since the bed is already loudness-matched.
    sr = 48000
    n = sr * 2
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(13)
    channels = {label.value: rng.standard_normal(n) * 1e-4 for label in bed_fmt.channels}
    cfg = UpmixConfig(binaural_profile=profile)

    _, result = render_binaural_delivery(channels, bed_fmt, sr, cfg)

    assert result.applied_gain_db <= BINAURAL_LOUDNESS_MAX_GAIN_DB + 1e-6


def test_lfe_is_attenuated_relative_to_unity_sum():
    # Regression: the LFE was summed into both ears at unity gain, fully
    # correlated across ears, effectively doubling its perceived weight next
    # to the HRTF-decoded bed and reading as boomy. It must now be attenuated
    # (default -10 dB, matching UpmixConfig.lfe_gain).
    sr = 48000
    n = sr
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(3)
    lfe = rng.standard_normal(n) * 0.2
    channels = {label.value: np.zeros(n) for label in bed_fmt.channels}
    channels[ChannelLabel.LFE.value] = lfe

    attenuated_l, attenuated_r = render_binaural(channels, bed_fmt, sr, "flat")
    unity_l, unity_r = render_binaural(channels, bed_fmt, sr, "flat", lfe_gain=1.0)

    attenuated_rms = float(np.sqrt(np.mean(attenuated_l**2) + np.mean(attenuated_r**2)))
    unity_rms = float(np.sqrt(np.mean(unity_l**2) + np.mean(unity_r**2)))
    assert attenuated_rms < unity_rms * 0.5


def test_lfe_lowpass_follows_configured_cutoff():
    # Regression: the binaural LFE re-add hardcoded a 120 Hz cutoff instead
    # of reading UpmixConfig.lfe_cutoff_hz, so a manifest/CLI cutoff change
    # silently had no effect on binaural or transaural renders.
    sr = 48000
    n = sr
    bed_fmt = FORMAT_MAP["7.1.4"]
    t = np.arange(n) / sr
    tone = 0.2 * np.sin(2.0 * np.pi * 100.0 * t)
    channels = {label.value: np.zeros(n) for label in bed_fmt.channels}
    channels[ChannelLabel.LFE.value] = tone

    narrow_l, narrow_r = render_binaural(channels, bed_fmt, sr, "flat", lfe_gain=1.0, lfe_cutoff_hz=80.0)
    wide_l, wide_r = render_binaural(channels, bed_fmt, sr, "flat", lfe_gain=1.0, lfe_cutoff_hz=120.0)

    narrow_rms = float(np.sqrt(np.mean(narrow_l**2) + np.mean(narrow_r**2)))
    wide_rms = float(np.sqrt(np.mean(wide_l**2) + np.mean(wide_r**2)))
    assert narrow_rms < wide_rms * 0.5
