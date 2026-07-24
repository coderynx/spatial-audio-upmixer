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

    accepted = {
        "version": "1.0.0",
        "format": {
            "downmix": {
                "enabled": False,
                "output": None,
                "surround_coeff": 0.7071,
            },
        },
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Null downmix output",
        "manifest": accepted,
    })
    assert response.status_code == 201

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


def test_project_lifecycle_persists_settings_and_expansion(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    monkeypatch.setattr("upmixer_web.api.ensure_stem_separation_available", lambda *_args: None)
    with TestClient(create_app(settings)) as client:
        imported = client.post(
            "/api/v1/imports",
            files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
            data={"relative_paths": "tone.wav"},
        ).json()
        response = client.post("/api/v1/projects", json={
            "import_id": imported["id"],
            "name": "Editable master",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "realtime", "stems": ["Vocals", "Drums", "Kick"]},
                "mixing": {"channel_layout": "7.1.4"},
            },
            "scene": {"stems": {"Vocals": {"azimuth_deg": 0, "elevation_deg": 0}}},
        })
        assert response.status_code == 201
        project = response.json()
        assert project["status"] == "queued"
        assert project["manifest"]["engine"]["mode"] == "stem"
        assert project["requested_stems"] == ["Vocals", "Kick"]

        saved = client.put(f"/api/v1/projects/{project['id']}/settings", json={
            "name": "Editable master v2",
            "manifest": project["manifest"],
            "scene": {"stems": {"Vocals": {"azimuth_deg": 20, "elevation_deg": 10}}},
        })
        assert saved.status_code == 200
        assert saved.json()["name"] == "Editable master v2"
        assert saved.json()["revision"] == 2

        expanded = client.post(f"/api/v1/projects/{project['id']}/stems", json={"stems": ["Bass"]})
        assert expanded.status_code == 200
        assert expanded.json()["requested_stems"] == ["Vocals", "Kick", "Bass"]


def test_project_view_builds_stem_urls_from_catalogued_stems(tmp_path, monkeypatch):
    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'stem-view.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="song.wav", relative_path="song.wav",
            storage_key="objects/song.wav", sha256="0" * 64, size_bytes=1,
        )
        project = Project(import_batch=batch, name="Preview project", manifest={})
        track = ProjectTrack(project=project, asset=asset, position=0)
        stem = ProjectStem(
            project=project, track=track, stem_key="Vocals", relative_path="a/Vocals.wav",
            sample_rate=48_000, channels=2, size_bytes=10, generation=1,
        )
        session.add_all([batch, asset, project, track, stem])
        session.commit()
        project_id = project.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/v1/projects/{project_id}")

    assert response.status_code == 200
    body = response.json()
    track_view = body["tracks"][0]
    stem_view = track_view["stems"][0]
    assert stem_view["audio_url"] == (
        f"/api/v1/projects/{project_id}/tracks/{track_view['id']}/stems/{stem_view['id']}/audio"
    )


def test_project_delete_returns_404_for_missing_project(web_client):
    response = web_client.delete("/api/v1/projects/does-not-exist")
    assert response.status_code == 404


def test_project_delete_removes_project_and_all_stem_data(tmp_path, monkeypatch):
    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'delete.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="song.wav", relative_path="song.wav",
            storage_key="objects/song.wav", sha256="0" * 64, size_bytes=1,
        )
        project = Project(
            import_batch=batch, name="Preview project", manifest={},
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
        )
        track = ProjectTrack(project=project, asset=asset, position=0)
        stem = ProjectStem(
            project=project, track=track, stem_key="Vocals", relative_path="a/Vocals.wav",
            sample_rate=48_000, channels=2, size_bytes=10, generation=1,
        )
        session.add_all([batch, asset, project, track, stem])
        session.commit()
        project_id = project.id
    engine.dispose()

    stem_dir = tmp_path / "project-stems" / project_id
    stem_dir.mkdir(parents=True)
    (stem_dir / "marker.txt").write_text("stem data", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        deleted = client.delete(f"/api/v1/projects/{project_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404

    assert not stem_dir.exists()


def test_project_delete_preserves_export_jobs_with_nulled_project_id(tmp_path, monkeypatch):
    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'delete-export.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="song.wav", relative_path="song.wav",
            storage_key="objects/song.wav", sha256="0" * 64, size_bytes=1,
        )
        manifest = {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": "5.1"},
        }
        project = Project(
            import_batch=batch, name="Preview project", manifest=manifest,
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
        )
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.commit()
        project_id = project.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        exported = client.post(f"/api/v1/projects/{project_id}/exports")
        assert exported.status_code == 201
        job_id = exported.json()["id"]

        deleted = client.delete(f"/api/v1/projects/{project_id}")
        assert deleted.status_code == 204

        job = client.get(f"/api/v1/jobs/{job_id}")
        assert job.status_code == 200
        assert job.json()["project_id"] is None
