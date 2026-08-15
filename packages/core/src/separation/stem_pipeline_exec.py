"""Separation-plan execution: staged model runs and silence-skipped runs."""
from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Callable

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.separation.drum_remask import reproject_drum_pieces
from upmixer.separation.separator import StemSeparator
from upmixer.separation.stem_plan import DRUM_SUB_STEMS, MODEL_DRUMS, SeparationPlan

_log = logging.getLogger("upmixer")

GetSeparator = Callable[[str, int], StemSeparator]
StageCallback = Callable[[int, int, str, frozenset[str]], None]


def temporary_wav_path(prefix: str) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".wav", prefix=prefix, delete=False)
    path = handle.name
    handle.close()
    return path


def cacheable_plan_stems(plan: SeparationPlan) -> frozenset[str]:
    """All public outputs produced by a plan at no extra inference cost."""
    return frozenset(
        stem
        for task in plan.tasks
        for stem in task.output_stems
        if not stem.startswith("_")
    )


def _remask_drum_pieces(
    loaded: dict[str, np.ndarray], parent_path: str, alpha: float
) -> None:
    """Re-derive the kit pieces in *loaded* from the parent Drums file, in place."""
    pieces = {
        name: audio for name, audio in loaded.items() if name in DRUM_SUB_STEMS
    }
    if not pieces:
        return
    parent, parent_sr = sf.read(parent_path, dtype="float32", always_2d=True)
    loaded.update(reproject_drum_pieces(parent, pieces, parent_sr, alpha))


def execute_plan(
    get_separator: GetSeparator,
    plan: SeparationPlan,
    sep_path: str,
    sep_sr: int,
    stage_callback: StageCallback | None = None,
    cfg: UpmixConfig | None = None,
) -> dict[str, np.ndarray]:
    """Execute all tasks in the plan against one audio zone (sep_path).

    Manages intermediate on-disk files between stages: whichever stems a
    later task's ``input_source`` names (``_crowd_other``, ``_deux_inst``,
    ``Drums``, ``Vocals``) are kept on disk until consumed. Intermediate
    files not in the final requested stems are deleted after all stages
    complete.

    Args:
        stage_callback: Optional callable ``(stage_idx, n_tasks, model,
            output_stems)`` invoked before each model stage runs.
        cfg: Configuration for the post-separation passes run here (drum
            kit-piece re-masking). ``None`` runs none of them.

    Returns a dict of canonical_name → ndarray for all requested stems.
    """
    all_loaded: dict[str, np.ndarray] = {}
    all_disk: dict[str, str] = {}

    later_inputs: frozenset[str] = frozenset(
        t.input_source for t in plan.tasks if t.input_source != "original"
    )

    n_tasks = len(plan.tasks)
    for stage_idx, task in enumerate(plan.tasks):
        if stage_callback is not None:
            stage_callback(stage_idx, n_tasks, task.model, task.output_stems)
        _log.info(
            "  [stage %d/%d] model=%s  input=%s  keep_on_disk=%s",
            stage_idx + 1,
            n_tasks,
            task.model,
            task.input_source,
            sorted(task.output_stems & later_inputs) or "(none)",
        )

        if task.input_source != "original" and task.input_source not in all_disk:
            available = sorted(all_disk.keys()) or ["(none)"]
            raise RuntimeError(
                f"Stage {stage_idx + 1} needs intermediate stem "
                f"'{task.input_source}' on disk, but it was not produced by "
                f"any previous stage.\n"
                f"Available on-disk stems: {available}\n"
                f"Likely cause: the model that should produce "
                f"'{task.input_source}' outputs a different filename tag — "
                f"run with --verbose (-v) to see raw output filenames and "
                f"update STEM_NAME_MAP in separator.py if needed."
            )

        input_path_for_task = (
            sep_path if task.input_source == "original"
            else all_disk[task.input_source]
        )

        keep_on_disk = task.output_stems & later_inputs

        sep = get_separator(task.model, sep_sr)
        loaded, on_disk = sep.separate_to_file(input_path_for_task, keep_on_disk)

        for name, path in on_disk.items():
            stable_path = temporary_wav_path("upmixer_intermediate_")
            try:
                os.replace(path, stable_path)
            except OSError:
                if os.path.exists(stable_path):
                    os.unlink(stable_path)
                raise
            on_disk[name] = stable_path

        if (
            cfg is not None
            and cfg.stem_drum_remask
            and task.model == MODEL_DRUMS
            and task.input_source in all_disk
        ):
            _log.info(
                "  [stage %d/%d] drum re-mask alpha=%.2f onto parent %s",
                stage_idx + 1,
                n_tasks,
                cfg.stem_drum_remask_alpha,
                task.input_source,
            )
            _remask_drum_pieces(
                loaded, all_disk[task.input_source], cfg.stem_drum_remask_alpha
            )

        _log.info(
            "  [stage %d/%d] produced: loaded=%s  on_disk=%s",
            stage_idx + 1,
            n_tasks,
            sorted(loaded.keys()) or "(none)",
            sorted(on_disk.keys()) or "(none)",
        )

        for name, audio in loaded.items():
            if not name.startswith("_"):
                all_loaded[name] = audio

        all_disk.update(on_disk)

    for name, path in all_disk.items():
        if not name.startswith("_") and name not in all_loaded:
            audio, _ = sf.read(path, dtype="float32", always_2d=True)
            if audio.shape[1] == 1:
                audio = np.concatenate([audio, audio], axis=1)
            all_loaded[name] = audio

    for name, path in all_disk.items():
        if name not in plan.requested_stems:
            try:
                os.unlink(path)
            except OSError:
                pass

    _log.info("  All stages complete. Produced stems: %s", sorted(all_loaded.keys()))
    return all_loaded


def execute_plan_with_silence_skip(
    get_separator: GetSeparator,
    plan: SeparationPlan,
    zone_audio: np.ndarray,
    sr: int,
    sep_sr: int,
    cfg: UpmixConfig,
    original_path: str | None = None,
    stage_callback: StageCallback | None = None,
) -> dict[str, np.ndarray]:
    """Run stem separation on active spans only, skipping silent regions.

    Detects contiguous silent runs in *zone_audio*, separates only the
    active portions, and stitches the per-stem outputs back into
    full-length arrays with a linear crossfade at each boundary.

    Returns the same dict shape as :func:`execute_plan`:
    ``{stem_name: (n_sep_samples, 2) float32}``.
    """
    from upmixer.separation.silence import (
        find_active_spans,
        write_crossfaded_span,
    )

    n_sr = len(zone_audio)
    silence_started = time.monotonic()
    spans = find_active_spans(
        zone_audio,
        sr,
        threshold_db=cfg.stem_silence_threshold_db,
        min_silence_s=cfg.stem_silence_min_duration_s,
        pad_ms=cfg.stem_silence_pad_ms,
    )
    _log.debug(
        "  Timing silence-detection=%.3fs",
        time.monotonic() - silence_started,
    )

    n_sep = int(round(n_sr * sep_sr / sr)) if sep_sr != sr else n_sr

    if not spans:
        _log.info("  Silence-skip: zone is entirely silent — skipping separator")
        return {
            name: np.zeros((n_sep, 2), dtype=np.float32)
            for name in cacheable_plan_stems(plan)
        }

    if len(spans) == 1 and spans[0] == (0, n_sr):
        if original_path is not None:
            _log.debug("  Silence-skip: full-active fast path uses source file")
            return execute_plan(
                get_separator, plan, original_path, sep_sr, stage_callback, cfg
            )
        tmp = temporary_wav_path("upmixer_full_")
        try:
            sf.write(tmp, zone_audio, sr, subtype="FLOAT")
            return execute_plan(get_separator, plan, tmp, sep_sr, stage_callback, cfg)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    _log.info("  Silence-skip: %d active span(s)", len(spans))
    fade_samples = max(0, int(cfg.stem_silence_crossfade_ms / 1000.0 * sep_sr))
    stem_names = cacheable_plan_stems(plan)
    result = {
        stem_name: np.zeros((n_sep, 2), dtype=np.float32)
        for stem_name in stem_names
    }

    for s_start, s_end in spans:
        span_audio = zone_audio[s_start:s_end]
        tmp = temporary_wav_path("upmixer_span_")
        try:
            sf.write(tmp, span_audio, sr, subtype="FLOAT")
            outputs = execute_plan(
                get_separator, plan, tmp, sep_sr, stage_callback, cfg
            )
            sep_start = (
                int(round(s_start * sep_sr / sr))
                if sep_sr != sr else s_start
            )
            out_len = max((len(v) for v in outputs.values()), default=0)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

        required_length = sep_start + out_len
        if required_length > n_sep:
            for stem_name in stem_names:
                result[stem_name].resize((required_length, 2), refcheck=False)
                result[stem_name][n_sep:] = 0.0
            n_sep = required_length
        for stem_name, output in outputs.items():
            if stem_name in result:
                write_crossfaded_span(
                    result[stem_name], sep_start, sep_start + out_len,
                    output, fade_samples,
                )
        del outputs
        output = None

    return result
