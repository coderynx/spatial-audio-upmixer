"""Per-track, per-layout mixes: a track carries several speaker layouts and
each one owns an independent mix, master and delivery."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings

from _helpers import _wav_bytes


def _project_with_track(client, layout="7.1.4"):
    created = client.post("/api/v1/projects", json={
        "name": "Layouts",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": layout},
        },
    })
    assert created.status_code == 201
    project_id = created.json()["id"]
    imported = client.post(
        "/api/v1/imports",
        files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
        data={"relative_paths": "tone.wav"},
    ).json()
    project = client.post(
        f"/api/v1/projects/{project_id}/assets", json={"import_id": imported["id"]}
    ).json()
    return project_id, project["tracks"][0]["id"]


@pytest.fixture
def layouts_client(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'layouts.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        yield client


def test_a_layouts_mix_edit_leaves_every_other_layout_untouched(layouts_client):
    """The whole point of the per-layout store: a compressor ratio, stem
    balance or routing tuned for stereo must not follow the user back to
    7.1.4, and vice versa."""
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    response = layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4", "stereo"]})
    assert response.status_code == 200
    track = response.json()["tracks"][0]
    assert track["layouts"] == ["7.1.4", "stereo"]
    before_714 = track["layout_overrides"]["7.1.4"]

    response = layouts_client.put(f"{base}/layouts/stereo/settings", json={
        "manifest_overrides": {
            "mastering": {"compressor": {"ratio": 8.0}},
            "mixing": {"stem_rebalance": {"Vocals": -3.0}},
        },
        "scene_overrides": {},
    })
    assert response.status_code == 200
    overrides = response.json()["tracks"][0]["layout_overrides"]
    assert overrides["stereo"]["mastering"]["compressor"]["ratio"] == 8.0
    assert overrides["stereo"]["mixing"]["stem_rebalance"] == {"Vocals": -3.0}
    assert overrides["7.1.4"] == before_714, "editing one layout must not touch another"

    # ...and back the other way.
    response = layouts_client.put(f"{base}/layouts/7.1.4/settings", json={
        "manifest_overrides": {"mastering": {"compressor": {"ratio": 2.0}}},
        "scene_overrides": {},
    })
    assert response.status_code == 200
    overrides = response.json()["tracks"][0]["layout_overrides"]
    assert overrides["7.1.4"]["mastering"]["compressor"]["ratio"] == 2.0
    assert overrides["stereo"]["mastering"]["compressor"]["ratio"] == 8.0


def test_adding_a_layout_rebuilds_routing_for_its_own_speakers(layouts_client):
    """`stem_routing` is keyed by speaker name, so it cannot cross layouts —
    a layout added to a track is seeded from the track's current mix with the
    routing re-placed onto the new layout's channel set."""
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    response = layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4", "5.1"]})
    assert response.status_code == 200
    overrides = response.json()["tracks"][0]["layout_overrides"]
    routing_51 = overrides["5.1"]["mixing"]["stem_routing"]
    assert routing_51, "a new layout must get its own routing, not an empty one"
    assert not {channel for route in routing_51.values() for channel in route} - {
        "FL", "FR", "C", "LFE", "SL", "SR",
    }, "5.1 routing must not carry 7.1.4-only speakers"


def test_a_stereo_layout_block_is_folded_to_the_front_pair(layouts_client):
    """The two-channel fold is load-bearing (docs/project_manifest_parity.md):
    the preview reads routing straight from the manifest while the export
    folds the built-in base route, so an unfolded route on a `stereo` layout
    previews several dB below the render. Track overrides never ran this fold
    before layouts were per track."""
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4", "stereo"]})
    response = layouts_client.put(f"{base}/layouts/stereo/settings", json={
        "manifest_overrides": {
            "mixing": {"stem_routing": {"Vocals": {"FL": 0.5, "FR": 0.5, "C": 0.7, "LFE": 0.3}}},
        },
        "scene_overrides": {},
    })
    assert response.status_code == 200
    routing = response.json()["tracks"][0]["layout_overrides"]["stereo"]["mixing"]["stem_routing"]
    assert set(routing["Vocals"]) <= {"FL", "FR"}


def test_a_layout_block_retargets_a_delivery_it_cannot_carry(layouts_client):
    """Per-layout `format.type` gets the same fallback the project manifest
    has always had — a stereo layout cannot carry ADM-BWF."""
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4", "stereo"]})
    response = layouts_client.put(f"{base}/layouts/stereo/settings", json={
        "manifest_overrides": {"format": {"type": "adm-bwf"}},
        "scene_overrides": {},
    })
    assert response.status_code == 200
    overrides = response.json()["tracks"][0]["layout_overrides"]
    assert overrides["stereo"]["format"]["type"] == "multichannel"


