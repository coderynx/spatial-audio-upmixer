"""Bleed-reduction post-processing over separated stems.

Runs two optional passes between separation and downstream routing:

* the pure-DSP phase fixer (:mod:`upmixer.separation.phase_fix`), using the
  instrumental output of a vocal-target reference model, and
* an inference-based debleed model pass on the stem itself.

When the master gate (``config.stem_bleed_reduction``) is on, the phase fixer
defaults enabled for stems whose default routing reaches surround/height, while
the debleed pass is opt-in — it runs one full model inference per stem, so it
stays off unless explicitly enabled via ``config.stem_debleed``. Both are
overridable per stem (``config.stem_phase_fix`` / ``config.stem_debleed``, keyed
by canonical stem name or ``"*"``). Off overall by default. Inference is injected
as ``separate_array`` so this module stays unit-testable.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import OutputFormat
from upmixer.separation.phase_fix import apply_phase_fix
from upmixer.separation.stem_router import _VOCAL_STEM_NAMES, stem_reaches_surround_height

SeparateArray = Callable[[str, np.ndarray, int], dict[str, np.ndarray]]

# Registered checkpoints sanctioned for each pass (knowledge base
# techniques/phase_and_bleed.md, models/cleanup.md).
PHASE_FIX_REFERENCE_MODELS: tuple[str, ...] = (
    "kimmel_unwa_ft2_bleedless.ckpt",
)
DEBLEED_MODELS: tuple[str, ...] = (
    "mel_band_roformer_bleed_suppressor_v1.ckpt",
    "mel_band_roformer_denoise_debleed_gabox.ckpt",
    "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
)


def _enabled(overrides: dict | None, canonical: str, default: bool) -> bool:
    if overrides:
        if canonical in overrides:
            return bool(overrides[canonical])
        if "*" in overrides:
            return bool(overrides["*"])
    return default


def _pick_instrumental(outputs: dict[str, np.ndarray]) -> np.ndarray | None:
    """The instrumental (non-vocal) output of a vocal-target model."""
    if "Instrumental" in outputs:
        return outputs["Instrumental"]
    candidates = sorted(
        k for k in outputs if k not in _VOCAL_STEM_NAMES and not k.startswith("_")
    )
    return outputs[candidates[0]] if candidates else None


def _pick_primary(outputs: dict[str, np.ndarray], canonical: str) -> np.ndarray | None:
    """The main cleaned output of a debleed model run on a single stem."""
    if canonical in outputs:
        return outputs[canonical]
    if "Instrumental" in outputs:
        return outputs["Instrumental"]
    candidates = sorted(k for k in outputs if not k.startswith("_"))
    return outputs[candidates[0]] if candidates else None


def _replace_prefix(original: np.ndarray, cleaned: np.ndarray) -> np.ndarray:
    n = min(len(original), len(cleaned))
    if n == 0:
        return original
    out = original.copy()
    out[:n] = cleaned[:n, : original.shape[1]]
    return out


def apply_bleed_reduction(
    all_stems: dict[str, np.ndarray],
    source_zones: dict[str, np.ndarray],
    source_sr: int,
    sep_sr: int,
    config: UpmixConfig,
    output_fmt: OutputFormat,
    separate_array: SeparateArray,
    progress: Optional[Callable[[str], None]] = None,
) -> dict[str, np.ndarray]:
    """Apply the phase-fix and debleed passes to the enabled stems in place."""
    if not config.stem_bleed_reduction:
        return all_stems

    phase_overrides = config.stem_phase_fix
    debleed_overrides = config.stem_debleed
    ref_model = config.stem_phase_fix_reference_model
    debleed_model = config.stem_debleed_model
    reference_cache: dict[str, np.ndarray | None] = {}

    def _reference_for_zone(zone: str) -> np.ndarray | None:
        if zone not in reference_cache:
            zone_audio = source_zones.get(zone)
            if zone_audio is None or getattr(zone_audio, "size", 0) == 0:
                reference_cache[zone] = None
            else:
                outputs = separate_array(ref_model, zone_audio, source_sr)
                reference_cache[zone] = _pick_instrumental(outputs)
        return reference_cache[zone]

    for stem_key in list(all_stems.keys()):
        canonical = stem_key.split("@", 1)[0]
        zone = stem_key.rsplit("@", 1)[1] if "@" in stem_key else "front"
        surround, height = stem_reaches_surround_height(stem_key, output_fmt)

        # Phase fixer defaults on for diffuse (surround/height) stems; the
        # debleed pass is opt-in because it costs one full inference per stem.
        phase_on = _enabled(phase_overrides, canonical, surround or height)
        debleed_on = _enabled(debleed_overrides, canonical, False)
        if not (phase_on or debleed_on):
            continue
        if progress is not None:
            passes = "+".join(
                name for name, on in (("phase-fix", phase_on), ("debleed", debleed_on)) if on
            )
            progress(f"    Bleed reduction ({passes}): {stem_key}")

        if phase_on:
            reference = _reference_for_zone(zone)
            if reference is not None:
                all_stems[stem_key] = apply_phase_fix(
                    all_stems[stem_key],
                    reference,
                    sep_sr,
                    config.stem_phase_fix_low_hz,
                    config.stem_phase_fix_high_hz,
                    config.stem_phase_fix_scale,
                )

        if debleed_on:
            outputs = separate_array(debleed_model, all_stems[stem_key], sep_sr)
            cleaned = _pick_primary(outputs, canonical)
            if cleaned is not None:
                all_stems[stem_key] = _replace_prefix(all_stems[stem_key], cleaned)

    return all_stems
