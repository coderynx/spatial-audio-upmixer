import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings

from _helpers import _wav_bytes


def test_download_track_stems_archive_streams_a_zip_of_every_stem(tmp_path, monkeypatch):
    import io
    import zipfile

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'stems-archive.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    stem_bytes = {"Vocals": _wav_bytes(440.0), "Bass": _wav_bytes(220.0)}
    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="song.wav", relative_path="song.wav",
            storage_key="objects/song.wav", sha256="0" * 64, size_bytes=1, title="Song",
        )
        project = Project(import_batch=batch, name="Archive project", manifest={})
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.flush()
        stems_dir = tmp_path / "project-stems" / project.id / track.id / "stems"
        stems_dir.mkdir(parents=True)
        for stem_key, data in stem_bytes.items():
            (stems_dir / f"{stem_key}.wav").write_bytes(data)
            session.add(ProjectStem(
                project=project, track=track, stem_key=stem_key,
                relative_path=f"{project.id}/{track.id}/stems/{stem_key}.wav",
                sample_rate=48_000, channels=2, size_bytes=len(data), generation=1,
            ))
        session.commit()
        project_id, track_id = project.id, track.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/v1/projects/{project_id}/tracks/{track_id}/stems/archive")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="Song-stems.zip"' in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert sorted(archive.namelist()) == ["Bass.wav", "Vocals.wav"]
            for stem_key, data in stem_bytes.items():
                assert archive.read(f"{stem_key}.wav") == data

        assert client.get(f"/api/v1/projects/{project_id}/tracks/nope/stems/archive").status_code == 404


def test_download_track_stems_archive_returns_409_when_track_has_no_stems(tmp_path, monkeypatch):
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'stems-archive-empty.db'}"
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
        project = Project(import_batch=batch, name="Empty project", manifest={})
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.commit()
        project_id, track_id = project.id, track.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/v1/projects/{project_id}/tracks/{track_id}/stems/archive")
        assert response.status_code == 409


def test_project_view_exposes_versioned_peaks_url_and_serves_the_envelope(tmp_path, monkeypatch):
    import json

    import numpy as np

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack
    from upmixer_web.features.projects.storage import PEAK_BINS, PEAKS_SCHEMA

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
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack
    from upmixer_web.features.projects.storage import ProjectStemStorage
    from upmixer_web.shared.storage import LocalObjectStorage, StorageAudioSink, StorageAudioSource
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
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack

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
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

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
        exported = client.post(f"/api/v1/projects/{project_id}/exports", json={"layout": "5.1"})
        assert exported.status_code == 201
        job_id = exported.json()["id"]

        deleted = client.delete(f"/api/v1/projects/{project_id}")
        assert deleted.status_code == 204

        job = client.get(f"/api/v1/jobs/{job_id}")
        assert job.status_code == 200
        assert job.json()["project_id"] is None