def test_removing_a_layout_discards_only_that_layouts_mix(layouts_client):
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4", "stereo"]})
    layouts_client.put(f"{base}/layouts/7.1.4/settings", json={
        "manifest_overrides": {"mastering": {"compressor": {"ratio": 2.0}}},
        "scene_overrides": {},
    })
    response = layouts_client.put(f"{base}/layouts", json={"layouts": ["7.1.4"]})
    assert response.status_code == 200
    track = response.json()["tracks"][0]
    assert track["layouts"] == ["7.1.4"]
    assert "stereo" not in track["layout_overrides"]
    assert track["layout_overrides"]["7.1.4"]["mastering"]["compressor"]["ratio"] == 2.0


def test_a_track_must_keep_at_least_one_layout(layouts_client):
    project_id, track_id = _project_with_track(layouts_client)
    response = layouts_client.put(
        f"/api/v1/projects/{project_id}/tracks/{track_id}/layouts", json={"layouts": []}
    )
    assert response.status_code == 422


def test_an_unknown_layout_is_rejected(layouts_client):
    project_id, track_id = _project_with_track(layouts_client)
    base = f"/api/v1/projects/{project_id}/tracks/{track_id}"

    assert layouts_client.put(f"{base}/layouts", json={"layouts": ["9.9.9"]}).status_code == 422
    response = layouts_client.put(f"{base}/layouts/9.9.9/settings", json={
        "manifest_overrides": {}, "scene_overrides": {},
    })
    assert response.status_code == 422


def test_saving_a_layout_the_track_does_not_have_is_rejected(layouts_client):
    project_id, track_id = _project_with_track(layouts_client)
    response = layouts_client.put(
        f"/api/v1/projects/{project_id}/tracks/{track_id}/layouts/5.1/settings",
        json={"manifest_overrides": {}, "scene_overrides": {}},
    )
    assert response.status_code == 422


