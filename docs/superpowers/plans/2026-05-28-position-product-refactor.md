# Position Product Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Open OTC so positions reference normalized QuantArk-aligned product rows while preserving legacy position APIs and current pricing/RFQ/import behavior.

**Architecture:** Use the approved Product Root Table + Family Detail Tables design. Product terms live in `products` plus family detail tables; positions become holding/trade records with `product_id` and temporary compatibility fields. All booking paths converge through a deterministic booking service that creates products, positions, compatibility mirrors, and audit records.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Alembic, Pydantic, SQLite local repair helpers, Vitest/React Testing Library, pytest.

---

## File Structure

- Create `backend/alembic/versions/0018_product_root_family_tables.py`: canonical migration for product tables and `positions.product_id`.
- Modify `backend/app/models.py`: ORM models and `Position.product_id` relationship.
- Modify `backend/app/database.py`: SQLite additive local repair for product tables and `positions.product_id`.
- Create `backend/app/services/domains/products.py`: product specs, term hashing, create/reuse, family-row persistence, compatibility hydration, and product query helpers.
- Create `backend/app/services/domains/booking.py`: shared position booking service.
- Modify `backend/app/schemas.py`: product API schemas, `PortfolioPositionSpec.product`, and `PositionOut.product`.
- Modify `backend/app/main.py`: manual position create/patch uses booking/product service while preserving response shape.
- Modify `backend/app/services/rfq.py`: RFQ booking uses booking service.
- Modify `backend/app/services/position_adapter.py`: import path uses booking service after mapping rows.
- Modify `backend/app/services/domains/position_terms.py`: compatibility mirrors can hydrate from product-linked rows when needed.
- Modify `backend/app/tools/positions.py`: expose product-aware query/booking helpers for agents.
- Modify frontend files only after backend compatibility is stable:
  - `frontend/src/types.ts`
  - `frontend/src/routes/Positions.tsx`
  - `frontend/src/components/PositionEditForm.tsx`
  - `frontend/src/components/ProductTermsForm.tsx`
  - focused tests beside those components.
- Add tests:
  - `tests/test_product_models.py`
  - `tests/test_product_booking.py`
  - `tests/test_product_migration.py`
  - update `tests/test_api.py`
  - update `tests/test_structured_position_terms.py`
  - update `tests/test_tools_positions.py`
  - update RFQ/import tests that assert direct `Position(...)` behavior.

## Task 1: Schema Migration and ORM Models

**Files:**
- Create: `backend/alembic/versions/0018_product_root_family_tables.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Test: `tests/test_product_migration.py`

- [ ] **Step 1: Write migration smoke tests**

Create `tests/test_product_migration.py`:

```python
from __future__ import annotations

from sqlalchemy import inspect

from app.models import Portfolio, Position


def test_product_tables_exist_after_metadata_create(session):
    bind = session.get_bind()
    tables = set(inspect(bind).get_table_names())
    assert "products" in tables
    assert "equity_option_products" in tables
    assert "equity_autocallable_products" in tables
    assert "equity_autocallable_observations" in tables
    assert "equity_phoenix_coupon_products" in tables
    assert "equity_barrier_products" in tables
    assert "equity_touch_products" in tables
    assert "equity_asian_products" in tables
    assert "equity_asian_observations" in tables
    assert "equity_range_accrual_products" in tables
    assert "equity_range_accrual_observations" in tables
    assert "equity_sharkfin_products" in tables
    assert "equity_spot_products" in tables
    assert "equity_futures_products" in tables
    assert "equity_product_components" in tables


def test_position_can_reference_product(session):
    portfolio = Portfolio(name="Book", base_currency="USD")
    session.add(portfolio)
    session.flush()

    position = Position(
        portfolio_id=portfolio.id,
        product_id=None,
        underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "maturity": 1.0, "option_type": "CALL"},
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        status="open",
    )
    session.add(position)
    session.flush()

    assert position.id is not None
    assert position.product_id is None
```

- [ ] **Step 2: Run migration tests and verify failure**

Run:

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_migration.py -q
```

Expected: FAIL because product ORM tables do not exist and `Position.product_id` is not mapped.

- [ ] **Step 3: Add ORM models**

In `backend/app/models.py`, add the product models after `Position` or immediately before the existing position-linked term mirrors:

