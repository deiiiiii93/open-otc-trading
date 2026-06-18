# backend/alembic/versions/0022_hedge_bands.py
"""hedge_bands table"""
from alembic import op
import sqlalchemy as sa

revision = "0022_hedge_bands"
down_revision = "0021_hedging_instrument_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "hedge_bands" in insp.get_table_names():
        return
    op.create_table(
        "hedge_bands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying_id", sa.Integer(),
                  sa.ForeignKey("underlyings.id"), nullable=True),
        sa.Column("delta_cash_band", sa.Float(), nullable=False),
        sa.Column("gamma_cash_band", sa.Float(), nullable=False),
        sa.Column("vega_band", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("updated_by", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("underlying_id", name="uq_hedge_bands_underlying"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "hedge_bands" not in insp.get_table_names():
        return
    op.drop_table("hedge_bands")
