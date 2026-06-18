"""structured position term mirrors

Revision ID: 0016_structured_position_terms
Revises: 0015_long_agent_workflow_schema
Create Date: 2026-05-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_structured_position_terms"
down_revision = "0015_long_agent_workflow_schema"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "option_core_terms" not in tables:
        op.create_table(
            "option_core_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("expiry_date", sa.Date(), nullable=True),
            sa.Column("option_type", sa.String(length=8), nullable=True),
            sa.Column("side", sa.String(length=8), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False),
            sa.Column("notional", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "single_barrier_terms" not in tables:
        op.create_table(
            "single_barrier_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("barrier", sa.Float(), nullable=True),
            sa.Column("barrier_type", sa.String(length=4), nullable=True),
            sa.Column("rebate", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "double_barrier_terms" not in tables:
        op.create_table(
            "double_barrier_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("upper_barrier", sa.Float(), nullable=True),
            sa.Column("lower_barrier", sa.Float(), nullable=True),
            sa.Column("barrier_kind", sa.String(length=4), nullable=True),
            sa.Column("rebate", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "sharkfin_terms" not in tables:
        op.create_table(
            "sharkfin_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("participation_rate", sa.Float(), nullable=True),
            sa.Column("coupon", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "asian_terms" not in tables:
        op.create_table(
            "asian_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("averaging_method", sa.String(length=16), nullable=True),
            sa.Column("averaging_kind", sa.String(length=8), nullable=True),
            sa.Column("n_observations", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "asian_averaging_dates" not in tables:
        op.create_table(
            "asian_averaging_dates",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id", "observation_date"),
        )
    if "snowball_terms" not in tables:
        op.create_table(
            "snowball_terms",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("initial_price", sa.Float(), nullable=True),
            sa.Column("ki_barrier", sa.Float(), nullable=True),
            sa.Column("coupon", sa.Float(), nullable=True),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("knocked_in", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("ki_observation", sa.String(length=20), nullable=True),
            sa.Column("payoff_kind", sa.String(length=40), nullable=True),
            sa.Column("legacy_kwargs", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "snowball_ko_schedule" not in tables:
        op.create_table(
            "snowball_ko_schedule",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=False),
            sa.Column("ko_level", sa.Float(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "position_id",
                "observation_date",
                name="uq_snowball_ko_schedule_position_date",
            ),
        )
        op.create_index(
            "ix_snowball_ko_schedule_position_id",
            "snowball_ko_schedule",
            ["position_id"],
        )
    if "position_barrier_state" not in tables:
        op.create_table(
            "position_barrier_state",
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("nearest_barrier_kind", sa.String(length=8), nullable=True),
            sa.Column("nearest_barrier_level", sa.Float(), nullable=True),
            sa.Column("nearest_barrier_date", sa.Date(), nullable=True),
            sa.Column("days_to_nearest", sa.Integer(), nullable=True),
            sa.Column("last_computed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("position_id"),
        )
    if "positions" in _tables():
        _backfill_position_terms()


def downgrade() -> None:
    for table_name in (
        "snowball_ko_schedule",
        "position_barrier_state",
        "snowball_terms",
        "asian_averaging_dates",
        "asian_terms",
        "sharkfin_terms",
        "double_barrier_terms",
        "single_barrier_terms",
        "option_core_terms",
    ):
        if table_name in _tables():
            op.drop_table(table_name)


def _backfill_position_terms() -> None:
    from sqlalchemy.orm import Session

    from app.services.domains.position_terms import backfill_position_term_rows

    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        backfill_position_term_rows(session)
        session.flush()
    finally:
        session.close()
