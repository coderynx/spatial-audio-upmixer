"""Configuration schema aggregation for dynamic and advanced web controls."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from upmixer.codecs import CODECS, WAV_SUBTYPES
from upmixer.config import UpmixConfig


def engine_constants() -> dict[str, Any]:
    """Return the tunable DSP constants the web preview engine mirrors.

    Every value is read from its real core source module (``UpmixConfig``
    defaults and the mastering/routing/voicing profile tables) — never
    re-typed as a literal — so the frontend fetches one authoritative copy at
    bootstrap instead of hand-mirroring these numbers. See
    ``docs/contracts/preview_export_parity.md``.
    """
    from upmixer.binaural.profiles import DECODE_FILTER_SET
    from upmixer.binaural.profiles import VOICING_PARAMS as BINAURAL_VOICING
    from upmixer.binaural.renderer import BINAURAL_LOUDNESS_MAX_GAIN_DB
    from upmixer.crosstalk.profiles import XTC_FILTER_SET
    from upmixer.crosstalk.profiles import VOICING_PARAMS as TRANSAURAL_VOICING
    from upmixer.crosstalk.renderer import CROSSTALK_LOUDNESS_MAX_GAIN_DB
    from upmixer.mastering.eq import EQ_FIR_ASSETS
    from upmixer.mastering.bass import (
        BASS_PROFILES,
        DECORR_FAST_MS,
        DECORR_HIGH_HZ,
        DECORR_LOW_HZ,
        DECORR_MAX_DELAY_MS,
        DECORR_SECTIONS,
        DECORR_SLOW_MS,
        EXCITE_BLEND,
        EXCITE_DRIVE,
        LF_SPREADS,
        MID_CUTOFF_HZ,
        PUNCH_FAST_MS,
        PUNCH_MAX_DB,
        PUNCH_SLOW_MS,
        SUB_CUTOFF_HZ,
    )
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.mastering.delivery import (
        DEFAULT_MAX_TP_DBTP,
        DEFAULT_TARGET_LKFS,
        DELIVERY_TARGETS,
    )
    from upmixer.mastering.dyneq import MAX_BANDS as DYNEQ_MAX_BANDS
    from upmixer.mastering.limiter import _SAFETY_MARGIN_DB
    from upmixer.separation.stem_eq import STEM_EQ_FIR_ASSETS
    from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
    from upmixer.utils import ITU_CENTER_COEFF

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
        "height_directional_band_hz": cfg.height_directional_band_hz,
        "height_directional_band_gain": cfg.height_directional_band_gain,
        "stem_transient_duck": cfg.stem_transient_duck,
        "soft_limit_threshold": cfg.peak_limit_threshold,
        "limiter_lookahead_ms": cfg.limiter_lookahead_ms,
        "limiter_release_ms": cfg.limiter_release_ms,
        "safety_margin_db": _SAFETY_MARGIN_DB,
        "loudness_max_gain_db": cfg.loudness_max_gain_db,
        "surround_downmix_coeff": cfg.surround_downmix_coeff,
        "height_downmix_coeff": cfg.height_downmix_coeff,
        "itu_center_coeff": ITU_CENTER_COEFF,
        # The shared DSP core encodes the ambisonic bus, so the browser must
        # not re-derive these angles from its own coordinate table.
        "speaker_directions": {
            label.value: {
                "azimuth_rad": position.azimuth_rad,
                "elevation_rad": position.elevation_rad,
            }
            for label, position in SPEAKER_AZIMUTH_ELEVATION.items()
        },
        "dyneq_max_bands": DYNEQ_MAX_BANDS,
        "comp_profiles": COMP_PROFILES,
        "bass_profiles": BASS_PROFILES,
        "delivery_targets": DELIVERY_TARGETS,
        # What an unset target/ceiling resolves to with no preset named.
        "delivery_default": {
            "target_lkfs": DEFAULT_TARGET_LKFS,
            "max_tp_dbtp": DEFAULT_MAX_TP_DBTP,
            "tolerance_lu": None,
        },
        "bass_sub_cutoff_hz": SUB_CUTOFF_HZ,
        "bass_mid_cutoff_hz": MID_CUTOFF_HZ,
        "bass_excite_blend": EXCITE_BLEND,
        "bass_excite_drive": EXCITE_DRIVE,
        "bass_lf_spreads": {name: list(channels) for name, channels in LF_SPREADS.items()},
        "bass_punch_fast_ms": PUNCH_FAST_MS,
        "bass_punch_slow_ms": PUNCH_SLOW_MS,
        "bass_punch_max_db": PUNCH_MAX_DB,
        "bass_decorr_low_hz": DECORR_LOW_HZ,
        "bass_decorr_high_hz": DECORR_HIGH_HZ,
        "bass_decorr_sections": DECORR_SECTIONS,
        "bass_decorr_max_delay_ms": DECORR_MAX_DELAY_MS,
        "bass_decorr_fast_ms": DECORR_FAST_MS,
        "bass_decorr_slow_ms": DECORR_SLOW_MS,
        "binaural_loudness_max_gain_db": BINAURAL_LOUDNESS_MAX_GAIN_DB,
        "crosstalk_loudness_max_gain_db": CROSSTALK_LOUDNESS_MAX_GAIN_DB,
        "voicing_params": {p.value: asdict(v) for p, v in BINAURAL_VOICING.items()},
        "transaural_voicing_params": {p.value: asdict(v) for p, v in TRANSAURAL_VOICING.items()},
        "eq_fir_assets": EQ_FIR_ASSETS,
        "stem_eq_fir_assets": STEM_EQ_FIR_ASSETS,
        "decode_filter_set": {p.value: name for p, name in DECODE_FILTER_SET.items()},
        "xtc_filter_set": {p.value: name for p, name in XTC_FILTER_SET.items()},
    }


def configuration_schema(capability: dict[str, Any]) -> dict[str, Any]:
    """Return defaults used by dynamic and advanced web controls."""
    from upmixer.crosstalk.profiles import CROSSTALK_PROFILES
    from upmixer.formats import BINAURAL_BED_FORMATS, FORMAT_MAP, TRANSAURAL_BED_FORMATS
    from upmixer.manifest import list_manifest_keys, manifest_parameter_schema
    from upmixer.mastering.bass import BASS_PROFILES, LFE_MODES, LF_SPREAD_NAMES
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.mastering.delivery import DELIVERY_TARGETS
    from upmixer.mastering.eq import EQ_PROFILES
    from upmixer.separation.bleed_reduction import DEBLEED_MODELS, PHASE_FIX_REFERENCE_MODELS
    from upmixer.separation.stem_eq import STEM_EQ_PROFILES
    from upmixer.separation.stem_plan import MANIFEST_TO_CANONICAL
    from upmixer.separation.stem_placement import STEM_ROUTING_PRESET_NAMES
    from upmixer_web.features.projects.storage import PREVIEW_QUALITY_LEVELS

    stems = list(dict.fromkeys(MANIFEST_TO_CANONICAL.values()))

    return {
        "defaults": asdict(UpmixConfig()),
        "manifest_keys": list_manifest_keys(),
        "manifest_parameters": manifest_parameter_schema(),
        "choices": {
            "channel_layouts": list(FORMAT_MAP),
            "output_types": ["multichannel", "adm-bwf", "binaural", "transaural"],
            "output_codecs": [
                {
                    "name": codec.name,
                    "label": codec.label,
                    "extension": codec.extension,
                    "subtypes": list(codec.subtypes),
                    "max_channels": codec.max_channels,
                    "sample_rates": list(codec.sample_rates) if codec.sample_rates else None,
                }
                for codec in CODECS.values()
            ],
            "output_subtypes": list(WAV_SUBTYPES),
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
            "delivery_targets": list(DELIVERY_TARGETS),
            "bass_spreads": list(LF_SPREAD_NAMES),
            "bass_lfe_modes": list(LFE_MODES),
            "stem_eq_profiles": sorted(STEM_EQ_PROFILES),
            "stem_phase_fix_reference_models": list(PHASE_FIX_REFERENCE_MODELS),
            "stem_debleed_models": list(DEBLEED_MODELS),
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
