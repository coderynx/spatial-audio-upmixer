"""Model registry: filename -> architecture, bundled config, and provenance.

This is the single place new checkpoints are added to enable free model
integration (the point of owning inference instead of depending on a
third-party wrapper's supported-model list). Each entry pins the exact
checkpoint by filename, a bundled YAML config in ``inference/configs/``, and
a best-known weights source. ``stem_plan.py`` remains the source of truth
for *which* stems map to which model; this module only resolves a model
filename to loadable weights.
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
        weights_url: Best-known upstream source for this checkpoint. Used
                     only as a fallback when the weights are not already
                     present in the local model cache; verify before relying
                     on it for a cold deployment, since community
                     checkpoints move between hosts more often than
                     published packages.
    """

    filename: str
    arch: Arch
    config_name: str
    weights_url: str


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "BS-Roformer-SW.ckpt": ModelSpec(
        filename="BS-Roformer-SW.ckpt",
        arch="bs_roformer",
        config_name="BS-Roformer-SW",
        weights_url="https://huggingface.co/jarredou/BS-ROFO-SW-Fixed",
    ),
    "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt": ModelSpec(
        filename="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144",
        weights_url="https://github.com/ZFTurbo/Music-Source-Separation-Training/releases",
    ),
    "MDX23C-DrumSep-aufr33-jarredou.ckpt": ModelSpec(
        filename="MDX23C-DrumSep-aufr33-jarredou.ckpt",
        arch="tfc_tdf_v3",
        config_name="MDX23C-DrumSep-aufr33-jarredou",
        weights_url="https://github.com/TRvlvr/model_repo/releases",
    ),
    "mel_band_roformer_karaoke_gabox_v2.ckpt": ModelSpec(
        filename="mel_band_roformer_karaoke_gabox_v2.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_karaoke_gabox_v2",
        weights_url="https://huggingface.co/GaboxR67/MelBandRoformers",
    ),
}


def get_model_spec(filename: str) -> ModelSpec:
    """Look up a checkpoint's registry entry.

    Raises:
        KeyError: Filename is not a registered model.
    """
    return MODEL_REGISTRY[filename]
