"""instrument unification — id-preserving rename, contract merge, quote backfill

Revision ID: 0024_instrument_unification
Revises: 0023_hedge_bands_default_unique
Create Date: 2026-06-05

Carries the LIVE production book across the underlyings→instruments rename. Data
preservation is the whole point: position/product/profile/hedge-map FKs keep
their (unchanged) integer values, only their FK *target* is retargeted; curated
columns (rate/dividend_yield/volatility/currency/notes) are never clobbered.

Two live-DB-integrity rules, found by a dry-run review, shape the data steps:

  * Legacy ``default_underlying`` pricing-parameter profiles are RETAGGED to
    ``source_type='default_underlying_archived'`` (not deleted). Historical
    ``position_valuation_runs`` / ``risk_runs`` carry a
    ``pricing_parameter_profile_id`` pointing at them (a REAL FK for the former),
    so deletion would orphan runs / violate FK on a live ``PRAGMA
    foreign_keys=ON`` connection. The new assumption_sets remain the canonical
    home; the archived profile rows stay only to preserve run referential
    integrity. The spot column drop later strips their spots like everyone else's.

  * The hedge_instruments→instruments merge keys on an EXCHANGE-AGNOSTIC prefix
    match (``instruments.symbol LIKE contract_code || '.%'``), because live
    registry symbols use the akshare-suffix style (``IC2606.CFE``) while
    hedge_instruments.exchange holds ``CFFEX``/``SHFE``/``SSE``/``UNKNOWN`` — a
    ``contract_code || '.' || exchange`` key never collides, duplicating every
    position-referenced contract. Contract codes embed series+expiry, so a
    cross-exchange code collision is implausible; we assert it (RuntimeError on
    >1 match). The existing instrument's symbol (registry style, position-
    referenced) is kept on merge.

HOUSE RULE: all schema + data ops use migration-local Core SQL / sa.Table on a
fresh MetaData. We never import app models/services — they drift to the future
schema (this exact mistake once broke 0018). Mirrors the 0020/0021 batch style.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0024_instrument_unification"
down_revision = "0023_hedge_bands_default_unique"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


# Canonical listed-contract symbol mirrors hedging_loader: f"{contract_code}.{exchange}".


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables()

    # --- Step 0: safety on a pre-existing ``instruments`` (init_db create_all
    # artifact). Empty → drop and proceed with the rename. Non-empty → abort;
    # we must never silently merge over real instrument rows.
    if "instruments" in tables:
        count = bind.execute(text("SELECT COUNT(*) FROM instruments")).scalar() or 0
        if count == 0:
            op.drop_table("instruments")
            tables = _tables()
        else:
            raise RuntimeError(
                "0024: an 'instruments' table already exists with %d row(s). "
                "Refusing to merge over it — restore from a pre-0024 backup and "
                "re-run the upgrade on the legacy 'underlyings' schema only." % count
            )

    if "underlyings" not in tables:
        # Already at/after the target (e.g. a fresh DB built by 0001 create_all).
        # Nothing to migrate.
        return

    # --- Step 1: id-preserving rename underlyings → instruments, add the
    # contract-term columns, rename asset_class → kind, fix index names.
    op.rename_table("underlyings", "instruments")
    with op.batch_alter_table("instruments") as batch:
        batch.alter_column("asset_class", new_column_name="kind")
        batch.add_column(sa.Column("contract_code", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("series_root", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("expiry", sa.Date(), nullable=True))
        batch.add_column(sa.Column("multiplier", sa.Float(), nullable=True))
        batch.add_column(sa.Column("strike", sa.Float(), nullable=True))
        batch.add_column(sa.Column("option_type", sa.String(length=4), nullable=True))
        batch.add_column(
            sa.Column(
                "parent_id",
                sa.Integer(),
                sa.ForeignKey("instruments.id", name="fk_instruments_parent_id"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("loaded_at", sa.DateTime(), nullable=True))

    # The renamed table inherited ix_underlyings_* indexes; recreate under the
    # ORM names (ix_instruments_*) and add the new contract indexes.
    instr_idx = _indexes("instruments")
    for legacy in ("ix_underlyings_symbol", "ix_underlyings_akshare_symbol", "ix_underlyings_status"):
        if legacy in instr_idx:
            op.drop_index(legacy, table_name="instruments")
    for name, cols, unique in (
        ("ix_instruments_symbol", ["symbol"], True),
        ("ix_instruments_akshare_symbol", ["akshare_symbol"], False),
        ("ix_instruments_status", ["status"], False),
        ("ix_instruments_kind_status", ["kind", "status"], False),
        ("ix_instruments_series_root_kind", ["series_root", "kind"], False),
        ("ix_instruments_contract_code", ["contract_code"], False),
        ("ix_instruments_parent_id", ["parent_id"], False),
    ):
        if name not in _indexes("instruments"):
            op.create_index(name, "instruments", cols, unique=unique)

    # --- Step 2: retarget FK-bearing tables → instruments.id (values unchanged).
    # SQLite stores the FK target as literal DDL text; the rename above did NOT
    # update child tables, so each still says REFERENCES underlyings. Rebuild via
    # batch with an explicit replacement FK so the new DDL references instruments.
    _retarget_fk(
        "positions",
        [("underlying_id", "instruments", "id")],
        keep_fks=[
            ("portfolio_id", "portfolios", "id"),
            ("product_id", "products", "id"),
            ("rfq_id", "rfqs", "id"),
            ("rfq_quote_version_id", "rfq_quote_versions", "id"),
        ],
    )
    _retarget_fk(
        "products",
        [("underlying_id", "instruments", "id")],
        keep_fks=[],
    )
    _retarget_fk(
        "market_data_profiles",
        [("underlying_id", "instruments", "id")],
        keep_fks=[],
    )
    _retarget_fk(
        "hedge_bands",
        [("underlying_id", "instruments", "id")],
        keep_fks=[],
    )
    _retarget_fk(
        "hedge_map_entries",
        [("underlying_id", "instruments", "id")],
        keep_fks=[],
    )
    # hedge_map_entries GAINS instrument_id (nullable FK + index). Done as its own
    # batch so the FK target table (instruments) already exists and the rebuild's
    # INSERT…SELECT doesn't reference the not-yet-created column.
    if "instrument_id" not in _columns("hedge_map_entries"):
        with op.batch_alter_table("hedge_map_entries") as batch:
            batch.add_column(
                sa.Column(
                    "instrument_id",
                    sa.Integer(),
                    sa.ForeignKey("instruments.id", name="fk_hedge_map_entries_instrument_id"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_hedge_map_entries_instrument_id", "hedge_map_entries", ["instrument_id"]
        )

    # --- Step 3 (cont.): create market_quotes / assumption_sets / assumption_rows
    # mirroring the ORM exactly, and add pricing_parameter_rows.instrument_id.
    _create_market_quotes()
    _create_assumption_tables()
    if "instrument_id" not in _columns("pricing_parameter_rows"):
        with op.batch_alter_table("pricing_parameter_rows") as batch:
            batch.add_column(
                sa.Column(
                    "instrument_id",
                    sa.Integer(),
                    sa.ForeignKey("instruments.id", name="fk_pricing_parameter_rows_instrument_id"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_pricing_parameter_rows_instrument_id",
            "pricing_parameter_rows",
            ["instrument_id"],
        )

    # --- Step 4: merge hedge_instruments → instruments (LH-style identity merge).
    _merge_hedge_instruments(bind)

    # --- Step 5: backfill market_quotes (honest as_of, every observation).
    _backfill_quotes(bind)

    # --- Step 6: backfill instrument_id by symbol match.
    _backfill_instrument_ids(bind)

    # --- Step 7: split legacy default_underlying profiles → assumption_sets/rows,
    # then RETAG (not delete) the profiles so runs referencing them stay valid.
    _split_default_underlying_profiles(bind)

    # --- Step 8: fold position_market_inputs r/q/vol into synthetic profiles,
    # then drop the inputs/batch tables.
    _fold_position_market_inputs(bind)
    op.drop_table("position_market_inputs")
    if "market_input_import_batches" in _tables():
        op.drop_table("market_input_import_batches")

    # --- Step 9: drop pricing_parameter_rows.spot (read by step 5 already).
    if "spot" in _columns("pricing_parameter_rows"):
        with op.batch_alter_table("pricing_parameter_rows") as batch:
            batch.drop_column("spot")

    # --- Step 10: drop hedge_instruments (merged in step 4, read in step 5).
    if "hedge_instruments" in _tables():
        op.drop_table("hedge_instruments")


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _retarget_fk(table: str, retarget, keep_fks) -> None:
    """Recreate ``table`` so its foreign keys reference the new target tables.

    SQLite does not update child FK DDL when a parent is renamed, so we rebuild
    the table with batch_alter_table in 'always recreate' mode. We feed an
    explicit ``copy_from`` Table whose ForeignKeyConstraints already point at the
    correct targets; alembic emits the new CREATE TABLE from it and copies rows.
    """
    if table not in _tables():
        return

    insp = inspect(op.get_bind())
    meta = sa.MetaData()
    cols = [
        sa.Column(
            c["name"],
            c["type"],
            nullable=c["nullable"],
            primary_key=bool(c.get("primary_key")),
        )
        for c in insp.get_columns(table)
    ]

    fk_map = {col: (tbl, ref) for (col, tbl, ref) in [*keep_fks, *retarget]}
    constraints = [
        sa.ForeignKeyConstraint([col_name], [f"{tbl}.{ref}"])
        for col_name, (tbl, ref) in fk_map.items()
    ]
    # Preserve unique constraints.
    for uq in insp.get_unique_constraints(table):
        constraints.append(sa.UniqueConstraint(*uq["column_names"], name=uq["name"]))

    copy_from = sa.Table(table, meta, *cols, *constraints)

    # Indexes to recreate after the rebuild (batch drops them).
    existing_indexes = [
        (i["name"], list(i["column_names"]), bool(i["unique"]))
        for i in insp.get_indexes(table)
        if i.get("name")
    ]
    # Expression-based / partial indexes (e.g. hedge_bands' uq_hedge_bands_default
    # partial-unique guard) cannot be reflected by the inspector. Capture their raw
    # CREATE DDL from sqlite_master so we can re-issue them verbatim after rebuild.
    raw_index_ddl = {
        r[0]: r[1]
        for r in op.get_bind().execute(text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"
        ), {"t": table})
    }
    reflected_names = {name for name, _, _ in existing_indexes}

    with op.batch_alter_table(table, copy_from=copy_from, recreate="always"):
        pass

    have = _indexes(table)
    for name, icols, unique in existing_indexes:
        if name not in have:
            op.create_index(name, table, icols, unique=unique)
    # Re-issue any non-reflectable indexes (expression/partial) that the rebuild
    # dropped, using their original DDL.
    for name, ddl in raw_index_ddl.items():
        if name in reflected_names or name in _indexes(table):
            continue
        op.get_bind().execute(text(ddl))


def _create_market_quotes() -> None:
    if "market_quotes" in _tables():
        return
    op.create_table(
        "market_quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("price_type", sa.String(length=12), nullable=False, server_default="close"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column(
            "market_data_profile_id",
            sa.Integer(),
            sa.ForeignKey("market_data_profiles.id"),
            nullable=True,
        ),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_market_quotes_instrument_id", "market_quotes", ["instrument_id"])
    op.create_index(
        "ix_market_quotes_instrument_as_of", "market_quotes", ["instrument_id", "as_of"]
    )


def _create_assumption_tables() -> None:
    if "assumption_sets" not in _tables():
        op.create_table(
            "assumption_sets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="completed"),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_assumption_sets_valuation_date", "assumption_sets", ["valuation_date"]
        )
    if "assumption_rows" not in _tables():
        op.create_table(
            "assumption_rows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("set_id", sa.Integer(), sa.ForeignKey("assumption_sets.id"), nullable=False),
            sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("volatility", sa.Float(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_assumption_rows_set_id", "assumption_rows", ["set_id"])
        op.create_index(
            "ix_assumption_rows_instrument_id", "assumption_rows", ["instrument_id"]
        )
        op.create_index("ix_assumption_rows_symbol", "assumption_rows", ["symbol"])


def _like_prefix(contract_code: str) -> str:
    """LIKE pattern matching ``<contract_code>.<anything>`` with wildcards in the
    code escaped (option codes may legitimately contain ``_``). Pairs with
    ``ESCAPE '\\'`` on the LIKE."""
    escaped = (
        (contract_code or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"{escaped}.%"


def _merge_hedge_instruments(bind) -> None:
    if "hedge_instruments" not in _tables():
        return
    rows = bind.execute(text(
        "SELECT id, contract_code, exchange, instrument_type, option_type, series_root, "
        "       expiry, multiplier, strike, akshare_symbol, status, loaded_at "
        "FROM hedge_instruments"
    )).mappings().all()
    for r in rows:
        canonical = f"{r['contract_code']}.{r['exchange']}"
        kind = "listed_option" if r["option_type"] else "futures"
        status = "expired" if r["status"] == "expired" else "active"
        # Exchange-agnostic identity: a registry row may carry the akshare-suffix
        # style (IC2606.CFE) while hedge_instruments.exchange is CFFEX — an exact
        # ``contract_code.exchange`` key never collides, duplicating the contract.
        # Match on the contract-code prefix instead (codes embed series+expiry,
        # so cross-exchange collisions are implausible — assert uniqueness).
        matches = bind.execute(
            text(
                "SELECT id, symbol, akshare_symbol FROM instruments "
                "WHERE symbol LIKE :p ESCAPE '\\'"
            ),
            {"p": _like_prefix(r["contract_code"])},
        ).mappings().all()
        if len(matches) > 1:
            raise RuntimeError(
                "0024: contract_code %r prefix-matched %d instruments (%s); "
                "ambiguous merge — resolve the duplicate symbols before re-running."
                % (
                    r["contract_code"],
                    len(matches),
                    ", ".join(sorted(m["symbol"] for m in matches)),
                )
            )
        existing = matches[0] if matches else None
        params = {
            "kind": kind,
            "contract_code": r["contract_code"],
            "series_root": r["series_root"],
            "exchange": r["exchange"],
            "expiry": r["expiry"],
            "multiplier": r["multiplier"],
            "strike": r["strike"],
            "option_type": r["option_type"],
            "akshare_symbol": r["akshare_symbol"],
            "status": status,
            "loaded_at": r["loaded_at"],
        }
        if existing is not None:
            # Identity merge: feed terms authoritative; akshare_symbol only when
            # the instrument's is null; curated currency/notes/r/q/vol untouched.
            # The existing ``symbol`` is KEPT (registry style is position-
            # referenced, load-bearing) — only the term columns are refreshed.
            set_akshare = existing["akshare_symbol"] is None and r["akshare_symbol"] is not None
            sql = (
                "UPDATE instruments SET kind=:kind, contract_code=:contract_code, "
                "series_root=:series_root, exchange=:exchange, expiry=:expiry, "
                "multiplier=:multiplier, strike=:strike, option_type=:option_type, "
                "status=:status, loaded_at=:loaded_at"
            )
            if set_akshare:
                sql += ", akshare_symbol=:akshare_symbol"
            sql += " WHERE id = :id"
            params["id"] = existing["id"]
            bind.execute(text(sql), params)
        else:
            # New instrument. parent_id left NULL: the loader repopulates it on the
            # next contract load (and curation is possible in the UI). No registry
            # row carried this code, so insert under the canonical symbol.
            params["symbol"] = canonical
            bind.execute(text(
                "INSERT INTO instruments "
                "(symbol, kind, contract_code, series_root, exchange, expiry, multiplier, "
                " strike, option_type, akshare_symbol, status, source, currency, "
                " created_at, updated_at) "
                "VALUES (:symbol, :kind, :contract_code, :series_root, :exchange, :expiry, "
                ":multiplier, :strike, :option_type, :akshare_symbol, :status, 'hedge_load', "
                "'CNY', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), params)


def _instrument_id_for_symbol(bind, symbol: str):
    if symbol is None:
        return None
    return bind.execute(
        text("SELECT id FROM instruments WHERE symbol = :s"), {"s": symbol}
    ).scalar()


def _instrument_id_for_contract(bind, contract_code: str):
    """Resolve the instrument carrying ``contract_code`` by the exchange-agnostic
    ``symbol LIKE contract_code || '.%'`` prefix (the merge keeps the existing
    registry symbol). Returns None when unresolved; on the post-merge >1 match it
    is a genuine ambiguity, so skip the quote rather than guess."""
    if not contract_code:
        return None
    ids = bind.execute(
        text("SELECT id FROM instruments WHERE symbol LIKE :p ESCAPE '\\'"),
        {"p": _like_prefix(contract_code)},
    ).scalars().all()
    return ids[0] if len(ids) == 1 else None


def _backfill_quotes(bind) -> None:
    # (a) market_data_profiles headline spot.
    profiles = bind.execute(text(
        "SELECT id, underlying_id, valuation_date, "
        "       json_extract(data, '$.spot') AS spot "
        "FROM market_data_profiles"
    )).mappings().all()
    for p in profiles:
        if p["spot"] is None or p["underlying_id"] is None:
            continue
        bind.execute(text(
            "INSERT INTO market_quotes "
            "(instrument_id, as_of, price, price_type, source, market_data_profile_id, "
            " created_at) "
            "VALUES (:iid, :as_of, :price, 'close', 'akshare', :pid, CURRENT_TIMESTAMP)"
        ), {
            "iid": p["underlying_id"],
            "as_of": p["valuation_date"],
            "price": p["spot"],
            "pid": p["id"],
        })

    # (b) hedge_instruments.last_price → the merged/inserted instrument. Resolve
    # by the SAME exchange-agnostic contract-code prefix the merge used: a merged
    # row kept the existing registry symbol (IC2606.CFE), so a canonical
    # ``code.exchange`` lookup would miss it.
    if "hedge_instruments" in _tables():
        hedge = bind.execute(text(
            "SELECT contract_code, exchange, last_price, loaded_at "
            "FROM hedge_instruments WHERE last_price IS NOT NULL"
        )).mappings().all()
        for h in hedge:
            iid = _instrument_id_for_contract(bind, h["contract_code"])
            if iid is None:
                continue
            bind.execute(text(
                "INSERT INTO market_quotes "
                "(instrument_id, as_of, price, price_type, source, created_at) "
                "VALUES (:iid, :as_of, :price, 'last', 'hedge_load', CURRENT_TIMESTAMP)"
            ), {"iid": iid, "as_of": h["loaded_at"], "price": h["last_price"]})

    # (c) pricing_parameter_rows.spot (legacy) — as_of = profile.valuation_date.
    # default_underlying profiles are not trade observations; their spots are
    # baseline defaults that step 7 splits into assumption_sets and deletes, so
    # they must NOT become market_quotes.
    if "spot" in _columns("pricing_parameter_rows"):
        rows = bind.execute(text(
            "SELECT r.id AS row_id, r.source_trade_id, r.symbol, r.spot, r.profile_id, "
            "       p.valuation_date "
            "FROM pricing_parameter_rows r "
            "JOIN pricing_parameter_profiles p ON p.id = r.profile_id "
            "WHERE r.spot IS NOT NULL AND p.source_type != 'default_underlying'"
        )).mappings().all()
        for r in rows:
            iid = _instrument_id_for_symbol(bind, r["symbol"])
            if iid is None:
                continue  # skip unresolvable (counted by the row not landing a quote)
            bind.execute(text(
                "INSERT INTO market_quotes "
                "(instrument_id, as_of, price, price_type, source, meta, created_at) "
                "VALUES (:iid, :as_of, :price, 'mid', 'legacy', :meta, CURRENT_TIMESTAMP)"
            ), {
                "iid": iid,
                "as_of": r["valuation_date"],
                "price": r["spot"],
                "meta": json.dumps({
                    "trade_id": r["source_trade_id"],
                    "profile_id": r["profile_id"],
                }),
            })

    # (d) position_market_inputs.spot (legacy).
    if "position_market_inputs" in _tables() and "spot" in _columns("position_market_inputs"):
        rows = bind.execute(text(
            "SELECT source_trade_id, symbol, valuation_date, spot "
            "FROM position_market_inputs WHERE spot IS NOT NULL"
        )).mappings().all()
        for r in rows:
            iid = _instrument_id_for_symbol(bind, r["symbol"])
            if iid is None:
                continue
            bind.execute(text(
                "INSERT INTO market_quotes "
                "(instrument_id, as_of, price, price_type, source, meta, created_at) "
                "VALUES (:iid, :as_of, :price, 'mid', 'legacy', :meta, CURRENT_TIMESTAMP)"
            ), {
                "iid": iid,
                "as_of": r["valuation_date"],
                "price": r["spot"],
                "meta": json.dumps({"trade_id": r["source_trade_id"]}),
            })


def _backfill_instrument_ids(bind) -> None:
    # pricing_parameter_rows: symbol → instruments.symbol.
    bind.execute(text(
        "UPDATE pricing_parameter_rows SET instrument_id = ("
        "  SELECT i.id FROM instruments i WHERE i.symbol = pricing_parameter_rows.symbol"
        ") WHERE instrument_id IS NULL"
    ))
    # hedge_map_entries: exchange-agnostic contract-code prefix → instruments. A
    # canonical ``contract_code.exchange`` match would miss merged contracts that
    # kept a registry-style symbol (IC2606.CFE vs map entry IC2606@CFFEX). Assign
    # only when the prefix resolves to exactly one instrument.
    if "instrument_id" in _columns("hedge_map_entries"):
        entries = bind.execute(text(
            "SELECT id, contract_code FROM hedge_map_entries WHERE instrument_id IS NULL"
        )).mappings().all()
        for e in entries:
            iid = _instrument_id_for_contract(bind, e["contract_code"])
            if iid is None:
                continue
            bind.execute(
                text("UPDATE hedge_map_entries SET instrument_id = :iid WHERE id = :id"),
                {"iid": iid, "id": e["id"]},
            )


def _split_default_underlying_profiles(bind) -> None:
    profiles = bind.execute(text(
        "SELECT id, name, valuation_date, status, summary FROM pricing_parameter_profiles "
        "WHERE source_type = 'default_underlying'"
    )).mappings().all()
    for p in profiles:
        summary = p["summary"] if p["summary"] is not None else "{}"
        res = bind.execute(text(
            "INSERT INTO assumption_sets (name, valuation_date, status, summary, created_at, "
            "updated_at) VALUES (:name, :vd, :status, :summary, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        ), {
            "name": p["name"],
            "vd": p["valuation_date"],
            "status": p["status"],
            "summary": summary,
        })
        set_id = res.lastrowid
        rows = bind.execute(text(
            "SELECT symbol, rate, dividend_yield, volatility, source_payload "
            "FROM pricing_parameter_rows WHERE profile_id = :pid"
        ), {"pid": p["id"]}).mappings().all()
        for r in rows:
            iid = _instrument_id_for_symbol(bind, r["symbol"])
            if iid is None:
                continue  # skip + (implicitly) count unresolvable symbols
            bind.execute(text(
                "INSERT INTO assumption_rows "
                "(set_id, instrument_id, symbol, rate, dividend_yield, volatility, "
                " source_payload, created_at) "
                "VALUES (:set_id, :iid, :symbol, :rate, :dy, :vol, :sp, CURRENT_TIMESTAMP)"
            ), {
                "set_id": set_id,
                "iid": iid,
                "symbol": r["symbol"],
                "rate": r["rate"],
                "dy": r["dividend_yield"],
                "vol": r["volatility"],
                "sp": r["source_payload"],
            })
    # These are no longer the canonical home for assumptions, but historical
    # position_valuation_runs / risk_runs reference them by
    # pricing_parameter_profile_id (a REAL FK on the former). DELETING the
    # profiles would orphan those runs / violate FK under foreign_keys=ON. RETAG
    # instead: the rows survive (the step-9 spot-column drop strips their spots
    # like every other profile's), the assumption_sets created above are the new
    # canonical home, and run referential integrity / audit trails are preserved.
    bind.execute(text(
        "UPDATE pricing_parameter_profiles "
        "SET source_type = 'default_underlying_archived' "
        "WHERE source_type = 'default_underlying'"
    ))


def _fold_position_market_inputs(bind) -> None:
    if "position_market_inputs" not in _tables():
        return
    # Group by valuation_date (date part) — one synthetic profile per distinct day.
    dates = bind.execute(text(
        "SELECT DISTINCT date(valuation_date) AS d FROM position_market_inputs "
        "WHERE rate IS NOT NULL OR dividend_yield IS NOT NULL OR volatility IS NOT NULL "
        "ORDER BY d"
    )).mappings().all()
    for row in dates:
        d = row["d"]
        if d is None:
            continue
        res = bind.execute(text(
            "INSERT INTO pricing_parameter_profiles "
            "(name, valuation_date, source_type, status, summary, created_at, updated_at) "
            "VALUES (:name, :vd, 'xlsx', 'completed', :summary, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        ), {
            "name": f"Migrated position market inputs (legacy) {d}",
            "vd": f"{d} 00:00:00",
            "summary": json.dumps({"migrated_from": "position_market_inputs"}),
        })
        profile_id = res.lastrowid
        rows = bind.execute(text(
            "SELECT source_trade_id, symbol, rate, dividend_yield, volatility, source_payload "
            "FROM position_market_inputs "
            "WHERE date(valuation_date) = :d "
            "  AND (rate IS NOT NULL OR dividend_yield IS NOT NULL OR volatility IS NOT NULL)"
        ), {"d": d}).mappings().all()
        for r in rows:
            iid = _instrument_id_for_symbol(bind, r["symbol"])
            bind.execute(text(
                "INSERT INTO pricing_parameter_rows "
                "(profile_id, source_trade_id, symbol, instrument_id, rate, dividend_yield, "
                " volatility, source_payload, created_at, updated_at) "
                "VALUES (:pid, :tid, :symbol, :iid, :rate, :dy, :vol, :sp, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ), {
                "pid": profile_id,
                "tid": r["source_trade_id"],
                "symbol": r["symbol"],
                "iid": iid,
                "rate": r["rate"],
                "dy": r["dividend_yield"],
                "vol": r["volatility"],
                "sp": json.dumps({"migrated_from": "position_market_inputs"}),
            })


def downgrade() -> None:
    # One-way migration. The hedge_instruments→instruments identity merge folds two
    # rows into one and is not reversible; the legacy-profile split and quote
    # backfills likewise lose the original table boundaries. Roll back from a
    # pre-0024 backup instead (house style: 0020/0021 implement downgrades, but
    # this migration cannot honestly reconstruct the prior shape).
    raise NotImplementedError(
        "0024 is a one-way data-merge migration; restore from a pre-0024 backup."
    )
