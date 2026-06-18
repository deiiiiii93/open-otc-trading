from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app import database
from app.config import Settings
from app.models import Portfolio, Position
from app.services.domains.products import backfill_position_products


PRODUCT_TABLES = {
    "products",
    "equity_option_products",
    "equity_autocallable_products",
    "equity_autocallable_observations",
    "equity_phoenix_coupon_products",
    "equity_barrier_products",
    "equity_touch_products",
    "equity_asian_products",
    "equity_asian_observations",
    "equity_range_accrual_products",
    "equity_range_accrual_observations",
    "equity_sharkfin_products",
    "equity_spot_products",
    "equity_futures_products",
    "equity_product_components",
}


def test_product_tables_exist_after_metadata_create(session):
    bind = session.get_bind()
    tables = set(inspect(bind).get_table_names())
    assert PRODUCT_TABLES.issubset(tables)


def test_position_can_reference_product(session):
    portfolio = Portfolio(name="Book", base_currency="USD")
    session.add(portfolio)
    session.flush()

    position = Position(
        portfolio_id=portfolio.id,
        product_id=None,
        underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        status="open",
    )
    session.add(position)
    session.flush()

    assert position.id is not None
    assert position.product_id is None


def test_backfill_creates_one_product_per_legacy_position(session):
    portfolio = Portfolio(name="Legacy Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    first = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={"initial_price": 100.0, "strike": 100.0, "maturity": 1.0},
        engine_name="SnowballQuadEngine",
        engine_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        status="open",
    )
    second = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={"initial_price": 100.0, "strike": 100.0, "maturity": 1.0},
        engine_name="SnowballQuadEngine",
        engine_kwargs={},
        quantity=2.0,
        entry_price=0.0,
        status="open",
    )
    session.add_all([first, second])
    session.flush()

    count = backfill_position_products(session)
    session.flush()

    assert count == 2
    assert first.product_id is not None
    assert second.product_id is not None
    assert first.product_id != second.product_id


