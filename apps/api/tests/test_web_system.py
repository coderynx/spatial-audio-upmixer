import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from upmixer_web.shared.separation import separation_capability
from upmixer_web.shared.storage import LocalObjectStorage


def test_local_storage_rejects_parent_path(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="relative path"):
        storage.local_path("../escape.wav")


def test_configuration_lists_every_stem_and_runtime_capability(web_client):
    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    configuration = response.json()
    assert configuration["choices"]["stems"] == [
        "Vocals", "Bass", "Drums", "Guitar", "Piano", "Other",
        "Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash", "Crowd",
        "Lead Vocals", "Backing Vocals",
    ]
    assert "vocal-presence" in configuration["choices"]["stem_eq_profiles"]
    capability = configuration["capabilities"]["stem_separation"]
    assert isinstance(capability["available"], bool)
    assert isinstance(capability["accelerated"], bool)
    assert isinstance(capability["accelerator_detected"], bool)
    assert capability["accelerator_issue"] is None or isinstance(
        capability["accelerator_issue"],
        str,
    )
    assert capability["platform"]


def test_configuration_serves_engine_constants(web_client):
    from upmixer.config import UpmixConfig
    from upmixer.mastering.compressor import COMP_PROFILES

    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    constants = response.json()["constants"]

    expected_keys = {
        "channel_group_gains", "lfe_gain", "lfe_lowpass_hz", "surround_bass_cutoff_hz",
        "height_low_rolloff_hz", "height_low_rolloff_gain", "height_crossover_hz",
        "height_high_shelf_gain", "soft_limit_threshold", "limiter_lookahead_ms",
        "limiter_release_ms", "loudness_max_gain_db", "surround_downmix_coeff",
        "itu_center_coeff", "diffuse_send_blend", "surround_haas_ms", "height_haas_ms",
        "comp_profiles", "bass_profiles", "bass_sub_cutoff_hz", "bass_mid_cutoff_hz",
        "bass_excite_blend", "bass_excite_drive", "binaural_loudness_max_gain_db",
        "crosstalk_loudness_max_gain_db", "voicing_params", "transaural_voicing_params",
    }
    assert set(constants) == expected_keys

    # Served straight from core source modules (never re-typed): a couple of
    # spot-checks that the assembler reads the real values.
    cfg = UpmixConfig()
    assert constants["lfe_gain"] == cfg.lfe_gain
    assert constants["channel_group_gains"]["center"] == cfg.center_gain
    assert constants["comp_profiles"]["transparent"]["threshold_db"] == (
        COMP_PROFILES["transparent"]["threshold_db"]
    )
    # Voicing tables serialize the frozen dataclass in snake_case.
    listening = constants["voicing_params"]["listening"]
    assert listening["crossfeed_amount"] == 0.10
    assert listening["loudness_target_lkfs"] is None
    assert set(constants["transaural_voicing_params"]) == {
        "stereo", "smart_speaker", "car", "laptop", "phone",
    }


def test_capability_uses_engine_selected_device(tmp_path, monkeypatch):
    class FakeStemSeparator:
        def __init__(self, **_kwargs):
            pass

        @property
        def backend(self):
            return "mps"

    monkeypatch.setattr(
        "upmixer_web.shared.separation.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "upmixer.separation.separator.StemSeparator", FakeStemSeparator,
    )

    capability = separation_capability(tmp_path)

    assert capability["available"]
    assert capability["backend"] == "mps"
    assert capability["accelerated"]


def test_capability_rejects_unsupported_torch_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("upmixer_web.shared.separation.sys.version_info", (3, 14, 0))
    monkeypatch.setattr(
        "upmixer_web.shared.separation.importlib.util.find_spec",
        lambda _name: pytest.fail("torch must not load on Python 3.14"),
    )

    capability = separation_capability(tmp_path)

    assert not capability["available"]
    assert capability["install_message"] == (
        "Stem separation is unavailable on Python 3.14 or newer. "
        "Use Python 3.11, 3.12, or 3.13."
    )
