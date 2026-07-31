"""Short end-to-end separation through the full StemSeparator surface.

Drives the real SeparationEngine/demix path (file in -> WAV stems out) with
a fast stub model standing in for real weights, verifying a non-default
overlap actually reaches the engine and the run still produces correctly
shaped, non-silent stems.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import soundfile as sf
import torch

from upmixer.separation.inference.config import ModelConfig
from upmixer.separation.inference.registry import ModelSpec
from upmixer.separation.separator import StemSeparator


class _HalfGainModel(torch.nn.Module):
    """Splits the mix evenly between the two stems (both non-silent)."""

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        return batch * 0.5


def _make_config() -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 8000, "hop_length": 80},
        model={"stft_hop_length": 80},
        training={"instruments": ["vocals", "other"], "target_instrument": "vocals"},
        inference={"dim_t": 6},  # chunk_size = 80 * 5 = 400 samples
    )


def test_short_separation_produces_correct_stems_with_custom_overlap(tmp_path):
    sample_rate = 8000
    n_samples = 1600  # 4 chunks worth at overlap=1
    rng = np.random.default_rng(0)
    mix = 0.5 * rng.standard_normal((n_samples, 2)).astype(np.float32)
    input_path = str(tmp_path / "mix.wav")
    sf.write(input_path, mix, sample_rate)

    fake_spec = ModelSpec(
        filename="stub.ckpt", arch="bs_roformer", config_name="unused", weights_url="",
    )
    fake_model = _HalfGainModel()
    fake_config = _make_config()

    with (
        patch(
            "upmixer.separation.inference.loader.load_model",
            return_value=(fake_model, fake_config),
        ),
        patch(
            "upmixer.separation.inference.registry.get_model_spec",
            return_value=fake_spec,
        ),
        StemSeparator(
            model="stub.ckpt", sample_rate=sample_rate,
            batch_size=1, segment_size=None, chunk_duration_s=None,
            overlap=4,
        ) as separator,
    ):
        engine = separator._get_separator()
        assert engine._overlap == 4

        stems = separator.separate(input_path)

    assert set(stems) == {"Vocals", "Other"}
    for audio in stems.values():
        assert audio.shape == (n_samples, 2)
        assert np.any(np.abs(audio) > 1e-6)
