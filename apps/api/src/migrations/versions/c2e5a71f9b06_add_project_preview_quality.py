"""Add preview_quality to projects.

Revision ID: c2e5a71f9b06
Revises: b3f6c1d9a742
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2e5a71f9b06"
down_revision: Union[str, None] = "b3f6c1d9a742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("preview_quality", sa.String(length=16), nullable=False, server_default="high")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("preview_quality")
