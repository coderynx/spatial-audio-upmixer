"""Configuration schema aggregation for dynamic and advanced web controls."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from upmixer.config import UpmixConfig


def engine_constants() -> dict[str, Any]:
    """Return the tunable DSP constants the web preview engine mirrors.

    Every value is read from its real core source module (``UpmixConfig``
    defaults and the mastering/routing/voicing profile tables) — never
    re-typed as a literal — so the frontend fetches one authoritative copy at
    bootstrap instead of hand-mirroring these numbers. See
    ``docs/contracts/preview_export_parity.md``.
    """
    from upmixer.binaural.profiles import VOICING_PARAMS as BINAURAL_VOICING
    from upmixer.binaural.renderer import BINAURAL_LOUDNESS_MAX_GAIN_DB
    from upmixer.crosstalk.profiles import VOICING_PARAMS as TRANSAURAL_VOICING
    from upmixer.crosstalk.renderer import CROSSTALK_LOUDNESS_MAX_GAIN_DB
    from upmixer.mastering.bass import (
        BASS_PROFILES,
        EXCITE_BLEND,
        EXCITE_DRIVE,
        MID_CUTOFF_HZ,
        SUB_CUTOFF_HZ,
    )
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.separation.stem_router import (
        HEIGHT_HAAS_DELAY_MS_L,
        HEIGHT_HAAS_DELAY_MS_R,
        SURROUND_HAAS_DELAY_MS_L,
        SURROUND_HAAS_DELAY_MS_R,
    )
    from upmixer.utils import DIFFUSE_SEND_BLEND, ITU_CENTER_COEFF

    cfg = UpmixConfig()
    return {
        "channel_group_gains": {
            "center": cfg.center_gain,
            "surround": cfg.surround_gain,
            "back": cfg.back_gain,
            "height": cfg.height_gain,
        },
        "lfe_gain": cfg.lfe_gain,
        "lfe_lowpass_hz": cfg.lfe_cutoff_hz,
        "surround_bass_cutoff_hz": cfg.surround_bass_cutoff_hz,
        "height_low_rolloff_hz": cfg.height_low_rolloff_hz,
        "height_low_rolloff_gain": cfg.height_low_rolloff_gain,
        "height_crossover_hz": cfg.height_crossover_hz,
        "height_high_shelf_gain": cfg.height_high_shelf_gain,
        "soft_limit_threshold": cfg.peak_limit_threshold,
        "limiter_lookahead_ms": cfg.limiter_lookahead_ms,
        "limiter_release_ms": cfg.limiter_release_ms,
        "loudness_max_gain_db": cfg.loudness_max_gain_db,
        "surround_downmix_coeff": cfg.surround_downmix_coeff,
        "itu_center_coeff": ITU_CENTER_COEFF,
        "diffuse_send_blend": DIFFUSE_SEND_BLEND,
        "surround_haas_ms": {"left": SURROUND_HAAS_DELAY_MS_L, "right": SURROUND_HAAS_DELAY_MS_R},
        "height_haas_ms": {"left": HEIGHT_HAAS_DELAY_MS_L, "right": HEIGHT_HAAS_DELAY_MS_R},
        "comp_profiles": COMP_PROFILES,
        "bass_profiles": BASS_PROFILES,
        "bass_sub_cutoff_hz": SUB_CUTOFF_HZ,
        "bass_mid_cutoff_hz": MID_CUTOFF_HZ,
        "bass_excite_blend": EXCITE_BLEND,
        "bass_excite_drive": EXCITE_DRIVE,
        "binaural_loudness_max_gain_db": BINAURAL_LOUDNESS_MAX_GAIN_DB,
        "crosstalk_loudness_max_gain_db": CROSSTALK_LOUDNESS_MAX_GAIN_DB,
        "voicing_params": {p.value: asdict(v) for p, v in BINAURAL_VOICING.items()},
        "transaural_voicing_params": {p.value: asdict(v) for p, v in TRANSAURAL_VOICING.items()},
    }


def configuration_schema(capability: dict[str, Any]) -> dict[str, Any]:
    """Return defaults used by dynamic and advanced web controls."""
    from upmixer.crosstalk.profiles import CROSSTALK_PROFILES
    from upmixer.formats import BINAURAL_BED_FORMATS, FORMAT_MAP, TRANSAURAL_BED_FORMATS
    from upmixer.manifest import list_manifest_keys, manifest_parameter_schema
    from upmixer.mastering.bass import BASS_PROFILES
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.mastering.eq import EQ_PROFILES
    from upmixer.separation.stem_eq import STEM_EQ_PROFILES
    from upmixer.separation.stem_plan import MANIFEST_TO_CANONICAL
    from upmixer.separation.stem_router import STEM_ROUTING_PRESET_NAMES
    from upmixer_web.features.projects.storage import PREVIEW_QUALITY_LEVELS

    stems = list(dict.fromkeys(MANIFEST_TO_CANONICAL.values()))

    return {
        "defaults": asdict(UpmixConfig()),
        "manifest_keys": list_manifest_keys(),
        "manifest_parameters": manifest_parameter_schema(),
        "choices": {
            "channel_layouts": list(FORMAT_MAP),
            "output_types": ["wav", "adm-bwf", "binaural", "transaural"],
            "output_subtypes": ["PCM_16", "PCM_24", "PCM_32", "FLOAT"],
            "sample_rates": [44100, 48000, 88200, 96000, 192000],
            "modes": ["realtime", "stem"],
            "spatial_profiles": ["auto", "balanced", "intimate", "rhythmic", "spacious", "live", "detailed"],
            "binaural_profiles": ["studio", "listening", "flat"],
            "binaural_beds": list(BINAURAL_BED_FORMATS),
            "transaural_profiles": list(CROSSTALK_PROFILES),
            "transaural_beds": list(TRANSAURAL_BED_FORMATS),
            "eq_profiles": sorted(EQ_PROFILES),
            "compressor_profiles": sorted(COMP_PROFILES),
            "bass_profiles": sorted(BASS_PROFILES),
            "stem_eq_profiles": sorted(STEM_EQ_PROFILES),
            "stem_routing_presets": list(STEM_ROUTING_PRESET_NAMES),
            "layout_channels": {
                name: [label.value for label in fmt.channels]
                for name, fmt in FORMAT_MAP.items()
            },
            "stems": stems,
            "preview_qualities": list(PREVIEW_QUALITY_LEVELS),
        },
        "constants": engine_constants(),
        "capabilities": {"stem_separation": capability},
    }
