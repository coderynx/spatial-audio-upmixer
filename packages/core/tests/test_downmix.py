"""Stereo/mono downmix level law, including the project's height fold."""

import numpy as np
import pytest

from upmixer.separation.stem_router import fold_route_to_stereo
from upmixer.utils import ITU_CENTER_COEFF, itu_downmix_mono, itu_downmix_stereo


@pytest.fixture
def impulse() -> np.ndarray:
    return np.array([1.0, 0.0, 0.0, 0.0])


def test_front_heights_fold_onto_their_own_side(impulse):
    left, right = itu_downmix_stereo(
        {"TFL": impulse, "TFR": np.zeros_like(impulse)}, height_coeff=0.5
    )
    assert left[0] == pytest.approx(0.5)
    assert right[0] == pytest.approx(0.0)


def test_back_heights_fold_through_the_surround_coefficient(impulse):
    left, _ = itu_downmix_stereo(
        {"TBL": impulse}, surround_coeff=0.5, height_coeff=0.5
    )
    assert left[0] == pytest.approx(0.25)


def test_mono_heights_take_the_front_and_surround_routes(impulse):
    mono = itu_downmix_mono(
        {"TFL": impulse, "TBL": impulse}, surround_coeff=0.5, height_coeff=0.5
    )
    assert mono[0] == pytest.approx(0.5 * ITU_CENTER_COEFF + 0.5 * 0.5)


def test_non_height_channels_keep_the_bs775_coefficients(impulse):
    bed = {name: impulse.copy() for name in ("FL", "C", "SL", "BL")}
    left, _ = itu_downmix_stereo(bed, surround_coeff=0.7071)
    want = 1.0 + ITU_CENTER_COEFF + 0.7071 + 0.7071 * ITU_CENTER_COEFF
    assert left[0] == pytest.approx(want)


def test_zero_height_coefficient_reproduces_the_pre_fold_downmix(impulse):
    bed = {"FL": impulse.copy(), "TFL": impulse.copy(), "TBL": impulse.copy()}
    left, _ = itu_downmix_stereo(bed, height_coeff=0.0)
    assert left[0] == pytest.approx(1.0)
    assert itu_downmix_mono(bed, height_coeff=0.0)[0] == pytest.approx(ITU_CENTER_COEFF)


def test_render_and_downmix_paths_agree_that_heights_are_kept(impulse):
    """Both stereo paths carry height content; only the law differs."""
    route = fold_route_to_stereo({"TFL": 1.0, "TBR": 1.0})
    assert route["FL"] > 0.0 and route["FR"] > 0.0

    left, right = itu_downmix_stereo({"TFL": impulse, "TBR": impulse})
    assert left[0] > 0.0 and right[0] > 0.0
