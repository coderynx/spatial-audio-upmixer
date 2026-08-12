"""Add view_state to projects.

Revision ID: f1a2c8d6e903
Revises: 7aaaf439f619
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2c8d6e903"
down_revision: Union[str, None] = "7aaaf439f619"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("view_state", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("view_state")
