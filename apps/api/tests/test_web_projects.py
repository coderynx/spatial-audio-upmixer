import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings

from _helpers import _wav_bytes


def test_separation_settings_detects_dsp_stem_cleanup_changes():
    from upmixer_web.features.projects.service import _separation_settings

    off = {"engine": {"mode": "stem"}}
    on = {"engine": {"mode": "stem", "stem_bleed_reduction": True}}
    assert _separation_settings(off) != _separation_settings(on)


def test_separation_settings_treats_missing_keys_as_client_defaults():
    """A freshly-prepared project stores only `engine.mode`/`engine.stems`
    (see `_normalized_project_manifest`), but the web client's
    `normalizeManifest` always sends the full `stem_*` default block on every
    save — these two shapes must compare equal, or the first settings save
    after preparation spuriously clears `prepared_stems` and re-separates."""
    from upmixer_web.features.projects.service import _separation_settings

    minimal = {"engine": {"mode": "stem", "stems": ["Vocals", "Bass"]}}
    client_defaults = {
        "engine": {
            "mode": "stem",
            "stems": ["Vocals", "Bass"],
            "stem_silence_skip": True,
            "stem_batch_size": None,
            "stem_silence_threshold_db": -90,
            "stem_silence_min_duration_s": 2,
            "stem_silence_crossfade_ms": 10,
            "stem_silence_pad_ms": 200,
            "stem_bleed_reduction": False,
        }
    }
    assert _separation_settings(minimal) == _separation_settings(client_defaults)


def test_project_lifecycle_persists_settings_and_expansion(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
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
            "name": "Editable master",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals", "Drums", "Kick"]},
                "mixing": {"channel_layout": "7.1.4"},
            },
            "scene": {"stems": {"Vocals": {"azimuth_deg": 0, "elevation_deg": 0}}},
        })
        assert created.status_code == 201
        assert created.json()["status"] == "ready"
        assert created.json()["tracks"] == []
        response = client.post(f"/api/v1/projects/{created.json()['id']}/assets", json={
            "import_id": imported["id"],
        })
        assert response.status_code == 201
        project = response.json()
        assert project["status"] == "queued"
        assert project["manifest"]["engine"]["mode"] == "stem"
        assert project["requested_stems"] == ["Vocals", "Kick"]
        assert len(project["tracks"]) == 1

        saved = client.put(f"/api/v1/projects/{project['id']}/settings", json={
            "name": "Editable master v2",
            "manifest": project["manifest"],
            "scene": {"stems": {"Vocals": {"azimuth_deg": 20, "elevation_deg": 10}}},
        })
        assert saved.status_code == 200
        assert saved.json()["name"] == "Editable master v2"
        assert saved.json()["revision"] == 3

        expanded = client.post(f"/api/v1/projects/{project['id']}/stems", json={"stems": ["Bass"]})
        assert expanded.status_code == 200
        assert expanded.json()["requested_stems"] == ["Vocals", "Kick", "Bass"]


def test_stem_placement_survives_the_settings_round_trip(tmp_path, monkeypatch):
    """The mix editor edits placements and derives the gain table from them, so
    a placement that does not come back out of `/settings` silently costs the
    stem its position on the next load."""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    placement = {
        "azimuth_deg": -42.5,
        "elevation_deg": 12.0,
        "width_deg": 128.0,
        "object_size": 0.5,
        "diversity": 0.25,
        "center_level_db": -3.0,
    }
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={
            "name": "Placed",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals", "Guitar"]},
                "mixing": {
                    "channel_layout": "7.1.4",
                    "stem_placement": {"Guitar": placement},
                },
            },
        })
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["manifest"]["mixing"]["stem_placement"]["Guitar"] == placement

        moved = {**placement, "azimuth_deg": 90.0}
        manifest = project["manifest"]
        manifest["mixing"]["stem_placement"] = {"Guitar": moved}
        saved = client.put(f"/api/v1/projects/{project['id']}/settings", json={
            "name": project["name"],
            "manifest": manifest,
            "scene": project["scene"],
        })
        assert saved.status_code == 200, saved.text

        reloaded = client.get(f"/api/v1/projects/{project['id']}").json()
        assert reloaded["manifest"]["mixing"]["stem_placement"] == {"Guitar": moved}


