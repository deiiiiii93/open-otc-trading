"""arena_run: per-model reasoning efforts, replacing the single-value column

Revision ID: 0057_arena_run_reasoning_efforts_map
Revises: 0056_arena_run_reasoning_effort

0056 stored ONE effort per run. That cannot express a real pinned board: the
ladders differ per model (GLM-5.2 accepts only high/max, most models accept
low/medium/high, five arena contestants are toggle-only with no levels at all),
so no single level is valid across a mixed field. Replaced by a JSON map
`{model_slug: effort}`.

No data migration: 0056 shipped the same day and every existing row is NULL
(nothing had been pinned yet), so there is nothing to carry across. Should a
non-NULL scalar exist, it is fanned out to every model in the run rather than
dropped — that is what it meant.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so on a fresh
database the new column already exists and the old one never did. Every
migration after 0001 must therefore guard its DDL on current schema state.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0057_arena_run_reasoning_efforts_map"
down_revision = "0056_arena_run_reasoning_effort"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "arena_run" not in _tables():
        return
    cols = _columns("arena_run")

    if "reasoning_efforts" not in cols:
        op.add_column(
            "arena_run", sa.Column("reasoning_efforts", sa.JSON(), nullable=True)
        )

    if "reasoning_effort" in cols:
        # Carry any pinned scalar across as "this effort, for every model in the
        # run" — the only faithful reading of the old column.
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT id, model_ids, reasoning_effort FROM arena_run "
                "WHERE reasoning_effort IS NOT NULL"
            )
        ).fetchall()
        for run_id, model_ids, effort in rows:
            try:
                models = json.loads(model_ids) if isinstance(model_ids, str) else model_ids
            except (TypeError, ValueError):
                models = []
            mapping = {str(m): effort for m in (models or [])}
            bind.execute(
                sa.text("UPDATE arena_run SET reasoning_efforts = :m WHERE id = :i"),
                {"m": json.dumps(mapping), "i": run_id},
            )

        # batch_alter_table, never a direct op.drop_column: SQLite refuses
        # ALTER TABLE ... DROP COLUMN while any FK definition names the column,
        # and a create_all database hands columns their ORM foreign keys even
        # when no historical migration created them.
        with op.batch_alter_table("arena_run") as batch:
            batch.drop_column("reasoning_effort")


def downgrade() -> None:
    if "arena_run" not in _tables():
        return
    cols = _columns("arena_run")

    if "reasoning_effort" not in cols:
        op.add_column(
            "arena_run", sa.Column("reasoning_effort", sa.String(16), nullable=True)
        )
        # Reverse only what round-trips: a map with a single distinct value is a
        # uniform pin. A genuinely mixed map cannot be represented by one column,
        # so it is left NULL rather than silently reporting one model's effort as
        # the whole run's.
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT id, reasoning_efforts FROM arena_run "
                "WHERE reasoning_efforts IS NOT NULL"
            )
        ).fetchall()
        for run_id, payload in rows:
            try:
                mapping = json.loads(payload) if isinstance(payload, str) else payload
            except (TypeError, ValueError):
                continue
            values = {v for v in (mapping or {}).values() if v}
            if len(values) == 1:
                bind.execute(
                    sa.text(
                        "UPDATE arena_run SET reasoning_effort = :e WHERE id = :i"
                    ),
                    {"e": values.pop(), "i": run_id},
                )

    if "reasoning_efforts" in cols:
        with op.batch_alter_table("arena_run") as batch:
            batch.drop_column("reasoning_efforts")
