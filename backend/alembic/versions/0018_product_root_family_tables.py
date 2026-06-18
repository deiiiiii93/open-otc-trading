"""product root family tables

Revision ID: 0018_product_root_family_tables
Revises: 0017_position_lifecycle_event_cancellation
Create Date: 2026-05-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0018_product_root_family_tables"
down_revision = "0017_position_lifecycle_event_cancellation"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _foreign_keys(table_name: str) -> list[dict[str, object]]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return []
    return list(inspector.get_foreign_keys(table_name))


def _has_positions_product_fk() -> bool:
    return any(
        foreign_key.get("referred_table") == "products"
        and foreign_key.get("constrained_columns") == ["product_id"]
        for foreign_key in _foreign_keys("positions")
    )


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def upgrade() -> None:
    tables = _tables()
    if "products" not in tables:
        op.create_table(
            "products",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "asset_class",
                sa.String(length=40),
                server_default="equity",
                nullable=False,
            ),
            sa.Column("product_family", sa.String(length=40), nullable=False),
            sa.Column("quantark_class", sa.String(length=120), nullable=True),
            sa.Column("display_name", sa.String(length=160), nullable=True),
            sa.Column("underlying", sa.String(length=80), nullable=False),
            sa.Column(
                "currency", sa.String(length=8), server_default="USD", nullable=False
            ),
            sa.Column("term_hash", sa.String(length=80), nullable=False),
            sa.Column("raw_terms", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing(
        "ix_products_asset_family", "products", ["asset_class", "product_family"]
    )
    _create_index_if_missing(
        "ix_products_product_family", "products", ["product_family"]
    )
    _create_index_if_missing("ix_products_underlying", "products", ["underlying"])
    _create_index_if_missing(
        "ix_products_quantark_class", "products", ["quantark_class"]
    )
    _create_index_if_missing("ix_products_term_hash", "products", ["term_hash"])

    tables = _tables()
    if "equity_option_products" not in tables:
        op.create_table(
            "equity_option_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("option_type", sa.String(length=8), nullable=True),
            sa.Column("exercise_type", sa.String(length=16), nullable=True),
            sa.Column("maturity", sa.Float(), nullable=True),
            sa.Column("exercise_date", sa.Date(), nullable=True),
            sa.Column("settlement_date", sa.Date(), nullable=True),
            sa.Column("maturity_date", sa.Date(), nullable=True),
            sa.Column("tenor", sa.Float(), nullable=True),
            sa.Column("tenor_end", sa.String(length=40), nullable=True),
            sa.Column("annualization_day_count", sa.String(length=40), nullable=True),
            sa.Column("initial_price", sa.Float(), nullable=True),
            sa.Column(
                "contract_multiplier",
                sa.Float(),
                server_default="1.0",
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_autocallable_products" not in tables:
        op.create_table(
            "equity_autocallable_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("autocallable_kind", sa.String(length=40), nullable=False),
            sa.Column("is_reverse", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("initial_price", sa.Float(), nullable=False),
            sa.Column("strike", sa.Float(), nullable=False),
            sa.Column(
                "contract_multiplier",
                sa.Float(),
                server_default="1.0",
                nullable=False,
            ),
            sa.Column("ko_observation_type", sa.String(length=24), nullable=True),
            sa.Column("ki_observation_type", sa.String(length=24), nullable=True),
            sa.Column(
                "ki_continuous", sa.Boolean(), server_default="0", nullable=False
            ),
            sa.Column(
                "disable_ko_after_ki", sa.Boolean(), server_default="0", nullable=False
            ),
            sa.Column("payoff_rebate_rate", sa.Float(), nullable=True),
            sa.Column(
                "payoff_call_rebate_enabled",
                sa.Boolean(),
                server_default="0",
                nullable=False,
            ),
            sa.Column("payoff_call_strike", sa.Float(), nullable=True),
            sa.Column("payoff_call_participation_rate", sa.Float(), nullable=True),
            sa.Column(
                "payoff_include_principal",
                sa.Boolean(),
                server_default="1",
                nullable=False,
            ),
            sa.Column("payoff_participation_rate", sa.Float(), nullable=True),
            sa.Column("payoff_protection_type", sa.String(length=24), nullable=True),
            sa.Column("payoff_protection_rate", sa.Float(), nullable=True),
            sa.Column("accrual_coupon_pay_type", sa.String(length=24), nullable=True),
            sa.Column(
                "accrual_is_annualized",
                sa.Boolean(),
                server_default="1",
                nullable=False,
            ),
            sa.Column("accrual_is_annualized_ko", sa.Boolean(), nullable=True),
            sa.Column("accrual_is_annualized_ki", sa.Boolean(), nullable=True),
            sa.Column("accrual_is_annualized_rebate", sa.Boolean(), nullable=True),
            sa.Column("reset_rate", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_autocallable_observations" not in tables:
        op.create_table(
            "equity_autocallable_observations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("observation_role", sa.String(length=24), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=True),
            sa.Column("observation_time", sa.Float(), nullable=True),
            sa.Column("barrier_level", sa.Float(), nullable=True),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("accrual_factor", sa.Float(), nullable=True),
            sa.Column("aggregation", sa.String(length=24), nullable=True),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint(
                "product_id",
                "observation_role",
                "sequence",
                name="uq_equity_autocallable_observations_role_sequence",
            ),
        )
    if "equity_phoenix_coupon_products" not in tables:
        op.create_table(
            "equity_phoenix_coupon_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("coupon_barrier", sa.Float(), nullable=False),
            sa.Column("coupon_rate", sa.Float(), nullable=False),
            sa.Column("coupon_pay_type", sa.String(length=24), nullable=True),
            sa.Column("day_count_convention", sa.String(length=40), nullable=True),
            sa.Column(
                "memory_coupon", sa.Boolean(), server_default="1", nullable=False
            ),
            sa.Column("fixed_coupon_year_fraction", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_barrier_products" not in tables:
        op.create_table(
            "equity_barrier_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("barrier_kind", sa.String(length=32), nullable=False),
            sa.Column("barrier", sa.Float(), nullable=True),
            sa.Column("barrier_type", sa.String(length=32), nullable=True),
            sa.Column("upper_barrier", sa.Float(), nullable=True),
            sa.Column("lower_barrier", sa.Float(), nullable=True),
            sa.Column("rebate", sa.Float(), nullable=True),
            sa.Column("monitoring_type", sa.String(length=24), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_touch_products" not in tables:
        op.create_table(
            "equity_touch_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("touch_kind", sa.String(length=32), nullable=False),
            sa.Column("barrier", sa.Float(), nullable=True),
            sa.Column("upper_barrier", sa.Float(), nullable=True),
            sa.Column("lower_barrier", sa.Float(), nullable=True),
            sa.Column("touch_type", sa.String(length=32), nullable=True),
            sa.Column("payout", sa.Float(), nullable=True),
            sa.Column("rebate", sa.Float(), nullable=True),
            sa.Column("monitoring_type", sa.String(length=24), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_asian_products" not in tables:
        op.create_table(
            "equity_asian_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("averaging_method", sa.String(length=24), nullable=True),
            sa.Column("averaging_kind", sa.String(length=24), nullable=True),
            sa.Column("n_observations", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_asian_observations" not in tables:
        op.create_table(
            "equity_asian_observations",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=True),
            sa.Column("observation_time", sa.Float(), nullable=True),
            sa.Column("observed_price", sa.Float(), nullable=True),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id", "sequence"),
        )
    if "equity_range_accrual_products" not in tables:
        op.create_table(
            "equity_range_accrual_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("lower_barrier", sa.Float(), nullable=False),
            sa.Column("upper_barrier", sa.Float(), nullable=False),
            sa.Column("accrual_rate", sa.Float(), nullable=False),
            sa.Column("observation_type", sa.String(length=24), nullable=True),
            sa.Column("day_count_convention", sa.String(length=40), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_range_accrual_observations" not in tables:
        op.create_table(
            "equity_range_accrual_observations",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=True),
            sa.Column("observation_time", sa.Float(), nullable=True),
            sa.Column("lower_barrier", sa.Float(), nullable=True),
            sa.Column("upper_barrier", sa.Float(), nullable=True),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id", "sequence"),
        )
    if "equity_sharkfin_products" not in tables:
        op.create_table(
            "equity_sharkfin_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("sharkfin_kind", sa.String(length=16), nullable=False),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("barrier", sa.Float(), nullable=True),
            sa.Column("upper_barrier", sa.Float(), nullable=True),
            sa.Column("lower_barrier", sa.Float(), nullable=True),
            sa.Column("option_type", sa.String(length=8), nullable=True),
            sa.Column("participation_rate", sa.Float(), nullable=True),
            sa.Column("coupon", sa.Float(), nullable=True),
            sa.Column("rebate", sa.Float(), nullable=True),
            sa.Column("observation_type", sa.String(length=24), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_spot_products" not in tables:
        op.create_table(
            "equity_spot_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("deltaone_type", sa.String(length=16), nullable=False),
            sa.Column("instrument_code", sa.String(length=80), nullable=False),
            sa.Column("exchange", sa.String(length=40), nullable=True),
            sa.Column(
                "contract_multiplier",
                sa.Float(),
                server_default="1.0",
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_futures_products" not in tables:
        op.create_table(
            "equity_futures_products",
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("contract_code", sa.String(length=80), nullable=False),
            sa.Column("multiplier", sa.Float(), server_default="1.0", nullable=False),
            sa.Column("maturity", sa.Float(), nullable=True),
            sa.Column("maturity_date", sa.Date(), nullable=True),
            sa.Column("basis", sa.Float(), server_default="0.0", nullable=False),
            sa.Column(
                "basis_decay_rate", sa.Float(), server_default="1.0", nullable=False
            ),
            sa.Column("market_price", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(
                ["product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("product_id"),
        )
    if "equity_product_components" not in tables:
        op.create_table(
            "equity_product_components",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("parent_product_id", sa.Integer(), nullable=False),
            sa.Column("component_product_id", sa.Integer(), nullable=False),
            sa.Column("component_role", sa.String(length=40), nullable=False),
            sa.Column("quantity", sa.Float(), server_default="1.0", nullable=False),
            sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(
                ["parent_product_id"], ["products.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["component_product_id"], ["products.id"]),
            sa.UniqueConstraint(
                "parent_product_id",
                "sequence",
                name="uq_equity_product_components_parent_sequence",
            ),
        )

    if "positions" in _tables() and "product_id" not in _columns("positions"):
        with op.batch_alter_table("positions") as batch:
            batch.add_column(sa.Column("product_id", sa.Integer(), nullable=True))
    if "positions" in _tables():
        _create_index_if_missing("ix_positions_product_id", "positions", ["product_id"])
        _backfill_position_products()
        if not _has_positions_product_fk():
            with op.batch_alter_table("positions") as batch:
                batch.create_foreign_key(
                    "fk_positions_product_id_products",
                    "products",
                    ["product_id"],
                    ["id"],
                )


def downgrade() -> None:
    if "positions" in _tables() and "product_id" in _columns("positions"):
        if "ix_positions_product_id" in _indexes("positions"):
            op.drop_index("ix_positions_product_id", table_name="positions")
        with op.batch_alter_table("positions") as batch:
            batch.drop_column("product_id")

    for table_name in (
        "equity_product_components",
        "equity_range_accrual_observations",
        "equity_asian_observations",
        "equity_autocallable_observations",
        "equity_futures_products",
        "equity_spot_products",
        "equity_sharkfin_products",
        "equity_range_accrual_products",
        "equity_asian_products",
        "equity_touch_products",
        "equity_barrier_products",
        "equity_phoenix_coupon_products",
        "equity_autocallable_products",
        "equity_option_products",
    ):
        if table_name in _tables():
            op.drop_table(table_name)

    if "products" in _tables():
        for index_name in (
            "ix_products_term_hash",
            "ix_products_quantark_class",
            "ix_products_underlying",
            "ix_products_product_family",
            "ix_products_asset_family",
        ):
            if index_name in _indexes("products"):
                op.drop_index(index_name, table_name="products")
        op.drop_table("products")


def _backfill_position_products() -> None:
    required_columns = {
        "id",
        "portfolio_id",
        "product_id",
        "underlying",
        "product_type",
        "product_kwargs",
        "engine_name",
        "engine_kwargs",
        "quantity",
        "entry_price",
        "status",
        "source_trade_id",
        "source_row",
        "mapping_status",
        "mapping_error",
        "source_payload",
        "rfq_id",
        "rfq_quote_version_id",
        "trade_effective_date",
        "kwargs_migrated_at",
        "version",
        "created_at",
        "updated_at",
    }
    if "positions" not in _tables() or not required_columns.issubset(_columns("positions")):
        return

    from datetime import datetime

    # Reuse only the *pure* spec/hash helpers: they take no Session and never
    # touch the database, so they stay safe inside a migration. Product rows are
    # written through migration-local Core tables that declare ONLY the columns
    # 0018 creates -- so columns added by later revisions (e.g. ``underlying_id``
    # from 0019) can never leak into these statements and break the upgrade.
    from app.services.domains.products import (
        normalize_terms,
        product_spec_from_position_payload,
        product_term_hash,
    )

    metadata = sa.MetaData()
    positions_table = sa.Table(
        "positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer()),
        sa.Column("underlying", sa.String()),
        sa.Column("product_type", sa.String()),
        sa.Column("product_kwargs", sa.JSON()),
        sa.Column("source_payload", sa.JSON()),
    )
    products_table = sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_class", sa.String()),
        sa.Column("product_family", sa.String()),
        sa.Column("quantark_class", sa.String()),
        sa.Column("display_name", sa.String()),
        sa.Column("underlying", sa.String()),
        sa.Column("currency", sa.String()),
        sa.Column("term_hash", sa.String()),
        sa.Column("raw_terms", sa.JSON()),
        sa.Column("source_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )

    bind = op.get_bind()
    legacy_rows = bind.execute(
        sa.select(
            positions_table.c.id,
            positions_table.c.underlying,
            positions_table.c.product_type,
            positions_table.c.product_kwargs,
            positions_table.c.source_payload,
        ).where(positions_table.c.product_id.is_(None))
    ).all()

    now = datetime.utcnow()
    for row in legacy_rows:
        spec = product_spec_from_position_payload(
            {
                "underlying": row.underlying,
                "product_type": row.product_type,
                "product_kwargs": row.product_kwargs or {},
                "source_payload": row.source_payload or {},
            }
        )
        insert_result = bind.execute(
            products_table.insert().values(
                asset_class=spec.asset_class,
                product_family=spec.product_family,
                quantark_class=spec.quantark_class,
                display_name=spec.display_name,
                underlying=spec.underlying,
                currency=spec.currency,
                term_hash=product_term_hash(spec),
                raw_terms=normalize_terms(
                    {"terms": spec.terms, "components": spec.components}
                ),
                source_payload=normalize_terms(spec.source_payload or {}),
                created_at=now,
                updated_at=now,
            )
        )
        product_id = insert_result.inserted_primary_key[0]
        bind.execute(
            positions_table.update()
            .where(positions_table.c.id == row.id)
            .values(product_id=product_id)
        )
