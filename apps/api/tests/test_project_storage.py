import json
import multiprocessing
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("sqlalchemy")

from upmixer_web.features.projects.storage import (
    _PREVIEW_VORBIS_COMPRESSION_LEVEL,
    PEAK_BINS,
    PREVIEW_SAMPLE_RATE,
    ProjectStemStorage,
    _compute_peaks,
    _write_preview,
)
from upmixer_web.shared.database import create_database_engine, create_session_factory, upgrade_database
from upmixer_web.shared.models import ImportBatch, MediaAsset, Project, ProjectTrack


@pytest.fixture
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'storage.db'}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    yield create_session_factory(engine)
    engine.dispose()


def _seed_project_track(session, suffix="a", requested_stems=("Vocals",)):
    batch = ImportBatch(kind="track", title="Song")
    asset = MediaAsset(
        import_batch=batch,
        filename=f"song-{suffix}.wav",
        relative_path=f"song-{suffix}.wav",
        storage_key=f"objects/song-{suffix}.wav",
        sha256="0" * 64,
        size_bytes=1,
    )
    project = Project(
        import_batch=batch, name="Preview project", manifest={},
        requested_stems=list(requested_stems),
    )
    track = ProjectTrack(project=project, asset=asset, position=0)
    session.add_all([batch, asset, project, track])
    session.commit()
    return project, track


def test_catalogue_track_writes_low_rate_preview_alongside_full_stem(tmp_path):
    engine_url = f"sqlite:///{tmp_path / 'catalogue.db'}"
    upgrade_database(engine_url)
    engine = create_database_engine(engine_url)
    factory = create_session_factory(engine)
    storage = ProjectStemStorage(tmp_path / "project-stems")

    with factory() as session:
        project, track = _seed_project_track(session)

        sample_rate = 48_000
        samples = np.arange(sample_rate) / sample_rate
        audio = np.column_stack([
            0.2 * np.sin(2 * np.pi * 220 * samples),
            0.2 * np.sin(2 * np.pi * 220 * samples),
        ])
        entry = storage.stem_dir(project.id, track.id)
        entry.mkdir(parents=True)
        full_path = entry / "Vocals.wav"
        sf.write(str(full_path), audio, sample_rate, subtype="PCM_16")
        (entry / "stems.json").write_text(
            json.dumps({"sample_rate": sample_rate, "stem_keys": ["Vocals"]}), encoding="utf-8"
        )

        rows = storage.catalogue_track(session, project, track, generation=1)
        session.commit()

    assert len(rows) == 1
    stem = rows[0]
    assert stem.preview_relative_path is not None
    preview_path = storage.resolve(stem.preview_relative_path)
    assert preview_path.suffix == ".ogg"
    assert preview_path.is_file()
    assert stem.preview_size_bytes == preview_path.stat().st_size
    assert stem.preview_size_bytes < stem.size_bytes

    preview_audio, preview_rate = sf.read(str(preview_path))
    assert preview_rate == PREVIEW_SAMPLE_RATE
    assert preview_audio.shape[0] > 0

    engine.dispose()


def _seed_stem_store(storage, project, track, stem_keys, sample_rate=48_000, seconds=1):
    samples = np.arange(sample_rate * seconds) / sample_rate
    entry = storage.stem_dir(project.id, track.id)
    entry.mkdir(parents=True)
    for index, stem_key in enumerate(stem_keys):
        tone = 0.5 * np.sin(2 * np.pi * (220 * (index + 1)) * samples)
        sf.write(str(entry / f"{stem_key}.wav"), np.column_stack([tone, tone]), sample_rate, subtype="PCM_16")
    (entry / "stems.json").write_text(
        json.dumps({"sample_rate": sample_rate, "stem_keys": list(stem_keys)}), encoding="utf-8"
    )
    return entry


def test_catalogue_track_excludes_bonus_stems_not_requested(tmp_path):
    """stem_pipeline.py caches parent stems (e.g. `Drums`) alongside their
    children (`Kick`, `Snare`, ...) as a free byproduct even when only the
    children were requested (see its "Models often emit more stems than
    requested" comment) — surfacing the parent as a playable track would
    double the drum/vocal content in the monitor mix. catalogue_track must
    only expose stems the project actually requested."""
    engine_url = f"sqlite:///{tmp_path / 'bonus.db'}"
    upgrade_database(engine_url)
    engine = create_database_engine(engine_url)
    factory = create_session_factory(engine)
    storage = ProjectStemStorage(tmp_path / "project-stems")

    with factory() as session:
        project, track = _seed_project_track(session, requested_stems=["Kick", "Snare"])
        _seed_stem_store(storage, project, track, ["Kick", "Snare", "Drums"])
        rows = storage.catalogue_track(session, project, track, generation=1)
        session.commit()

    assert {row.stem_key for row in rows} == {"Kick", "Snare"}

    engine.dispose()


