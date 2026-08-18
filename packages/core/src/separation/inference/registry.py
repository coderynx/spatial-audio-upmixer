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
        # Original jarredou/BS-ROFO-SW-Fixed account was deleted; mirrored
        # under enerjazzer with the checkpoint renamed BS-Rofo-SW-Fixed.ckpt.
        weights_url="https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed/resolve/main/BS-Rofo-SW-Fixed.ckpt",
        default_chunk_samples=882000,
    ),
    "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt": ModelSpec(
        filename="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144",
        weights_url="https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v.1.0.4/mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt",
    ),
    "MDX23C-DrumSep-aufr33-jarredou.ckpt": ModelSpec(
        filename="MDX23C-DrumSep-aufr33-jarredou.ckpt",
        arch="tfc_tdf_v3",
        config_name="MDX23C-DrumSep-aufr33-jarredou",
        weights_url="https://huggingface.co/Politrees/UVR_resources/resolve/main/models/MDX23C/MDX23C-DrumSep-aufr33-jarredou.ckpt",
    ),
    "mel_band_roformer_karaoke_gabox_v2.ckpt": ModelSpec(
        filename="mel_band_roformer_karaoke_gabox_v2.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_karaoke_gabox_v2",
        # Upstream file is Karaoke_GaboxV2.ckpt under melbandroformers/experimental/.
        weights_url="https://huggingface.co/GaboxR67/MelBandRoformers/resolve/main/melbandroformers/experimental/Karaoke_GaboxV2.ckpt",
    ),
    "BS-Roformer-Resurrection-Inst.ckpt": ModelSpec(
        filename="BS-Roformer-Resurrection-Inst.ckpt",
        arch="bs_roformer",
        config_name="BS-Roformer-Resurrection-Inst",
        weights_url="https://huggingface.co/pcunwa/BS-Roformer-Resurrection/resolve/main/BS-Roformer-Resurrection-Inst.ckpt",
    ),
    "bs_roformer_inst_hyperacev2.ckpt": ModelSpec(
        filename="bs_roformer_inst_hyperacev2.ckpt",
        arch="bs_roformer",
        config_name="bs_roformer_inst_hyperacev2",
        weights_url="https://huggingface.co/pcunwa/BS-Roformer-HyperACE/resolve/main/v2_inst/bs_roformer_inst_hyperacev2.ckpt",
    ),
    "becruily_deux.ckpt": ModelSpec(
        filename="becruily_deux.ckpt",
        arch="mel_band_roformer",
        config_name="becruily_deux",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-deux/resolve/main/becruily_deux.ckpt",
        # Community sweet spot for instrumental targets is 661500-749700
        # samples; 705600 (16s @ 44.1kHz) lands mid-range at an exact dim_t.
        default_chunk_samples=705600,
    ),
    "kimmel_unwa_ft2_bleedless.ckpt": ModelSpec(
        filename="kimmel_unwa_ft2_bleedless.ckpt",
        arch="mel_band_roformer",
        config_name="kimmel_unwa_ft2_bleedless",
        weights_url="https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT/resolve/main/kimmel_unwa_ft2_bleedless.ckpt",
    ),
    "mel_band_roformer_vocals_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_vocals_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_vocals_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-vocals/resolve/main/mel_band_roformer_vocals_becruily.ckpt",
    ),
    "mel_band_roformer_instrumental_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_instrumental_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_instrumental_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-instrumental/resolve/main/mel_band_roformer_instrumental_becruily.ckpt",
    ),
    "mel_band_roformer_karaoke_becruily.ckpt": ModelSpec(
        filename="mel_band_roformer_karaoke_becruily.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_karaoke_becruily",
        weights_url="https://huggingface.co/becruily/mel-band-roformer-karaoke/resolve/main/mel_band_roformer_karaoke_becruily.ckpt",
    ),
    "mel_band_roformer_bleed_suppressor_v1.ckpt": ModelSpec(
        filename="mel_band_roformer_bleed_suppressor_v1.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_bleed_suppressor_v1",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/mel_band_roformer_bleed_suppressor_v1.ckpt",
    ),
    "mel_band_roformer_denoise_debleed_gabox.ckpt": ModelSpec(
        filename="mel_band_roformer_denoise_debleed_gabox.ckpt",
        arch="mel_band_roformer",
        config_name="mel_band_roformer_denoise_debleed_gabox",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/mel_band_roformer_denoise_debleed_gabox.ckpt",
    ),
    "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt": ModelSpec(
        filename="denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
        arch="mel_band_roformer",
        config_name="denoise_mel_band_roformer_aufr33_sdr_27.9959",
        weights_url="https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
    ),
    # GPL weights: runtime download only, never bundled. The whole anvuew
    # dereverb family shares one upstream YAML.
    "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt": ModelSpec(
        filename="dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
        arch="mel_band_roformer",
        config_name="dereverb_mel_band_roformer_anvuew",
        weights_url="https://huggingface.co/anvuew/dereverb_mel_band_roformer/resolve/main/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
    ),
    "dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt": ModelSpec(
        filename="dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt",
        arch="mel_band_roformer",
        config_name="dereverb_mel_band_roformer_anvuew",
        weights_url="https://huggingface.co/anvuew/dereverb_mel_band_roformer/resolve/main/dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt",
    ),
}


def get_model_spec(filename: str) -> ModelSpec:
    """Look up a checkpoint's registry entry.

    Raises:
        KeyError: Filename is not a registered model.
    """
    return MODEL_REGISTRY[filename]