def test_alembic_0018_adds_product_fk_to_legacy_positions(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    sa.Table("positions", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    metadata.create_all(engine)

    migration = importlib.import_module(
        "backend.alembic.versions.0018_product_root_family_tables"
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

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    position_columns = {column["name"] for column in inspector.get_columns("positions")}
    position_indexes = {index["name"] for index in inspector.get_indexes("positions")}
    position_foreign_keys = inspector.get_foreign_keys("positions")

    assert PRODUCT_TABLES.issubset(tables)
    assert "product_id" in position_columns
    assert "ix_positions_product_id" in position_indexes
    assert any(
        foreign_key["referred_table"] == "products"
        and foreign_key["constrained_columns"] == ["product_id"]
        for foreign_key in position_foreign_keys
    )


def test_alembic_0018_backfills_existing_legacy_positions(tmp_path: Path):
    db_path = tmp_path / "migration-backfill.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    positions = sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("underlying", sa.String(80), nullable=False),
        sa.Column("product_type", sa.String(120), nullable=False),
        sa.Column("product_kwargs", sa.JSON(), nullable=False),
        sa.Column("engine_name", sa.String(120), nullable=False),
        sa.Column("engine_kwargs", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("source_trade_id", sa.String(160), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("mapping_status", sa.String(40), nullable=False),
        sa.Column("mapping_error", sa.Text(), nullable=True),
        sa.Column("source_payload", sa.JSON(), nullable=True),
        sa.Column("rfq_id", sa.Integer(), nullable=True),
        sa.Column("rfq_quote_version_id", sa.Integer(), nullable=True),
        sa.Column("trade_effective_date", sa.DateTime(), nullable=True),
        sa.Column("kwargs_migrated_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            positions.insert(),
            [
                {
                    "id": 1,
                    "portfolio_id": 1,
                    "underlying": "000300.SH",
                    "product_type": "SnowballOption",
                    "product_kwargs": {
                        "initial_price": 100.0,
                        "strike": 100.0,
                        "maturity": 1.0,
                    },
                    "engine_name": "SnowballQuadEngine",
                    "engine_kwargs": {},
                    "quantity": 1.0,
                    "entry_price": 0.0,
                    "status": "open",
                    "mapping_status": "manual",
                    "source_payload": {},
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": 2,
                    "portfolio_id": 1,
                    "underlying": "000300.SH",
                    "product_type": "SnowballOption",
                    "product_kwargs": {
                        "initial_price": 100.0,
                        "strike": 100.0,
                        "maturity": 1.0,
                    },
                    "engine_name": "SnowballQuadEngine",
                    "engine_kwargs": {},
                    "quantity": 2.0,
                    "entry_price": 0.0,
                    "status": "open",
                    "mapping_status": "manual",
                    "source_payload": {},
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    migration = importlib.import_module(
        "backend.alembic.versions.0018_product_root_family_tables"
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

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT id, product_id FROM positions ORDER BY id")
        ).all()
        product_count = connection.execute(sa.text("SELECT COUNT(*) FROM products")).scalar()

    assert product_count == 2
    assert rows[0].product_id is not None
    assert rows[1].product_id is not None
    assert rows[0].product_id != rows[1].product_id


def test_alembic_0018_backfills_when_product_id_already_exists(tmp_path: Path):
    db_path = tmp_path / "migration-backfill-existing-product-id.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    positions = _legacy_positions_table(metadata, include_product_id=True)
    metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            positions.insert().values(
                id=1,
                portfolio_id=1,
                underlying="000300.SH",
                product_type="SnowballOption",
                product_kwargs={
                    "initial_price": 100.0,
                    "strike": 100.0,
                    "maturity": 1.0,
                },
                engine_name="SnowballQuadEngine",
                engine_kwargs={},
                quantity=1.0,
                entry_price=0.0,
                status="open",
                mapping_status="manual",
                source_payload={},
                created_at=now,
                updated_at=now,
            )
        )

    _run_0018_upgrade(engine)

    with engine.connect() as connection:
        product_id = connection.execute(
            sa.text("SELECT product_id FROM positions WHERE id = 1")
        ).scalar()
        product_count = connection.execute(sa.text("SELECT COUNT(*) FROM products")).scalar()
    position_foreign_keys = sa.inspect(engine).get_foreign_keys("positions")

    assert product_id is not None
    assert product_count == 1
    assert any(
        foreign_key["referred_table"] == "products"
        and foreign_key["constrained_columns"] == ["product_id"]
        for foreign_key in position_foreign_keys
    )


def test_alembic_0018_skips_backfill_for_partial_position_schema(tmp_path: Path):
    db_path = tmp_path / "migration-partial-schema.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("underlying", sa.String(80), nullable=False),
        sa.Column("product_type", sa.String(120), nullable=False),
        sa.Column("product_kwargs", sa.JSON(), nullable=False),
        sa.Column("source_payload", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)

    _run_0018_upgrade(engine)

    inspector = sa.inspect(engine)
    assert "products" in set(inspector.get_table_names())


def test_local_sqlite_repair_adds_product_schema_without_rebuilding_positions(
    tmp_path: Path,
):
    db_path = tmp_path / "repair.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    positions = sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_trade_id", sa.String(160), nullable=True),
        sa.Column("source_payload", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(positions.insert().values(id=1))

    database.configure_database(
        Settings(
            database_url=f"sqlite+pysqlite:///{db_path}",
            artifact_dir=tmp_path / "artifacts",
            agent_checkpoint_db_path=":memory:",
        )
    )
    database.init_db()

    inspector = sa.inspect(database.engine)
    tables = set(inspector.get_table_names())
    position_columns = {column["name"] for column in inspector.get_columns("positions")}
    position_indexes = {index["name"] for index in inspector.get_indexes("positions")}

    with database.engine.connect() as connection:
        row_count = connection.execute(sa.text("SELECT COUNT(*) FROM positions")).scalar()

    assert PRODUCT_TABLES.issubset(tables)
    assert "product_id" in position_columns
    assert "ix_positions_product_id" in position_indexes
    assert row_count == 1


def _legacy_positions_table(
    metadata: sa.MetaData, *, include_product_id: bool = False
) -> sa.Table:
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
    ]
    if include_product_id:
        columns.append(sa.Column("product_id", sa.Integer(), nullable=True))
    columns.extend(
        [
            sa.Column("underlying", sa.String(80), nullable=False),
            sa.Column("product_type", sa.String(120), nullable=False),
            sa.Column("product_kwargs", sa.JSON(), nullable=False),
            sa.Column("engine_name", sa.String(120), nullable=False),
            sa.Column("engine_kwargs", sa.JSON(), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("source_trade_id", sa.String(160), nullable=True),
            sa.Column("source_row", sa.Integer(), nullable=True),
            sa.Column("mapping_status", sa.String(40), nullable=False),
            sa.Column("mapping_error", sa.Text(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("rfq_id", sa.Integer(), nullable=True),
            sa.Column("rfq_quote_version_id", sa.Integer(), nullable=True),
            sa.Column("trade_effective_date", sa.DateTime(), nullable=True),
            sa.Column("kwargs_migrated_at", sa.DateTime(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        ]
    )
    return sa.Table("positions", metadata, *columns)


def _run_0018_upgrade(engine: sa.Engine) -> None:
    migration = importlib.import_module(
        "backend.alembic.versions.0018_product_root_family_tables"
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
