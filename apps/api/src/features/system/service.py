"""Configuration schema aggregation for dynamic and advanced web controls."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from upmixer.config import UpmixConfig


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
        "capabilities": {"stem_separation": capability},
    }
