"""atomic hedge booking claims

Revision ID: 0049_hedge_booking_claim
Revises: 0048_limit_monitor_active_unique
Create Date: 2026-07-20

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0049_hedge_booking_claim"
down_revision = "0048_limit_monitor_active_unique"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "hedge_booking_claims" in _tables():
        return
    op.create_table(
        "hedge_booking_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "risk_run_id",
            sa.Integer(),
            sa.ForeignKey("risk_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("underlying", sa.String(length=80), nullable=False),
        sa.Column(
            "source_artifact_id",
            sa.Integer(),
            sa.ForeignKey("session_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=40), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "source_artifact_id",
            name="uq_hedge_booking_claim_source_artifact",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "risk_run_id",
            "underlying",
            name="uq_hedge_booking_claim_run_underlying",
        ),
    )
    op.create_index(
        "ix_hedge_booking_claims_portfolio_id",
        "hedge_booking_claims",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_hedge_booking_claims_risk_run_id",
        "hedge_booking_claims",
        ["risk_run_id"],
    )
    op.create_index(
        "ix_hedge_booking_claims_source_artifact_id",
        "hedge_booking_claims",
        ["source_artifact_id"],
    )
    op.create_index(
        "ix_hedge_booking_claims_workflow_id",
        "hedge_booking_claims",
        ["workflow_id"],
    )


def downgrade() -> None:
    if "hedge_booking_claims" in _tables():
        op.drop_table("hedge_booking_claims")
