"""Import response-view builder."""

from __future__ import annotations

from upmixer_web.features.imports.schemas import ImportView
from upmixer_web.shared.models import ImportBatch


def import_view(batch: ImportBatch, root_path: str = "") -> ImportView:
    view = ImportView.model_validate(batch)
    if batch.cover_key:
        view.cover_url = f"{root_path}/api/v1/imports/{batch.id}/cover"
    for asset in view.assets:
        asset.audio_url = (
            f"{root_path}/api/v1/imports/{batch.id}/assets/{asset.id}/audio"
        )
    return view
