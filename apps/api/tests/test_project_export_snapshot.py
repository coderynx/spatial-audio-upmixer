import pytest

from upmixer_web.shared.project_snapshot import ProjectExportSnapshot, ProjectExportTrack


def test_project_export_snapshot_round_trips_track_overrides():
    snapshot = ProjectExportSnapshot({
        "asset-1": ProjectExportTrack({"format": {"codec": "wav_pcm"}}, "/stems/track-1")
    })

    loaded = ProjectExportSnapshot.from_data(snapshot.to_data())

    assert loaded.track_for("asset-1") == snapshot.track_for("asset-1")
    assert loaded.track_for("missing") is None


def test_project_export_snapshot_rejects_invalid_track_data():
    with pytest.raises(ValueError, match="Project export track"):
        ProjectExportSnapshot.from_data({"tracks": {"asset-1": {}}})
