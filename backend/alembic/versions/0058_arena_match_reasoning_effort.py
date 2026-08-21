"""arena_match: reasoning_effort column + effort in the contestant key

Revision ID: 0058_arena_match_reasoning_effort
Revises: 0057_arena_run_reasoning_efforts_map

A contestant becomes (model_id, reasoning_effort) so one board can rank the same
model at two efforts. The 3-column unique constraint made the second arm collide
with the first.

'' means "unpinned, vendor default" — not NULL, because SQL treats NULLs as
distinct in a UNIQUE constraint, so NULL would silently stop protecting unpinned
pairs at the DB level.

The backfill derives each historical row's effort from its OWN config, never a
blind ''. Runs #107/#108 already recorded config.reasoning_effort; flattening
them would assert that a high board and a low board were the same regime, and
merge_runs — which now groups on this column — would fold them.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so a fresh database
already has both the column and the 4-column constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0058_arena_match_reasoning_effort"
down_revision = "0057_arena_run_reasoning_efforts_map"
branch_labels = None
depends_on = None

_OLD_UQ = "uq_arena_match_run_workflow_model"
_NEW_UQ = "uq_arena_match_run_workflow_model_effort"
_NEW_COLS = ["run_id", "workflow_id", "model_id", "reasoning_effort"]
_OLD_COLS = ["run_id", "workflow_id", "model_id"]


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {u["name"] for u in inspect(op.get_bind()).get_unique_constraints(table)}


def _superseded(table: str) -> bool:
    """True if a unique constraint already extends _NEW_COLS (a later shape)."""
    n = len(_NEW_COLS)
    return any(
        list(u["column_names"])[:n] == _NEW_COLS and len(u["column_names"]) > n
        for u in inspect(op.get_bind()).get_unique_constraints(table)
    )


def upgrade() -> None:
    if "arena_match" not in _tables():
        return

    if "reasoning_effort" not in _columns("arena_match"):
        op.add_column(
            "arena_match",
            sa.Column("reasoning_effort", sa.String(20),
                      nullable=False, server_default=""),
        )
        # json_valid guards a corrupt payload: json_extract RAISES on malformed
        # JSON, which would abort the whole upgrade over one bad row.
        op.get_bind().execute(sa.text(
            "UPDATE arena_match "
            "   SET reasoning_effort = "
            "       COALESCE(json_extract(config, '$.reasoning_effort'), '') "
            " WHERE json_valid(config)"
        ))

    uniques = _uniques("arena_match")
    # Superseded = some existing unique constraint already EXTENDS this one, i.e.
    # its columns start with these four. 0001_initial create_all materialises
    # today's ORM, so a fresh database arrives here already carrying 0059's
    # 5-column (…, max_output_tokens) constraint. Creating this 4-column one
    # anyway would leave the table with BOTH — and the 4-column one is STRICTER,
    # so it would silently forbid the second output-budget arm that 0059 exists
    # to allow. The fresh-chain test cannot see that (adding a redundant
    # constraint succeeds); 0058's own idempotency test is what caught it.
    #
    # Matched on COLUMNS, not names: the pre-0058 table's 3-column constraint is
    # UNNAMED, so a name-based test would read it as "superseded" and skip the
    # real conversion this migration exists to perform.
    if not _superseded("arena_match") and _NEW_UQ not in uniques:
        # batch_alter_table, never a direct constraint op: SQLite cannot alter a
        # constraint in place, so alembic rebuilds the table (create, copy, drop,
        # rename) and carries the rows, sibling FKs and other indexes across.
        with op.batch_alter_table("arena_match") as batch:
            if _OLD_UQ in uniques:
                batch.drop_constraint(_OLD_UQ, type_="unique")
            batch.create_unique_constraint(_NEW_UQ, _NEW_COLS)


def downgrade() -> None:
    if "arena_match" not in _tables():
        return

    uniques = _uniques("arena_match")
    if _OLD_UQ not in uniques:
        with op.batch_alter_table("arena_match") as batch:
            if _NEW_UQ in uniques:
                batch.drop_constraint(_NEW_UQ, type_="unique")
            batch.create_unique_constraint(_OLD_UQ, _OLD_COLS)

    if "reasoning_effort" in _columns("arena_match"):
        with op.batch_alter_table("arena_match") as batch:
            batch.drop_column("reasoning_effort")
