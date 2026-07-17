"""core persistence for versioned risk limits and monitoring evidence

Revision ID: 0046_risk_limits_core
Revises: 0045_arena_run_trials
Create Date: 2026-07-17

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0046_risk_limits_core"
down_revision = "0045_arena_run_trials"
branch_labels = None
depends_on = None

_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(table_name)
    }


def _has_foreign_key(
    table_name: str,
    constrained_columns: list[str],
    referred_table: str,
    referred_columns: list[str],
) -> bool:
    if table_name not in _tables():
        return False
    return any(
        foreign_key["constrained_columns"] == constrained_columns
        and foreign_key["referred_table"] == referred_table
        and foreign_key["referred_columns"] == referred_columns
        for foreign_key in inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _create_index(
    name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    sqlite_where: sa.TextClause | None = None,
) -> None:
    if name in _indexes(table_name):
        return
    kwargs: dict[str, object] = {"unique": unique}
    if sqlite_where is not None:
        kwargs["sqlite_where"] = sqlite_where
        kwargs["postgresql_where"] = sqlite_where
    op.create_index(name, table_name, columns, **kwargs)


def _repair_memory_dedup_postgresql() -> None:
    """Forward-repair the pre-fix full unique index shipped before 0046."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if "memory_entries" not in inspector.get_table_names():
        return
    existing = next(
        (
            index
            for index in inspector.get_indexes("memory_entries")
            if index["name"] == "ux_memory_dedup"
        ),
        None,
    )
    dialect_options = (existing or {}).get("dialect_options") or {}
    predicate = dialect_options.get("postgresql_where")
    if predicate is None and existing is not None:
        predicate = existing.get("postgresql_where")
    normalized_predicate = str(predicate or "").lower()
    if (
        "status" in normalized_predicate
        and "archived" in normalized_predicate
        and ("!=" in normalized_predicate or "<>" in normalized_predicate)
    ):
        return
    if existing is not None:
        op.drop_index("ux_memory_dedup", table_name="memory_entries")
    op.create_index(
        "ux_memory_dedup",
        "memory_entries",
        ["scope_type", "scope_id", "normalized_content"],
        unique=True,
        postgresql_where=sa.text("status != 'archived'"),
    )


