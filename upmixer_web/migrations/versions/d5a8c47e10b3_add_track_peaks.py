"""Add precomputed waveform peaks to project tracks.

Revision ID: d5a8c47e10b3
Revises: c2e5a71f9b06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a8c47e10b3"
down_revision: Union[str, None] = "c2e5a71f9b06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.add_column(sa.Column("peaks_relative_path", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("peaks_duration_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.drop_column("peaks_duration_seconds")
        batch.drop_column("peaks_relative_path")
