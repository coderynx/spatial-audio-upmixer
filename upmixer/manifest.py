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
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from upmixer.config import UpmixConfig

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
        "stem_cache_dir": ("config", "stem_cache_dir"),
        "stems":          ("engine", "stems"),
    },

    "format": {
        "type":        ("config", "output_type"),
        "subtype":     ("config", "output_subtype"),
        "sample_rate": ("config", "output_sample_rate"),
    },

    "mixing": {
        "channel_layout": ("config", "format"),
        "stem_rebalance": ("config", "stem_rebalance"),
        "stem_eq":        ("config", "stem_eq_profiles"),
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
    "stem_cache_dir":              ("stem_cache_dir",   str),
    "stems":                       ("stems",            list),
    "downmix_output":              ("downmix_output_path",    str),
    "downmix_surround_coeff":      ("surround_downmix_coeff", float),
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

    if isinstance(data.get("engine"), dict) and "stem_model" in data["engine"]:
        import warnings
        warnings.warn(
            "'engine.stem_model' is no longer supported and will be ignored. "
            "Model selection is now automatic based on the 'stems' list.",
            DeprecationWarning,
            stacklevel=2,
        )

    _VALID_STEM_NAMES = {
        "vocals", "bass", "drums", "guitar", "piano", "other",
        "kick", "snare", "hi-hat", "ride", "crash", "crowd",
        "Vocals", "Bass", "Drums", "Guitar", "Piano", "Other",
        "Kick", "Snare", "Hi-Hat", "Ride", "Crash", "Crowd",
    }
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
                    f"Valid names: vocals, bass, drums, guitar, piano, other, "
                    f"kick, snare, hi-hat, ride, crash, crowd."
                )


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
                    config=dict(config_flat),
                    engine=dict(engine_params),
                ))
        else:
            jobs.append(AssetJob(
                input=asset["input"],
                output=asset["output"],
                config=config_flat,
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
    """Return a human-readable mapping of manifest keys to config attributes.

    Used by ``--manifest-keys`` CLI flag.
    """
    out: dict[str, str] = {}
    for mk, (ca, t) in sorted(_FIELD_MAP.items()):
        out[mk] = f"{ca}  ({t.__name__})"
    for key in ("mode", "stem_model_dir", "input_format", "stems"):
        out[key] = "engine parameter"
    return out
