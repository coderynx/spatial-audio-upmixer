import time

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings

from _helpers import _seed_prepared_stems


def test_fir_endpoint_varies_with_strength_and_max_db(tmp_path, monkeypatch):
    """The reference-match FIR endpoint designs the filter from the
    persisted curve on demand (see Ledger D21) — different `strength`/
    `max_db` query params must produce different filter bytes without any
    recompute of the underlying curve, since strength/max_db are excluded
    from the recompute signature (see the docs-comment on
    `_reference_match_signature`)."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-fir-endpoint.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    with TestClient(create_app(settings)) as client:
        storage = client.app.state.storage
        source_wav = tmp_path / "source.wav"
        reference_wav = tmp_path / "reference.wav"
        samples = np.arange(48_000 * 2) / 48_000
        sf.write(source_wav, 0.2 * np.column_stack([
            np.sin(2 * np.pi * 440.0 * samples), np.sin(2 * np.pi * 442.0 * samples),
        ]), 48_000, subtype="FLOAT")
        sf.write(reference_wav, 0.4 * np.column_stack([
            np.sin(2 * np.pi * 220.0 * samples), np.sin(2 * np.pi * 8000.0 * samples),
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
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 6.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match fir endpoint project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        project_stems = client.app.state.project_stems
        _seed_prepared_stems(
            project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )
        manager.prepare_reference_match(project_id)
        meta = project_stems.read_reference_match_meta(project_id, "5.1")
        assert meta["curve"]

        base_url = f"/api/v1/projects/{project_id}/reference-match/5.1/fir"
        full_strength = client.get(base_url, params={"strength": 1.0, "max_db": 6.0})
        half_strength = client.get(base_url, params={"strength": 0.5, "max_db": 6.0})
        clamped = client.get(base_url, params={"strength": 1.0, "max_db": 1.0})
        assert full_strength.status_code == half_strength.status_code == clamped.status_code == 200
        assert full_strength.content != half_strength.content
        assert full_strength.content != clamped.content

    engine.dispose()


def test_prepare_reference_match_reuses_cached_layout_asset(tmp_path, monkeypatch):
    """Switching speaker layout back to one already computed this session
    must restore the cached asset instead of re-running the mix + PSD-match
    pass — the round-trip cost this cache exists to remove (see the plan:
    "Cache reference-match assets per signature")."""
    from unittest.mock import patch

    from upmixer.mastering.match_reference import ReferenceMatchProcessor
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-cache-reuse.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

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
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 6.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match cache-reuse project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        project_stems = client.app.state.project_stems
        _seed_prepared_stems(
            project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )

        manager.prepare_reference_match(project_id)
        meta_51 = project_stems.read_reference_match_meta(project_id, "5.1")
        signature_51 = meta_51["signature"]

        with factory() as session:
            project = session.get(Project, project_id)
            project.manifest = {**project.manifest, "mixing": {"channel_layout": "7.1"}}
            session.commit()
        manager.prepare_reference_match(project_id)
        meta_71 = project_stems.read_reference_match_meta(project_id, "7.1")
        assert meta_71["signature"] != signature_51

        with factory() as session:
            project = session.get(Project, project_id)
            project.manifest = {**project.manifest, "mixing": {"channel_layout": "5.1"}}
            session.commit()

        def _forbidden_compute_curve(self_inner, channels, lfe_key="LFE"):
            raise AssertionError("a revisited layout's signature must be promoted from cache, not recomputed")

        with patch.object(ReferenceMatchProcessor, "compute_curve", _forbidden_compute_curve):
            manager.prepare_reference_match(project_id)

        restored = project_stems.read_reference_match_meta(project_id, "5.1")
        assert restored == meta_51, "the promoted asset must exactly match the original 5.1 asset"

    engine.dispose()


def test_schedule_reference_match_promotes_cache_without_pending(tmp_path, monkeypatch):
    """schedule_reference_match must resolve a revisited layout's cache hit
    inline, synchronously, so the settings-save response already carries the
    restored fir_url and reference_match_pending never opens — a cache hit
    is what makes a revisited speaker layout feel instant instead of showing
    the "Preparing reference EQ match…" banner again."""
    from concurrent.futures import ThreadPoolExecutor

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-schedule-cache-reuse.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

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
                        "strength": 1.0, "spectrum": True, "rms": True, "max_db": 6.0,
                    },
                },
            }
            project = Project(
                import_batch=batch, name="Ref match schedule cache-reuse project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        project_stems = client.app.state.project_stems
        _seed_prepared_stems(
            project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch-cache")
        try:
            manager.prepare_reference_match(project_id)
            signature_51 = project_stems.read_reference_match_meta(project_id, "5.1")["signature"]

            with factory() as session:
                project = session.get(Project, project_id)
                project.manifest = {**project.manifest, "mixing": {"channel_layout": "7.1"}}
                session.commit()
            manager.prepare_reference_match(project_id)
            assert project_stems.read_reference_match_meta(project_id, "7.1")["signature"] != signature_51

            with factory() as session:
                project = session.get(Project, project_id)
                project.manifest = {**project.manifest, "mixing": {"channel_layout": "5.1"}}
                session.commit()

            manager.schedule_reference_match(project_id)
            assert not manager.reference_match_pending(project_id), (
                "a cache hit must resolve inline, never opening the pending window"
            )
            assert project_stems.read_reference_match_meta(project_id, "5.1")["signature"] == signature_51
        finally:
            manager._refmatch_executor.shutdown(wait=True)

    engine.dispose()


def test_reference_match_cache_prunes_stale_entries_without_orphaning_active_asset(tmp_path):
    """`write_reference_match`'s per-signature cache must not grow without
    bound as `stem_generation` (or any other signature input) churns — old
    entries beyond `REFERENCE_MATCH_CACHE_LIMIT` are pruned, and the layout's
    active slot every reader relies on must never be among the pruned
    files."""
    from upmixer_web.features.projects.storage import REFERENCE_MATCH_CACHE_LIMIT, ProjectStemStorage

    project_stems = ProjectStemStorage(tmp_path)
    project_id = "proj-cache-prune"

    signatures = [f"sig-{i:02d}" for i in range(REFERENCE_MATCH_CACHE_LIMIT + 3)]
    for signature in signatures:
        project_stems.write_reference_match(
            project_id, "5.1", [[100.0, 0.0]], ["FL", "FR"], 0.0, 48_000, 1023, signature,
        )

    cache_dir = project_stems.reference_match_dir(project_id) / "cache"
    cached_signatures = {path.stem for path in cache_dir.glob("*.json")}
    assert len(cached_signatures) == REFERENCE_MATCH_CACHE_LIMIT
    assert cached_signatures == set(signatures[-REFERENCE_MATCH_CACHE_LIMIT:]), (
        "pruning must keep the newest entries, not an arbitrary subset"
    )

    active = project_stems.read_reference_match_meta(project_id, "5.1")
    assert active is not None and active["signature"] == signatures[-1]

    assert project_stems.promote_cached_reference_match(project_id, "5.1", signatures[-1])
    assert not project_stems.promote_cached_reference_match(project_id, "5.1", signatures[0]), (
        "a pruned signature must no longer be promotable"
    )


def test_resolve_project_mastering_reference_allows_reference_from_any_track_import(tmp_path):
    """A project can accumulate tracks from more than one import over time
    (unlike a job, which is scoped to a single import). A reference attached
    from import A must stay resolvable even after the project later gains a
    track from import B — before this widened check, the strict
    single-`ImportBatch` comparison would start rejecting an already-valid
    reference the moment a second-import track was added, and the only
    workaround was re-uploading the same reference file again under
    import B."""
    from upmixer_web.features.imports.service import resolve_project_mastering_reference
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-resolver.db'}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    with factory() as session:
        batch_a = ImportBatch(kind="track", title="Import A")
        batch_b = ImportBatch(kind="track", title="Import B")
        asset_a = MediaAsset(
            import_batch=batch_a, filename="a.wav", relative_path="a.wav",
            storage_key="assets/a.wav", sha256="a" * 64, size_bytes=1,
        )
        asset_b = MediaAsset(
            import_batch=batch_b, filename="b.wav", relative_path="b.wav",
            storage_key="assets/b.wav", sha256="b" * 64, size_bytes=1,
        )
        reference = MasteringReference(
            import_batch=batch_a, filename="ref.wav",
            storage_key="references/ref.wav", sha256="r" * 64, size_bytes=1,
        )
        project = Project(
            import_batch=batch_a, name="Multi-import project", manifest={"version": "1.0.0"},
            status="ready",
        )
        track_a = ProjectTrack(project=project, asset=asset_a, position=0)
        session.add_all([batch_a, batch_b, asset_a, asset_b, reference, project, track_a])
        session.commit()
        reference_id = reference.id

        # Single-import project: resolves exactly like the strict job-side check.
        resolved = resolve_project_mastering_reference(session, project, reference_id)
        assert resolved is not None and resolved.id == reference_id

        # Add a track from a *different* import — the reference from import A
        # must remain resolvable through the project's track set.
        track_b = ProjectTrack(project=project, asset=asset_b, position=1)
        session.add(track_b)
        session.commit()
        session.refresh(project)

        resolved = resolve_project_mastering_reference(session, project, reference_id)
        assert resolved is not None and resolved.id == reference_id

    engine.dispose()


def test_resolve_project_mastering_reference_rejects_unrelated_reference(tmp_path):
    """A reference belonging to neither the project's own import nor any
    track's import must still be rejected."""
    from upmixer_web.features.imports.service import resolve_project_mastering_reference
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-resolver-reject.db'}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    with factory() as session:
        batch_a = ImportBatch(kind="track", title="Import A")
        batch_unrelated = ImportBatch(kind="track", title="Unrelated import")
        asset_a = MediaAsset(
            import_batch=batch_a, filename="a.wav", relative_path="a.wav",
            storage_key="assets/a2.wav", sha256="c" * 64, size_bytes=1,
        )
        unrelated_reference = MasteringReference(
            import_batch=batch_unrelated, filename="ref.wav",
            storage_key="references/unrelated.wav", sha256="d" * 64, size_bytes=1,
        )
        project = Project(
            import_batch=batch_a, name="Single-import project", manifest={"version": "1.0.0"},
            status="ready",
        )
        track_a = ProjectTrack(project=project, asset=asset_a, position=0)
        session.add_all([batch_a, batch_unrelated, asset_a, unrelated_reference, project, track_a])
        session.commit()

        with pytest.raises(ValueError):
            resolve_project_mastering_reference(session, project, unrelated_reference.id)

    engine.dispose()


