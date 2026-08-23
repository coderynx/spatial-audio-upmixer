import numpy as np

from upmixer.config import UpmixConfig
from upmixer.utils import elevation_eq

SR = 48_000
N = 8192


def _kernel_response(cfg: UpmixConfig, band_gain: float) -> np.ndarray:
    impulse = np.zeros(N)
    impulse[0] = 1.0
    shaped = elevation_eq(
        impulse,
        SR,
        low_rolloff_hz=cfg.height_low_rolloff_hz,
        low_rolloff_gain=cfg.height_low_rolloff_gain,
        high_shelf_hz=cfg.height_crossover_hz,
        high_shelf_gain=cfg.height_high_shelf_gain,
        directional_band_hz=cfg.height_directional_band_hz,
        directional_band_gain=band_gain,
    )
    return np.abs(np.fft.rfft(shaped))


def _third_octave_means(freqs: np.ndarray, values_db: np.ndarray) -> np.ndarray:
    edges = 20.0 * 2.0 ** (np.arange(0, 31) / 3.0)
    return np.array(
        [
            values_db[(freqs >= lo) & (freqs < hi)].mean()
            for lo, hi in zip(edges[:-1], edges[1:])
            if ((freqs >= lo) & (freqs < hi)).any()
        ]
    )


def test_default_band_gain_leaves_the_height_send_untouched():
    cfg = UpmixConfig()
    assert cfg.height_directional_band_gain == 1.0

    rng = np.random.default_rng(7)
    signal = rng.standard_normal(N)
    flat = elevation_eq(
        signal,
        SR,
        low_rolloff_hz=cfg.height_low_rolloff_hz,
        low_rolloff_gain=cfg.height_low_rolloff_gain,
        high_shelf_hz=cfg.height_crossover_hz,
        high_shelf_gain=cfg.height_high_shelf_gain,
    )
    configured = elevation_eq(
        signal,
        SR,
        low_rolloff_hz=cfg.height_low_rolloff_hz,
        low_rolloff_gain=cfg.height_low_rolloff_gain,
        high_shelf_hz=cfg.height_crossover_hz,
        high_shelf_gain=cfg.height_high_shelf_gain,
        directional_band_hz=cfg.height_directional_band_hz,
        directional_band_gain=cfg.height_directional_band_gain,
    )
    assert np.array_equal(flat, configured)


def test_band_lifts_only_its_own_octave():
    cfg = UpmixConfig()
    freqs = np.fft.rfftfreq(N, 1.0 / SR)
    lift_db = 20.0 * np.log10(_kernel_response(cfg, 1.6) / _kernel_response(cfg, 1.0))

    centre = np.argmin(np.abs(freqs - cfg.height_directional_band_hz))
    assert abs(lift_db[centre] - 20.0 * np.log10(1.6)) < 0.05
    for hz in (1000.0, 2000.0, 20000.0):
        assert abs(lift_db[np.argmin(np.abs(freqs - hz))]) < 0.5, hz
