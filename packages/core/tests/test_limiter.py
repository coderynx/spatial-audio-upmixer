import numpy as np
import pytest

from upmixer.loudness import measure_true_peak
from upmixer.mastering.limiter import LookAheadLimiter, _forward_window_min

_SR = 48000


def _brute_force_forward_min(values: np.ndarray, window: int) -> np.ndarray:
    """Reference forward-window minimum, treating out-of-range as 1.0."""
    n = len(values)
    out = np.empty(n)
    for i in range(n):
        end = min(i + window, n)
        tail = [1.0] * (window - (end - i))
        out[i] = min(list(values[i:end]) + tail)
    return out


@pytest.mark.parametrize("window", [1, 3, 5, 7, 25])
def test_forward_window_min_matches_brute_force(window):
    rng = np.random.default_rng(1)
    values = rng.random(200)
    got = _forward_window_min(values, window)
    expected = _brute_force_forward_min(values, window)
    np.testing.assert_allclose(got, expected)


def test_forward_window_min_rounds_even_window_up_to_odd():
    """Documented behavior: an even window is widened by one to stay centerable."""
    rng = np.random.default_rng(2)
    values = rng.random(200)
    got = _forward_window_min(values, 10)
    expected = _brute_force_forward_min(values, 11)
    np.testing.assert_allclose(got, expected)


def test_limiter_meets_true_peak_ceiling_on_hard_transient():
    """A sample-domain spike a tanh soft-limit would clip abruptly must
    instead be brought under the true-peak ceiling by the limiter."""
    rng = np.random.default_rng(0)
    n = _SR * 2
    signal = 0.3 * rng.standard_normal(n)
    signal[1000] = 2.0
    signal[1001] = -1.8
    channels = {"FL": signal.copy(), "FR": signal.copy()}

    ceiling_dbtp = -1.0
    limiter = LookAheadLimiter(
        ceiling_dbtp=ceiling_dbtp, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    tp = measure_true_peak(out)
    assert tp <= ceiling_dbtp + 0.05, f"True peak {tp} dBTP exceeds ceiling {ceiling_dbtp}"


def test_limiter_meets_true_peak_ceiling_on_near_nyquist_tone():
    """Near-Nyquist content has the largest sample-vs-true-peak gap (ISPs)."""
    n = _SR * 1
    t = np.arange(n) / _SR
    signal = 0.99 * np.sin(2 * np.pi * (0.45 * _SR) * t + 0.37)
    channels = {"FL": signal.copy(), "FR": signal.copy()}

    ceiling_dbtp = -1.0
    limiter = LookAheadLimiter(
        ceiling_dbtp=ceiling_dbtp, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    tp = measure_true_peak(out)
    assert tp <= ceiling_dbtp + 0.05, f"True peak {tp} dBTP exceeds ceiling {ceiling_dbtp}"


@pytest.mark.parametrize("seed", range(15))
def test_limiter_meets_ceiling_on_dense_random_noise(seed):
    """Regression: dense broadband noise can pack natural ISPs close enough
    together that per-block gain, applied pointwise and re-interpolated,
    recombines into fresh overshoot unless gain reduction is held across
    the detector FIR's own kernel width (see limiter.py's module docstring,
    'Gain-modulation edge effect'). This caught a real bug during
    development — several seeds overshot the ceiling before the fix."""
    rng = np.random.default_rng(seed)
    n = _SR * 3
    signal = 0.5 * rng.standard_normal(n)
    channels = {"FL": signal.copy(), "FR": signal.copy()}

    ceiling_dbtp = -1.0
    limiter = LookAheadLimiter(
        ceiling_dbtp=ceiling_dbtp, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    tp = measure_true_peak(out)
    assert tp <= ceiling_dbtp + 0.05, f"True peak {tp} dBTP exceeds ceiling {ceiling_dbtp}"


def test_limiter_preserves_length_dtype_and_channel_set():
    rng = np.random.default_rng(3)
    n = _SR
    channels = {
        "FL": (0.5 * rng.standard_normal(n)).astype(np.float64),
        "LFE": (0.2 * rng.standard_normal(n)).astype(np.float64),
    }
    limiter = LookAheadLimiter(
        ceiling_dbtp=-1.0, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    assert set(out.keys()) == set(channels.keys())
    for name in channels:
        assert out[name].shape == channels[name].shape
        assert out[name].dtype == channels[name].dtype


def test_limiter_is_transparent_below_ceiling():
    """Content well under the ceiling should pass through effectively unchanged."""
    rng = np.random.default_rng(4)
    n = _SR
    signal = 0.1 * rng.standard_normal(n)
    channels = {"FL": signal.copy(), "FR": signal.copy()}

    limiter = LookAheadLimiter(
        ceiling_dbtp=-1.0, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    np.testing.assert_allclose(out["FL"], signal, atol=1e-6)
    np.testing.assert_allclose(out["FR"], signal, atol=1e-6)


def test_limiter_gain_is_linked_across_channels():
    """A peak on one channel must attenuate all channels by the same gain
    (linked detection), not just the channel that peaked."""
    rng = np.random.default_rng(5)
    n = _SR
    quiet = 0.05 * rng.standard_normal(n)
    hot = quiet.copy()
    hot[500] = 3.0

    channels = {"HOT": hot, "QUIET": quiet.copy()}
    limiter = LookAheadLimiter(
        ceiling_dbtp=-1.0, lookahead_ms=5.0, release_ms=50.0, sample_rate=_SR
    )
    out = limiter.process(channels)

    with np.errstate(divide="ignore", invalid="ignore"):
        gain_hot = np.where(hot != 0, out["HOT"] / hot, np.nan)
        gain_quiet = np.where(quiet != 0, out["QUIET"] / quiet, np.nan)

    near_peak = slice(400, 600)
    valid = np.isfinite(gain_hot[near_peak]) & np.isfinite(gain_quiet[near_peak])
    np.testing.assert_allclose(
        gain_hot[near_peak][valid], gain_quiet[near_peak][valid], atol=1e-9
    )