def upgrade() -> None:
    _repair_memory_dedup_postgresql()

    if "risk_limits" not in _tables():
        op.create_table(
            "risk_limits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(120), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("category", sa.String(32), nullable=False),
            sa.Column("owner", sa.String(120), nullable=False),
            sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("active_version_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_by_actor",
                sa.String(120),
                nullable=False,
                server_default="system",
            ),
            sa.Column("created_by_persona", sa.String(40), nullable=True),
            sa.Column(
                "row_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("key", name="uq_risk_limits_key"),
        )

    if "risk_limit_versions" not in _tables():
        op.create_table(
            "risk_limit_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "risk_limit_id",
                sa.Integer(),
                sa.ForeignKey("risk_limits.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "state",
                sa.String(24),
                nullable=False,
                server_default="draft",
            ),
            sa.Column("metric_kind", sa.String(24), nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column(
                "methodology",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column(
                "scope_config",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("aggregation", sa.String(24), nullable=False),
            sa.Column("transform", sa.String(24), nullable=False),
            sa.Column("comparator", sa.String(16), nullable=False),
            sa.Column("warning_lower", sa.Float(), nullable=True),
            sa.Column("warning_upper", sa.Float(), nullable=True),
            sa.Column("hard_lower", sa.Float(), nullable=True),
            sa.Column("hard_upper", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(40), nullable=False),
            sa.Column("currency", sa.String(16), nullable=True),
            sa.Column("bump_convention", sa.String(80), nullable=True),
            sa.Column(
                "freshness_policy",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("effective_from", sa.DateTime(), nullable=True),
            sa.Column("effective_until", sa.DateTime(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column(
                "created_by_actor",
                sa.String(120),
                nullable=False,
                server_default="system",
            ),
            sa.Column("created_by_persona", sa.String(40), nullable=True),
            sa.Column("created_in_mode", sa.String(16), nullable=True),
            sa.Column("created_in_thread_id", sa.Integer(), nullable=True),
            sa.Column("activated_by_actor", sa.String(120), nullable=True),
            sa.Column("activated_by_persona", sa.String(40), nullable=True),
            sa.Column("activated_in_mode", sa.String(16), nullable=True),
            sa.Column("activated_in_thread_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "risk_limit_id",
                "version",
                name="uq_risk_limit_versions_limit_version",
            ),
        )

    if "limit_monitoring_runs" not in _tables():
        op.create_table(
            "limit_monitoring_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("trigger", sa.String(24), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("schedule_id", sa.Integer(), nullable=True),
            sa.Column("occurrence_id", sa.Integer(), nullable=True),
            sa.Column(
                "portfolio_id",
                sa.Integer(),
                sa.ForeignKey("portfolios.id"),
                nullable=False,
            ),
            sa.Column(
                "pricing_parameter_profile_id",
                sa.Integer(),
                sa.ForeignKey("pricing_parameter_profiles.id"),
                nullable=True,
            ),
            sa.Column(
                "engine_config_id",
                sa.Integer(),
                sa.ForeignKey("engine_config_variants.id"),
                nullable=True,
            ),
            sa.Column(
                "market_snapshot_id",
                sa.Integer(),
                sa.ForeignKey("market_snapshots.id"),
                nullable=True,
            ),
            sa.Column("valuation_as_of", sa.DateTime(), nullable=False),
            sa.Column("source_policy", sa.String(24), nullable=False),
            sa.Column("max_source_age_seconds", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                sa.String(40),
                nullable=False,
                server_default="queued",
            ),
            sa.Column(
                "summary",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "definition_snapshot",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "definition_snapshot_hash",
                sa.String(64),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if "limit_monitoring_run_versions" not in _tables():
        op.create_table(
            "limit_monitoring_run_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "monitoring_run_id",
                sa.Integer(),
                sa.ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "limit_version_id",
                sa.Integer(),
                sa.ForeignKey("risk_limit_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "monitoring_run_id",
                "limit_version_id",
                name="uq_limit_monitoring_run_versions_run_version",
            ),
        )

    if "limit_source_references" not in _tables():
        op.create_table(
            "limit_source_references",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "monitoring_run_id",
                sa.Integer(),
                sa.ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column(
                "risk_run_id",
                sa.Integer(),
                sa.ForeignKey("risk_runs.id"),
                nullable=True,
            ),
            sa.Column(
                "scenario_test_run_id",
                sa.Integer(),
                sa.ForeignKey("scenario_test_runs.id"),
                nullable=True,
            ),
            sa.Column(
                "backtest_run_id",
                sa.Integer(),
                sa.ForeignKey("backtest_runs.id"),
                nullable=True,
            ),
            sa.Column(
                "requested_parameters",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("source_status", sa.String(40), nullable=False),
            sa.Column(
                "is_fresh",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "completeness_diagnostics",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("source_valuation_at", sa.DateTime(), nullable=True),
            sa.Column("source_created_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if "limit_evaluations" not in _tables():
        op.create_table(
            "limit_evaluations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "monitoring_run_id",
                sa.Integer(),
                sa.ForeignKey("limit_monitoring_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "limit_version_id",
                sa.Integer(),
                sa.ForeignKey("risk_limit_versions.id"),
                nullable=False,
            ),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("scope_key", sa.String(200), nullable=False),
            sa.Column("scope_label", sa.String(200), nullable=False),
            sa.Column("observed_value", sa.Float(), nullable=True),
            sa.Column("adverse_value", sa.Float(), nullable=True),
            sa.Column("warning_lower", sa.Float(), nullable=True),
            sa.Column("warning_upper", sa.Float(), nullable=True),
            sa.Column("hard_lower", sa.Float(), nullable=True),
            sa.Column("hard_upper", sa.Float(), nullable=True),
            sa.Column("utilization", sa.Float(), nullable=True),
            sa.Column("headroom", sa.Float(), nullable=True),
            sa.Column("governing_boundary", sa.String(16), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("reason_code", sa.String(64), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("coverage_count", sa.Integer(), nullable=True),
            sa.Column("coverage_ratio", sa.Float(), nullable=True),
            sa.Column(
                "evidence",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "evaluated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "monitoring_run_id",
                "limit_version_id",
                "scope_key",
                name="uq_limit_evaluations_run_version_scope",
            ),
        )

    if "limit_incidents" not in _tables():
        op.create_table(
            "limit_incidents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "risk_limit_id",
                sa.Integer(),
                sa.ForeignKey("risk_limits.id"),
                nullable=False,
            ),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("scope_key", sa.String(200), nullable=False),
            sa.Column("scope_label", sa.String(200), nullable=False),
            sa.Column("severity", sa.String(16), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column(
                "first_evaluation_id",
                sa.Integer(),
                sa.ForeignKey("limit_evaluations.id"),
                nullable=True,
            ),
            sa.Column(
                "last_evaluation_id",
                sa.Integer(),
                sa.ForeignKey("limit_evaluations.id"),
                nullable=True,
            ),
            sa.Column(
                "first_seen_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "last_seen_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
            sa.Column("waived_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("owner", sa.String(120), nullable=True),
            sa.Column("assignee", sa.String(120), nullable=True),
            sa.Column("waiver_expires_at", sa.DateTime(), nullable=True),
            sa.Column("waiver_rationale", sa.Text(), nullable=True),
            sa.Column(
                "row_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if "limit_incident_events" not in _tables():
        op.create_table(
            "limit_incident_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "incident_id",
                sa.Integer(),
                sa.ForeignKey("limit_incidents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column(
                "evaluation_id",
                sa.Integer(),
                sa.ForeignKey("limit_evaluations.id"),
                nullable=True,
            ),
            sa.Column("actor", sa.String(120), nullable=False),
            sa.Column("persona", sa.String(40), nullable=True),
            sa.Column("mode", sa.String(16), nullable=True),
            sa.Column("thread_id", sa.Integer(), nullable=True),
            sa.Column("audit_ref", sa.String(80), nullable=True),
            sa.Column(
                "payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    task_runs_has_link = (
        "task_runs" in _tables()
        and "limit_monitoring_run_id" in _columns("task_runs")
    )
    task_runs_has_link_fk = task_runs_has_link and _has_foreign_key(
        "task_runs",
        ["limit_monitoring_run_id"],
        "limit_monitoring_runs",
        ["id"],
    )
    if "task_runs" in _tables() and (
        not task_runs_has_link or not task_runs_has_link_fk
    ):
        with op.batch_alter_table(
            "task_runs",
            naming_convention=_NAMING_CONVENTION,
        ) as batch:
            if not task_runs_has_link:
                batch.add_column(
                    sa.Column(
                        "limit_monitoring_run_id",
                        sa.Integer(),
                        nullable=True,
                    )
                )
            if not task_runs_has_link_fk:
                batch.create_foreign_key(
                    "fk_task_runs_limit_monitoring_run_id_limit_monitoring_runs",
                    "limit_monitoring_runs",
                    ["limit_monitoring_run_id"],
                    ["id"],
                )

    _create_index(
        "ix_risk_limits_active_version_id",
        "risk_limits",
        ["active_version_id"],
    )
    _create_index(
        "ix_risk_limit_versions_risk_limit_id",
        "risk_limit_versions",
        ["risk_limit_id"],
    )
    _create_index(
        "ix_risk_limit_versions_state",
        "risk_limit_versions",
        ["state"],
    )
    _create_index(
        "ix_limit_monitoring_runs_portfolio_id",
        "limit_monitoring_runs",
        ["portfolio_id"],
    )
    _create_index(
        "ix_limit_monitoring_runs_status",
        "limit_monitoring_runs",
        ["status"],
    )
    _create_index(
        "ix_limit_monitoring_run_versions_monitoring_run_id",
        "limit_monitoring_run_versions",
        ["monitoring_run_id"],
    )
    _create_index(
        "ix_limit_source_references_monitoring_run_id",
        "limit_source_references",
        ["monitoring_run_id"],
    )
    _create_index(
        "ix_limit_source_references_source_kind",
        "limit_source_references",
        ["source_kind"],
    )
    _create_index(
        "ix_limit_evaluations_monitoring_run_id",
        "limit_evaluations",
        ["monitoring_run_id"],
    )
    _create_index(
        "ix_limit_evaluations_status",
        "limit_evaluations",
        ["status"],
    )
    _create_index(
        "ix_limit_incidents_risk_limit_id",
        "limit_incidents",
        ["risk_limit_id"],
    )
    _create_index(
        "ix_limit_incidents_status",
        "limit_incidents",
        ["status"],
    )
    _create_index(
        "uq_limit_incidents_active_episode",
        "limit_incidents",
        ["risk_limit_id", "scope_key"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('open', 'acknowledged', 'assigned', 'waived')"
        ),
    )
    _create_index(
        "ix_limit_incident_events_incident_id",
        "limit_incident_events",
        ["incident_id"],
    )
    _create_index(
        "ix_task_runs_limit_monitoring_run_id",
        "task_runs",
        ["limit_monitoring_run_id"],
    )


def downgrade() -> None:
    if "task_runs" in _tables():
        if "ix_task_runs_limit_monitoring_run_id" in _indexes("task_runs"):
            op.drop_index(
                "ix_task_runs_limit_monitoring_run_id",
                table_name="task_runs",
            )
        if "limit_monitoring_run_id" in _columns("task_runs"):
            with op.batch_alter_table(
                "task_runs",
                naming_convention=_NAMING_CONVENTION,
            ) as batch:
                batch.drop_column("limit_monitoring_run_id")

    for table_name in (
        "limit_incident_events",
        "limit_incidents",
        "limit_evaluations",
        "limit_source_references",
        "limit_monitoring_run_versions",
        "limit_monitoring_runs",
        "risk_limit_versions",
        "risk_limits",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
