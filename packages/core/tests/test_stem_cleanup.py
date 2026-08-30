from __future__ import annotations

from unittest.mock import patch

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.separation.stem_cleanup import apply_stem_cleanup
from upmixer.separation.stem_pipeline_exec import execute_plan
from upmixer.separation.stem_plan import MODEL_DEUX, resolve_separation_plan


def test_cleanup_is_finite_complementary_and_shape_preserving():
    sample_rate = 44_100
    t = np.arange(sample_rate, dtype=np.float64) / sample_rate
    vocals = np.column_stack([
        0.2 * np.sin(2 * np.pi * 880.0 * t),
        0.2 * np.sin(2 * np.pi * 880.0 * t),
    ]).astype(np.float32)
    instrumental = np.column_stack([
        0.3 * np.sin(2 * np.pi * 220.0 * t),
        0.25 * np.sin(2 * np.pi * 330.0 * t),
    ]).astype(np.float32)
    parent = vocals + instrumental

    cleaned_vocals, cleaned_instrumental = apply_stem_cleanup(
        parent, vocals, instrumental, sample_rate
    )

    assert cleaned_vocals.shape == vocals.shape
    assert cleaned_instrumental.shape == instrumental.shape
    assert cleaned_vocals.dtype == vocals.dtype
    assert np.isfinite(cleaned_vocals).all()
    assert np.isfinite(cleaned_instrumental).all()
    assert np.max(np.abs(parent - cleaned_vocals - cleaned_instrumental)) < 1e-6


def test_execute_plan_uses_separator_parent_not_native_rate_source(tmp_path):
    native_rate = 48_000
    separation_rate = 44_100
    source = tmp_path / "source.wav"
    sf.write(
        source,
        np.zeros((native_rate, 2), dtype=np.float32),
        native_rate,
        subtype="FLOAT",
    )
    exact_parent = np.full((separation_rate, 2), 0.75, dtype=np.float32)

    class FakeSeparator:
        def separate_to_file(
            self, _path, _keep, _overrides=None, _wanted=None,
            retain_parent=False,
        ):
            assert retain_parent
            return {
                "Vocals": np.full_like(exact_parent, 0.25),
                "_deux_inst": np.full_like(exact_parent, 0.5),
            }, {}

        def take_last_parent(self):
            return exact_parent

    captured = []

    def fake_cleanup(parent, vocals, instrumental, sample_rate):
        captured.append((parent, sample_rate))
        return vocals, instrumental

    plan = resolve_separation_plan(["Vocals"])
    with patch(
        "upmixer.separation.stem_cleanup.apply_stem_cleanup",
        side_effect=fake_cleanup,
    ):
        stems = execute_plan(
            lambda model, _sr: FakeSeparator() if model == MODEL_DEUX else None,
            plan,
            str(source),
            separation_rate,
            cfg=UpmixConfig(stem_bleed_reduction=True),
        )

    assert len(stems["Vocals"]) == separation_rate
    assert len(captured) == 1
    assert captured[0][0] is exact_parent
    assert captured[0][1] == separation_rate
