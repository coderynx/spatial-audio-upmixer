"""Replace legacy object spread with direct ADM object size.

Revision ID: b9d2f4a6c810
Revises: e4b7d2f81c95
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d2f4a6c810"
down_revision: Union[str, None] = "e4b7d2f81c95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON_COLUMNS = {
    "projects": ("manifest",),
    "jobs": ("manifest", "project_snapshot"),
    "project_tracks": ("layout_overrides",),
}


def _walk(node: object, upgrade: bool) -> bool:
    changed = False
    if isinstance(node, dict):
        is_placement = {"azimuth_deg", "elevation_deg", "width_deg"}.intersection(node)
        if upgrade and "spread_deg" in node and is_placement:
            node.pop("spread_deg")
            if upgrade:
                node.setdefault("object_size", 0.0)
            changed = True
        elif not upgrade and "object_size" in node and is_placement:
            node["spread_deg"] = 0.0
            node.pop("object_size")
            changed = True
        for value in node.values():
            changed |= _walk(value, upgrade)
    elif isinstance(node, list):
        for value in node:
            changed |= _walk(value, upgrade)
    return changed


def _rewrite(upgrade: bool) -> None:
    connection = op.get_bind()
    tables = {row[0] for row in connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'table'"))}
    for table, columns in _JSON_COLUMNS.items():
        if table not in tables:
            continue
        for column in columns:
            for row_id, raw in connection.execute(sa.text(f'SELECT id, "{column}" FROM {table} WHERE "{column}" IS NOT NULL')).fetchall():
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if _walk(payload, upgrade):
                    connection.execute(sa.text(f'UPDATE {table} SET "{column}" = :payload WHERE id = :id'), {"id": row_id, "payload": json.dumps(payload)})


def upgrade() -> None:
    _rewrite(True)


def downgrade() -> None:
    _rewrite(False)
