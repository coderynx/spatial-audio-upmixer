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
        default_chunk_samples: Community-published sweet-spot chunk length
                     in samples, or ``None`` to use the model's own YAML
                     ``dim_t`` default. Only applied when the caller leaves
                     ``segment_size`` unset (see ``docs/`` model catalogs in
                     ``upmixer-knowledge`` for provenance).
    """

    filename: str
    arch: Arch
    config_name: str
    weights_url: str
    default_chunk_samples: int | None = None


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "BS-Roformer-SW.ckpt": ModelSpec(
        filename="BS-Roformer-SW.ckpt",
        arch="bs_roformer",
        config_name="BS-Roformer-SW",
        weights_url="https://huggingface.co/jarredou/BS-ROFO-SW-Fixed",
        default_chunk_samples=882000,
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
    "BS-Roformer-Resurrection-Inst.ckpt": ModelSpec(
        filename="BS-Roformer-Resurrection-Inst.ckpt",
        arch="bs_roformer",
        config_name="BS-Roformer-Resurrection-Inst",
        weights_url="https://huggingface.co/pcunwa/BS-Roformer-Resurrection",
    ),
    "bs_roformer_inst_hyperacev2.ckpt": ModelSpec(
        filename="bs_roformer_inst_hyperacev2.ckpt",
        arch="bs_roformer",
        config_name="bs_roformer_inst_hyperacev2",
        weights_url="https://huggingface.co/pcunwa/BS-Roformer-HyperACE",
    ),
    "becruily_deux.ckpt": ModelSpec(
        filename="becruily_deux.ckpt",
        arch="mel_band_roformer",
        config_name="becruily_deux",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-deux",
        # Community sweet spot for instrumental targets is 661500-749700
        # samples; 705600 (16s @ 44.1kHz) lands mid-range at an exact dim_t.
        default_chunk_samples=705600,
    ),
    "kimmel_unwa_ft2_bleedless.ckpt": ModelSpec(
        filename="kimmel_unwa_ft2_bleedless.ckpt",
        arch="mel_band_roformer",
        config_name="kimmel_unwa_ft2_bleedless",
        weights_url="https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT",
    ),
    "mel_band_roformer_vocals_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_vocals_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_vocals_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-vocals",
    ),
    "mel_band_roformer_instrumental_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_instrumental_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_instrumental_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-instrumental",
    ),
    "mel_band_roformer_karaoke_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_karaoke_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_karaoke_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-karaoke",
    ),
    "mel_band_roformer_bleed_suppressor_v1.ckpt": ModelSpec(
        filename="mel_band_roformer_bleed_suppressor_v1.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_bleed_suppressor_v1",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/tag/model-configs",
    ),
    "mel_band_roformer_denoise_debleed_gabox.ckpt": ModelSpec(
        filename="mel_band_roformer_denoise_debleed_gabox.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_denoise_debleed_gabox",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/tag/model-configs",
    ),
    "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt": ModelSpec(
        filename="denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
        arch="mel_band_roformer",
        config_name="denoise_mel_band_roformer_aufr33_sdr_27.9959",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/tag/model-configs",
    ),
}


def get_model_spec(filename: str) -> ModelSpec:
    """Look up a checkpoint's registry entry.

    Raises:
        KeyError: Filename is not a registered model.
    """
    return MODEL_REGISTRY[filename]
