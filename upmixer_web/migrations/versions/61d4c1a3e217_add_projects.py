"""Add editable web projects and project stem storage.

Revision ID: 61d4c1a3e217
Revises: 0fc416c63eae
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "61d4c1a3e217"
down_revision: Union[str, None] = "0fc416c63eae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("status_message", sa.String(length=1024), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("scene", sa.JSON(), nullable=False),
        sa.Column("requested_stems", sa.JSON(), nullable=False),
        sa.Column("prepared_stems", sa.JSON(), nullable=False),
        sa.Column("stem_generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_import_id"), "projects", ["import_id"], unique=False)
    op.create_index(op.f("ix_projects_status"), "projects", ["status"], unique=False)
    op.create_table(
        "project_tracks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("manifest_overrides", sa.JSON(), nullable=False),
        sa.Column("scene_overrides", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_tracks_asset_id"), "project_tracks", ["asset_id"], unique=False)
    op.create_index(op.f("ix_project_tracks_project_id"), "project_tracks", ["project_id"], unique=False)
    op.create_table(
        "project_stems",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("stem_key", sa.String(length=512), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("preview_relative_path", sa.String(length=1024), nullable=True),
        sa.Column("preview_size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["project_tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_stems_project_id"), "project_stems", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_stems_track_id"), "project_stems", ["track_id"], unique=False)
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("project_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("project_snapshot", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_jobs_project_id_projects", "projects", ["project_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_jobs_project_id", ["project_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_project_id")
        batch.drop_constraint("fk_jobs_project_id_projects", type_="foreignkey")
        batch.drop_column("project_snapshot")
        batch.drop_column("project_revision")
        batch.drop_column("project_id")
    op.drop_index(op.f("ix_project_stems_track_id"), table_name="project_stems")
    op.drop_index(op.f("ix_project_stems_project_id"), table_name="project_stems")
    op.drop_table("project_stems")
    op.drop_index(op.f("ix_project_tracks_project_id"), table_name="project_tracks")
    op.drop_index(op.f("ix_project_tracks_asset_id"), table_name="project_tracks")
    op.drop_table("project_tracks")
    op.drop_index(op.f("ix_projects_status"), table_name="projects")
    op.drop_index(op.f("ix_projects_import_id"), table_name="projects")
    op.drop_table("projects")
