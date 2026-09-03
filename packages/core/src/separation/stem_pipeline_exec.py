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
from upmixer.separation.remask import share_parent_residual
from upmixer.separation.separator import StemSeparator
from upmixer.separation.stem_plan import (
    ENSEMBLE_ALGORITHM,
    MODEL_ENSEMBLE,
    MODEL_DEUX,
    MODEL_DRUMS,
    MODEL_PRIMARY,
    SeparationPlan,
    SeparationTask,
)
from upmixer.separation.stem_resume import ResumeStore

_log = logging.getLogger("upmixer")

GetSeparator = Callable[[str, int], StemSeparator]
StageCallback = Callable[[int, int, str, frozenset[str]], None]


def temporary_wav_path(prefix: str) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".wav", prefix=prefix, delete=False)
    path = handle.name
    handle.close()
    return path


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def cacheable_plan_stems(plan: SeparationPlan) -> frozenset[str]:
    """All public outputs produced by a plan at no extra inference cost."""
    return frozenset(
        stem
        for task in plan.tasks
        for stem in task.output_stems
        if not stem.startswith("_")
    )


def _remasks(cfg: UpmixConfig | None, model: str) -> bool:
    """Whether *model*'s stage shares its parent's remainder over its outputs.

    Both stages share out only the remainder their own split left: a full
    re-projection measured worse on every stem metric for either of them
    (docs/reports/primary_remask.md, docs/reports/drum_remask.md).
    """
    if cfg is None:
        return False
    if model == MODEL_DRUMS:
        return cfg.stem_drum_remask
    if model == MODEL_PRIMARY:
        return cfg.stem_primary_remask
    return False


def _remask_stage(
    loaded: dict[str, np.ndarray],
    on_disk: dict[str, str],
    parent_path: str,
    names: frozenset[str],
) -> None:
    """Share this stage's parent remainder over its outputs.

    Children kept on disk for a later stage (the Drums stem feeding drumsep)
    are rewritten in place, so the next stage separates and re-masks against
    the re-masked parent and the two passes compose.
    """
    children = {name: audio for name, audio in loaded.items() if name in names}
    for name in names & on_disk.keys():
        audio, _ = sf.read(on_disk[name], dtype="float32", always_2d=True)
        children[name] = audio
    if not children:
        return
    parent, parent_sr = sf.read(parent_path, dtype="float32", always_2d=True)
    for name, audio in share_parent_residual(parent, children, parent_sr).items():
        if name in on_disk:
            sf.write(on_disk[name], audio, parent_sr, subtype="FLOAT")
        else:
            loaded[name] = audio


def _write_float_atomic(path: str, audio: np.ndarray, sample_rate: int) -> None:
    handle = tempfile.NamedTemporaryFile(
        suffix=".wav", prefix="upmixer_cleanup_",
        dir=os.path.dirname(path), delete=False,
    )
    temporary = handle.name
    handle.close()
    try:
        sf.write(temporary, audio, sample_rate, subtype="FLOAT")
        os.replace(temporary, path)
    finally:
        _discard(temporary)


def _stabilize_on_disk(on_disk: dict[str, str]) -> None:
    """Move stage files out of a separator temp dir before model eviction."""
    for name, path in list(on_disk.items()):
        stable_path = temporary_wav_path("upmixer_intermediate_")
        try:
            os.replace(path, stable_path)
        except OSError:
            _discard(stable_path)
            raise
        on_disk[name] = stable_path


def _cleanup_deux_stage(
    loaded: dict[str, np.ndarray],
    on_disk: dict[str, str],
    parent: np.ndarray,
    sample_rate: int,
) -> None:
    from upmixer.separation.stem_cleanup import apply_stem_cleanup

    children: dict[str, np.ndarray] = {}
    for name in ("Vocals", "_deux_inst"):
        if name in loaded:
            children[name] = loaded[name]
        elif name in on_disk:
            children[name], _ = sf.read(
                on_disk[name], dtype="float32", always_2d=True
            )
        else:
            raise RuntimeError(f"DSP stem cleanup requires the {name} estimate")

    vocals, instrumental = apply_stem_cleanup(
        parent, children["Vocals"], children["_deux_inst"], sample_rate
    )
    for name, audio in (("Vocals", vocals), ("_deux_inst", instrumental)):
        if name in on_disk:
            _write_float_atomic(on_disk[name], audio, sample_rate)
        else:
            loaded[name] = audio