def test_export_renders_one_layout_and_only_the_tracks_that_have_it(tmp_path, monkeypatch):
    """One export is one layout. A track mixed only for stereo has nothing to
    contribute to a 5.1 render and must not be silently exported at a layout
    it was never mixed for."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'layout-export.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        batch = ImportBatch(kind="album", title="Songs")
        both = MediaAsset(
            import_batch=batch, filename="a.wav", relative_path="a.wav",
            storage_key="objects/a.wav", sha256="1" * 64, size_bytes=1,
        )
        stereo_only = MediaAsset(
            import_batch=batch, filename="b.wav", relative_path="b.wav",
            storage_key="objects/b.wav", sha256="2" * 64, size_bytes=1,
        )
        project = Project(
            import_batch=batch, name="Layout export",
            manifest={
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {"channel_layout": "5.1"},
            },
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
        )
        session.add_all([
            batch, both, stereo_only, project,
            ProjectTrack(
                project=project, asset=both, position=0,
                layout_overrides={"5.1": {}, "stereo": {}},
            ),
            ProjectTrack(
                project=project, asset=stereo_only, position=1,
                layout_overrides={"stereo": {}},
            ),
        ])
        session.commit()
        project_id, both_id, stereo_only_id = project.id, both.id, stereo_only.id
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        exported = client.post(f"/api/v1/projects/{project_id}/exports", json={"layout": "5.1"})
        assert exported.status_code == 201
        job = exported.json()
        assert job["manifest"]["mixing"]["channel_layout"] == "5.1"
        assert {track["asset"]["id"] for track in job["tracks"]} == {both_id}

        exported = client.post(f"/api/v1/projects/{project_id}/exports", json={"layout": "stereo"})
        assert exported.status_code == 201
        assert {track["asset"]["id"] for track in exported.json()["tracks"]} == {both_id, stereo_only_id}

        nobody = client.post(f"/api/v1/projects/{project_id}/exports", json={"layout": "7.1.4"})
        assert nobody.status_code == 409


def test_migration_folds_a_legacy_track_override_onto_its_layout(tmp_path):
    """Every existing track carried one mix for one layout. The migration must
    land it as a single-key `layout_overrides` holding exactly that mix, so an
    upgraded project sounds identical."""
    import json
    from pathlib import Path

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    database_url = f"sqlite:///{tmp_path / 'migrate.db'}"
    package_dir = Path(__import__("upmixer_web").__file__).resolve().parent
    config = Config()
    config.attributes["database_url_configured"] = True
    config.set_main_option("script_location", str(package_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    # The revision immediately before layouts became per track.
    command.upgrade(config, "f1a2c8d6e903")
    engine = sa.create_engine(database_url)
    overrides = {"mastering": {"compressor": {"ratio": 5.0}}, "mixing": {"channel_layout": "5.1"}}
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO import_batches (id, kind, title, created_at)"
            " VALUES ('b1', 'track', 'Song', '2026-01-01')"
        ))
        connection.execute(sa.text(
            "INSERT INTO media_assets (id, import_id, filename, relative_path, storage_key,"
            " sha256, size_bytes, position) VALUES ('a1', 'b1', 's.wav', 's.wav',"
            " 'objects/s.wav', '0000', 1, 0)"
        ))
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, import_id, name, status, progress, status_message,"
                " manifest, scene, requested_stems, prepared_stems, stem_generation, revision,"
                " created_at, updated_at) VALUES ('p1', 'b1', 'Legacy', 'ready', 1.0, 'ok',"
                " :manifest, '{}', '[]', '[]', 0, 1, '2026-01-01', '2026-01-01')"
            ),
            {"manifest": json.dumps({"mixing": {"channel_layout": "7.1.4"}})},
        )
        connection.execute(
            sa.text(
                "INSERT INTO project_tracks (id, project_id, asset_id, position, status, progress,"
                " manifest_overrides, scene_overrides)"
                " VALUES ('t1', 'p1', 'a1', 0, 'ready', 1.0, :overrides, '{}')"
            ),
            {"overrides": json.dumps(overrides)},
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        stored = connection.execute(sa.text(
            "SELECT layout_overrides FROM project_tracks WHERE id = 't1'"
        )).scalar_one()
    engine.dispose()

    # The track's own layout wins over the project's, and the mix is intact.
    migrated = json.loads(stored) if isinstance(stored, str) else stored
    assert migrated == {"5.1": overrides}


def test_a_project_stored_before_a_field_was_retired_still_saves(layouts_client, tmp_path):
    """A project written while `mixing.spatial` existed keeps its stored copy.
    Reading it, echoing it back and saving a mix edit all have to survive the
    field no longer being part of the manifest."""
    import json

    import sqlalchemy as sa

    project_id, track_id = _project_with_track(layouts_client)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'layouts.db'}")
    with engine.begin() as connection:
        stored = json.loads(connection.execute(
            sa.text("SELECT manifest FROM projects WHERE id = :id"), {"id": project_id}
        ).scalar_one())
        stored["mixing"]["spatial"] = {"profile": "balanced", "intensity": 0.0, "preanalyze": False}
        stored["routing"] = {**stored.get("routing", {}), "content_mix_strength": 0.0}
        stored["engine"]["mode"] = "realtime"
        connection.execute(
            sa.text("UPDATE projects SET manifest = :manifest WHERE id = :id"),
            {"manifest": json.dumps(stored), "id": project_id},
        )
    engine.dispose()

    read = layouts_client.get(f"/api/v1/projects/{project_id}")
    assert read.status_code == 200
    served = read.json()["manifest"]
    assert "spatial" not in served["mixing"]
    assert "content_mix_strength" not in served["routing"]
    assert served["engine"]["mode"] == "stem"

    # The client saves the blocks it was served, plus its edit.
    response = layouts_client.put(
        f"/api/v1/projects/{project_id}/tracks/{track_id}/layouts/7.1.4/settings",
        json={"manifest_overrides": {
            "mixing": {**served["mixing"], "stem_ambient_rear": {"Vocals": 0.4}},
        }},
    )
    assert response.status_code == 200, response.text
    saved = response.json()["tracks"][0]["layout_overrides"]["7.1.4"]["mixing"]
    assert saved["stem_ambient_rear"] == {"Vocals": 0.4}
    assert "spatial" not in saved

    response = layouts_client.put(
        f"/api/v1/projects/{project_id}/tracks/{track_id}/layouts/7.1.4/settings",
        json={"manifest_overrides": {"mixing": {"spatial_downmix_lock": True}}},
    )
    assert response.status_code == 200, response.text
    saved = response.json()["tracks"][0]["layout_overrides"]["7.1.4"]["mixing"]
    assert saved["spatial_downmix_lock"] is True
