import io
import sys
import time
import types

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.separation import separation_capability
from upmixer_web.settings import Settings
from upmixer_web.storage import LocalObjectStorage


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


def test_local_storage_rejects_parent_path(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="relative path"):
        storage.local_path("../escape.wav")


def test_album_import_preview_and_paused_job(web_client):
    response = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("01.wav", _wav_bytes(), "audio/wav")),
            ("files", ("02.wav", _wav_bytes(550.0), "audio/wav")),
            ("relative_paths", (None, "Example Album/01.wav")),
            ("relative_paths", (None, "Example Album/02.wav")),
        ],
    )
    assert response.status_code == 201
    imported = response.json()
    assert imported["kind"] == "album"
    assert imported["title"] == "Example Album"
    assert [asset["position"] for asset in imported["assets"]] == [0, 1]
    assert all(asset["audio_url"] for asset in imported["assets"])

    audio_url = imported["assets"][0]["audio_url"]
    audio = web_client.get(audio_url)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")
    partial = web_client.get(audio_url, headers={"Range": "bytes=0-15"})
    assert partial.status_code == 206
    assert partial.content == audio.content[:16]
    assert web_client.get(
        audio_url.replace(imported["id"], "wrong-import", 1)
    ).status_code == 404

    manifest = {
        "version": "1.0.0",
        "engine": {"mode": "realtime"},
        "mixing": {"channel_layout": "5.1"},
        "format": {"type": "wav", "subtype": "PCM_24", "sample_rate": 48000},
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Album master",
        "manifest": manifest,
        "start": False,
    })
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "paused"
    assert len(job["tracks"]) == 2

    clone = web_client.post(f"/api/v1/jobs/{job['id']}/clone", json={"start": False})
    assert clone.status_code == 201
    assert clone.json()["source_job_id"] == job["id"]
    assert all(track["asset"]["audio_url"] for track in job["tracks"])


def test_configuration_lists_every_stem_and_runtime_capability(web_client):
    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    configuration = response.json()
    assert configuration["choices"]["stems"] == [
        "Vocals", "Bass", "Drums", "Guitar", "Piano", "Other",
        "Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash", "Crowd",
        "Lead Vocals", "Backing Vocals",
    ]
    assert "vocal-presence" in configuration["choices"]["stem_eq_profiles"]
    capability = configuration["capabilities"]["stem_separation"]
    assert isinstance(capability["available"], bool)
    assert isinstance(capability["accelerated"], bool)
    assert isinstance(capability["accelerator_detected"], bool)
    assert capability["accelerator_issue"] is None or isinstance(
        capability["accelerator_issue"],
        str,
    )
    assert capability["platform"]


def test_capability_uses_audio_separator_selected_device(tmp_path, monkeypatch):
    class FakeSeparator:
        torch_device = "mps"

        def __init__(self, **_kwargs):
            pass

    package = types.ModuleType("audio_separator")
    module = types.ModuleType("audio_separator.separator")
    module.Separator = FakeSeparator
    package.separator = module
    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setitem(sys.modules, "audio_separator", package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", module)

    capability = separation_capability(tmp_path)

    assert capability["available"]
    assert capability["backend"] == "mps"
    assert capability["accelerated"]


def test_capability_rejects_unsupported_roformer_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("upmixer_web.separation.sys.version_info", (3, 14, 0))
    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
        lambda _name: pytest.fail("audio-separator must not load on Python 3.14"),
    )

    capability = separation_capability(tmp_path)

    assert not capability["available"]
    assert capability["install_message"] == (
        "Stem separation is unavailable on Python 3.14 or newer. "
        "Use Python 3.11, 3.12, or 3.13."
    )


def test_stem_jobs_fail_before_queue_when_dependency_is_missing(
    web_client,
    monkeypatch,
):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()

    def unavailable(_manifest, _capability):
        raise ValueError("Stem separation is unavailable")

    monkeypatch.setattr(
        "upmixer_web.api.ensure_stem_separation_available",
        unavailable,
    )
    payload = {
        "import_id": imported["id"],
        "name": "Stem master",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": "5.1"},
        },
        "start": True,
    }
    response = web_client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "Stem separation is unavailable"

    payload["start"] = False
    paused = web_client.post("/api/v1/jobs", json=payload)
    assert paused.status_code == 201
    resume = web_client.post(f"/api/v1/jobs/{paused.json()['id']}/resume")
    assert resume.status_code == 422


def test_realtime_job_completes_and_downloads(web_client):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Tone master",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "realtime"},
            "mixing": {
                "channel_layout": "5.1",
                "spatial": {"profile": "balanced", "intensity": 0.5, "preanalyze": False},
            },
            "mastering": {"loudness": {"normalize": False}},
            "format": {"type": "wav", "subtype": "PCM_24", "sample_rate": 48000},
        },
        "start": True,
    })
    assert response.status_code == 201
    job_id = response.json()["id"]

    deadline = time.monotonic() + 10
    job = None
    while time.monotonic() < deadline:
        job = web_client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job is not None
    assert job["status"] == "completed", job.get("error")
    assert job["progress"] == 1.0
    artifact = job["artifacts"][0]
    download = web_client.get(artifact["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/wav")
    assert len(download.content) > 44


def test_mastering_reference_upload_runs_and_rejects_client_path(web_client):
    imported = web_client.post(
        "/api/v1/imports",
        files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
        data={"relative_paths": "tone.wav"},
    ).json()
    reference = web_client.post(
        f"/api/v1/imports/{imported['id']}/mastering-references",
        files={"file": ("reference.wav", _wav_bytes(660.0), "audio/wav")},
    )
    assert reference.status_code == 201
    reference_data = reference.json()
    assert reference_data["filename"] == "reference.wav"
    assert reference_data["channels"] == 2

    manifest = {
        "version": "1.0.0",
        "engine": {"mode": "realtime"},
        "mixing": {"channel_layout": "5.1"},
        "mastering": {
            "loudness": {"normalize": False},
            "match_reference": {
                "strength": 0.5,
                "spectrum": True,
                "rms": True,
                "max_db": 8.0,
            },
        },
        "format": {"type": "wav", "subtype": "PCM_24", "sample_rate": 48000},
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Reference master",
        "manifest": manifest,
        "mastering_reference_id": reference_data["id"],
        "start": True,
    })
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert response.json()["mastering_reference"]["id"] == reference_data["id"]
    assert "path" not in response.json()["manifest"]["mastering"]["match_reference"]

    deadline = time.monotonic() + 10
    job = None
    while time.monotonic() < deadline:
        job = web_client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job is not None
    assert job["status"] == "completed", job.get("error")

    manifest["mastering"]["match_reference"]["path"] = "/unsafe/reference.wav"
    rejected = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Unsafe reference",
        "manifest": manifest,
        "start": False,
    })
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == (
        "mastering.match_reference.path is managed by reference upload"
    )
