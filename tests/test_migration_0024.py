"""Migration 0024 — instrument unification.

This migration carries the LIVE production book across the rename
underlyings→instruments: id-preserving rename, hedge_instruments contract
merge, market_quotes / assumption_sets backfill, pricing_parameter_rows.spot
fold, legacy-profile split.

NOTE ON TEST STYLE: we deliberately do NOT use ``alembic command.upgrade`` to
reach the pre-0024 state. ``0001_initial`` does ``Base.metadata.create_all`` off
the CURRENT ORM, so a fresh ``upgrade`` produces the *target* (post-0024) schema
immediately and every later migration no-ops via its table-exists guards. The
genuine pre-0024 schema only exists on the live DB (built incrementally against
an older ORM). We therefore reconstruct that pre-0024 schema with a migration-
local MetaData — the same direct-invocation style as
``test_underlying_migration.py`` / ``test_hedging_migration.py`` — seed it, then
invoke ``migration.upgrade()`` against it.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def _build_pre_0024_schema(engine: sa.Engine) -> None:
    """Reconstruct the real 0023 live schema (underlyings, FK→underlyings,
    pricing_parameter_rows WITH spot / NO instrument_id, hedge_map_entries with
    NO instrument_id, hedge_instruments, position_market_inputs, ...)."""
    md = sa.MetaData()

    sa.Table(
        "underlyings", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160)),
        sa.Column("asset_class", sa.String(40), nullable=False, server_default="index"),
        sa.Column("market", sa.String(40)),
        sa.Column("exchange", sa.String(40)),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("akshare_symbol", sa.String(80)),
        sa.Column("akshare_asset_class", sa.String(40)),
        sa.Column("status", sa.String(40), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("rate", sa.Float()),
        sa.Column("dividend_yield", sa.Float()),
        sa.Column("volatility", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "portfolios", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("base_currency", sa.String(12), nullable=False, server_default="USD"),
        sa.Column("kind", sa.String(20), nullable=False, server_default="container"),
        sa.Column("filter_rule", sa.JSON()),
        sa.Column("manual_include_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("manual_exclude_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_portfolio_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "products", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_class", sa.String(40), nullable=False, server_default="equity"),
        sa.Column("product_family", sa.String(40), nullable=False),
        sa.Column("quantark_class", sa.String(120)),
        sa.Column("display_name", sa.String(160)),
        # NOTE: live products has NO FK on underlying_id (built incrementally
        # against an older ORM). 0024 ADDS the instruments FK to reach the ORM
        # end-state; reconstructing it here would test a non-existent live state.
        sa.Column("underlying_id", sa.Integer()),
        sa.Column("underlying", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("term_hash", sa.String(80), nullable=False),
        sa.Column("raw_terms", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "positions", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        # live positions carries ONLY the portfolio_id FK; product_id /
        # underlying_id / rfq_id / rfq_quote_version_id are bare integer columns.
        # 0024 adds the underlying_id→instruments FK (and rebuilds in the
        # product_id/rfq* FKs to match the ORM); the pre-state must omit them.
        sa.Column("product_id", sa.Integer()),
        sa.Column("underlying_id", sa.Integer()),
        sa.Column("underlying", sa.String(80), nullable=False),
        sa.Column("product_type", sa.String(120), nullable=False),
        sa.Column("product_kwargs", sa.JSON(), nullable=False),
        sa.Column("engine_name", sa.String(120), nullable=False),
        sa.Column("engine_kwargs", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("source_trade_id", sa.String(160)),
        sa.Column("source_row", sa.Integer()),
        sa.Column("mapping_status", sa.String(40), nullable=False),
        sa.Column("mapping_error", sa.Text()),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("rfq_id", sa.Integer()),
        sa.Column("rfq_quote_version_id", sa.Integer()),
        sa.Column("trade_effective_date", sa.DateTime()),
        sa.Column("kwargs_migrated_at", sa.DateTime()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "market_data_profiles", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        # live market_data_profiles has NO FK on underlying_id; 0024 adds the
        # instruments FK to reach the ORM end-state.
        sa.Column("underlying_id", sa.Integer()),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("source", sa.String(80), nullable=False, server_default="akshare"),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("asset_class", sa.String(40), nullable=False, server_default="index"),
        sa.Column("start_date", sa.String(20), nullable=False),
        sa.Column("end_date", sa.String(20), nullable=False),
        sa.Column("adjust", sa.String(20), nullable=False, server_default="qfq"),
        sa.Column("valuation_date", sa.DateTime(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "hedge_instruments", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
        sa.Column("family", sa.String(40), nullable=False),
        sa.Column("series_root", sa.String(40), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("contract_code", sa.String(80), nullable=False),
        sa.Column("instrument_type", sa.String(20), nullable=False),
        sa.Column("option_type", sa.String(4)),
        sa.Column("strike", sa.Float()),
        sa.Column("expiry", sa.Date()),
        sa.Column("multiplier", sa.Float()),
        sa.Column("last_price", sa.Float()),
        sa.Column("akshare_symbol", sa.String(80)),
        sa.Column("status", sa.String(20), nullable=False, server_default="live"),
        sa.Column("loaded_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("exchange", "contract_code", name="uq_hedge_instruments_exchange_code"),
    )
    sa.Table(
        "hedge_map_entries", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("contract_code", sa.String(80), nullable=False),
        sa.Column("family", sa.String(40), nullable=False),
        sa.Column("series_root", sa.String(40), nullable=False),
        sa.Column("instrument_type", sa.String(20), nullable=False),
        sa.Column("option_type", sa.String(4)),
        sa.Column("strike", sa.Float()),
        sa.Column("expiry", sa.Date()),
        sa.Column("reconcile_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("marked_by", sa.String(80)),
        sa.Column("marked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("underlying_id", "exchange", "contract_code", name="uq_hedge_map_entries_underlying_contract"),
    )
    sa.Table(
        "hedge_bands", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id")),
        sa.Column("delta_cash_band", sa.Float(), nullable=False),
        sa.Column("gamma_cash_band", sa.Float(), nullable=False),
        sa.Column("vega_band", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("updated_by", sa.String(80)),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("underlying_id", name="uq_hedge_bands_underlying"),
    )
    # mirror the 0023 partial-unique guard on the defaults row (expression-based;
    # added below via raw DDL since SQLAlchemy can't model the constant target).
    sa.Table(
        "pricing_parameter_profiles", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("valuation_date", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False, server_default="xlsx"),
        sa.Column("source_path", sa.Text()),
        sa.Column("status", sa.String(40), nullable=False, server_default="completed"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # Runs that carry a pricing_parameter_profile_id: position_valuation_runs has
    # a REAL FK to pricing_parameter_profiles (live), risk_runs only a soft int
    # ref (no FK declared on live). 0024 must not orphan these. risk_runs'
    # market_snapshot_id FK target is intentionally omitted (market_snapshots is
    # not reconstructed; 0024 never touches risk_runs).
    sa.Table(
        "position_valuation_runs", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("market_source_path", sa.Text()),
        sa.Column("valuation_date", sa.DateTime(), nullable=False),
        sa.Column("overrides", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(40), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_position_ids", sa.JSON()),
        sa.Column(
            "pricing_parameter_profile_id",
            sa.Integer(),
            sa.ForeignKey("pricing_parameter_profiles.id"),
        ),
    )
    sa.Table(
        "risk_runs", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("market_snapshot_id", sa.Integer()),
        sa.Column("method", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scenario_cells", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_position_ids", sa.JSON()),
        sa.Column("pricing_parameter_profile_id", sa.Integer()),
    )
    sa.Table(
        "pricing_parameter_rows", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("pricing_parameter_profiles.id"), nullable=False),
        sa.Column("source_trade_id", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("spot", sa.Float()),  # legacy column folded by 0024
        sa.Column("rate", sa.Float()),
        sa.Column("dividend_yield", sa.Float()),
        sa.Column("volatility", sa.Float()),
        sa.Column("source_row", sa.Integer()),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "position_market_inputs", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id")),
        sa.Column("source_trade_id", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("valuation_date", sa.DateTime(), nullable=False),
        sa.Column("spot", sa.Float()),
        sa.Column("rate", sa.Float()),
        sa.Column("dividend_yield", sa.Float()),
        sa.Column("volatility", sa.Float()),
        sa.Column("source_row", sa.Integer()),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    sa.Table(
        "market_input_import_batches", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sheet", sa.String(120), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(40), nullable=False, server_default="completed"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_hedge_bands_default ON hedge_bands (1) "
            "WHERE underlying_id IS NULL"
        ))
        # Mirror the live 0023 indexes that 0005/0021 created, so the rebuild's
        # index-preservation path is genuinely exercised.
        conn.execute(text(
            "CREATE INDEX ix_hedge_map_entries_underlying_id ON hedge_map_entries (underlying_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_pricing_parameter_rows_profile_id ON pricing_parameter_rows (profile_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_pricing_parameter_rows_source_trade_id ON pricing_parameter_rows (source_trade_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_pricing_parameter_rows_symbol ON pricing_parameter_rows (symbol)"
        ))
        # positions / products / market_data_profiles live indexes (0002/0018/0019)
        for ddl in (
            "CREATE INDEX ix_positions_portfolio_id ON positions (portfolio_id)",
            "CREATE INDEX ix_positions_product_id ON positions (product_id)",
            "CREATE INDEX ix_positions_underlying_id ON positions (underlying_id)",
            "CREATE INDEX ix_positions_rfq_id ON positions (rfq_id)",
            "CREATE INDEX ix_positions_rfq_quote_version_id ON positions (rfq_quote_version_id)",
            "CREATE INDEX ix_positions_source_trade_id ON positions (source_trade_id)",
            "CREATE INDEX ix_positions_trade_effective_date ON positions (trade_effective_date)",
            "CREATE INDEX ix_products_product_family ON products (product_family)",
            "CREATE INDEX ix_products_quantark_class ON products (quantark_class)",
            "CREATE INDEX ix_products_underlying ON products (underlying)",
            "CREATE INDEX ix_products_underlying_id ON products (underlying_id)",
            "CREATE INDEX ix_products_term_hash ON products (term_hash)",
            "CREATE INDEX ix_products_asset_family ON products (asset_class, product_family)",
            "CREATE INDEX ix_market_data_profiles_symbol ON market_data_profiles (symbol)",
            "CREATE INDEX ix_market_data_profiles_underlying_id ON market_data_profiles (underlying_id)",
            "CREATE INDEX ix_position_valuation_runs_portfolio_id ON position_valuation_runs (portfolio_id)",
            "CREATE INDEX ix_position_valuation_runs_pricing_parameter_profile_id ON position_valuation_runs (pricing_parameter_profile_id)",
            "CREATE INDEX ix_risk_runs_portfolio_id ON risk_runs (portfolio_id)",
            "CREATE INDEX ix_risk_runs_pricing_parameter_profile_id ON risk_runs (pricing_parameter_profile_id)",
        ):
            conn.execute(text(ddl))


def _seed(engine: sa.Engine) -> None:
    now = datetime(2026, 6, 4)
    with engine.begin() as conn:
        # underlyings: id=2 LH future-underlier wrong asset_class='index' (real
        # world), id=7 the IC index, id=14 a registry row carrying the akshare
        # SUFFIX style symbol (IC2606.CFE) — the live mismatch that the exchange-
        # agnostic merge must collapse (hedge row arrives as exchange 'CFFEX').
        conn.execute(text(
            "INSERT INTO underlyings (id, symbol, display_name, asset_class, currency, "
            "status, source, rate, dividend_yield, volatility, created_at, updated_at) "
            "VALUES "
            "(2, 'LH2609.DCE', 'LH2609', 'index', 'CNY', 'active', 'positions', 0.023, NULL, NULL, :n, :n), "
            "(7, '000905.SH', 'CSI 500', 'index', 'CNY', 'active', 'manual', 0.021, 0.018, 0.22, :n, :n), "
            "(14, 'IC2606.CFE', 'IC2606', 'index', 'CNY', 'active', 'positions', 0.019, NULL, NULL, :n, :n)"
        ), {"n": now})
        conn.execute(text(
            "INSERT INTO portfolios (id, name, base_currency, kind, manual_include_ids, "
            "manual_exclude_ids, source_portfolio_ids, tags, created_at, updated_at) "
            "VALUES (1, 'Book', 'CNY', 'container', '[]', '[]', '[]', '[]', :n, :n)"
        ), {"n": now})
        conn.execute(text(
            "INSERT INTO positions (id, portfolio_id, underlying_id, underlying, product_type, "
            "product_kwargs, engine_name, engine_kwargs, quantity, entry_price, status, "
            "mapping_status, source_payload, created_at, updated_at) "
            "VALUES (1, 1, 2, 'LH2609.DCE', 'futures', '{}', 'FuturesEngine', '{}', 1, 0, "
            "'open', 'manual', '{}', :n, :n)"
        ), {"n": now})
        # hedge_instruments:
        #  - LH2609@DCE  → exchange matches the registry suffix → merge onto id=2.
        #  - IC2606@CFFEX → registry has IC2606.CFE (id=14): EXCHANGE-AGNOSTIC
        #    prefix merge collapses onto it (keeps symbol IC2606.CFE), not a twin.
        #  - IC2609@CFFEX → no registry row carries this code → INSERT as
        #    IC2609.CFFEX (canonical).
        conn.execute(text(
            "INSERT INTO hedge_instruments (id, underlying_id, family, series_root, exchange, "
            "contract_code, instrument_type, multiplier, last_price, status, loaded_at, "
            "created_at, updated_at) VALUES "
            "(1, 2, 'lean_hog_future', 'LH', 'DCE', 'LH2609', 'future', 16, 13905.0, 'live', "
            ":h, :n, :n), "
            "(2, 14, 'index_future', 'IC', 'CFFEX', 'IC2606', 'future', 200, 6398.2, 'live', "
            ":h, :n, :n), "
            "(3, 7, 'index_future', 'IC', 'CFFEX', 'IC2609', 'future', 200, 6450.5, 'live', "
            ":h, :n, :n)"
        ), {"n": now, "h": datetime(2026, 6, 3, 10, 0, 0)})
        # market_data_profile for id=7 with headline spot
        conn.execute(text(
            "INSERT INTO market_data_profiles (id, underlying_id, name, source, symbol, "
            "asset_class, start_date, end_date, adjust, valuation_date, data, source_metadata, "
            "created_at, updated_at) VALUES "
            "(11, 7, '000905.SH profile', 'akshare', '000905.SH', 'index', '2026-01-01', "
            "'2026-06-04', 'qfq', :v, :d, '{}', :n, :n)"
        ), {"n": now, "v": datetime(2026, 6, 4), "d": json.dumps({"spot": 6412.55})})
        # pricing_parameter_profiles: 31 xlsx (kept), 32 default_underlying (split→assumptions)
        conn.execute(text(
            "INSERT INTO pricing_parameter_profiles (id, name, valuation_date, source_type, "
            "status, summary, created_at, updated_at) VALUES "
            "(31, 'Trade params', :v, 'xlsx', 'completed', '{}', :n, :n), "
            "(32, 'Default underlyings', :v2, 'default_underlying', 'completed', '{}', :n, :n)"
        ), {"n": now, "v": datetime(2026, 6, 4), "v2": datetime(2026, 6, 1)})
        conn.execute(text(
            "INSERT INTO pricing_parameter_rows (id, profile_id, source_trade_id, symbol, spot, "
            "rate, dividend_yield, volatility, source_payload, created_at, updated_at) VALUES "
            "(101, 31, 'T-0142', '000905.SH', 6411.0, NULL, NULL, 0.221, '{}', :n, :n), "
            "(102, 32, '', '000905.SH', 6300.0, 0.02, 0.017, 0.215, '{}', :n, :n)"
        ), {"n": now})
        # position_market_inputs: T-0166 LH spot + vol
        conn.execute(text(
            "INSERT INTO position_market_inputs (id, portfolio_id, position_id, source_trade_id, "
            "symbol, valuation_date, spot, volatility, source_payload, created_at, updated_at) "
            "VALUES (201, 1, 1, 'T-0166', 'LH2609.DCE', :v, 13800.0, 0.314, '{}', :n, :n)"
        ), {"n": now, "v": datetime(2026, 6, 2)})
        # hedge_map_entry: points at IC2606@CFFEX. Exchange-agnostic backfill
        # resolves it to the MERGED instrument (id=14, symbol IC2606.CFE), proving
        # a canonical IC2606.CFFEX lookup is not required.
        conn.execute(text(
            "INSERT INTO hedge_map_entries (id, underlying_id, exchange, contract_code, family, "
            "series_root, instrument_type, reconcile_status, marked_at, created_at, updated_at) "
            "VALUES (301, 7, 'CFFEX', 'IC2606', 'index_future', 'IC', 'future', 'active', :n, :n, :n)"
        ), {"n": now})
        # Historical runs referencing the default_underlying profile (id=32): a
        # REAL FK on position_valuation_runs, a soft ref on risk_runs. They must
        # survive 0024 (profile RETAGGED, not deleted) with no FK violation.
        conn.execute(text(
            "INSERT INTO position_valuation_runs (id, portfolio_id, valuation_date, overrides, "
            "summary, status, created_at, pricing_parameter_profile_id) "
            "VALUES (501, 1, :v, '{}', '{}', 'completed', :n, 32)"
        ), {"n": now, "v": datetime(2026, 6, 1)})
        conn.execute(text(
            "INSERT INTO risk_runs (id, portfolio_id, method, status, metrics, created_at, "
            "pricing_parameter_profile_id) "
            "VALUES (601, 1, 'greeks', 'completed', '{}', :n, 32)"
        ), {"n": now})


def _run_migration(engine: sa.Engine) -> None:
    migration = importlib.import_module(
        "backend.alembic.versions.0024_instrument_unification"
    )
    position_kind = importlib.import_module(
        "backend.alembic.versions.0025_position_kind"
    )
    connection = engine.connect()
    ctx = MigrationContext.configure(connection)
    original_op = migration.op
    original_position_kind_op = position_kind.op
    migration.op = Operations(ctx)
    position_kind.op = migration.op
    try:
        migration.upgrade()
        position_kind.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        position_kind.op = original_position_kind_op
        connection.close()


def test_migration_0024_unifies_instruments(tmp_path: Path):
    db_path = tmp_path / "m.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    _build_pre_0024_schema(engine)
    _seed(engine)
    _run_migration(engine)

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    # --- rename: underlyings GONE, instruments present, legacy contract tables gone
    assert "instruments" in tables
    for gone in (
        "underlyings", "hedge_instruments", "position_market_inputs",
        "market_input_import_batches",
    ):
        assert gone not in tables, f"{gone} should be dropped"

    with engine.connect() as conn:
        # --- id=2: merged from hedge_instruments LH2609.DCE
        row = conn.execute(text(
            "SELECT kind, contract_code, multiplier, status, rate, series_root, exchange, "
            "loaded_at, source FROM instruments WHERE id = 2"
        )).mappings().one()
        assert row["kind"] == "futures"          # instrument_type 'future' → 'futures'
        assert row["contract_code"] == "LH2609"
        assert row["multiplier"] == 16
        assert row["status"] == "active"
        assert row["rate"] == 0.023              # curated field PRESERVED (not clobbered)
        assert row["series_root"] == "LH"
        assert row["exchange"] == "DCE"
        assert row["loaded_at"] is not None
        assert row["source"] == "positions"      # original source preserved on merge

        # --- id=14: IC2606 MERGED (exchange-agnostic). Registry symbol IC2606.CFE
        # is kept (NOT a IC2606.CFFEX twin); terms come from the feed.
        ic = conn.execute(text(
            "SELECT id, symbol, kind, source, multiplier, contract_code, rate FROM instruments "
            "WHERE id = 14"
        )).mappings().one()
        assert ic["symbol"] == "IC2606.CFE"      # registry style kept, no twin
        assert ic["kind"] == "futures"
        assert ic["source"] == "positions"       # original source preserved on merge
        assert ic["multiplier"] == 200           # multiplier from the feed
        assert ic["contract_code"] == "IC2606"
        assert ic["rate"] == 0.019               # curated field PRESERVED
        ic_id = ic["id"]
        # exactly ONE instrument carries this contract code — no duplicate twin.
        assert conn.execute(text(
            "SELECT COUNT(*) FROM instruments WHERE contract_code = 'IC2606'"
        )).scalar() == 1
        assert conn.execute(text(
            "SELECT COUNT(*) FROM instruments WHERE symbol = 'IC2606.CFFEX'"
        )).scalar() == 0

        # --- IC2609@CFFEX: no registry row carried it → INSERT under canonical symbol
        ic2609 = conn.execute(text(
            "SELECT id, kind, source, multiplier, contract_code FROM instruments "
            "WHERE symbol = 'IC2609.CFFEX'"
        )).mappings().one()
        assert ic2609["kind"] == "futures"
        assert ic2609["source"] == "hedge_load"
        assert ic2609["multiplier"] == 200
        assert ic2609["contract_code"] == "IC2609"
        ic2609_id = ic2609["id"]

        # --- positions: value unchanged, FK retargeted to instruments
        assert conn.execute(text(
            "SELECT underlying_id FROM positions WHERE id = 1"
        )).scalar() == 2
        pos_fks = conn.execute(text("PRAGMA foreign_key_list(positions)")).mappings().all()
        assert any(fk["table"] == "instruments" and fk["from"] == "underlying_id" for fk in pos_fks)

        # --- market_quotes: 6 observations (profile + 3 hedge + pricing-row + market-input)
        quotes = conn.execute(text(
            "SELECT instrument_id, price, source, price_type, as_of FROM market_quotes ORDER BY price"
        )).mappings().all()
        assert len(quotes) == 6
        by_price = {round(q["price"], 2): q for q in quotes}
        # profile headline spot
        assert 6412.55 in by_price
        assert by_price[6412.55]["source"] == "akshare"
        assert by_price[6412.55]["instrument_id"] == 7
        assert str(by_price[6412.55]["as_of"]).startswith("2026-06-04")
        # LH hedge last_price → instrument 2, source hedge_load, as_of loaded_at
        assert 13905.0 in by_price
        assert by_price[13905.0]["source"] == "hedge_load"
        assert by_price[13905.0]["instrument_id"] == 2
        assert str(by_price[13905.0]["as_of"]).startswith("2026-06-03 10:00")
        # IC2606 hedge last_price → the MERGED instrument (id=14), resolved by
        # the exchange-agnostic prefix (not a canonical IC2606.CFFEX lookup)
        assert 6398.2 in by_price
        assert by_price[6398.2]["instrument_id"] == ic_id
        # IC2609 hedge last_price → the freshly-inserted instrument
        assert 6450.5 in by_price
        assert by_price[6450.5]["instrument_id"] == ic2609_id
        # pricing-row legacy spot 6411.0
        assert 6411.0 in by_price
        assert by_price[6411.0]["source"] == "legacy"
        assert by_price[6411.0]["instrument_id"] == 7
        # market-input legacy spot 13800.0
        assert 13800.0 in by_price
        assert by_price[13800.0]["source"] == "legacy"
        assert by_price[13800.0]["instrument_id"] == 2

        # --- pricing_parameter_rows: spot column dropped; T-0142 backfilled instrument_id
        ppr_cols = {c["name"] for c in insp.get_columns("pricing_parameter_rows")}
        assert "spot" not in ppr_cols
        assert "instrument_id" in ppr_cols
        assert conn.execute(text(
            "SELECT instrument_id FROM pricing_parameter_rows WHERE source_trade_id = 'T-0142'"
        )).scalar() == 7

        # --- profile 32 split into assumption_sets/rows; the assumption_set IS
        # created, but (defect 1) the profile is RETAGGED, not deleted.
        sets = conn.execute(text(
            "SELECT id, valuation_date FROM assumption_sets"
        )).mappings().all()
        assert len(sets) == 1
        assert str(sets[0]["valuation_date"]).startswith("2026-06-01")
        arows = conn.execute(text(
            "SELECT rate, volatility, dividend_yield, instrument_id, symbol FROM assumption_rows"
        )).mappings().all()
        assert len(arows) == 1
        assert arows[0]["rate"] == 0.02
        assert arows[0]["volatility"] == 0.215
        assert arows[0]["instrument_id"] == 7
        # No live 'default_underlying' profiles remain — all retagged.
        assert conn.execute(text(
            "SELECT COUNT(*) FROM pricing_parameter_profiles WHERE source_type = 'default_underlying'"
        )).scalar() == 0
        # Profile 32 SURVIVES, retagged to 'default_underlying_archived' (so the
        # FK-bearing position_valuation_runs / soft-ref risk_runs stay valid).
        prof32 = conn.execute(text(
            "SELECT source_type, name FROM pricing_parameter_profiles WHERE id = 32"
        )).mappings().one()
        assert prof32["source_type"] == "default_underlying_archived"
        assert prof32["name"] == "Default underlyings"
        # Its original row survives too — but WITHOUT the dropped spot column.
        baseline = conn.execute(text(
            "SELECT rate, volatility FROM pricing_parameter_rows "
            "WHERE profile_id = 32 AND symbol = '000905.SH'"
        )).mappings().all()
        assert len(baseline) == 1
        assert baseline[0]["rate"] == 0.02
        # the runs referencing profile 32 are intact, and the FK is consistent
        assert conn.execute(text(
            "SELECT pricing_parameter_profile_id FROM position_valuation_runs WHERE id = 501"
        )).scalar() == 32
        assert conn.execute(text(
            "SELECT pricing_parameter_profile_id FROM risk_runs WHERE id = 601"
        )).scalar() == 32
        # CRITICAL: no orphaned FKs anywhere post-migration.
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        # profile 31 (xlsx) survives with its trade row
        assert conn.execute(text(
            "SELECT COUNT(*) FROM pricing_parameter_profiles WHERE id = 31"
        )).scalar() == 1
        assert conn.execute(text(
            "SELECT COUNT(*) FROM pricing_parameter_rows WHERE source_trade_id = 'T-0142'"
        )).scalar() == 1

        # --- position_market_inputs r/q/vol folded into a synthetic profile
        synth = conn.execute(text(
            "SELECT id, name FROM pricing_parameter_profiles "
            "WHERE name LIKE 'Migrated position market inputs%'"
        )).mappings().all()
        assert len(synth) == 1
        synth_rows = conn.execute(text(
            "SELECT source_trade_id, volatility, instrument_id FROM pricing_parameter_rows "
            "WHERE profile_id = :p"
        ), {"p": synth[0]["id"]}).mappings().all()
        assert len(synth_rows) == 1
        assert synth_rows[0]["source_trade_id"] == "T-0166"
        assert synth_rows[0]["volatility"] == 0.314
        assert synth_rows[0]["instrument_id"] == 2

        # --- hedge_map_entries: instrument_id backfilled (exchange-agnostically)
        # to the MERGED IC2606.CFE instrument (id=14), NOT a IC2606.CFFEX twin.
        assert conn.execute(text(
            "SELECT instrument_id FROM hedge_map_entries WHERE id = 301"
        )).scalar() == ic_id

        # --- hedge_bands rebuild preserved the partial-unique defaults guard and
        # retargeted its FK to instruments. Assert the index is not just present
        # by name but is genuinely UNIQUE and carries the `underlying_id IS NULL`
        # predicate (a plain re-create would silently drop the partial guard).
        hb_default_sql = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_hedge_bands_default'"
        )).scalar()
        assert hb_default_sql is not None, "uq_hedge_bands_default index missing"
        normalized = " ".join(hb_default_sql.split()).upper()
        assert "UNIQUE" in normalized
        assert "WHERE UNDERLYING_ID IS NULL" in normalized
        hb_fks = conn.execute(text("PRAGMA foreign_key_list(hedge_bands)")).mappings().all()
        assert any(fk["table"] == "instruments" for fk in hb_fks)

    # --- the migrated schema must equal the ORM create_all target (column sets,
    # FK targets, named indexes) for every table 0024 touches. This is the
    # live-DB-vs-fresh-DB equivalence the rest of the unification depends on.
    _assert_schema_matches_orm(insp)


def _assert_schema_matches_orm(migrated_insp) -> None:
    """Build a fresh ORM (create_all) DB and assert table parity."""
    import tempfile

    from app.config import Settings
    import app.config as cfg
    import app.database as database

    tmp = tempfile.mkdtemp()
    s = Settings(database_url=f"sqlite:///{tmp}/orm.db")
    orig = cfg.get_settings
    cfg.get_settings = lambda: s
    try:
        database.configure_database(s)
        database.init_db()
        orm_insp = inspect(database.engine)

        def cols(insp, t):
            return {c["name"] for c in insp.get_columns(t)}

        def fks(insp, t):
            return {
                (tuple(f["constrained_columns"]), f["referred_table"])
                for f in insp.get_foreign_keys(t)
            }

        def idx(insp, t):
            return {i["name"] for i in insp.get_indexes(t) if i.get("name")}

        for t in (
            "instruments", "positions", "products", "market_data_profiles",
            "hedge_map_entries", "hedge_bands", "pricing_parameter_rows",
            "market_quotes", "assumption_sets", "assumption_rows",
        ):
            assert cols(migrated_insp, t) == cols(orm_insp, t), f"{t} columns differ"
            assert fks(migrated_insp, t) == fks(orm_insp, t), f"{t} FKs differ"
            assert idx(migrated_insp, t) == idx(orm_insp, t), f"{t} indexes differ"
    finally:
        cfg.get_settings = orig
        # restore the app default DB binding for any later in-process tests
        database.configure_database(orig())


def test_migration_0024_drops_empty_init_db_instruments(tmp_path: Path):
    """If a zero-row ``instruments`` table already exists (init_db artifact), it
    is dropped before the rename rather than colliding."""
    db_path = tmp_path / "m.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    _build_pre_0024_schema(engine)
    _seed(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT)"))
    _run_migration(engine)  # must not raise
    insp = inspect(engine)
    assert "instruments" in insp.get_table_names()
    with engine.connect() as conn:
        # real data carried through (LH merge happened)
        assert conn.execute(text(
            "SELECT contract_code FROM instruments WHERE id = 2"
        )).scalar() == "LH2609"


def test_migration_0024_raises_on_nonempty_instruments(tmp_path: Path):
    """A non-empty pre-existing ``instruments`` table must abort the migration —
    never merge silently over real rows."""
    import pytest

    db_path = tmp_path / "m.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    _build_pre_0024_schema(engine)
    _seed(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT)"))
        conn.execute(text("INSERT INTO instruments (id, symbol) VALUES (99, 'X')"))
    with pytest.raises(RuntimeError):
        _run_migration(engine)