```python
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(40), default="equity", nullable=False)
    product_family: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    quantark_class: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    underlying: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    term_hash: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    raw_terms: Mapped[dict] = mapped_column(JSON, default=dict)
    source_payload: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    positions: Mapped[list["Position"]] = relationship(back_populates="product")
    option_terms: Mapped["EquityOptionProduct | None"] = relationship(back_populates="product", cascade="all, delete-orphan", uselist=False)
```

Add family models with the exact table names from the approved spec. Use SQLAlchemy class names:

```python
class EquityOptionProduct(Base):
    __tablename__ = "equity_option_products"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exercise_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    maturity: Mapped[float | None] = mapped_column(Float, nullable=True)
    exercise_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tenor: Mapped[float | None] = mapped_column(Float, nullable=True)
    tenor_end: Mapped[str | None] = mapped_column(String(40), nullable=True)
    annualization_day_count: Mapped[str | None] = mapped_column(String(40), nullable=True)
    initial_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    contract_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    product: Mapped[Product] = relationship(back_populates="option_terms")
```

Add the remaining classes with these exact table names and primary keys:

- `EquityAutocallableProduct`: `__tablename__ = "equity_autocallable_products"`, primary key `product_id`.
- `EquityAutocallableObservation`: `__tablename__ = "equity_autocallable_observations"`, primary key `id`, unique constraint on `product_id`, `observation_role`, `sequence`.
- `EquityPhoenixCouponProduct`: `__tablename__ = "equity_phoenix_coupon_products"`, primary key `product_id`.
- `EquityBarrierProduct`: `__tablename__ = "equity_barrier_products"`, primary key `product_id`.
- `EquityTouchProduct`: `__tablename__ = "equity_touch_products"`, primary key `product_id`.
- `EquityAsianProduct`: `__tablename__ = "equity_asian_products"`, primary key `product_id`.
- `EquityAsianObservation`: `__tablename__ = "equity_asian_observations"`, primary key `product_id`, `sequence`.
- `EquityRangeAccrualProduct`: `__tablename__ = "equity_range_accrual_products"`, primary key `product_id`.
- `EquityRangeAccrualObservation`: `__tablename__ = "equity_range_accrual_observations"`, primary key `product_id`, `sequence`.
- `EquitySharkfinProduct`: `__tablename__ = "equity_sharkfin_products"`, primary key `product_id`.
- `EquitySpotProduct`: `__tablename__ = "equity_spot_products"`, primary key `product_id`.
- `EquityFuturesProduct`: `__tablename__ = "equity_futures_products"`, primary key `product_id`.
- `EquityProductComponent`: `__tablename__ = "equity_product_components"`, primary key `id`, unique constraint on `parent_product_id`, `sequence`.

Use the column names and nullability from the approved spec. Do not add Airbag columns.

Do not include Airbag fields.

- [ ] **Step 4: Add `Position.product_id` and relationships**

In `Position`, add:

```python
product_id: Mapped[int | None] = mapped_column(
    ForeignKey("products.id"), index=True, nullable=True
)
```

Add relationship:

```python
product: Mapped["Product | None"] = relationship(back_populates="positions")
```

Add `"product_id"` to `_POSITION_VERSION_FIELDS`.

- [ ] **Step 5: Add Alembic migration 0018**

Create `backend/alembic/versions/0018_product_root_family_tables.py` with `down_revision = "0017_position_lifecycle_event_cancellation"`. Build it from the ORM models added in Step 3, then manually verify the migration creates these exact objects:

```python
revision = "0018_product_root_family_tables"
down_revision = "0017_position_lifecycle_event_cancellation"
```

The `upgrade()` function must create all product tables listed in Step 3, create indexes `ix_products_asset_family`, `ix_products_underlying`, `ix_products_quantark_class`, `ix_products_term_hash`, and add nullable `positions.product_id` with index `ix_positions_product_id`. The `downgrade()` function must drop `positions.product_id` and product tables in reverse dependency order.

The downgrade should drop `positions.product_id` and product tables in reverse dependency order.

- [ ] **Step 6: Add SQLite local repair helpers**

In `backend/app/database.py`, add a product schema repair function called from `ensure_sqlite_schema()` after the existing position column repairs:

