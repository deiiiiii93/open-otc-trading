"""scope risk-limit incident episodes by portfolio

Revision ID: 0047_limit_incident_portfolio
Revises: 0046_risk_limits_core
Create Date: 2026-07-17

The original active-episode identity in 0046 was only
``(risk_limit_id, scope_key)``.  A shared underlying or product-family limit
could therefore merge breaches from different portfolios.  This forward
migration preserves unambiguous incident history, derives its portfolio from
first, last, and event-linked evaluations, refuses mixed-portfolio ledgers,
and then makes portfolio identity mandatory.

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0047_limit_incident_portfolio"
down_revision = "0046_risk_limits_core"
branch_labels = None
depends_on = None

_ACTIVE_EPISODE_PREDICATE = "status IN ('open', 'acknowledged', 'assigned', 'waived')"
_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> dict[str, dict]:
    return {
        index["name"]: index for index in inspect(op.get_bind()).get_indexes(table_name)
    }


def _has_portfolio_foreign_key() -> bool:
    return any(
        foreign_key["constrained_columns"] == ["portfolio_id"]
        and foreign_key["referred_table"] == "portfolios"
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in inspect(op.get_bind()).get_foreign_keys("limit_incidents")
    )


def _validate_portfolio_evidence() -> None:
    conflicts = (
        op.get_bind()
        .execute(
            text(
                """
            SELECT incidents.id
            FROM limit_incidents AS incidents
            JOIN (
                SELECT id AS incident_id,
                       first_evaluation_id AS evaluation_id
                FROM limit_incidents
                WHERE first_evaluation_id IS NOT NULL
                UNION
                SELECT id AS incident_id,
                       last_evaluation_id AS evaluation_id
                FROM limit_incidents
                WHERE last_evaluation_id IS NOT NULL
                UNION
                SELECT incident_id, evaluation_id
                FROM limit_incident_events
                WHERE evaluation_id IS NOT NULL
            ) AS links
              ON links.incident_id = incidents.id
            JOIN limit_evaluations AS evaluations
              ON evaluations.id = links.evaluation_id
            JOIN limit_monitoring_runs AS monitoring_runs
              ON monitoring_runs.id = evaluations.monitoring_run_id
            GROUP BY incidents.id, incidents.portfolio_id
            HAVING COUNT(DISTINCT monitoring_runs.portfolio_id) > 1
                OR (
                    incidents.portfolio_id IS NOT NULL
                    AND MIN(monitoring_runs.portfolio_id)
                        <> incidents.portfolio_id
                )
            ORDER BY incidents.id
            LIMIT 10
            """
            )
        )
        .scalars()
        .all()
    )
    if conflicts:
        joined = ", ".join(str(value) for value in conflicts)
        raise RuntimeError(
            "limit incident history contains conflicting portfolio evidence; "
            f"split or repair these incidents before retrying: {joined}"
        )


def _backfill_portfolio_id() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE limit_incidents
            SET portfolio_id = COALESCE(
                (
                    SELECT MIN(monitoring_runs.portfolio_id)
                    FROM limit_evaluations AS evaluations
                    JOIN limit_monitoring_runs AS monitoring_runs
                      ON monitoring_runs.id = evaluations.monitoring_run_id
                    WHERE evaluations.id IN (
                        limit_incidents.last_evaluation_id,
                        limit_incidents.first_evaluation_id
                    )
                ),
                (
                    SELECT MIN(monitoring_runs.portfolio_id)
                    FROM limit_incident_events AS events
                    JOIN limit_evaluations AS evaluations
                      ON evaluations.id = events.evaluation_id
                    JOIN limit_monitoring_runs AS monitoring_runs
                      ON monitoring_runs.id = evaluations.monitoring_run_id
                    WHERE events.incident_id = limit_incidents.id
                )
            )
            WHERE portfolio_id IS NULL
            """
        )
    )
    unresolved = (
        bind.execute(
            text(
                "SELECT id FROM limit_incidents "
                "WHERE portfolio_id IS NULL ORDER BY id LIMIT 10"
            )
        )
        .scalars()
        .all()
    )
    if unresolved:
        joined = ", ".join(str(value) for value in unresolved)
        raise RuntimeError(
            "cannot infer portfolio_id for limit incident history; "
            f"repair incident evaluation links before retrying: {joined}"
        )


