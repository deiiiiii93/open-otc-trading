from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def test_alembic_0019_promotes_legacy_underlying_defaults(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    sa.Table(
        "underlying_pricing_defaults",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying", sa.String(80), nullable=False),
        sa.Column("rate", sa.Float()),
        sa.Column("dividend_yield", sa.Float()),
        sa.Column("volatility", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying", sa.String(80), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        now = datetime(2026, 5, 28)
        connection.execute(
            text(
                "INSERT INTO underlying_pricing_defaults "
                "(underlying, rate, dividend_yield, volatility, notes, created_at, updated_at) "
                "VALUES ('000300.SH', 0.025, 0.01, 0.2, 'baseline', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(text("INSERT INTO positions (underlying) VALUES ('000852.SH')"))

    migration = importlib.import_module(
        "backend.alembic.versions.0019_underlying_master_data"
    )
    connection = engine.connect()
    context = MigrationContext.configure(connection)
    original_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    inspector = inspect(engine)
    assert "underlyings" in inspector.get_table_names()
    assert "underlying_id" in {column["name"] for column in inspector.get_columns("positions")}
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT symbol, status, rate, notes FROM underlyings ORDER BY symbol")
        ).mappings().all()
        assert [row["symbol"] for row in rows] == ["000300.SH", "000852.SH"]
        assert rows[0]["status"] == "active"
        assert rows[0]["rate"] == 0.025
        assert rows[0]["notes"] == "baseline"
        linked = connection.execute(text("SELECT underlying_id FROM positions")).scalar_one()
        assert linked is not None
