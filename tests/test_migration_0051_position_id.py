"""Migration 0051 must work on a create_all-seeded database, in both directions.

`0001_initial` materialises live ORM metadata, so on the documented
empty-database path `pricing_parameter_rows.position_id` already exists — and,
unlike the bare column a real historical 0051 added, it arrives carrying a
foreign key to `positions`. SQLite refuses `ALTER TABLE ... DROP COLUMN` while
any foreign-key definition names the column, so the downgrade needs a batch
table rebuild.

A rebuild is precisely where sibling constraints, indexes and rows get silently
dropped, so these tests pin all three rather than only the column.
"""
from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


_TABLE = "pricing_parameter_rows"


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0051_pricing_parameter_row_position_id"
    )


def _run(module, method: str, engine: sa.Engine) -> None:
    with engine.connect() as connection:
        original = module.op
        module.op = Operations(MigrationContext.configure(connection))
        try:
            getattr(module, method)()
            connection.commit()
        finally:
            module.op = original


def _create_all_engine(tmp_path: Path, name: str) -> sa.Engine:
    """A database seeded the way `0001_initial` seeds one: from ORM metadata."""
    from app.models import Base

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    Base.metadata.create_all(bind=engine)
    return engine


def _seed_row(engine: sa.Engine) -> None:
    now = datetime(2026, 8, 11, 12, 0, 0)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO pricing_parameter_profiles "
                "(id, name, valuation_date, source_type, status, summary, "
                " created_at, updated_at) "
                "VALUES (1, 'p', :now, 'xlsx', 'completed', '{}', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO pricing_parameter_rows "
                "(id, profile_id, source_trade_id, symbol, position_id, rate, "
                " volatility, created_at, updated_at) "
                "VALUES (7, 1, 'T-1', 'AAPL', 42, 0.025, 0.2, :now, :now)"
            ),
            {"now": now},
        )


def _fks(engine: sa.Engine) -> set[tuple[str, str]]:
    return {
        (tuple(fk["constrained_columns"])[0], fk["referred_table"])
        for fk in sa.inspect(engine).get_foreign_keys(_TABLE)
    }


def _indexes(engine: sa.Engine) -> set[str]:
    return {i["name"] for i in sa.inspect(engine).get_indexes(_TABLE) if i.get("name")}


def _columns(engine: sa.Engine) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(_TABLE)}


def test_downgrade_drops_position_id_from_a_create_all_schema(tmp_path: Path) -> None:
    """The plain `op.drop_column` this replaced raised OperationalError here:
    'unknown column "position_id" in foreign key definition'."""
    engine = _create_all_engine(tmp_path, "drop.sqlite3")
    assert "position_id" in _columns(engine)
    assert ("position_id", "positions") in _fks(engine)

    _run(_migration(), "downgrade", engine)

    assert "position_id" not in _columns(engine)
    assert ("position_id", "positions") not in _fks(engine)
    assert "ix_pricing_parameter_rows_position_id" not in _indexes(engine)


def test_downgrade_preserves_sibling_constraints_indexes_and_rows(
    tmp_path: Path,
) -> None:
    """A table rebuild must remove ONLY position_id. Everything else on the
    table — the other two foreign keys, the other four indexes, and the data —
    has to come through untouched."""
    engine = _create_all_engine(tmp_path, "siblings.sqlite3")
    _seed_row(engine)

    _run(_migration(), "downgrade", engine)

    assert _fks(engine) == {
        ("profile_id", "pricing_parameter_profiles"),
        ("instrument_id", "instruments"),
    }
    assert {
        "ix_pricing_parameter_rows_profile_id",
        "ix_pricing_parameter_rows_source_trade_id",
        "ix_pricing_parameter_rows_symbol",
        "ix_pricing_parameter_rows_instrument_id",
    } <= _indexes(engine)

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT id, profile_id, source_trade_id, symbol, rate, volatility "
                f"FROM {_TABLE}"
            )
        ).mappings().one()
    assert row["id"] == 7
    assert row["profile_id"] == 1
    assert row["source_trade_id"] == "T-1"
    assert row["symbol"] == "AAPL"
    assert row["rate"] == 0.025
    assert row["volatility"] == 0.2


def test_round_trip_restores_the_column_and_its_index(tmp_path: Path) -> None:
    engine = _create_all_engine(tmp_path, "roundtrip.sqlite3")
    _seed_row(engine)
    module = _migration()

    _run(module, "downgrade", engine)
    _run(module, "upgrade", engine)

    assert "position_id" in _columns(engine)
    assert "ix_pricing_parameter_rows_position_id" in _indexes(engine)
    with engine.connect() as connection:
        # The row survives; position_id is legitimately NULL — the downgrade
        # dropped that value and the upgrade has no way to reconstruct it.
        assert connection.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar() == 1
        assert connection.execute(
            sa.text(f"SELECT position_id FROM {_TABLE} WHERE id = 7")
        ).scalar() is None


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path: Path) -> None:
    """The guard that keeps `alembic upgrade head` alive on a fresh database."""
    engine = _create_all_engine(tmp_path, "idempotent.sqlite3")

    _run(_migration(), "upgrade", engine)

    assert "position_id" in _columns(engine)
    assert "ix_pricing_parameter_rows_position_id" in _indexes(engine)