def test_project_export_clones_tracks_spanning_multiple_imports(tmp_path, monkeypatch):
    """A project's tracks can come from more than one import batch once
    assets are added incrementally (the Assets tab uploads in separate
    sessions) — project_export_job must clone JobTracks from the project's
    own tracks, not project.import_batch.assets, or a second-import track
    would be silently dropped from the export (or crash entirely for an
    empty-created project whose import_batch is None)."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'multi-import-export.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        first_batch = ImportBatch(kind="track", title="Song A")
        second_batch = ImportBatch(kind="track", title="Song B")
        first_asset = MediaAsset(
            import_batch=first_batch, filename="a.wav", relative_path="a.wav",
            storage_key="objects/a.wav", sha256="1" * 64, size_bytes=1,
        )
        second_asset = MediaAsset(
            import_batch=second_batch, filename="b.wav", relative_path="b.wav",
            storage_key="objects/b.wav", sha256="2" * 64, size_bytes=1,
        )
        manifest = {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": "5.1"},
        }
        # import_batch is None: mirrors a project created empty (no import
        # at creation time) whose tracks were all added afterwards.
        project = Project(
            import_batch=None, name="Multi-import project", manifest=manifest,
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
        )
        first_track = ProjectTrack(project=project, asset=first_asset, position=0)
        second_track = ProjectTrack(project=project, asset=second_asset, position=1)
        session.add_all([first_batch, second_batch, first_asset, second_asset, project, first_track, second_track])
        session.commit()
        project_id = project.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        exported = client.post(f"/api/v1/projects/{project_id}/exports", json={"layout": "5.1"})
        assert exported.status_code == 201
        job = exported.json()
        assert len(job["tracks"]) == 2
        asset_ids = {track["asset"]["id"] for track in job["tracks"]}
        assert asset_ids == {first_asset.id, second_asset.id}


def test_add_project_assets_stores_per_file_overrides_and_unions_stems(tmp_path, monkeypatch):
    """The Assets tab lets each uploaded file request its own stems/format —
    add_project_assets must store that per-track (so worker._run_project
    later separates that track with its own stem list) and widen the
    project's own requested_stems to include any stem a file asks for that
    the project didn't already have, rather than rejecting it."""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'assets.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    monkeypatch.setattr("upmixer_web.features.projects.routes.ensure_stem_separation_available", lambda *_args: None)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={
            "name": "Per-file settings",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
            },
        })
        assert created.status_code == 201
        project_id = created.json()["id"]

        imported = client.post(
            "/api/v1/imports",
            files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
            data={"relative_paths": "tone.wav"},
        ).json()
        asset_id = imported["assets"][0]["id"]

        response = client.post(f"/api/v1/projects/{project_id}/assets", json={
            "import_id": imported["id"],
            "per_asset_overrides": {
                asset_id: {
                    "engine": {
                        "stems": ["Bass"],
                        "stem_bleed_reduction": True,
                        "stem_phase_fix_scale": 0.6,
                        "stem_debleed_model": "mel_band_roformer_denoise_debleed_gabox.ckpt",
                        "stem_debleed": {"Bass": True},
                    },
                    "format": {"sample_rate": 48000, "subtype": "PCM_24"},
                    "mixing": {"channel_layout": "7.1.4"},
                },
            },
        })
        assert response.status_code == 201
        project = response.json()
        assert project["requested_stems"] == ["Vocals", "Bass"]
        track = project["tracks"][0]
        # The staged layout becomes the track's first (and so far only) one.
        assert track["layouts"] == ["7.1.4"]
        overrides = track["layout_overrides"]["7.1.4"]
        engine = overrides["engine"]
        assert engine["stems"] == ["Bass"]
        assert engine["stem_bleed_reduction"] is True
        assert engine["stem_phase_fix_scale"] == 0.6
        assert engine["stem_debleed_model"] == "mel_band_roformer_denoise_debleed_gabox.ckpt"
        assert engine["stem_debleed"] == {"Bass": True}
        assert overrides["format"]["sample_rate"] == 48000
        assert overrides["format"]["subtype"] == "PCM_24"
        assert overrides["mixing"]["channel_layout"] == "7.1.4"
        stem_routing = project["manifest"]["mixing"]["stem_routing"]
        assert "Vocals" in stem_routing
        assert "Bass" in stem_routing
        assert any(gain > 0 for gain in stem_routing["Bass"].values())


@pytest.mark.parametrize(
    ("output_type", "narrowed_layout"),
    [("binaural", "stereo"), ("transaural", "stereo"), ("adm-bwf", "stereo"), ("binaural", "5.1")],
)
def test_narrowing_the_layout_retargets_a_delivery_it_cannot_carry(
    tmp_path, monkeypatch, output_type, narrowed_layout
):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / f'retarget_{output_type}_{narrowed_layout}.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={
            "name": "Bed delivery",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "realtime", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "7.1.4"},
                "format": {"type": output_type, "subtype": "PCM_24", "sample_rate": 48000},
            },
        })
        assert created.status_code == 201
        assert created.json()["manifest"]["format"]["type"] == output_type

        # Only the layout changes; `format.type` is untouched by the user.
        response = client.put(f"/api/v1/projects/{created.json()['id']}/settings", json={
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": narrowed_layout},
                "format": {"type": output_type, "subtype": "PCM_24", "sample_rate": 48000},
            },
        })
        assert response.status_code == 200
        manifest = response.json()["manifest"]
        assert manifest["mixing"]["channel_layout"] == narrowed_layout
        assert manifest["format"]["type"] == "wav"
