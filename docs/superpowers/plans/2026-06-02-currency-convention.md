# Currency Convention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent app a coherent currency convention — per-position currency, currency-aware risk aggregation with a per-currency breakdown, a deterministic FX `convert_currency` tool driven by a per-thread report currency, and FX-rate management on the Market Data page.

**Architecture:** "Honest engine, converting presenter." `calculate_portfolio_risk` groups money metrics by each position's currency (never silently cross-summing) and emits a `by_currency` breakdown plus a currency-invariant `shared` block. A separate, pure `convert_currency` tool collapses those groups into one currency when the agent asks, using reproducible valuation-snapshot FX rates resolved "latest ≤ valuation_date." The thread's `report_currency` (ISO code or `by_position`) flows through the ContextPack and persona prompt to tell the agent whether/what to convert.

**Tech Stack:** Python 3 / SQLAlchemy 2 ORM / Alembic / FastAPI / Pydantic v2 / LangChain `@tool` / pytest; React + TypeScript + Vitest frontend.

**Spec:** `docs/superpowers/specs/2026-06-02-currency-convention-design.md`

**Worktree / test invocation:** Work in `/Users/fuxinyao/open-otc-trading-currency`. The `.venv` lives in the main checkout, so run tests as:

```bash
cd /Users/fuxinyao/open-otc-trading-currency
PYTHONPATH=/Users/fuxinyao/open-otc-trading-currency/backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest <args>
```

Every commit message ends with the trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## File Structure

**New files**
- `backend/app/services/fx.py` — FX domain: `fx_rate_as_of` resolver, pure `convert_risk_currency`, and FxRate CRUD helpers (`list_fx_rates`, `create_fx_rate`, `delete_fx_rate`, `fetch_akshare_fx_rate`).
- `backend/app/services/risk_currency.py` — pure currency-aware aggregator `build_currency_aware_totals` + the metric dimension map (`MONEY_METRIC_KEYS`, `SHARED_METRIC_KEYS`, `metric_dimension`).
- `backend/alembic/versions/0020_currency_convention.py` — schema + backfill migration.
- Tests: `tests/test_metric_dimension_map.py`, `tests/test_currency_aggregation.py`, `tests/test_fx_resolver.py`, `tests/test_convert_currency.py`, `tests/test_position_currency.py`, `tests/test_report_currency_plumbing.py`, `tests/test_fx_rates_api.py`.

**Modified files**
- `backend/app/models.py` — `Position.currency`, `AgentThread.report_currency`, new `FxRate`.
- `backend/app/services/quantark.py` — emit per-position `currency`; delegate final aggregation to `risk_currency.build_currency_aware_totals`.
- `backend/app/services/position_pricer.py` — rewire `_source_currency`.
- `backend/app/services/domains/booking.py` — set `position.currency` from product; soft underlying-mismatch warning.
- `backend/app/services/reports.py` — tolerate new risk-output shape.
- `backend/app/tools/risk.py` + `backend/app/tools/__init__.py` — `convert_currency` tool + registration.
- `backend/app/services/deep_agent/task_registry.py` — add `convert_currency` to risk/report task scopes.
- `backend/app/services/deep_agent/context_assembler.py` — inject `report_currency` into ContextPack `stable_payload`.
- `backend/app/services/deep_agent/personas.py` (or the prompt assembler it calls) — report-currency instruction block.
- `backend/app/main.py` — FxRate CRUD + akshare endpoints; thread `report_currency` PATCH; seed default.
- `backend/app/schemas.py` — `FxRateOut`/`FxRateCreate`, `AgentThreadOut.report_currency`.
- `frontend/src/routes/MarketData.tsx` + `frontend/src/api/client.ts` + `frontend/src/types.ts` + `frontend/src/routes/MarketData.test.tsx` — FX Rates tab.

---

## Phase A — Data model + migration

### Task A1: Add the three model changes

**Files:**
- Modify: `backend/app/models.py` (`Position` ~line 488; `AgentThread` ~line 116; add `FxRate` near `PricingParameterProfile` ~line 1197)
- Test: `tests/test_position_currency.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_position_currency.py
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_models_expose_currency_columns():
    from app.models import AgentThread, FxRate, Position

    assert "currency" in Position.__table__.columns
    assert "report_currency" in AgentThread.__table__.columns
    assert {"base_currency", "quote_currency", "rate", "as_of_date"} <= set(
        FxRate.__table__.columns.keys()
    )


def test_fx_rate_roundtrip():
    from datetime import datetime

    from app import database
    from app.models import FxRate

    with database.SessionLocal() as session:
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency="CNY",
                rate=7.2,
                as_of_date=datetime(2026, 6, 2),
                source="manual",
            )
        )
        session.commit()
        row = session.query(FxRate).one()
        assert row.rate == 7.2
        assert row.base_currency == "USD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py -v`
Expected: FAIL — `ImportError: cannot import name 'FxRate'`.

- [ ] **Step 3: Implement the model changes**

In `backend/app/models.py`, add to `Position` (after `entry_price`, ~line 505):

```python
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", server_default="CNY", nullable=False
    )
```

Add to `AgentThread` (after `character`, ~line 122):

```python
    report_currency: Mapped[str] = mapped_column(
        String(16), default="by_position", server_default="by_position", nullable=False
    )
```

Add the new model (place just above `class PricingParameterProfile`):

```python
class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        Index("ix_fx_rates_pair_as_of", "base_currency", "quote_currency", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    as_of_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
```

