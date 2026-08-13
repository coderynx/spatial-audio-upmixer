import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.pipeline import StreamingProcessor, UpmixPipeline, _AllPassDecorrelator
from upmixer.utils import itu_downmix_stereo


def _create_test_wav(path: str, left: np.ndarray, right: np.ndarray, sr: int):
    audio = np.column_stack([left, right])
    sf.write(path, audio, sr, subtype="PCM_24")


def test_end_to_end_51(stereo_mix, sample_rate):
    """End-to-end pipeline test producing 5.1 output."""
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="5.1", auto_fft_size=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, sr = sf.read(output_path)
        assert sr == sample_rate
        assert output.shape[1] == 6
        assert output.shape[0] == len(left)
        assert np.all(np.isfinite(output))
        assert np.max(np.abs(output)) > 0.01


def test_end_to_end_71(stereo_mix, sample_rate):
    """End-to-end pipeline test producing 7.1 output."""
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="7.1", auto_fft_size=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, sr = sf.read(output_path)
        assert sr == sample_rate
        assert output.shape[1] == 8
        assert np.all(np.isfinite(output))


def test_end_to_end_714(stereo_mix, sample_rate):
    """End-to-end pipeline test producing 7.1.4 Dolby Atmos output."""
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="7.1.4", auto_fft_size=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, sr = sf.read(output_path)
        assert sr == sample_rate
        assert output.shape[1] == 12
        assert np.all(np.isfinite(output))


def test_end_to_end_512(stereo_mix, sample_rate):
    """End-to-end pipeline test producing 5.1.2 output."""
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="5.1.2", auto_fft_size=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, sr = sf.read(output_path)
        assert output.shape[1] == 8
        assert np.all(np.isfinite(output))


def test_downmix_compatibility(stereo_mix, sample_rate):
    """Downmixing 5.1 output should correlate with the original stereo."""
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="5.1", auto_fft_size=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, _ = sf.read(output_path)
        fl = output[:, 0]
        fr = output[:, 1]
        c = output[:, 2]
        sl = output[:, 4]
        sr_ch = output[:, 5]

        coeff = 1.0 / np.sqrt(2.0)
        l_down = fl + coeff * c + coeff * sl
        r_down = fr + coeff * c + coeff * sr_ch

        def normalize(x):
            return x / (np.sqrt(np.sum(x**2)) + 1e-10)

        corr_l = np.sum(normalize(l_down) * normalize(left))
        corr_r = np.sum(normalize(r_down) * normalize(right))

        assert corr_l > 0.7, f"Left channel downmix correlation too low: {corr_l}"
        assert corr_r > 0.7, f"Right channel downmix correlation too low: {corr_r}"


def test_energy_conservation(stereo_mix, sample_rate):
    """Total output energy should approximately match input energy.

    Loudness normalization is disabled here because it applies a global gain
    to hit a LKFS target, which deliberately changes the output energy level.
    This test verifies the mixing-phase energy budget only.
    """
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="5.1", auto_fft_size=False, loudness_normalize=False)
        pipeline = UpmixPipeline(config)
        pipeline.process_file(input_path, output_path)

        output, _ = sf.read(output_path)

        input_energy = np.sum(left**2) + np.sum(right**2)
        output_energy = np.sum(output**2)

        ratio = output_energy / (input_energy + 1e-10)
        assert 0.3 < ratio < 3.0, f"Energy ratio out of range: {ratio}"


def _reference_all_pass(samples: np.ndarray, coefficient: float = 0.7) -> np.ndarray:
    """Direct per-sample reference for _AllPassDecorrelator's difference equation."""
    output = np.empty_like(samples)
    x_prev = 0.0
    y_prev = 0.0
    for i, sample in enumerate(samples):
        value = -coefficient * sample + x_prev + coefficient * y_prev
        output[i] = value
        x_prev = sample
        y_prev = value
    return output


def test_all_pass_decorrelator_matches_reference_single_shot():
    """Vectorized lfilter implementation must match the difference equation exactly."""
    rng = np.random.default_rng(42)
    samples = rng.standard_normal(4000)

    decorrelator = _AllPassDecorrelator()
    output = decorrelator.process(samples)
    expected = _reference_all_pass(samples)

    np.testing.assert_allclose(output, expected, atol=1e-12)


