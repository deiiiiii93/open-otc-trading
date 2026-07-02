"""hedge tag — server-derived "hedge" classification on Instrument.tags

Revision ID: 0044_hedge_tag
Revises: 0043_agent_action_audits
Create Date: 2026-07-02

Recomputes (not appends) "hedge" on every instrument's `tags` column:
tagged iff it's referenced by a `hedge_map_entries` row with
reconcile_status='active' (directly via instrument_id, or via legacy
(exchange, contract_code) matching for rows never backfilled with a durable
link), OR kind='stock' AND status='active' (the pre-existing stock
self-hedge default). Must be a full recomputation, not an append-only
backfill: the tags PUT endpoint accepted arbitrary tags before this feature
shipped its "hedge"-stripping, so a pre-existing hand-typed "hedge" tag that
doesn't satisfy either truth condition must be scrubbed, not left in place.

HOUSE RULE: migration-local Core SQL / sa.Table on a fresh MetaData only —
never import app models/services (they drift to the future schema).
"""
from __future__ import annotations

import json

from alembic import op
from sqlalchemy import inspect, text


revision = "0044_hedge_tag"
down_revision = "0043_agent_action_audits"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Only `instruments` is required. The active-stock self-hedge rule is
    # derivable from `instruments` alone — gating the whole function on
    # `hedge_map_entries` also existing would silently skip tagging active
    # stocks on a DB that has `instruments.tags` but no hedge-map table yet
    # (exactly the schema-drift scenario this migration/repair path exists
    # to tolerate).
    if "instruments" not in _tables():
        return
    bind = op.get_bind()
    hedge_ids: set[int] = set()

    if "hedge_map_entries" in _tables():
        # Both queries also require the Instrument row itself to be
        # status='active' — mirrors sync_hedge_tag's truth condition (Task
        # 1), which must check this because reconcile_status can be stale
        # relative to the instrument's current status. A one-time backfill
        # is exactly the place that stale data is most likely to already
        # exist, so this isn't optional here either.
        for (instrument_id,) in bind.execute(
            text(
                "SELECT DISTINCT h.instrument_id FROM hedge_map_entries h "
                "JOIN instruments i ON i.id = h.instrument_id "
                "WHERE h.reconcile_status = 'active' AND i.status = 'active'"
            )
        ).fetchall():
            hedge_ids.add(instrument_id)

        for (instrument_id,) in bind.execute(
            text(
                "SELECT DISTINCT i.id FROM instruments i "
                "JOIN hedge_map_entries h ON h.instrument_id IS NULL "
                "AND h.exchange = i.exchange AND h.contract_code = i.contract_code "
                "WHERE h.reconcile_status = 'active' AND i.status = 'active' "
                "AND i.exchange IS NOT NULL AND i.contract_code IS NOT NULL"
            )
        ).fetchall():
            hedge_ids.add(instrument_id)

    for (instrument_id,) in bind.execute(
        text("SELECT id FROM instruments WHERE kind = 'stock' AND status = 'active'")
    ).fetchall():
        hedge_ids.add(instrument_id)

    # Full recompute, not append-only: strip any pre-existing "hedge" tag
    # from every row first (it may have been hand-typed through the tags
    # PUT endpoint before this feature made "hedge" server-derived), then
    # add it back only where ground truth says so.
    for (instrument_id, tags_raw) in bind.execute(
        text("SELECT id, tags FROM instruments WHERE tags LIKE '%\"hedge\"%'")
    ).fetchall():
        current = json.loads(tags_raw) if tags_raw else []
        if "hedge" in current and instrument_id not in hedge_ids:
            current = [t for t in current if t != "hedge"]
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )

    for instrument_id in sorted(hedge_ids):
        row = bind.execute(
            text("SELECT tags FROM instruments WHERE id = :id"),
            {"id": instrument_id},
        ).fetchone()
        if row is None:
            continue
        current = json.loads(row[0]) if row[0] else []
        if "hedge" not in current:
            current.append("hedge")
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )


def downgrade() -> None:
    if "instruments" not in _tables():
        return
    bind = op.get_bind()
    for (instrument_id, tags_raw) in bind.execute(
        text("SELECT id, tags FROM instruments WHERE tags LIKE '%\"hedge\"%'")
    ).fetchall():
        current = json.loads(tags_raw) if tags_raw else []
        if "hedge" in current:
            current = [t for t in current if t != "hedge"]
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )
