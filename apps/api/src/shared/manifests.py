"""Manifest validation and worker-path materialization."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from upmixer.manifest import parse_manifest, validate_manifest
from upmixer_web.shared.models import Job

# Import-time side effect: registers manifest block keys. MasteringChain only
# imports these lazily inside process(), so without this validate_manifest
# rejects mastering.*/match_reference.* fields as "Unknown manifest field".
import upmixer.mastering.bass  # noqa: F401 E402
import upmixer.mastering.compressor  # noqa: F401 E402
import upmixer.mastering.eq  # noqa: F401 E402
import upmixer.mastering.match_reference  # noqa: F401 E402


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
    format_block = normalized.get("format")
    downmix = format_block.get("downmix") if isinstance(format_block, dict) else None
    if isinstance(downmix, dict) and downmix.get("output") is not None:
        raise ValueError("format.downmix.output is managed by the server")
    validate_manifest({**normalized, "assets": [{"input": "input.wav", "output": "output.wav"}]})
    return normalized


def materialize_manifest(
    job: Job,
    input_paths: list[Path],
    work_dir: Path,
    stem_cache_dir: Path,
    mastering_reference_path: Path | None = None,
) -> dict[str, Any]:
    """Inject server-owned paths into a stored manifest.

    A project export's ``job.project_snapshot["tracks"]`` (keyed by asset id
    — see ``features.projects.service.project_export_job``) carries plain
    per-track data baked in at export time: `stem_input_dir` points at the
    project's own already-separated stems instead of the shared stem cache,
    and `manifest_overrides` is deep-merged over this asset's blocks. An
    ordinary (non-project) job has no snapshot and uses the shared
    `stem_cache_dir` as before.
    """
    data = copy.deepcopy(job.manifest)
    # Every format.type ("wav", "adm-bwf", "binaural") currently delivers a
    # WAV container; this will need to key off output_type once non-PCM
    # containers/codecs (ogg/opus, flac) are added.
    extension = ".wav"
    track_snapshots = (job.project_snapshot or {}).get("tracks", {})
    assets = []
    # Read each source through the JobTrack's own asset FK rather than a
    # positional zip against import_batch.assets — a project export's tracks
    # may span more than one import once assets are added to a project
    # incrementally, so the two lists are no longer guaranteed to align.
    for track, input_path in zip(job.tracks, input_paths, strict=True):
        asset = track.asset
        output = work_dir / f"{track.position + 1:02d}-{Path(asset.filename).stem}{extension}"
        asset_data: dict[str, Any] = {
            "input": str(input_path),
            "output": str(output),
        }
        snapshot = track_snapshots.get(track.asset_id)
        if snapshot:
            asset_data["stem_input_dir"] = snapshot["stem_input_dir"]
            for block, value in snapshot.get("manifest_overrides", {}).items():
                if isinstance(value, dict) and value:
                    asset_data[block] = copy.deepcopy(value)
        else:
            asset_data["stem_cache_dir"] = str(stem_cache_dir)
        assets.append(asset_data)
    data["assets"] = assets
    if mastering_reference_path is not None:
        mastering = data.setdefault("mastering", {})
        match_reference = mastering.setdefault("match_reference", {})
        match_reference["path"] = str(mastering_reference_path)
    parse_manifest(data)
    return data