def test_startup_sweep_schedules_reference_match_for_projects_with_a_reference(tmp_path):
    """The API's startup lifespan (`api.py`) sweeps every project with a
    reference attached through the normal signature-checked scheduling path
    — this is what lets an existing dev/prod database pick up a
    reference-match algorithm or signature-shape change (like this rebuild)
    on the next restart, regenerating every stale sidecar in the background
    with no user action and no re-upload, rather than waiting for the next
    settings save on each project. Unlike the other tests in this file,
    `WorkerManager.start` runs for real here — it's cheap (thread pools + a
    DB scan, no model loading) — since the sweep it performs is exactly
    what's under test."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-startup-sweep.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)

    app = create_app(settings)
    storage = app.state.storage
    project_stems = app.state.project_stems

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

    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
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
        }
        project = Project(
            import_batch=batch, name="Startup sweep project", manifest=manifest,
            status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
            mastering_reference=reference,
        )
        track = ProjectTrack(project=project, asset=asset, position=0)
        session.add_all([batch, asset, reference, project, track])
        session.commit()
        project_id, track_id = project.id, track.id

    _seed_prepared_stems(
        project_stems, project_id, track_id,
        {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
    )
    # No prepare_reference_match call here — the point is that the lifespan
    # startup sweep is what schedules it, with nothing else triggering it.
    assert project_stems.read_reference_match_meta(project_id, "5.1") is None

    with TestClient(app):
        deadline = time.monotonic() + 10
        meta = None
        while time.monotonic() < deadline:
            meta = project_stems.read_reference_match_meta(project_id, "5.1")
            if meta is not None:
                break
            time.sleep(0.05)

    assert meta is not None
    assert meta["curve"], "the startup sweep must compute a real curve, not just an empty stamp"

    engine.dispose()
