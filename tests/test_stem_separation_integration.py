"""Optional real-model accuracy/performance check.

Set UPMIXER_STEM_TEST_AUDIO to a representative stereo file, then run:
    pytest -m perf tests/test_stem_separation_integration.py -s
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest
import soundfile as sf

from upmixer.separation.separator import DEFAULT_MODEL, StemSeparator


@pytest.mark.perf
def test_automatic_batch_matches_full_precision_baseline():
    input_path = os.environ.get("UPMIXER_STEM_TEST_AUDIO")
    if not input_path:
        pytest.skip("set UPMIXER_STEM_TEST_AUDIO for real-model benchmark")
    pytest.importorskip("audio_separator")

    sample_rate = sf.info(input_path).samplerate
    model = os.environ.get("UPMIXER_STEM_TEST_MODEL", DEFAULT_MODEL)

    with StemSeparator(model=model, sample_rate=sample_rate, batch_size=1) as baseline:
        started = time.monotonic()
        expected = baseline.separate(input_path)
        baseline_s = time.monotonic() - started

    with StemSeparator(model=model, sample_rate=sample_rate) as optimized:
        started = time.monotonic()
        actual = optimized.separate(input_path)
        optimized_s = time.monotonic() - started

    assert set(actual) == set(expected)
    for stem in expected:
        assert actual[stem].shape == expected[stem].shape
        reference = expected[stem].astype(np.float64).ravel()
        candidate = actual[stem].astype(np.float64).ravel()
        if np.max(np.abs(reference)) < 1e-12:
            np.testing.assert_allclose(candidate, reference, atol=1e-12)
            continue
        correlation = np.corrcoef(reference, candidate)[0, 1]
        assert correlation >= 0.999999, stem

    print(
        f"baseline={baseline_s:.2f}s optimized={optimized_s:.2f}s "
        f"speedup={baseline_s / optimized_s:.2f}x"
    )
