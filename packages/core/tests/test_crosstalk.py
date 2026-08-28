import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.crosstalk.filters import apply_xtc, load_xtc_filter_set
from upmixer.crosstalk.geometry import speaker_azimuths_rad
from upmixer.crosstalk.profiles import (
    CROSSTALK_PROFILES,
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
from upmixer.binaural import head_model
from upmixer.binaural.head_model import synth_hrir
from upmixer.formats import FORMAT_MAP, TRANSAURAL, TRANSAURAL_BED_FORMATS, ChannelLabel
from upmixer.mastering.delivery import resolve_delivery_target

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


def _effective_response(
    profile: CrosstalkProfile, c: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Ear response through the real acoustic path C with XTC applied: E = C * H."""
    c_ll, c_lr, c_rl, c_rr = c
    filter_set = load_xtc_filter_set(XTC_FILTER_SET[profile], SR)
    h_ll, h_lr, h_rl, h_rr = (
        filter_set.taps[0, 0], filter_set.taps[0, 1], filter_set.taps[1, 0], filter_set.taps[1, 1],
    )
    e_ll = np.convolve(c_ll, h_ll) + np.convolve(c_lr, h_rl)  # desired-left -> left ear (ipsi)
    e_rl = np.convolve(c_rl, h_ll) + np.convolve(c_rr, h_rl)  # desired-left -> right ear (leakage)
    e_rr = np.convolve(c_rr, h_rr) + np.convolve(c_rl, h_lr)  # desired-right -> right ear (ipsi)
    e_lr = np.convolve(c_ll, h_lr) + np.convolve(c_lr, h_rr)  # desired-right -> left ear (leakage)
    return e_ll, e_lr, e_rl, e_rr


def _xtc_and_coloration_db(
    c: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    e: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    lo: float = _BAND_LO,
    hi: float = _BAND_HI,
) -> tuple[float, float]:
    """Return (leakage suppression vs no cancellation, |ipsilateral coloration|) in dB."""
    c_ll, c_lr, c_rl, c_rr = c
    e_ll, e_lr, e_rl, e_rr = e
    energy = lambda sig: _band_energy(sig, SR, lo, hi)  # noqa: E731
    ipsi_xtc = energy(e_ll) + energy(e_rr)
    contra_xtc = energy(e_rl) + energy(e_lr)
    ipsi_naive = energy(c_ll) + energy(c_rr)
    contra_naive = energy(c_rl) + energy(c_lr)
    return (
        float(10.0 * np.log10(contra_naive / contra_xtc)),
        float(abs(10.0 * np.log10(ipsi_xtc / ipsi_naive))),
    )


def test_resolve_profile_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_profile("theater")


def test_crosstalk_profiles_registered():
    assert set(CROSSTALK_PROFILES) == {"stereo", "smart_speaker", "car", "laptop", "phone"}


def test_stereo_and_smart_speaker_are_symmetric_spans():
    for profile in (
        CrosstalkProfile.STEREO,
        CrosstalkProfile.SMART_SPEAKER,
        CrosstalkProfile.LAPTOP,
        CrosstalkProfile.PHONE,
    ):
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


@pytest.mark.parametrize("profile", CROSSTALK_PROFILES)
def test_xtc_filter_set_is_delayed_identity_below_active_band(profile):
    resolved_profile = resolve_profile(profile)
    params = XTC_PARAMS[resolved_profile]
    taps = load_xtc_filter_set(XTC_FILTER_SET[resolved_profile], SR).taps
    n_fft = 4096
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / SR)
    response = np.fft.rfft(taps, n_fft, axis=-1)
    delay = np.exp(
        -2j * np.pi * np.arange(response.shape[-1]) * (taps.shape[-1] // 2) / n_fft
    )
    low = (freqs >= 20.0) & (freqs <= params.xtc_lo_hz)

    diagonal = np.concatenate(
        (response[0, 0, low] / delay[low], response[1, 1, low] / delay[low])
    )
    crossfeed = np.concatenate((response[0, 1, low], response[1, 0, low]))
    assert np.allclose(diagonal, 1.0, atol=0.01)
    assert np.max(np.abs(crossfeed)) <= 0.01


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


@pytest.mark.parametrize("profile", ["stereo", "smart_speaker", "laptop", "phone"])
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

    assert result.measured_tp_dbtp <= resolve_delivery_target(cfg).max_tp_dbtp + 0.05


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
        (CrosstalkProfile.STEREO, 22.0, 3.0),
        (CrosstalkProfile.SMART_SPEAKER, 13.0, 3.0),
        (CrosstalkProfile.CAR, 19.0, 3.0),
        (CrosstalkProfile.LAPTOP, 15.0, 3.0),
        (CrosstalkProfile.PHONE, 12.0, 3.0),
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
    c = _speaker_to_ear_matrix(profile)
    xtc_gain_db, coloration_db = _xtc_and_coloration_db(c, _effective_response(profile, c))

    assert xtc_gain_db >= min_xtc_db
    assert coloration_db <= max_coloration_db


@pytest.mark.parametrize("profile", list(CrosstalkProfile))
@pytest.mark.parametrize("lo,hi", [(300.0, 1000.0), (1000.0, 3000.0), (3000.0, 6000.0)])
def test_xtc_per_band_depth_and_coloration(profile, lo, hi):
    """The tradeoff must hold in every sub-band, not just on band-summed energy.

    A filter can post a strong 300 Hz-6 kHz total while being useless (or
    coloring badly) inside one octave of it; the whole point of the
    frequency-dependent regularization is that the budget is honored per bin.
    """
    c = _speaker_to_ear_matrix(profile)
    xtc_gain_db, coloration_db = _xtc_and_coloration_db(c, _effective_response(profile, c), lo, hi)

    assert xtc_gain_db > 0.0
    assert coloration_db <= max(XTC_PARAMS[profile].gamma_db, 3.0)


@pytest.mark.parametrize("profile", list(CrosstalkProfile))
@pytest.mark.parametrize("radius_scale", [1.1, 0.9])
def test_xtc_survives_head_size_mismatch(profile, radius_scale, monkeypatch):
    """Baked filters must degrade gracefully on a head they were not designed for.

    Evaluating H against the very C it was inverted from only proves the
    algebra; a listener's head is never the model's. This catches a filter
    that scores well by overfitting the design head.
    """
    monkeypatch.setattr(head_model, "HEAD_RADIUS_M", head_model.HEAD_RADIUS_M * radius_scale)
    c = _speaker_to_ear_matrix(profile)
    xtc_gain_db, coloration_db = _xtc_and_coloration_db(c, _effective_response(profile, c))

    assert xtc_gain_db >= 5.0
    assert coloration_db <= 6.0


@pytest.mark.parametrize("profile", list(CrosstalkProfile))
def test_xtc_passes_low_frequencies_without_a_crossover_notch(profile):
    """Below the active band the filter blends to identity — with no comb dip.

    Both blend branches carry the same bulk delay precisely so this crossover
    stays flat (docs/standards/transaural_speakers.md §4.3).
    """
    params = XTC_PARAMS[profile]
    c = _speaker_to_ear_matrix(profile)
    e_ll = _effective_response(profile, c)[0]
    n_fft = 16384
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / SR)
    deviation_db = 20.0 * np.log10(
        np.abs(np.fft.rfft(e_ll, n=n_fft)) / np.abs(np.fft.rfft(c[0], n=n_fft))
    )

    passband = (freqs >= 20.0) & (freqs <= params.xtc_lo_hz)
    assert np.max(np.abs(deviation_db[passband])) <= 1.0

    # A delay mismatch between the two blend branches would comb: narrow,
    # repeating notches. Regularization's own broad tilt is expected instead,
    # so compare each bin against its local average rather than against 0 dB.
    crossover = (freqs >= 0.7 * params.xtc_lo_hz) & (freqs <= 3.0 * params.xtc_lo_hz)
    local = np.convolve(deviation_db, np.ones(33) / 33.0, mode="same")
    assert np.max(np.abs((deviation_db - local)[crossover])) <= 1.0