def upgrade() -> None:
    required = {
        "portfolios",
        "limit_monitoring_runs",
        "limit_evaluations",
        "limit_incidents",
        "limit_incident_events",
    }
    missing = required - _tables()
    if missing:
        raise RuntimeError(
            "cannot scope limit incidents without prerequisite tables: "
            + ", ".join(sorted(missing))
        )

    columns = _columns("limit_incidents")
    if "portfolio_id" not in columns:
        with op.batch_alter_table(
            "limit_incidents",
            naming_convention=_NAMING_CONVENTION,
        ) as batch:
            batch.add_column(sa.Column("portfolio_id", sa.Integer(), nullable=True))

    _validate_portfolio_evidence()
    _backfill_portfolio_id()

    indexes = _indexes("limit_incidents")
    if "uq_limit_incidents_active_episode" in indexes:
        op.drop_index(
            "uq_limit_incidents_active_episode",
            table_name="limit_incidents",
        )

    columns = _columns("limit_incidents")
    needs_not_null = columns["portfolio_id"].get("nullable", True)
    needs_foreign_key = not _has_portfolio_foreign_key()
    if needs_not_null or needs_foreign_key:
        with op.batch_alter_table(
            "limit_incidents",
            naming_convention=_NAMING_CONVENTION,
        ) as batch:
            if needs_not_null:
                batch.alter_column(
                    "portfolio_id",
                    existing_type=sa.Integer(),
                    nullable=False,
                )
            if needs_foreign_key:
                batch.create_foreign_key(
                    "fk_limit_incidents_portfolio_id_portfolios",
                    "portfolios",
                    ["portfolio_id"],
                    ["id"],
                )

    indexes = _indexes("limit_incidents")
    if "ix_limit_incidents_portfolio_id" not in indexes:
        op.create_index(
            "ix_limit_incidents_portfolio_id",
            "limit_incidents",
            ["portfolio_id"],
        )
    if "uq_limit_incidents_active_episode" not in indexes:
        predicate = sa.text(_ACTIVE_EPISODE_PREDICATE)
        op.create_index(
            "uq_limit_incidents_active_episode",
            "limit_incidents",
            ["portfolio_id", "risk_limit_id", "scope_key"],
            unique=True,
            sqlite_where=predicate,
            postgresql_where=predicate,
        )


def downgrade() -> None:
    if "limit_incidents" not in _tables():
        return
    columns = _columns("limit_incidents")
    if "portfolio_id" not in columns:
        return

    duplicate = (
        op.get_bind()
        .execute(
            text(
                "SELECT risk_limit_id, scope_key "
                "FROM limit_incidents "
                f"WHERE {_ACTIVE_EPISODE_PREDICATE} "
                "GROUP BY risk_limit_id, scope_key "
                "HAVING COUNT(*) > 1 "
                "LIMIT 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "cannot downgrade portfolio-scoped incident history while "
            "multiple portfolios have active episodes for one limit scope"
        )

    indexes = _indexes("limit_incidents")
    for index_name in (
        "uq_limit_incidents_active_episode",
        "ix_limit_incidents_portfolio_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="limit_incidents")

    with op.batch_alter_table(
        "limit_incidents",
        naming_convention=_NAMING_CONVENTION,
    ) as batch:
        batch.drop_column("portfolio_id")

    predicate = sa.text(_ACTIVE_EPISODE_PREDICATE)
    op.create_index(
        "uq_limit_incidents_active_episode",
        "limit_incidents",
        ["risk_limit_id", "scope_key"],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )
