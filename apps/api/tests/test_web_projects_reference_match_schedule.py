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

    from upmixer.mastering.match_reference import ReferenceMatchProcessor
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-schedule.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    release = threading.Event()
    call_count = {"n": 0}
    original_compute_curve = ReferenceMatchProcessor.compute_curve

    def _blocking_compute_curve(self_inner, channels, lfe_key="LFE"):
        call_count["n"] += 1
        release.wait(timeout=5)
        return original_compute_curve(self_inner, channels, lfe_key)

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
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        _seed_prepared_stems(
            client.app.state.project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )
        # start() is mocked out above so the real dispatch/job pools never
        # spin up; create just the refmatch executor start() would normally
        # create, so schedule_reference_match has somewhere to submit to.
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch")
        try:
            with patch.object(ReferenceMatchProcessor, "compute_curve", _blocking_compute_curve):
                manager.schedule_reference_match(project_id)
                assert manager.reference_match_pending(project_id)
                # Fired while the first pass is still blocked in
                # compute_curve — must coalesce into the trailing
                # check rather than each starting a concurrent full-song pass.
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
        assert client.app.state.project_stems.read_reference_match_meta(project_id, "5.1") is not None

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-skip.db'}"
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
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        _seed_prepared_stems(
            manager.project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch-skip")
        try:
            # Populate an up-to-date asset first, exactly like a real settings
            # save that changed something reference-match-relevant would.
            manager.prepare_reference_match(project_id)
            assert manager.project_stems.read_reference_match_meta(project_id, "5.1") is not None

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-clear.db'}"
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
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        _seed_prepared_stems(
            manager.project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )
        manager._refmatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-refmatch-clear")
        try:
            manager.prepare_reference_match(project_id)
            assert manager.project_stems.read_reference_match_meta(project_id, "5.1") is not None

            with factory() as session:
                project = session.get(Project, project_id)
                project.mastering_reference_id = None
                session.commit()

            manager.schedule_reference_match(project_id)
            deadline = time.monotonic() + 5
            while manager.reference_match_pending(project_id) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert manager.project_stems.read_reference_match_meta(project_id, "5.1") is None
        finally:
            manager._refmatch_executor.shutdown(wait=True)

    engine.dispose()
