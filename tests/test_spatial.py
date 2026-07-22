import numpy as np

from upmixer.analysis.spatial import SpatialPlan, analyze_spatial_plan
from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP, INPUT_5_1
from upmixer.routing.channel_router import ChannelRouter
from upmixer.decomposition.direct_ambient import SoftMatrixResult
from upmixer.upmix.multichannel import MultichannelUpmixer


def _plan(profile: str, n: int = 4) -> SpatialPlan:
    return SpatialPlan(
        profile, 1.0, 100,
        np.ones(n), np.ones(n), np.ones(n), np.ones(n), np.ones(n),
    )


def test_explicit_spatial_profile_is_preserved():
    signal = np.sin(2 * np.pi * 440 * np.arange(48_000) / 48_000)
    plan = analyze_spatial_plan(signal, signal, 48_000, UpmixConfig(spatial_profile="detailed"))

    assert plan.profile == "detailed"
    assert plan.confidence == 1.0


def test_detail_auxiliary_gain_never_exceeds_three_db():
    plan = _plan("detailed")
    controls = plan.controls_at(0)

    assert 1.0 + 0.4125 * controls["detail"] <= 10 ** (3.0 / 20.0)


def test_realtime_transients_and_harmonics_hold_side_signal_front():
    cfg = UpmixConfig(output_format="5.1", auto_fft_size=False)
    router = ChannelRouter(cfg, 48_000, 32)
    signal = np.ones(32, dtype=np.complex128)
    direct = SoftMatrixResult(signal, signal, signal, signal, -signal, signal, signal,
                              np.ones(32), np.zeros(32), np.zeros(32))
    protected = SoftMatrixResult(signal, signal, signal, signal, -signal, signal, signal,
                                 np.ones(32), np.ones(32), np.ones(32))

    open_send = router.route_frame(direct, signal)["SL"]
    held_send = router.route_frame(protected, signal)["SL"]

    assert np.sum(np.abs(open_send) ** 2) > 100 * np.sum(np.abs(held_send) ** 2) + 1e-20


def test_realtime_transient_gate_min_remains_audible():
    cfg = UpmixConfig(output_format="5.1", auto_fft_size=False, transient_gate_min=0.25)
    router = ChannelRouter(cfg, 48_000, 32)
    signal = np.ones(32, dtype=np.complex128)
    transient = SoftMatrixResult(signal, signal, signal, signal, -signal, signal, signal,
                                 np.ones(32), np.ones(32), np.zeros(32))

    output = router.route_frame(transient, signal)["SL"]
    assert np.sum(np.abs(output) ** 2) > 0.0


def test_content_mix_strength_controls_realtime_diffuse_mask():
    signal = np.ones(32, dtype=np.complex128)
    decomp = SoftMatrixResult(signal, signal, signal, signal, -signal, signal, signal,
                              np.ones(32), np.zeros(32), np.ones(32))
    conservative = ChannelRouter(UpmixConfig(output_format="5.1", auto_fft_size=False,
                                               content_mix_strength=0.0), 48_000, 32)
    content_aware = ChannelRouter(UpmixConfig(output_format="5.1", auto_fft_size=False,
                                                content_mix_strength=1.0), 48_000, 32)

    assert np.sum(np.abs(conservative.route_frame(decomp, signal)["SL"]) ** 2) > 0.0
    assert np.sum(np.abs(content_aware.route_frame(decomp, signal)["SL"]) ** 2) == 0.0


def test_content_hf_analysis_controls_height_detail_send():
    signal = np.ones(2049, dtype=np.complex128)
    decomp = SoftMatrixResult(signal, signal, signal, signal, -signal, signal, signal,
                              np.ones(2049), np.zeros(2049), np.zeros(2049))
    low = ChannelRouter(UpmixConfig(output_format="5.1.2", auto_fft_size=False,
                                    content_hf_analysis_hz=100.0), 48_000, 2049)
    high = ChannelRouter(UpmixConfig(output_format="5.1.2", auto_fft_size=False,
                                     content_hf_analysis_hz=10_000.0), 48_000, 2049)

    low_energy = np.sum(np.abs(low.route_frame(decomp, signal)["TFL"]) ** 2)
    high_energy = np.sum(np.abs(high.route_frame(decomp, signal)["TFL"]) ** 2)
    assert low_energy > high_energy


def test_multichannel_spatial_motion_does_not_change_input_channels():
    n = 800
    inputs = {
        ChannelLabel.FL: np.linspace(-0.2, 0.2, n),
        ChannelLabel.FR: np.linspace(0.2, -0.2, n),
        ChannelLabel.C: np.full(n, 0.1),
        ChannelLabel.LFE: np.full(n, 0.05),
        ChannelLabel.SL: np.full(n, 0.03),
        ChannelLabel.SR: np.full(n, -0.03),
    }
    upmixer = MultichannelUpmixer(UpmixConfig(output_format="7.1.4"), INPUT_5_1, FORMAT_MAP["7.1.4"], 48_000)
    output = upmixer.process(inputs, _plan("spacious", n=8))

    for label, source in inputs.items():
        np.testing.assert_allclose(output[label.value], source)
