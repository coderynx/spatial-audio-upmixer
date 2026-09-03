"""Read, zone-split, separate, and cache stems — no routing or mastering."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import (
    FORMAT_MAP,
    INPUT_FORMAT_MAP,
    InputFormat,
    OutputFormat,
    detect_input_format,
)
from upmixer.io.reader import AudioReader
from upmixer.separation.stem_identity import stem_cache_identity
from upmixer.separation.stem_pipeline_exec import (
    GetSeparator,
    execute_plan,
    execute_plan_with_silence_skip,
    temporary_wav_path,
)
from upmixer.separation.stem_plan import (
    DEFAULT_STEMS,
    SeparationPlan,
    normalize_stems,
    resolve_separation_plan,
)
from upmixer.separation.stem_zones import _as_stereo_pair, _extract_zones
from upmixer.utils import preview_slice, itu_downmix_stereo

_log = logging.getLogger(__name__)


@dataclass
class SeparationResult:
    """Separated (and cached) stems plus the context downstream routing needs."""

    all_stems: dict[str, np.ndarray]
    plan: SeparationPlan
    input_fmt: InputFormat
    output_fmt: OutputFormat
    input_sr: int
    sep_sr: int
    out_sr: int
    audio_full: np.ndarray
    passthrough: dict[str, np.ndarray]
    source_zones: dict[str, np.ndarray]
    n_samples: int
    stem_summary: list[str]


def _resolve_output_sample_rate(cfg: UpmixConfig, sr: int) -> int:
    out_sr = cfg.output_sample_rate or sr
    if cfg.output_type == "adm-bwf":
        if cfg.output_sample_rate is None:
            out_sr = 48_000
        if out_sr not in (48_000, 96_000):
            raise ValueError(
                "Dolby ADM-BWF requires a 48 kHz or 96 kHz output sample rate"
            )
        if cfg.output_subtype != "PCM_24":
            raise ValueError("Dolby ADM-BWF requires output_subtype='PCM_24'")
    return out_sr


def _resolve_input_format(
    reader: AudioReader, input_format_override: str | None
) -> InputFormat:
    if input_format_override is None:
        return detect_input_format(reader.n_channels)
    if input_format_override not in INPUT_FORMAT_MAP:
        raise ValueError(
            f"Unknown input format '{input_format_override}'. "
            f"Valid: {sorted(INPUT_FORMAT_MAP.keys())}"
        )
    input_fmt = INPUT_FORMAT_MAP[input_format_override]
    if input_fmt.n_channels != reader.n_channels:
        raise ValueError(
            f"Input format '{input_format_override}' expects "
            f"{input_fmt.n_channels} channels but file has {reader.n_channels}"
        )
    return input_fmt


def _cache_key_kwargs(cfg: UpmixConfig) -> dict:
    """Cache-key components shared by the stem cache and the resume key."""
    return dict(
        is_preview=cfg.preview,
        preview_duration=cfg.preview_duration_s,
        preview_start=cfg.preview_start_s,
        silence_skip=cfg.stem_silence_skip,
        silence_threshold_db=cfg.stem_silence_threshold_db,
        silence_min_duration_s=cfg.stem_silence_min_duration_s,
        silence_crossfade_ms=cfg.stem_silence_crossfade_ms,
        silence_pad_ms=cfg.stem_silence_pad_ms,
        path_key=cfg.stem_cache_key,
    )


def _resume_key(
    cfg: UpmixConfig, input_path: str, cache_identity: str, sep_sr: int
) -> str | None:
    """Identity a crash checkpoint is filed under, or ``None`` when disabled.

    Same components as the stem cache's own key, so a checkpoint is only ever
    replayed into a run that would have produced it. Previews are excluded:
    they are short-lived and are never cached either.
    """
    if not cfg.stem_cache_dir or cfg.preview or not cache_identity:
        return None
    from upmixer.separation.stem_cache import _cache_key

    return _cache_key(input_path, cache_identity, sep_sr, **_cache_key_kwargs(cfg))


def _load_cached_stems(
    cfg: UpmixConfig,
    plan: SeparationPlan,
    input_path: str,
    sep_sr: int,
    cache_identity: str,
) -> dict[str, np.ndarray] | None:
    from upmixer.separation.stem_cache import StemCache

    silence_kwargs = _cache_key_kwargs(cfg)
    custom_inference_tuning = any(
        value not in (None, False)
        for value in (
            cfg.stem_batch_size,
            cfg.stem_segment_size,
            cfg.stem_chunk_duration_s,
            cfg.stem_overlap,
            cfg.stem_tta,
            cfg.stem_pitch_shift,
            cfg.stem_bleed_reduction,
            cfg.stem_ensemble,
        )
    )
    cache = StemCache(cfg.stem_cache_dir)
    cache_started = time.monotonic()
    result = cache.load(input_path, cache_identity, sep_sr, **silence_kwargs)
    # Read caches created before model-plan keys were introduced.
    if (
        result is None
        and not custom_inference_tuning
        and cache_identity != plan.stems_hash
    ):
        result = cache.load(input_path, plan.stems_hash, sep_sr, **silence_kwargs)
    _log.debug("  Timing cache-read=%.3fs", time.monotonic() - cache_started)
    return None if result is None else result[0]


def _save_cached_stems(
    cfg: UpmixConfig,
    input_path: str,
    cache_identity: str,
    sep_sr: int,
    all_stems: dict[str, np.ndarray],
) -> None:
    from upmixer.separation.stem_cache import StemCache

    cache_started = time.monotonic()
    StemCache(cfg.stem_cache_dir).save(
        input_path,
        cache_identity,
        sep_sr,
        all_stems,
        sep_sr,
        is_preview=cfg.preview,
        preview_duration=cfg.preview_duration_s,
        preview_start=cfg.preview_start_s,
        silence_skip=cfg.stem_silence_skip,
        silence_threshold_db=cfg.stem_silence_threshold_db,
        silence_min_duration_s=cfg.stem_silence_min_duration_s,
        silence_crossfade_ms=cfg.stem_silence_crossfade_ms,
        silence_pad_ms=cfg.stem_silence_pad_ms,
        path_key=cfg.stem_cache_key,
    )
    _log.debug("  Timing cache-write=%.3fs", time.monotonic() - cache_started)


def _run_zone_separation(
    get_separator: GetSeparator,
    cfg: UpmixConfig,
    plan: SeparationPlan,
    sep_zones: dict[str, str | np.ndarray],
    audio_full: np.ndarray,
    sr: int,
    sep_sr: int,
    stereo_mode: bool,
    progress: Callable[[str, float], None],
    resume_key: str | None = None,
) -> dict[str, np.ndarray]:
    all_stems: dict[str, np.ndarray] = {}
    tmp_files: list[str] = []
    zone_names = list(sep_zones.keys())
    n_zones = len(zone_names)

    try:
        for zone_idx, zone_name in enumerate(zone_names):
            pair_src = sep_zones[zone_name]
            zone_frac = 0.15 + 0.60 * (zone_idx / n_zones)
            next_zone_frac = 0.15 + 0.60 * ((zone_idx + 1) / n_zones)
            progress(f"    Separating zone: {zone_name}...", zone_frac)

            def _stage_callback(
                stage_idx: int,
                n_tasks: int,
                model: str,
                output_stems: frozenset[str],
                stage_progress: float = 0.0,
                zone_name: str = zone_name,
                zone_frac: float = zone_frac,
                next_zone_frac: float = next_zone_frac,
            ) -> None:
                stage_frac = zone_frac + (next_zone_frac - zone_frac) * (
                    (stage_idx + stage_progress) / n_tasks
                )
                stems_desc = ", ".join(sorted(output_stems)) or model
                progress(
                    f"    Extracting {stems_desc} (zone {zone_name}, model {model})...",
                    stage_frac,
                )

            zone_resume_key = (
                None if resume_key is None else f"{resume_key}|{zone_name}"
            )
            if cfg.stem_silence_skip:
                if isinstance(pair_src, str):
                    zone_audio = audio_full
                    original_path = pair_src
                else:
                    zone_audio = pair_src
                    original_path = None
                zone_stems = execute_plan_with_silence_skip(
                    get_separator,
                    plan,
                    zone_audio,
                    sr,
                    sep_sr,
                    cfg,
                    original_path=original_path,
                    stage_callback=_stage_callback,
                    resume_key=zone_resume_key,
                )
            else:
                if isinstance(pair_src, str):
                    sep_path = pair_src
                else:
                    tmp = temporary_wav_path(f"upmixer_{zone_name}_")
                    sf.write(tmp, pair_src, sr, subtype="FLOAT")
                    sep_path = tmp
                    tmp_files.append(tmp)
                zone_stems = execute_plan(
                    get_separator,
                    plan,
                    sep_path,
                    sep_sr,
                    _stage_callback,
                    cfg,
                    zone_resume_key,
                )

            for stem_name, stem_audio in zone_stems.items():
                key = stem_name if stereo_mode else f"{stem_name}@{zone_name}"
                all_stems[key] = stem_audio
            del zone_stems
    finally:
        for tmp in tmp_files:
            if os.path.exists(tmp):
                os.unlink(tmp)

    return all_stems


def separate(
    get_separator: GetSeparator,
    cfg: UpmixConfig,
    input_path: str,
    input_format_override: str | None,
    progress: Callable[[str, float], None],
) -> SeparationResult:
    """Read, zone-split, separate, and cache stems — no routing or mastering."""
    reader = AudioReader(input_path)
    read_started = time.monotonic()
    audio_full, sr = reader.read(dtype="float32")
    _log.debug("  Timing input-read=%.3fs", time.monotonic() - read_started)

    input_fmt = _resolve_input_format(reader, input_format_override)
    output_fmt = FORMAT_MAP[cfg.output_format]

    raw_stems = cfg.stems or []
    canonical = normalize_stems(raw_stems) if raw_stems else list(DEFAULT_STEMS)
    plan = resolve_separation_plan(canonical, cfg.stem_ensemble)
    _log.info(
        "separation_started input_format=%s input_channels=%d sample_rate=%d output_format=%s requested_stems=%s models=%s",
        input_fmt.name,
        input_fmt.n_channels,
        sr,
        output_fmt.name,
        sorted(plan.requested_stems),
        [task.model for task in plan.tasks],
    )

    forced_stereo_array = False
    if cfg.preview:
        audio_full, t0_preview, t1_preview = preview_slice(
            audio_full, sr, cfg.preview_duration_s, cfg.preview_start_s
        )
        _log.debug("preview_window start_s=%.2f end_s=%.2f", t0_preview, t1_preview)
        forced_stereo_array = True

    stereo_folded_input = output_fmt.n_channels == 2 and input_fmt.n_channels > 2
    if stereo_folded_input:
        left, right = itu_downmix_stereo(
            {
                label.value: audio_full[:, i]
                for i, label in enumerate(input_fmt.channels)
            },
            surround_coeff=cfg.surround_downmix_coeff,
            height_coeff=cfg.height_downmix_coeff,
        )
        audio_full = np.column_stack([left, right]).astype(np.float32, copy=False)
        forced_stereo_array = True
        _log.info("input_folded_to_stereo input_format=%s", input_fmt.name)

    out_sr = _resolve_output_sample_rate(cfg, sr)
    sep_sr = out_sr

    cache_identity = ""
    cache_hit_stems: dict[str, np.ndarray] | None = None
    if cfg.stem_input_dir:
        from upmixer.separation.stem_store import PlainStemStore

        stem_input_result = PlainStemStore(cfg.stem_input_dir).load()
        if stem_input_result is not None:
            cache_hit_stems = stem_input_result[0]
    elif cfg.stem_cache_dir:
        # A folded run separates one "front" zone where the same file
        # unfolded yields "@zone"-keyed stems, so the two must not share
        # a cache entry.
        cache_identity = stem_cache_identity(plan, cfg) + (
            "|stereo" if stereo_folded_input else ""
        )
        cache_hit_stems = _load_cached_stems(
            cfg, plan, input_path, sep_sr, cache_identity
        )

    if (audio_full.shape[1] if audio_full.ndim > 1 else 1) <= 2:
        if forced_stereo_array:
            n_ch = audio_full.shape[1] if audio_full.ndim > 1 else 1
            front_arr = (
                np.column_stack([audio_full[:, 0], audio_full[:, 0]])
                if n_ch == 1
                else audio_full[:, :2]
            )
            sep_zones: dict[str, str | np.ndarray] = {"front": front_arr}
        else:
            sep_zones = {"front": input_path}
        passthrough: dict[str, np.ndarray] = {}
        stereo_mode = True
        source_zones = {"front": _as_stereo_pair(audio_full)}
        _log.info("separation_mode mode=stereo zones=%s", sorted(sep_zones))
    else:
        sep_zones, passthrough = _extract_zones(audio_full, input_fmt)
        stereo_mode = False
        source_zones = {
            name: audio
            for name, audio in sep_zones.items()
            if isinstance(audio, np.ndarray)
        }
        _log.info("separation_mode mode=multichannel zones=%s", sorted(sep_zones))
        if passthrough:
            _log.info("separation_passthrough channels=%s", sorted(passthrough))

    progress("  Separating stems...", 0.1)

    if cache_hit_stems is not None:
        all_stems = cache_hit_stems
        cache_hit_stems = None
        _log.info("stem_cache_hit")
        progress("  Using cached stems...", 0.75)
    else:
        all_stems = _run_zone_separation(
            get_separator,
            cfg,
            plan,
            sep_zones,
            audio_full,
            sr,
            sep_sr,
            stereo_mode,
            progress,
            _resume_key(cfg, input_path, cache_identity, sep_sr),
        )

        if cfg.stem_cache_dir and not cfg.stem_input_dir and all_stems:
            _save_cached_stems(cfg, input_path, cache_identity, sep_sr, all_stems)

        if cfg.stem_output_dir and all_stems:
            from upmixer.separation.stem_store import PlainStemStore

            PlainStemStore(cfg.stem_output_dir).write(all_stems, sep_sr)

    del sep_zones

    # Models often emit more stems than requested. Cache those free outputs,
    # then keep only requested stems out of routing and mixing.
    all_stems = {
        key: audio
        for key, audio in all_stems.items()
        if key.split("@", 1)[0] in plan.requested_stems
    }

    if not all_stems:
        raise RuntimeError(
            "Stem separation produced no output. Check model and input file."
        )

    n_samples = max(len(s) for s in all_stems.values())
    stem_summary = sorted({k.split("@")[0] for k in all_stems})
    _log.info(
        "separation_completed stems=%s duration_s=%.3f sample_rate=%d",
        stem_summary,
        n_samples / sep_sr,
        sep_sr,
    )

    return SeparationResult(
        all_stems=all_stems,
        plan=plan,
        input_fmt=input_fmt,
        output_fmt=output_fmt,
        input_sr=sr,
        sep_sr=sep_sr,
        out_sr=out_sr,
        audio_full=audio_full,
        passthrough=passthrough,
        source_zones=source_zones,
        n_samples=n_samples,
        stem_summary=stem_summary,
    )