def test_a_malformed_stem_placement_is_rejected(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        rejected = client.post("/api/v1/projects", json={
            "name": "Bad placement",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals"]},
                "mixing": {
                    "channel_layout": "7.1.4",
                    "stem_placement": {"Vocals": {"width_deg": -1.0}},
                },
            },
        })
        assert rejected.status_code == 422


def test_project_view_state_persists_independently_of_settings(tmp_path, monkeypatch):
    """Timeline/monitoring preferences (stem order, listening profile, master
    volume, A/B bypass, haze/elevation intensity) round-trip through their own
    endpoint without disturbing the manifest-owned `revision`/`prepared_stems`
    state that `/settings` guards — see `update_project_view_state`."""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'view_state.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={
            "name": "View state project",
            "manifest": {"version": "1.0.0", "engine": {"mode": "stem", "stems": []}},
        })
        assert created.status_code == 201
        project = created.json()
        assert project["view_state"] == {}
        revision_before = project["revision"]

        view_state = {
            "stem_order": ["Drums", "Bass", "Vocals"],
            "output_mode": "transaural",
            "spatial_profile": "listening",
            "transaural_profile": "car",
            "apple_head_tracking": False,
            "master_volume": 0.75,
            "mastering_bypassed": True,
            "spatial_view": "scene",
            "haze_intensity": 0.9,
            "elevation_intensity": 0.1,
        }
        saved = client.put(f"/api/v1/projects/{project['id']}/view-state", json=view_state)
        assert saved.status_code == 204

        fetched = client.get(f"/api/v1/projects/{project['id']}").json()
        assert fetched["view_state"] == view_state
        assert fetched["revision"] == revision_before
        assert fetched["prepared_stems"] == project["prepared_stems"]

        settings_saved = client.put(f"/api/v1/projects/{project['id']}/settings", json={
            "manifest": project["manifest"],
        })
        assert settings_saved.status_code == 200
        assert settings_saved.json()["view_state"] == view_state


def test_project_view_state_rejects_out_of_range_values(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'view_state_invalid.db'}",
        worker_count=1,
    )
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={
            "name": "Invalid view state project",
            "manifest": {"version": "1.0.0", "engine": {"mode": "stem", "stems": []}},
        })
        project_id = created.json()["id"]

        response = client.put(f"/api/v1/projects/{project_id}/view-state", json={"master_volume": 1.5})
        assert response.status_code == 422

        response = client.put(f"/api/v1/projects/{project_id}/view-state", json={"haze_intensity": -0.1})
        assert response.status_code == 422


