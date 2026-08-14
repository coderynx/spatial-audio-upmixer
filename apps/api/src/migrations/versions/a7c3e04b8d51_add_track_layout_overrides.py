"""Replace project_tracks.manifest_overrides with per-layout overrides.

Revision ID: a7c3e04b8d51
Revises: f1a2c8d6e903
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c3e04b8d51"
down_revision: Union[str, None] = "f1a2c8d6e903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_LAYOUT = "7.1.4"


def _loads(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def upgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.add_column(
            sa.Column("layout_overrides", sa.JSON(), nullable=False, server_default="{}")
        )

    connection = op.get_bind()
    project_layouts = {
        row.id: (_loads(row.manifest).get("mixing") or {}).get("channel_layout") or DEFAULT_LAYOUT
        for row in connection.execute(sa.text("SELECT id, manifest FROM projects"))
    }
    for row in connection.execute(
        sa.text("SELECT id, project_id, manifest_overrides FROM project_tracks")
    ):
        overrides = _loads(row.manifest_overrides)
        mixing = overrides.get("mixing") if isinstance(overrides.get("mixing"), dict) else {}
        layout = mixing.get("channel_layout") or project_layouts.get(row.project_id) or DEFAULT_LAYOUT
        connection.execute(
            sa.text("UPDATE project_tracks SET layout_overrides = :value WHERE id = :id"),
            {
                "id": row.id,
                "value": json.dumps({layout: {**overrides, "mixing": {**mixing, "channel_layout": layout}}}),
            },
        )

    with op.batch_alter_table("project_tracks") as batch:
        batch.drop_column("manifest_overrides")


def downgrade() -> None:
    with op.batch_alter_table("project_tracks") as batch:
        batch.add_column(
            sa.Column("manifest_overrides", sa.JSON(), nullable=False, server_default="{}")
        )

    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id, layout_overrides FROM project_tracks")):
        layouts = _loads(row.layout_overrides)
        # Only one layout survives a downgrade — the column it goes back into
        # holds a single mix. The first is the track's original.
        first = next(iter(layouts.values()), {})
        connection.execute(
            sa.text("UPDATE project_tracks SET manifest_overrides = :value WHERE id = :id"),
            {"id": row.id, "value": json.dumps(first)},
        )

    with op.batch_alter_table("project_tracks") as batch:
        batch.drop_column("layout_overrides")
