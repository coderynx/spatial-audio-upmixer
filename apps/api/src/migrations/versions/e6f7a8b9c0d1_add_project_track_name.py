"""Add project-local track names.

Revision ID: e6f7a8b9c0d1
Revises: b9d2f4a6c810
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "b9d2f4a6c810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.add_column(sa.Column("name", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.drop_column("name")
