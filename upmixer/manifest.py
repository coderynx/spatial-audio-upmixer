"""YAML/JSON manifest files for defining upmix jobs.

A manifest file lets all CLI parameters live in a single file, making
it easy to version-control complex upmix jobs and run them reproducibly.

Supported formats
-----------------
* YAML  (``.yaml``, ``.yml``) — requires ``pyyaml``: ``pip install pyyaml``
* JSON  (``.json``) — no extra dependency

Key naming
----------
Manifest keys use the same names as the CLI flags (without the leading
``--``), with hyphens replaced by underscores.  For example:

* ``--output-sample-rate 48000``  →  ``output_sample_rate: 48000``
* ``--loudness-target -18.0``     →  ``loudness_target: -18.0``

Priority order
--------------
CLI flags > manifest values > UpmixConfig defaults.

Example (YAML)::

    input:   stereo.flac
    output:  atmos.adm.bwf
    format:  7.1.2
    mode:    stem

    stem_model: BS-Roformer-SW.ckpt

    loudness_target: -18.0
    preview: true
    preview_duration: 30.0

Job keys (``input``, ``output``, ``mode``, ``input_format``,
``stem_model``, ``stem_model_dir``) are returned separately from
:func:`parse_manifest` so the pipeline layer can use them directly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from upmixer.config import UpmixConfig

_log = logging.getLogger("upmixer")

# ── Field mapping ──────────────────────────────────────────────────────────────
# manifest key → (UpmixConfig attribute name, Python type for coercion)
# Only non-None values in the manifest are applied; null / omitted keys are
# treated as "not specified" and leave the config default intact.

_FIELD_MAP: dict[str, tuple[str, type]] = {
    # Output format
    "format":                     ("output_format",          str),
    "output_type":                ("output_type",            str),
    "output_subtype":             ("output_subtype",         str),
    "output_sample_rate":         ("output_sample_rate",     int),
    # Channel routing gains
    "center_gain":                ("center_gain",            float),
    "surround_gain":              ("surround_gain",          float),
    "back_gain":                  ("back_gain",              float),
    "height_gain":                ("height_gain",            float),
    "lfe_gain":                   ("lfe_gain",               float),
    # LFE
    "lfe_cutoff":                 ("lfe_cutoff_hz",          float),
    # Center extraction (realtime mode)
    "center_extraction_gain":     ("center_extraction_gain", float),
    "center_attenuation":         ("center_attenuation",     float),
    # Content-aware mixing
    "content_mix_strength":       ("content_mix_strength",   float),
    # Height EQ
    "height_low_rolloff_gain":    ("height_low_rolloff_gain",float),
    "height_high_shelf_gain":     ("height_high_shelf_gain", float),
    # STFT / processing
    "fft_size":                   ("fft_size",               int),
    "block_size":                 ("block_size",             int),
    # Energy normalization (mixing phase)
    "normalize_output":           ("normalize_output",       bool),
    # Mastering — loudness
    "loudness_normalize":         ("loudness_normalize",     bool),
    "loudness_target":            ("loudness_target_lkfs",   float),
    "loudness_max_tp":            ("loudness_max_tp",        float),
    # Mastering — EQ shaping (flat keys; also accessible via mastering: section)
    "mastering_eq_profile":       ("mastering_eq_profile",   str),
    "mastering_eq_strength":      ("mastering_eq_strength",  float),
    # Mastering — bus compressor
    "mastering_comp_profile":     ("mastering_comp_profile",      str),
    "mastering_comp_threshold_db":("mastering_comp_threshold_db", float),
    "mastering_comp_ratio":       ("mastering_comp_ratio",        float),
    "mastering_comp_attack_ms":   ("mastering_comp_attack_ms",    float),
    "mastering_comp_release_ms":  ("mastering_comp_release_ms",   float),
    "mastering_comp_knee_db":     ("mastering_comp_knee_db",      float),
    "mastering_comp_makeup_db":   ("mastering_comp_makeup_db",    float),
    # Mastering — bass control
    "mastering_bass_profile":        ("mastering_bass_profile",        str),
    "mastering_bass_sub_gain_db":    ("mastering_bass_sub_gain_db",    float),
    "mastering_bass_mid_gain_db":    ("mastering_bass_mid_gain_db",    float),
    "mastering_bass_mono_cutoff_hz": ("mastering_bass_mono_cutoff_hz", float),
    "mastering_bass_excite":         ("mastering_bass_excite",         bool),
    "mastering_bass_lfe_gain_db":    ("mastering_bass_lfe_gain_db",    float),
    # Mastering — spectral + RMS reference matching
    "mastering_match_ref_path":     ("mastering_match_ref_path",     str),
    "mastering_match_ref_strength": ("mastering_match_ref_strength",  float),
    "mastering_match_ref_spectrum": ("mastering_match_ref_spectrum",  bool),
    "mastering_match_ref_rms":      ("mastering_match_ref_rms",       bool),
    "mastering_match_ref_max_db":   ("mastering_match_ref_max_db",    float),
    # Mixing — stem rebalance (stem pipeline only)
    "stem_rebalance":                ("stem_rebalance",                dict),
    # Mixing — per-stem EQ (stem pipeline only)
    "stem_eq_profiles":              ("stem_eq_profiles",              dict),
    # Mixing — stem cache
    "stem_cache_dir":                ("stem_cache_dir",                str),
    # Downmix
    "downmix_output":             ("downmix_output_path",    str),
    "downmix_surround_coeff":     ("surround_downmix_coeff", float),
    # Preview
    "preview":                    ("preview",                bool),
    "preview_duration":           ("preview_duration_s",     float),
    "preview_start":              ("preview_start_s",        float),
}

# ── Nested mastering: section ─────────────────────────────────────────────────
# Maps sub-keys inside a ``mastering:`` YAML block to the flat manifest keys
# that feed into _FIELD_MAP above.  This lets users write either:
#
#   mastering_eq_profile: spatial-air        # flat form
#
# or the structured form:
#
#   mastering:
#     eq_profile: spatial-air
#     loudness_normalize: true
#
# Both forms produce identical UpmixConfig state.

_MASTERING_KEY_MAP: dict[str, str] = {
    # EQ
    "eq_profile":          "mastering_eq_profile",
    "eq_strength":         "mastering_eq_strength",
    # Compressor
    "comp_profile":     "mastering_comp_profile",
    "comp_threshold":   "mastering_comp_threshold_db",
    "comp_ratio":       "mastering_comp_ratio",
    "comp_attack":      "mastering_comp_attack_ms",
    "comp_release":     "mastering_comp_release_ms",
    "comp_knee":        "mastering_comp_knee_db",
    "comp_makeup":      "mastering_comp_makeup_db",
    # Bass control
    "bass_profile":     "mastering_bass_profile",
    "bass_sub_gain":    "mastering_bass_sub_gain_db",
    "bass_mid_gain":    "mastering_bass_mid_gain_db",
    "bass_mono_cutoff": "mastering_bass_mono_cutoff_hz",
    "bass_excite":      "mastering_bass_excite",
    "bass_lfe_gain":    "mastering_bass_lfe_gain_db",
    # Loudness (re-uses existing flat keys)
    "loudness_normalize": "loudness_normalize",
    "loudness_target":    "loudness_target",
    "loudness_max_tp":    "loudness_max_tp",
}

# ── Two-level mastering sub-section maps ──────────────────────────────────────
# When a mastering: sub-key maps to a dict (e.g. mastering: {eq: {profile: x}}),
# the inner dict is expanded using these maps.  This allows fully nested YAML:
#
#   mastering:
#     eq:
#       profile: atmos-streaming
#       strength: 0.8
#       match_strength: 0.4
#     compressor:
#       profile: glue
#     bass:
#       profile: enhance
#     loudness:
#       normalize: true
#       target: -18.0

_MASTERING_EQ_SUBMAP: dict[str, str] = {
    "profile":  "mastering_eq_profile",
    "strength": "mastering_eq_strength",
}

_MASTERING_COMP_SUBMAP: dict[str, str] = {
    "profile":   "mastering_comp_profile",
    "threshold": "mastering_comp_threshold_db",
    "ratio":     "mastering_comp_ratio",
    "attack":    "mastering_comp_attack_ms",
    "release":   "mastering_comp_release_ms",
    "knee":      "mastering_comp_knee_db",
    "makeup":    "mastering_comp_makeup_db",
}

_MASTERING_BASS_SUBMAP: dict[str, str] = {
    "profile":     "mastering_bass_profile",
    "sub_gain":    "mastering_bass_sub_gain_db",
    "mid_gain":    "mastering_bass_mid_gain_db",
    "mono_cutoff": "mastering_bass_mono_cutoff_hz",
    "excite":      "mastering_bass_excite",
    "lfe_gain":    "mastering_bass_lfe_gain_db",
}

_MASTERING_LOUDNESS_SUBMAP: dict[str, str] = {
    "normalize": "loudness_normalize",
    "target":    "loudness_target",
    "max_tp":    "loudness_max_tp",
}

_MASTERING_MATCH_REF_SUBMAP: dict[str, str] = {
    "reference":        "mastering_match_ref_path",
    "strength":         "mastering_match_ref_strength",
    "match_spectrum":   "mastering_match_ref_spectrum",
    "match_rms":        "mastering_match_ref_rms",
    "max_correction_db":"mastering_match_ref_max_db",
}

_MASTERING_SUBSECTIONS: dict[str, dict[str, str]] = {
    "eq":              _MASTERING_EQ_SUBMAP,
    "compressor":      _MASTERING_COMP_SUBMAP,
    "bass":            _MASTERING_BASS_SUBMAP,
    "loudness":        _MASTERING_LOUDNESS_SUBMAP,
    "match_reference": _MASTERING_MATCH_REF_SUBMAP,
}

# ── Routing section ───────────────────────────────────────────────────────────
# routing:
#   center_gain: 0.85
#   surround_gain: 0.6
#   lfe_cutoff: 120

_ROUTING_KEY_MAP: dict[str, str] = {
    "center_gain":            "center_gain",
    "surround_gain":          "surround_gain",
    "back_gain":              "back_gain",
    "height_gain":            "height_gain",
    "lfe_gain":               "lfe_gain",
    "lfe_cutoff":             "lfe_cutoff",
    "center_extraction_gain": "center_extraction_gain",
    "center_attenuation":     "center_attenuation",
    "height_low_rolloff_gain":"height_low_rolloff_gain",
    "height_high_shelf_gain": "height_high_shelf_gain",
    "content_mix_strength":   "content_mix_strength",
}

# ── Output section ────────────────────────────────────────────────────────────
# output:
#   type: adm-bwf
#   subtype: PCM_24
#   sample_rate: 48000
#   downmix: stereo_check.wav

_OUTPUT_KEY_MAP: dict[str, str] = {
    "type":             "output_type",
    "subtype":          "output_subtype",
    "sample_rate":      "output_sample_rate",
    "downmix":          "downmix_output",
    "downmix_surround": "downmix_surround_coeff",
}

# ── Processing section ────────────────────────────────────────────────────────
# processing:
#   normalize: true
#   fft_size: 4096
#   preview: true
#   preview_duration: 30.0

_PROCESSING_KEY_MAP: dict[str, str] = {
    "normalize":       "normalize_output",
    "fft_size":        "fft_size",
    "block_size":      "block_size",
    "preview":         "preview",
    "preview_duration":"preview_duration",
    "preview_start":   "preview_start",
}

# ── Nested mixing: section ────────────────────────────────────────────────────
# Mirrors mastering: section but for mixing-phase params.
# Usage (YAML):
#
#   mixing:
#     stem_rebalance:
#       Vocals: +2.0
#       Drums: -1.0
#     stem_eq:
#       Vocals: vocal-presence
#       Bass: bass-warmth

_MIXING_KEY_MAP: dict[str, str] = {
    "stem_rebalance": "stem_rebalance",    # dict value passes through as-is
    "stem_eq":        "stem_eq_profiles",  # renamed for config
    "stem_cache_dir": "stem_cache_dir",    # stem separation cache directory
}

# ── Batch section ─────────────────────────────────────────────────────────────
# batch:
#   input_dir: /albums/ok-computer/           # scan directory
#   inputs:                                   # explicit files (any dirs)
#     - /dir1/track1.wav
#     - /dir2/track2.flac
#   output_dir: /output/                      # shared output dir
#   workers: 1                                # realtime parallel workers
#   jobs:                                     # fully explicit pairs
#     - {input: /dir1/a.wav, output: /out/a.wav}
#     - {input: /dir2/b.flac}                 # output derived from output_dir

_BATCH_KEY_MAP: dict[str, str] = {
    "input_dir":  "batch_dir",
    "inputs":     "batch_inputs",
    "output_dir": "batch_output_dir",
    "workers":    "batch_workers",
    "jobs":       "batch_jobs",
}


def _expand_nested_sections(data: dict) -> dict:
    """Expand nested YAML sections into flat manifest keys.

    Supported sections:

    ``mastering:``
        One-level form (backward compatible)::

            mastering:
              eq_profile: atmos-streaming
              comp_profile: glue

        Two-level form (structured)::

            mastering:
              eq:
                profile: atmos-streaming
                strength: 0.8
              eq_match:
                reference: reference.wav
                strength: 0.5
              compressor:
                profile: glue
              bass:
                profile: enhance
              loudness:
                normalize: true

        Sub-keys that are dicts are dispatched via :data:`_MASTERING_SUBSECTIONS`;
        flat sub-keys are handled via :data:`_MASTERING_KEY_MAP`.  Both forms
        may be mixed freely.

    ``mixing:``
        ::

            mixing:
              stem_rebalance:
                Vocals: +2.0
              stem_eq:
                Bass: bass-warmth
              stem_cache_dir: /tmp/stems

    ``routing:``
        ::

            routing:
              center_gain: 0.85
              surround_gain: 0.6
              lfe_cutoff: 120

    ``output:``
        ::

            output:
              type: adm-bwf
              subtype: PCM_24
              sample_rate: 48000

    ``processing:``
        ::

            processing:
              preview: true
              preview_duration: 30.0

    The original section keys are removed from the returned dict.  Existing
    flat keys take priority — nested values do **not** overwrite them.

    Args:
        data: Original manifest dict.

    Returns:
        Expanded flat dict.  A copy is made when expansion occurs; the
        original dict is returned unchanged when no known sections are present.
    """
    _SECTION_KEYS = {"mastering", "mixing", "routing", "output", "processing", "batch"}
    present = {k for k in _SECTION_KEYS if k in data and isinstance(data.get(k), dict)}

    if not present:
        return data

    expanded = {k: v for k, v in data.items() if k not in present}

    # ── mastering: ────────────────────────────────────────────────────────────
    if "mastering" in present:
        for sub_key, value in data["mastering"].items():
            if isinstance(value, dict) and sub_key in _MASTERING_SUBSECTIONS:
                # Two-level: mastering.eq.profile, mastering.bass.sub_gain, etc.
                submap = _MASTERING_SUBSECTIONS[sub_key]
                for inner_key, inner_val in value.items():
                    flat_key = submap.get(inner_key, f"mastering_{sub_key}_{inner_key}")
                    if flat_key not in expanded:
                        expanded[flat_key] = inner_val
            else:
                # One-level: mastering.eq_profile, mastering.comp_profile, etc.
                flat_key = _MASTERING_KEY_MAP.get(sub_key, f"mastering_{sub_key}")
                if flat_key not in expanded:
                    expanded[flat_key] = value

    # ── mixing: ───────────────────────────────────────────────────────────────
    if "mixing" in present:
        for sub_key, value in data["mixing"].items():
            flat_key = _MIXING_KEY_MAP.get(sub_key, sub_key)
            if flat_key not in expanded:
                expanded[flat_key] = value

    # ── routing: ──────────────────────────────────────────────────────────────
    if "routing" in present:
        for sub_key, value in data["routing"].items():
            flat_key = _ROUTING_KEY_MAP.get(sub_key, sub_key)
            if flat_key not in expanded:
                expanded[flat_key] = value

    # ── output: ───────────────────────────────────────────────────────────────
    if "output" in present:
        for sub_key, value in data["output"].items():
            flat_key = _OUTPUT_KEY_MAP.get(sub_key, sub_key)
            if flat_key not in expanded:
                expanded[flat_key] = value

    # ── processing: ───────────────────────────────────────────────────────────
    if "processing" in present:
        for sub_key, value in data["processing"].items():
            flat_key = _PROCESSING_KEY_MAP.get(sub_key, sub_key)
            if flat_key not in expanded:
                expanded[flat_key] = value

    # ── batch: ────────────────────────────────────────────────────────────────
    if "batch" in present:
        for sub_key, value in data["batch"].items():
            flat_key = _BATCH_KEY_MAP.get(sub_key, sub_key)
            if flat_key not in expanded:
                expanded[flat_key] = value

    return expanded

# Keys handled at the pipeline / CLI level, not mapped into UpmixConfig.
_JOB_KEYS: frozenset[str] = frozenset({
    "input",
    "output",
    "mode",
    "input_format",
    "stem_model",
    "stem_model_dir",
    # Batch processing (from batch: section or flat keys)
    "batch_dir",
    "batch_inputs",
    "batch_output_dir",
    "batch_workers",
    "batch_jobs",
})


# ── Loader ─────────────────────────────────────────────────────────────────────

def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON manifest file and return it as a plain dict.

    Args:
        path: Path to a ``.yaml``, ``.yml``, or ``.json`` file.

    Returns:
        Dict of manifest key/value pairs.  The dict is always non-None; an
        empty manifest file returns ``{}``.

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


# ── Application ────────────────────────────────────────────────────────────────

def apply_manifest(
    config: UpmixConfig,
    manifest: dict[str, Any],
    *,
    allow_unknown_keys: bool = False,
) -> dict[str, Any]:
    """Apply manifest values to a :class:`~upmixer.config.UpmixConfig`.

    CLI flag values are NOT applied here — the caller applies them afterwards
    so they win over the manifest.

    Args:
        config:            The config object to modify *in-place*.
        manifest:          Dict loaded by :func:`load_manifest`.
        allow_unknown_keys: If ``False`` (default), logs a warning for keys
                           that are neither known config fields nor job keys.

    Returns:
        ``job_params`` dict containing job-level keys:
        ``input``, ``output``, ``mode``, ``input_format``,
        ``stem_model``, ``stem_model_dir``.  Keys absent from the manifest
        are not present in the returned dict (caller uses ``.get()``).
    """
    # ── Expand nested mastering: section into flat keys ───────────────────────
    manifest = _expand_nested_sections(manifest)

    # ── Apply config fields ───────────────────────────────────────────────────
    for key, value in manifest.items():
        if value is None:
            continue  # null / omitted → keep config default
        if key in _JOB_KEYS:
            continue  # handled below / by caller

        if key not in _FIELD_MAP:
            if not allow_unknown_keys:
                _log.warning("Unknown manifest key '%s' — ignored", key)
            continue

        config_attr, coerce = _FIELD_MAP[key]
        try:
            coerced = coerce(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Manifest key '{key}': cannot convert {value!r} to {coerce.__name__}: {exc}"
            ) from exc
        setattr(config, config_attr, coerced)

    # ── Collect job-level params ──────────────────────────────────────────────
    job_params: dict[str, Any] = {}
    for key in _JOB_KEYS:
        if key in manifest and manifest[key] is not None:
            job_params[key] = manifest[key]

    return job_params


def list_manifest_keys() -> dict[str, str]:
    """Return a human-readable mapping of manifest keys to config attributes.

    Useful for documentation and ``--manifest-help`` style output.
    """
    out: dict[str, str] = {}
    for mk, (ca, t) in sorted(_FIELD_MAP.items()):
        out[mk] = f"{ca}  ({t.__name__})"
    for jk in sorted(_JOB_KEYS):
        out[jk] = "job parameter"
    return out
