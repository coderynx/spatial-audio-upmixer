import math

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel
from upmixer.upmix.multichannel import MultichannelUpmixer, _extract_center
from upmixer.utils import ITU_CENTER_COEFF

_SR = 48_000
_N = _SR


def _db(signal: np.ndarray) -> float:
    return 10.0 * math.log10(max(float(np.dot(signal, signal)), 1e-30))


def _sine(freq: float = 440.0, amplitude: float = 0.5) -> np.ndarray:
    return amplitude * np.sin(2.0 * np.pi * freq * np.arange(_N) / _SR)


def test_correlated_content_moves_to_center_without_build_up():
    mono = _sine()
    center, front_L, front_R = _extract_center(mono, mono.copy(), UpmixConfig(), _SR)

    assert _db(front_L) - _db(mono) < -25.0
    assert _db(front_R) - _db(mono) < -25.0

    pair_in = _db(mono) + 10.0 * math.log10(2.0)
    triple_out = 10.0 * math.log10(
        float(np.dot(center, center))
        + float(np.dot(front_L, front_L))
        + float(np.dot(front_R, front_R))
    )
    assert abs(triple_out - pair_in) < 0.5


def test_front_triple_folds_back_to_the_input_fronts():
    left = _sine()
    right = _sine(freq=660.0, amplitude=0.3) + 0.4 * left
    center, front_L, front_R = _extract_center(left, right, UpmixConfig(), _SR)

    fold_L = front_L + ITU_CENTER_COEFF * center
    fold_R = front_R + ITU_CENTER_COEFF * center
    assert _db(fold_L - left) - _db(left) < -100.0
    assert _db(fold_R - right) - _db(right) < -100.0


def test_uncorrelated_fronts_stay_in_the_fronts():
    rng = np.random.default_rng(20260817)
    left = rng.standard_normal(_N) * 0.2
    right = rng.standard_normal(_N) * 0.2
    center, front_L, front_R = _extract_center(left, right, UpmixConfig(), _SR)

    assert _db(center) - _db(left) < -9.0
    assert abs(_db(front_L) - _db(left)) < 1.5
    assert abs(_db(front_R) - _db(right)) < 1.5


def test_hard_panned_content_does_not_bleed_into_center():
    left = _sine()
    right = np.zeros(_N)
    center, front_L, front_R = _extract_center(left, right, UpmixConfig(), _SR)

    assert _db(center) - _db(left) < -60.0
    assert _db(front_L - left) - _db(left) < -100.0
    assert _db(front_R) - _db(left) < -60.0


def test_existing_center_input_passes_through_untouched():
    mono = _sine()
    rng = np.random.default_rng(1)
    inputs = {
        ChannelLabel.FL: mono,
        ChannelLabel.FR: mono * 0.8,
        ChannelLabel.C: mono * 0.3,
        ChannelLabel.LFE: mono * 0.1,
        ChannelLabel.SL: rng.standard_normal(_N) * 0.1,
        ChannelLabel.SR: rng.standard_normal(_N) * 0.1,
    }
    upmixer = MultichannelUpmixer(
        UpmixConfig(output_format="7.1.4"),
        FORMAT_MAP["7.1.4"],
        _SR,
    )
    output = upmixer.process(inputs)

    for label, source in inputs.items():
        assert np.array_equal(output[label.value], source)


def test_derived_lfe_uses_the_original_fronts():
    mono = _sine(freq=60.0)
    upmixer = MultichannelUpmixer(
        UpmixConfig(output_format="5.1"),
        FORMAT_MAP["5.1"],
        _SR,
    )
    output = upmixer.process({ChannelLabel.FL: mono, ChannelLabel.FR: mono.copy()})

    # Fronts are near-silent after extraction; a residual-fed LFE would sit
    # ~30 dB below this.
    assert _db(output["LFE"]) - _db(mono) > -12.0
