from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def test_alembic_0025_backfills_position_kind(tmp_path: Path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'm.sqlite3'}")
    metadata = sa.MetaData()
    sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_family", sa.String(40), nullable=False),
    )
    sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_trade_id", sa.String(160)),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("product_id", sa.Integer()),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO products (id, product_family) VALUES (1, 'futures'), (2, 'option')"))
        conn.execute(
            sa.text(
                """
                INSERT INTO positions (id, source_trade_id, source_payload, product_id)
                VALUES
                    (1, 'HEDGE:1:1', '{}', 2),
                    (2, 'CLIENT-1', '{"hedge": {"is_hedge": true}}', 2),
                    (3, 'FUT-1', '{}', 1),
                    (4, 'OTC-1', '{}', 2)
                """
            )
        )

    migration = importlib.import_module("backend.alembic.versions.0025_position_kind")
    connection = engine.connect()
    original_op = migration.op
    migration.op = Operations(MigrationContext.configure(connection))
    try:
        migration.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("positions")}
    assert "position_kind" in columns
    with engine.connect() as conn:
        rows = dict(conn.execute(sa.text("SELECT id, position_kind FROM positions")).all())
    assert rows == {1: "listed", 2: "listed", 3: "listed", 4: "otc"}
