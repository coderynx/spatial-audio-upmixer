"""Integration coverage for newly registered separation checkpoints.

Tier 1 (default `pytest -q`) proves each new registry entry's bundled YAML
matches its architecture's constructor and produces correctly named,
non-silent stems — without downloading any weights: the arch is built from
the config with its (random) default-initialized parameters.

Tier 2 (`@pytest.mark.perf`) loads the real checkpoint from its
``weights_url`` and asserts non-silent output on real audio, mirroring
``test_stem_separation_integration.py``.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from upmixer.separation.inference.config import load_model_config
from upmixer.separation.inference.registry import get_model_spec
from upmixer.separation.separator import StemSeparator

_NEW_MODELS = (
    "BS-Roformer-Resurrection-Inst.ckpt",
    "bs_roformer_inst_hyperacev2.ckpt",
    "becruily_deux.ckpt",
    "kimmel_unwa_ft2_bleedless.ckpt",
    "mel_band_roformer_vocals_becruily.ckpt",
    "mel_band_roformer_instrumental_becruily.ckpt",
    "mel_band_roformer_karaoke_becruily.ckpt",
    "mel_band_roformer_bleed_suppressor_v1.ckpt",
    "mel_band_roformer_denoise_debleed_gabox.ckpt",
    "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
)


@pytest.mark.parametrize("model_filename", _NEW_MODELS)
def test_config_matches_arch_and_demixes_non_silent(model_filename):
    torch = pytest.importorskip("torch")
    from upmixer.separation.inference import demix, loader

    spec = get_model_spec(model_filename)
    config = load_model_config(spec.config_name)
    device = torch.device("cpu")

    model = loader._build_arch(spec, config, device).eval()

    rng = np.random.default_rng(0)
    mix = (0.1 * rng.standard_normal((2, config.sample_rate))).astype(np.float32)

    stems = demix.demix_roformer(model, mix, config, device, segment_size=None, batch_size=1)

    assert set(stems) == set(config.instruments)
    for name, stem in stems.items():
        assert stem.shape == mix.shape, name
        assert np.any(np.abs(stem) > 0), name


_TEST_AUDIO = os.environ.get("UPMIXER_STEM_TEST_AUDIO")


@pytest.mark.perf
@pytest.mark.parametrize("model_filename", _NEW_MODELS)
def test_real_checkpoint_loads_and_demixes_non_silent(model_filename):
    if not _TEST_AUDIO:
        pytest.skip("set UPMIXER_STEM_TEST_AUDIO for real-checkpoint smoke test")
    pytest.importorskip("torch")

    config = load_model_config(get_model_spec(model_filename).config_name)
    with StemSeparator(model=model_filename, sample_rate=config.sample_rate) as separator:
        stems = separator.separate(_TEST_AUDIO)

    # Canonical stem naming (STEM_NAME_MAP / MODEL_STEM_OVERRIDES in
    # separator.py) is separate, deferred wiring work — this only proves the
    # checkpoint loads and infers real, non-silent audio end to end.
    assert stems
    for name, stem in stems.items():
        assert np.any(np.abs(stem) > 1e-6), name