def test_catalogue_track_writes_track_peaks_for_every_stem(tmp_path):
    engine_url = f"sqlite:///{tmp_path / 'peaks.db'}"
    upgrade_database(engine_url)
    engine = create_database_engine(engine_url)
    factory = create_session_factory(engine)
    storage = ProjectStemStorage(tmp_path / "project-stems")

    with factory() as session:
        project, track = _seed_project_track(session, requested_stems=["Vocals", "Drums"])
        _seed_stem_store(storage, project, track, ["Vocals", "Drums"])
        storage.catalogue_track(session, project, track, generation=3)
        session.commit()
        project_id, track_id = project.id, track.id
        assert track.peaks_relative_path is not None
        assert track.peaks_duration_seconds == pytest.approx(1.0, abs=0.05)

    meta = storage.read_track_peaks_meta(project_id, track_id)
    assert meta["stems"] == ["Vocals", "Drums"]
    assert meta["bins"] == PEAK_BINS
    assert meta["generation"] == 3

    path = storage.track_peaks_path(project_id, track_id)
    assert path.stat().st_size == 2 * PEAK_BINS * 2

    envelope = np.frombuffer(path.read_bytes(), dtype=np.int8).reshape(-1, PEAK_BINS, 2)
    assert envelope.shape[0] == 2
    # A half-scale tone: the negative column stays below zero, the positive
    # column above it, and neither reaches the int8 rail.
    assert envelope[:, :, 0].min() < 0 < envelope[:, :, 1].max()
    assert np.abs(envelope).max() < 127

    engine.dispose()


def test_rebuild_track_peaks_backfills_from_existing_previews(tmp_path):
    engine_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    upgrade_database(engine_url)
    engine = create_database_engine(engine_url)
    factory = create_session_factory(engine)
    storage = ProjectStemStorage(tmp_path / "project-stems")

    with factory() as session:
        project, track = _seed_project_track(session, requested_stems=["Vocals", "Drums"])
        _seed_stem_store(storage, project, track, ["Vocals", "Drums"])
        rows = storage.catalogue_track(session, project, track, generation=1)
        session.commit()
        project_id, track_id = project.id, track.id

        storage.track_peaks_path(project_id, track_id).unlink()
        (storage.root / project_id / track_id / "peaks.json").unlink()
        assert storage.read_track_peaks_meta(project_id, track_id) is None

        storage.rebuild_track_peaks(track, rows, generation=1)
        session.commit()

    meta = storage.read_track_peaks_meta(project_id, track_id)
    assert meta["stems"] == ["Vocals", "Drums"]
    assert storage.track_peaks_path(project_id, track_id).stat().st_size == 2 * PEAK_BINS * 2

    engine.dispose()


def test_compute_peaks_handles_silence_and_clips_shorter_than_the_bin_grid():
    silent = _compute_peaks(np.zeros((1000, 2)))
    assert silent.shape == (PEAK_BINS, 2)
    assert not silent.any()

    short = _compute_peaks(np.full((3, 1), 0.5))
    assert short.shape == (PEAK_BINS, 2)
    assert short[:, 1].max() == 64

    empty = _compute_peaks(np.zeros((0, 2)))
    assert empty.shape == (PEAK_BINS, 2)

    loud = _compute_peaks(np.column_stack([np.tile([2.0, -2.0], 8192)]))
    assert loud[:, 1].max() == 127
    assert loud[:, 0].min() == -127


def test_delete_project_removes_directory_but_keeps_other_projects(tmp_path):
    engine_url = f"sqlite:///{tmp_path / 'delete.db'}"
    upgrade_database(engine_url)
    engine = create_database_engine(engine_url)
    factory = create_session_factory(engine)
    storage = ProjectStemStorage(tmp_path / "project-stems")

    with factory() as session:
        project, track = _seed_project_track(session, "a")
        other_project, other_track = _seed_project_track(session, "b")

        entry = storage.track_root(project.id, track.id) / "abc123"
        entry.mkdir(parents=True)
        (entry / "stem.wav").write_bytes(b"data")

        other_entry = storage.track_root(other_project.id, other_track.id) / "def456"
        other_entry.mkdir(parents=True)
        (other_entry / "stem.wav").write_bytes(b"data")

    storage.delete_project(project.id)

    assert not (storage.root / project.id).exists()
    assert (storage.root / other_project.id).is_dir()

    engine.dispose()


def test_delete_project_is_a_noop_for_unknown_project(tmp_path):
    storage = ProjectStemStorage(tmp_path / "project-stems")
    storage.delete_project("does-not-exist")


def _write_preview_on_a_thread(source_str: str, destination_str: str) -> None:
    """Run _write_preview on a background thread, matching WorkerManager's usage.

    Must run in a subprocess: libsndfile's OGG/Vorbis encoder can overflow a
    background thread's default (small) stack for long tracks, crashing the
    whole process with SIGBUS/SIGSEGV rather than raising a Python exception.
    """
    def run() -> None:
        _write_preview(
            Path(source_str), Path(destination_str),
            sample_rate=PREVIEW_SAMPLE_RATE, compression_level=_PREVIEW_VORBIS_COMPRESSION_LEVEL,
        )

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()


def test_write_preview_on_background_thread_does_not_crash_for_long_tracks(tmp_path):
    sample_rate = 48_000
    duration_s = 200
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal((sample_rate * duration_s, 2)) * 0.1).astype(np.float32)
    source = tmp_path / "long.wav"
    sf.write(str(source), audio, sample_rate, subtype="FLOAT")
    destination = tmp_path / "long.preview.ogg"

    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=_write_preview_on_a_thread, args=(str(source), str(destination)))
    process.start()
    process.join(timeout=120)

    assert process.exitcode == 0
    assert destination.is_file()
