"""Project response-view builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from upmixer_web.features.projects.schemas import ProjectView, ReferenceMatchAssetView
from upmixer_web.features.projects.layouts import track_layouts
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.shared.models import Project

if TYPE_CHECKING:
    # Deferred: this module is reachable from upmixer_web.worker's import
    # chain (via features.projects.routes), so a runtime import here would
    # cycle back into a partially initialized upmixer_web.worker. PEP 563
    # (see the __future__ import above) means the WorkerManager annotation
    # below is never evaluated at runtime.
    from upmixer_web.worker import WorkerManager


def project_view(
    project: Project, root_path: str = "", project_stems: ProjectStemStorage | None = None,
    manager: WorkerManager | None = None,
) -> ProjectView:
    view = ProjectView.model_validate(project)
    if manager is not None:
        view.reference_match_pending = manager.reference_match_pending(project.id)
        view.peaks_pending = manager.peaks_pending(project.id)
    stem_by_id = {stem.id: stem for stem in project.stems}
    # Each track's asset may belong to a different import batch than
    # project.import_id once assets are added to a project incrementally —
    # the audio route validates asset.import_id against the URL's import_id,
    # so the URL must use the track's own asset, not the project's.
    for track, track_orm in zip(view.tracks, project.tracks, strict=True):
        track.layouts = track_layouts(track_orm, project)
        track.layout_overrides = {
            layout: block
            for layout, block in track.layout_overrides.items()
        }
        track.asset.audio_url = (
            f"{root_path}/api/v1/imports/{track_orm.asset.import_id}/assets/{track.asset.id}/audio"
        )
        track.source_preview_url = (
            f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/source-preview"
        )
        peaks_meta = project_stems.read_track_peaks_meta(project.id, track.id) if project_stems else None
        if peaks_meta:
            # Versioned by the stem generation the envelopes were built from,
            # same cache-busting convention as `fir_url` below — the route
            # itself ignores the query param.
            track.peaks_url = (
                f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/peaks"
                f"?v={peaks_meta.get('generation', 0)}"
            )
            track.peaks_bins = peaks_meta.get("bins", 0)
            track.peaks_stem_keys = peaks_meta.get("stems", [])
            track.peaks_duration_seconds = peaks_meta.get("duration_seconds")
        for stem in track.stems:
            base_url = (
                f"{root_path}/api/v1/projects/{project.id}/tracks/{track.id}/"
                f"stems/{stem.id}/audio"
            )
            stem.audio_url = base_url
            if stem_by_id[stem.id].preview_relative_path:
                stem.preview_url = (
                    f"{base_url}?quality=preview&v={stem_by_id[stem.id].generation}-{project.preview_quality}"
                )
    for layout in project_stems.reference_match_layouts(project.id) if project_stems else []:
        meta = project_stems.read_reference_match_meta(project.id, layout)
        if not meta:
            continue
        fir_url = None
        if meta.get("channels") and meta.get("curve"):
            fir_url = f"{root_path}/api/v1/projects/{project.id}/reference-match/{layout}/fir"
            # Signature-versioned so the browser's fir_url-keyed decode cache
            # busts on a real recompute; `strength`/`max_db` are appended by
            # the caller as live query params (see ReferenceMatchAssetView).
            if meta.get("signature"):
                fir_url = f"{fir_url}?v={meta['signature']}"
        view.reference_match[layout] = ReferenceMatchAssetView(
            fir_url=fir_url,
            channels=meta.get("channels", []),
            rms_gain_db=meta.get("rms_gain_db", 0.0),
            sample_rate=meta.get("sample_rate", 0),
        )
    return view
