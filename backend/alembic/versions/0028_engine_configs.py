"""engine config variants

Revision ID: 0028_engine_configs
Revises: 0027_backtest_runs
Create Date: 2026-06-10
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0028_engine_configs"
down_revision = "0027_backtest_runs"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


DEFAULT_RULES = {
    "rules": [
        {"name": "Autocallables", "match": {"product_family": "autocallables"}, "pricing": {"engine_type": "QUAD"}},
        {"name": "Others", "match": {"product_family": "others"}, "pricing": {"engine_type": "ANALYTICAL"}},
        {"name": "Futures", "match": {"product_type": "Futures"}, "pricing": {"engine_name": "DeltaOneEngine", "engine_kwargs": {}}},
        {"name": "SpotInstrument", "match": {"product_type": "SpotInstrument"}, "pricing": {"engine_name": "DeltaOneEngine", "engine_kwargs": {}}},
    ]
}


def upgrade() -> None:
    if not _has_table("engine_config_variants"):
        op.create_table(
            "engine_config_variants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    for table in ("position_valuation_runs", "risk_runs", "scenario_test_runs", "backtest_runs"):
        if table in inspect(op.get_bind()).get_table_names() and "engine_config_id" not in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("engine_config_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(f"fk_{table}_engine_config_id", "engine_config_variants", ["engine_config_id"], ["id"])
            op.create_index(f"ix_{table}_engine_config_id", table, ["engine_config_id"])
    bind = op.get_bind()
    exists = bind.execute(text("SELECT id FROM engine_config_variants WHERE is_default = 1 LIMIT 1")).first()
    if exists is None:
        bind.execute(
            text("INSERT INTO engine_config_variants (name, description, status, is_default, rules, created_at, updated_at) VALUES (:name, :description, 'active', 1, :rules, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"name": "System Default", "description": "Default engine rules matching current product mappings.", "rules": json.dumps(DEFAULT_RULES)},
        )


def downgrade() -> None:
    for table in ("backtest_runs", "scenario_test_runs", "risk_runs", "position_valuation_runs"):
        if table in inspect(op.get_bind()).get_table_names() and "engine_config_id" in _columns(table):
            op.drop_index(f"ix_{table}_engine_config_id", table_name=table)
            with op.batch_alter_table(table) as batch:
                batch.drop_column("engine_config_id")
    if _has_table("engine_config_variants"):
        op.drop_table("engine_config_variants")
