from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    raw_path = database_url.split("///", 1)[-1]
    if raw_path in {":memory:", ""}:
        return
    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent(settings.database_url)


def _create_engine(database_url: str) -> Engine:
    sqlite = database_url.startswith("sqlite")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30.0} if sqlite else {},
    )
    if sqlite:
        _configure_sqlite_engine(engine)
    return engine


def _configure_sqlite_engine(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 30000")
            try:
                cursor.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                # Another local app process may already hold the SQLite file.
                # Keep the connection usable with busy_timeout; WAL can be set
                # on a later connection once the lock clears.
                pass
        finally:
            cursor.close()


engine = _create_engine(settings.database_url)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def configure_database(new_settings: Settings) -> None:
    global settings, engine, SessionLocal
    # Dispose the engine we're replacing so its connection pool is released (every
    # pooled connection closed) before we reassign ``engine``. Without this, a test
    # that leaves a session open then reconfigures the DB could carry a
    # 'prepared'/in-progress transaction forward on a still-pooled connection.
    old_engine = globals().get("engine")
    if old_engine is not None:
        try:
            old_engine.dispose()
        except Exception:
            pass
    settings = new_settings
    _ensure_sqlite_parent(settings.database_url)
    engine = _create_engine(settings.database_url)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def init_db() -> None:
    from . import models  # noqa: F401

    if engine.dialect.name == "sqlite":
        _existing_tables = inspect(engine).get_table_names()
        if "underlyings" in _existing_tables and "instruments" not in _existing_tables:
            raise RuntimeError(
                "Database schema predates the instrument-unification migration: "
                "found legacy 'underlyings' table. "
                "Run 'alembic upgrade head' before starting the app."
            )

    Base.metadata.create_all(bind=engine)
    _ensure_incremental_schema(engine)
    _backfill_missing_position_products(engine)


def _backfill_missing_position_products(active_engine: Engine) -> None:
    inspector = inspect(active_engine)
    tables = set(inspector.get_table_names())
    if "positions" not in tables or "products" not in tables:
        return
    position_columns = {c["name"] for c in inspector.get_columns("positions")}
    required_columns = {
        "portfolio_id",
        "product_id",
        "underlying",
        "product_type",
        "product_kwargs",
    }
    if not required_columns.issubset(position_columns):
        return
    from .models import Position
    from .services.domains.products import backfill_position_products

    session_factory = sessionmaker(bind=active_engine, autoflush=False, autocommit=False)
    with session_factory() as session:
        missing = session.query(Position.id).filter(Position.product_id.is_(None)).first()
        if missing is None:
            return
        backfill_position_products(session)
        session.commit()


def seed_desk_workflows(active_engine: Engine) -> None:
    """Idempotently seed the server-owned desk workflows (``source='seed'``).

    One ``INSERT … SELECT … WHERE NOT EXISTS`` per entry, plus an ``UPDATE`` that
    refreshes an already-seeded row only when its canonical script changed and it is
    still ``source='seed'`` (so a user-authored slug collision is never clobbered).
    """
    from .desk_workflow_seed import SEED_WORKFLOWS

    with active_engine.begin() as connection:
        for wf in SEED_WORKFLOWS:
            connection.execute(
                text(
                    "INSERT INTO desk_workflows "
                    "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
                    "SELECT :slug, :title, :persona, :description, :scope, :default_mode, :script, 'seed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                    "WHERE NOT EXISTS (SELECT 1 FROM desk_workflows WHERE slug = :slug)"
                ),
                wf,
            )
            connection.execute(
                text(
                    "UPDATE desk_workflows "
                    "SET title = :title, persona = :persona, description = :description, "
                    "scope = :scope, default_mode = :default_mode, script = :script, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE slug = :slug AND source = 'seed' AND script != :script"
                ),
                wf,
            )


def _ensure_incremental_schema(active_engine: Engine) -> None:
    """Apply additive schema repairs for local DBs created via create_all().

    Alembic remains the documented migration path, but this app historically
    boots local SQLite databases with create_all(), which does not alter
    existing tables when models gain nullable columns.
    """
    inspector = inspect(active_engine)
    tables = set(inspector.get_table_names())
    if "rfqs" in tables and "rfq_quote_versions" not in tables:
        with active_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS rfq_quote_versions (id INTEGER NOT NULL PRIMARY KEY, rfq_id INTEGER NOT NULL, version INTEGER NOT NULL, quote_mode VARCHAR(20) NOT NULL, status VARCHAR(40) NOT NULL, request_payload JSON NOT NULL, quote_payload JSON NOT NULL, error TEXT, created_by VARCHAR(120) NOT NULL, approved_by VARCHAR(120), approved_at DATETIME, released_at DATETIME, valid_until DATETIME, created_at DATETIME NOT NULL, FOREIGN KEY(rfq_id) REFERENCES rfqs (id))"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rfq_quote_versions_rfq_id "
                    "ON rfq_quote_versions (rfq_id)"
                )
            )
        tables.add("rfq_quote_versions")
    if "rfqs" in tables and "rfq_quote_versions" in tables:
        _backfill_rfq_quote_versions(active_engine)
    if "agent_threads" in tables:
        thread_cols = {c["name"] for c in inspector.get_columns("agent_threads")}
        with active_engine.begin() as connection:
            if "active_workflow_id" not in thread_cols:
                connection.execute(
                    text("ALTER TABLE agent_threads ADD COLUMN active_workflow_id INTEGER")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_agent_threads_active_workflow_id "
                    "ON agent_threads (active_workflow_id)"
                )
            )
            # Migration 0033 (arena threads): mirror here so existing local DBs
            # that boot through create_all without running Alembic still get the
            # columns the ORM now references on every AgentThread query.
            if "source" not in thread_cols:
                connection.execute(
                    text("ALTER TABLE agent_threads ADD COLUMN source VARCHAR(20) "
                         "NOT NULL DEFAULT 'desk'")
                )
            if "arena_run_id" not in thread_cols:
                connection.execute(
                    text("ALTER TABLE agent_threads ADD COLUMN arena_run_id INTEGER")
                )
            # Migration 0036 (goal mode): goal_run / goal_contract JSON columns the
            # GoalRunService persists per thread; mirror here for the same reason.
            if "goal_run" not in thread_cols:
                connection.execute(
                    text("ALTER TABLE agent_threads ADD COLUMN goal_run JSON")
                )
            if "goal_contract" not in thread_cols:
                connection.execute(
                    text("ALTER TABLE agent_threads ADD COLUMN goal_contract JSON")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_agent_threads_arena_run_id "
                    "ON agent_threads (arena_run_id)"
                )
            )
    if "arena_match" in tables:
        # Migration 0034: per-check score breakdown. Mirror here so local DBs
        # booting via create_all without Alembic still get the column the ORM
        # now references on every ArenaMatch read.
        arena_match_cols = {c["name"] for c in inspector.get_columns("arena_match")}
        if "score_breakdown" not in arena_match_cols:
            with active_engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE arena_match ADD COLUMN score_breakdown JSON")
                )
    if "agent_messages" in tables:
        message_cols = {c["name"] for c in inspector.get_columns("agent_messages")}
        with active_engine.begin() as connection:
            if "workflow_id" not in message_cols:
                connection.execute(
                    text("ALTER TABLE agent_messages ADD COLUMN workflow_id INTEGER")
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_agent_messages_workflow_id "
                        "ON agent_messages (workflow_id)"
                    )
                )
            if "session_id" not in message_cols:
                connection.execute(
                    text("ALTER TABLE agent_messages ADD COLUMN session_id INTEGER")
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_agent_messages_session_id "
                        "ON agent_messages (session_id)"
                    )
                )
    if "agent_sessions" in tables:
        with active_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_agent_sessions_active_workflow_persona "
                    "ON agent_sessions (workflow_id, persona) "
                    "WHERE status = 'active'"
                )
            )
    _ensure_underlying_schema(active_engine, inspector, tables)

    from .services.engine_configs import DEFAULT_ENGINE_CONFIG_RULES

    with active_engine.begin() as connection:
        if "engine_config_variants" not in tables:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS engine_config_variants ("
                    "id INTEGER NOT NULL PRIMARY KEY, "
                    "name VARCHAR(160) NOT NULL UNIQUE, "
                    "description TEXT, "
                    "status VARCHAR(40) NOT NULL DEFAULT 'active', "
                    "is_default BOOLEAN NOT NULL DEFAULT 0, "
                    "rules JSON NOT NULL, "
                    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            tables.add("engine_config_variants")
        # Seed outside the create branch: fresh DBs get the table from
        # metadata.create_all, but engine-less positions still need a default
        # config to resolve against.
        connection.execute(
            text(
                "INSERT INTO engine_config_variants "
                "(name, description, status, is_default, rules, created_at, updated_at) "
                "SELECT :name, :description, 'active', 1, :rules, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                "WHERE NOT EXISTS (SELECT 1 FROM engine_config_variants WHERE is_default = 1)"
            ),
            {
                "name": "System Default",
                "description": "Default engine rules matching current product mappings.",
                "rules": json.dumps(DEFAULT_ENGINE_CONFIG_RULES),
            },
        )

    # Desk workflows (migration 0035): seed the flagship for create_all-booted
    # DBs that never ran Alembic. Idempotent; the table itself comes from
    # metadata.create_all.
    if "desk_workflows" in tables:
        seed_desk_workflows(active_engine)

    if "positions" not in tables:
        return
    if "position_lifecycle_events" in tables:
        lifecycle_cols = {
            c["name"] for c in inspector.get_columns("position_lifecycle_events")
        }
        with active_engine.begin() as connection:
            if "cancelled_at" not in lifecycle_cols:
                connection.execute(
                    text(
                        "ALTER TABLE position_lifecycle_events "
                        "ADD COLUMN cancelled_at DATETIME"
                    )
                )
            if "cancelled_by" not in lifecycle_cols:
                connection.execute(
                    text(
                        "ALTER TABLE position_lifecycle_events "
                        "ADD COLUMN cancelled_by VARCHAR(120)"
                    )
                )
            if "cancellation_reason" not in lifecycle_cols:
                connection.execute(
                    text(
                        "ALTER TABLE position_lifecycle_events "
                        "ADD COLUMN cancellation_reason TEXT"
                    )
                )
    columns = {column["name"] for column in inspector.get_columns("positions")}
    with active_engine.begin() as connection:
        if "rfq_id" not in columns:
            connection.execute(text("ALTER TABLE positions ADD COLUMN rfq_id INTEGER"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_positions_rfq_id ON positions (rfq_id)")
            )
        if "rfq_quote_version_id" not in columns:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN rfq_quote_version_id INTEGER")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_positions_rfq_quote_version_id "
                    "ON positions (rfq_quote_version_id)"
                )
            )
        if "kwargs_migrated_at" not in columns:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN kwargs_migrated_at DATETIME")
            )
        if "version" not in columns:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            )
        added_position_kind = "position_kind" not in columns
        if added_position_kind:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN position_kind VARCHAR(16) NOT NULL DEFAULT 'otc'")
            )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_positions_position_kind ON positions (position_kind)")
        )
    _ensure_product_schema(active_engine, inspector, tables)
    if added_position_kind:
        with active_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE positions
                    SET position_kind = 'listed'
                    WHERE source_trade_id LIKE 'HEDGE:%'
                       OR json_extract(source_payload, '$.hedge.is_hedge') = 1
                       OR product_id IN (
                           SELECT id FROM products WHERE product_family IN ('futures', 'spot')
                       )
                    """
                )
            )
    with active_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE positions
                SET position_kind = 'otc'
                WHERE position_kind NOT IN ('otc', 'listed') OR position_kind IS NULL
                """
            )
        )
    if "trade_effective_date" not in columns:
        with active_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN trade_effective_date DATETIME")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_positions_trade_effective_date "
                    "ON positions (trade_effective_date)"
                )
            )
            rows = connection.execute(
                text(
                    "SELECT id, source_payload FROM positions WHERE trade_effective_date IS NULL"
                )
            ).mappings()
            for row in rows:
                effective = _source_effective_date(row["source_payload"])
                if effective is None:
                    continue
                connection.execute(
                    text(
                        "UPDATE positions SET trade_effective_date = :value WHERE id = :id"
                    ),
                    {"id": row["id"], "value": effective},
                )

    # Async-agent columns on task_runs (mirrors alembic 0014). Without these,
    # an existing local DB created before this branch will raise "no such column"
    # on the first mark_stale_tasks_failed query in create_app().
    if "task_runs" in tables:
        task_run_cols = {c["name"] for c in inspector.get_columns("task_runs")}
        with active_engine.begin() as connection:
            if "parent_thread_id" not in task_run_cols:
                connection.execute(
                    text("ALTER TABLE task_runs ADD COLUMN parent_thread_id INTEGER")
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_task_runs_parent_thread_id "
                        "ON task_runs (parent_thread_id)"
                    )
                )
            if "description" not in task_run_cols:
                connection.execute(
                    text("ALTER TABLE task_runs ADD COLUMN description VARCHAR(120)")
                )
            if "result_payload" not in task_run_cols:
                connection.execute(
                    text("ALTER TABLE task_runs ADD COLUMN result_payload JSON")
                )
            if "cancel_requested" not in task_run_cols:
                connection.execute(
                    text(
                        "ALTER TABLE task_runs ADD COLUMN cancel_requested "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            if "scenario_test_run_id" not in task_run_cols:
                connection.execute(
                    text(
                        "ALTER TABLE task_runs ADD COLUMN scenario_test_run_id INTEGER"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_task_runs_scenario_test_run_id "
                        "ON task_runs (scenario_test_run_id)"
                    )
                )
            if "greeks_landscape_run_id" not in task_run_cols:
                connection.execute(
                    text("ALTER TABLE task_runs ADD COLUMN greeks_landscape_run_id INTEGER")
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_task_runs_greeks_landscape_run_id "
                        "ON task_runs (greeks_landscape_run_id)"
                    )
                )

    for table_name in ("position_valuation_runs", "risk_runs", "scenario_test_runs", "backtest_runs"):
        if table_name not in tables:
            continue
        table_cols = {c["name"] for c in inspector.get_columns(table_name)}
        if "engine_config_id" in table_cols:
            continue
        with active_engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN engine_config_id INTEGER"))
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table_name}_engine_config_id "
                    f"ON {table_name} (engine_config_id)"
                )
            )

    _ensure_structured_position_terms_schema(active_engine, inspector, tables)


