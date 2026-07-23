"""Add uploaded mastering references.

Revision ID: 0fc416c63eae
Revises: 8012858fef4f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0fc416c63eae"
down_revision: Union[str, None] = "8012858fef4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mastering_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(op.f("ix_mastering_references_import_id"), "mastering_references", ["import_id"], unique=False)
    op.create_index(op.f("ix_mastering_references_sha256"), "mastering_references", ["sha256"], unique=False)
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("mastering_reference_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_mastering_reference_id_mastering_references",
            "mastering_references",
            ["mastering_reference_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_jobs_mastering_reference_id", ["mastering_reference_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_mastering_reference_id")
        batch.drop_constraint("fk_jobs_mastering_reference_id_mastering_references", type_="foreignkey")
        batch.drop_column("mastering_reference_id")
    op.drop_index(op.f("ix_mastering_references_sha256"), table_name="mastering_references")
    op.drop_index(op.f("ix_mastering_references_import_id"), table_name="mastering_references")
    op.drop_table("mastering_references")
