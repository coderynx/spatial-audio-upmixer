"""Manifest validation and worker-path materialization."""

from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from upmixer.config import UpmixConfig
from upmixer.manifest import parse_manifest, validate_manifest
from upmixer_web.models import ImportBatch, Job


def ensure_stem_separation_available(
    manifest: dict[str, Any],
    capability: dict[str, Any],
) -> None:
    """Reject runnable stem manifests when optional inference support is absent."""
    engine = manifest.get("engine", {})
    if isinstance(engine, dict) and engine.get("mode", "realtime") == "stem":
        if not capability["available"]:
            raise ValueError(str(capability["install_message"]))


def normalize_job_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate user-configurable blocks without trusting user file paths."""
    normalized = copy.deepcopy(manifest)
    normalized.setdefault("version", "1.0.0")
    normalized.pop("assets", None)
    mastering = normalized.get("mastering")
    match_reference = (
        mastering.get("match_reference") if isinstance(mastering, dict) else None
    )
    if isinstance(match_reference, dict) and "path" in match_reference:
        raise ValueError("mastering.match_reference.path is managed by reference upload")
    validate_manifest({**normalized, "assets": [{"input": "input.wav", "output": "output.wav"}]})
    return normalized


def materialize_manifest(
    job: Job,
    import_batch: ImportBatch,
    input_paths: list[Path],
    work_dir: Path,
    stem_cache_dir: Path,
    mastering_reference_path: Path | None = None,
) -> dict[str, Any]:
    """Inject server-owned paths into a stored manifest."""
    data = copy.deepcopy(job.manifest)
    output_type = data.get("format", {}).get("type", "wav")
    extension = ".wav" if output_type in {"wav", "adm-bwf"} else ".wav"
    assets = []
    for track, asset, input_path in zip(job.tracks, import_batch.assets, input_paths, strict=True):
        output = work_dir / f"{track.position + 1:02d}-{Path(asset.filename).stem}{extension}"
        assets.append({
            "input": str(input_path),
            "output": str(output),
            "stem_cache_dir": str(stem_cache_dir),
        })
    data["assets"] = assets
    if mastering_reference_path is not None:
        mastering = data.setdefault("mastering", {})
        match_reference = mastering.setdefault("match_reference", {})
        match_reference["path"] = str(mastering_reference_path)
    parse_manifest(data)
    return data


def configuration_schema(capability: dict[str, Any]) -> dict[str, Any]:
    """Return defaults used by dynamic and advanced web controls."""
    from upmixer.formats import FORMAT_MAP
    from upmixer.manifest import list_manifest_keys
    from upmixer.mastering.bass import BASS_PROFILES
    from upmixer.mastering.compressor import COMP_PROFILES
    from upmixer.mastering.eq import EQ_PROFILES
    from upmixer.separation.stem_eq import STEM_EQ_PROFILES
    from upmixer.separation.stem_plan import MANIFEST_TO_CANONICAL
    from upmixer.separation.stem_router import STEM_ROUTING_PRESET_NAMES

    stems = list(dict.fromkeys(MANIFEST_TO_CANONICAL.values()))

    return {
        "defaults": asdict(UpmixConfig()),
        "manifest_keys": list_manifest_keys(),
        "choices": {
            "channel_layouts": list(FORMAT_MAP),
            "output_types": ["wav", "adm-bwf"],
            "output_subtypes": ["PCM_16", "PCM_24", "PCM_32", "FLOAT"],
            "sample_rates": [44100, 48000, 88200, 96000, 192000],
            "modes": ["realtime", "stem"],
            "spatial_profiles": ["auto", "balanced", "intimate", "rhythmic", "spacious", "live", "detailed"],
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
        },
        "capabilities": {"stem_separation": capability},
    }