```python
def _ensure_product_schema(active_engine: Engine, inspector: Any, tables: set[str]) -> None:
    if active_engine.dialect.name != "sqlite":
        return
    with active_engine.begin() as connection:
        if "products" not in tables:
            connection.execute(
                text(
                    "CREATE TABLE products ("
                    "id INTEGER NOT NULL, "
                    "asset_class VARCHAR(40) NOT NULL, "
                    "product_family VARCHAR(40) NOT NULL, "
                    "quantark_class VARCHAR(120), "
                    "display_name VARCHAR(160), "
                    "underlying VARCHAR(80) NOT NULL, "
                    "currency VARCHAR(8) NOT NULL, "
                    "term_hash VARCHAR(80) NOT NULL, "
                    "raw_terms JSON NOT NULL, "
                    "source_payload JSON, "
                    "created_at DATETIME NOT NULL, "
                    "updated_at DATETIME NOT NULL, "
                    "PRIMARY KEY (id)"
                    ")"
                )
            )
        if "product_id" not in {c["name"] for c in inspector.get_columns("positions")}:
            connection.execute(text("ALTER TABLE positions ADD COLUMN product_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_product_id ON positions (product_id)"))
```

Add matching `CREATE TABLE IF NOT EXISTS` SQL for every family table from Step 3. Keep the local repair additive; do not rebuild existing user tables.

- [ ] **Step 7: Run schema tests**

Run:

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_migration.py -q
```

Expected: PASS.

- [ ] **Step 8: Run Alembic upgrade**

Run:

```bash
cd backend && env LANGSMITH_TRACING=false ../.venv/bin/alembic upgrade head
```

Expected: migration completes with head `0018_product_root_family_tables`.

- [ ] **Step 9: Commit schema slice**

```bash
git add backend/alembic/versions/0018_product_root_family_tables.py backend/app/models.py backend/app/database.py tests/test_product_migration.py
git commit -m "feat(positions): add product root schema"
```

## Task 2: Product Domain Service

**Files:**
- Create: `backend/app/services/domains/products.py`
- Test: `tests/test_product_models.py`

- [ ] **Step 1: Write product service tests**

Create `tests/test_product_models.py` with cases:

```python
from __future__ import annotations

from app.models import EquityAutocallableObservation, Product
from app.services.domains.products import ProductSpec, create_or_get_product, compatibility_terms


def test_create_snowball_product_with_ko_schedule(session):
    spec = ProductSpec(
        asset_class="equity",
        product_family="autocallable",
        quantark_class="SnowballOption",
        underlying="000300.SH",
        currency="CNY",
        terms={
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity": 1.0,
            "contract_multiplier": 1.0,
            "barrier_config": {
                "ko_barrier": 103.0,
                "ko_rate": 0.15,
                "ki_barrier": 75.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "barrier_level": 103.0, "rate": 0.15}
                ],
            },
            "payoff_config": {"include_principal": False},
            "accrual_config": {},
        },
    )

    product = create_or_get_product(session, spec, reuse=False)
    session.flush()

    assert product.id is not None
    assert product.product_family == "autocallable"
    assert product.quantark_class == "SnowballOption"
    rows = session.query(EquityAutocallableObservation).filter_by(product_id=product.id).all()
    assert [(row.observation_role, row.sequence, row.barrier_level) for row in rows] == [("ko", 0, 103.0)]


def test_create_fund_product_maps_to_etf_spot(session):
    spec = ProductSpec(
        asset_class="equity",
        product_family="spot",
        quantark_class="SpotInstrument",
        underlying="510300.SH",
        currency="CNY",
        terms={"deltaone_type": "ETF", "instrument_code": "510300.SH", "contract_multiplier": 1.0},
    )

    product = create_or_get_product(session, spec, reuse=False)
    terms = compatibility_terms(product)

    assert product.product_family == "spot"
    assert terms["product_type"] == "SpotInstrument"
    assert terms["product_kwargs"]["deltaone_type"] == "ETF"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_models.py -q
