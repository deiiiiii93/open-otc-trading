"""limit_incident_reviews — System One's display-only read of incident text

Revision ID: 0064_limit_incident_reviews
Revises: 0063_extracted_trade_family_check

One row per (event_id, kind) (spec 2026-09-21-limit-incident-review D3). event_id
is NOT NULL, so the NULL-distinct UNIQUE trap 0061 documents does not arise.

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already
has this table and its indexes. HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0064_limit_incident_reviews"
down_revision = "0063_extracted_trade_family_check"
branch_labels = None
depends_on = None

_TABLE = "limit_incident_reviews"
_IX_INCIDENT = "ix_limit_incident_reviews_incident_id"
_IX_CREATED = "ix_limit_incident_reviews_created_at"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("incident_id", sa.Integer(),
                      sa.ForeignKey("limit_incidents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_id", sa.Integer(),
                      sa.ForeignKey("limit_incident_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("unscored_reason", sa.String(40), nullable=True),
            sa.Column("rationale_grade", sa.Float(), nullable=True),
            sa.Column("rationale_confidence", sa.Float(), nullable=True),
            sa.Column("authority_only_p", sa.Float(), nullable=True),
            sa.Column("thread_state", sa.String(32), nullable=True),
            sa.Column("thread_state_p", sa.Float(), nullable=True),
            sa.Column("claims_json", sa.JSON(), nullable=False),
            sa.Column("answers_json", sa.JSON(), nullable=False),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("attempted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("event_id", "kind", name="uq_limit_incident_reviews_event_kind"),
        )
    existing = _indexes(_TABLE)
    if _IX_INCIDENT not in existing:
        op.create_index(_IX_INCIDENT, _TABLE, ["incident_id"])
    if _IX_CREATED not in existing:
        op.create_index(_IX_CREATED, _TABLE, ["created_at"])


def downgrade() -> None:
    if _TABLE in _tables():
        op.drop_table(_TABLE)
