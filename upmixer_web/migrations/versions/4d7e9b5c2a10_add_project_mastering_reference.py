"""Add project mastering-reference association.

Revision ID: 4d7e9b5c2a10
Revises: 8a2d4f9be101
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d7e9b5c2a10"
down_revision: Union[str, None] = "8a2d4f9be101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("mastering_reference_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_projects_mastering_reference_id_mastering_references",
            "mastering_references", ["mastering_reference_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_projects_mastering_reference_id", ["mastering_reference_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_mastering_reference_id")
        batch.drop_constraint("fk_projects_mastering_reference_id_mastering_references", type_="foreignkey")
        batch.drop_column("mastering_reference_id")
