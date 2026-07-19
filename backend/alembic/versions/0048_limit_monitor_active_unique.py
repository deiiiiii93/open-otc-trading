"""enforce one active limit-monitoring run per portfolio

Revision ID: 0048_limit_monitor_active_unique
Revises: 0047_limit_incident_portfolio
Create Date: 2026-07-19

The partial unique index is the cross-process authority. UI state and
pre-insert service checks improve feedback, but cannot prevent two humans,
agents, or browser tabs from racing each other.

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0048_limit_monitor_active_unique"
down_revision = "0047_limit_incident_portfolio"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_limit_monitoring_runs_active_portfolio"
_ACTIVE_PREDICATE = "status IN ('queued', 'running')"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(table_name)
    }


def _validate_single_active_run() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT portfolio_id "
                "FROM limit_monitoring_runs "
                f"WHERE {_ACTIVE_PREDICATE} "
                "GROUP BY portfolio_id "
                "HAVING COUNT(*) > 1 "
                "ORDER BY portfolio_id "
                "LIMIT 10"
            )
        )
        .scalars()
        .all()
    )
    if duplicates:
        joined = ", ".join(str(value) for value in duplicates)
        raise RuntimeError(
            "multiple active limit monitoring runs exist for portfolios: "
            f"{joined}; finish or fail all but one run before retrying"
        )


def upgrade() -> None:
    if "limit_monitoring_runs" not in _tables():
        raise RuntimeError(
            "cannot enforce active monitoring uniqueness without "
            "limit_monitoring_runs"
        )
    required = {"id", "portfolio_id", "status"}
    missing = required - _columns("limit_monitoring_runs")
    if missing:
        raise RuntimeError(
            "limit_monitoring_runs is missing required columns: "
            + ", ".join(sorted(missing))
        )

    _validate_single_active_run()
    if _INDEX_NAME not in _indexes("limit_monitoring_runs"):
        predicate = sa.text(_ACTIVE_PREDICATE)
        op.create_index(
            _INDEX_NAME,
            "limit_monitoring_runs",
            ["portfolio_id"],
            unique=True,
            sqlite_where=predicate,
            postgresql_where=predicate,
        )


def downgrade() -> None:
    if (
        "limit_monitoring_runs" in _tables()
        and _INDEX_NAME in _indexes("limit_monitoring_runs")
    ):
        op.drop_index(_INDEX_NAME, table_name="limit_monitoring_runs")
