import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings

from tests.web_test_helpers import _wav_bytes, web_client


def test_project_lifecycle_persists_settings_and_expansion(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    monkeypatch.setattr("upmixer_web.routes_projects.ensure_stem_separation_available", lambda *_args: None)
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
    monkeypatch.setattr("upmixer_web.routes_projects.ensure_stem_separation_available", lambda *_args: None)
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
