import json
import multiprocessing
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("sqlalchemy")

from upmixer_web.database import create_database_engine, create_session_factory, upgrade_database
from upmixer_web.models import ImportBatch, MediaAsset, Project, ProjectTrack
from upmixer_web.project_storage import PREVIEW_SAMPLE_RATE, ProjectStemStorage, _write_preview


@pytest.fixture
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'storage.db'}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    yield create_session_factory(engine)
    engine.dispose()


def _seed_project_track(session, suffix="a"):
    batch = ImportBatch(kind="track", title="Song")
    asset = MediaAsset(
        import_batch=batch,
        filename=f"song-{suffix}.wav",
        relative_path=f"song-{suffix}.wav",
        storage_key=f"objects/song-{suffix}.wav",
        sha256="0" * 64,
        size_bytes=1,
    )
    project = Project(import_batch=batch, name="Preview project", manifest={})
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
        entry = storage.track_root(project.id, track.id) / "abc123"
        entry.mkdir(parents=True)
        full_path = entry / "Vocals.wav"
        sf.write(str(full_path), audio, sample_rate, subtype="PCM_16")
        (entry / "metadata.json").write_text(
            json.dumps({"sep_sr": sample_rate, "stem_keys": ["Vocals"]}), encoding="utf-8"
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
        _write_preview(Path(source_str), Path(destination_str))

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