```

Expected: FAIL because `backend/app/services/domains/products.py` does not exist.

- [ ] **Step 3: Implement product spec and hashing**

Create `backend/app/services/domains/products.py`:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import Product, utcnow


@dataclass(frozen=True)
class ProductSpec:
    asset_class: str
    product_family: str
    quantark_class: str | None
    underlying: str
    currency: str = "USD"
    terms: dict[str, Any] = field(default_factory=dict)
    components: list[dict[str, Any]] = field(default_factory=list)
    display_name: str | None = None
    source_payload: dict[str, Any] | None = None


def normalize_terms(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize_terms(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize_terms(item) for item in value]
    return value


def product_term_hash(spec: ProductSpec) -> str:
    payload = {
        "asset_class": spec.asset_class,
        "product_family": spec.product_family,
        "quantark_class": spec.quantark_class,
        "underlying": spec.underlying,
        "currency": spec.currency,
        "terms": normalize_terms(spec.terms),
        "components": normalize_terms(spec.components),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Implement create/reuse and family writers**

Add:

```python
def create_or_get_product(session: Session, spec: ProductSpec, *, reuse: bool = True) -> Product:
    term_hash = product_term_hash(spec)
    if reuse:
        existing = session.query(Product).filter(Product.term_hash == term_hash).one_or_none()
        if existing is not None:
            return existing
    product = Product(
        asset_class=spec.asset_class,
        product_family=spec.product_family,
        quantark_class=spec.quantark_class,
        display_name=spec.display_name,
        underlying=spec.underlying,
        currency=spec.currency,
        term_hash=term_hash,
        raw_terms=normalize_terms({"terms": spec.terms, "components": spec.components}),
        source_payload=normalize_terms(spec.source_payload or {}),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(product)
    session.flush()
    _write_family_rows(session, product, spec)
    return product
```

Implement `_write_family_rows()` dispatches for:

- option-like scalar row,
- autocallable row and observations,
- Phoenix coupon row,
- barrier row,
- touch row,
- Asian row and observations,
- range-accrual row and observations,
- sharkfin row,
- spot row,
- futures row,
- product components.

Each dispatcher should use `session.merge(row)` for one-to-one rows and delete/replace observation/component rows for the product before inserting new rows.

- [ ] **Step 5: Implement compatibility helpers**

Add:

```python
def compatibility_terms(product: Product) -> dict[str, Any]:
    raw = dict(product.raw_terms or {})
    terms = dict(raw.get("terms") or {})
    return {
        "underlying": product.underlying,
        "product_type": product.quantark_class or product.product_family,
        "product_kwargs": terms,
    }


def hydrate_position_product_fields(position) -> None:
    if position.product is None:
        return
    terms = compatibility_terms(position.product)
    position.underlying = terms["underlying"]
    position.product_type = terms["product_type"]
    position.product_kwargs = terms["product_kwargs"]
```

- [ ] **Step 6: Implement legacy payload mapper**

Add:

```python
def product_spec_from_position_payload(payload: dict[str, Any]) -> ProductSpec:
    product_type = str(payload.get("product_type") or "EuropeanVanillaOption")
    kwargs = dict(payload.get("product_kwargs") or {})
    underlying = str(payload.get("underlying") or kwargs.get("underlying") or "UNKNOWN")
    family = product_family_for_quantark_class(product_type)
    return ProductSpec(
        asset_class="equity",
        product_family=family,
        quantark_class=product_type,
        underlying=underlying,
        currency=str(kwargs.get("currency") or payload.get("currency") or "USD"),
        terms=kwargs,
        source_payload=dict(payload.get("source_payload") or {}),
    )
```

`product_family_for_quantark_class()` must map known classes from the approved spec and use `"package"` only when explicit components are supplied.

- [ ] **Step 7: Run product tests**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_models.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit product service**

```bash
git add backend/app/services/domains/products.py tests/test_product_models.py
git commit -m "feat(positions): add product domain service"
```

## Task 3: Backfill Legacy Positions to Products

**Files:**
- Modify: `backend/alembic/versions/0018_product_root_family_tables.py`
- Modify: `backend/app/services/domains/products.py`
- Test: `tests/test_product_migration.py`

- [ ] **Step 1: Add backfill test**

Extend `tests/test_product_migration.py`:

```python
from app.services.domains.products import backfill_position_products


def test_backfill_creates_one_product_per_legacy_position(session):
    portfolio = Portfolio(name="Legacy Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    first = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={"initial_price": 100.0, "strike": 100.0, "maturity": 1.0},
        engine_name="SnowballQuadEngine",
        engine_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        status="open",
    )
    second = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={"initial_price": 100.0, "strike": 100.0, "maturity": 1.0},
        engine_name="SnowballQuadEngine",
        engine_kwargs={},
        quantity=2.0,
        entry_price=0.0,
        status="open",
    )
    session.add_all([first, second])
    session.flush()

    count = backfill_position_products(session)
    session.flush()

    assert count == 2
    assert first.product_id is not None
    assert second.product_id is not None
    assert first.product_id != second.product_id
