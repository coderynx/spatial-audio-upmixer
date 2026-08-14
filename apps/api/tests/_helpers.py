"""Shared non-fixture helpers for the ``apps/api`` test suite."""

import io

import numpy as np
import soundfile as sf

from upmixer.separation.stem_store import PlainStemStore


def _wav_bytes(frequency: float = 440.0) -> bytes:
    sample_rate = 48_000
    samples = np.arange(4_800) / sample_rate
    audio = np.column_stack([
        0.1 * np.sin(2 * np.pi * frequency * samples),
        0.1 * np.sin(2 * np.pi * (frequency + 2.0) * samples),
    ])
    output = io.BytesIO()
    sf.write(output, audio, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def _seed_prepared_stems(project_stems, project_id, track_id, stems, sample_rate=48_000):
    """Populate a track's plain stem store, as a real prepare_stems pass
    would — the reference-match precompute reads straight from this store
    (see worker_reference_match.py) and never runs separation itself."""
    PlainStemStore(str(project_stems.stem_dir(project_id, track_id))).write(stems, sample_rate)
