"""Manifest structural and value validation."""
from __future__ import annotations

import math
import re

from upmixer.codecs import CODECS, WAV_SUBTYPES, validate_codec
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, ChannelLabel, validate_delivery
from upmixer.io.writer import DITHER_MODES
from upmixer.manifest.schema import _BLOCK_REGISTRY, BlockMapping, ManifestError, _leaf_type
from upmixer.separation.bleed_reduction import DEBLEED_MODELS, PHASE_FIX_REFERENCE_MODELS
from upmixer.separation.stem_plan import DEREVERB_MODELS, MANIFEST_TO_CANONICAL

_SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def _binaural_profile_choices() -> tuple[str, ...]:
    # Deferred import: upmixer.binaural imports upmixer.mastering.chain, which
    # imports this module at load time — a top-level import here would cycle.
    from upmixer.binaural.profiles import BINAURAL_PROFILES
    return BINAURAL_PROFILES


def _dyneq_profile_choices() -> tuple[str, ...]:
    # Deferred for the same reason as the two below: upmixer.mastering.dyneq
    # registers its manifest block at import time, which needs this module
    # already loaded.
    from upmixer.mastering.dyneq import DYNEQ_PROFILE_NAMES
    return DYNEQ_PROFILE_NAMES


def _smooth_octave_bounds() -> tuple[float, float]:
    # Deferred for the same reason as the profile-choice helpers around it.
    from upmixer.mastering.match_reference.curve import SMOOTH_OCT_MAX, SMOOTH_OCT_MIN
    return SMOOTH_OCT_MIN, SMOOTH_OCT_MAX


def _transaural_profile_choices() -> tuple[str, ...]:
    # Deferred import: upmixer.crosstalk imports upmixer.binaural.renderer,
    # which imports upmixer.mastering.chain, which imports this module at
    # load time — a top-level import here would cycle, same as binaural's.
    from upmixer.crosstalk.profiles import CROSSTALK_PROFILES
    return CROSSTALK_PROFILES


#: Bounds every dynamic-EQ band field is held to, as ``(minimum, maximum)``.
_DYNEQ_BOUNDS: dict[str, tuple[float, float]] = {
    "freq_hz": (20.0, 20000.0),
    "q": (0.3, 12.0),
    "threshold_db": (-80.0, 0.0),
    "ratio": (1.0, 20.0),
    "attack_ms": (0.1, 200.0),
    "release_ms": (1.0, 2000.0),
}