def test_all_pass_decorrelator_state_continuity_across_blocks():
    """Splitting input across arbitrary block boundaries must not change output."""
    rng = np.random.default_rng(7)
    samples = rng.standard_normal(2531)
    expected = _reference_all_pass(samples)

    decorrelator = _AllPassDecorrelator()
    chunks = []
    for start in range(0, len(samples), 173):
        chunks.append(decorrelator.process(samples[start:start + 173]))
    output = np.concatenate(chunks)

    np.testing.assert_allclose(output, expected, atol=1e-12)


def test_all_pass_decorrelator_reset_clears_state():
    rng = np.random.default_rng(99)
    samples = rng.standard_normal(500)

    decorrelator = _AllPassDecorrelator()
    decorrelator.process(samples)
    decorrelator.reset()
    output_after_reset = decorrelator.process(samples)
    expected = _reference_all_pass(samples)

    np.testing.assert_allclose(output_after_reset, expected, atol=1e-12)


def test_streaming_processor_flushes_delayed_tail(sample_rate):
    config = UpmixConfig(auto_fft_size=False, spatial_preanalysis=False)
    processor = StreamingProcessor(config, sample_rate)
    _, hop = config.resolve_fft_params(sample_rate)
    signal = np.sin(2 * np.pi * 440 * np.arange(hop * 2 + 17) / sample_rate)
    outputs = []
    for start in range(0, len(signal), 173):
        outputs.append(processor.process_block(signal[start:start + 173], signal[start:start + 173])["FL"])
    outputs.append(processor.flush()["FL"])

    output = np.concatenate(outputs)
    expected = ((len(signal) + hop - 1) // hop) * hop + processor.latency_samples
    assert len(output) == expected
    assert np.max(np.abs(output[processor.latency_samples:processor.latency_samples + len(signal)])) > 0.01
    assert len(processor.flush()["FL"]) == 0


def test_stereo_output_passes_a_stereo_source_through(stereo_mix, sample_rate):
    left, right = stereo_mix

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        _create_test_wav(input_path, left, right, sample_rate)

        config = UpmixConfig(output_format="stereo", loudness_normalize=False)
        UpmixPipeline(config).process_file(input_path, output_path)

        output, sr = sf.read(output_path)
        assert sr == sample_rate
        assert output.shape == (len(left), 2)
        assert np.corrcoef(output[:, 0], left)[0, 1] > 0.999
        assert np.corrcoef(output[:, 1], right)[0, 1] > 0.999


def test_stereo_output_duplicates_a_mono_source(sample_rate):
    mono = 0.2 * np.sin(2.0 * np.pi * 440.0 * np.arange(sample_rate) / sample_rate)

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        sf.write(input_path, mono, sample_rate, subtype="PCM_24")

        config = UpmixConfig(output_format="stereo", loudness_normalize=False)
        UpmixPipeline(config).process_file(input_path, output_path)

        output, _ = sf.read(output_path)
        assert output.shape == (len(mono), 2)
        assert np.allclose(output[:, 0], output[:, 1])


def test_stereo_output_folds_a_multichannel_source(sample_rate):
    n = sample_rate
    t = np.arange(n) / sample_rate
    bed = {
        "FL": 0.2 * np.sin(2.0 * np.pi * 220.0 * t),
        "FR": 0.2 * np.sin(2.0 * np.pi * 330.0 * t),
        "C": 0.2 * np.sin(2.0 * np.pi * 440.0 * t),
        "LFE": 0.2 * np.sin(2.0 * np.pi * 50.0 * t),
        "SL": 0.1 * np.sin(2.0 * np.pi * 660.0 * t),
        "SR": 0.1 * np.sin(2.0 * np.pi * 770.0 * t),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = str(Path(tmpdir) / "input.wav")
        output_path = str(Path(tmpdir) / "output.wav")
        sf.write(input_path, np.column_stack(list(bed.values())), sample_rate, subtype="PCM_24")

        config = UpmixConfig(output_format="stereo", loudness_normalize=False)
        UpmixPipeline(config).process_file(input_path, output_path)

        output, _ = sf.read(output_path)
        expected_l, expected_r = itu_downmix_stereo(bed, surround_coeff=config.surround_downmix_coeff)
        assert output.shape == (n, 2)
        assert np.corrcoef(output[:, 0], expected_l)[0, 1] > 0.999
        assert np.corrcoef(output[:, 1], expected_r)[0, 1] > 0.999
