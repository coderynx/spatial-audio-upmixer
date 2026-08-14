"""Allow empty project creation: nullable import_id, add notes.

Revision ID: 7aaaf439f619
Revises: d5a8c47e10b3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7aaaf439f619"
down_revision: Union[str, None] = "d5a8c47e10b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("import_id", existing_type=sa.String(length=36), nullable=True)
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("notes")
        batch.alter_column("import_id", existing_type=sa.String(length=36), nullable=False)
