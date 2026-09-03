"""Project configuration realization: manifests, layouts, and scene routing."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from upmixer.codecs import DEFAULT_CODEC
from upmixer.config import UpmixConfig
from upmixer.manifest import apply_asset_job, parse_manifest
from upmixer.separation.stem_plan import normalize_stems
from upmixer_web.features.projects.layouts import (
    delivery_codec_for_layout,
    delivery_type_for_layout,
    normalize_layout_mix,
    seed_balanced_mix,
)
from upmixer_web.features.projects.routing import routing_for_scene
from upmixer_web.shared.manifests import normalize_job_manifest
from upmixer_web.shared.models import Project

_CHILD_STEMS = {
    "Vocals": ("Lead Vocals", "Backing Vocals"),
    "Drums": ("Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"),
}

_SEPARATION_ENGINE_KEYS = (
    "stem_batch_size", "stem_segment_size", "stem_chunk_duration_s",
    "stem_model_cache_size", "stem_silence_skip", "stem_silence_threshold_db",
    "stem_silence_min_duration_s", "stem_silence_crossfade_ms", "stem_silence_pad_ms",
    "stem_ensemble", "stem_bleed_reduction",
    "stem_drum_remask", "stem_primary_remask",
)

_TRACK_ENGINE_OVERRIDE_KEYS = {"stems", "stem_bleed_reduction"}


def separation_settings(manifest: dict[str, Any]) -> tuple[object, ...]:
    """Resolve absent separation settings to their configuration defaults."""
    engine = manifest.get("engine")
    if not isinstance(engine, dict):
        engine = {}
    defaults = UpmixConfig()
    return tuple(engine.get(key, getattr(defaults, key)) for key in _SEPARATION_ENGINE_KEYS)


def merge_manifest(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_manifest(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def normalize_project_stems(stems: Iterable[str]) -> list[str]:
    """Keep a parent stem only when none of its detailed stems is requested."""
    normalized = normalize_stems(list(stems))
    selected = set(normalized)
    return [
        stem for stem in normalized
        if not (stem in _CHILD_STEMS and any(child in selected for child in _CHILD_STEMS[stem]))
    ]


def normalize_project_manifest(
    manifest: dict[str, Any], *, seed_balanced: bool = True
) -> tuple[dict[str, Any], list[str]]:
    migrated = copy.deepcopy(manifest)
    mixing = migrated.setdefault("mixing", {})
    format_block = migrated.setdefault("format", {})
    if isinstance(mixing, dict) and isinstance(format_block, dict):
        layout = str(mixing.setdefault("channel_layout", "7.1.4"))
        format_block["type"] = delivery_type_for_layout(layout, str(format_block.get("type", "multichannel")))
        format_block["codec"] = delivery_codec_for_layout(
            layout, format_block["type"], str(format_block.get("codec", DEFAULT_CODEC)),
            str(format_block.get("subtype", UpmixConfig.output_subtype)),
        )
    normalized = normalize_job_manifest(migrated)
    engine = normalized.setdefault("engine", {})
    engine["mode"] = "stem"
    stems = normalize_project_stems(engine.get("stems") or [])
    engine["stems"] = stems
    engine.setdefault("stem_ensemble", UpmixConfig().stem_ensemble)
    mixing = normalized.setdefault("mixing", {})
    if isinstance(mixing.get("stem_solo"), str):
        mixing["stem_solo"] = [mixing["stem_solo"]]
    mixing["stem_source_anchor_strength"] = mixing.get("stem_source_anchor_strength", 0.0)
    format_block = normalized.setdefault("format", {})
    format_block.setdefault("type", "multichannel")
    format_block.setdefault("codec", DEFAULT_CODEC)
    format_block.setdefault("binaural", {}).setdefault("profile", "studio")
    format_block.setdefault("transaural", {}).setdefault("profile", "stereo")
    normalizer = seed_balanced_mix if seed_balanced else normalize_layout_mix
    normalizer(normalized, str(mixing.setdefault("channel_layout", "7.1.4")), stems)
    normalized.setdefault("routing", {})
    normalized.setdefault("processing", {})["preview"] = False
    return normalized, stems


def normalize_track_layout_block(
    project: Project, layout: str, overrides: dict[str, Any], *, seed_balanced: bool = False
) -> dict[str, Any]:
    block = copy.deepcopy(overrides)
    (seed_balanced_mix if seed_balanced else normalize_layout_mix)(
        block, layout, list(project.requested_stems)
    )
    allowed = {"engine", "mixing", "routing", "mastering", "format", "processing"}
    unknown = set(block) - allowed
    if unknown:
        raise ValueError(f"Unknown track override blocks: {', '.join(sorted(unknown))}")
    engine = block.get("engine", {})
    if engine and (not isinstance(engine, dict) or set(engine) - _TRACK_ENGINE_OVERRIDE_KEYS):
        raise ValueError("Track engine overrides may only set stems and bleed-reduction settings")
    if isinstance(engine, dict) and "stems" in engine:
        stems = normalize_project_stems(engine["stems"])
        if any(stem not in project.requested_stems for stem in stems):
            raise ValueError("Track stems must be prepared project stems")
    normalize_job_manifest(merge_manifest(project.manifest, block))
    return block


def seed_layout_block(project: Project, layout: str, source: dict[str, Any]) -> dict[str, Any]:
    seed = copy.deepcopy(source)
    mixing = seed.get("mixing")
    if isinstance(mixing, dict):
        mixing.pop("stem_routing", None)
    return normalize_track_layout_block(project, layout, seed, seed_balanced=True)


def resolve_scene_routing(
    manifest: dict[str, Any], overrides: dict[str, Any], requested_stems: list[str], scene: dict[str, Any]
) -> dict[str, dict[str, float]] | None:
    probe = merge_manifest(manifest, overrides)
    probe.setdefault("engine", {})["mode"] = "stem"
    probe["engine"]["stems"] = list(requested_stems)
    probe["assets"] = [{"input": "probe-in.wav", "output": "probe-out.wav"}]
    _, asset_jobs = parse_manifest(probe)
    asset_job = asset_jobs[0]
    config = UpmixConfig()
    apply_asset_job(config, asset_job)
    if config.stem_routing is not None:
        return None
    config.stems = asset_job.engine.get("stems") or list(requested_stems)
    return routing_for_scene(scene, config) or None


def preparation_manifest(
    block: dict[str, Any], stems: list[str], stem_bleed_reduction: bool, layout: str,
    stem_ensemble: bool | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(block)
    engine = updated.setdefault("engine", {})
    engine.update({"stems": stems, "stem_bleed_reduction": stem_bleed_reduction})
    if stem_ensemble is not None:
        engine["stem_ensemble"] = stem_ensemble
    return seed_balanced_mix(updated, layout, stems)
