"""evolve memory_entries into a typed facts table + backfill

Revision ID: 0038_memory_entries_evolve
Revises: 0037_gateway_tables
Create Date: 2026-06-29
"""
from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0038_memory_entries_evolve"
down_revision = "0037_gateway_tables"
branch_labels = None
depends_on = None

_VALID_TYPES = {"user", "book", "domain", "correction"}
_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", (s or "").strip()).casefold()
    return _WS.sub(" ", s).strip()


def _has_column(table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspect(op.get_bind()).get_columns(table))


def _has_index(table: str, name: str) -> bool:
    return any(
        index["name"] == name
        for index in inspect(op.get_bind()).get_indexes(table)
    )


def upgrade() -> None:
    bind = op.get_bind()
    has_legacy_namespace = _has_column("memory_entries", "namespace")
    for col, type_, default in [
        ("scope_type", sa.String(), None),
        ("scope_id", sa.String(), None),
        ("normalized_content", sa.String(), None),
        ("confidence", sa.Float(), "1.0"),
        ("status", sa.String(), "'active'"),
        ("category", sa.String(), None),
        ("source_error", sa.Boolean(), "0"),
        ("created_by", sa.String(), "'migration'"),
        ("pinned", sa.Boolean(), "0"),
        ("updated_at", sa.DateTime(), None),
    ]:
        if not _has_column("memory_entries", col):
            op.add_column("memory_entries", sa.Column(col, type_, nullable=True,
                          server_default=default))

    # 0001_initial intentionally creates from current ORM metadata. On a fresh
    # database that means the typed post-0038 table and its indexes already
    # exist by the time this historical revision runs. There is no legacy
    # namespace payload to backfill or column to rebuild in that path.
    if not has_legacy_namespace:
        if not _has_index("memory_entries", "ix_memory_scope_status"):
            op.create_index(
                "ix_memory_scope_status",
                "memory_entries",
                ["scope_type", "scope_id", "status"],
            )
        if not _has_index("memory_entries", "ux_memory_dedup"):
            op.create_index(
                "ux_memory_dedup",
                "memory_entries",
                ["scope_type", "scope_id", "normalized_content"],
                unique=True,
                sqlite_where=sa.text("status != 'archived'"),
            )
        return

    rows = list(bind.execute(sa.text(
        "SELECT id, namespace, content FROM memory_entries"
    )))
    for rid, namespace, content in rows:
        stype, _, sid = (namespace or "").partition(":")
        if stype not in _VALID_TYPES:
            stype, sid = "user", "desk"
        if not sid:
            sid = {"user": "desk", "correction": "desk", "domain": "global"}.get(stype, "desk")
        status = "proposed" if stype == "domain" else "active"
        source_error = stype == "correction"
        norm = _normalize(content or "")
        if not norm:
            status = "archived"
        bind.execute(sa.text(
            "UPDATE memory_entries SET scope_type=:t, scope_id=:s, status=:st, "
            "normalized_content=:n, confidence=1.0, source_error=:e, "
            "created_by='migration', pinned=0, updated_at=created_at WHERE id=:id"
        ), {"t": stype, "s": sid, "st": status, "n": norm,
            "e": 1 if source_error else 0, "id": rid})

    dup_rows = list(bind.execute(sa.text(
        "SELECT id, scope_type, scope_id, normalized_content, confidence, created_at "
        "FROM memory_entries WHERE status != 'archived'"
    )))
    best: dict[tuple, tuple] = {}
    for rid, t, s, n, conf, created in dup_rows:
        key = (t, s, n)
        cand = (conf or 0.0, str(created or ""))
        cur = best.get(key)
        if cur is None or cand > cur[0]:
            if cur is not None:
                bind.execute(sa.text("UPDATE memory_entries SET status='archived' WHERE id=:id"),
                             {"id": cur[1]})
            best[key] = (cand, rid)
        else:
            bind.execute(sa.text("UPDATE memory_entries SET status='archived' WHERE id=:id"),
                         {"id": rid})

    # Drop the old namespace index before batch rebuild to avoid "no such column" on flush
    bind_insp = inspect(op.get_bind())
    existing_idx = {ix["name"] for ix in bind_insp.get_indexes("memory_entries")}
    if "ix_memory_entries_namespace" in existing_idx:
        op.drop_index("ix_memory_entries_namespace", table_name="memory_entries")

    with op.batch_alter_table("memory_entries") as batch:
        if _has_column("memory_entries", "namespace"):
            batch.drop_column("namespace")
        # Tighten NOT NULL to match the ORM (the backfill above populated every
        # row, so this rebuild cannot fail on existing data). `category` stays
        # nullable. This is a single SQLite table rebuild.
        for col, type_ in [
            ("scope_type", sa.String()), ("scope_id", sa.String()),
            ("normalized_content", sa.String()), ("confidence", sa.Float()),
            ("status", sa.String()), ("source_error", sa.Boolean()),
            ("created_by", sa.String()), ("pinned", sa.Boolean()),
            ("updated_at", sa.DateTime()),
        ]:
            batch.alter_column(col, existing_type=type_, nullable=False)
    op.create_index("ix_memory_scope_status", "memory_entries",
                    ["scope_type", "scope_id", "status"])
    op.create_index("ux_memory_dedup", "memory_entries",
                    ["scope_type", "scope_id", "normalized_content"],
                    unique=True, sqlite_where=sa.text("status != 'archived'"))


def downgrade() -> None:
    op.drop_index("ux_memory_dedup", table_name="memory_entries")
    op.drop_index("ix_memory_scope_status", table_name="memory_entries")
    op.add_column("memory_entries", sa.Column("namespace", sa.String(), nullable=True))
    for col in ("scope_type", "scope_id", "normalized_content", "confidence",
                "status", "category", "source_error", "created_by", "pinned", "updated_at"):
        with op.batch_alter_table("memory_entries") as batch:
            batch.drop_column(col)