def execute_plan(
    get_separator: GetSeparator,
    plan: SeparationPlan,
    sep_path: str,
    sep_sr: int,
    stage_callback: StageCallback | None = None,
    cfg: UpmixConfig | None = None,
    resume_key: str | None = None,
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
        cfg: Configuration for stage-local DSP passes: parent remainder
            sharing and optional two-child stem cleanup. ``None`` runs none.
        resume_key: Identity of this run, enabling the crash checkpoint in
            ``stem_resume``: a failing stage leaves the finished stages'
            stems on disk under ``cfg.stem_cache_dir`` and the next run with
            the same key restarts at the stage that failed. ``None``, or a
            config with no stem cache directory, disables it.

    Returns a dict of canonical_name → ndarray for all requested stems.
    """
    all_loaded: dict[str, np.ndarray] = {}
    all_disk: dict[str, str] = {}

    later_inputs: frozenset[str] = frozenset(
        t.input_source for t in plan.tasks if t.input_source != "original"
    )

    n_tasks = len(plan.tasks)
    resume = ResumeStore.open(
        cfg.stem_cache_dir if cfg is not None else None, resume_key, sep_sr
    )
    completed = 0
    if resume is not None:
        restored = resume.restore()
        if restored is not None:
            completed, all_loaded, all_disk = restored
            _log.info(
                "  Resuming after stage %d/%d — %s already separated",
                completed,
                n_tasks,
                sorted(all_loaded.keys() | all_disk.keys()) or "(nothing)",
            )

    try:
        _run_stages(
            get_separator, plan, sep_path, sep_sr, stage_callback, cfg,
            all_loaded, all_disk, later_inputs, completed, resume,
        )
    except Exception:
        # A failed logical stage must not leave newly-created intermediates
        # behind. ResumeStore has already copied completed-stage files when
        # configured; those checkpoint paths are deliberately retained.
        checkpoint_root = (
            os.path.abspath(str(resume.root)) if resume is not None else None
        )
        for path in all_disk.values():
            absolute = os.path.abspath(path)
            if checkpoint_root is None or not absolute.startswith(
                checkpoint_root + os.sep
            ):
                _discard(path)
        raise

    for name, path in all_disk.items():
        if not name.startswith("_") and name not in all_loaded:
            audio, _ = sf.read(path, dtype="float32", always_2d=True)
            if audio.shape[1] == 1:
                audio = np.concatenate([audio, audio], axis=1)
            all_loaded[name] = audio

    for path in all_disk.values():
        _discard(path)
    if resume is not None:
        resume.clear()

    _log.info("  All stages complete. Produced stems: %s", sorted(all_loaded.keys()))
    return all_loaded


def _run_stages(
    get_separator: GetSeparator,
    plan: SeparationPlan,
    sep_path: str,
    sep_sr: int,
    stage_callback: StageCallback | None,
    cfg: UpmixConfig | None,
    all_loaded: dict[str, np.ndarray],
    all_disk: dict[str, str],
    later_inputs: frozenset[str],
    completed: int,
    resume: "ResumeStore | None",
) -> None:
    """Run the plan's stages from ``completed`` onward, mutating both dicts.

    On any failure the stems held at the last completed stage boundary are
    checkpointed — the failing stage's index is exactly how many stages
    finished — before the exception propagates.
    """
    n_tasks = len(plan.tasks)
    for stage_idx, task in enumerate(plan.tasks):
        if stage_idx < completed:
            _log.info(
                "  [stage %d/%d] model=%s — restored from checkpoint",
                stage_idx + 1, n_tasks, task.model,
            )
            continue
        try:
            _run_one_stage(
                get_separator, task, stage_idx, n_tasks, sep_path,
                sep_sr, stage_callback, cfg, all_loaded, all_disk,
                later_inputs,
            )
        except Exception:
            if resume is not None:
                resume.checkpoint(stage_idx, all_loaded, all_disk)
            raise


def _read_ensemble_stem(
    loaded: dict[str, np.ndarray],
    on_disk: dict[str, str],
    name: str,
    model: str,
) -> np.ndarray:
    """Read one selected ensemble output from memory or its temporary WAV."""
    if name in loaded:
        return np.asarray(loaded[name])
    path = on_disk.get(name)
    if path is None:
        raise RuntimeError(
            f"Ensemble model {model} did not produce required stem '{name}'"
        )
    try:
        audio, _ = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:
        raise RuntimeError(
            f"Could not read ensemble model {model} stem '{name}'"
        ) from exc
    return audio


def _average_ensemble_stem(
    name: str,
    primary: np.ndarray,
    partner: np.ndarray,
) -> np.ndarray:
    """Validate and average one pair without clipping or dtype promotion."""
    primary = np.asarray(primary)
    partner = np.asarray(partner)
    if primary.shape != partner.shape:
        raise ValueError(
            f"Ensemble stem '{name}' shape mismatch: primary"
            f" {primary.shape}, partner {partner.shape}"
        )
    if primary.dtype.kind not in "fiu" or partner.dtype.kind not in "fiu":
        raise ValueError(f"Ensemble stem '{name}' outputs must be float-compatible")
    try:
        primary = primary.astype(np.float32, copy=False)
        partner = partner.astype(np.float32, copy=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"Ensemble stem '{name}' outputs must be float-compatible"
        ) from exc
    if not np.isfinite(primary).all() or not np.isfinite(partner).all():
        raise ValueError(f"Ensemble stem '{name}' outputs must be finite")
    return np.add(
        np.multiply(primary, np.float32(0.5), dtype=np.float32),
        np.multiply(partner, np.float32(0.5), dtype=np.float32),
        dtype=np.float32,
    )


def _run_ensemble_stage(
    get_separator: GetSeparator,
    task: SeparationTask,
    input_path: str,
    sep_sr: int,
    keep_on_disk: frozenset[str],
    stage_idx: int,
    n_tasks: int,
    stage_callback: StageCallback | None,
) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """Run the primary model and its fixed SCNet partner on one parent."""
    if task.model != MODEL_PRIMARY:
        raise ValueError("Only the BS-Roformer-SW stage supports ensembling")
    if task.ensemble_models != (MODEL_ENSEMBLE,):
        raise ValueError("The primary ensemble must contain the registered SCNet partner")
    if not task.ensemble_stems:
        raise ValueError("The primary ensemble must declare at least one stem")

    primary = get_separator(task.model, sep_sr)
    primary_loaded, primary_on_disk = primary.separate_to_file(
        input_path,
        keep_on_disk,
        task.stem_overrides,
        task.output_stems,
    )
    partner_loaded: dict[str, np.ndarray] = {}
    partner_on_disk: dict[str, str] = {}
    partner_ok = False
    try:
        # CPU model caching may evict/close the primary separator as soon as
        # the SCNet instance is requested, so keep its files outside that
        # separator's temporary directory first.
        _stabilize_on_disk(primary_on_disk)
        if stage_callback is not None:
            stage_callback(
                stage_idx, n_tasks, MODEL_ENSEMBLE, task.ensemble_stems
            )
        _log.info(
            "  [stage %d/%d] ensemble model=%s  input=%s  stems=%s  algorithm=%s",
            stage_idx + 1,
            n_tasks,
            MODEL_ENSEMBLE,
            input_path,
            sorted(task.ensemble_stems),
            ENSEMBLE_ALGORITHM,
        )
        partner = get_separator(MODEL_ENSEMBLE, sep_sr)
        # Partner outputs stay in memory; the fused Drums array is the only
        # result materialized for a later drum-piece stage.
        partner_loaded, partner_on_disk = partner.separate_to_file(
            input_path,
            frozenset(),
            None,
            task.ensemble_stems,
        )

        fused = {
            name: _average_ensemble_stem(
                name,
                _read_ensemble_stem(
                    primary_loaded, primary_on_disk, name, task.model
                ),
                _read_ensemble_stem(
                    partner_loaded, partner_on_disk, name, MODEL_ENSEMBLE
                ),
            )
            for name in sorted(task.ensemble_stems)
        }

        for name, audio in fused.items():
            old_path = primary_on_disk.pop(name, None)
            if old_path is not None:
                _discard(old_path)
            primary_loaded.pop(name, None)
            if name in keep_on_disk:
                fused_path = temporary_wav_path("upmixer_ensemble_")
                try:
                    sf.write(fused_path, audio, sep_sr, subtype="FLOAT")
                except Exception:
                    _discard(fused_path)
                    raise
                primary_on_disk[name] = fused_path
            else:
                primary_loaded[name] = audio
        partner_ok = True
        return primary_loaded, primary_on_disk
    finally:
        for path in partner_on_disk.values():
            _discard(path)
        if not partner_ok:
            for path in primary_on_disk.values():
                _discard(path)


def _run_one_stage(
    get_separator: GetSeparator,
    task: SeparationTask,
    stage_idx: int,
    n_tasks: int,
    sep_path: str,
    sep_sr: int,
    stage_callback: StageCallback | None,
    cfg: UpmixConfig | None,
    all_loaded: dict[str, np.ndarray],
    all_disk: dict[str, str],
    later_inputs: frozenset[str],
) -> None:
    """Run one model stage, merging its outputs into the run's two dicts."""
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

    if task.ensemble_models:
        loaded, on_disk = _run_ensemble_stage(
            get_separator,
            task,
            input_path_for_task,
            sep_sr,
            keep_on_disk,
            stage_idx,
            n_tasks,
            stage_callback,
        )
        cleanup_parent = None
    else:
        sep = get_separator(task.model, sep_sr)
        retain_parent = (
            cfg is not None
            and cfg.stem_bleed_reduction
            and task.model == MODEL_DEUX
        )
        loaded, on_disk = sep.separate_to_file(
            input_path_for_task,
            keep_on_disk,
            task.stem_overrides,
            task.output_stems,
            **({"retain_parent": True} if retain_parent else {}),
        )
        cleanup_parent = sep.take_last_parent() if retain_parent else None

    if not task.ensemble_models:
        _stabilize_on_disk(on_disk)

    if _remasks(cfg, task.model) and task.input_source in all_disk:
        _log.info(
            "  [stage %d/%d] sharing the remainder of parent %s",
            stage_idx + 1,
            n_tasks,
            task.input_source,
        )
        _remask_stage(
            loaded,
            on_disk,
            all_disk[task.input_source],
            task.output_stems,
        )

    if cleanup_parent is not None:
        _log.info("  [stage %d/%d] applying DSP stem cleanup", stage_idx + 1, n_tasks)
        _cleanup_deux_stage(loaded, on_disk, cleanup_parent, sep_sr)

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

    # A later stage may re-emit a stem an earlier one left on disk.
    for name in loaded.keys() | on_disk.keys():
        superseded = all_disk.pop(name, None)
        if superseded is not None and superseded != on_disk.get(name):
            _discard(superseded)

    all_disk.update(on_disk)


def execute_plan_with_silence_skip(
    get_separator: GetSeparator,
    plan: SeparationPlan,
    zone_audio: np.ndarray,
    sr: int,
    sep_sr: int,
    cfg: UpmixConfig,
    original_path: str | None = None,
    stage_callback: StageCallback | None = None,
    resume_key: str | None = None,
) -> dict[str, np.ndarray]:
    """Run stem separation on active spans only, skipping silent regions.

    Detects contiguous silent runs in *zone_audio*, separates only the
    active portions, and stitches the per-stem outputs back into
    full-length arrays with a linear crossfade at each boundary.

    Returns the same dict shape as :func:`execute_plan`:
    ``{stem_name: (n_sep_samples, 2) float32}``.

    ``resume_key`` is per span, so a crash on a multi-span zone resumes the
    span it died in; spans that already finished are re-separated, since
    their stems live only in the caller's stitched array.
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
                get_separator, plan, original_path, sep_sr, stage_callback,
                cfg, resume_key,
            )
        tmp = temporary_wav_path("upmixer_full_")
        try:
            sf.write(tmp, zone_audio, sr, subtype="FLOAT")
            return execute_plan(
                get_separator, plan, tmp, sep_sr, stage_callback, cfg, resume_key
            )
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

    for span_idx, (s_start, s_end) in enumerate(spans):
        span_audio = zone_audio[s_start:s_end]
        tmp = temporary_wav_path("upmixer_span_")
        try:
            sf.write(tmp, span_audio, sr, subtype="FLOAT")
            outputs = execute_plan(
                get_separator, plan, tmp, sep_sr, stage_callback, cfg,
                None if resume_key is None else f"{resume_key}|span{span_idx}",
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