def test_reprepare_project_stems_requeues_a_ready_project_and_rejects_in_flight(tmp_path, monkeypatch):
    """`/stems/reprepare` must force a full re-separation for an already-ready
    project — e.g. a separation-engine/model-registry change (see
    `service.reprepare_project_stems` and
    ~/Projects/upmixer-knowledge/roadmap.md's "cache-identity misses" standing
    risk) left its cached stems stale even though `prepared_stems` is
    populated — but refuse while a preparation is already in flight or the
    project has no tracks to prepare."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'reprepare.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    with TestClient(create_app(settings)) as client:
        with factory() as session:
            batch = ImportBatch(kind="track", title="Song")
            ready_asset = MediaAsset(
                import_batch=batch, filename="ready.wav", relative_path="ready.wav",
                storage_key="objects/ready.wav", sha256="0" * 64, size_bytes=1,
            )
            ready_project = Project(
                import_batch=batch, name="Ready project", manifest={},
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                stem_generation=1,
            )
            ready_track = ProjectTrack(
                project=ready_project,
                asset=ready_asset,
                position=0,
                layout_overrides={"7.1.4": {"engine": {"stems": ["Vocals"]}}},
            )
            expanding_asset = MediaAsset(
                import_batch=batch, filename="expanding.wav", relative_path="expanding.wav",
                storage_key="objects/expanding.wav", sha256="1" * 64, size_bytes=1,
            )
            expanding_project = Project(
                import_batch=batch, name="Expanding project", manifest={},
                status="expanding", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                stem_generation=1,
            )
            expanding_track = ProjectTrack(project=expanding_project, asset=expanding_asset, position=0)
            empty_project = Project(import_batch=batch, name="Empty project", manifest={}, status="ready")
            session.add_all([
                batch, ready_asset, ready_project, ready_track,
                expanding_asset, expanding_project, expanding_track, empty_project,
            ])
            session.commit()
            ready_id = ready_project.id
            expanding_id = expanding_project.id
            empty_id = empty_project.id

        response = client.post(f"/api/v1/projects/{ready_id}/stems/reprepare", json={
            "stems": ["Vocals", "Bass"],
            "stem_bleed_reduction": True,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "expanding"
        assert body["progress"] == 0.0
        assert body["requested_stems"] == ["Vocals", "Bass"]
        assert body["manifest"]["engine"]["stem_bleed_reduction"] is True
        assert body["tracks"][0]["layout_overrides"]["7.1.4"]["engine"] == {
            "stems": ["Vocals", "Bass"],
            "stem_bleed_reduction": True,
        }
        for manifest in (
            body["manifest"],
            body["tracks"][0]["layout_overrides"]["7.1.4"],
        ):
            mixing = manifest["mixing"]
            for field in (
                "stem_placement",
                "stem_routing",
                "stem_ambient_rear",
                "stem_ambient_height",
                "stem_ambient_height_crossover_hz",
            ):
                assert set(mixing[field]) == {"Vocals", "Bass"}
        assert all(track["status"] == "queued" for track in body["tracks"])

        conflict = client.post(f"/api/v1/projects/{expanding_id}/stems/reprepare")
        assert conflict.status_code == 409

        no_tracks = client.post(f"/api/v1/projects/{empty_id}/stems/reprepare")
        assert no_tracks.status_code == 409

        missing = client.post("/api/v1/projects/does-not-exist/stems/reprepare")
        assert missing.status_code == 404

    engine.dispose()


def test_settings_save_with_full_client_engine_block_does_not_reseparate(tmp_path, monkeypatch):
    """A settings save that changes only `mixing.channel_layout` (a mix/
    routing-time concern, independent of stem audio) must not clear
    `prepared_stems` or requeue tracks — even though the web client always
    sends the full `engine.stem_*` default block while a freshly-prepared
    project's stored manifest carries only `engine.mode`/`engine.stems` (see
    `_normalized_project_manifest`). Regression test for the spurious
    re-separation this shape mismatch used to cause via `_separation_settings`."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'layout.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    minimal_manifest = {
        "version": "1.0.0",
        "engine": {"mode": "stem", "stems": ["Vocals", "Bass"]},
        "mixing": {"channel_layout": "7.1.4", "stem_routing": {}},
    }

    with factory() as session:
        batch = ImportBatch(kind="track", title="Song")
        asset = MediaAsset(
            import_batch=batch, filename="ready.wav", relative_path="ready.wav",
            storage_key="objects/ready.wav", sha256="0" * 64, size_bytes=1,
        )
        project = Project(
            import_batch=batch, name="Ready project", manifest=minimal_manifest,
            status="ready", prepared_stems=["Vocals", "Bass"], requested_stems=["Vocals", "Bass"],
            stem_generation=1,
        )
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, project, track])
        session.commit()
        project_id = project.id

    with TestClient(create_app(settings)) as client:
        # Mirrors the web client's normalizeManifest: the full engine
        # default block, only `mixing.channel_layout` changed.
        client_manifest = {
            "version": "1.0.0",
            "engine": {
                "mode": "stem",
                "stems": ["Vocals", "Bass"],
                "stem_silence_skip": True,
                "stem_batch_size": None,
                "stem_silence_threshold_db": -90,
                "stem_silence_min_duration_s": 2,
                "stem_silence_crossfade_ms": 10,
                "stem_silence_pad_ms": 200,
                "stem_bleed_reduction": False,
            },
            "mixing": {"channel_layout": "5.1.4", "stem_routing": {}},
        }
        saved = client.put(f"/api/v1/projects/{project_id}/settings", json={
            "manifest": client_manifest,
            "scene": {},
        })
        assert saved.status_code == 200
        body = saved.json()
        assert body["status"] == "ready"
        assert body["prepared_stems"] == ["Vocals", "Bass"]
        assert body["manifest"]["mixing"]["channel_layout"] == "5.1.4"

    engine.dispose()


