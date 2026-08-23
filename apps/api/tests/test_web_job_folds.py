"""The fold QC block has to reach the jobs API without a schema of its own.

`TrackView.result` is `dict[str, Any]` carrying `UpmixResult.to_dict()`
(mastering phase 1's note), and the column behind it is JSON — so a nested
dataclass added in core reaches the browser only if it flattens to JSON types
and survives view validation untouched. Nothing enforces that in core.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from upmixer_web.features.jobs.schemas import TrackView

_ASSET = {
    "id": "asset-1",
    "position": 0,
    "filename": "tone.wav",
    "relative_path": "tone.wav",
    "size_bytes": 1024,
    "title": None,
    "artist": None,
    "album": None,
    "release_date": None,
    "track_number": None,
    "duration_seconds": 8.0,
    "sample_rate": 48_000,
    "channels": 2,
}


def _mastered_result() -> dict:
    import numpy as np

    from upmixer.config import UpmixConfig
    from upmixer.formats import FORMAT_MAP
    from upmixer.mastering.chain import MasteringChain
    from upmixer.result import UpmixResult

    rng = np.random.default_rng(20260819)
    n = 4 * 48_000
    bed = {
        label.value: 0.3 * rng.standard_normal(n)
        for label in FORMAT_MAP["7.1.4"].channels
    }
    cfg = UpmixConfig(output_format="7.1.4", qc_measure_binaural=False)
    _, mastering = MasteringChain(cfg).process(bed, 48_000, FORMAT_MAP["7.1.4"])
    return UpmixResult(
        input_path="in.wav",
        output_path="out.wav",
        input_format="Stereo",
        output_format="7.1.4",
        input_sample_rate=48_000,
        output_sample_rate=48_000,
        duration_seconds=4.0,
        n_channels_in=2,
        n_channels_out=12,
        mode="stem",
        **mastering.delivery_fields(),
    ).to_dict()


def test_the_folds_block_survives_the_track_view():
    stored = json.loads(json.dumps(_mastered_result()))
    view = TrackView.model_validate({
        "id": "track-1",
        "position": 0,
        "status": "completed",
        "progress": 1.0,
        "result": stored,
        "error": None,
        "asset": _ASSET,
        "artifacts": [],
    })

    folds = view.result["folds"]
    assert set(folds) == {"native_lkfs", "stereo", "surround_51", "binaural"}
    assert folds["binaural"] is None
    for name in ("stereo", "surround_51"):
        assert set(folds[name]) == {
            "lkfs",
            "tp_dbtp",
            "plr_db",
            "lkfs_delta_lu",
            "tp_compliant",
            "loudness_divergent",
        }, name
        assert isinstance(folds[name]["tp_compliant"], bool), name
