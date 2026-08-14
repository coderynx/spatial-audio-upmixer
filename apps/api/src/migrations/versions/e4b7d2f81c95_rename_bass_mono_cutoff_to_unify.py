"""Rename mastering.bass.mono_cutoff_hz to unify_hz in stored JSON.

The pairwise bass mono-maker was replaced by LF unification, so the old key no
longer exists in the manifest schema and any stored copy of it fails
validation the moment the client sends the block back.

A non-null cutoff meant "collapse the low end below X", which the `front`
spread is the closest equivalent to — so those blocks also get `spread` pinned
rather than inheriting the new `bed` default.

The bass block is reached through several different shapes (a manifest, a
per-layout override map, a whole project snapshot), so the rewrite walks the
decoded JSON rather than indexing a fixed path. `mono_cutoff_hz` appears
nowhere else in the schema, which is what makes that safe.

Revision ID: e4b7d2f81c95
Revises: a7c3e04b8d51
"""

import json
from typing import Callable, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7d2f81c95"
down_revision: Union[str, None] = "a7c3e04b8d51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every column holding JSON that can carry a mastering block.
_JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": ("manifest", "scene", "view_state"),
    "jobs": ("manifest", "project_snapshot"),
    "project_tracks": ("layout_overrides", "scene_overrides"),
}

OLD_KEY = "mono_cutoff_hz"
NEW_KEY = "unify_hz"
#: Keys the old shape had no way to express, dropped on downgrade.
_UNIFY_ONLY_KEYS = ("spread", "punch", "lfe_mode", "lfe_send")


def rename_to_unify(block: dict) -> bool:
    """Rewrite one bass block in place; True when it changed."""
    if OLD_KEY not in block:
        return False
    cutoff = block.pop(OLD_KEY)
    block[NEW_KEY] = cutoff
    if cutoff is not None:
        block["spread"] = "front"
    return True


def rename_to_mono_cutoff(block: dict) -> bool:
    """Inverse of :func:`rename_to_unify`, dropping what it cannot express."""
    if NEW_KEY not in block:
        return False
    block[OLD_KEY] = block.pop(NEW_KEY)
    for key in _UNIFY_ONLY_KEYS:
        block.pop(key, None)
    return True


def walk(node, rewrite: Callable[[dict], bool]) -> bool:
    """Apply *rewrite* to every dict in *node*; True when anything changed."""
    changed = False
    if isinstance(node, dict):
        changed = rewrite(node)
        for value in node.values():
            changed |= walk(value, rewrite)
    elif isinstance(node, list):
        for value in node:
            changed |= walk(value, rewrite)
    return changed


def _rewrite_all(rewrite: Callable[[dict], bool]) -> None:
    connection = op.get_bind()
    existing = {
        row[0]
        for row in connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
    }
    for table, columns in _JSON_COLUMNS.items():
        if table not in existing:
            continue
        for column in columns:
            rows = connection.execute(
                sa.text(
                    f'SELECT id, "{column}" FROM {table} WHERE "{column}" IS NOT NULL'
                )
            ).fetchall()
            for row_id, raw in rows:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not walk(payload, rewrite):
                    continue
                connection.execute(
                    sa.text(
                        f'UPDATE {table} SET "{column}" = :payload WHERE id = :id'
                    ),
                    {"payload": json.dumps(payload), "id": row_id},
                )


def upgrade() -> None:
    _rewrite_all(rename_to_unify)


def downgrade() -> None:
    _rewrite_all(rename_to_mono_cutoff)
