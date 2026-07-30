"""Add detailed progress_log to projects.

Revision ID: b3f6c1d9a742
Revises: 4d7e9b5c2a10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f6c1d9a742"
down_revision: Union[str, None] = "4d7e9b5c2a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("progress_log", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("progress_log")