(`Index`, `String`, `Float`, `Integer`, `DateTime`, `ForeignKey`, `utcnow` are already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py tests/test_position_currency.py
git commit -m "feat(model): position.currency, agent_thread.report_currency, FxRate table"
```

---

### Task A2: Alembic migration 0020 (columns + fx_rates + Position.currency backfill)

**Files:**
- Create: `backend/alembic/versions/0020_currency_convention.py`
- Test: `tests/test_position_currency.py` (append)

Per project rule, the migration MUST use migration-local Core tables / raw SQL, never ORM models/services.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_position_currency.py
def test_migration_backfills_position_currency_from_product(tmp_path, monkeypatch):
    """Backfill priority: booked source payload ccy -> product.currency -> 'CNY'."""
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    db = f"sqlite:///{tmp_path}/m.db"
    engine = sa.create_engine(db)
    # Stamp to 0019, seed legacy rows WITHOUT position.currency, then upgrade to 0020.
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db)
    command.upgrade(cfg, "0019_underlying_master_data")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO products (id, asset_class, product_family, underlying, currency, term_hash) "
            "VALUES (1, 'equity', 'vanilla', 'AAPL', 'USD', 'h1')"
        ))
        conn.execute(sa.text(
            "INSERT INTO portfolios (id, name, base_currency, kind) "
            "VALUES (1, 'P', 'USD', 'container')"
        ))
        conn.execute(sa.text(
            "INSERT INTO positions (id, portfolio_id, product_id, underlying, product_type, "
            "quantity, entry_price, status, mapping_status, source_payload) "
            "VALUES (1, 1, 1, 'AAPL', 'vanilla', 1, 0, 'open', 'manual', '{}')"
        ))
    command.upgrade(cfg, "0020_currency_convention")
    with engine.begin() as conn:
        ccy = conn.execute(sa.text("SELECT currency FROM positions WHERE id = 1")).scalar()
        assert ccy == "USD"  # inherited from product
        assert conn.execute(sa.text("SELECT COUNT(*) FROM fx_rates")).scalar() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py::test_migration_backfills_position_currency_from_product -v`
Expected: FAIL — `Can't locate revision '0020_currency_convention'`.

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0020_currency_convention.py
"""currency convention: position.currency, agent_threads.report_currency, fx_rates

Revision ID: 0020_currency_convention
Revises: 0019_underlying_master_data
Create Date: 2026-06-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0020_currency_convention"
down_revision = "0019_underlying_master_data"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if "positions" in _tables() and "currency" not in _columns("positions"):
        op.add_column(
            "positions",
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        )
        # Backfill priority: source_payload booked ccy -> product.currency -> CNY.
        # (source_payload booked ccy is product-derived already at booking, so
        #  product.currency is the authoritative legacy fallback here.)
        bind.execute(text(
            "UPDATE positions SET currency = ("
            "  SELECT p.currency FROM products p WHERE p.id = positions.product_id"
            ") WHERE product_id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM products p WHERE p.id = positions.product_id"
            ")"
        ))
        bind.execute(text("UPDATE positions SET currency = 'CNY' WHERE currency IS NULL"))

    if "agent_threads" in _tables() and "report_currency" not in _columns("agent_threads"):
        op.add_column(
            "agent_threads",
            sa.Column(
                "report_currency", sa.String(length=16),
                nullable=False, server_default="by_position",
            ),
        )

    if "fx_rates" not in _tables():
        op.create_table(
            "fx_rates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("base_currency", sa.String(length=8), nullable=False),
            sa.Column("quote_currency", sa.String(length=8), nullable=False),
            sa.Column("rate", sa.Float(), nullable=False),
            sa.Column("as_of_date", sa.DateTime(), nullable=False),
            sa.Column("pricing_parameter_profile_id", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["pricing_parameter_profile_id"], ["pricing_parameter_profiles.id"]
            ),
        )
        op.create_index("ix_fx_rates_as_of_date", "fx_rates", ["as_of_date"])
        op.create_index(
            "ix_fx_rates_pair_as_of", "fx_rates",
            ["base_currency", "quote_currency", "as_of_date"],
        )


def downgrade() -> None:
    if "fx_rates" in _tables():
        op.drop_table("fx_rates")
    if "report_currency" in _columns("agent_threads"):
        op.drop_column("agent_threads", "report_currency")
    if "currency" in _columns("positions"):
        op.drop_column("positions", "currency")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0020_currency_convention.py tests/test_position_currency.py
git commit -m "feat(migration): add position.currency, report_currency, fx_rates with backfill"
```

---

## Phase B — Position currency wiring

### Task B1: Set `position.currency` at booking + soft underlying-mismatch warning

**Files:**
- Modify: `backend/app/services/domains/booking.py` (`book_position` ~line 220–241)
- Test: `tests/test_position_currency.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_position_currency.py
def test_booking_sets_currency_from_product_and_warns_on_mismatch(caplog):
    import logging
    from app import database
    from app.models import Portfolio, Product, Underlying
    from app.services.domains import booking as booking_svc

    with database.SessionLocal() as session:
        session.add(Underlying(symbol="AAPL", currency="USD", status="active"))
        session.add(Portfolio(name="P", base_currency="USD", kind="container"))
        session.commit()

    # Build a product whose currency is USD but underlying record says CNY -> warn.
    with database.SessionLocal() as session:
        u = session.query(Underlying).filter_by(symbol="AAPL").one()
        u.currency = "CNY"
        session.commit()

    # set_position_currency is the seam under test; assert it copies product ccy
    # and emits a warning when the linked underlying disagrees.
    with database.SessionLocal() as session:
        from types import SimpleNamespace
        product = SimpleNamespace(currency="USD")
        position = SimpleNamespace(currency=None, product=product,
                                   underlying_record=SimpleNamespace(currency="CNY"))
        with caplog.at_level(logging.WARNING):
            booking_svc.set_position_currency(position)
        assert position.currency == "USD"
        assert any("same currency for non-quanto" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py::test_booking_sets_currency_from_product_and_warns_on_mismatch -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'set_position_currency'`.

- [ ] **Step 3: Implement**

In `backend/app/services/domains/booking.py`, add a module-level logger if absent (`import logging; logger = logging.getLogger(__name__)`) and the helper:

```python
def set_position_currency(position) -> None:
    """Copy the booked product's currency onto the position (source of truth = the
    booked trade). Soft-warn (never block) when the linked underlying disagrees —
    that only happens for quanto products, which are not yet supported."""
    product = getattr(position, "product", None)
    currency = getattr(product, "currency", None) or "CNY"
    position.currency = currency
    underlying = getattr(position, "underlying_record", None)
    underlying_ccy = getattr(underlying, "currency", None)
    if underlying_ccy and underlying_ccy != currency:
        logger.warning(
            "Position and Underlying should have same currency for non-quanto "
            "products: position=%s underlying=%s",
            currency,
            underlying_ccy,
        )
```

Then, in `book_position`, after `link_position_underlying(session, position, source=request.source)` (~line 241), add:

```python
    set_position_currency(position)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/booking.py tests/test_position_currency.py
git commit -m "feat(booking): set position.currency from product, warn on underlying mismatch"
```

---

### Task B2: Rewire `_source_currency` to read `position.currency` first

**Files:**
- Modify: `backend/app/services/position_pricer.py` (`_source_currency` ~line 822–826)
- Test: `tests/test_position_currency.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_position_currency.py
def test_source_currency_prefers_position_column():
    from types import SimpleNamespace
    from app.services.position_pricer import _source_currency

    # New behavior: explicit column wins.
    pos = SimpleNamespace(currency="USD", source_payload={"交易规模单位": "CNY"})
    assert _source_currency(pos) == "USD"

    # Fallback: no column -> legacy source-payload scrape -> CNY default.
    legacy = SimpleNamespace(currency=None, source_payload={"交易规模单位": "EUR"})
    assert _source_currency(legacy) == "EUR"
    bare = SimpleNamespace(currency=None, source_payload={})
    assert _source_currency(bare) == "CNY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py::test_source_currency_prefers_position_column -v`
Expected: FAIL — explicit-column case returns `"CNY"` (current code ignores the column).

- [ ] **Step 3: Implement**

Replace `_source_currency` in `backend/app/services/position_pricer.py`:

```python
def _source_currency(position: Position) -> str:
    explicit = getattr(position, "currency", None)
    if explicit:
        return str(explicit)
    row = getattr(position, "source_payload", None)
    currency = text_value(row.get("交易规模单位")) if isinstance(row, dict) else ""
    return currency or "CNY"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_position_currency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_pricer.py tests/test_position_currency.py
git commit -m "feat(pricer): source position currency from the new column, scrape as fallback"
```

---

## Phase C — Metric dimension map + currency-aware aggregator (pure)

### Task C1: Metric dimension map

**Files:**
- Create: `backend/app/services/risk_currency.py`
- Test: `tests/test_metric_dimension_map.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metric_dimension_map.py
from app.services.quantark import RISK_GREEK_KEYS
from app.services.risk_currency import (
    MONEY_METRIC_KEYS,
    SHARED_METRIC_KEYS,
    metric_dimension,
)

ALL_TOTAL_KEYS = {
    "market_value", "delta_proxy", "gross_notional", "pnl", "one_day_var_proxy",
    *RISK_GREEK_KEYS,
}


def test_money_and_shared_partition_all_total_keys():
    assert MONEY_METRIC_KEYS.isdisjoint(SHARED_METRIC_KEYS)
    assert MONEY_METRIC_KEYS | SHARED_METRIC_KEYS == ALL_TOTAL_KEYS


def test_known_tags():
    assert metric_dimension("market_value") == "money"
    assert metric_dimension("vega") == "money"
    assert metric_dimension("delta_cash") == "money"
    assert metric_dimension("one_day_var_proxy") == "money"
    assert metric_dimension("delta") == "shared"
    assert metric_dimension("gamma") == "shared"
    assert metric_dimension("delta_proxy") == "shared"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_metric_dimension_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.risk_currency'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/risk_currency.py
"""Currency-aware risk aggregation: the metric dimension map plus a pure
aggregator that groups money metrics by currency and keeps currency-invariant
metrics in one shared block. No pricing / DB dependency — unit-testable in
isolation."""
from __future__ import annotations

from typing import Any

# Money metrics scale with FX (price-denominated, or a money derivative of a
# dimensionless input). Shared metrics are currency-invariant ratios / underlying
# units (delta = dPrice/dSpot is money/money; gamma is 1/money; delta_proxy is a
# share count) and must NEVER be FX-converted.
MONEY_METRIC_KEYS: frozenset[str] = frozenset({
    "market_value", "gross_notional", "pnl",
    "vega", "theta", "rho", "rho_q",
    "delta_cash", "gamma_cash",
    "one_day_var_proxy",
})
SHARED_METRIC_KEYS: frozenset[str] = frozenset({"delta", "gamma", "delta_proxy"})


def metric_dimension(key: str) -> str:
    if key in MONEY_METRIC_KEYS:
        return "money"
    if key in SHARED_METRIC_KEYS:
        return "shared"
    raise KeyError(f"Unknown risk metric: {key}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_metric_dimension_map.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_currency.py tests/test_metric_dimension_map.py
git commit -m "feat(risk): declarative money/shared metric dimension map"
```

---

### Task C2: Pure currency-aware aggregator

**Files:**
- Modify: `backend/app/services/risk_currency.py`
- Test: `tests/test_currency_aggregation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_currency_aggregation.py
from app.services.risk_currency import build_currency_aware_totals


def _contrib(**kw):
    base = {
        "market_value": 0.0, "gross_notional": 0.0, "pnl": 0.0,
        "vega": 0.0, "theta": 0.0, "rho": 0.0, "rho_q": 0.0,
        "delta_cash": 0.0, "gamma_cash": 0.0, "one_day_var_proxy": 0.0,
        "delta": 0.0, "gamma": 0.0, "delta_proxy": 0.0,
    }
    base.update(kw)
    return base


def test_money_grouped_by_currency_shared_pooled():
    per_position = [
        ("CNY", _contrib(market_value=100.0, delta=2.0, one_day_var_proxy=5.0)),
        ("CNY", _contrib(market_value=50.0, delta=1.0, one_day_var_proxy=1.0)),
        ("USD", _contrib(market_value=10.0, delta=0.5, one_day_var_proxy=0.2)),
    ]
    out = build_currency_aware_totals(per_position)

    assert out["currencies"] == ["CNY", "USD"]
    assert out["by_currency"]["CNY"]["market_value"] == 150.0
    assert out["by_currency"]["CNY"]["one_day_var_proxy"] == 6.0
    assert out["by_currency"]["CNY"]["position_count"] == 2
    assert out["by_currency"]["USD"]["market_value"] == 10.0
    # shared metrics pooled across all currencies, one set:
    assert out["shared"]["delta"] == 3.5
    # mixed currency -> no flat totals
    assert out["mixed_currency"] is True
    assert out["totals"] is None


def test_single_currency_keeps_flat_totals_for_backcompat():
    per_position = [
        ("CNY", _contrib(market_value=100.0, delta=2.0, gross_notional=200.0)),
        ("CNY", _contrib(market_value=50.0, delta=1.0, gross_notional=80.0)),
    ]
    out = build_currency_aware_totals(per_position)
    assert out["mixed_currency"] is False
    # flat totals carry the FULL legacy key set (money + shared)
    assert out["totals"]["market_value"] == 150.0
    assert out["totals"]["gross_notional"] == 280.0
    assert out["totals"]["delta"] == 3.0
    # and equals the by_currency sum for money keys (characterization guard)
    assert out["totals"]["market_value"] == out["by_currency"]["CNY"]["market_value"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_aggregation.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_currency_aware_totals'`.

- [ ] **Step 3: Implement** (append to `backend/app/services/risk_currency.py`)

```python
def _empty_money_bucket() -> dict[str, float]:
    return {key: 0.0 for key in MONEY_METRIC_KEYS} | {"position_count": 0}


def build_currency_aware_totals(
    per_position: list[tuple[str, dict[str, float]]],
) -> dict[str, Any]:
    """Group money metrics by currency, pool shared metrics into one block.

    `per_position` is a list of (currency, contribution) where contribution is a
    totals-shaped dict keyed by the metric names. Returns:
      by_currency: {ccy: {money_key: sum, position_count: n}}
      shared:      {shared_key: sum}      # one set, currency-invariant
      totals:      flat legacy dict | None  # populated only when 1 currency
      mixed_currency: bool
      currencies:  sorted list
    """
    by_currency: dict[str, dict[str, float]] = {}
    shared: dict[str, float] = {key: 0.0 for key in SHARED_METRIC_KEYS}

    for currency, contribution in per_position:
        ccy = currency or "UNKNOWN"
        bucket = by_currency.setdefault(ccy, _empty_money_bucket())
        bucket["position_count"] += 1
        for key in MONEY_METRIC_KEYS:
            bucket[key] += float(contribution.get(key, 0.0) or 0.0)
        for key in SHARED_METRIC_KEYS:
            shared[key] += float(contribution.get(key, 0.0) or 0.0)

    currencies = sorted(by_currency)
    mixed = len(currencies) > 1
    if not mixed and currencies:
        only = currencies[0]
        totals: dict[str, float] | None = {
            key: by_currency[only][key] for key in MONEY_METRIC_KEYS
        } | dict(shared)
    elif not currencies:
        totals = {key: 0.0 for key in MONEY_METRIC_KEYS} | dict(shared)
    else:
        totals = None

    return {
        "by_currency": by_currency,
        "shared": shared,
        "totals": totals,
        "mixed_currency": mixed,
        "currencies": currencies,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_aggregation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_currency.py tests/test_currency_aggregation.py
git commit -m "feat(risk): pure currency-aware aggregator (by_currency + shared + guarded totals)"
```

---

### Task C3: Wire the aggregator into `calculate_portfolio_risk` + tag each row

**Files:**
- Modify: `backend/app/services/quantark.py` (`_risk_row` builder ~line 1260–1283; `_calculate_position_risk` ~line 943; final aggregation in `calculate_portfolio_risk` ~line 1139–1148)
- Test: `tests/test_currency_aggregation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_currency_aggregation.py
def test_calculate_portfolio_risk_emits_by_currency(monkeypatch):
    """End-to-end shape: two positions in different currencies must not cross-sum."""
    from types import SimpleNamespace
    from app.services import quantark

    # Stub per-position pricing so the test is currency-logic only, not pricing.
    def fake_job(job):
        index, snapshot, market = job
        ccy = market.currency
        row = {"currency": ccy, "market_value": 100.0 if ccy == "CNY" else 10.0}
        contribution = quantark._empty_risk_totals()
        contribution["market_value"] = 100.0 if ccy == "CNY" else 10.0
        contribution["delta"] = 1.0
        return (index, row, contribution, 0.0)

    monkeypatch.setattr(quantark, "_calculate_position_risk_job", fake_job)

    from app.schemas import PricingEnvironmentSnapshot
    p_cny = SimpleNamespace(id=1, currency="CNY")
    p_usd = SimpleNamespace(id=2, currency="USD")
    portfolio = SimpleNamespace(positions=[p_cny, p_usd])
    markets = {
        1: PricingEnvironmentSnapshot(currency="CNY"),
        2: PricingEnvironmentSnapshot(currency="USD"),
    }
    result = quantark.calculate_portfolio_risk(portfolio, position_markets=markets)

    assert set(result["by_currency"]) == {"CNY", "USD"}
    assert result["by_currency"]["CNY"]["market_value"] == 100.0
    assert result["by_currency"]["USD"]["market_value"] == 10.0
    assert result["mixed_currency"] is True
    assert result["totals"] is None
    assert result["shared"]["delta"] == 2.0
    assert all("currency" in row for row in result["positions"])
```

(Note: if `_risk_position_snapshot` strips `currency`, also stub it — see Step 3; the fake job above reads `market.currency`, which `market_snapshot_for_position` already sets.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_aggregation.py::test_calculate_portfolio_risk_emits_by_currency -v`
Expected: FAIL — `KeyError: 'by_currency'` (current return only has `totals`/`positions`).

- [ ] **Step 3: Implement**

(a) Ensure each row carries currency. In `_risk_row` (~line 1260), add a `currency` field to the returned dict (the builder receives `position`; read it off the snapshot/market). Simplest: thread the market currency through. In `_calculate_position_risk` (~line 1038 return and the exclusion return ~line 960), pass the position market currency into `_risk_row`. Add a `currency` kwarg to `_risk_row` and include `"currency": currency` in its returned dict. For the snapshot, set currency in `_risk_position_snapshot` (~line 921) by adding:

```python
        currency=getattr(position, "currency", None),
```

and in `_calculate_position_risk`, compute `currency = position_market.currency` and pass `currency=currency` to both `_risk_row(...)` calls.

(b) Replace the final aggregation loop in `calculate_portfolio_risk` (~lines 1139–1148):

```python
    from .risk_currency import build_currency_aware_totals

    per_position: list[tuple[str, dict[str, float]]] = []
    for result in results:
        if result is None:
            continue
        _index, row, contribution, position_var_proxy = result
        rows.append(row)
        contribution = dict(contribution)
        contribution["one_day_var_proxy"] = position_var_proxy
        per_position.append((row.get("currency") or "UNKNOWN", contribution))

    aggregated = build_currency_aware_totals(per_position)
    return {
        "by_currency": aggregated["by_currency"],
        "shared": aggregated["shared"],
        "totals": aggregated["totals"],
        "mixed_currency": aggregated["mixed_currency"],
        "currencies": aggregated["currencies"],
        "positions": rows,
    }
```

(`_empty_risk_totals` must include `one_day_var_proxy` so the contribution dict has the key; add `"one_day_var_proxy": 0.0` to the dict returned by `_empty_risk_totals` at ~line 884.)

- [ ] **Step 4: Run the focused test, then the full quantark/risk suite**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_aggregation.py tests/test_tools_risk.py -v`
Expected: PASS. Then run any existing risk/quantark suite to catch consumers of the old `totals` shape:
Run: `PYTHONPATH=.../backend .../python -m pytest tests/ -k "risk or quantark or report" -q`
Expected: investigate any failure — these are the downstream consumers handled in Task C4.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quantark.py tests/test_currency_aggregation.py
git commit -m "feat(risk): currency-aware calculate_portfolio_risk (by_currency/shared/guarded totals)"
```

---

### Task C4: Update downstream consumers of the risk-output shape

**Files:**
- Modify: `backend/app/services/reports.py` (`_build_report_payload` ~line 215–229)
- Modify: `backend/app/services/risk_engine.py` (`_risk_status_from_metrics` — wherever it reads `metrics["totals"]`)
- Test: `tests/test_currency_aggregation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_currency_aggregation.py
def test_risk_status_handles_mixed_currency_metrics():
    from app.services.risk_engine import _risk_status_from_metrics

    mixed = {"by_currency": {"CNY": {"market_value": 1.0}, "USD": {"market_value": 2.0}},
             "shared": {"delta": 1.0}, "totals": None, "mixed_currency": True,
             "currencies": ["CNY", "USD"], "positions": []}
    # Must not raise on totals=None; returns a valid status string.
    assert isinstance(_risk_status_from_metrics(mixed), str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_currency_aggregation.py::test_risk_status_handles_mixed_currency_metrics -v`
Expected: FAIL — `TypeError`/`AttributeError` from `None.get(...)` (current code assumes a dict `totals`).

- [ ] **Step 3: Implement**

In `backend/app/services/risk_engine.py`, make `_risk_status_from_metrics` tolerate `totals is None` by falling back to per-currency presence:

```python
def _risk_status_from_metrics(metrics: dict) -> str:
    totals = metrics.get("totals")
    if totals is None:
        # mixed-currency: status from whether any currency bucket has positions
        has_rows = bool(metrics.get("by_currency"))
        return "completed" if has_rows else "empty"
    # ... existing logic unchanged ...
```

(Preserve the existing branch for the dict case; only add the `totals is None` guard at the top.)

`reports.py::_build_report_payload` already returns `{"risk": risk}` verbatim, so the new shape flows through unchanged — no edit needed there beyond confirming the report renderer (if any) reads `risk["by_currency"]`. Add a comment noting the new keys for the reader.

- [ ] **Step 4: Run test to verify it passes + re-run the broad suite**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/ -k "risk or quantark or report" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/risk_engine.py backend/app/services/reports.py tests/test_currency_aggregation.py
git commit -m "fix(risk): downstream consumers tolerate by_currency/null-totals shape"
```

---

## Phase D — FX resolver + convert tool

### Task D1: `fx_rate_as_of` resolver

**Files:**
- Create: `backend/app/services/fx.py`
- Test: `tests/test_fx_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fx_resolver.py
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed(session, base, quote, rate, day):
    from app.models import FxRate
    session.add(FxRate(base_currency=base, quote_currency=quote, rate=rate,
                       as_of_date=datetime(2026, 6, day), source="manual"))


def test_latest_on_or_before_valuation_date():
    from app import database
    from app.services.fx import fx_rate_as_of

    with database.SessionLocal() as session:
        _seed(session, "USD", "CNY", 7.0, 1)
        _seed(session, "USD", "CNY", 7.2, 3)
        _seed(session, "USD", "CNY", 7.5, 10)  # after valuation -> ignored
        session.commit()
        assert fx_rate_as_of(session, "USD", "CNY", datetime(2026, 6, 5)) == 7.2


def test_identity_and_inverse_and_missing():
    from app import database
    from app.services.fx import fx_rate_as_of

    with database.SessionLocal() as session:
        _seed(session, "USD", "CNY", 8.0, 1)
        session.commit()
        assert fx_rate_as_of(session, "USD", "USD", datetime(2026, 6, 5)) == 1.0
        assert fx_rate_as_of(session, "CNY", "USD", datetime(2026, 6, 5)) == pytest.approx(1 / 8.0)
        assert fx_rate_as_of(session, "JPY", "USD", datetime(2026, 6, 5)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.fx'`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/fx.py
"""FX rates: reproducible point-in-time conversion. The resolver picks the latest
rate with as_of_date <= valuation_date, deriving identity and inverse pairs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FxRate


