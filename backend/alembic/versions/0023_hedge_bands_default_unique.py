"""partial unique index capping the hedge_bands portfolio-wide defaults row

A plain ``UNIQUE(underlying_id)`` (added in 0022) treats NULLs as distinct, so it
does not prevent two defaults rows (underlying_id IS NULL) from coexisting. This
adds a partial unique index over a constant that all defaults rows share, capping
them at one. Per-underlying rows remain covered by the 0022 constraint.

Expression-based indexes are not reflectable via ``inspect().get_indexes()``, so
idempotency relies on the native ``IF NOT EXISTS`` clause rather than a guard.
"""
from alembic import op

revision = "0023_hedge_bands_default_unique"
down_revision = "0022_hedge_bands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_hedge_bands_default "
        "ON hedge_bands (1) WHERE underlying_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_hedge_bands_default")
