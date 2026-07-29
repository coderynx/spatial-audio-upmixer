import io
import time

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


def test_capability_uses_engine_selected_device(tmp_path, monkeypatch):
    class FakeStemSeparator:
        def __init__(self, **_kwargs):
            pass

        @property
        def backend(self):
            return "mps"

    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "upmixer.separation.separator.StemSeparator", FakeStemSeparator,
    )

    capability = separation_capability(tmp_path)

    assert capability["available"]
    assert capability["backend"] == "mps"
    assert capability["accelerated"]


def test_capability_rejects_unsupported_torch_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("upmixer_web.separation.sys.version_info", (3, 14, 0))
    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
        lambda _name: pytest.fail("torch must not load on Python 3.14"),
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


def test_project_seeds_stem_routing_when_client_sends_empty_dict(tmp_path, monkeypatch):
    """The web client always sends `mixing.stem_routing` (default `{}`) rather than
    omitting the key, so seeding must trigger on an empty dict, not just a missing
    one — otherwise every stem is silent in preview/export until the user manually
    applies a routing preset."""
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
            "name": "Seeded routing",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals", "Bass"]},
                "mixing": {"channel_layout": "7.1.4", "stem_routing": {}},
            },
            "scene": {},
        })
        assert response.status_code == 201
        stem_routing = response.json()["manifest"]["mixing"]["stem_routing"]
        assert stem_routing.get("Vocals")
        assert stem_routing.get("Bass")


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


def test_project_view_exposes_versioned_peaks_url_and_serves_the_envelope(tmp_path, monkeypatch):
    import json

    import numpy as np

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectTrack
    from upmixer_web.project_storage import PEAK_BINS, PEAKS_SCHEMA

    database_url = f"sqlite:///{tmp_path / 'peaks-view.db'}"
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
        project = Project(import_batch=batch, name="Peaks project", manifest={}, stem_generation=4)
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.commit()
        project_id, track_id = project.id, track.id
    engine.dispose()

    directory = tmp_path / "project-stems" / project_id / track_id
    directory.mkdir(parents=True)
    payload = np.zeros((2 * PEAK_BINS, 2), dtype=np.int8).tobytes()
    (directory / "peaks.bin").write_bytes(payload)
    (directory / "peaks.json").write_text(json.dumps({
        "schema": PEAKS_SCHEMA, "bins": PEAK_BINS, "generation": 4,
        "duration_seconds": 12.5, "stems": ["Vocals", "Drums"],
    }), encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        body = client.get(f"/api/v1/projects/{project_id}").json()
        track_view = body["tracks"][0]
        assert track_view["peaks_url"] == (
            f"/api/v1/projects/{project_id}/tracks/{track_id}/peaks?v=4"
        )
        assert track_view["peaks_bins"] == PEAK_BINS
        assert track_view["peaks_stem_keys"] == ["Vocals", "Drums"]
        assert track_view["peaks_duration_seconds"] == 12.5

        served = client.get(f"/api/v1/projects/{project_id}/tracks/{track_id}/peaks")
        assert served.status_code == 200
        assert served.content == payload


def test_project_peaks_returns_404_when_the_envelope_is_missing(tmp_path, monkeypatch):
    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'peaks-missing.db'}"
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
        project = Project(import_batch=batch, name="Peaks project", manifest={})
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.commit()
        project_id, track_id = project.id, track.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/projects/{project_id}").json()["tracks"][0]["peaks_url"] is None
        assert client.get(f"/api/v1/projects/{project_id}/tracks/{track_id}/peaks").status_code == 404
        assert client.get(f"/api/v1/projects/{project_id}/tracks/nope/peaks").status_code == 404


