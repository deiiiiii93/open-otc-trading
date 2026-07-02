"""instrument tags — real, storable "underlying" classification

Revision ID: 0042_instrument_tags
Revises: 0041_morning_breach_assemble_prompt
Create Date: 2026-07-02

Adds Instrument.tags (JSON list[str], mirrors Portfolio.tags). Backfills
"underlying" onto:

  * every instrument referenced by an OPEN OTC position (mirrors
    services/underlyings.open_position_underlying_symbols' KNOCKED_OUT_STATUSES
    exclusion: {"Knocked Out", "敲出"} read from source_payload.trade_state), and
  * every ACTIVE root instrument that is NOT a dated derivative contract
    instance (kind != 'listed_option' AND expiry IS NULL AND contract_code IS
    NULL) — this second clause is required, not optional: it's the exact
    "curated but not yet traded" underlying (e.g. 000905.SH, DRAFT-turned-
    active before any position references it) this feature exists to stop
    from disappearing off the Booking/TrySolve pickers on migration day.

An open-position underlying with NO matching instruments row (Position.underlying
is free-text; legacy/bulk-imported positions may predate ensure_underlying())
gets a minimal stub instrument row created, not silently skipped — otherwise a
currently-open position's underlying becomes permanently unbookable once the
picker/tool gate ships, since there'd be no row left to register.

HOUSE RULE: migration-local Core SQL / sa.Table on a fresh MetaData only —
never import app models/services (they drift to the future schema).
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0042_instrument_tags"
down_revision = "0041_morning_breach_assemble_prompt"
branch_labels = None
depends_on = None


_KNOCKED_OUT_STATES = {"Knocked Out", "敲出"}


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "tags" not in _columns("instruments"):
        op.add_column(
            "instruments",
            sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        )

    bind = op.get_bind()
    tagged: set[str] = set()

    if "positions" in _tables():
        rows = bind.execute(
            text(
                "SELECT underlying, status, source_payload FROM positions "
                "WHERE underlying IS NOT NULL AND status = 'open' "
                "AND position_kind = 'otc'"
            )
        ).fetchall()
        for underlying, status, payload_raw in rows:
            if status == "closed":
                continue
            payload = {}
            if payload_raw:
                try:
                    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                except (TypeError, ValueError):
                    payload = {}
            state = payload.get("trade_state") if isinstance(payload, dict) else None
            if state in _KNOCKED_OUT_STATES:
                continue
            symbol = (underlying or "").strip()
            if symbol:
                tagged.add(symbol)

    active_root_rows = bind.execute(
        text(
            "SELECT symbol FROM instruments WHERE status = 'active' "
            "AND kind != 'listed_option' AND expiry IS NULL AND contract_code IS NULL"
        )
    ).fetchall()
    for (symbol,) in active_root_rows:
        if symbol:
            tagged.add(symbol)

    for symbol in sorted(tagged):
        row = bind.execute(
            text("SELECT id, tags FROM instruments WHERE symbol = :symbol"),
            {"symbol": symbol},
        ).fetchone()
        if row is None:
            # An open OTC position can reference a symbol with no matching
            # instruments row — Position.underlying is a free-text column,
            # and legacy/bulk-imported positions may never have gone through
            # ensure_underlying(). Skipping here would leave a currently-open
            # position's underlying permanently unbookable once the picker/
            # tool gate ships (book_position would reject a symbol nothing
            # can register, since it's not even a row to tag). Create a
            # minimal stub row instead of a full ensure_underlying()-style
            # inference (the house rule bans importing app services into a
            # migration) — kind/currency mirror the Instrument model's own
            # column defaults (models.py:535-541: kind='index', currency='CNY').
            bind.execute(
                text(
                    "INSERT INTO instruments "
                    "(symbol, kind, currency, status, source, tags, created_at, updated_at) "
                    "VALUES (:symbol, 'index', 'CNY', 'active', 'migration_backfill', :tags, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"symbol": symbol, "tags": json.dumps(["underlying"])},
            )
            continue
        instrument_id, tags_raw = row
        current = json.loads(tags_raw) if tags_raw else []
        if "underlying" not in current:
            current.append("underlying")
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )


def downgrade() -> None:
    if "tags" in _columns("instruments"):
        with op.batch_alter_table("instruments") as batch:
            batch.drop_column("tags")
