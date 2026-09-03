"""Runs separation over a corpus and scores it against ground truth.

``separate_for_eval`` drives the public ``StemSeparator`` — the same
inference path production code uses — and records every setting that
affects its output, so scores are never reported without the configuration
that produced them (see ``docs/evaluation_harness.md``). ``evaluate_corpus``
takes a pluggable separation callable so tests can substitute a fast, offline
stand-in without downloading model weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import soundfile as sf

from upmixer.eval.corpus import ReferenceCorpus
from upmixer.eval.metrics import bleedless, fullness, sdr
from upmixer.eval.report import EvalReport, StemScore
from upmixer.separation.separator import DEFAULT_MODEL, StemSeparator
from upmixer.separation.stem_plan import ENSEMBLE_ALGORITHM, MODEL_ENSEMBLE

SeparateFn = Callable[[str], tuple[dict[str, np.ndarray], "RunSettings"]]


@dataclass
class RunSettings:
    """Inference configuration recorded alongside every score.

    Scores without settings are noise: a model swap, segment-size change, or
    ensemble config change all alter output, so every EvalReport carries the
    exact settings used to produce it.
    """

    model: str
    sample_rate: int
    segment_size: int | None = None
    overlap: int | None = None
    batch_size: int | None = None
    ensemble_algorithm: str | None = None
    ensemble_models: tuple[str, ...] | None = None


def separate_for_eval(
    mixture_path: str,
    sample_rate: int,
    model: str = DEFAULT_MODEL,
    batch_size: int | None = None,
    segment_size: int | None = None,
    chunk_duration_s: float | None = None,
    overlap: int | None = None,
    stem_ensemble: bool = False,
) -> tuple[dict[str, np.ndarray], RunSettings]:
    """Separate a mixture with the real ``StemSeparator`` and record settings.

    Args:
        mixture_path: Path to the mixture audio file.
        sample_rate:  Output sample rate for separated stems.
        model:        Model filename (registry name), defaults to the
                      package's default model.
        batch_size, segment_size, chunk_duration_s, overlap: Forwarded to
            ``StemSeparator``; ``None`` selects its backend-aware defaults.
        stem_ensemble: Run the production fixed BS-Roformer-SW + SCNet
            primary-stem ensemble.

    Returns:
        (stems, settings) — canonical stem name -> (n_samples, 2) float32
        array, and the RunSettings actually used.
    """
    if stem_ensemble:
        if model != DEFAULT_MODEL:
            raise ValueError(
                "stem_ensemble uses the registered BS-Roformer-SW primary model"
            )
        from upmixer.config import UpmixConfig
        from upmixer.separation.stem_pipeline import StemUpmixPipeline

        pipeline = StemUpmixPipeline(UpmixConfig(
            output_sample_rate=sample_rate,
            stem_batch_size=batch_size,
            stem_segment_size=segment_size,
            stem_chunk_duration_s=chunk_duration_s,
            stem_overlap=overlap,
            stem_ensemble=True,
        ))
        try:
            stems = pipeline._separate(
                mixture_path, None, lambda _message, _fraction: None
            ).all_stems
        finally:
            pipeline.close()
    else:
        separator = StemSeparator(
            model=model,
            sample_rate=sample_rate,
            batch_size=batch_size,
            segment_size=segment_size,
            chunk_duration_s=chunk_duration_s,
            overlap=overlap,
        )
        try:
            stems = separator.separate(mixture_path)
        finally:
            separator.close()
    settings = RunSettings(
        model=model,
        sample_rate=sample_rate,
        segment_size=segment_size,
        batch_size=batch_size,
        overlap=overlap,
        ensemble_algorithm=ENSEMBLE_ALGORITHM if stem_ensemble else None,
        ensemble_models=(model, MODEL_ENSEMBLE) if stem_ensemble else None,
    )
    return stems, settings


def evaluate_corpus(
    corpus: ReferenceCorpus,
    separate_fn: SeparateFn,
    sample_rate: int,
) -> EvalReport:
    """Score a separation run over every item in a corpus.

    For each corpus item, calls ``separate_fn(item.mixture)`` and compares
    every stem the estimate and the reference share by name against the
    reference audio, computing SDR + fullness + bleedless (never SDR alone,
    per the harness requirements).

    Args:
        corpus:      Reference corpus to evaluate against.
        separate_fn: Callable producing (stems, RunSettings) for a mixture
            path — either ``separate_for_eval`` (bound to fixed settings via
            ``functools.partial``) for real inference, or a test double.
        sample_rate: Sample rate of the reference audio (used for the
            magnitude-STFT fullness/bleedless computation).

    Returns:
        EvalReport with one StemScore per (item, shared stem) and the
        RunSettings from the last item processed (settings are expected to
        be constant across a single evaluation run).
    """
    scores: list[StemScore] = []
    settings: RunSettings | None = None
    for item in corpus.items:
        estimate_stems, settings = separate_fn(item.mixture)
        for stem_name, ref_path in item.stems.items():
            if stem_name not in estimate_stems:
                continue
            reference, _ = sf.read(ref_path, dtype="float32", always_2d=True)
            estimate = estimate_stems[stem_name]
            scores.append(
                StemScore(
                    stem=stem_name,
                    category=item.category,
                    sdr=sdr(reference, estimate),
                    fullness=fullness(reference, estimate, sample_rate),
                    bleedless=bleedless(reference, estimate, sample_rate),
                )
            )
    if settings is None:
        raise ValueError("corpus has no items to evaluate")
    return EvalReport(settings=settings, scores=scores)
