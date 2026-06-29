"""gateway tables — IM message gateway bindings, codes, cards, dedup, lock

Revision ID: 0037_gateway_tables
Revises: 0036_agent_thread_goal_run
Create Date: 2026-06-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0037_gateway_tables"
down_revision = "0036_agent_thread_goal_run"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    return any(
        ix["name"] == index_name
        for ix in inspect(op.get_bind()).get_indexes(table)
    )


def upgrade() -> None:
    # --- gateway_binding --------------------------------------------------
    if not _has_table("gateway_binding"):
        op.create_table(
            "gateway_binding",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("provider", sa.String, nullable=False),
            sa.Column("external_account_id", sa.String, nullable=False),
            sa.Column("workspace_id", sa.String, nullable=False, server_default=""),
            sa.Column("desk_user", sa.String, nullable=False),
            sa.Column("persona", sa.String, nullable=False),
            sa.Column("status", sa.String, nullable=False, server_default="active"),
            sa.Column("bound_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime, nullable=True),
            sa.Column("revoked_at", sa.DateTime, nullable=True),
            sa.Column(
                "supersedes_binding_id",
                sa.Integer,
                sa.ForeignKey("gateway_binding.id"),
                nullable=True,
            ),
        )
    if not _has_index("gateway_binding", "uq_gateway_binding_active"):
        op.create_index(
            "uq_gateway_binding_active",
            "gateway_binding",
            ["provider", "external_account_id", "workspace_id"],
            unique=True,
            sqlite_where=sa.text("status='active'"),
            postgresql_where=sa.text("status='active'"),
        )

    # --- gateway_linking_code ---------------------------------------------
    if not _has_table("gateway_linking_code"):
        op.create_table(
            "gateway_linking_code",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("code", sa.String, nullable=False, unique=True),
            sa.Column("desk_user", sa.String, nullable=False),
            sa.Column("persona", sa.String, nullable=False),
            sa.Column("expires_at", sa.DateTime, nullable=False),
            sa.Column(
                "redeemed_by_binding_id",
                sa.Integer,
                sa.ForeignKey("gateway_binding.id"),
                nullable=True,
            ),
            sa.Column("issued_by", sa.String, nullable=False),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    # --- gateway_thread_map -----------------------------------------------
    if not _has_table("gateway_thread_map"):
        op.create_table(
            "gateway_thread_map",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "binding_id",
                sa.Integer,
                sa.ForeignKey("gateway_binding.id"),
                nullable=False,
            ),
            sa.Column("chat_id", sa.String, nullable=False),
            sa.Column("thread_id", sa.Integer, nullable=False),
            sa.UniqueConstraint(
                "binding_id", "chat_id",
                name="uq_gateway_thread_map_binding_chat",
            ),
        )

    # --- gateway_inbound_seen ---------------------------------------------
    if not _has_table("gateway_inbound_seen"):
        op.create_table(
            "gateway_inbound_seen",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("connector", sa.String, nullable=False),
            sa.Column("workspace_id", sa.String, nullable=False, server_default=""),
            sa.Column("provider_event_id", sa.String, nullable=False),
            sa.Column("state", sa.String, nullable=False, server_default="processing"),
            sa.Column("owner_token", sa.String, nullable=True),
            sa.Column("claimed_at", sa.DateTime, nullable=True),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
            sa.Column("seen_at", sa.DateTime, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "connector", "workspace_id", "provider_event_id",
                name="uq_gateway_inbound_seen",
            ),
        )

    # --- gateway_card_action ----------------------------------------------
    if not _has_table("gateway_card_action"):
        op.create_table(
            "gateway_card_action",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("token", sa.String, nullable=False, unique=True),
            sa.Column("out_connector", sa.String, nullable=False),
            sa.Column("out_workspace_id", sa.String, nullable=False, server_default=""),
            sa.Column("out_chat_id", sa.String, nullable=False),
            sa.Column("out_message_id", sa.String, nullable=False),
            sa.Column(
                "binding_id",
                sa.Integer,
                sa.ForeignKey("gateway_binding.id"),
                nullable=False,
            ),
            sa.Column("thread_id", sa.Integer, nullable=False),
            sa.Column("message_id", sa.Integer, nullable=False),
            sa.Column("action_id", sa.String, nullable=False),
            sa.Column("decision", sa.String, nullable=False),
            sa.Column("expires_at", sa.DateTime, nullable=False),
            sa.Column("status", sa.String, nullable=False, server_default="pending"),
            sa.Column("resolved_by_binding_id", sa.Integer, nullable=True),
            sa.UniqueConstraint(
                "thread_id", "message_id", "action_id", "decision",
                name="uq_gateway_card_action_action",
            ),
        )

    # --- gateway_worker_lock ----------------------------------------------
    if not _has_table("gateway_worker_lock"):
        op.create_table(
            "gateway_worker_lock",
            sa.Column("id", sa.Integer, primary_key=True, server_default="1"),
            sa.Column("owner_token", sa.String, nullable=False),
            sa.Column("acquired_at", sa.DateTime, nullable=False),
            sa.Column("lease_expires_at", sa.DateTime, nullable=False),
        )


def downgrade() -> None:
    for table in (
        "gateway_card_action",
        "gateway_inbound_seen",
        "gateway_thread_map",
        "gateway_linking_code",
        "gateway_worker_lock",
        "gateway_binding",
    ):
        if _has_table(table):
            op.drop_table(table)
