"""Project mutations and their storage and worker aftermath."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from upmixer_web.features.projects.archive import import_project_archive
from upmixer_web.features.projects.service import (
    add_project_assets,
    create_empty_project,
    expand_project_stems,
    mark_project_deleting,
    project_export_job,
    reprepare_project_stems,
    retry_project,
    set_track_layouts,
    update_project_settings,
    update_project_track_name,
    update_project_view_state,
    update_track_layout_settings,
)
from upmixer_web.features.projects.storage import ProjectStemStorage
from upmixer_web.shared.models import ImportBatch, Job, MasteringReference, Project
from upmixer_web.shared.storage import ObjectStorage

if TYPE_CHECKING:
    from upmixer_web.worker import WorkerManager


class ProjectMutations:
    """Own Project mutation aftermath that routes must not coordinate."""

    def __init__(
        self, storage: ObjectStorage, project_stems: ProjectStemStorage, manager: WorkerManager
    ) -> None:
        self.storage = storage
        self.project_stems = project_stems
        self.manager = manager

    def create(
        self, session: Session, name: str, notes: str | None, manifest: dict[str, Any], scene: dict[str, Any]
    ) -> Project:
        return create_empty_project(session, name, notes, manifest, scene)

    def add_assets(
        self, session: Session, project: Project, batch: ImportBatch, overrides: dict[str, dict[str, Any]]
    ) -> Project:
        project = add_project_assets(session, project, batch, overrides)
        self.manager.notify()
        return project

    def update_settings(
        self,
        session: Session,
        project: Project,
        manifest: dict[str, Any],
        scene: dict[str, Any],
        name: str | None,
        notes: str | None,
        reference: MasteringReference | None,
        preview_quality: str | None,
    ) -> Project:
        previous_quality = project.preview_quality
        project = update_project_settings(
            session, project, manifest, scene, name, notes, reference, preview_quality
        )
        if preview_quality != previous_quality and preview_quality is not None and project.prepared_stems:
            self.project_stems.regenerate_previews(project, project.preview_quality, self.storage)
            session.commit()
        self.manager.schedule_reference_match(project.id)
        if project.status == "queued":
            self.manager.notify()
        return project

    def update_view_state(self, session: Session, project: Project, view_state: dict[str, Any]) -> None:
        update_project_view_state(session, project, view_state)

    def set_track_layouts(
        self, session: Session, project: Project, track_id: str, layouts: Iterable[str]
    ) -> Project:
        return set_track_layouts(session, project, track_id, layouts)

    def rename_track(self, session: Session, project: Project, track_id: str, name: str) -> Project:
        return update_project_track_name(session, project, track_id, name)

    def update_track_layout(
        self, session: Session, project: Project, track_id: str, layout: str,
        manifest_overrides: dict[str, Any], scene_overrides: dict[str, Any],
    ) -> Project:
        return update_track_layout_settings(
            session, project, track_id, layout, manifest_overrides, scene_overrides
        )

    def expand_stems(self, session: Session, project: Project, stems: Iterable[str]) -> Project:
        project = expand_project_stems(session, project, stems)
        self.manager.notify()
        return project

    def export(self, session: Session, project: Project, layout: str) -> Job:
        job = project_export_job(session, project, self.project_stems, layout)
        self.manager.notify()
        return job

    def retry(self, session: Session, project: Project) -> Project:
        project = retry_project(session, project)
        self.manager.notify()
        return project

    def reprepare(
        self, session: Session, project: Project, stems: Iterable[str] | None,
        stem_bleed_reduction: bool | None, stem_ensemble: bool | None,
    ) -> Project:
        project = reprepare_project_stems(
            session, project, stems, stem_bleed_reduction, stem_ensemble
        )
        self.manager.notify()
        return project

    def import_archive(self, session: Session, archive_path: Path) -> Project:
        project = import_project_archive(
            session, self.storage, self.project_stems, self.manager.work_root, archive_path
        )
        self.manager.notify()
        return project

    def delete(self, session: Session, project: Project) -> None:
        if mark_project_deleting(session, project):
            session.close()
            self.manager.delete_now_project(project.id)
        self.manager.notify()
