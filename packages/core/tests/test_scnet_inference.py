"""Offline SCNet registry, architecture, and demix smoke coverage."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from upmixer.separation.inference import demix, loader
from upmixer.separation.inference.archs.scnet import SCNet
from upmixer.separation.inference.config import ModelConfig, load_model_config
from upmixer.separation.inference.registry import get_model_spec


MODEL_FILENAME = "model_scnet_ep_36_sdr_10.0891.ckpt"


def _tiny_model() -> SCNet:
    return SCNet(
        sources=["drums", "bass", "other", "vocals"],
        audio_channels=2,
        dims=[4, 4, 4, 4],
        nfft=16,
        hop_size=4,
        win_size=16,
        band_SR=[0.25, 0.375, 0.375],
        band_stride=[1, 2, 2],
        band_kernel=[3, 2, 2],
        conv_depths=[1, 1, 1],
        compress=2,
        num_dplayer=2,
    ).eval()


def _demix_config(chunk_size: int = 20) -> ModelConfig:
    return ModelConfig(
        audio={"sample_rate": 44100, "chunk_size": chunk_size, "num_channels": 2},
        model={"audio_channels": 2},
        training={
            "instruments": ["drums", "bass", "other", "vocals"],
            "target_instrument": None,
        },
        inference={"dim_t": 1, "num_overlap": 2},
    )


def test_scnet_registry_pins_v1015_artifact_and_config():
    spec = get_model_spec(MODEL_FILENAME)
    assert spec.arch == "scnet"
    assert spec.config_name == "config_musdb18_scnet_xl_more_wide_v5"
    assert spec.weights_url.endswith(
        "/releases/download/v1.0.15/model_scnet_ep_36_sdr_10.0891.ckpt"
    )

    config = load_model_config(spec.config_name)
    assert config.sample_rate == 44100
    assert config.chunk_size == 485100
    assert config.num_overlap == 4
    assert config.instruments == ["drums", "bass", "other", "vocals"]
    assert config.model["dims"] == [4, 64, 128, 256]
    assert config.model["band_stride"] == [1, 4, 4]
    assert config.model["num_dplayer"] == 8


def test_scnet_release_config_builds_arch_without_weights():
    spec = get_model_spec(MODEL_FILENAME)
    config = load_model_config(spec.config_name)
    model = loader._build_arch(spec, config, torch.device("cpu"))
    assert isinstance(model, SCNet)
    assert model.sources == config.instruments


def test_scnet_forward_preserves_four_stereo_stems_and_length():
    model = _tiny_model()
    with torch.inference_mode():
        output = model(torch.randn(1, 2, 64))
    assert output.shape == (1, 4, 2, 64)
    assert output.dtype == torch.float32


def test_scnet_state_dict_round_trip_has_no_missing_keys():
    model = _tiny_model()
    restored = _tiny_model()
    restored.load_state_dict(model.state_dict())


def test_scnet_loader_accepts_training_checkpoint_wrapper(tmp_path):
    state = {"weight": torch.ones(1)}
    checkpoint = tmp_path / "wrapped.ckpt"
    torch.save({"model_state_dict": state, "epoch": 36}, checkpoint)
    loaded = loader._load_state_dict(str(checkpoint))
    assert torch.equal(loaded["weight"], state["weight"])


def test_scnet_demix_maps_canonical_sources_and_preserves_channels():
    class ConstantModel(torch.nn.Module):
        def forward(self, batch):
            values = [1.0, 2.0, 3.0, 4.0]
            return torch.stack(
                [torch.full_like(batch, value) for value in values], dim=1
            )

    mix = np.zeros((2, 5), dtype=np.float32)
    stems = demix.demix_scnet(
        ConstantModel(),
        mix,
        _demix_config(),
        torch.device("cpu"),
        batch_size=2,
    )

    assert list(stems) == ["drums", "bass", "other", "vocals"]
    for index, name in enumerate(stems, start=1):
        assert stems[name].shape == mix.shape
        assert np.all(stems[name] == index)


def test_exact_scnet_mps_load_uses_cpu(monkeypatch, caplog, tmp_path):
    devices = []

    monkeypatch.setattr(loader, "_ensure_weights", lambda _spec, _dir: str(tmp_path / "model.ckpt"))
    monkeypatch.setattr(loader, "_load_state_dict", lambda _path: {})

    def build(_spec, _config, device):
        devices.append(device)
        return torch.nn.Identity()

    monkeypatch.setattr(loader, "_build_arch", build)
    caplog.set_level(logging.WARNING, logger="upmixer")

    loader.load_model(MODEL_FILENAME, torch.device("mps"), str(tmp_path))

    assert devices == [torch.device("cpu")]
    assert "falling back to CPU" in caplog.text


@pytest.mark.perf
def test_real_scnet_checkpoint_loads_when_requested(tmp_path):
    checkpoint = os.environ.get("UPMIXER_SCNET_CHECKPOINT")
    if not checkpoint:
        pytest.skip("set UPMIXER_SCNET_CHECKPOINT for real-checkpoint smoke test")
    checkpoint_path = Path(checkpoint)
    model, config = loader.load_model(
        MODEL_FILENAME, torch.device("cpu"), str(checkpoint_path.parent)
    )
    assert model.training is False
    assert config.instruments == ["drums", "bass", "other", "vocals"]
