import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.crosstalk.filters import apply_xtc, load_xtc_filter_set
from upmixer.crosstalk.geometry import speaker_azimuths_rad
from upmixer.crosstalk.profiles import (
    CROSSTALK_PROFILES,
    VOICING_PARAMS,
    XTC_FILTER_SET,
    XTC_PARAMS,
    CrosstalkProfile,
    resolve_profile,
)
from upmixer.crosstalk.renderer import (
    CROSSTALK_LOUDNESS_MAX_GAIN_DB,
    render_crosstalk,
    render_crosstalk_delivery,
)
from upmixer.binaural.head_model import synth_hrir
from upmixer.formats import FORMAT_MAP, TRANSAURAL, TRANSAURAL_BED_FORMATS, ChannelLabel

SR = 48000
HRIR_TAPS = 256
_BAND_LO, _BAND_HI = 300.0, 6000.0


def _band_energy(signal: np.ndarray, sr: int, lo: float, hi: float, n_fft: int = 4096) -> float:
    spectrum = np.fft.rfft(signal, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    mask = (freqs >= lo) & (freqs <= hi)
    return float(np.sum(np.abs(spectrum[mask]) ** 2))


def _speaker_to_ear_matrix(profile: CrosstalkProfile) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    params = XTC_PARAMS[profile]
    az_left, az_right = speaker_azimuths_rad(params)
    c_ll, c_rl = synth_hrir(az_left, 0.0, SR, HRIR_TAPS)
    c_lr, c_rr = synth_hrir(az_right, 0.0, SR, HRIR_TAPS)
    return c_ll, c_lr, c_rl, c_rr


def test_resolve_profile_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_profile("theater")


def test_crosstalk_profiles_registered():
    assert set(CROSSTALK_PROFILES) == {"stereo", "smart_speaker", "car"}


def test_stereo_and_smart_speaker_are_symmetric_spans():
    for profile in (CrosstalkProfile.STEREO, CrosstalkProfile.SMART_SPEAKER):
        params = XTC_PARAMS[profile]
        assert params.azimuth_left_deg == pytest.approx(-params.azimuth_right_deg)


def test_car_profile_is_asymmetric():
    params = XTC_PARAMS[CrosstalkProfile.CAR]
    assert params.azimuth_left_deg != pytest.approx(-params.azimuth_right_deg)


@pytest.mark.parametrize("profile", CROSSTALK_PROFILES)
def test_load_xtc_filter_set_shape(profile):
    name = XTC_FILTER_SET[resolve_profile(profile)]
    filter_set = load_xtc_filter_set(name, SR)
    assert filter_set.taps.shape[0] == 2
    assert filter_set.taps.shape[1] == 2
    assert filter_set.sample_rate == SR
    assert np.all(np.isfinite(filter_set.taps))


def test_load_xtc_filter_set_resamples():
    filter_set_48k = load_xtc_filter_set("stereo_xtc", 48000)
    filter_set_44k = load_xtc_filter_set("stereo_xtc", 44100)
    assert filter_set_44k.sample_rate == 44100
    ratio = filter_set_44k.taps.shape[-1] / filter_set_48k.taps.shape[-1]
    assert ratio == pytest.approx(44100 / 48000, rel=0.05)


def test_apply_xtc_silence_in_silence_out():
    filter_set = load_xtc_filter_set("stereo_xtc", SR)
    left = np.zeros(1000)
    right = np.zeros(1000)
    speaker_l, speaker_r = apply_xtc(left, right, filter_set)
    assert speaker_l.shape == (1000,)
    assert np.all(speaker_l == 0.0)
    assert np.all(speaker_r == 0.0)


@pytest.mark.parametrize("bed_name", TRANSAURAL_BED_FORMATS)
@pytest.mark.parametrize("profile", CROSSTALK_PROFILES)
def test_render_crosstalk_shape_and_finite(bed_name, profile):
    n = SR // 2
    bed_fmt = FORMAT_MAP[bed_name]
    rng = np.random.default_rng(42)
    channels = {label.value: rng.standard_normal(n) * 0.05 for label in bed_fmt.channels}

    left, right = render_crosstalk(channels, bed_fmt, SR, profile)
    assert left.shape == (n,)
    assert right.shape == (n,)
    assert np.all(np.isfinite(left))
    assert np.all(np.isfinite(right))


def test_render_crosstalk_silent_bed_is_silent():
    n = 4800
    bed_fmt = FORMAT_MAP["7.1.4"]
    channels = {label.value: np.zeros(n) for label in bed_fmt.channels}
    left, right = render_crosstalk(channels, bed_fmt, SR, "stereo")
    assert np.max(np.abs(left)) == pytest.approx(0.0, abs=1e-9)
    assert np.max(np.abs(right)) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("profile", ["stereo", "smart_speaker"])
def test_symmetric_profiles_are_left_right_balanced_for_a_centered_signal(profile):
    # Same regression class as binaural's mirror-symmetry test: a perfectly
    # centered/symmetric bed through a symmetric speaker span must not decode
    # to audibly unequal L/R levels.
    n = SR
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(0)
    mono = rng.standard_normal(n) * 0.1
    channels = {label.value: mono.copy() for label in bed_fmt.channels}

    left, right = render_crosstalk(channels, bed_fmt, SR, profile)
    left_rms = float(np.sqrt(np.mean(left**2)))
    right_rms = float(np.sqrt(np.mean(right**2)))
    assert left_rms == pytest.approx(right_rms, rel=1e-6)


def test_car_profile_is_not_left_right_balanced_for_a_centered_signal():
    # Confirms the asymmetric driver-seat geometry actually takes effect
    # (regression against accidentally wiring symmetric azimuths for `car`).
    n = SR
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(0)
    mono = rng.standard_normal(n) * 0.1
    channels = {label.value: mono.copy() for label in bed_fmt.channels}

    left, right = render_crosstalk(channels, bed_fmt, SR, "car")
    left_rms = float(np.sqrt(np.mean(left**2)))
    right_rms = float(np.sqrt(np.mean(right**2)))
    assert left_rms != pytest.approx(right_rms, rel=1e-3)


@pytest.mark.parametrize("profile", CROSSTALK_PROFILES)
def test_crosstalk_delivery_meets_true_peak_ceiling_on_hot_bed(profile):
    n = SR * 2
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(7)
    channels = {label.value: rng.standard_normal(n) * 0.9 for label in bed_fmt.channels}
    cfg = UpmixConfig(transaural_profile=profile)

    _, result = render_crosstalk_delivery(channels, bed_fmt, SR, cfg)

    assert result.measured_tp_dbtp <= cfg.loudness_max_tp + 0.05


@pytest.mark.parametrize("profile", CROSSTALK_PROFILES)
def test_crosstalk_delivery_upward_gain_is_bounded(profile):
    n = SR * 2
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(13)
    channels = {label.value: rng.standard_normal(n) * 1e-4 for label in bed_fmt.channels}
    cfg = UpmixConfig(transaural_profile=profile)

    _, result = render_crosstalk_delivery(channels, bed_fmt, SR, cfg)

    assert result.applied_gain_db <= CROSSTALK_LOUDNESS_MAX_GAIN_DB + 1e-6


def test_transaural_format_registered():
    assert TRANSAURAL.n_channels == 2
    assert TRANSAURAL.channels == (ChannelLabel.FL, ChannelLabel.FR)
    assert "transaural" not in FORMAT_MAP


def test_transaural_bed_formats_are_valid_output_formats():
    for name in TRANSAURAL_BED_FORMATS:
        assert name in FORMAT_MAP
        assert FORMAT_MAP[name].n_channels > 2


@pytest.mark.parametrize(
    "profile,min_xtc_db,max_coloration_db",
    [
        (CrosstalkProfile.STEREO, 15.0, 3.0),
        (CrosstalkProfile.SMART_SPEAKER, 6.0, 3.0),
        (CrosstalkProfile.CAR, 10.0, 3.0),
    ],
)
def test_xtc_reduces_contralateral_leakage_within_coloration_bound(profile, min_xtc_db, max_coloration_db):
    """Objective correctness check for the XTC design (see docs/standards/
    transaural_speakers.md §4 and §6, and the evaluation-harness precedent in
    docs/evaluation_harness.md): no crosstalk-cancellation change ships
    without confirming both halves of the tradeoff — the baked filter must
    measurably suppress contralateral (opposite-ear) leakage relative to
    playing raw binaural on speakers with no cancellation at all, while
    keeping the ipsilateral (same-ear) response within a bounded coloration
    window instead of just chasing suppression depth.
    """
    c_ll, c_lr, c_rl, c_rr = _speaker_to_ear_matrix(profile)
    filter_set = load_xtc_filter_set(XTC_FILTER_SET[profile], SR)
    h_ll, h_lr, h_rl, h_rr = (
        filter_set.taps[0, 0], filter_set.taps[0, 1], filter_set.taps[1, 0], filter_set.taps[1, 1],
    )

    # Effective ear response through the real acoustic path C with the XTC
    # filters H applied first: E = C (convolution) H.
    e_ll = np.convolve(c_ll, h_ll) + np.convolve(c_lr, h_rl)  # desired-left -> left ear (ipsi)
    e_rl = np.convolve(c_rl, h_ll) + np.convolve(c_rr, h_rl)  # desired-left -> right ear (leakage)
    e_rr = np.convolve(c_rr, h_rr) + np.convolve(c_rl, h_lr)  # desired-right -> right ear (ipsi)
    e_lr = np.convolve(c_ll, h_lr) + np.convolve(c_lr, h_rr)  # desired-right -> left ear (leakage)

    ipsi_xtc = _band_energy(e_ll, SR, _BAND_LO, _BAND_HI) + _band_energy(e_rr, SR, _BAND_LO, _BAND_HI)
    contra_xtc = _band_energy(e_rl, SR, _BAND_LO, _BAND_HI) + _band_energy(e_lr, SR, _BAND_LO, _BAND_HI)
    ipsi_naive = _band_energy(c_ll, SR, _BAND_LO, _BAND_HI) + _band_energy(c_rr, SR, _BAND_LO, _BAND_HI)
    contra_naive = _band_energy(c_rl, SR, _BAND_LO, _BAND_HI) + _band_energy(c_lr, SR, _BAND_LO, _BAND_HI)

    xtc_gain_db = 10.0 * np.log10(contra_naive / contra_xtc)
    coloration_db = abs(10.0 * np.log10(ipsi_xtc / ipsi_naive))

    assert xtc_gain_db >= min_xtc_db
    assert coloration_db <= max_coloration_db
