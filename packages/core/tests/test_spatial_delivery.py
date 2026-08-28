import numpy as np
import pytest

from upmixer.binaural.renderer import render_binaural_delivery
from upmixer.config import UpmixConfig
from upmixer.crosstalk.renderer import render_crosstalk_delivery
from upmixer.formats import FORMAT_MAP
from upmixer.loudness import measure_true_peak
from upmixer.mastering.delivery import resolve_delivery_target


@pytest.mark.parametrize(
    ("renderer", "config"),
    [
        (render_binaural_delivery, {"binaural_profile": "flat"}),
        (render_crosstalk_delivery, {"transaural_profile": "stereo"}),
    ],
)
def test_spatial_delivery_enforces_true_peak_without_loudness_normalization(
    renderer, config
):
    sample_rate = 48_000
    bed_fmt = FORMAT_MAP["7.1.4"]
    rng = np.random.default_rng(42)
    channels = {
        label.value: rng.standard_normal(sample_rate) * 0.9
        for label in bed_fmt.channels
    }
    cfg = UpmixConfig(loudness_normalize=False, **config)

    output, result = renderer(channels, bed_fmt, sample_rate, cfg)
    ceiling = resolve_delivery_target(cfg).max_tp_dbtp

    assert result.tp_limited
    assert result.applied_gain_db < 0.0
    assert result.measured_tp_dbtp <= ceiling + 1e-6
    assert measure_true_peak(output) <= ceiling + 1e-6
