import time

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings


def test_worker_prepare_reference_match_computes_and_serves_fir(tmp_path, monkeypatch):
    """WorkerManager.prepare_reference_match precomputes the FIR + RMS-gain
    asset a project's reference-match preview needs (see
    docs/contracts/preview_export_parity.md Ledger D12), skips recompute when
    its signature is unchanged, and clears the asset when the reference is
    removed. Separation is mocked (see _fake_execute_plan) since only the
    hook plumbing and storage/signature logic are under test here — the
    algorithm itself is covered by test_match_reference.py."""
    from unittest.mock import patch

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    # These tests exercise signature/FIR/scheduling bookkeeping on the
    # assumption stems are already cached (the real invariant, guaranteed by
    # `project.prepared_stems` only going true after a real prepare_stems
    # pass) — not stems_cached()'s own miss-detection, which has its own
    # dedicated test below.
    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline.StemUpmixPipeline.stems_cached",
        lambda self, input_path: True,
    )

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
        # `_project_view` in apps/api/src/api.py) so the browser's
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


def test_worker_prepare_reference_match_applies_track_manifest_overrides(tmp_path, monkeypatch):
    """prepare_reference_match's asset job must carry a track's
    manifest_overrides (e.g. engine.stem_batch_size, set from
    StemsSection.tsx), exactly like worker.py's _run_project does when it
    originally cached the stems. Otherwise the stem-cache identity computed
    here (see _stem_cache_identity in stem_pipeline.py) disagrees with the
    cached entry, so this "cheap, stems-already-cached" precompute silently
    re-runs a full GPU separation pass instead of hitting the cache — the
    "Preparing reference EQ match" spinner then hangs for as long as
    separation takes."""
    from unittest.mock import patch

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch_overrides.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    # These tests exercise signature/FIR/scheduling bookkeeping on the
    # assumption stems are already cached (the real invariant, guaranteed by
    # `project.prepared_stems` only going true after a real prepare_stems
    # pass) — not stems_cached()'s own miss-detection, which has its own
    # dedicated test below.
    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline.StemUpmixPipeline.stems_cached",
        lambda self, input_path: True,
    )

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    captured_batch_sizes: list[int | None] = []

    def _fake_execute_plan(self_inner, plan, sep_path, sep_sr, stage_callback=None):
        captured_batch_sizes.append(self_inner.config.stem_batch_size)
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
            project_id = project.id

        manager = client.app.state.manager

        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _fake_execute_plan,
        ):
            manager.prepare_reference_match(project_id)

        assert captured_batch_sizes, "separation should have run at least once"
        assert all(size == 4 for size in captured_batch_sizes), (
            "track.manifest_overrides must reach the reference-match asset job "
            "so its stem-cache identity matches what stem-prep cached"
        )

    engine.dispose()


def test_worker_prepare_reference_match_skips_on_stem_cache_miss(tmp_path, monkeypatch):
    """When the project's stems don't actually hit the cache at the current
    config (e.g. the separation plan/engine changed since the stems were
    prepared, or settings drifted without a re-prepare), prepare_reference_match
    must bail rather than fall through to a full uncached separation pass on
    the caller's thread — that pass has no crash isolation and no progress
    reporting, and can run for many minutes pegging the GPU behind a static
    "Preparing reference EQ match" banner with no feedback. It must return
    cleanly (no exception raised out of the worker loop) and leave no FIR
    asset behind, so the frontend's fallback (original EQ, no reference
    match) applies until a real re-prepare repopulates the cache."""
    from unittest.mock import patch

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch_cache_miss.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)

    execute_plan_called = {"n": 0}

    def _fake_execute_plan(_self, plan, sep_path, sep_sr, stage_callback=None):
        execute_plan_called["n"] += 1
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
                import_batch=batch, name="Ref match cache-miss project", manifest=manifest,
                status="ready", prepared_stems=["Vocals"], requested_stems=["Vocals"],
                mastering_reference=reference,
            )
            track = ProjectTrack(project=project, asset=asset, position=0)
            session.add_all([batch, asset, reference, project, track])
            session.commit()
            project_id = project.id

        manager = client.app.state.manager

        # No prior prepare_stems pass ever ran for this track, so
        # stems_cached() genuinely misses — real production never reaches
        # here in this state (prepared_stems only goes true after a real,
        # isolated prepare_stems pass), but this is exactly the state a
        # drifted/rebuilt separation engine produces.
        with patch(
            "upmixer.separation.stem_pipeline.StemUpmixPipeline._execute_plan",
            _fake_execute_plan,
        ):
            manager.prepare_reference_match(project_id)

        assert execute_plan_called["n"] == 0, "must bail before running any separation"
        assert client.app.state.project_stems.read_reference_match_meta(project_id) is None
        assert not manager.reference_match_pending(project_id)

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-schedule.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    # These tests exercise signature/FIR/scheduling bookkeeping on the
    # assumption stems are already cached (the real invariant, guaranteed by
    # `project.prepared_stems` only going true after a real prepare_stems
    # pass) — not stems_cached()'s own miss-detection, which has its own
    # dedicated test below.
    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline.StemUpmixPipeline.stems_cached",
        lambda self, input_path: True,
    )

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-skip.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    # These tests exercise signature/FIR/scheduling bookkeeping on the
    # assumption stems are already cached (the real invariant, guaranteed by
    # `project.prepared_stems` only going true after a real prepare_stems
    # pass) — not stems_cached()'s own miss-detection, which has its own
    # dedicated test below.
    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline.StemUpmixPipeline.stems_cached",
        lambda self, input_path: True,
    )

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

    from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
    from upmixer_web.shared.models import ImportBatch, MasteringReference, MediaAsset, Project, ProjectTrack

    database_url = f"sqlite:///{tmp_path / 'refmatch-clear.db'}"
    settings = Settings(data_dir=tmp_path, database_url=database_url, worker_count=1)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.start", lambda _self: None)
    monkeypatch.setattr("upmixer_web.worker.WorkerManager.stop", lambda _self: None)
    # These tests exercise signature/FIR/scheduling bookkeeping on the
    # assumption stems are already cached (the real invariant, guaranteed by
    # `project.prepared_stems` only going true after a real prepare_stems
    # pass) — not stems_cached()'s own miss-detection, which has its own
    # dedicated test below.
    monkeypatch.setattr(
        "upmixer.separation.stem_pipeline.StemUpmixPipeline.stems_cached",
        lambda self, input_path: True,
    )

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
