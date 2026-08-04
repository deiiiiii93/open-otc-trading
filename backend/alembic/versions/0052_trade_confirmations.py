"""Trade confirmation intake — confirmation_batches / confirmation_documents /
extracted_trades.

Adds the three tables that back the trade-confirmation-to-book pipeline: a
batch groups one or more uploaded confirmation documents (PDF/DOCX), each
document is parsed into zero or more extracted trades pending review and
booking. No backfill.

HOUSE RULE: migration-local Core SQL only — no ORM models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_trade_confirmations"
down_revision = "0051_pricing_parameter_row_position_id"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("confirmation_batches"):
        op.create_table(
            "confirmation_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "source", sa.String(length=20), nullable=False, server_default="web"
            ),
            sa.Column(
                "default_portfolio_id",
                sa.Integer(),
                sa.ForeignKey("portfolios.id"),
                nullable=True,
            ),
            sa.Column(
                "task_id", sa.Integer(), sa.ForeignKey("task_runs.id"), nullable=True
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not _has_table("confirmation_documents"):
        op.create_table(
            "confirmation_documents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "batch_id",
                sa.Integer(),
                sa.ForeignKey("confirmation_batches.id"),
                nullable=False,
            ),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("stored_path", sa.String(length=1024), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("byte_len", sa.Integer(), nullable=False),
            sa.Column("mime", sa.String(length=120), nullable=False),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("extract_mode", sa.String(length=20), nullable=True),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("model_provenance", sa.JSON(), nullable=True),
            sa.Column("extraction_debug", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("parsed_at", sa.DateTime(), nullable=True),
        )
        for name, cols in [
            ("ix_confirmation_documents_batch_id", ["batch_id"]),
            ("ix_confirmation_documents_sha256", ["sha256"]),
            ("ix_confirmation_documents_status", ["status"]),
        ]:
            op.create_index(name, "confirmation_documents", cols)

    if not _has_table("extracted_trades"):
        op.create_table(
            "extracted_trades",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "document_id",
                sa.Integer(),
                sa.ForeignKey("confirmation_documents.id"),
                nullable=False,
            ),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("family", sa.String(length=80), nullable=False),
            sa.Column("extracted_terms", sa.JSON(), nullable=False),
            sa.Column("terms", sa.JSON(), nullable=False),
            sa.Column("underlying", sa.String(length=80), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("entry_price", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(length=10), nullable=True),
            sa.Column("counterparty", sa.String(length=255), nullable=True),
            sa.Column("trade_date", sa.String(length=40), nullable=True),
            sa.Column("external_trade_id", sa.String(length=120), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column(
                "validation_status",
                sa.String(length=20),
                nullable=False,
                server_default="invalid",
            ),
            sa.Column("validation_errors", sa.JSON(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="extracted",
            ),
            sa.Column(
                "booked_position_id",
                sa.Integer(),
                sa.ForeignKey("positions.id"),
                nullable=True,
            ),
            sa.Column("reject_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for name, cols in [
            ("ix_extracted_trades_document_id", ["document_id"]),
            ("ix_extracted_trades_status", ["status"]),
        ]:
            op.create_index(name, "extracted_trades", cols)


def downgrade() -> None:
    if _has_table("extracted_trades"):
        op.drop_table("extracted_trades")
    if _has_table("confirmation_documents"):
        op.drop_table("confirmation_documents")
    if _has_table("confirmation_batches"):
        op.drop_table("confirmation_batches")