def test_project_seeds_complete_balanced_preset(tmp_path, monkeypatch):
    """Creation and preparation persist the whole shared balanced mix, even when
    the web client sends empty preset maps."""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'projects.db'}",
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
            "name": "Seeded routing",
            "manifest": {
                "version": "1.0.0",
                "engine": {"mode": "stem", "stems": ["Vocals", "Bass"]},
                "mixing": {"channel_layout": "7.1.4", "stem_routing": {}},
            },
            "scene": {},
        })
        assert created.status_code == 201
        created_mixing = created.json()["manifest"]["mixing"]
        for field in (
            "stem_placement",
            "stem_routing",
            "stem_ambient_rear",
            "stem_ambient_height",
            "stem_ambient_height_crossover_hz",
        ):
            assert set(created_mixing[field]) == {"Vocals", "Bass"}
        assert created_mixing["stem_placement"]["Vocals"] == {
            "azimuth_deg": 0.0,
            "elevation_deg": 2.0,
            "width_deg": 26.0,
            "object_size": 0.12,
            "diversity": 0.0,
            "center_level_db": 0.5,
        }
        assert created_mixing["stem_ambient_rear"]["Vocals"] == 0.06
        assert created_mixing["stem_ambient_height"]["Vocals"] == 0.04
        assert created_mixing["stem_ambient_height_crossover_hz"]["Vocals"] == 4000.0
        response = client.post(f"/api/v1/projects/{created.json()['id']}/assets", json={
            "import_id": imported["id"],
        })
        assert response.status_code == 201
        stem_routing = response.json()["manifest"]["mixing"]["stem_routing"]
        assert stem_routing.get("Vocals")
        assert stem_routing.get("Bass")
        track_mixing = response.json()["tracks"][0]["layout_overrides"]["7.1.4"]["mixing"]
        for field in (
            "stem_placement",
            "stem_routing",
            "stem_ambient_rear",
            "stem_ambient_height",
            "stem_ambient_height_crossover_hz",
        ):
            assert set(track_mixing[field]) == {"Vocals", "Bass"}


def test_project_view_builds_stem_urls_from_catalogued_stems(tmp_path, monkeypatch):
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectStem, ProjectTrack

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
        stem_root = tmp_path / "project-stems" / "a"
        stem_root.mkdir(parents=True)
        (stem_root / "Vocals.wav").write_bytes(b"full")
        (stem_root / "Vocals.preview.ogg").write_bytes(b"preview")
        stem = ProjectStem(
            project=project, track=track, stem_key="Vocals", relative_path="a/Vocals.wav",
            sample_rate=48_000, channels=2, size_bytes=10, generation=1,
            preview_relative_path="a/Vocals.preview.ogg",
        )
        session.add_all([batch, asset, project, track, stem])
        session.commit()
        project_id = project.id

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/v1/projects/{project_id}")

        assert response.status_code == 200
        body = response.json()
        track_view = body["tracks"][0]
        stem_view = track_view["stems"][0]
        assert stem_view["audio_url"] == (
            f"/api/v1/projects/{project_id}/tracks/{track_view['id']}/stems/{stem_view['id']}/audio"
        )
        assert stem_view["preview_url"] == f"{stem_view['audio_url']}?quality=preview&v=1-high"

        preview = client.get(stem_view["preview_url"])
        assert preview.content == b"preview"
        assert preview.headers["cache-control"] == "private, max-age=31536000, immutable"

        full = client.get(stem_view["audio_url"])
        assert full.content == b"full"
        assert "cache-control" not in full.headers

        with factory() as session:
            project = session.get(Project, project_id)
            project.preview_quality = "low"
            project.stems[0].generation = 2
            session.commit()

    low_quality = client.get(f"/api/v1/projects/{project_id}").json()
    assert low_quality["tracks"][0]["stems"][0]["preview_url"].endswith("?quality=preview&v=2-low")

    engine.dispose()
