"""Bleed-reduction orchestration: gating, surround/height default, overrides."""
from __future__ import annotations

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.separation.bleed_reduction import apply_bleed_reduction
from upmixer.separation.stem_router import stem_reaches_surround_height

SR = 48_000
FMT = FORMAT_MAP["7.1.4"]


def _stereo(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.standard_normal(n), rng.standard_normal(n)]).astype(
        np.float32
    ) * 0.1


class _Recorder:
    """Fake separate_array: records calls, returns a per-model marker stem."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(self, model: str, audio: np.ndarray, in_sr: int) -> dict:
        self.calls.append((model, in_sr))
        n = len(audio)
        # Instrumental output for the reference model; primary for debleed.
        marker = np.full((n, 2), 0.5, dtype=np.float32)
        return {"Instrumental": marker, "Vocals": np.zeros((n, 2), np.float32)}


def _run(cfg: UpmixConfig, stems: dict[str, np.ndarray], zones: dict[str, np.ndarray]):
    rec = _Recorder()
    out = apply_bleed_reduction(stems, zones, SR, SR, cfg, FMT, rec)
    return out, rec


def test_gate_off_is_noop_and_runs_no_inference():
    cfg = UpmixConfig(stem_bleed_reduction=False)
    stems = {"Other@surround": _stereo(8000, 1)}
    original = stems["Other@surround"].copy()

    out, rec = _run(cfg, stems, {"surround": _stereo(8000, 9)})

    assert rec.calls == []
    assert np.array_equal(out["Other@surround"], original)


def test_surround_stem_processed_front_stem_untouched():
    cfg = UpmixConfig(stem_bleed_reduction=True)
    bass = _stereo(8000, 2)  # Bass routes front+LFE only → no surround/height
    other = _stereo(8000, 3)  # Other routes to surround/height
    stems = {"Bass@front": bass.copy(), "Other@surround": other.copy()}
    zones = {"front": _stereo(8000, 7), "surround": _stereo(8000, 8)}

    # Sanity on the routing assumption this test depends on.
    assert stem_reaches_surround_height("Bass@front", FMT) == (False, False)
    assert any(stem_reaches_surround_height("Other@surround", FMT))

    out, rec = _run(cfg, stems, zones)

    assert np.array_equal(out["Bass@front"], bass)
    assert not np.array_equal(out["Other@surround"], other)
    models = {m for m, _ in rec.calls}
    # Phase-fix runs by default for the diffuse stem; debleed is opt-in.
    assert cfg.stem_phase_fix_reference_model in models
    assert cfg.stem_debleed_model not in models


def test_debleed_replaces_stem_with_model_output():
    cfg = UpmixConfig(
        stem_bleed_reduction=True,
        stem_phase_fix={"*": False},  # isolate the debleed pass
        stem_debleed={"Other": True},
    )
    stems = {"Other@surround": _stereo(8000, 4)}
    out, rec = _run(cfg, stems, {"surround": _stereo(8000, 5)})

    # Debleed marker (0.5) replaced the stem; reference model was not run.
    assert np.allclose(out["Other@surround"], 0.5)
    assert all(m == cfg.stem_debleed_model for m, _ in rec.calls)


def test_per_stem_override_disables_pass():
    cfg = UpmixConfig(
        stem_bleed_reduction=True,
        stem_phase_fix={"Other": False},
        stem_debleed={"Other": False},
    )
    other = _stereo(8000, 6)
    stems = {"Other@surround": other.copy()}

    out, rec = _run(cfg, stems, {"surround": _stereo(8000, 1)})

    assert rec.calls == []
    assert np.array_equal(out["Other@surround"], other)


def test_debleed_off_by_default_and_progress_emitted():
    cfg = UpmixConfig(stem_bleed_reduction=True)
    stems = {"Other@surround": _stereo(8000, 2), "Guitar@surround": _stereo(8000, 3)}
    rec = _Recorder()
    messages: list[str] = []

    apply_bleed_reduction(
        stems, {"surround": _stereo(8000, 4)}, SR, SR, cfg, FMT, rec,
        progress=messages.append,
    )

    assert all(m != cfg.stem_debleed_model for m, _ in rec.calls)
    assert len(messages) == 2
    assert all("phase-fix" in m and "debleed" not in m for m in messages)


def test_reference_reused_across_stems_in_zone():
    cfg = UpmixConfig(stem_bleed_reduction=True, stem_debleed={"*": False})
    stems = {
        "Other@surround": _stereo(8000, 2),
        "Guitar@surround": _stereo(8000, 3),
    }
    _, rec = _run(cfg, stems, {"surround": _stereo(8000, 4)})

    ref_calls = [m for m, _ in rec.calls if m == cfg.stem_phase_fix_reference_model]
    assert len(ref_calls) == 1
