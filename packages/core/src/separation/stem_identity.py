"""Cache identity for stem-separation runs."""
from __future__ import annotations

import hashlib

from upmixer.config import UpmixConfig
from upmixer.separation.stem_plan import (
    MODEL_DEUX,
    MODEL_DRUMS,
    MODEL_PRIMARY,
    SeparationPlan,
)


_STEM_CLEANUP_REVISION = 1


def stem_cleanup_cache_component(config: UpmixConfig) -> str:
    """Identify the fixed DSP cleanup that changes stored stem audio."""
    if not config.stem_bleed_reduction:
        return ""
    return f"stemcleanup={_STEM_CLEANUP_REVISION}"


def remask_cache_component(plan: SeparationPlan, config: UpmixConfig) -> str:
    """Serialize the re-mask settings that change stored stem audio.

    Each pass only counts for plans that actually run its model, so a plan
    keeps the cache identity it had before that pass existed.
    """
    models = {task.model for task in plan.tasks}
    parts = []
    if config.stem_primary_remask and MODEL_PRIMARY in models:
        parts.append("primaryremask")
    if config.stem_drum_remask and MODEL_DRUMS in models:
        parts.append("drumremask")
    return "|".join(parts)


def stem_cache_identity(plan: SeparationPlan, config: UpmixConfig) -> str:
    """Return model-plan identity including output-affecting inference overrides."""
    base = plan.inference_hash or plan.stems_hash
    options = (
        config.stem_batch_size,
        config.stem_segment_size,
        config.stem_chunk_duration_s,
        config.stem_overlap,
        config.stem_tta,
        config.stem_pitch_shift,
    )
    cleanup = (
        stem_cleanup_cache_component(config)
        if MODEL_DEUX in {task.model for task in plan.tasks}
        else ""
    )
    remask = remask_cache_component(plan, config)
    if all(value in (None, False) for value in options) and not cleanup and not remask:
        return base
    raw = (
        f"{base}|batch={options[0]}|segment={options[1]}|chunk={options[2]}"
        f"|overlap={options[3]}|tta={options[4]}|pitch={options[5]}"
    )
    if cleanup:
        raw += f"|{cleanup}"
    if remask:
        raw += f"|{remask}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]
