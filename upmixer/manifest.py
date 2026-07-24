"""Unified assets-based manifest for upmix jobs.

Schema
------
Every manifest must declare a ``version`` and an ``assets`` list::

    version: "1.0.0"

    # Optional informational block — not inherited by assets
    metadata:
      name: "My Project"
      author: "Jane Doe"
      description: "..."

    # Global pipeline blocks (inherited by every asset unless overridden)
    engine:
      mode: stem          # or realtime
      stem_cache_dir: /tmp/upmixer_stems
    mixing:
      channel_layout: 7.1.4
      stem_rebalance:
        Vocals: +1.5
    mastering:
      loudness:
        normalize: true
        target: -18.0
    routing:
      center_gain: 0.85
    format:
      type: adm-bwf
      subtype: PCM_24
      sample_rate: 48000

    # Assets — single file or batch (uniform treatment)
    assets:
      - input: tracks/01.flac
        output: dist/01.wav
        stem_cache_dir: /tmp/stems/01   # asset-level shortcut

      - input: tracks/02.flac
        output: dist/02.wav
        mixing:                         # asset-level block override (deep-merged)
          stem_rebalance:
            Vocals: +0.0

Versioning
----------
``version`` must match ``MAJOR.MINOR`` or ``MAJOR.MINOR.PATCH`` (SemVer-like).
Missing or malformed versions raise :class:`ManifestError`.

Extensibility
-------------
Modules can register their own YAML block keys without modifying this file::

    from upmixer.manifest import register_block_keys

    register_block_keys('mixing', {
        'reverb': {
            'room_size': ('config', 'reverb_room_size'),
            'wet':       ('config', 'reverb_wet'),
        }
    })

See :func:`register_block` and :func:`register_block_keys`.

Priority
--------
CLI flags > per-asset manifest values > global manifest values > UpmixConfig defaults.
"""
from __future__ import annotations

import glob as _glob
import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel
from upmixer.separation.stem_plan import MANIFEST_TO_CANONICAL

_log = logging.getLogger("upmixer")

_SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


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
    "height_low_rolloff_gain":    ("height_low_rolloff_gain",  float),
    "height_high_shelf_gain":     ("height_high_shelf_gain",   float),
    "fft_size":                   ("fft_size",                 int),
    "block_size":                 ("block_size",               int),
    "normalize_output":           ("normalize_output",         bool),
    "loudness_normalize":         ("loudness_normalize",       bool),
    "loudness_target":            ("loudness_target_lkfs",     float),
    "loudness_max_tp":            ("loudness_max_tp",          float),
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*; override wins on conflicts."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _expand_mapping(
    data: dict,
    mapping: BlockMapping,
    config_out: dict,
    engine_out: dict,
) -> None:
    """Walk *mapping* against *data*, populating *config_out* / *engine_out*."""
    for yaml_key, entry in mapping.items():
        if yaml_key not in data or data[yaml_key] is None:
            continue
        value = data[yaml_key]
        if isinstance(entry, tuple):
            bucket, flat_key = entry
            if bucket == "config":
                config_out[flat_key] = value
            elif bucket == "engine":
                engine_out[flat_key] = value
        elif isinstance(entry, dict) and isinstance(value, dict):
            _expand_mapping(value, entry, config_out, engine_out)


def _expand_blocks(blocks: dict) -> tuple[dict, dict]:
    """Expand merged config blocks into ``(config_flat, engine_params)``.

    Only block names present in :data:`_BLOCK_REGISTRY` are processed.
    Unrecognised block names are silently ignored (may belong to a module
    that has not yet registered its keys).
    """
    config_out: dict = {}
    engine_out: dict = {}
    for block_name, block_data in blocks.items():
        mapping = _BLOCK_REGISTRY.get(block_name)
        if mapping is None or not isinstance(block_data, dict):
            continue
        _expand_mapping(block_data, mapping, config_out, engine_out)
    return config_out, engine_out


def _with_downmix_path(config: dict, output: str) -> dict:
    """Derive a sibling stereo filename when downmix output is enabled."""
    resolved = dict(config)
    if resolved.get("downmix_enabled") and not resolved.get("downmix_output"):
        destination = Path(output)
        resolved["downmix_output"] = str(
            destination.with_name(f"{destination.stem}_stereo{destination.suffix or '.wav'}")
        )
    return resolved


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


