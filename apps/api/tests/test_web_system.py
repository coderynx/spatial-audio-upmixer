import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from upmixer_web.separation import separation_capability
from upmixer_web.storage import LocalObjectStorage


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


def test_capability_uses_engine_selected_device(tmp_path, monkeypatch):
    class FakeStemSeparator:
        def __init__(self, **_kwargs):
            pass

        @property
        def backend(self):
            return "mps"

    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
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
    monkeypatch.setattr("upmixer_web.separation.sys.version_info", (3, 14, 0))
    monkeypatch.setattr(
        "upmixer_web.separation.importlib.util.find_spec",
        lambda _name: pytest.fail("torch must not load on Python 3.14"),
    )

    capability = separation_capability(tmp_path)

    assert not capability["available"]
    assert capability["install_message"] == (
        "Stem separation is unavailable on Python 3.14 or newer. "
        "Use Python 3.11, 3.12, or 3.13."
    )
