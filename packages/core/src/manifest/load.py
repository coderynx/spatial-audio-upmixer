"""Manifest file loading, parsing, and application to :class:`UpmixConfig`."""
from __future__ import annotations

import glob as _glob
import json
import logging
import os
from pathlib import Path
from typing import Any

from upmixer.codecs import DEFAULT_CODEC, codec_extension
from upmixer.config import UpmixConfig
from upmixer.manifest.schema import AssetJob, BlockMapping, ManifestMeta, _BLOCK_REGISTRY, _FIELD_MAP

_log = logging.getLogger("upmixer")


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


def _migrate_format(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    migrated = dict(block)
    # ``wav`` used to mean both "a multichannel bed" and "a WAV container";
    # those are now format.type and format.codec.
    if migrated.get("type") == "wav":
        migrated["type"] = "multichannel"
    migrated.setdefault("codec", DEFAULT_CODEC)
    return migrated


# Blocks and keys the pipeline no longer has. A stored project or manifest
# written while they existed still carries them, and validation rejects an
# unknown field, so they are dropped rather than allowed to fail a load.
_RETIRED_FIELDS: dict[str, set[str]] = {
    "engine": {
        "stem_phase_fix",
        "stem_phase_fix_low_hz",
        "stem_phase_fix_high_hz",
        "stem_phase_fix_scale",
        "stem_phase_fix_reference_model",
        "stem_debleed",
        "stem_debleed_model",
    },
    "mixing": {"spatial"},
    "routing": {
        "center_extraction_gain",
        "center_attenuation",
        "content_mix_strength",
        "content_hf_analysis_hz",
    },
    "processing": {"block_size"},
}


def _migrate_blocks(data: dict) -> dict:
    migrated = dict(data)
    if "format" in migrated:
        migrated["format"] = _migrate_format(migrated["format"])
    for block_name, retired in _RETIRED_FIELDS.items():
        block = migrated.get(block_name)
        if isinstance(block, dict) and retired.intersection(block):
            migrated[block_name] = {k: v for k, v in block.items() if k not in retired}
    engine = migrated.get("engine")
    if isinstance(engine, dict) and engine.get("mode") == "realtime":
        migrated["engine"] = {**engine, "mode": "stem"}
    return migrated


def migrate_manifest(data: dict) -> dict:
    """Fold retired manifest shapes into the current one.

    Applied to the root and to every asset override before validation, so
    manifests and stored projects written before codecs existed — or before
    the realtime pipeline was removed — keep loading.
    """
    migrated = _migrate_blocks(data)
    assets = migrated.get("assets")
    if isinstance(assets, list):
        migrated["assets"] = [
            _migrate_blocks(asset) if isinstance(asset, dict) else asset
            for asset in assets
        ]
    return migrated


def _with_downmix_path(config: dict, output: str) -> dict:
    """Derive a sibling stereo filename when downmix output is enabled."""
    resolved = dict(config)
    if resolved.get("downmix_enabled") and not resolved.get("downmix_output"):
        destination = Path(output)
        resolved["downmix_output"] = str(
            destination.with_name(f"{destination.stem}_stereo{destination.suffix or '.wav'}")
        )
    return resolved


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

        # Asset-level shortcut: stem_cache_dir/stem_cache_key/stem_output_dir/
        # stem_input_dir → engine.*
        _stem_shortcut_keys = ("stem_cache_dir", "stem_cache_key", "stem_output_dir", "stem_input_dir")
        if any(asset.get(key) is not None for key in _stem_shortcut_keys):
            engine_ov = dict(asset_blocks.get("engine", {}))
            for key in _stem_shortcut_keys:
                if asset.get(key) is not None:
                    engine_ov.setdefault(key, asset[key])
            asset_blocks["engine"] = engine_ov

        effective = _deep_merge(global_blocks, asset_blocks)

        config_flat, engine_params = _expand_blocks(effective)

        if asset.get("input_dir"):
            extension = codec_extension(config_flat.get("output_codec") or DEFAULT_CODEC)
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
                out = os.path.join(output_dir, stem + extension)
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