def _validate_leaf(value: object, entry: tuple[str, str], path: str) -> None:
    if value is None:
        return
    expected = _leaf_type(entry)
    if expected is float:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif path == "mixing.channel_layout":
        # YAML parses unquoted layouts such as 7.1 as floats; existing
        # manifests conventionally use that concise spelling.
        valid = isinstance(value, (str, int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, expected)
    if not valid:
        raise ManifestError(f"{path} must be a {expected.__name__}.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ManifestError(f"{path} must be finite.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ManifestError(f"{path} must be finite.")
        minimums = {
            "mixing.spatial.intensity": 0.0,
            "mixing.stem_source_anchor_strength": 0.0,
            "engine.stem_batch_size": 1.0,
            "engine.stem_segment_size": 1.0,
            "engine.stem_chunk_duration_s": 0.0,
            "engine.stem_model_cache_size": 1.0,
            "engine.stem_silence_min_duration_s": 0.0,
            "engine.stem_silence_crossfade_ms": 0.0,
            "engine.stem_silence_pad_ms": 0.0,
            "processing.preview_duration": 0.0,
            "processing.preview_start": 0.0,
            "routing.lfe_cutoff": 0.0,
            "routing.content_hf_analysis_hz": 0.0,
            "mastering.eq.strength": 0.0,
            "mastering.match_reference.strength": 0.0,
            "mastering.match_reference.max_db": 0.0,
            "mastering.compressor.ratio": 1.0,
            "mastering.compressor.attack_ms": 0.0,
            "mastering.compressor.release_ms": 0.0,
            "mastering.compressor.knee_db": 0.0,
        }
        maximums = {
            "mixing.spatial.intensity": 1.0,
            "mixing.stem_source_anchor_strength": 1.0,
            "mastering.eq.strength": 1.0,
            "mastering.match_reference.strength": 1.0,
        }
        if path in minimums and float(value) < minimums[path]:
            raise ManifestError(f"{path} must be at least {minimums[path]}.")
        if path in maximums and float(value) > maximums[path]:
            raise ManifestError(f"{path} must be at most {maximums[path]}.")
    choices = {
        "engine.mode": {"realtime", "stem"},
        "format.type": {"wav", "adm-bwf"},
        "format.subtype": {"PCM_16", "PCM_24", "PCM_32", "FLOAT"},
        "format.downmix.surround_coeff": {0.7071, 0.5, 0.0},
        "mixing.spatial.profile": {"auto", "balanced", "intimate", "rhythmic", "spacious", "live", "detailed"},
    }
    if path in choices and value not in choices[path]:
        raise ManifestError(f"{path} has an unsupported value: {value!r}.")


def _validate_block_fields(block: dict, mapping: BlockMapping, prefix: str) -> None:
    for key, value in block.items():
        path = f"{prefix}.{key}"
        if key not in mapping:
            raise ManifestError(f"Unknown manifest field '{path}'.")
        entry = mapping[key]
        if isinstance(entry, dict):
            if not isinstance(value, dict):
                raise ManifestError(f"{path} must be a mapping.")
            _validate_block_fields(value, entry, path)
        else:
            _validate_leaf(value, entry, path)


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


def validate_manifest(data: dict) -> None:
    """Validate the top-level manifest structure.

    Raises:
        ManifestError: if ``version`` is missing/malformed, ``assets`` is absent
                       or empty, or any asset entry lacks ``input`` / ``output``.
    """
    version = data.get("version")
    if not version or not _SEMVER_RE.match(str(version).strip()):
        raise ManifestError(
            f"Invalid or missing 'version': {version!r}. "
            'Must be MAJOR.MINOR or MAJOR.MINOR.PATCH (e.g. "1.0" or "1.0.0").'
        )

    allowed_root = {"version", "metadata", "assets", *_BLOCK_REGISTRY}
    for key in data:
        if key not in allowed_root and not key.startswith("_"):
            raise ManifestError(f"Unknown manifest block '{key}'.")
    for block_name, mapping in _BLOCK_REGISTRY.items():
        block = data.get(block_name)
        if block is not None:
            if not isinstance(block, dict):
                raise ManifestError(f"{block_name} must be a mapping.")
            _validate_block_fields(block, mapping, block_name)

    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) == 0:
        raise ManifestError(
            "'assets' must be a non-empty list. "
            "Each entry needs at least 'input' and 'output' fields."
        )

    for i, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ManifestError(
                f"assets[{i}] must be a mapping, got {type(asset).__name__}."
            )
        has_explicit = bool(asset.get("input") and asset.get("output"))
        has_dir = bool(asset.get("input_dir") and asset.get("output_dir"))
        if not has_explicit and not has_dir:
            raise ManifestError(
                f"assets[{i}] needs 'input'+'output' or 'input_dir'+'output_dir'."
            )
        allowed_asset = _ASSET_NON_BLOCK_KEYS | set(_BLOCK_REGISTRY)
        for key in asset:
            if key not in allowed_asset and not key.startswith("_"):
                raise ManifestError(f"Unknown manifest field 'assets[{i}].{key}'.")
        for block_name, mapping in _BLOCK_REGISTRY.items():
            block = asset.get(block_name)
            if block is not None:
                if not isinstance(block, dict):
                    raise ManifestError(f"assets[{i}].{block_name} must be a mapping.")
                _validate_block_fields(block, mapping, f"assets[{i}].{block_name}")

    if isinstance(data.get("engine"), dict) and "stem_model" in data["engine"]:
        import warnings
        warnings.warn(
            "'engine.stem_model' is no longer supported and will be ignored. "
            "Model selection is now automatic based on the 'stems' list.",
            DeprecationWarning,
            stacklevel=2,
        )

    _valid_manifest = set(MANIFEST_TO_CANONICAL.keys())
    _valid_canonical = set(MANIFEST_TO_CANONICAL.values())
    _VALID_STEM_NAMES = _valid_manifest | _valid_canonical
    _stems_to_check = [
        data.get("engine", {}).get("stems") if isinstance(data.get("engine"), dict) else None,
        data.get("mixing", {}).get("stems") if isinstance(data.get("mixing"), dict) else None,
    ]
    for asset in assets:
        if isinstance(asset.get("engine"), dict):
            _stems_to_check.append(asset["engine"].get("stems"))
        if isinstance(asset.get("mixing"), dict):
            _stems_to_check.append(asset["mixing"].get("stems"))
    for stem_list in _stems_to_check:
        if stem_list is None:
            continue
        if not isinstance(stem_list, list):
            raise ManifestError(
                f"'stems' must be a list of stem name strings, "
                f"got {type(stem_list).__name__}."
            )
        for s in stem_list:
            if s not in _VALID_STEM_NAMES:
                raise ManifestError(
                    f"Unknown stem name '{s}'. "
                    f"Valid names: {', '.join(sorted(_valid_manifest))}."
                )

    valid_channels = {label.value for label in ChannelLabel}

    def _valid_route_stem(stem_key: object) -> bool:
        if not isinstance(stem_key, str):
            return False
        stem_name, _, zone = stem_key.partition("@")
        return stem_name in _VALID_STEM_NAMES and (
            not zone or zone in {"front", "surround", "back", "height_front", "height_back"}
        )

    def _validate_stem_mix(blocks: dict, location: str) -> None:
        mixing = blocks.get("mixing")
        if not isinstance(mixing, dict):
            return
        enabled = mixing.get("stem_enabled")
        if enabled is not None:
            if not isinstance(enabled, dict):
                raise ManifestError(f"{location}.mixing.stem_enabled must be a mapping.")
            for stem_key, value in enabled.items():
                if not _valid_route_stem(stem_key):
                    raise ManifestError(f"Unknown stem routing key '{stem_key}'.")
                if not isinstance(value, bool):
                    raise ManifestError(
                        f"{location}.mixing.stem_enabled.{stem_key} must be true or false."
                    )
        solo = mixing.get("stem_solo")
        if solo is not None:
            if not isinstance(solo, list):
                raise ManifestError(f"{location}.mixing.stem_solo must be a list.")
            for stem_key in solo:
                if not _valid_route_stem(stem_key):
                    raise ManifestError(f"Unknown solo stem '{stem_key}'.")
        routing = mixing.get("stem_routing")
        if routing is None:
            return
        if not isinstance(routing, dict):
            raise ManifestError(f"{location}.mixing.stem_routing must be a mapping.")
        for stem_key, channel_map in routing.items():
            if not _valid_route_stem(stem_key):
                raise ManifestError(f"Unknown stem routing key '{stem_key}'.")
            if not isinstance(channel_map, dict):
                raise ManifestError(
                    f"{location}.mixing.stem_routing.{stem_key} must be a channel mapping."
                )
            for channel, weight in channel_map.items():
                if channel not in valid_channels:
                    raise ManifestError(f"Unknown output channel '{channel}' for stem '{stem_key}'.")
                if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                    raise ManifestError(
                        f"Route weight for '{stem_key}.{channel}' must be a non-negative number."
                    )
                if not math.isfinite(float(weight)) or float(weight) < 0.0:
                    raise ManifestError(
                        f"Route weight for '{stem_key}.{channel}' must be finite and non-negative."
                    )

    _validate_stem_mix(data, "manifest")
    for index, asset in enumerate(assets):
        _validate_stem_mix(asset, f"assets[{index}]")


_ASSET_NON_BLOCK_KEYS: frozenset[str] = frozenset({
    "input", "output", "stem_cache_dir",
    "input_dir", "output_dir", "glob",
})


def parse_manifest(data: dict) -> tuple[ManifestMeta | None, list[AssetJob]]:
    """Parse a validated manifest dict into ``(ManifestMeta, list[AssetJob])``.

    Call :func:`validate_manifest` first.  Each :class:`AssetJob` has a
    ``config`` dict of flat UpmixConfig-ready keys (global defaults deep-merged
    with any asset-level overrides) and an ``engine`` dict for job-level params.

    Args:
        data: Raw manifest dict from :func:`load_manifest`.

    Returns:
        Tuple of optional :class:`ManifestMeta` and list of :class:`AssetJob`.
    """
    meta: ManifestMeta | None = None
    meta_raw = data.get("metadata")
    if isinstance(meta_raw, dict):
        meta = ManifestMeta(
            name=meta_raw.get("name"),
            author=meta_raw.get("author"),
            description=meta_raw.get("description"),
        )

    all_block_keys = set(_BLOCK_REGISTRY.keys())
    global_blocks: dict[str, dict] = {
        k: v for k, v in data.items()
        if k in all_block_keys and isinstance(v, dict)
    }

    jobs: list[AssetJob] = []
    for asset in data.get("assets", []):
        asset_blocks: dict[str, dict] = {
            k: v for k, v in asset.items()
            if k in all_block_keys and isinstance(v, dict)
        }

        # Asset-level shortcut: stem_cache_dir → engine.stem_cache_dir
        if asset.get("stem_cache_dir") is not None:
            engine_ov = dict(asset_blocks.get("engine", {}))
            engine_ov.setdefault("stem_cache_dir", asset["stem_cache_dir"])
            asset_blocks["engine"] = engine_ov

        effective = _deep_merge(global_blocks, asset_blocks)

        config_flat, engine_params = _expand_blocks(effective)

        if asset.get("input_dir"):
            input_dir = asset["input_dir"]
            output_dir = asset["output_dir"]
            glob_pat = asset.get("glob")
            safe = _glob.escape(input_dir)
            if glob_pat:
                files = sorted(_glob.glob(os.path.join(safe, glob_pat)))
            else:
                wav = _glob.glob(os.path.join(safe, "*.wav"))
                flac = _glob.glob(os.path.join(safe, "*.flac"))
                files = sorted(wav + flac, key=os.path.basename)
            if not files:
                _log.warning("assets input_dir=%r matched no .wav/.flac files", input_dir)
            for f in files:
                stem = os.path.splitext(os.path.basename(f))[0]
                out = os.path.join(output_dir, stem + ".wav")
                jobs.append(AssetJob(
                    input=f,
                    output=out,
                    config=_with_downmix_path(config_flat, out),
                    engine=dict(engine_params),
                ))
        else:
            jobs.append(AssetJob(
                input=asset["input"],
                output=asset["output"],
                config=_with_downmix_path(config_flat, asset["output"]),
                engine=engine_params,
            ))

    return meta, jobs


def apply_asset_job(config: UpmixConfig, job: AssetJob) -> None:
    """Apply an :class:`AssetJob`'s config dict to a :class:`UpmixConfig` in-place.

    Iterates ``job.config``, coerces each value via :data:`_FIELD_MAP`, and
    sets the corresponding attribute on *config*.  Unknown keys log a warning
    and are skipped.  ``None`` values are skipped (preserve config default).

    Args:
        config: Config object to mutate.
        job:    Resolved asset job from :func:`parse_manifest`.
    """
    for key, value in job.config.items():
        if value is None:
            continue
        if key not in _FIELD_MAP:
            _log.warning("Unknown manifest config key '%s' — ignored", key)
            continue
        config_attr, coerce = _FIELD_MAP[key]
        try:
            coerced = coerce(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Manifest key '{key}': cannot convert {value!r} to "
                f"{coerce.__name__}: {exc}"
            ) from exc
        setattr(config, config_attr, coerced)


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON manifest file and return it as a plain dict.

    Args:
        path: Path to a ``.yaml``, ``.yml``, or ``.json`` file.

    Returns:
        Dict of manifest key/value pairs.  Empty manifest returns ``{}``.

    Raises:
        FileNotFoundError: if *path* does not exist.
        ImportError:       if a YAML file is given but PyYAML is not installed.
        ValueError:        if the file extension is not recognised.
        json.JSONDecodeError / yaml.YAMLError: on parse failure.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest file not found: {path}")

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required to load YAML manifest files. "
                "Install it with: pip install pyyaml"
            ) from exc
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(
            f"Unrecognised manifest extension '{suffix}'. "
            "Use .yaml, .yml, or .json."
        )

    return data or {}


def list_manifest_keys() -> dict[str, str]:
    """Return canonical dotted manifest paths and types.

    Used by ``--manifest-keys`` CLI flag.
    """
    return {
        str(item["path"]): f"{item['type']}"
        for item in manifest_parameter_schema()
    }
