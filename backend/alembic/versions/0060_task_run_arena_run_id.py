"""task_runs.arena_run_id — link an arena board to the task driving it

Revision ID: 0060_task_run_arena_run_id
Revises: 0059_arena_output_budget_variant

An arena run had **no link at all** to its `task_runs` row — not a column, not
even a hint in `description`. Two consequences, both met in practice:

* Nothing could cancel a run. `_execute` honours `cancel_requested`, but that
  flag lives on the task, and given a run id there was no way to find it. The
  only way to stop a board was to find and kill its process — and the task row
  OUTLIVES the process, so a worker could pick the run up and silently resume it
  (run #121: killed at the launcher, resumed by a `--reload` dev server at a
  different recursion limit).
* A stuck run could only be matched to its task by hand, by comparing timestamps.

`task_runs` already carries exactly this shape for seven other domains
(`portfolio_id`, `risk_run_id`, `greeks_landscape_run_id`, `scenario_test_run_id`,
`backtest_run_id`, `report_job_id`, `limit_monitoring_run_id`), so this follows
the established column pattern rather than inventing a second link mechanism.

`ondelete="SET NULL"`, not CASCADE: deleting an arena run must not delete the
record of the task that ran it. `store.delete_runs` already nulls dangling
`agent_threads.arena_run_id` for the same reason.

Historical rows stay NULL and are uncancellable — correct, since every one of
them is already terminal.

IDEMPOTENT: `0001_initial` materialises the live ORM metadata, so a fresh
database already has the column and its index.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0060_task_run_arena_run_id"
down_revision = "0059_arena_output_budget_variant"
branch_labels = None
depends_on = None

_IX = "ix_task_runs_arena_run_id"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "task_runs" not in _tables():
        return

    if "arena_run_id" not in _columns("task_runs"):
        # add_column is one of the few ops SQLite takes natively, so no batch
        # rebuild is needed here (unlike a constraint change or a DROP COLUMN).
        op.add_column(
            "task_runs", sa.Column("arena_run_id", sa.Integer(), nullable=True)
        )

    if _IX not in _indexes("task_runs"):
        op.create_index(_IX, "task_runs", ["arena_run_id"])


def downgrade() -> None:
    if "task_runs" not in _tables():
        return

    if _IX in _indexes("task_runs"):
        op.drop_index(_IX, table_name="task_runs")

    if "arena_run_id" in _columns("task_runs"):
        # batch mode, never a direct drop_column: a create_all database hands the
        # column its ORM foreign key, and SQLite refuses DROP COLUMN while any FK
        # definition names it.
        with op.batch_alter_table("task_runs") as batch:
            batch.drop_column("arena_run_id")