def _direct_rate(session: Session, base: str, quote: str, as_of: datetime) -> float | None:
    stmt = (
        select(FxRate.rate)
        .where(
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
            FxRate.as_of_date <= as_of,
        )
        .order_by(FxRate.as_of_date.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def fx_rate_as_of(
    session: Session, base: str, quote: str, as_of: datetime
) -> float | None:
    """1 unit of `base` in units of `quote`, as of `as_of` (latest <= as_of).
    Returns 1.0 for identity, 1/rate for the inverse pair, None if unknown."""
    if base == quote:
        return 1.0
    direct = _direct_rate(session, base, quote, as_of)
    if direct is not None:
        return float(direct)
    inverse = _direct_rate(session, quote, base, as_of)
    if inverse:
        return 1.0 / float(inverse)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fx.py tests/test_fx_resolver.py
git commit -m "feat(fx): fx_rate_as_of resolver (latest<=date, identity, inverse)"
```

---

### Task D2: Pure `convert_risk_currency`

**Files:**
- Modify: `backend/app/services/fx.py`
- Test: `tests/test_convert_currency.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_convert_currency.py
from app.services.fx import convert_risk_currency


def _rate_lookup(base, quote):
    table = {("CNY", "USD"): 0.14, ("USD", "USD"): 1.0}
    return table.get((base, quote))


def test_converts_money_sums_into_target_and_lists_missing():
    by_currency = {
        "CNY": {"market_value": 100.0, "vega": 10.0, "position_count": 2},
        "USD": {"market_value": 5.0, "vega": 1.0, "position_count": 1},
        "JPY": {"market_value": 9.0, "vega": 0.0, "position_count": 1},  # no rate
    }
    out = convert_risk_currency(by_currency, "USD", _rate_lookup)
    # CNY converted (100*0.14) + USD as-is (5) = 19.0
    assert out["totals"]["market_value"] == 19.0
    assert out["totals"]["vega"] == 10.0 * 0.14 + 1.0
    assert out["fx_rates_used"]["CNY->USD"] == 0.14
    assert out["missing"] == ["JPY->USD"]
    # position_count is summed straight (not FX'd)
    assert out["totals"]["position_count"] == 3  # CNY(2)+USD(1); JPY excluded (missing)


def test_identity_only():
    by_currency = {"USD": {"market_value": 7.0, "position_count": 1}}
    out = convert_risk_currency(by_currency, "USD", _rate_lookup)
    assert out["totals"]["market_value"] == 7.0
    assert out["missing"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py -v`
Expected: FAIL — `ImportError: cannot import name 'convert_risk_currency'`.

- [ ] **Step 3: Implement** (append to `backend/app/services/fx.py`)

```python
from typing import Any, Callable

from app.services.risk_currency import MONEY_METRIC_KEYS


def convert_risk_currency(
    by_currency: dict[str, dict[str, float]],
    target_currency: str,
    rate_lookup: Callable[[str, str], float | None],
) -> dict[str, Any]:
    """Collapse the per-currency money buckets into `target_currency`.

    rate_lookup(base, quote) -> float | None. Pure: no DB here. Currencies whose
    rate is unknown are skipped and reported in `missing`. Money metrics are
    FX-scaled; position_count is summed straight."""
    totals: dict[str, float] = {key: 0.0 for key in MONEY_METRIC_KEYS}
    totals["position_count"] = 0.0
    fx_rates_used: dict[str, float] = {}
    missing: list[str] = []

    for ccy, bucket in by_currency.items():
        rate = rate_lookup(ccy, target_currency)
        if rate is None:
            missing.append(f"{ccy}->{target_currency}")
            continue
        if ccy != target_currency:
            fx_rates_used[f"{ccy}->{target_currency}"] = rate
        for key in MONEY_METRIC_KEYS:
            totals[key] += float(bucket.get(key, 0.0) or 0.0) * rate
        totals["position_count"] += float(bucket.get("position_count", 0) or 0)

    return {
        "totals": totals,
        "fx_rates_used": fx_rates_used,
        "missing": missing,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fx.py tests/test_convert_currency.py
git commit -m "feat(fx): pure convert_risk_currency (FX-scale money, report missing pairs)"
```

---

### Task D3: `convert_currency` agent tool + registration

**Files:**
- Modify: `backend/app/tools/risk.py` (add tool); `backend/app/tools/__init__.py` (import + add to `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/deep_agent/task_registry.py` (add `"convert_currency"` to relevant task `tools_scope`)
- Test: `tests/test_convert_currency.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_convert_currency.py
import pytest


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_convert_currency_tool_uses_db_rates(_db):
    from datetime import datetime
    from app import database
    from app.models import FxRate
    from app.tools.risk import convert_currency_tool

    with database.SessionLocal() as session:
        session.add(FxRate(base_currency="CNY", quote_currency="USD", rate=0.14,
                           as_of_date=datetime(2026, 6, 1), source="manual"))
        session.commit()

    result = convert_currency_tool.invoke({
        "by_currency": {"CNY": {"market_value": 100.0, "position_count": 1}},
        "target_currency": "USD",
        "valuation_date": "2026-06-02",
    })
    assert result["totals"]["market_value"] == pytest.approx(14.0)
    assert result["missing"] == []


def test_convert_currency_in_tool_list():
    from app.tools import QUANT_AGENT_TOOLS
    names = {getattr(t, "name", None) for t in QUANT_AGENT_TOOLS}
    assert "convert_currency" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py -v`
Expected: FAIL — `ImportError: cannot import name 'convert_currency_tool'`.

- [ ] **Step 3: Implement**

In `backend/app/tools/risk.py`, add (mirroring the existing `@capability_gated`/`@tool` pattern):

```python
from datetime import datetime

from app.services import fx as fx_svc
from app.services.fx import fx_rate_as_of
from app import database


class ConvertCurrencyInput(BaseModel):
    by_currency: dict[str, dict[str, float]] = Field(
        description="The by_currency block from a risk run (money metrics per currency)."
    )
    target_currency: str = Field(description="ISO code to convert into, e.g. 'USD'.")
    valuation_date: str = Field(
        description="ISO date (YYYY-MM-DD) used to resolve FX rates reproducibly."
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("convert_currency", args_schema=ConvertCurrencyInput)
def convert_currency_tool(
    by_currency: dict[str, dict[str, float]],
    target_currency: str,
    valuation_date: str,
) -> dict[str, Any]:
    """Deterministically FX-convert a risk run's per-currency money metrics into a
    single target currency, using valuation-snapshot rates (latest <= valuation_date).
    Never fabricates a rate: unconvertible currencies are returned in `missing`."""
    as_of = datetime.fromisoformat(valuation_date)
    with database.SessionLocal() as session:
        def _lookup(base: str, quote: str) -> float | None:
            return fx_rate_as_of(session, base, quote, as_of)

        result = fx_svc.convert_risk_currency(by_currency, target_currency, _lookup)
    result["as_of"] = valuation_date
    return result
```

In `backend/app/tools/__init__.py`: add `convert_currency_tool` to the `from .risk import (...)` block and to the `QUANT_AGENT_TOOLS` list (next to `calculate_risk_tool`).

In `backend/app/services/deep_agent/task_registry.py`: add `"convert_currency"` to the `tools_scope` tuple of the risk/reporting task registrations that produce or narrate risk (e.g. any task assigned to `risk_manager` that already includes `calculate_risk`/risk-run tools). Grep for `calculate_risk` / risk task entries and append `"convert_currency"` to those `tools_scope` tuples.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_convert_currency.py -v`
Expected: PASS.

> **Catalog coupling:** adding a tool/scope may trip exact-set assertions in deep-agent catalog tests. Run `pytest tests/ -k "task_registry or tool_scope or catalog" -q` and update the expected sets if present.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/risk.py backend/app/tools/__init__.py backend/app/services/deep_agent/task_registry.py tests/test_convert_currency.py
git commit -m "feat(tool): convert_currency agent tool + scope registration"
```

---

## Phase E — report_currency plumbing

### Task E1: Inject `report_currency` into the ContextPack payload

**Files:**
- Modify: `backend/app/services/deep_agent/context_assembler.py` (`_context_pack_payload` ~line 100–127; `assemble_context_pack` ~line 130 to source the thread's value)
- Test: `tests/test_report_currency_plumbing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_currency_plumbing.py
from app.services.deep_agent.context_assembler import _context_pack_payload
from app.services.deep_agent.task_registry import TaskSpec


def _spec():
    return TaskSpec(task_type="fetch_position_summaries", inputs={},
                    depends_on=[], assigned_persona="risk_manager")


class _WF:
    canonical_snapshot_ids = {}


def test_report_currency_changes_content_hash():
    h_native, payload_native = _context_pack_payload(
        task_spec=_spec(), workflow=_WF(), artifact_ids=[], recent_summary="",
        report_currency="by_position",
    )
    h_usd, payload_usd = _context_pack_payload(
        task_spec=_spec(), workflow=_WF(), artifact_ids=[], recent_summary="",
        report_currency="USD",
    )
    assert payload_native["report_currency"] == "by_position"
    assert payload_usd["report_currency"] == "USD"
    assert h_native != h_usd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py -v`
Expected: FAIL — `_context_pack_payload() got an unexpected keyword argument 'report_currency'`.

- [ ] **Step 3: Implement**

In `_context_pack_payload`, add a `report_currency: str` keyword param and include it in `stable_payload`:

```python
def _context_pack_payload(
    *,
    task_spec: TaskSpec,
    workflow: Workflow,
    artifact_ids: list[int],
    recent_summary: str,
    report_currency: str,
) -> tuple[str, dict[str, Any]]:
    ...
    stable_payload = {
        ...
        "model_id": current_model_id(),
        "report_currency": report_currency,
    }
    return _canonical_hash(stable_payload), stable_payload
```

In `assemble_context_pack`, resolve the thread's value and pass it through. The workflow has `thread_id`; load it:

```python
    from app.models import AgentThread
    thread = session.get(AgentThread, workflow.thread_id)
    report_currency = getattr(thread, "report_currency", "by_position") or "by_position"
    content_hash, stable_payload = _context_pack_payload(
        task_spec=task_spec,
        workflow=workflow,
        artifact_ids=artifact_ids,
        recent_summary=summary_text,
        report_currency=report_currency,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/context_assembler.py tests/test_report_currency_plumbing.py
git commit -m "feat(agent): thread report_currency flows into ContextPack stable_payload"
```

---

### Task E2: Persona prompt instruction block

**Files:**
- Modify: `backend/app/services/deep_agent/personas.py` (or the prompt assembler it calls — grep for where `stable_payload` / persona prompt is composed)
- Test: `tests/test_report_currency_plumbing.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_report_currency_plumbing.py
from app.services.deep_agent.personas import report_currency_instruction


def test_instruction_iso_vs_by_position():
    iso = report_currency_instruction("USD")
    assert "USD" in iso
    assert "convert_currency" in iso

    native = report_currency_instruction("by_position")
    assert "convert_currency" not in native
    assert "separately" in native.lower() or "do not" in native.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py::test_instruction_iso_vs_by_position -v`
Expected: FAIL — `ImportError: cannot import name 'report_currency_instruction'`.

- [ ] **Step 3: Implement**

Add to `backend/app/services/deep_agent/personas.py`:

```python
def report_currency_instruction(report_currency: str) -> str:
    """Declarative instruction block telling a persona how to treat report currency."""
    if report_currency and report_currency != "by_position":
        return (
            f"REPORT CURRENCY: All monetary risk is reported in {report_currency}. "
            f"When you present aggregate money figures from a risk run's `by_currency` "
            f"block, first call the `convert_currency` tool with that block, the target "
            f"`{report_currency}`, and the run's valuation date. Never sum money metrics "
            f"across currencies yourself; the `shared` block (delta/gamma) is "
            f"currency-invariant and needs no conversion."
        )
    return (
        "REPORT CURRENCY: by position. Present each currency group from a risk run's "
        "`by_currency` block separately. Do NOT convert or cross-sum monetary metrics "
        "across currencies."
    )
```

Then compose this into the persona system prompt where the persona prompt is built from the ContextPack (grep `report_currency` / persona prompt assembly; append `report_currency_instruction(stable_payload["report_currency"])` to the system message). Add a one-line test-or-comment anchor where it's injected.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py -v`
Expected: PASS.

> **Prompt-revision coupling:** `persona_prompt_revision_hash` may be asserted in tests. Run `pytest tests/ -k "persona or prompt" -q` and update any pinned hash/snapshot.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/personas.py tests/test_report_currency_plumbing.py
git commit -m "feat(agent): report-currency persona instruction block"
```

---

### Task E3: `AgentThreadOut.report_currency` + PATCH endpoint + default seed

**Files:**
- Modify: `backend/app/schemas.py` (`AgentThreadOut` ~line 200; add `ThreadUpdate`)
- Modify: `backend/app/main.py` (thread create — seed default from portfolio base_currency; add `PATCH /api/chat/threads/{id}`)
- Test: `tests/test_report_currency_plumbing.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_report_currency_plumbing.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.main import create_app

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return TestClient(create_app())


def test_thread_report_currency_patch(client):
    from app import database
    from app.models import AgentThread
    with database.SessionLocal() as session:
        t = AgentThread(title="T", character="trader")
        session.add(t)
        session.commit()
        tid = t.id

    got = client.get("/api/chat/threads").json()
    assert any(row["report_currency"] == "by_position" for row in got)

    resp = client.patch(f"/api/chat/threads/{tid}", json={"report_currency": "USD"})
    assert resp.status_code == 200
    assert resp.json()["report_currency"] == "USD"
```

(If `create_app` is named differently, grep `def create_app` / `app = FastAPI` in `main.py` and adjust.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py -k patch -v`
Expected: FAIL — `KeyError: 'report_currency'` and/or 405 on PATCH.

- [ ] **Step 3: Implement**

In `schemas.py`, add `report_currency: str = "by_position"` to `AgentThreadOut`, and a new model:

```python
class ThreadUpdate(BaseModel):
    report_currency: str | None = None
```

In `main.py`, add the endpoint near the other `/api/chat/threads` routes:

```python
    @app.patch("/api/chat/threads/{thread_id}", response_model=AgentThreadOut)
    def update_thread(thread_id: int, payload: ThreadUpdate, session: Session = Depends(get_db)):
        thread = session.get(AgentThread, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        if payload.report_currency is not None:
            thread.report_currency = payload.report_currency
        session.commit()
        session.refresh(thread)
        return thread
```

(Import `ThreadUpdate` in `main.py`.) For default seeding: where threads are created, if a portfolio context is available set `report_currency` from its `base_currency`; otherwise the column default `by_position` applies — no extra code needed for the no-portfolio path.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_report_currency_plumbing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_report_currency_plumbing.py
git commit -m "feat(api): expose + patch thread report_currency"
```

---

## Phase F — FX rates on the Market Data page

### Task F1: FxRate CRUD + akshare service + schemas

**Files:**
- Modify: `backend/app/services/fx.py` (add `list_fx_rates`, `create_fx_rate`, `delete_fx_rate`, `fetch_akshare_fx_rate`)
- Modify: `backend/app/schemas.py` (`FxRateOut`, `FxRateCreate`, `FxRateAkshareRequest`)
- Test: `tests/test_fx_rates_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fx_rates_api.py
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_create_list_delete_fx_rate():
    from app import database
    from app.schemas import FxRateCreate
    from app.services import fx as fx_svc

    with database.SessionLocal() as session:
        created = fx_svc.create_fx_rate(session, FxRateCreate(
            base_currency="USD", quote_currency="CNY", rate=7.2,
            as_of_date=datetime(2026, 6, 2), source="manual",
        ))
        session.commit()
        rid = created.id
        rows = fx_svc.list_fx_rates(session)
        assert len(rows) == 1 and rows[0].rate == 7.2
        fx_svc.delete_fx_rate(session, rid)
        session.commit()
        assert fx_svc.list_fx_rates(session) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_rates_api.py -v`
Expected: FAIL — `AttributeError: module 'app.services.fx' has no attribute 'create_fx_rate'`.

- [ ] **Step 3: Implement**

In `schemas.py`:

```python
class FxRateCreate(BaseModel):
    base_currency: str
    quote_currency: str
    rate: float
    as_of_date: datetime
    source: str = "manual"
    pricing_parameter_profile_id: int | None = None


class FxRateOut(BaseModel):
    id: int
    base_currency: str
    quote_currency: str
    rate: float
    as_of_date: datetime
    source: str
    pricing_parameter_profile_id: int | None = None

    model_config = {"from_attributes": True}


class FxRateAkshareRequest(BaseModel):
    base_currency: str
    quote_currency: str
    as_of_date: datetime | None = None
```

Append to `backend/app/services/fx.py`:

```python
def list_fx_rates(session: Session) -> list[FxRate]:
    stmt = select(FxRate).order_by(FxRate.as_of_date.desc(), FxRate.id.desc())
    return list(session.execute(stmt).scalars().all())


def create_fx_rate(session: Session, payload) -> FxRate:
    row = FxRate(
        base_currency=payload.base_currency,
        quote_currency=payload.quote_currency,
        rate=float(payload.rate),
        as_of_date=payload.as_of_date,
        source=getattr(payload, "source", "manual"),
        pricing_parameter_profile_id=getattr(payload, "pricing_parameter_profile_id", None),
    )
    session.add(row)
    session.flush()
    return row


def delete_fx_rate(session: Session, fx_rate_id: int) -> None:
    row = session.get(FxRate, fx_rate_id)
    if row is not None:
        session.delete(row)


def fetch_akshare_fx_rate(base_currency: str, quote_currency: str) -> float:
    """Fetch a spot FX rate via akshare's forex feed. Returns 1 base = N quote.
    (Thin wrapper; the akshare symbol convention is e.g. 'USDCNY'.)"""
    import akshare as ak

    symbol = f"{base_currency}{quote_currency}"
    df = ak.forex_spot_quote()  # columns include 货币对 / 最新价 (adjust to live schema)
    match = df[df["货币对"].astype(str).str.replace("/", "") == symbol]
    if match.empty:
        raise ValueError(f"No akshare FX quote for {symbol}")
    return float(match.iloc[0]["最新价"])
```

(The akshare schema may differ at runtime; the engineer should confirm the actual `ak` forex function/columns and adjust. Keep the `(base, quote) -> float` contract.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_rates_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fx.py backend/app/schemas.py tests/test_fx_rates_api.py
git commit -m "feat(fx): FxRate CRUD service + schemas + akshare fetch"
```

---

### Task F2: FxRate API endpoints (list/create/akshare/delete) with audit

**Files:**
- Modify: `backend/app/main.py` (near the `/api/market-data/profiles` routes ~line 2923)
- Test: `tests/test_fx_rates_api.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_fx_rates_api.py
from fastapi.testclient import TestClient


def _client():
    from app.main import create_app
    return TestClient(create_app())


def test_fx_rate_endpoints():
    client = _client()
    resp = client.post("/api/market-data/fx-rates", json={
        "base_currency": "USD", "quote_currency": "CNY", "rate": 7.1,
        "as_of_date": "2026-06-02T00:00:00", "source": "manual",
    })
    assert resp.status_code == 200
    rid = resp.json()["id"]

    listing = client.get("/api/market-data/fx-rates").json()
    assert any(r["id"] == rid and r["rate"] == 7.1 for r in listing)

    assert client.delete(f"/api/market-data/fx-rates/{rid}").status_code == 200
    assert all(r["id"] != rid for r in client.get("/api/market-data/fx-rates").json())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_rates_api.py -k endpoints -v`
Expected: FAIL — 404/405 (routes not registered).

- [ ] **Step 3: Implement** (in `main.py`, mirroring the profile routes + `record_audit`)

```python
    @app.get("/api/market-data/fx-rates", response_model=list[FxRateOut])
    def list_fx_rates_endpoint(session: Session = Depends(get_db)):
        return fx_service.list_fx_rates(session)

    @app.post("/api/market-data/fx-rates", response_model=FxRateOut)
    def create_fx_rate_endpoint(payload: FxRateCreate, session: Session = Depends(get_db)):
        row = fx_service.create_fx_rate(session, payload)
        record_audit(session, event_type="market_data.fx_rate.manual", actor="desk_user",
                     subject_type="fx_rate", subject_id=row.id,
                     payload={"pair": f"{row.base_currency}{row.quote_currency}", "rate": row.rate})
        session.commit()
        session.refresh(row)
        return row

    @app.post("/api/market-data/fx-rates/akshare", response_model=FxRateOut)
    def create_fx_rate_akshare_endpoint(payload: FxRateAkshareRequest, session: Session = Depends(get_db)):
        from datetime import datetime as _dt
        rate = fx_service.fetch_akshare_fx_rate(payload.base_currency, payload.quote_currency)
        row = fx_service.create_fx_rate(session, FxRateCreate(
            base_currency=payload.base_currency, quote_currency=payload.quote_currency,
            rate=rate, as_of_date=payload.as_of_date or _dt.utcnow(), source="akshare"))
        record_audit(session, event_type="market_data.fx_rate.akshare", actor="desk_user",
                     subject_type="fx_rate", subject_id=row.id,
                     payload={"pair": f"{row.base_currency}{row.quote_currency}", "rate": rate})
        session.commit()
        session.refresh(row)
        return row

    @app.delete("/api/market-data/fx-rates/{fx_rate_id}")
    def delete_fx_rate_endpoint(fx_rate_id: int, session: Session = Depends(get_db)):
        fx_service.delete_fx_rate(session, fx_rate_id)
        record_audit(session, event_type="market_data.fx_rate.delete", actor="desk_user",
                     subject_type="fx_rate", subject_id=fx_rate_id, payload={})
        session.commit()
        return {"ok": True}
```

Add imports at the top of `main.py`: `from app.services import fx as fx_service` and `FxRateOut, FxRateCreate, FxRateAkshareRequest` to the schemas import.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/test_fx_rates_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_fx_rates_api.py
git commit -m "feat(api): /api/market-data/fx-rates CRUD + akshare + audit"
```

---

### Task F3: Frontend FX Rates tab

**Files:**
- Modify: `frontend/src/types.ts` (add `FxRate` type)
- Modify: `frontend/src/api/client.ts` (add `listFxRates`, `createFxRate`, `fetchFxRateAkshare`, `deleteFxRate`)
- Modify: `frontend/src/routes/MarketData.tsx` (`Tab` union line 28; `activeTab` line 94; tab map line 568; add panel after the `describe` panel ~line 652)
- Test: `frontend/src/routes/MarketData.test.tsx` (append)

Frontend test command:
```bash
cd /Users/fuxinyao/open-otc-trading-currency/frontend && npm test -- MarketData
```

- [ ] **Step 1: Write the failing test**

```tsx
// append to frontend/src/routes/MarketData.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

describe('MarketData FX Rates tab', () => {
  it('renders the FX Rates tab and lists rates', async () => {
    // Mock the client module's fx methods (match the existing test's mocking style).
    vi.mock('../api/client', async (orig) => ({
      ...(await orig<typeof import('../api/client')>()),
      listFxRates: vi.fn().mockResolvedValue([
        { id: 1, base_currency: 'USD', quote_currency: 'CNY', rate: 7.2,
          as_of_date: '2026-06-02T00:00:00', source: 'manual' },
      ]),
    }));
    const { default: MarketData } = await import('./MarketData');
    render(<MarketData />);
    fireEvent.click(await screen.findByRole('tab', { name: /fx rates/i }));
    await waitFor(() => expect(screen.getByText('USD')).toBeInTheDocument());
    expect(screen.getByText('7.2')).toBeInTheDocument();
  });
});
```

(Align the mock/render harness with the existing `MarketData.test.tsx` setup — reuse its provider wrappers and prop shape rather than the bare `render` above if the page requires props.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- MarketData`
Expected: FAIL — no `fx rates` tab / `listFxRates` undefined.

- [ ] **Step 3: Implement**

`frontend/src/types.ts`:

```ts
export interface FxRate {
  id: number;
  base_currency: string;
  quote_currency: string;
  rate: number;
  as_of_date: string;
  source: string;
  pricing_parameter_profile_id?: number | null;
}
```

`frontend/src/api/client.ts`:

```ts
import type { FxRate } from '../types';

export const listFxRates = () => api<FxRate[]>('/api/market-data/fx-rates');
export const createFxRate = (body: Omit<FxRate, 'id'>) =>
  api<FxRate>('/api/market-data/fx-rates', { method: 'POST', body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' } });
export const fetchFxRateAkshare = (base: string, quote: string) =>
  api<FxRate>('/api/market-data/fx-rates/akshare', { method: 'POST',
    body: JSON.stringify({ base_currency: base, quote_currency: quote }),
    headers: { 'Content-Type': 'application/json' } });
export const deleteFxRate = (id: number) =>
  api<{ ok: boolean }>(`/api/market-data/fx-rates/${id}`, { method: 'DELETE' });
```

`frontend/src/routes/MarketData.tsx`:
- Line 28: `type Tab = 'historical' | 'describe' | 'fx';`
- Add state for rates: `const [fxRates, setFxRates] = useState<FxRate[]>([]);` and a `useEffect`/loader calling `listFxRates()` when `activeTab === 'fx'`.
- Line 568 tab map: change `(['historical', 'describe'] as Tab[])` → `(['historical', 'describe', 'fx'] as Tab[])` and map a label (`fx` → "FX Rates") in the button text.
- After the `describe` panel (~line 652), add:

```tsx
{activeTab === 'fx' && (
  <div className="wl-market-data__fx">
    <Table
      columns={[
        { key: 'base_currency', header: 'Base' },
        { key: 'quote_currency', header: 'Quote' },
        { key: 'rate', header: 'Rate' },
        { key: 'as_of_date', header: 'As of' },
        { key: 'source', header: 'Source' },
      ] as Column<FxRate>[]}
      rows={fxRates}
    />
    {/* add-rate Modal form (base/quote/rate/as_of) calling createFxRate, and a
        "Fetch from AKShare" button calling fetchFxRateAkshare, then reload list */}
  </div>
)}
```

Wire an add-rate `Modal` (reuse the page's existing `Modal`/`Button` imports) submitting `createFxRate` and a "Fetch from AKShare" button calling `fetchFxRateAkshare`, each followed by a reload via `listFxRates()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- MarketData`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/routes/MarketData.tsx frontend/src/routes/MarketData.test.tsx
git commit -m "feat(ui): FX Rates tab on the Market Data page"
```

---

## Phase G — Full-suite verification

### Task G1: Run the entire suite + reconcile defaults

**Files:**
- Modify: `backend/app/models.py` (align `Product.currency` default if reconciling — see spec §1d, open item 1)
- Test: full suite

- [ ] **Step 1: Run the full backend suite**

Run: `PYTHONPATH=.../backend .../python -m pytest tests/ -q`
Expected: all pass. Triage failures by category: (a) catalog/scope exact-set tests (update expected sets for `convert_currency`), (b) persona/prompt hash snapshots (re-pin), (c) any remaining consumer of the old flat `totals`.

- [ ] **Step 2: Run the frontend suite**

Run: `cd frontend && npm test`
Expected: all pass.

- [ ] **Step 3: Decide the aligned default currency (spec open item 1)**

Confirm with the user whether to align `Product.currency`/`Underlying.currency`/`Position.currency` defaults to `CNY`. If yes, change `Product.currency` `default`/`server_default` to `"CNY"` in `models.py` and add an idempotent column-default note in migration 0020 (no data rewrite of existing rows). Re-run affected tests (`pytest tests/ -k currency or underlying -q`).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: full-suite green for currency convention; reconcile currency defaults"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §1a Position.currency → A1, A2, B1, B2 ✓
- §1b FxRate table + resolver → A1, A2, D1 ✓
- §1c AgentThread.report_currency → A1, A2, E1, E3 ✓
- §1d default reconciliation → G1 step 3 ✓ (deferred decision, flagged)
- §2 currency-aware aggregation + by_currency breakdown → C1, C2, C3 ✓
- §2 downstream consumers → C4 ✓
- §3 convert_currency tool → D2, D3 ✓
- §4 report-currency plumbing → E1, E2, E3 ✓
- §5 Market Data FX (CRUD+akshare+UI) → F1, F2, F3 ✓
- §6 error handling (missing rate / mismatch warn / null totals / identity-inverse) → D1, D2, C2, B1 ✓
- §7 testing strategy → every task is TDD; characterization guard in C2 ✓

**Placeholder scan:** Two intentional engineer-judgment notes remain (akshare forex schema in F1; persona-prompt injection anchor in E2) — both have a concrete contract + grep anchor, not blank TODOs. Frontend F3 add-form is described with the exact components to reuse.

**Type consistency:** `build_currency_aware_totals(per_position: list[tuple[str, dict]])`, `convert_risk_currency(by_currency, target, rate_lookup)`, `fx_rate_as_of(session, base, quote, as_of)`, `MONEY_METRIC_KEYS`/`SHARED_METRIC_KEYS`, and risk-output keys (`by_currency`/`shared`/`totals`/`mixed_currency`/`currencies`/`positions`) are used identically across tasks.
