"""Create web job tables.

Revision ID: 8012858fef4f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8012858fef4f"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("artist", sa.String(length=512), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("cover_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("status_message", sa.String(length=1024), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_batches.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_import_id"), "jobs", ["import_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("artist", sa.String(length=512), nullable=True),
        sa.Column("album", sa.String(length=512), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["import_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(op.f("ix_media_assets_import_id"), "media_assets", ["import_id"], unique=False)
    op.create_index(op.f("ix_media_assets_sha256"), "media_assets", ["sha256"], unique=False)
    op.create_table(
        "job_tracks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("output_key", sa.String(length=1024), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_tracks_asset_id"), "job_tracks", ["asset_id"], unique=False)
    op.create_index(op.f("ix_job_tracks_job_id"), "job_tracks", ["job_id"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["job_tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(op.f("ix_artifacts_job_id"), "artifacts", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_artifacts_job_id"), table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_job_tracks_job_id"), table_name="job_tracks")
    op.drop_index(op.f("ix_job_tracks_asset_id"), table_name="job_tracks")
    op.drop_table("job_tracks")
    op.drop_index(op.f("ix_media_assets_sha256"), table_name="media_assets")
    op.drop_index(op.f("ix_media_assets_import_id"), table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_import_id"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("import_batches")
