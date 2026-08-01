import time

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer.separation.stem_store import PlainStemStore
from upmixer_web.api import create_app
from upmixer_web.settings import Settings


def _seed_prepared_stems(project_stems, project_id, track_id, stems, sample_rate=48_000):
    """Populate a track's plain stem store, as a real prepare_stems pass
    would — the reference-match precompute reads straight from this store
    (see worker_reference_match.py) and never runs separation itself."""
    PlainStemStore(str(project_stems.stem_dir(project_id, track_id))).write(stems, sample_rate)


def test_worker_prepare_reference_match_computes_and_serves_fir(tmp_path, monkeypatch):
    """WorkerManager.prepare_reference_match precomputes the FIR + RMS-gain
    asset a project's reference-match preview needs (see
    docs/contracts/preview_export_parity.md Ledger D12), skips recompute when
    its signature is unchanged, and clears the asset when the reference is
    removed. Stems are pre-seeded into the plain stem store (see
    _seed_prepared_stems) exactly like a real prepare_stems pass would leave
    them — the precompute reads them directly and never runs separation
    itself, so only the hook plumbing and storage/signature logic are under
    test here; the reference-match algorithm itself is covered by
    test_match_reference.py."""
    from unittest.mock import patch

    from upmixer.mastering.match_reference import ReferenceMatchProcessor
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch.db'}"
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
                import_batch=batch, name="Ref match project", manifest=manifest,
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

        meta = project_stems.read_reference_match_meta(project_id)
        assert meta is not None
        assert meta["channels"], "spectral FIRs should be present with spectrum=True, strength=1.0"
        assert project_stems.reference_match_fir_path(project_id) is not None
        first_signature = meta["signature"]

        view = client.get(f"/api/v1/projects/{project_id}").json()
        assert view["reference_match"] is not None
        assert view["reference_match"]["fir_url"]
        # The URL is versioned with the asset's signature (see
        # `_project_view` in apps/api/src/api.py) so the browser's
        # fir_url-keyed decode cache (useStemPreview.ts's
        # refMatchBufferCache) is naturally busted on a real recompute
        # instead of serving a stale FIR for the AudioContext's lifetime.
        assert f"v={first_signature}" in view["reference_match"]["fir_url"]
        fir_response = client.get(view["reference_match"]["fir_url"])
        assert fir_response.status_code == 200
        assert fir_response.headers["content-type"].startswith("audio/")

        call_count = {"n": 0}
        original_compute_filters = ReferenceMatchProcessor.compute_channel_filters

        def _counting_compute_filters(self_inner, channels):
            call_count["n"] += 1
            return original_compute_filters(self_inner, channels)

        with patch.object(ReferenceMatchProcessor, "compute_channel_filters", _counting_compute_filters):
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
        with patch.object(ReferenceMatchProcessor, "compute_channel_filters", _counting_compute_filters):
            manager.prepare_reference_match(project_id)
        assert call_count["n"] == 0, "strength/rms changes must not recompute"
        assert project_stems.read_reference_match_meta(project_id)["signature"] == first_signature

        # max_db does change the FIR — must trigger a recompute. Note this
        # doesn't re-run separation: stems come straight from the plain stem
        # store (separation-affecting config is untouched) — only the
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
        with patch.object(ReferenceMatchProcessor, "compute_channel_filters", _counting_compute_filters):
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


def test_worker_prepare_reference_match_applies_track_manifest_overrides(tmp_path, monkeypatch):
    """prepare_reference_match's asset job must carry a track's
    manifest_overrides (e.g. engine.stem_batch_size, set from
    StemsSection.tsx) into the config it builds, exactly like worker.py's
    _run_project does for real preparation — otherwise a track-specific
    tuning knob silently only applies at prepare time and not to the
    reference-match precompute's own mix pass."""
    from upmixer.separation.stem_pipeline import StemUpmixPipeline
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch_overrides.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    captured_batch_sizes: list[int | None] = []
    original_init = StemUpmixPipeline.__init__

    def _capturing_init(self_inner, *args, **kwargs):
        captured_batch_sizes.append(kwargs["config"].stem_batch_size)
        return original_init(self_inner, *args, **kwargs)

    monkeypatch.setattr(StemUpmixPipeline, "__init__", _capturing_init)

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
                import_batch=batch, name="Ref match overrides project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(
                project=project, asset=asset, position=0,
                manifest_overrides={"engine": {"stem_batch_size": 4}},
            )
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id, track_id = project.id, track.id

        manager = client.app.state.manager
        _seed_prepared_stems(
            client.app.state.project_stems, project_id, track_id,
            {"Vocals": np.full((len(samples), 2), 0.2, dtype=np.float32)},
        )

        manager.prepare_reference_match(project_id)

        assert captured_batch_sizes, "the reference-match pass should have built a pipeline config"
        assert all(size == 4 for size in captured_batch_sizes), (
            "track.manifest_overrides must reach the config the reference-match pass builds"
        )

    engine.dispose()