```

- [ ] **Step 2: Run test and verify failure**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_migration.py::test_backfill_creates_one_product_per_legacy_position -q
```

Expected: FAIL because `backfill_position_products` does not exist.

- [ ] **Step 3: Implement backfill helper**

In `products.py`, add:

```python
def backfill_position_products(session: Session, *, limit: int | None = None) -> int:
    from ...models import Position

    query = session.query(Position).filter(Position.product_id.is_(None))
    if limit is not None:
        query = query.limit(limit)
    positions = query.all()
    for position in positions:
        spec = product_spec_from_position_payload(
            {
                "underlying": position.underlying,
                "product_type": position.product_type,
                "product_kwargs": position.product_kwargs or {},
                "source_payload": position.source_payload or {},
            }
        )
        product = create_or_get_product(session, spec, reuse=False)
        position.product_id = product.id
    session.flush()
    return len(positions)
```

- [ ] **Step 4: Wire Alembic backfill**

In migration 0018, after adding `positions.product_id`, call `backfill_position_products(Session(bind=op.get_bind()))` in a guarded block. Use the app service so migration and local repair share mapping semantics.

- [ ] **Step 5: Run migration tests**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_migration.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit backfill**

```bash
git add backend/alembic/versions/0018_product_root_family_tables.py backend/app/services/domains/products.py tests/test_product_migration.py
git commit -m "feat(positions): backfill products from legacy positions"
```

## Task 4: Shared Booking Service and API Schemas

**Files:**
- Create: `backend/app/services/domains/booking.py`
- Modify: `backend/app/schemas.py`
- Test: `tests/test_product_booking.py`

- [ ] **Step 1: Write booking tests**

Create `tests/test_product_booking.py`:

```python
from __future__ import annotations

from app.models import Portfolio, PortfolioKind, Position
from app.services.domains.booking import BookingRequest, ProductBookingSpec, book_position


def test_booking_creates_product_and_position(session):
    portfolio = Portfolio(name="Book", base_currency="CNY", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    booked = book_position(
        session,
        BookingRequest(
            portfolio_id=portfolio.id,
            product=ProductBookingSpec(
                asset_class="equity",
                product_family="spot",
                quantark_class="SpotInstrument",
                underlying="510300.SH",
                currency="CNY",
                terms={"deltaone_type": "ETF", "instrument_code": "510300.SH"},
            ),
            quantity=100.0,
            entry_price=4.2,
            status="open",
            source_trade_id="FUND-001",
            engine_name="DeltaOneEngine",
            engine_kwargs={},
            actor="desk_user",
            source="manual",
        ),
    )
    session.flush()

    assert booked.product_id is not None
    assert booked.product_type == "SpotInstrument"
    assert booked.product_kwargs["deltaone_type"] == "ETF"
    assert session.query(Position).count() == 1
```

- [ ] **Step 2: Run booking test and verify failure**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_booking.py -q
```

Expected: FAIL because `booking.py` does not exist.

- [ ] **Step 3: Implement booking dataclasses and service**

Create `backend/app/services/domains/booking.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import Portfolio, PortfolioKind, Position, utcnow
from ..audit import record_audit
from .position_terms import refresh_position_barrier_state, upsert_position_term_rows
from .products import ProductSpec, create_or_get_product, hydrate_position_product_fields


@dataclass(frozen=True)
class ProductBookingSpec(ProductSpec):
    pass


@dataclass(frozen=True)
class BookingRequest:
    portfolio_id: int
    product: ProductBookingSpec
    quantity: float
    entry_price: float = 0.0
    status: str = "open"
    source_trade_id: str | None = None
    source_row: int | None = None
    mapping_status: str = "supported"
    mapping_error: str | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: datetime | None = None
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = field(default_factory=dict)
    actor: str = "desk_user"
    source: str = "manual"


