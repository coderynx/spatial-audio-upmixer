"""Real-model check for the separation evaluation harness.

Runs the harness against the synthetic corpus with the default model and
prints the per-stem SDR/fullness/bleedless report. Requires audio-separator
and a model download; skipped unless run with -m perf.

    pytest -m perf -k eval -s
"""
from __future__ import annotations

from functools import partial

import numpy as np
import pytest

from upmixer.eval.corpus import synthetic_corpus
from upmixer.eval.harness import evaluate_corpus, separate_for_eval
from upmixer.eval.report import format_report
from upmixer.separation.separator import DEFAULT_MODEL


@pytest.mark.perf
def test_eval_harness_reports_default_model_quality(tmp_path):
    pytest.importorskip("audio_separator")

    sample_rate = 44100
    corpus = synthetic_corpus(sample_rate=sample_rate, out_dir=str(tmp_path / "corpus"))
    separate_fn = partial(separate_for_eval, sample_rate=sample_rate, model=DEFAULT_MODEL)

    report = evaluate_corpus(corpus, separate_fn, sample_rate=sample_rate)

    assert report.scores, "expected at least one scored stem"
    for mean_sdr, mean_fullness, mean_bleedless in report.by_stem().values():
        assert np.isfinite(mean_sdr)
        assert 0.0 <= mean_fullness <= 1.0
        assert 0.0 <= mean_bleedless <= 1.0

    print(format_report(report))
