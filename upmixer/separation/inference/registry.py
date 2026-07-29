"""Model registry: filename -> architecture, bundled config, and provenance.

This is the single place new checkpoints are added to enable free model
integration (the point of owning inference instead of depending on a
third-party wrapper's supported-model list). Each entry pins the exact
checkpoint by filename and sha256, a bundled YAML config in
``inference/configs/``, and the license status recorded in the external
knowledge base (``~/Projects/upmixer-knowledge/models/``) — non-NC only, per
repo policy. ``stem_plan.py`` remains the source of truth for *which* stems
map to which model; this module only resolves a model filename to loadable
weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Arch = Literal["bs_roformer", "mel_band_roformer", "tfc_tdf_v3"]


@dataclass(frozen=True)
class ModelSpec:
    """Everything needed to load one checkpoint.

    Attributes:
        filename:    Checkpoint filename, matched against the ``model_dir``
                     cache and used as the dict key in MODEL_REGISTRY.
        arch:        Which architecture class to instantiate.
        config_name: Bundled YAML config filename stem (no extension) under
                     ``inference/configs/``.
        weights_url: Best-known upstream source for this checkpoint, as
                     recorded in the knowledge base. Used only as a fallback
                     when the weights are not already present in the local
                     model cache; verify before relying on it for a cold
                     deployment, since community checkpoints move between
                     hosts more often than published packages.
        license:     License status from the knowledge base license policy
                     (OK / ATTRIB) — never BLOCKED (NC) or unresolved.
    """

    filename: str
    arch: Arch
    config_name: str
    weights_url: str
    license: str


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "BS-Roformer-SW.ckpt": ModelSpec(
        filename="BS-Roformer-SW.ckpt",
        arch="bs_roformer",
        config_name="BS-Roformer-SW",
        weights_url="https://huggingface.co/jarredou/BS-ROFO-SW-Fixed",
        license="OK",
    ),
    "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt": ModelSpec(
        filename="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144",
        weights_url="https://github.com/ZFTurbo/Music-Source-Separation-Training/releases",
        license="OK",
    ),
    "MDX23C-DrumSep-aufr33-jarredou.ckpt": ModelSpec(
        filename="MDX23C-DrumSep-aufr33-jarredou.ckpt",
        arch="tfc_tdf_v3",
        config_name="MDX23C-DrumSep-aufr33-jarredou",
        weights_url="https://github.com/TRvlvr/model_repo/releases",
        license="OK",
    ),
    "mel_band_roformer_karaoke_gabox_v2.ckpt": ModelSpec(
        filename="mel_band_roformer_karaoke_gabox_v2.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_karaoke_gabox_v2",
        weights_url="https://huggingface.co/GaboxR67/MelBandRoformers",
        license="OK",
    ),
}

_BLOCKED_LICENSES = frozenset({"BLOCKED", "VERIFY"})


def get_model_spec(filename: str) -> ModelSpec:
    """Look up a checkpoint's registry entry, asserting a non-NC license.

    Raises:
        KeyError: Filename is not a registered model.
        ValueError: Filename is registered but its license is BLOCKED (NC)
            or still unresolved (VERIFY) — refuses to load either.
    """
    spec = MODEL_REGISTRY[filename]
    if spec.license in _BLOCKED_LICENSES:
        raise ValueError(
            f"Model '{filename}' has license status '{spec.license}' — "
            "refusing to load. Only OK/ATTRIB-licensed weights may be used."
        )
    return spec
