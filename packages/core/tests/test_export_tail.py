"""The export tail: master at the delivery rate, then quantize, then nothing.

The contract these pin is stated in
``docs/standards/loudness_dsp_bs1770.md`` §"Export tail".
"""
from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.writer import AudioWriter, dither_channels, write_audio
from upmixer.manifest import ManifestError, parse_manifest, validate_manifest
from upmixer.resample import anti_imaging_fir
from upmixer.separation.stem_pipeline import StemUpmixPipeline

_SR = 48_000
_SEED = 20260819


def _fade(bits: int, amplitude_db: float, seconds: float = 4.0) -> np.ndarray:
    """A fading tone at ``amplitude_db``, the level truncation mangles."""
    n = int(seconds * _SR)
    envelope = 1.0 - np.arange(n) / n
    return (
        10.0 ** (amplitude_db / 20.0)
        * envelope
        * np.sin(2 * np.pi * 997.0 * np.arange(n) / _SR)
    )


def _error_stats(signal: np.ndarray, delivered: np.ndarray, bits: int) -> dict[str, float]:
    error = delivered - signal
    lsb = 2.0 ** -(bits - 1)
    return {
        "rms_ratio": float(np.sqrt(np.mean(error ** 2))) / (lsb / math.sqrt(12.0)),
        "dc_lsb": float(np.mean(error)) / lsb,
    }


def _round_trip(tmp_path, signal: np.ndarray, subtype: str, mode: str) -> np.ndarray:
    path = tmp_path / f"{subtype}-{mode}.wav"
    write_audio(path, signal.reshape(-1, 1), _SR, "wav_pcm", subtype, mode, _SEED)
    delivered, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return delivered[:, 0]


def test_the_stem_pipeline_masters_at_the_delivery_rate(tmp_path):
    """The stem path separates at the delivery rate, so nothing resamples the
    mastered bed. ``pre_master_hook`` sees the rate the chain will run at."""
    source = 0.2 * np.sin(2 * np.pi * 440.0 * np.arange(2 * _SR) / _SR)
    input_path = tmp_path / "in.wav"
    sf.write(str(input_path), np.column_stack([source, source]), _SR, subtype="FLOAT")

    def fake_execute_plan(get_separator, plan, sep_path, sep_sr, stage_callback=None,
                          cfg=None, resume_key=None):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        return {
            name: np.full((len(audio), 2), 0.2, dtype=np.float32)
            for name in plan.requested_stems
        }

    captured: dict[str, int] = {}
    config = UpmixConfig(stems=["Vocals"], output_format="5.1", output_sample_rate=96_000)
    pipeline = StemUpmixPipeline(config)
    with patch("upmixer.separation.stem_pipeline_exec.execute_plan", side_effect=fake_execute_plan):
        pipeline.process_file(
            str(input_path), str(tmp_path / "out.wav"),
            pre_master_hook=lambda channels, sr, fmt: captured.update(sr=sr),
        )
    pipeline.close()

    delivered = sf.info(str(tmp_path / "out.wav"))
    assert captured["sr"] == 96_000
    assert delivered.samplerate == 96_000


def test_nothing_scales_the_bed_after_the_quantizer(tmp_path):
    bed = [_fade(24, -20.0), _fade(24, -20.0) * 0.5]
    path = tmp_path / "out.wav"
    write_audio(path, np.column_stack(bed), _SR, "wav_pcm", "PCM_24", "tpdf", _SEED)
    delivered, _ = sf.read(str(path), dtype="float64", always_2d=True)
    expected = dither_channels(bed, "PCM_24", "tpdf", _SEED)
    assert np.array_equal(delivered, np.column_stack(expected))


