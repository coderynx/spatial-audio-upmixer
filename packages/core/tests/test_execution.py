"""Coverage for production-safety execution helpers."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.execution import PreflightError, RunState, preflight_job
from upmixer.io.writer import AudioWriter
from upmixer.result import UpmixResult


def _result(input_path: str, output_path: str) -> UpmixResult:
    return UpmixResult(
        input_path=input_path, output_path=output_path, input_format="stereo",
        output_format="5.1", input_sample_rate=48_000, output_sample_rate=48_000,
        duration_seconds=0.1, n_channels_in=2, n_channels_out=6, mode="realtime",
    )


def test_preflight_rejects_existing_input_output_path(tmp_path):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((480, 2)), 48_000)
    with pytest.raises(PreflightError, match="different"):
        preflight_job(str(source), str(source), UpmixConfig())


def test_preflight_rejects_bad_adm_delivery(tmp_path):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((480, 2)), 44_100)
    cfg = UpmixConfig(output_type="adm-bwf", output_sample_rate=44_100)
    with pytest.raises(PreflightError, match="48 kHz or 96 kHz"):
        preflight_job(str(source), str(tmp_path / "out.wav"), cfg)


def test_run_state_matches_written_output(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "out.wav"
    sf.write(source, np.zeros((480, 2)), 48_000)
    cfg = UpmixConfig()
    plan = preflight_job(str(source), str(output), cfg)
    AudioWriter(output, 48_000, cfg).write({name: np.zeros(480) for name in ("FL", "FR", "C", "LFE", "SL", "SR")})
    state = RunState.load(tmp_path / "state.json")
    state.record(plan, _result(str(source), str(output)))
    reloaded = RunState.load(tmp_path / "state.json")
    assert reloaded.matches(plan)


def test_audio_writer_creates_parent_and_publishes_complete_file(tmp_path):
    output = tmp_path / "nested" / "out.wav"
    cfg = UpmixConfig()
    AudioWriter(output, 48_000, cfg).write({name: np.zeros(480) for name in ("FL", "FR", "C", "LFE", "SL", "SR")})
    info = sf.info(output)
    assert info.channels == 6
    assert info.samplerate == 48_000
    assert not list(output.parent.glob(".*.wav"))