def test_schedule_peaks_coalesces_repeat_calls_into_one_run(tmp_path, monkeypatch):
    import threading

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack
    from upmixer_web.project_storage import ProjectStemStorage
    from upmixer_web.storage import LocalObjectStorage, StorageAudioSink, StorageAudioSource
    from upmixer_web.worker import WorkerManager

    database_url = f"sqlite:///{tmp_path / 'peaks-schedule.db'}"
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
            import_batch=batch, name="Peaks project", manifest={},
            status="ready", prepared_stems=["Vocals"], stem_generation=1,
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

    storage = LocalObjectStorage(tmp_path / "objects")
    manager = WorkerManager(
        sessions=factory, storage=storage,
        source=StorageAudioSource(storage), sink=StorageAudioSink(storage),
        work_root=tmp_path / "work", stem_cache_dir=tmp_path / "cache",
        project_stems=ProjectStemStorage(tmp_path / "project-stems"), worker_count=1,
    )
    manager.start()
    try:
        release = threading.Event()
        runs = []

        def blocking_prepare(_self, pid):
            runs.append(pid)
            release.wait(5)

        monkeypatch.setattr(WorkerManager, "prepare_peaks", blocking_prepare)
        for _ in range(5):
            manager.schedule_peaks(project_id)
        assert manager.peaks_pending(project_id)
        release.set()
        manager._peaks_executor.shutdown(wait=True)
        # Five calls while one run is in flight collapse into that run plus a
        # single trailing one, never one run per call.
        assert len(runs) <= 2
    finally:
        manager.stop()


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