def test_worker_prepare_reference_match_skips_when_stems_not_prepared(tmp_path, monkeypatch):
    """When a track's plain stem store isn't populated yet (no prepare_stems
    pass has run, or a re-prepare is pending), prepare_reference_match must
    bail rather than fall through to a full unisolated separation pass on the
    caller's thread — that pass has no crash isolation and no progress
    reporting, and can run for many minutes pegging the GPU behind a static
    "Preparing reference EQ match" banner with no feedback. It must return
    cleanly (no exception raised out of the worker loop) and leave no FIR
    asset behind, so the frontend's fallback (original EQ, no reference
    match) applies until a real prepare populates the store. It must also
    stamp a signature-matching empty meta record so an unrelated settings
    save (e.g. a stem mute/solo toggle, which never changes the signature)
    doesn't see "no meta" and reopen reference_match_pending on every save
    while stems stay unprepared."""
    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch_cache_miss.db'}"
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
                import_batch=batch, name="Ref match cache-miss project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager

        # No prepare_stems pass ever ran for this track, so its plain stem
        # store is empty — real production never reaches here once
        # prepared_stems is true (it only goes true after a real, isolated
        # prepare_stems pass), but this is exactly the state a pending
        # re-prepare leaves the store in.
        manager.prepare_reference_match(project_id)

        meta = client.app.state.project_stems.read_reference_match_meta(project_id)
        assert meta is not None
        assert meta["channels"] == []
        assert client.app.state.project_stems.reference_match_fir_path(project_id) is None
        assert not manager.reference_match_pending(project_id)

        # An unrelated re-schedule (e.g. a stem mute/solo save) must not
        # reopen pending or retry the doomed mix pass: the signature hasn't
        # changed, so the stamped empty meta above satisfies
        # `_reference_match_needs_work` on its own.
        manager.schedule_reference_match(project_id)
        assert not manager.reference_match_pending(project_id)

        from upmixer_web.features.projects.worker_reference_match import _reference_match_needs_work

        with factory() as session:
            project = session.get(Project, project_id)
            assert not _reference_match_needs_work(project, client.app.state.project_stems), (
                "the empty stamp at the current stem_generation must satisfy "
                "needs_work so it doesn't reopen pending for an unrelated save"
            )
            # A real stem re-prepare bumps stem_generation (worker.py's
            # _run_project) — that must invalidate the stale empty stamp so
            # the FIR gets a real chance to compute once stems are cached,
            # rather than being stuck empty forever at the old signature.
            project.stem_generation += 1
            session.commit()

        with factory() as session:
            project = session.get(Project, project_id)
            assert _reference_match_needs_work(project, client.app.state.project_stems), (
                "a stem_generation bump (real re-prepare) must invalidate the "
                "stale empty stamp so the reference match can recompute for real"
            )

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
    original_compute_filters = ReferenceMatchProcessor.compute_channel_filters

    def _blocking_compute_filters(self_inner, channels):
        call_count["n"] += 1
        release.wait(timeout=5)
        return original_compute_filters(self_inner, channels)

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
            with patch.object(ReferenceMatchProcessor, "compute_channel_filters", _blocking_compute_filters):
                manager.schedule_reference_match(project_id)
                assert manager.reference_match_pending(project_id)
                # Fired while the first pass is still blocked in
                # compute_channel_filters — must coalesce into the trailing
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
