"""Add compressed original-track previews to projects.

Revision ID: 8a2d4f9be101
Revises: 61d4c1a3e217
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a2d4f9be101"
down_revision: Union[str, None] = "61d4c1a3e217"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.add_column(sa.Column("source_preview_relative_path", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("source_preview_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.drop_column("source_preview_size_bytes")
        batch.drop_column("source_preview_relative_path")