def test_two_renders_of_the_same_job_are_byte_identical(tmp_path):
    source = 0.3 * np.sin(2 * np.pi * 220.0 * np.arange(_SR) / _SR)
    input_path = tmp_path / "in.wav"
    sf.write(str(input_path), np.column_stack([source, source * 0.7]), _SR, subtype="PCM_24")

    def fake_execute_plan(get_separator, plan, sep_path, sep_sr, stage_callback=None,
                          cfg=None, resume_key=None):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        return {name: audio.copy() for name in plan.requested_stems}

    config = UpmixConfig(stems=["Vocals"], output_format="5.1")
    rendered = []
    with patch("upmixer.separation.stem_pipeline_exec.execute_plan", side_effect=fake_execute_plan):
        for name in ("a.wav", "b.wav"):
            output = tmp_path / name
            pipeline = StemUpmixPipeline(config)
            pipeline.process_file(str(input_path), str(output))
            pipeline.close()
            rendered.append(output.read_bytes())
    assert rendered[0] == rendered[1]


def test_sixteen_bit_dither_replaces_truncation_distortion_with_a_flat_floor(tmp_path):
    """Phase 0's acceptance fixture, at the level the defect is audible."""
    signal = _fade(16, -90.0)

    raw = tmp_path / "undithered.wav"
    sf.write(str(raw), signal.reshape(-1, 1), _SR, format="WAV", subtype="PCM_16")
    truncated = _error_stats(
        signal, sf.read(str(raw), dtype="float64", always_2d=True)[0][:, 0], 16
    )
    rounded = _error_stats(signal, _round_trip(tmp_path, signal, "PCM_16", "off"), 16)
    dithered = _error_stats(signal, _round_trip(tmp_path, signal, "PCM_16", "tpdf"), 16)

    # Phase 0's two discriminators: truncation reads 2.000x the round-to-nearest
    # error RMS and carries -0.500 LSB of DC. TPDF reads √3 with neither.
    assert truncated["rms_ratio"] == pytest.approx(2.0, abs=0.05)
    assert truncated["dc_lsb"] == pytest.approx(-0.5, abs=0.02)
    assert rounded["rms_ratio"] == pytest.approx(1.0, abs=0.1)
    assert abs(rounded["dc_lsb"]) < 0.02
    assert dithered["rms_ratio"] == pytest.approx(math.sqrt(3.0), abs=0.1)
    assert abs(dithered["dc_lsb"]) < 0.02


def test_sixteen_bit_dither_removes_the_quantizer_harmonics(tmp_path):
    """A steady tone one LSB tall — the case a fade's own sweep would hide."""
    n = 4 * _SR
    signal = 10.0 ** (-90.0 / 20.0) * np.sin(2 * np.pi * 997.0 * np.arange(n) / _SR)

    def third_harmonic(mode: str) -> float:
        delivered = _round_trip(tmp_path, signal, "PCM_16", mode)
        probe = np.exp(-2j * np.pi * 3 * 997.0 * np.arange(n) / _SR)
        return 2.0 * abs(np.vdot(probe, delivered)) / n

    assert third_harmonic("tpdf") < third_harmonic("off") / 10.0


def test_twenty_four_bit_delivery_stays_within_the_dithered_lsb(tmp_path):
    signal = _fade(24, -20.0)
    lsb = 2.0 ** -23
    # The shaper's (1 - z^-1)^2 feedback can stack four LSB-scale errors.
    for mode, bound in (("off", 0.5), ("tpdf", 1.5), ("shaped", 6.0)):
        delivered = _round_trip(tmp_path, signal, "PCM_24", mode)
        assert np.max(np.abs(delivered - signal)) <= bound * lsb


def test_float_and_lossy_subtypes_are_delivered_undithered():
    signal = _fade(24, -20.0)
    for subtype in ("FLOAT", "VORBIS"):
        passed_through = dither_channels([signal], subtype, "tpdf", _SEED)
        assert np.array_equal(passed_through[0], signal)


def test_shaped_dither_moves_its_noise_out_of_the_low_band(tmp_path):
    signal = _fade(16, -20.0)
    window = np.ones(32) / 32.0

    def low_band_rms(mode: str) -> float:
        error = _round_trip(tmp_path, signal, "PCM_16", mode) - signal
        return float(np.sqrt(np.mean(np.convolve(error, window, "valid") ** 2)))

    assert low_band_rms("shaped") < low_band_rms("tpdf") / 2.0