def book_position(session: Session, request: BookingRequest, *, reuse_product: bool = True) -> Position:
    portfolio = session.get(Portfolio, request.portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio {request.portfolio_id} not found")
    if portfolio.kind != PortfolioKind.CONTAINER.value:
        raise ValueError("Position booking is only available for container portfolios")
    product = create_or_get_product(session, request.product, reuse=reuse_product)
    position = Position(
        portfolio_id=portfolio.id,
        product_id=product.id,
        engine_name=request.engine_name,
        engine_kwargs=request.engine_kwargs,
        quantity=request.quantity,
        entry_price=request.entry_price,
        status=request.status,
        source_trade_id=request.source_trade_id,
        source_row=request.source_row,
        mapping_status=request.mapping_status,
        mapping_error=request.mapping_error,
        source_payload=request.source_payload,
        rfq_id=request.rfq_id,
        rfq_quote_version_id=request.rfq_quote_version_id,
        trade_effective_date=request.trade_effective_date,
    )
    position.product = product
    hydrate_position_product_fields(position)
    session.add(position)
    session.flush()
    upsert_position_term_rows(session, position)
    refresh_position_barrier_state(session, position_id=position.id)
    record_audit(
        session,
        event_type="position.booked",
        actor=request.actor,
        subject_type="position",
        subject_id=position.id,
        payload={"source": request.source, "product_id": product.id, "portfolio_id": portfolio.id},
    )
    return position
```

- [ ] **Step 4: Add Pydantic schemas**

In `backend/app/schemas.py`, add:

```python
class ProductSpecIn(BaseModel):
    asset_class: str = "equity"
    product_family: str = "option"
    quantark_class: str | None = "EuropeanVanillaOption"
    underlying: str = "CSI500"
    currency: str = "USD"
    terms: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)
    display_name: str | None = None
    source_payload: dict[str, Any] | None = None


class ProductOut(ProductSpecIn):
    id: int
    term_hash: str
    raw_terms: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}
```

Modify `PortfolioPositionSpec`:

```python
product: ProductSpecIn | None = None
```

Modify `PositionOut`:

```python
product_id: int | None = None
product: ProductOut | None = None
```

- [ ] **Step 5: Run booking tests**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_product_booking.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit booking service**

```bash
git add backend/app/services/domains/booking.py backend/app/schemas.py tests/test_product_booking.py
git commit -m "feat(positions): add shared booking service"
```

## Task 5: Move Manual API, RFQ Booking, and Import Paths

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/rfq.py`
- Modify: `backend/app/services/position_adapter.py`
- Test: `tests/test_api.py`
- Test: existing RFQ/import tests

- [ ] **Step 1: Add API regression tests**

In `tests/test_api.py`, add one manual create test near existing position endpoint tests:

```python
def test_add_position_creates_product_and_keeps_legacy_fields(client):
    portfolio = client.post("/api/portfolios", json={"name": "Book", "base_currency": "USD"}).json()
    response = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "product": {
                "asset_class": "equity",
                "product_family": "spot",
                "quantark_class": "SpotInstrument",
                "underlying": "AAPL",
                "currency": "USD",
                "terms": {"deltaone_type": "STOCK", "instrument_code": "AAPL"},
            },
            "quantity": 10,
            "entry_price": 180.0,
            "engine_name": "DeltaOneEngine",
        },
    )

    assert response.status_code == 200
    body = response.json()
    row = body["positions"][0]
    assert row["product_id"] is not None
    assert row["product_type"] == "SpotInstrument"
    assert row["product_kwargs"]["deltaone_type"] == "STOCK"
```

- [ ] **Step 2: Run API test and verify failure**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_api.py::test_add_position_creates_product_and_keeps_legacy_fields -q
```

Expected: FAIL because route does not call booking service and response lacks `product_id`.

- [ ] **Step 3: Update manual create route**

In `backend/app/main.py`, change `add_position()` to:

```python
from app.services.domains.booking import BookingRequest, ProductBookingSpec, book_position
from app.services.domains.products import product_spec_from_position_payload


def _booking_product_from_payload(payload: PortfolioPositionSpec) -> ProductBookingSpec:
    if payload.product is not None:
        data = payload.product.model_dump(mode="json")
        return ProductBookingSpec(**data)
    spec = product_spec_from_position_payload(payload.model_dump(mode="json"))
    return ProductBookingSpec(**spec.__dict__)
