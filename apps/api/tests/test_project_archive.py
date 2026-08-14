"""Project archive (Save/Open) export -> import round-trip."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.features.projects.archive import export_project_archive, import_project_archive
from upmixer_web.features.projects.service import get_project
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.settings import Settings
from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack
from upmixer_web.shared.storage import LocalObjectStorage

from _helpers import _wav_bytes


def test_export_then_import_reconstructs_an_identical_workspace(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'archive.db'}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    storage = LocalObjectStorage(tmp_path / "objects")
    project_stems = ProjectStemStorage(tmp_path / "project-stems")
    work_root = tmp_path / "work"
    work_root.mkdir()

    source_path = tmp_path / "source.wav"
    source_path.write_bytes(_wav_bytes())
    storage.put_file("imports/original/audio/0000-source.wav", source_path)

    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="source.wav", relative_path="source.wav",
            storage_key="imports/original/audio/0000-source.wav", sha256="0" * 64,
            size_bytes=source_path.stat().st_size, sample_rate=48000, channels=2,
        )
        manifest = {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": "5.1"},
        }
        project = Project(
            import_batch=batch, name="Archived project", notes="Some notes",
            manifest=manifest, scene={"stems": {"Vocals": {"azimuth_deg": 10}}},
            view_state={"stem_order": ["Vocals"], "master_volume": 0.6},
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
        )
        track = ProjectTrack(
            project=project, asset=asset, position=0,
            layout_overrides={"5.1": {"mastering": {"loudness_target": -16.0}}},
        )
        session.add_all([batch, asset, project, track])
        session.flush()

        stem_dir = project_stems.track_root(project.id, track.id)
        stem_path = stem_dir / "vocals.wav"
        stem_path.write_bytes(_wav_bytes(220.0))
        stem = ProjectStem(
            project=project, track=track, stem_key="Vocals",
            relative_path=str(stem_path.relative_to(project_stems.root)),
            sample_rate=48000, channels=2, size_bytes=stem_path.stat().st_size, generation=1,
        )
        session.add(stem)
        session.commit()

        archive_path = tmp_path / "export.upmix.zip"
        export_project_archive(project, storage, project_stems, archive_path)
        assert archive_path.is_file()

        imported = import_project_archive(session, storage, project_stems, work_root, archive_path)

        assert imported.id != project.id
        assert imported.name == "Archived project"
        assert imported.notes == "Some notes"
        assert imported.manifest["mixing"]["channel_layout"] == "5.1"
        assert imported.scene == {"stems": {"Vocals": {"azimuth_deg": 10}}}
        assert imported.view_state == {"stem_order": ["Vocals"], "master_volume": 0.6}
        assert imported.requested_stems == ["Vocals"]
        assert imported.prepared_stems == ["Vocals"]
        assert len(imported.tracks) == 1

        imported_track = imported.tracks[0]
        assert imported_track.id != track.id
        assert imported_track.layout_overrides == {"5.1": {"mastering": {"loudness_target": -16.0}}}
        assert imported_track.asset.filename == "source.wav"
        assert imported_track.asset.import_id != asset.import_id
        assert len(imported_track.stems) == 1

        imported_stem = imported_track.stems[0]
        assert imported_stem.stem_key == "Vocals"
        assert imported_stem.sample_rate == 48000
        assert imported_stem.channels == 2
        restored_path = project_stems.resolve(imported_stem.relative_path)
        assert restored_path.read_bytes() == stem_path.read_bytes()

        restored_source = storage.local_path(imported_track.asset.storage_key)
        assert restored_source.read_bytes() == source_path.read_bytes()

        # Re-fetching through the ordinary API load path must see the same
        # graph — archive import isn't a special-cased shape.
        reloaded = get_project(session, imported.id)
        assert reloaded is not None
        assert len(reloaded.tracks) == 1

    engine.dispose()


def test_archive_export_and_import_routes_round_trip(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'archive-routes.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        imported = client.post(
            "/api/v1/imports",
            files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
            data={"relative_paths": "tone.wav"},
        ).json()
        created = client.post("/api/v1/projects", json={
            "name": "Route archive project",
            "notes": "Round trip via HTTP",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
            },
        }).json()
        client.post(f"/api/v1/projects/{created['id']}/assets", json={"import_id": imported["id"]})

        exported = client.get(f"/api/v1/projects/{created['id']}/archive")
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"

        reimported = client.post(
            "/api/v1/projects/import",
            files={"file": ("project.upmix.zip", exported.content, "application/zip")},
        )
        assert reimported.status_code == 201
        new_project = reimported.json()
        assert new_project["id"] != created["id"]
        assert new_project["name"] == "Route archive project"
        assert new_project["notes"] == "Round trip via HTTP"
        assert len(new_project["tracks"]) == 1
        assert new_project["tracks"][0]["asset"]["filename"] == "tone.wav"

        listed = client.get("/api/v1/projects").json()
        assert {project["id"] for project in listed} >= {created["id"], new_project["id"]}