def test_the_adm_writer_delivers_the_same_quantization(tmp_path):
    fmt = "5.1"
    config = UpmixConfig(output_format=fmt, output_type="adm-bwf")
    labels = FORMAT_MAP[fmt].channels
    channels = {
        label.value: _fade(24, -30.0) * (1.0 - 0.1 * index)
        for index, label in enumerate(labels)
    }
    output = tmp_path / "out.adm.wav"
    AdmBwfWriter(str(output), _SR, config).write(channels)

    delivered, _ = sf.read(str(output), dtype="float64", always_2d=True)
    ordered = [channels[label.value] for label in labels]
    expected = dither_channels(ordered, "PCM_24", config.output_dither, config.output_dither_seed)
    assert np.array_equal(delivered, np.column_stack(expected))


def _manifest(dither: str) -> dict:
    return {
        "version": "1.0.0",
        "format": {"subtype": "PCM_16", "dither": dither},
        "assets": [{"input": "a.wav", "output": "b.wav"}],
    }


def test_an_unknown_dither_mode_is_rejected_by_the_writer_and_the_manifest():
    with pytest.raises(ValueError, match="Unknown output_dither"):
        dither_channels([_fade(16, -20.0)], "PCM_16", "triangular", _SEED)
    with pytest.raises(ManifestError, match="format.dither"):
        validate_manifest(_manifest("triangular"))


def test_the_manifest_carries_the_dither_choice_into_the_config():
    validate_manifest(_manifest("shaped"))
    _, jobs = parse_manifest(_manifest("shaped"))
    assert jobs[0].config.get("output_dither") == "shaped"


def _tone_response(source: np.ndarray, resampled: np.ndarray, hz: float) -> tuple[str, str]:
    """Passband error and worst spurious level of one resampled tone.

    Trims 0.5 s of filter transient from each end and reads 3 s, so every
    integer tone frequency lands on an exact bin and the measured floor is the
    resampler's rather than the analysis window's.
    """
    trim, window = _SR // 2, 3 * _SR
    magnitude = np.abs(np.fft.rfft(resampled[trim:trim + window])) * 2.0 / window
    bin_index = round(hz * window / _SR)
    keep = magnitude[bin_index] if bin_index < len(magnitude) else 0.0
    magnitude[max(0, bin_index - 2):bin_index + 3] = 0.0
    magnitude[:3] = 0.0
    passband = "—" if hz * 2 > _SR else f"{20 * math.log10(max(keep, 1e-30)):+.3f}"
    return passband, f"{20 * math.log10(max(float(np.max(magnitude)), 1e-30)):.1f}"


@pytest.mark.perf
def test_audit_sample_rate_conversion_quality() -> None:
    """Audit 5 — the delivery resampler, scipy's default against ours."""
    header = (
        "source", "tone Hz", "default pass dB", "default worst dBFS",
        "upgraded pass dB", "upgraded worst dBFS",
    )
    rows = []
    for src_sr, tones in ((44_100, (100, 1_000, 5_000, 10_000, 15_000, 19_000, 20_000)),
                          (96_000, (100, 1_000, 10_000, 20_000, 23_000, 30_000, 40_000))):
        divisor = math.gcd(_SR, src_sr)
        up, down = _SR // divisor, src_sr // divisor
        fir = anti_imaging_fir(up, down)
        for hz in tones:
            source = np.sin(2 * np.pi * hz * np.arange(4 * src_sr) / src_sr)
            rows.append((
                f"{src_sr // 100 / 10:g} kHz", f"{hz}",
                *_tone_response(source, resample_poly(source, up, down), hz),
                *_tone_response(source, resample_poly(source, up, down, window=fir), hz),
            ))

    print("\n### Audit 5 — delivery resampling to 48 kHz\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(value) for value in row) + " |")


def test_the_writer_defaults_to_tpdf_on_an_integer_subtype(tmp_path):
    config = UpmixConfig(output_format="stereo")
    assert config.output_dither == "tpdf"
    signal = _fade(24, -60.0)
    output = tmp_path / "out.wav"
    AudioWriter(output, _SR, config).write({"FL": signal, "FR": signal})
    delivered, _ = sf.read(str(output), dtype="float64", always_2d=True)
    assert not np.array_equal(delivered[:, 0], delivered[:, 1])
    assert np.max(np.abs(delivered[:, 0] - signal)) <= 1.5 * 2.0 ** -23
