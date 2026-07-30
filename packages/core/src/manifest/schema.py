"""Manifest block registry, dataclasses, and schema introspection.

Modules can register their own YAML block keys without modifying this file::

    from upmixer.manifest import register_block_keys

    register_block_keys('mixing', {
        'reverb': {
            'room_size': ('config', 'reverb_room_size'),
            'wet':       ('config', 'reverb_wet'),
        }
    })

See :func:`register_block` and :func:`register_block_keys`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from upmixer.config import UpmixConfig


class ManifestError(ValueError):
    """Raised when a manifest fails structural or version validation."""


@dataclass
class ManifestMeta:
    """Optional informational block from the manifest ``metadata:`` section.

    Not inherited by assets — purely for logging and display.
    """

    name: str | None = None
    author: str | None = None
    description: str | None = None


@dataclass
class AssetJob:
    """One resolved processing job from the ``assets:`` list.

    ``config`` contains flat UpmixConfig-ready keys after deep-merging global
    and asset-level blocks.  ``engine`` holds job-level params that are not
    part of UpmixConfig (mode, stem_model_dir, input_format, stems).
    """

    input: str
    output: str
    config: dict = field(default_factory=dict)
    engine: dict = field(default_factory=dict)


BlockMapping = dict[str, Any]

_BLOCK_REGISTRY: dict[str, BlockMapping] = {
    "engine": {
        "mode":           ("engine", "mode"),
        # stem_model removed — model selection is now automatic based on stems
        "stem_model_dir": ("engine", "stem_model_dir"),
        "input_format":   ("engine", "input_format"),
        "stem_cache_dir":               ("config", "stem_cache_dir"),
        "stem_cache_key":               ("config", "stem_cache_key"),
        "stem_batch_size":              ("config", "stem_batch_size"),
        "stem_segment_size":            ("config", "stem_segment_size"),
        "stem_chunk_duration_s":         ("config", "stem_chunk_duration_s"),
        "stem_model_cache_size":         ("config", "stem_model_cache_size"),
        "stems":                        ("engine", "stems"),
        "stem_silence_skip":            ("config", "stem_silence_skip"),
        "stem_silence_threshold_db":    ("config", "stem_silence_threshold_db"),
        "stem_silence_min_duration_s":  ("config", "stem_silence_min_duration_s"),
        "stem_silence_crossfade_ms":    ("config", "stem_silence_crossfade_ms"),
        "stem_silence_pad_ms":          ("config", "stem_silence_pad_ms"),
    },

    "format": {
        "type":        ("config", "output_type"),
        "subtype":     ("config", "output_subtype"),
        "sample_rate": ("config", "output_sample_rate"),
        "downmix": {
            "enabled":        ("config", "downmix_enabled"),
            "output":         ("config", "downmix_output"),
            "surround_coeff": ("config", "downmix_surround_coeff"),
        },
        # Spatial Audio Engine binaural render — a delivery format
        # (format.type: binaural), not a channel layout or a mixing concern.
        "binaural": {
            "profile": ("config", "binaural_profile"),
        },
        # Spatial Audio Engine crosstalk-cancellation (transaural) render —
        # a delivery format (format.type: transaural), not a channel layout.
        "transaural": {
            "profile": ("config", "transaural_profile"),
        },
    },

    "mixing": {
        "channel_layout": ("config", "format"),
        "stem_rebalance": ("config", "stem_rebalance"),
        "stem_eq":        ("config", "stem_eq_profiles"),
        "stem_routing":   ("config", "stem_routing"),
        "stem_enabled":   ("config", "stem_enabled"),
        "stem_solo":      ("config", "stem_solo"),
        "stem_source_anchor_strength": ("config", "stem_source_anchor_strength"),
        "spatial": {
            "profile": ("config", "spatial_profile"),
            "intensity": ("config", "spatial_intensity"),
            "preanalyze": ("config", "spatial_preanalysis"),
        },
        "stems":          ("engine", "stems"),
    },

    "processing": {
        "preview":          ("config", "preview"),
        "preview_duration": ("config", "preview_duration"),
        "preview_start":    ("config", "preview_start"),
        "fft_size":         ("config", "fft_size"),
        "block_size":       ("config", "block_size"),
        "normalize_output": ("config", "normalize_output"),
    },

    # routing: and mastering: blocks are populated at import time by domain modules
}


def register_block(name: str, mapping: BlockMapping) -> None:
    """Register a new top-level YAML block.

    Use this to add a completely new section (e.g. a reverb or dynamics plugin
    that has its own top-level key in the manifest).

    Args:
        name:    The YAML key name (e.g. ``'reverb'``).
        mapping: Dict mapping YAML sub-keys to ``(bucket, flat_key)`` leaf
                 tuples or nested sub-section dicts.

    Example::

        register_block('reverb', {
            'room_size': ('config', 'reverb_room_size'),
            'wet':       ('config', 'reverb_wet'),
        })
    """
    _BLOCK_REGISTRY[name] = mapping


def register_block_keys(section: str, keys: BlockMapping) -> None:
    """Add or update keys within an existing block section.

    Use this to extend an existing section like ``'mixing'`` or ``'mastering'``
    with new sub-keys contributed by a module.

    Args:
        section: Existing block name (e.g. ``'mixing'``, ``'mastering'``).
        keys:    Dict of new or updated entries (same format as
                 :func:`register_block`).

    Example::

        register_block_keys('mastering', {
            'reverb': {
                'room_size': ('config', 'reverb_room_size'),
                'wet':       ('config', 'reverb_wet'),
            }
        })
    """
    _BLOCK_REGISTRY.setdefault(section, {}).update(keys)


_FIELD_MAP: dict[str, tuple[str, type]] = {
    "format":                     ("output_format",            str),
    "output_type":                ("output_type",              str),
    "output_subtype":             ("output_subtype",           str),
    "output_sample_rate":         ("output_sample_rate",       int),
    "center_gain":                ("center_gain",              float),
    "surround_gain":              ("surround_gain",            float),
    "back_gain":                  ("back_gain",                float),
    "height_gain":                ("height_gain",              float),
    "lfe_gain":                   ("lfe_gain",                 float),
    "lfe_cutoff":                 ("lfe_cutoff_hz",            float),
    "center_extraction_gain":     ("center_extraction_gain",   float),
    "center_attenuation":         ("center_attenuation",       float),
    "content_mix_strength":       ("content_mix_strength",     float),
    "content_hf_analysis_hz":     ("content_hf_analysis_hz",   float),
    "spatial_profile":            ("spatial_profile",          str),
    "spatial_intensity":          ("spatial_intensity",        float),
    "spatial_preanalysis":        ("spatial_preanalysis",      bool),
    "binaural_profile":           ("binaural_profile",         str),
    "transaural_profile":         ("transaural_profile",       str),
    "height_low_rolloff_gain":    ("height_low_rolloff_gain",  float),
    "height_high_shelf_gain":     ("height_high_shelf_gain",   float),
    "fft_size":                   ("fft_size",                 int),
    "block_size":                 ("block_size",               int),
    "normalize_output":           ("normalize_output",         bool),
    "loudness_normalize":         ("loudness_normalize",       bool),
    "loudness_target":            ("loudness_target_lkfs",     float),
    "loudness_max_tp":            ("loudness_max_tp",          float),
    "limiter_lookahead_ms":       ("limiter_lookahead_ms",     float),
    "limiter_release_ms":         ("limiter_release_ms",       float),
    "mastering_eq_profile":       ("mastering_eq_profile",     str),
    "mastering_eq_strength":      ("mastering_eq_strength",    float),
    "mastering_comp_profile":     ("mastering_comp_profile",      str),
    "mastering_comp_threshold_db":("mastering_comp_threshold_db", float),
    "mastering_comp_ratio":       ("mastering_comp_ratio",        float),
    "mastering_comp_attack_ms":   ("mastering_comp_attack_ms",    float),
    "mastering_comp_release_ms":  ("mastering_comp_release_ms",   float),
    "mastering_comp_knee_db":     ("mastering_comp_knee_db",      float),
    "mastering_comp_makeup_db":   ("mastering_comp_makeup_db",    float),
    "mastering_bass_profile":        ("mastering_bass_profile",        str),
    "mastering_bass_sub_gain_db":    ("mastering_bass_sub_gain_db",    float),
    "mastering_bass_mid_gain_db":    ("mastering_bass_mid_gain_db",    float),
    "mastering_bass_mono_cutoff_hz": ("mastering_bass_mono_cutoff_hz", float),
    "mastering_bass_excite":         ("mastering_bass_excite",         bool),
    "mastering_bass_lfe_gain_db":    ("mastering_bass_lfe_gain_db",    float),
    "mastering_match_ref_path":     ("mastering_match_ref_path",     str),
    "mastering_match_ref_strength": ("mastering_match_ref_strength",  float),
    "mastering_match_ref_spectrum": ("mastering_match_ref_spectrum",  bool),
    "mastering_match_ref_rms":      ("mastering_match_ref_rms",       bool),
    "mastering_match_ref_max_db":   ("mastering_match_ref_max_db",    float),
    "stem_rebalance":              ("stem_rebalance",   dict),
    "stem_eq_profiles":            ("stem_eq_profiles", dict),
    "stem_routing":                ("stem_routing",     dict),
    "stem_enabled":                ("stem_enabled",     dict),
    "stem_solo":                   ("stem_solo",        list),
    "stem_cache_dir":              ("stem_cache_dir",   str),
    "stem_cache_key":              ("stem_cache_key",   str),
    "stem_batch_size":             ("stem_batch_size",  int),
    "stem_segment_size":           ("stem_segment_size", int),
    "stem_chunk_duration_s":        ("stem_chunk_duration_s", float),
    "stem_model_cache_size":        ("stem_model_cache_size", int),
    "stems":                       ("stems",            list),
    "stem_silence_skip":           ("stem_silence_skip",           bool),
    "stem_silence_threshold_db":   ("stem_silence_threshold_db",   float),
    "stem_silence_min_duration_s": ("stem_silence_min_duration_s", float),
    "stem_silence_crossfade_ms":   ("stem_silence_crossfade_ms",   float),
    "stem_silence_pad_ms":         ("stem_silence_pad_ms",         float),
    "stem_source_anchor_strength": ("stem_source_anchor_strength", float),
    "downmix_output":              ("downmix_output_path",    str),
    "downmix_surround_coeff":      ("surround_downmix_coeff", float),
    "downmix_enabled":             ("downmix_enabled",        bool),
    "preview":          ("preview",           bool),
    "preview_duration": ("preview_duration_s", float),
    "preview_start":    ("preview_start_s",    float),
}


_ENGINE_TYPES: dict[str, type] = {
    "mode": str,
    "stem_model_dir": str,
    "input_format": str,
    "stems": list,
}


def _leaf_type(entry: tuple[str, str]) -> type:
    bucket, key = entry
    if bucket == "engine":
        return _ENGINE_TYPES[key]
    return _FIELD_MAP[key][1]


def manifest_parameter_schema() -> list[dict[str, object]]:
    """Return canonical manifest fields for UIs, docs, and API clients."""
    defaults = asdict(UpmixConfig())
    result: list[dict[str, object]] = []

    def visit(mapping: BlockMapping, prefix: str) -> None:
        for key, entry in mapping.items():
            path = f"{prefix}.{key}"
            if isinstance(entry, dict):
                visit(entry, path)
                continue
            bucket, flat_key = entry
            expected = _leaf_type(entry)
            result.append({
                "path": path,
                "type": expected.__name__,
                "default": defaults.get(_FIELD_MAP[flat_key][0]) if bucket == "config" else None,
                "asset_override": True,
            })

    for block_name, mapping in _BLOCK_REGISTRY.items():
        visit(mapping, block_name)
    return sorted(result, key=lambda item: str(item["path"]))


def list_manifest_keys() -> dict[str, str]:
    """Return canonical dotted manifest paths and types.

    Used by ``--manifest-keys`` CLI flag.
    """
    return {
        str(item["path"]): f"{item['type']}"
        for item in manifest_parameter_schema()
    }
