"""Add term-structure curve columns to instruments.

Adds Instrument.rate_curve / dividend_yield_curve / volatility_curve — each a
nullable JSON list[{"tenor": <label>, "value": <float>}]. None means "no curve;
use the flat scalar". No backfill.

HOUSE RULE: migration-local Core SQL only — no ORM models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_instrument_term_structure_curves"
down_revision = "0049_hedge_booking_claim"
branch_labels = None
depends_on = None

_COLUMNS = ("rate_curve", "dividend_yield_curve", "volatility_curve")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("instruments", sa.Column(column, sa.JSON(), nullable=True))


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("instruments", column)