def test_worker_prepare_reference_match_computes_and_serves_fir(tmp_path, monkeypatch):
    """WorkerManager.prepare_reference_match precomputes the FIR + RMS-gain
    asset a project's reference-match preview needs (see
    docs/contracts/preview_export_parity.md Ledger D12), skips recompute when
    its signature is unchanged, and clears the asset when the reference is
    removed. Separation is mocked (see _fake_execute_plan) since only the
    hook plumbing and storage/signature logic are under test here — the
    algorithm itself is covered by test_match_reference.py."""
    from unittest.mock import patch

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    def _fake_execute_plan(_self, plan, sep_path, sep_sr, stage_callback=None):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        n = len(audio)
        return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}

    with TestClient(create_app(settings)) as client:
        storage = client.app.state.storage
        source_wav = tmp_path / "source.wav"
        reference_wav = tmp_path / "reference.wav"
        samples = np.arange(48_000 * 2) / 48_000
        sf.write(source_wav, 0.2 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        sf.write(reference_wav, 0.4 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        storage.put_file("assets/source.wav", source_wav)
        storage.put_file("references/reference.wav", reference_wav)

        with factory() as session:
            batch = ImportBatch(kind="track", title="Song")
            asset = MediaAsset(
                import_batch=batch, filename="source.wav", relative_path="source.wav",
                storage_key="assets/source.wav", sha256="0" * 64, size_bytes=1,
            )
            reference = MasteringReference(
                import_batch=batch, filename="reference.wav",
                storage_key="references/reference.wav", sha256="1" * 64, size_bytes=1,
            )
            manifest = {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
                "mastering": {
                    "match_reference": {
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 12.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager
        project_stems = client.app.state.project_stems

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _fake_execute_plan,
        ):
            manager.prepare_reference_match(project_id)

        meta = project_stems.read_reference_match_meta(project_id)
        assert meta is not None
        assert meta["channels"], "spectral FIRs should be present with spectrum=True, strength=1.0"
        assert project_stems.reference_match_fir_path(project_id) is not None
        first_signature = meta["signature"]

        view = client.get(f"/api/v1/projects/{project_id}").json()
        assert view["reference_match"] is not None
        assert view["reference_match"]["fir_url"]
        # The URL is versioned with the asset's signature (see
        # `_project_view` in upmixer_web/api.py) so the browser's
        # fir_url-keyed decode cache (useStemPreview.ts's
        # refMatchBufferCache) is naturally busted on a real recompute
        # instead of serving a stale FIR for the AudioContext's lifetime.
        assert f"v={first_signature}" in view["reference_match"]["fir_url"]
        fir_response = client.get(view["reference_match"]["fir_url"])
        assert fir_response.status_code == 200
        assert fir_response.headers["content-type"].startswith("audio/")

        call_count = {"n": 0}

        def _counting_execute_plan(self_inner, plan, sep_path, sep_sr, stage_callback=None):
            call_count["n"] += 1
            return _fake_execute_plan(self_inner, plan, sep_path, sep_sr, stage_callback)

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _counting_execute_plan,
        ):
            manager.prepare_reference_match(project_id)
        assert call_count["n"] == 0, "unchanged signature must not recompute"
        assert project_stems.read_reference_match_meta(project_id)["signature"] == first_signature

        # strength/rms are live preview-only knobs (wet/dry blend, gate) that
        # never change the FIR bytes or rms_gain_db — hashing them into the
        # signature would force a full recompute on every strength-slider
        # drag (the reported CPU-storm bug). Confirm they're excluded.
        with factory() as session:
            project = session.get(Project, project_id)
            project.manifest = {
                **project.manifest,
                "mastering": {
                    "match_reference": {
                        "strength": 0.1, "spectrum": True, "rms": False, "max_db": 12.0,
                    },
                },
            }
            session.commit()
        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _counting_execute_plan,
        ):
            manager.prepare_reference_match(project_id)
        assert call_count["n"] == 0, "strength/rms changes must not recompute"
        assert project_stems.read_reference_match_meta(project_id)["signature"] == first_signature

        # max_db does change the FIR — must trigger a recompute. Note this
        # doesn't re-invoke `_execute_plan`: the mix pass hits the same
        # `StemCache` entry the first run wrote (separation-affecting config
        # is untouched), which is the whole point of the cache — only the
        # mastering-adjacent hook (PSD/FIR + RMS gain) actually depends on
        # `max_db`, so a changed signature (not a call count) is the correct
        # signal that a real recompute happened rather than an early return.
        with factory() as session:
            project = session.get(Project, project_id)
            project.manifest = {
                **project.manifest,
                "mastering": {
                    "match_reference": {
                        "strength": 0.1, "spectrum": True, "rms": False, "max_db": 6.0,
                    },
                },
            }
            session.commit()
        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _counting_execute_plan,
        ):
            manager.prepare_reference_match(project_id)
        new_signature = project_stems.read_reference_match_meta(project_id)["signature"]
        assert new_signature != first_signature

        # The served fir_url must change with the signature, so the
        # browser's cache treats this as a different asset.
        view = client.get(f"/api/v1/projects/{project_id}").json()
        assert f"v={new_signature}" in view["reference_match"]["fir_url"]

        with factory() as session:
            project = session.get(Project, project_id)
            project.mastering_reference_id = None
            session.commit()
        manager.prepare_reference_match(project_id)
        assert project_stems.read_reference_match_meta(project_id) is None

    engine.dispose()


def test_worker_schedule_reference_match_coalesces_and_reports_pending(tmp_path, monkeypatch):
    """schedule_reference_match must not run the heavy mix+PSD pass inline on
    the caller's thread — that was the CPU-storm bug: settings saves are
    debounced at only 350ms in the browser, so dragging the reference-match
    strength/max_db slider used to launch a fresh full-song pass on the API
    request thread for every tick. Rapid repeat calls made while a run is
    already in flight must coalesce into a single trailing recompute rather
    than stacking up concurrent passes, and reference_match_pending must
    report True for the whole queued-or-running window so the frontend keeps
    polling until the asset lands."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-schedule.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    release = threading.Event()
    call_count = {"n": 0}

    def _blocking_execute_plan(_self, plan, sep_path, sep_sr, stage_callback=None):
        call_count["n"] += 1
        release.wait(timeout=5)
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        n = len(audio)
        return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}

    with TestClient(create_app(settings)) as client:
        storage = client.app.state.storage
        source_wav = tmp_path / "source.wav"
        reference_wav = tmp_path / "reference.wav"
        samples = np.arange(48_000 * 2) / 48_000
        sf.write(source_wav, 0.2 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        sf.write(reference_wav, 0.4 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        storage.put_file("assets/source.wav", source_wav)
        storage.put_file("references/reference.wav", reference_wav)

        with factory() as session:
            batch = ImportBatch(kind="track", title="Song")
            asset = MediaAsset(
                import_batch=batch, filename="source.wav", relative_path="source.wav",
                storage_key="assets/source.wav", sha256="0" * 64, size_bytes=1,
            )
            reference = MasteringReference(
                import_batch=batch, filename="reference.wav",
                storage_key="references/reference.wav", sha256="1" * 64, size_bytes=1,
            )
            manifest = {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
                "mastering": {
                    "match_reference": {
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 12.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match schedule project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager
        # start() is mocked out above so the real dispatch/job pools never
        # spin up; create just the refmatch executor start() would normally
        # create, so schedule_reference_match has somewhere to submit to.
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch")
        try:
            with patch(
                "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
                _blocking_execute_plan,
            ):
                manager.schedule_reference_match(project_id)
                assert manager.reference_match_pending(project_id)
                # Fired while the first pass is still blocked in _execute_plan
                # — must coalesce into the trailing check rather than each
                # starting a concurrent full-song pass.
                manager.schedule_reference_match(project_id)
                manager.schedule_reference_match(project_id)
                release.set()
                deadline = time.monotonic() + 5
                while manager.reference_match_pending(project_id) and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert not manager.reference_match_pending(project_id)
        finally:
            manager._refmatch_executor.shutdown(wait=True)

        # One real pass, plus one coalesced trailing check that no-ops
        # immediately (signature unchanged) — never one pass per call.
        assert call_count["n"] == 1
        assert client.app.state.project_stems.read_reference_match_meta(project_id) is not None

    engine.dispose()


def test_worker_schedule_reference_match_skips_pending_when_nothing_to_do(tmp_path, monkeypatch):
    """schedule_reference_match must not open a `reference_match_pending`
    window for a call that `prepare_reference_match` would provably no-op —
    that window is what drives the frontend's "Preparing reference EQ
    match…" banner (ProjectDetailPage.tsx), and every settings save used to
    open it regardless of whether anything the FIR depends on actually
    changed, flashing the banner on unrelated edits like a volume/mix tweak.
    Covers: an up-to-date signature already on disk, and no reference
    attached at all with nothing to clean up. The reference-removal cleanup
    case (asset still on disk, reference detached) is covered separately by
    test_worker_schedule_reference_match_still_runs_to_clear_removed_reference."""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-skip.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    def _fake_execute_plan(_self, plan, sep_path, sep_sr, stage_callback=None):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        n = len(audio)
        return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}

    with TestClient(create_app(settings)) as client:
        storage = client.app.state.storage
        source_wav = tmp_path / "source.wav"
        reference_wav = tmp_path / "reference.wav"
        samples = np.arange(48_000 * 2) / 48_000
        sf.write(source_wav, 0.2 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        sf.write(reference_wav, 0.4 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        storage.put_file("assets/source.wav", source_wav)
        storage.put_file("references/reference.wav", reference_wav)

        with factory() as session:
            batch = ImportBatch(kind="track", title="Song")
            asset = MediaAsset(
                import_batch=batch, filename="source.wav", relative_path="source.wav",
                storage_key="assets/source.wav", sha256="0" * 64, size_bytes=1,
            )
            reference = MasteringReference(
                import_batch=batch, filename="reference.wav",
                storage_key="references/reference.wav", sha256="1" * 64, size_bytes=1,
            )
            manifest = {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
                "mastering": {
                    "match_reference": {
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 12.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match skip project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch-skip")
        try:
            # Populate an up-to-date asset first, exactly like a real settings
            # save that changed something reference-match-relevant would.
            with patch(
                "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
                _fake_execute_plan,
            ):
                manager.prepare_reference_match(project_id)
            assert manager.project_stems.read_reference_match_meta(project_id) is not None

            # Case 1: signature already up to date (e.g. a mixing/volume edit,
            # which `_reference_match_signature` deliberately excludes) — must
            # not open the pending window at all.
            manager.schedule_reference_match(project_id)
            assert not manager.reference_match_pending(project_id)

            # Case 2: no reference attached, and nothing on disk to clear.
            # Independent batch/asset/project rather than reusing objects
            # from a closed session.
            with factory() as session:
                other_batch = ImportBatch(kind="track", title="Song 2")
                other_asset = MediaAsset(
                    import_batch=other_batch, filename="source2.wav", relative_path="source2.wav",
                    storage_key="assets/source2.wav", sha256="2" * 64, size_bytes=1,
                )
                other_project = Project(
                    import_batch=other_batch, name="No-reference project", manifest={"version": "1.0.0"},
                    status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                )
                other_track = ProjectTrack(project=other_project, asset=other_asset, position=0)
                session.add_all([other_batch, other_asset, other_project, other_track])
                session.commit()
                other_project_id = other_project.id

            manager.schedule_reference_match(other_project_id)
            assert not manager.reference_match_pending(other_project_id)
        finally:
            manager._refmatch_executor.shutdown(wait=True)

    engine.dispose()


def test_worker_schedule_reference_match_still_runs_to_clear_removed_reference(tmp_path, monkeypatch):
    """When a reference is detached but a prior FIR asset is still on disk,
    schedule_reference_match must still schedule a run — `_reference_match_
    needs_work`'s no-reference branch must not blanket-skip, since that run
    is what actually clears the stale asset (see prepare_reference_match's
    `target_signature is None` branch)."""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-clear.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    def _fake_execute_plan(_self, plan, sep_path, sep_sr, stage_callback=None):
        audio, _ = sf.read(sep_path, dtype="float32", always_2d=True)
        n = len(audio)
        return {name: np.full((n, 2), 0.2, dtype=np.float32) for name in plan.requested_stems}

    with TestClient(create_app(settings)) as client:
        storage = client.app.state.storage
        source_wav = tmp_path / "source.wav"
        reference_wav = tmp_path / "reference.wav"
        samples = np.arange(48_000 * 2) / 48_000
        sf.write(source_wav, 0.2 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        sf.write(reference_wav, 0.4 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        storage.put_file("assets/source.wav", source_wav)
        storage.put_file("references/reference.wav", reference_wav)

        with factory() as session:
            batch = ImportBatch(kind="track", title="Song")
            asset = MediaAsset(
                import_batch=batch, filename="source.wav", relative_path="source.wav",
                storage_key="assets/source.wav", sha256="0" * 64, size_bytes=1,
            )
            reference = MasteringReference(
                import_batch=batch, filename="reference.wav",
                storage_key="references/reference.wav", sha256="1" * 64, size_bytes=1,
            )
            manifest = {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
                "mastering": {
                    "match_reference": {
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 12.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match clear project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch-clear")
        try:
            with patch(
                "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
                _fake_execute_plan,
            ):
                manager.prepare_reference_match(project_id)
            assert manager.project_stems.read_reference_match_meta(project_id) is not None

            with factory() as session:
                project = session.get(Project, project_id)
                project.mastering_reference_id = None
                session.commit()

            manager.schedule_reference_match(project_id)
            deadline = time.monotonic() + 5
            while manager.reference_match_pending(project_id) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert manager.project_stems.read_reference_match_meta(project_id) is None
        finally:
            manager._refmatch_executor.shutdown(wait=True)

    engine.dispose()


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