```

Then create via:

```python
book_position(
    session,
    BookingRequest(
        portfolio_id=portfolio.id,
        product=_booking_product_from_payload(payload),
        quantity=payload.quantity,
        entry_price=payload.entry_price,
        status=payload.status,
        source_trade_id=payload.source_trade_id,
        rfq_id=payload.rfq_id,
        rfq_quote_version_id=payload.rfq_quote_version_id,
        trade_effective_date=_to_datetime(payload.trade_effective_date),
        engine_name=payload.engine_name,
        engine_kwargs=payload.engine_kwargs,
        actor="desk_user",
        source="manual",
        source_payload=payload.model_dump(mode="json"),
    ),
)
```

- [ ] **Step 4: Update patch route**

Keep PATCH compatibility. If `payload.product` is present, create/reuse product and update `position.product_id`, hydrate legacy fields, and upsert mirrors. If no product is present, preserve existing editable legacy fields.

- [ ] **Step 5: Move RFQ booking**

In `backend/app/services/rfq.py`, replace direct `Position(...)` construction in `book_rfq_to_position()` with `book_position()`. Build `ProductBookingSpec` from executable terms:

```python
from .domains.booking import BookingRequest, ProductBookingSpec, book_position
from .domains.products import product_spec_from_executable_terms

product_spec = product_spec_from_executable_terms(terms)
position = book_position(
    session,
    BookingRequest(
        portfolio_id=portfolio.id,
        product=ProductBookingSpec(**product_spec.__dict__),
        quantity=payload.quantity if payload.quantity is not None else _signed_booking_quantity(terms),
        entry_price=payload.entry_price if payload.entry_price is not None else _quote_price(quote),
        status="open",
        source_trade_id=f"RFQ-{rfq.id}-V{latest.version}",
        mapping_status="supported",
        source_payload={
            "source": "rfq",
            "rfq_id": rfq.id,
            "quote_version_id": latest.id,
            "request_payload": rfq.request_payload,
            "executable_terms": model_to_dict(terms),
            "quote_payload": quote,
        },
        rfq_id=rfq.id,
        rfq_quote_version_id=latest.id,
        trade_effective_date=_to_datetime(payload.trade_effective_date),
        engine_name=terms.engine_spec.engine_name,
        engine_kwargs=_engine_kwargs_for_position(terms.engine_spec),
        actor=payload.actor,
        source="rfq",
    ),
)
```

- [ ] **Step 6: Move import path**

In `backend/app/services/position_adapter.py`, replace direct position creation/update with:

- for new rows: build a `BookingRequest` from `PositionMapping` and call `book_position(session, request)`,
- for existing rows: update product via product service, set `product_id`, hydrate compatibility fields, then update position-level fields.

Keep import batch counts unchanged.

- [ ] **Step 7: Run targeted backend tests**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_api.py tests/test_product_booking.py tests/test_position_import_pricing.py -q
```

Expected: PASS or only unrelated pre-existing failures. If failures are related to response shape, update `PositionOut` serialization rather than reverting product creation.

- [ ] **Step 8: Commit booking path migration**

```bash
git add backend/app/main.py backend/app/services/rfq.py backend/app/services/position_adapter.py tests/test_api.py tests/test_product_booking.py tests/test_position_import_pricing.py
git commit -m "feat(positions): route booking paths through products"
```

## Task 6: Product Query Helpers and Agent Tools

**Files:**
- Modify: `backend/app/services/domains/products.py`
- Modify: `backend/app/tools/positions.py`
- Test: `tests/test_tools_positions.py`

- [ ] **Step 1: Write product-query tests**

Add tests:

```python
def test_query_autocallable_observations_without_product_kwargs(session):
    from app.services.domains.products import query_autocallable_observations

    product = create_or_get_product(session, snowball_spec_with_schedule(), reuse=False)
    rows = query_autocallable_observations(session, product_id=product.id, role="ko")

    assert rows[0]["observation_role"] == "ko"
    assert rows[0]["barrier_level"] == 103.0
```

Add an agent tool test that invokes the wrapper and asserts no raw `product_kwargs` is required in arguments.

- [ ] **Step 2: Implement query helpers**

