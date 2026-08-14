"""Cache identity and validation for stem-separation runs."""
from __future__ import annotations

import hashlib

from upmixer.config import UpmixConfig
from upmixer.separation.stem_plan import SeparationPlan


def bleed_cache_component(config: UpmixConfig) -> str:
    """Serialize the bleed-reduction settings that change stored stem audio."""
    if not config.stem_bleed_reduction:
        return ""

    def _dict(value: dict | None) -> str:
        return "" if not value else "&".join(f"{k}={value[k]}" for k in sorted(value))

    return (
        f"bleed|pf={_dict(config.stem_phase_fix)}"
        f"|low={config.stem_phase_fix_low_hz}|high={config.stem_phase_fix_high_hz}"
        f"|scale={config.stem_phase_fix_scale}|ref={config.stem_phase_fix_reference_model}"
        f"|db={_dict(config.stem_debleed)}|dbmodel={config.stem_debleed_model}"
    )


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
    bleed = bleed_cache_component(config)
    if all(value in (None, False) for value in options) and not bleed:
        return base
    raw = (
        f"{base}|batch={options[0]}|segment={options[1]}|chunk={options[2]}"
        f"|overlap={options[3]}|tta={options[4]}|pitch={options[5]}"
    )
    if bleed:
        raw += f"|{bleed}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def validate_bleed_config(config: UpmixConfig) -> None:
    """Validate bleed-reduction settings before any inference runs."""
    if not 0.0 < config.stem_phase_fix_scale <= 1.0:
        raise ValueError("stem_phase_fix_scale must be in (0.0, 1.0]")
    if not 0.0 < config.stem_phase_fix_low_hz < config.stem_phase_fix_high_hz:
        raise ValueError("stem_phase_fix requires 0 < low_hz < high_hz")
    from upmixer.separation.inference.registry import get_model_spec

    for model in (
        config.stem_phase_fix_reference_model,
        config.stem_debleed_model,
    ):
        get_model_spec(model)