def _validate_dyneq_bands(blocks: dict, location: str) -> None:
    """Check ``mastering.dynamic_eq.bands`` — a list of fully specified bands.

    The generic block walker only knows the leaf is a list; the band dicts
    inside it are a trust boundary of their own.
    """
    # Deferred import: upmixer.mastering.dyneq registers its manifest block at
    # import time, which needs this module already loaded.
    from upmixer.mastering.dyneq import BAND_FIELDS, MAX_BANDS

    mastering = blocks.get("mastering")
    if not isinstance(mastering, dict):
        return
    dynamic_eq = mastering.get("dynamic_eq")
    if not isinstance(dynamic_eq, dict):
        return
    bands = dynamic_eq.get("bands")
    if bands is None:
        return
    prefix = f"{location}.mastering.dynamic_eq.bands"
    if not isinstance(bands, list):
        raise ManifestError(f"{prefix} must be a list of bands.")
    if len(bands) > MAX_BANDS:
        raise ManifestError(f"{prefix} takes at most {MAX_BANDS} bands.")
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            raise ManifestError(f"{prefix}[{index}] must be a mapping.")
        for key in band:
            if key not in BAND_FIELDS:
                raise ManifestError(f"Unknown manifest field '{prefix}[{index}].{key}'.")
        for field in BAND_FIELDS:
            value = band.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ManifestError(f"{prefix}[{index}].{field} must be a number.")
            low, high = _DYNEQ_BOUNDS[field]
            if not math.isfinite(float(value)) or not low <= float(value) <= high:
                raise ManifestError(
                    f"{prefix}[{index}].{field} must be between {low} and {high}."
                )


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
            "mixing.stem_source_anchor_strength": 0.0,
            "engine.stem_batch_size": 1.0,
            "engine.stem_segment_size": 1.0,
            "engine.stem_chunk_duration_s": 0.0,
            "engine.stem_model_cache_size": 1.0,
            "engine.stem_silence_min_duration_s": 0.0,
            "engine.stem_silence_crossfade_ms": 0.0,
            "engine.stem_silence_pad_ms": 0.0,
            "engine.stem_phase_fix_low_hz": 1.0,
            "engine.stem_phase_fix_high_hz": 1.0,
            "engine.stem_phase_fix_scale": 0.0,
            "processing.preview_duration": 0.0,
            "processing.preview_start": 0.0,
            "format.downmix.height_coeff": 0.0,
            "routing.lfe_cutoff": 0.0,
            "mastering.eq.strength": 0.0,
            "mastering.highpass.cutoff_hz": 10.0,
            "mastering.clip.clip_db": 0.0,
            "mastering.clip.knee": 0.0,
            "mastering.match_reference.strength": 0.0,
            "mastering.match_reference.max_db": 0.0,
            "mastering.match_reference.smooth_octaves": _smooth_octave_bounds()[0],
            "mastering.match_reference.low_hz": 20.0,
            "mastering.match_reference.high_hz": 20.0,
            "mastering.compressor.ratio": 1.0,
            "mastering.compressor.attack_ms": 0.0,
            "mastering.compressor.release_ms": 0.0,
            "mastering.compressor.knee_db": 0.0,
        }
        maximums = {
            "mixing.stem_source_anchor_strength": 1.0,
            "mastering.eq.strength": 1.0,
            "mastering.highpass.cutoff_hz": 30.0,
            "mastering.clip.clip_db": 6.0,
            "mastering.clip.knee": 1.0,
            "mastering.match_reference.strength": 1.0,
            "mastering.match_reference.smooth_octaves": _smooth_octave_bounds()[1],
            "mastering.match_reference.low_hz": 20000.0,
            "mastering.match_reference.high_hz": 20000.0,
            "engine.stem_phase_fix_scale": 1.0,
            "format.downmix.height_coeff": 1.0,
        }
        if path in minimums and float(value) < minimums[path]:
            raise ManifestError(f"{path} must be at least {minimums[path]}.")
        if path in maximums and float(value) > maximums[path]:
            raise ManifestError(f"{path} must be at most {maximums[path]}.")
    choices = {
        "engine.mode": {"stem"},
        "mixing.channel_layout": set(FORMAT_MAP),
        "format.type": {"multichannel", "adm-bwf", "binaural", "transaural"},
        "format.codec": set(CODECS),
        "format.subtype": set(WAV_SUBTYPES),
        "format.dither": set(DITHER_MODES),
        "format.downmix.surround_coeff": {0.7071, 0.5, 0.0},
        "mastering.dynamic_eq.profile": set(_dyneq_profile_choices()),
        "format.binaural.profile": set(_binaural_profile_choices()),
        "format.transaural.profile": set(_transaural_profile_choices()),
        "engine.stem_phase_fix_reference_model": set(PHASE_FIX_REFERENCE_MODELS),
        "engine.stem_debleed_model": set(DEBLEED_MODELS),
        "engine.stem_dereverb_model": set(DEREVERB_MODELS),
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


_ASSET_NON_BLOCK_KEYS: frozenset[str] = frozenset({
    "input", "output", "stem_cache_dir", "stem_cache_key",
    "stem_output_dir", "stem_input_dir",
    "input_dir", "output_dir", "glob",
})


_PLACEMENT_FIELDS = frozenset({"azimuth_deg", "elevation_deg", "width_deg", "spread_deg"})
_PLACEMENT_NON_NEGATIVE = frozenset({"width_deg", "spread_deg"})


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

    root_mixing = data.get("mixing") or {}
    root_format = data.get("format") or {}
    defaults = UpmixConfig()
    for scope in (data, *assets):
        mixing = {**root_mixing, **(scope.get("mixing") or {})}
        fmt = {**root_format, **(scope.get("format") or {})}
        layout = str(mixing.get("channel_layout", defaults.output_format))
        output_type = str(fmt.get("type", defaults.output_type))
        sample_rate = fmt.get("sample_rate")
        try:
            validate_delivery(layout, output_type)
            validate_codec(
                layout,
                output_type,
                str(fmt.get("codec", defaults.output_codec)),
                str(fmt.get("subtype", defaults.output_subtype)),
                int(sample_rate) if sample_rate is not None else None,
            )
        except ValueError as exc:
            raise ManifestError(str(exc)) from exc

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
        placement = mixing.get("stem_placement")
        if placement is not None:
            if not isinstance(placement, dict):
                raise ManifestError(f"{location}.mixing.stem_placement must be a mapping.")
            for stem_key, fields in placement.items():
                if not _valid_route_stem(stem_key):
                    raise ManifestError(f"Unknown stem routing key '{stem_key}'.")
                if not isinstance(fields, dict):
                    raise ManifestError(
                        f"{location}.mixing.stem_placement.{stem_key} must be a mapping."
                    )
                for field, value in fields.items():
                    if field not in _PLACEMENT_FIELDS:
                        raise ManifestError(
                            f"Unknown placement field '{field}' for stem '{stem_key}'."
                        )
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ManifestError(
                            f"Placement '{stem_key}.{field}' must be a number."
                        )
                    if not math.isfinite(float(value)):
                        raise ManifestError(
                            f"Placement '{stem_key}.{field}' must be finite."
                        )
                    if field in _PLACEMENT_NON_NEGATIVE and float(value) < 0.0:
                        raise ManifestError(
                            f"Placement '{stem_key}.{field}' must be non-negative."
                        )
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
    _validate_dyneq_bands(data, "manifest")
    for index, asset in enumerate(assets):
        _validate_stem_mix(asset, f"assets[{index}]")
        _validate_dyneq_bands(asset, f"assets[{index}]")