In `products.py`, add:

```python
def query_products(session: Session, *, family: str | None = None, quantark_class: str | None = None, underlying: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = session.query(Product)
    if family:
        query = query.filter(Product.product_family == family)
    if quantark_class:
        query = query.filter(Product.quantark_class == quantark_class)
    if underlying:
        query = query.filter(Product.underlying == underlying)
    return [product_summary(row) for row in query.limit(min(limit, 1000)).all()]
```

Add `query_autocallable_observations()` and `product_summary()`.

- [ ] **Step 3: Expose tool wrappers**

In `backend/app/tools/positions.py`, add tools:

- `query_products`
- `query_autocallable_observations`
- `book_position`

Use Pydantic input models with allowlisted fields. Do not expose raw SQL.

- [ ] **Step 4: Run tool tests**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest tests/test_tools_positions.py tests/test_product_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit tools**

```bash
git add backend/app/services/domains/products.py backend/app/tools/positions.py tests/test_tools_positions.py tests/test_product_models.py
git commit -m "feat(agent): add product-aware position tools"
```

## Task 7: Frontend Compatibility Surface

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/routes/Positions.tsx`
- Modify: `frontend/src/components/PositionEditForm.tsx`
- Modify: `frontend/src/components/ProductTermsForm.tsx`
- Test: focused frontend tests under `frontend/src`

- [ ] **Step 1: Add frontend type tests or update existing tests**

Update existing position fixture tests so `PositionRow` accepts:

```ts
product_id?: number | null;
product?: {
  id: number;
  asset_class: string;
  product_family: string;
  quantark_class?: string | null;
  underlying: string;
  currency: string;
  terms?: Record<string, unknown>;
  raw_terms?: Record<string, unknown>;
} | null;
```

- [ ] **Step 2: Update types**

In `frontend/src/types.ts` or the local `PositionRow` definition, add `product_id` and `product`.

- [ ] **Step 3: Update edit form submission**

In `PositionEditForm.tsx`, preserve existing JSON editor behavior but submit both:

```ts
const product = {
  asset_class: 'equity',
  product_family: inferProductFamily(form.product_type),
  quantark_class: form.product_type,
  underlying: form.underlying,
  currency: 'USD',
  terms: product_kwargs,
};
```

Include `product` in the PATCH/POST payload while keeping `product_type` and `product_kwargs`.

- [ ] **Step 4: Update detail display**

In `Positions.tsx`, show product id/family/class in the product detail area. Keep the existing Contract Terms and Extra Fields sections.

- [ ] **Step 5: Run frontend tests**

```bash
cd frontend && npm test -- Positions PositionEditForm ProductTermsForm -- --runInBand
```

- [ ] **Step 6: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit frontend compatibility**

```bash
git add frontend/src/types.ts frontend/src/routes/Positions.tsx frontend/src/components/PositionEditForm.tsx frontend/src/components/ProductTermsForm.tsx frontend/src/**/*.test.tsx
git commit -m "feat(frontend): show product-backed positions"
```

## Task 8: Final Verification and Cleanup

**Files:**
- Review all touched files.

- [ ] **Step 1: Run backend targeted suite**

```bash
env LANGSMITH_TRACING=false ./.venv/bin/python -m pytest \
  tests/test_product_migration.py \
  tests/test_product_models.py \
  tests/test_product_booking.py \
  tests/test_structured_position_terms.py \
  tests/test_tools_positions.py \
  tests/test_api.py \
  tests/test_position_import_pricing.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run Alembic upgrade**

```bash
cd backend && env LANGSMITH_TRACING=false ../.venv/bin/alembic upgrade head
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 4: Search for accidental Airbag scope**

```bash
rg -n "Airbag|airbag" backend/app frontend/src tests
```

Expected: no new stage-one implementation references in backend/frontend/tests. Existing historical docs or QuantArk references may remain outside this plan.

- [ ] **Step 5: Inspect git diff**

```bash
git status --short
git diff --stat
```

Expected: only intended implementation files are modified.

- [ ] **Step 6: Final verification commit**

If verification required code or test fixes, stage the files touched by this implementation plan and commit them:

```bash
git add backend frontend tests
git commit -m "test(positions): verify product refactor"
```

If Step 5 shows a clean worktree, skip this commit step.