def _ensure_underlying_schema(
    active_engine: Engine,
    inspector: Any,
    tables: set[str],
) -> None:
    """Bootstrap the instrument master table and backfill FK columns.

    This function is a fresh/test-DB bootstrap path only: it creates the
    ``instruments`` table via ``CREATE TABLE IF NOT EXISTS`` for in-memory and
    newly created SQLite databases where alembic has not been run.  Legacy live
    databases (those that still have the old ``underlyings`` table) are caught
    earlier by the init_db() boot guard and must go through ``alembic upgrade
    head`` (migration 0024 performs the rename) before the app will start.
    """
    if active_engine.dialect.name != "sqlite":
        return
    with active_engine.begin() as connection:
        # CREATE TABLE IF NOT EXISTS is a no-op on fresh DBs where create_all()
        # already created the instruments table from the ORM model. It is a
        # safety net for very old SQLite files that predate even the underlyings
        # table (e.g. CI-only in-memory DBs used before the rename).
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS instruments ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "symbol VARCHAR(80) NOT NULL UNIQUE, "
                "display_name VARCHAR(160), "
                "kind VARCHAR(40) NOT NULL DEFAULT 'index', "
                "market VARCHAR(40), "
                "exchange VARCHAR(40), "
                "currency VARCHAR(8) NOT NULL DEFAULT 'CNY', "
                "akshare_symbol VARCHAR(80), "
                "akshare_asset_class VARCHAR(40), "
                "status VARCHAR(40) NOT NULL DEFAULT 'draft', "
                "source VARCHAR(40) NOT NULL DEFAULT 'manual', "
                "rate FLOAT, "
                "dividend_yield FLOAT, "
                "volatility FLOAT, "
                "notes TEXT, "
                "contract_code VARCHAR(80), "
                "series_root VARCHAR(40), "
                "expiry DATE, "
                "multiplier FLOAT, "
                "strike FLOAT, "
                "option_type VARCHAR(4), "
                "parent_id INTEGER, "
                "loaded_at DATETIME, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        )
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_instruments_symbol ON instruments (symbol)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_instruments_akshare_symbol ON instruments (akshare_symbol)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_instruments_status ON instruments (status)")
        )
    tables.add("instruments")

    for table_name, symbol_column in (
        ("positions", "underlying"),
        ("products", "underlying"),
        ("market_data_profiles", "symbol"),
    ):
        if table_name not in tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table_name)}
        with active_engine.begin() as connection:
            if "underlying_id" not in columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN underlying_id INTEGER")
                )
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table_name}_underlying_id "
                        f"ON {table_name} (underlying_id)"
                    )
                )
            if symbol_column not in columns:
                # A partial/legacy table without the symbol column has nothing to
                # backfill into ``instruments`` (seen on minimally-rebuilt local DBs).
                continue
            connection.execute(
                text(
                    f"""
                    INSERT OR IGNORE INTO instruments
                        (symbol, display_name, kind, currency, akshare_symbol,
                         akshare_asset_class, status, source, created_at, updated_at)
                    SELECT DISTINCT {symbol_column}, {symbol_column}, 'index', 'CNY',
                           CASE WHEN instr({symbol_column}, '.') > 0
                                THEN substr({symbol_column}, 1, instr({symbol_column}, '.') - 1)
                                ELSE {symbol_column} END,
                           'index', 'draft', :source, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM {table_name}
                    WHERE {symbol_column} IS NOT NULL AND trim({symbol_column}) != ''
                    """
                ),
                {"source": table_name},
            )
            connection.execute(
                text(
                    f"""
                    UPDATE {table_name}
                    SET underlying_id = (
                        SELECT instruments.id FROM instruments
                        WHERE instruments.symbol = {table_name}.{symbol_column}
                    )
                    WHERE underlying_id IS NULL
                      AND {symbol_column} IS NOT NULL
                      AND trim({symbol_column}) != ''
                    """
                )
            )

    # This block only fires on pre-0019 SQLite files; migration 0019 folded
    # underlying_pricing_defaults into the instrument registry, so the table
    # no longer exists on any DB that has been upgraded past that point.
    if "underlying_pricing_defaults" in tables:
        with active_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO instruments
                        (symbol, display_name, kind, currency, akshare_symbol,
                         akshare_asset_class, status, source, rate, dividend_yield,
                         volatility, notes, created_at, updated_at)
                    SELECT underlying, underlying, 'index', 'CNY',
                           CASE WHEN instr(underlying, '.') > 0
                                THEN substr(underlying, 1, instr(underlying, '.') - 1)
                                ELSE underlying END,
                           'index', 'active', 'pricing_default', rate, dividend_yield,
                           volatility, notes, created_at, updated_at
                    FROM underlying_pricing_defaults
                    WHERE underlying IS NOT NULL AND trim(underlying) != ''
                    """
                )
            )


_PRODUCT_SCHEMA_SQL: dict[str, str] = {
    "products": (
        "CREATE TABLE IF NOT EXISTS products ("
        "id INTEGER NOT NULL, "
        "asset_class VARCHAR(40) NOT NULL, "
        "product_family VARCHAR(40) NOT NULL, "
        "quantark_class VARCHAR(120), "
        "display_name VARCHAR(160), "
        "underlying VARCHAR(80) NOT NULL, "
        "currency VARCHAR(8) NOT NULL, "
        "term_hash VARCHAR(80) NOT NULL, "
        "raw_terms JSON NOT NULL, "
        "source_payload JSON, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (id)"
        ")"
    ),
    "equity_option_products": (
        "CREATE TABLE IF NOT EXISTS equity_option_products ("
        "product_id INTEGER NOT NULL, "
        "strike FLOAT, "
        "option_type VARCHAR(8), "
        "exercise_type VARCHAR(16), "
        "maturity FLOAT, "
        "exercise_date DATE, "
        "settlement_date DATE, "
        "maturity_date DATE, "
        "tenor FLOAT, "
        "tenor_end VARCHAR(40), "
        "annualization_day_count VARCHAR(40), "
        "initial_price FLOAT, "
        "contract_multiplier FLOAT NOT NULL DEFAULT 1.0, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_autocallable_products": (
        "CREATE TABLE IF NOT EXISTS equity_autocallable_products ("
        "product_id INTEGER NOT NULL, "
        "autocallable_kind VARCHAR(40) NOT NULL, "
        "is_reverse BOOLEAN NOT NULL DEFAULT 0, "
        "initial_price FLOAT NOT NULL, "
        "strike FLOAT NOT NULL, "
        "contract_multiplier FLOAT NOT NULL DEFAULT 1.0, "
        "ko_observation_type VARCHAR(24), "
        "ki_observation_type VARCHAR(24), "
        "ki_continuous BOOLEAN NOT NULL DEFAULT 0, "
        "disable_ko_after_ki BOOLEAN NOT NULL DEFAULT 0, "
        "payoff_rebate_rate FLOAT, "
        "payoff_call_rebate_enabled BOOLEAN NOT NULL DEFAULT 0, "
        "payoff_call_strike FLOAT, "
        "payoff_call_participation_rate FLOAT, "
        "payoff_include_principal BOOLEAN NOT NULL DEFAULT 1, "
        "payoff_participation_rate FLOAT, "
        "payoff_protection_type VARCHAR(24), "
        "payoff_protection_rate FLOAT, "
        "accrual_coupon_pay_type VARCHAR(24), "
        "accrual_is_annualized BOOLEAN NOT NULL DEFAULT 1, "
        "accrual_is_annualized_ko BOOLEAN, "
        "accrual_is_annualized_ki BOOLEAN, "
        "accrual_is_annualized_rebate BOOLEAN, "
        "reset_rate FLOAT, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_autocallable_observations": (
        "CREATE TABLE IF NOT EXISTS equity_autocallable_observations ("
        "id INTEGER NOT NULL, "
        "product_id INTEGER NOT NULL, "
        "observation_role VARCHAR(24) NOT NULL, "
        "sequence INTEGER NOT NULL, "
        "observation_date DATE, "
        "observation_time FLOAT, "
        "barrier_level FLOAT, "
        "rate FLOAT, "
        "accrual_factor FLOAT, "
        "aggregation VARCHAR(24), "
        "weight FLOAT, "
        "source_payload JSON, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE, "
        "CONSTRAINT uq_equity_autocallable_observations_role_sequence "
        "UNIQUE (product_id, observation_role, sequence)"
        ")"
    ),
    "equity_phoenix_coupon_products": (
        "CREATE TABLE IF NOT EXISTS equity_phoenix_coupon_products ("
        "product_id INTEGER NOT NULL, "
        "coupon_barrier FLOAT NOT NULL, "
        "coupon_rate FLOAT NOT NULL, "
        "coupon_pay_type VARCHAR(24), "
        "day_count_convention VARCHAR(40), "
        "memory_coupon BOOLEAN NOT NULL DEFAULT 1, "
        "fixed_coupon_year_fraction FLOAT, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_barrier_products": (
        "CREATE TABLE IF NOT EXISTS equity_barrier_products ("
        "product_id INTEGER NOT NULL, "
        "barrier_kind VARCHAR(32) NOT NULL, "
        "barrier FLOAT, "
        "barrier_type VARCHAR(32), "
        "upper_barrier FLOAT, "
        "lower_barrier FLOAT, "
        "rebate FLOAT, "
        "monitoring_type VARCHAR(24), "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_touch_products": (
        "CREATE TABLE IF NOT EXISTS equity_touch_products ("
        "product_id INTEGER NOT NULL, "
        "touch_kind VARCHAR(32) NOT NULL, "
        "barrier FLOAT, "
        "upper_barrier FLOAT, "
        "lower_barrier FLOAT, "
        "touch_type VARCHAR(32), "
        "payout FLOAT, "
        "rebate FLOAT, "
        "monitoring_type VARCHAR(24), "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_asian_products": (
        "CREATE TABLE IF NOT EXISTS equity_asian_products ("
        "product_id INTEGER NOT NULL, "
        "averaging_method VARCHAR(24), "
        "averaging_kind VARCHAR(24), "
        "n_observations INTEGER, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_asian_observations": (
        "CREATE TABLE IF NOT EXISTS equity_asian_observations ("
        "product_id INTEGER NOT NULL, "
        "sequence INTEGER NOT NULL, "
        "observation_date DATE, "
        "observation_time FLOAT, "
        "observed_price FLOAT, "
        "weight FLOAT, "
        "PRIMARY KEY (product_id, sequence), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_range_accrual_products": (
        "CREATE TABLE IF NOT EXISTS equity_range_accrual_products ("
        "product_id INTEGER NOT NULL, "
        "lower_barrier FLOAT NOT NULL, "
        "upper_barrier FLOAT NOT NULL, "
        "accrual_rate FLOAT NOT NULL, "
        "observation_type VARCHAR(24), "
        "day_count_convention VARCHAR(40), "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_range_accrual_observations": (
        "CREATE TABLE IF NOT EXISTS equity_range_accrual_observations ("
        "product_id INTEGER NOT NULL, "
        "sequence INTEGER NOT NULL, "
        "observation_date DATE, "
        "observation_time FLOAT, "
        "lower_barrier FLOAT, "
        "upper_barrier FLOAT, "
        "weight FLOAT, "
        "PRIMARY KEY (product_id, sequence), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_sharkfin_products": (
        "CREATE TABLE IF NOT EXISTS equity_sharkfin_products ("
        "product_id INTEGER NOT NULL, "
        "sharkfin_kind VARCHAR(16) NOT NULL, "
        "strike FLOAT, "
        "barrier FLOAT, "
        "upper_barrier FLOAT, "
        "lower_barrier FLOAT, "
        "option_type VARCHAR(8), "
        "participation_rate FLOAT, "
        "coupon FLOAT, "
        "rebate FLOAT, "
        "observation_type VARCHAR(24), "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_spot_products": (
        "CREATE TABLE IF NOT EXISTS equity_spot_products ("
        "product_id INTEGER NOT NULL, "
        "deltaone_type VARCHAR(16) NOT NULL, "
        "instrument_code VARCHAR(80) NOT NULL, "
        "exchange VARCHAR(40), "
        "contract_multiplier FLOAT NOT NULL DEFAULT 1.0, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_futures_products": (
        "CREATE TABLE IF NOT EXISTS equity_futures_products ("
        "product_id INTEGER NOT NULL, "
        "contract_code VARCHAR(80) NOT NULL, "
        "multiplier FLOAT NOT NULL DEFAULT 1.0, "
        "maturity FLOAT, "
        "maturity_date DATE, "
        "basis FLOAT NOT NULL DEFAULT 0.0, "
        "basis_decay_rate FLOAT NOT NULL DEFAULT 1.0, "
        "market_price FLOAT, "
        "PRIMARY KEY (product_id), "
        "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE"
        ")"
    ),
    "equity_product_components": (
        "CREATE TABLE IF NOT EXISTS equity_product_components ("
        "id INTEGER NOT NULL, "
        "parent_product_id INTEGER NOT NULL, "
        "component_product_id INTEGER NOT NULL, "
        "component_role VARCHAR(40) NOT NULL, "
        "quantity FLOAT NOT NULL DEFAULT 1.0, "
        "weight FLOAT NOT NULL DEFAULT 1.0, "
        "sequence INTEGER NOT NULL, "
        "source_payload JSON, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(parent_product_id) REFERENCES products (id) ON DELETE CASCADE, "
        "FOREIGN KEY(component_product_id) REFERENCES products (id), "
        "CONSTRAINT uq_equity_product_components_parent_sequence "
        "UNIQUE (parent_product_id, sequence)"
        ")"
    ),
}


def _ensure_product_schema(
    active_engine: Engine,
    inspector: Any,
    tables: set[str],
) -> None:
    if active_engine.dialect.name != "sqlite":
        return
    with active_engine.begin() as connection:
        for table_name, create_sql in _PRODUCT_SCHEMA_SQL.items():
            if table_name not in tables:
                connection.execute(text(create_sql))
                tables.add(table_name)
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_asset_family "
                "ON products (asset_class, product_family)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_product_family "
                "ON products (product_family)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_underlying ON products (underlying)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_quantark_class "
                "ON products (quantark_class)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_term_hash ON products (term_hash)"
            )
        )
        product_columns = {c["name"] for c in inspector.get_columns("products")}
        if "underlying_id" not in product_columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN underlying_id INTEGER"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_products_underlying_id "
                "ON products (underlying_id)"
            )
        )
        position_columns = {c["name"] for c in inspector.get_columns("positions")}
        if "product_id" not in position_columns:
            connection.execute(
                text("ALTER TABLE positions ADD COLUMN product_id INTEGER")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_positions_product_id ON positions (product_id)"
            )
        )


_STRUCTURED_TERM_TABLE_REBUILDS: dict[str, dict[str, Any]] = {
    "option_core_terms": {
        "columns": (
            "position_id",
            "strike",
            "expiry_date",
            "option_type",
            "side",
            "currency",
            "notional",
        ),
        "nullable": {"strike", "expiry_date", "option_type", "notional"},
        "create_sql": (
            "CREATE TABLE option_core_terms ("
            "position_id INTEGER NOT NULL, "
            "strike FLOAT, "
            "expiry_date DATE, "
            "option_type VARCHAR(8), "
            "side VARCHAR(8) NOT NULL, "
            "currency VARCHAR(8) NOT NULL, "
            "notional FLOAT, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
    "single_barrier_terms": {
        "columns": ("position_id", "barrier", "barrier_type", "rebate"),
        "nullable": {"barrier", "barrier_type", "rebate"},
        "create_sql": (
            "CREATE TABLE single_barrier_terms ("
            "position_id INTEGER NOT NULL, "
            "barrier FLOAT, "
            "barrier_type VARCHAR(4), "
            "rebate FLOAT, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
    "double_barrier_terms": {
        "columns": (
            "position_id",
            "upper_barrier",
            "lower_barrier",
            "barrier_kind",
            "rebate",
        ),
        "nullable": {"upper_barrier", "lower_barrier", "barrier_kind", "rebate"},
        "create_sql": (
            "CREATE TABLE double_barrier_terms ("
            "position_id INTEGER NOT NULL, "
            "upper_barrier FLOAT, "
            "lower_barrier FLOAT, "
            "barrier_kind VARCHAR(4), "
            "rebate FLOAT, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
    "sharkfin_terms": {
        "columns": ("position_id", "participation_rate", "coupon"),
        "nullable": {"participation_rate", "coupon"},
        "create_sql": (
            "CREATE TABLE sharkfin_terms ("
            "position_id INTEGER NOT NULL, "
            "participation_rate FLOAT, "
            "coupon FLOAT, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
    "asian_terms": {
        "columns": (
            "position_id",
            "averaging_method",
            "averaging_kind",
            "n_observations",
        ),
        "nullable": {"averaging_method", "averaging_kind", "n_observations"},
        "create_sql": (
            "CREATE TABLE asian_terms ("
            "position_id INTEGER NOT NULL, "
            "averaging_method VARCHAR(16), "
            "averaging_kind VARCHAR(8), "
            "n_observations INTEGER, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
    "snowball_terms": {
        "columns": (
            "position_id",
            "initial_price",
            "ki_barrier",
            "coupon",
            "start_date",
            "knocked_in",
            "ki_observation",
            "payoff_kind",
            "legacy_kwargs",
        ),
        "nullable": {
            "initial_price",
            "ki_barrier",
            "coupon",
            "start_date",
            "ki_observation",
            "payoff_kind",
            "legacy_kwargs",
        },
        "create_sql": (
            "CREATE TABLE snowball_terms ("
            "position_id INTEGER NOT NULL, "
            "initial_price FLOAT, "
            "ki_barrier FLOAT, "
            "coupon FLOAT, "
            "start_date DATE, "
            "knocked_in BOOLEAN DEFAULT 0 NOT NULL, "
            "ki_observation VARCHAR(20), "
            "payoff_kind VARCHAR(40), "
            "legacy_kwargs JSON, "
            "PRIMARY KEY (position_id), "
            "FOREIGN KEY(position_id) REFERENCES positions (id) ON DELETE CASCADE"
            ")"
        ),
    },
}


def _ensure_structured_position_terms_schema(
    active_engine: Engine,
    inspector: Any,
    tables: set[str],
) -> None:
    if active_engine.dialect.name != "sqlite":
        return
    for table_name, spec in _STRUCTURED_TERM_TABLE_REBUILDS.items():
        if table_name not in tables:
            continue
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        nullable_columns = spec["nullable"]
        if not any(
            columns.get(column, {}).get("nullable") is False
            for column in nullable_columns
        ):
            continue
        _rebuild_structured_term_table(active_engine, table_name, spec)


def _rebuild_structured_term_table(
    active_engine: Engine,
    table_name: str,
    spec: dict[str, Any],
) -> None:
    temp_table = f"{table_name}_old"
    columns = ", ".join(spec["columns"])
    with active_engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
        connection.execute(text(f"ALTER TABLE {table_name} RENAME TO {temp_table}"))
        connection.execute(text(spec["create_sql"]))
        connection.execute(
            text(
                f"INSERT INTO {table_name} ({columns}) "
                f"SELECT {columns} FROM {temp_table}"
            )
        )
        connection.execute(text(f"DROP TABLE {temp_table}"))


def _backfill_rfq_quote_versions(active_engine: Engine) -> None:
    with active_engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, status, request_payload, quote_payload, created_at "
                "FROM rfqs WHERE NOT EXISTS ("
                "SELECT 1 FROM rfq_quote_versions WHERE rfq_quote_versions.rfq_id = rfqs.id)"
            )
        ).mappings()
        for row in rows:
            quote_payload = _json_payload(row["quote_payload"])
            status = str(row["status"] or "pending_approval")
            if (
                isinstance(quote_payload, dict)
                and quote_payload.get("quantark_ok") is False
                and status
                not in {"approved", "rejected", "released", "client_accepted", "booked"}
            ):
                status = "pricing_failed"
            error = (
                str(quote_payload.get("quantark_error"))
                if isinstance(quote_payload, dict) and quote_payload.get("quantark_error")
                else None
            )
            connection.execute(
                text(
                    "INSERT INTO rfq_quote_versions "
                    "(rfq_id, version, quote_mode, status, request_payload, "
                    "quote_payload, error, created_by, created_at) "
                    "VALUES (:rfq_id, 1, 'solve', :status, :request_payload, "
                    ":quote_payload, :error, 'legacy', :created_at)"
                ),
                {
                    "rfq_id": row["id"],
                    "status": status,
                    "request_payload": _json_text(row["request_payload"]),
                    "quote_payload": _json_text(row["quote_payload"]),
                    "error": error,
                    "created_at": row["created_at"] or datetime.utcnow(),
                },
            )


def _json_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


def _source_effective_date(source_payload: Any) -> datetime | None:
    payload = source_payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    source_row = payload.get("row")
    if not isinstance(source_row, dict):
        return None
    return _parse_date(source_row.get("起始日"))


def _parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    text_value = str(value).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        return None


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
