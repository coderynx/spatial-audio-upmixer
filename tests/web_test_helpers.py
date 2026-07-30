"""Shared fixtures for the ``test_web_*.py`` suite (not itself a test module)."""

import io

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings


def _wav_bytes(frequency: float = 440.0) -> bytes:
    sample_rate = 48_000
    samples = np.arange(4_800) / sample_rate
    audio = np.column_stack([
        0.1 * np.sin(2 * np.pi * frequency * samples),
        0.1 * np.sin(2 * np.pi * (frequency + 2.0) * samples),
    ])
    output = io.BytesIO()
    sf.write(output, audio, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


@pytest.fixture
def web_client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        worker_count=1,
    )
    with TestClient(create_app(settings)) as client:
        yield client
