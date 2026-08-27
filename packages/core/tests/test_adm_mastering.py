"""Renderer-aware mastering checks for one-bed-plus-objects ADM exports."""
from __future__ import annotations

import struct
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.io.adm_writer import AdmObject, render_adm_programme
from upmixer.loudness import measure_integrated_loudness, measure_true_peak
from upmixer.mastering import MasteringChain
from upmixer.separation.stem_pipeline import StemUpmixPipeline

_SR = 48_000
_FMT = FORMAT_MAP["5.1"]


def _bed(n_samples: int) -> dict[str, np.ndarray]:
    return {label.value: np.zeros(n_samples) for label in _FMT.channels}


def test_adm_loudness_uses_the_rendered_bed_and_object_programme():
    n_samples = 4 * _SR
    time = np.arange(n_samples) / _SR
    bed = _bed(n_samples)
    bed["FL"] = 0.02 * np.sin(2.0 * np.pi * 440.0 * time)
    obj = AdmObject(
        "Vocals",
        0.1 * np.sin(2.0 * np.pi * 997.0 * time),
        (0.0, 1.0, 0.0),
    )
    linked = {"0": obj.audio}

    def render(channels):
        return render_adm_programme(
            channels, _FMT, [replace(obj, audio=linked["0"])]
        )

    pre_lkfs = measure_integrated_loudness(render(bed), _SR, _FMT)
    mastered, result = MasteringChain(UpmixConfig(
        output_format="5.1",
        output_type="adm-bwf",
        loudness_target_lkfs=-18.0,
        mastering_clip_enabled=False,
    )).process(bed, _SR, _FMT, linked, render)
    rendered = render(mastered)

    assert result.applied_gain_db == pytest.approx(-18.0 - pre_lkfs, abs=1e-6)
    assert result.measured_lkfs == pytest.approx(-18.0, abs=0.05)
    assert result.measured_lkfs == pytest.approx(
        measure_integrated_loudness(rendered, _SR, _FMT), abs=1e-6
    )


def test_adm_limiter_links_bed_and_objects_to_the_rendered_peak():
    n_samples = 4 * _SR
    time = np.arange(n_samples) / _SR
    signal = 0.01 * np.sin(2.0 * np.pi * 440.0 * time)
    signal[2 * _SR] = 0.8
    bed = _bed(n_samples)
    bed["C"] = signal.copy()
    obj = AdmObject("Vocals", signal.copy(), (0.0, 1.0, 0.0))
    linked = {"0": obj.audio}

    def render(channels):
        return render_adm_programme(
            channels, _FMT, [replace(obj, audio=linked["0"])]
        )

    target = measure_integrated_loudness(render(bed), _SR, _FMT)
    mastered, result = MasteringChain(UpmixConfig(
        output_format="5.1",
        output_type="adm-bwf",
        loudness_target_lkfs=target,
        loudness_max_tp=-1.0,
        mastering_clip_enabled=False,
    )).process(bed, _SR, _FMT, linked, render)
    rendered = render(mastered)

    assert result.limiter_gr_peak_db > 0.0
    assert result.measured_tp_dbtp <= -1.0
    assert result.measured_tp_dbtp == pytest.approx(
        measure_true_peak(rendered), abs=1e-6
    )
    np.testing.assert_allclose(mastered["C"], linked["0"])


def test_adm_pipeline_reports_rendered_qc_without_copying_objects_to_bed(tmp_path):
    n_samples = 4 * _SR
    time = np.arange(n_samples) / _SR
    signal = 0.1 * np.sin(2.0 * np.pi * 997.0 * time)
    source = tmp_path / "source.wav"
    output = tmp_path / "master.adm.wav"
    sf.write(source, np.column_stack([signal, signal]), _SR, subtype="FLOAT")

    def fake_execute_plan(
        get_separator, plan, sep_path, sep_sr, stage_callback=None,
        cfg=None, resume_key=None,
    ):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        return {name: audio.copy() for name in plan.requested_stems}

    pipeline = StemUpmixPipeline(UpmixConfig(
        stems=["Vocals"],
        output_format="5.1",
        output_type="adm-bwf",
        mastering_clip_enabled=False,
    ))
    with patch(
        "upmixer.separation.stem_pipeline_exec.execute_plan",
        side_effect=fake_execute_plan,
    ):
        result = pipeline.process_file(str(source), str(output))
    pipeline.close()

    delivered, _ = sf.read(output, dtype="float64", always_2d=True)
    data = output.read_bytes()
    bext = data.index(b"bext") + 8
    assert result.n_channels_out == _FMT.n_channels + 2
    assert result.measured_lkfs == pytest.approx(-18.0, abs=0.05)
    assert struct.unpack_from("<h", data, bext + 412)[0] == round(
        100.0 * result.measured_lkfs
    )
    assert struct.unpack_from("<h", data, bext + 416)[0] == round(
        100.0 * result.measured_tp_dbtp
    )
    assert np.max(np.abs(delivered[:, :_FMT.n_channels])) < 2.0 ** -21
    assert np.max(np.abs(delivered[:, _FMT.n_channels:])) > 0.01
