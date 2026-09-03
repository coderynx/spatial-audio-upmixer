"""Owned JSON shape for a Project export attached to a Job."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProjectExportTrack:
    manifest_overrides: dict[str, Any]
    stem_input_dir: str

    @classmethod
    def from_data(cls, data: object) -> ProjectExportTrack:
        if not isinstance(data, dict):
            raise ValueError("Project export track must be an object")
        overrides = data.get("manifest_overrides")
        stem_input_dir = data.get("stem_input_dir")
        if not isinstance(overrides, dict) or not isinstance(stem_input_dir, str):
            raise ValueError("Project export track is invalid")
        return cls(copy.deepcopy(overrides), stem_input_dir)

    def to_data(self) -> dict[str, Any]:
        return {
            "manifest_overrides": copy.deepcopy(self.manifest_overrides),
            "stem_input_dir": self.stem_input_dir,
        }


@dataclass(frozen=True)
class ProjectExportSnapshot:
    tracks: dict[str, ProjectExportTrack]

    @classmethod
    def from_data(cls, data: object) -> ProjectExportSnapshot:
        if data is None:
            return cls({})
        if not isinstance(data, dict) or not isinstance(data.get("tracks"), dict):
            raise ValueError("Project export snapshot is invalid")
        if not all(isinstance(asset_id, str) for asset_id in data["tracks"]):
            raise ValueError("Project export snapshot has an invalid asset id")
        return cls({
            asset_id: ProjectExportTrack.from_data(track)
            for asset_id, track in data["tracks"].items()
        })

    def to_data(self) -> dict[str, Any]:
        return {"tracks": {asset_id: track.to_data() for asset_id, track in self.tracks.items()}}

    def track_for(self, asset_id: str) -> ProjectExportTrack | None:
        return self.tracks.get(asset_id)
