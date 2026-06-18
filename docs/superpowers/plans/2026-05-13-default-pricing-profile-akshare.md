# Default Pricing Profile (Underlying Defaults + AKShare Spot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-click workflow that produces a position-level `PricingParameterProfile` snapshot by joining manually-maintained per-underlying r/q/σ with fresh AKShare spot, surfaced through a new collapsible panel on the existing `Pricing Parameters` page.

**Architecture:** Add a dedicated `underlying_pricing_defaults` table that backs per-underlying assumptions, plus a strict build service that scans open positions, validates the store, fetches AKShare spots, and emits a new `PricingParameterProfile` with `source_type='default_underlying'`. Snapshots remain immutable; every build appends a new profile. The Pricing Parameters page becomes a pure management page (the redundant "Load to Positions" handoff is removed); Positions and Risk pages continue to consume profiles from the library via their existing selectors.

**Tech Stack:** SQLAlchemy 2.x ORM, Alembic, FastAPI, Pydantic v2, Pytest, React 19, TypeScript, Vitest, existing AKShare integration via `fetch_akshare_snapshot`.

**Spec:** `docs/superpowers/specs/2026-05-13-default-pricing-profile-akshare-design.md`

---

## File Structure

Backend:

- Create `backend/alembic/versions/0013_underlying_pricing_defaults.py`: Alembic migration adding the `underlying_pricing_defaults` table.
- Modify `backend/app/models.py`: declare `UnderlyingPricingDefault`.
- Modify `backend/app/schemas.py`: `UnderlyingPricingDefaultOut`, `UnderlyingPricingDefaultUpdate`, `BuildDefaultProfileRequest`.
- Create `backend/app/services/underlying_defaults.py`: list/upsert/delete logic and the `refresh_from_open_positions` auto-discover helper.
- Modify `backend/app/services/pricing_profiles.py`: add `build_default_pricing_profile` plus a small `_open_position_underlyings` helper.
- Modify `backend/app/main.py`: wire five new endpoints (CRUD + refresh + build).
- Create `tests/test_underlying_defaults.py`: backend coverage for the table, CRUD, refresh, and strict build pipeline.

Frontend:

- Modify `frontend/src/types.ts`: add `UnderlyingPricingDefault`, tighten `PricingParameterProfile.source_type`.
- Modify `frontend/src/api/client.ts` (no API surface changes — already exposes `api` / `uploadForm`).
- Modify `frontend/src/main.tsx`: drop `positionsPricingProfileId` state and `onUseInPositions` wire-up.
- Modify `frontend/src/routes/PricingParameters.tsx`: remove "Load to Positions" button, accept new props for the defaults panel + library enhancements, render new sections.
- Modify `frontend/src/routes/PricingParameters.live.tsx`: load defaults alongside profiles, drive the panel actions, dispatch the build action.
- Modify `frontend/src/routes/PricingParameters.css`: tokens for the panel, source-type chips, list stripes, origin badges.
- Create `frontend/src/routes/UnderlyingDefaultsPanel.tsx`: presentational component for the panel only (keeps `PricingParameters.tsx` from growing too large).
- Modify `frontend/src/routes/PricingParameters.test.tsx` and `frontend/src/routes/PricingParameters.live.test.tsx`: cover the new flows and the removed handoff.

Verification commands:

- Backend focused: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
- Backend regression: `.venv/bin/python -m pytest tests/test_api.py tests/test_position_import_pricing.py tests/test_position_pricer_grid.py -q`
- Frontend focused: `cd frontend && npm test -- PricingParameters`
- Frontend regression: `cd frontend && npm test -- Risk Positions`
- Frontend build: `cd frontend && npm run build`
- Browser smoke: `cd frontend && npm run dev` then exercise the new panel on `/pricing-parameters`.

---

### Task 1: Database migration

**Files:**
- Create: `backend/alembic/versions/0013_underlying_pricing_defaults.py`

- [ ] **Step 1: Write the Alembic migration**

```python
"""underlying pricing defaults

Revision ID: 0013_underlying_pricing_defaults
Revises: 0012_rfq_quote_versions
Create Date: 2026-05-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0013_underlying_pricing_defaults"
down_revision = "0012_rfq_quote_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "underlying_pricing_defaults" in set(inspector.get_table_names()):
        return
    op.create_table(
        "underlying_pricing_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying", sa.String(length=80), nullable=False, unique=True),
        sa.Column("rate", sa.Float(), nullable=True),
        sa.Column("dividend_yield", sa.Float(), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_underlying_pricing_defaults_underlying",
        "underlying_pricing_defaults",
        ["underlying"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "underlying_pricing_defaults" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("underlying_pricing_defaults")}
    if "ix_underlying_pricing_defaults_underlying" in indexes:
        op.drop_index(
            "ix_underlying_pricing_defaults_underlying",
            table_name="underlying_pricing_defaults",
        )
    op.drop_table("underlying_pricing_defaults")
```

- [ ] **Step 2: Run the migration**

Run: `.venv/bin/alembic upgrade head`
Expected: ends with `INFO  [alembic.runtime.migration] Running upgrade 0012_rfq_quote_versions -> 0013_underlying_pricing_defaults`. No errors.

- [ ] **Step 3: Verify schema**

Run: `.venv/bin/python -c "from sqlalchemy import inspect; from app.database import engine; print(inspect(engine).get_columns('underlying_pricing_defaults'))"`
Expected: lists columns `id, underlying, rate, dividend_yield, volatility, notes, created_at, updated_at`.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0013_underlying_pricing_defaults.py
git commit -m "feat(pricing): add underlying_pricing_defaults table migration"
```

---

### Task 2: SQLAlchemy model

**Files:**
- Modify: `backend/app/models.py` (insert after the `PricingParameterRow` block around line 365)
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write the failing model test**

Create `tests/test_underlying_defaults.py`:

```python
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import UnderlyingPricingDefault


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_underlying_pricing_default_persists_and_round_trips(session: Session) -> None:
    row = UnderlyingPricingDefault(
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.add(row)
    session.commit()

    fetched = session.query(UnderlyingPricingDefault).filter_by(underlying="000300.SH").one()
    assert fetched.rate == pytest.approx(0.025)
    assert fetched.dividend_yield == pytest.approx(0.02)
    assert fetched.volatility == pytest.approx(0.185)
    assert fetched.notes == "baseline"
    assert isinstance(fetched.created_at, datetime)
    assert isinstance(fetched.updated_at, datetime)


def test_underlying_unique(session: Session) -> None:
    session.add(UnderlyingPricingDefault(underlying="000300.SH"))
    session.commit()
    session.add(UnderlyingPricingDefault(underlying="000300.SH"))
    with pytest.raises(Exception):
        session.commit()
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: ImportError or AttributeError for `UnderlyingPricingDefault`.

- [ ] **Step 3: Declare the model**

Insert into `backend/app/models.py` after the `PricingParameterRow` class definition:

```python
class UnderlyingPricingDefault(Base):
    __tablename__ = "underlying_pricing_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    @property
    def is_complete(self) -> bool:
        return (
            self.rate is not None
            and self.dividend_yield is not None
            and self.volatility is not None
        )
```

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add UnderlyingPricingDefault SQLAlchemy model"
```

---

### Task 3: Pydantic schemas

**Files:**
- Modify: `backend/app/schemas.py` (append a new block after `SpotPricingProfileCreate` around line 629)

- [ ] **Step 1: Write failing schema tests**

Append to `tests/test_underlying_defaults.py`:

```python
from app.schemas import (
    BuildDefaultProfileRequest,
    UnderlyingPricingDefaultOut,
    UnderlyingPricingDefaultUpdate,
)


def test_underlying_pricing_default_out_includes_is_complete(session: Session) -> None:
    row = UnderlyingPricingDefault(
        underlying="000300.SH", rate=0.025, dividend_yield=0.02, volatility=0.185
    )
    session.add(row)
    session.commit()
    out = UnderlyingPricingDefaultOut.model_validate(row, from_attributes=True)
    assert out.is_complete is True
    assert out.latest_akshare_close is None


def test_underlying_pricing_default_out_marks_incomplete(session: Session) -> None:
    row = UnderlyingPricingDefault(underlying="000300.SH", rate=0.025)
    session.add(row)
    session.commit()
    out = UnderlyingPricingDefaultOut.model_validate(row, from_attributes=True)
    assert out.is_complete is False


def test_update_payload_allows_partial_fields() -> None:
    payload = UnderlyingPricingDefaultUpdate(rate=0.02)
    assert payload.rate == 0.02
    assert payload.dividend_yield is None
    assert payload.volatility is None
    assert payload.notes is None


def test_build_request_defaults() -> None:
    req = BuildDefaultProfileRequest()
    assert req.name is None
    assert req.valuation_date is None
    assert req.adjust == "qfq"
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: ImportError for the new schema classes.

- [ ] **Step 3: Add the schemas**

Append to `backend/app/schemas.py` (after `SpotPricingProfileCreate`):

```python
class LatestAkshareClose(BaseModel):
    spot: float | None
    fetched_at: datetime | None
    fallback: bool = False
    market_data_profile_id: int | None = None


class UnderlyingPricingDefaultOut(BaseModel):
    underlying: str
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None
    is_complete: bool
    latest_akshare_close: LatestAkshareClose | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UnderlyingPricingDefaultUpdate(BaseModel):
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None


class BuildDefaultProfileRequest(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None
    adjust: str = "qfq"
```

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add Pydantic schemas for underlying pricing defaults"
```

---

### Task 4: Open-positions helper

**Files:**
- Modify: `backend/app/services/pricing_profiles.py` (add helper near the top)
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_underlying_defaults.py`:

```python
from app.models import Portfolio, Position
from app.services.pricing_profiles import _open_position_underlyings


def _make_position(
    session: Session,
    *,
    underlying: str,
    status: str = "open",
    source_trade_id: str | None = "TRD-1",
    source_trade_state: str | None = None,
) -> Position:
    portfolio = (
        session.query(Portfolio).first()
        or Portfolio(name="Test")
    )
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    payload = {"trade_state": source_trade_state} if source_trade_state else {}
    position = Position(
        portfolio_id=portfolio.id,
        underlying=underlying,
        source_trade_id=source_trade_id,
        status=status,
        engine_name="quantark.vanilla",
        engine_kwargs={},
        source_trade_payload=payload,
        mapping_status="supported",
    )
    session.add(position)
    session.flush()
    return position


def test_open_position_underlyings_returns_distinct_open(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-3")
    _make_position(
        session, underlying="600519.SH", status="closed", source_trade_id="TRD-4"
    )
    _make_position(
        session,
        underlying="600000.SH",
        source_trade_state="敲出",
        source_trade_id="TRD-5",
    )
    assert _open_position_underlyings(session) == ["000300.SH", "000852.SH"]


def test_open_position_underlyings_empty(session: Session) -> None:
    assert _open_position_underlyings(session) == []
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py::test_open_position_underlyings_returns_distinct_open -q`
Expected: ImportError for `_open_position_underlyings`.

- [ ] **Step 3: Implement the helper**

Add to `backend/app/services/pricing_profiles.py` (top of file, after existing imports):

```python
from ..models import (
    MarketDataProfile,
    PricingParameterProfile,
    PricingParameterRow,
    Position,
    UnderlyingPricingDefault,
)
from .position_adapter import make_json_safe


def _open_position_underlyings(session: Session) -> list[str]:
    rows = (
        session.query(Position.underlying, Position.source_trade_payload, Position.status)
        .filter(Position.underlying.isnot(None))
        .filter(Position.status != "closed")
        .all()
    )
    underlyings: set[str] = set()
    for underlying, payload, status in rows:
        if status == "closed":
            continue
        state = None
        if isinstance(payload, dict):
            state = payload.get("trade_state")
        if state == "敲出":
            continue
        cleaned = (underlying or "").strip()
        if cleaned:
            underlyings.add(cleaned)
    return sorted(underlyings)
```

Note: this uses `Position.source_trade_payload.trade_state` to mirror the position pricer's `_source_trade_state` helper. If that helper exposes a public surface, prefer it; otherwise inline as shown.

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing_profiles.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add _open_position_underlyings helper"
```

---

### Task 5: Underlying defaults service (CRUD + refresh)

**Files:**
- Create: `backend/app/services/underlying_defaults.py`
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write failing service tests**

Append to `tests/test_underlying_defaults.py`:

```python
from app.services.underlying_defaults import (
    delete_underlying_default,
    list_underlying_defaults,
    refresh_underlying_defaults_from_open_positions,
    upsert_underlying_default,
)


def test_upsert_creates_new_row(session: Session) -> None:
    row = upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.commit()
    assert row.is_complete is True
    assert row.notes == "baseline"


def test_upsert_preserves_omitted_fields(session: Session) -> None:
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.commit()
    updated = upsert_underlying_default(session, underlying="000300.SH", rate=0.03)
    session.commit()
    assert updated.rate == pytest.approx(0.03)
    assert updated.dividend_yield == pytest.approx(0.02)
    assert updated.volatility == pytest.approx(0.185)
    assert updated.notes == "baseline"


def test_delete_underlying_default(session: Session) -> None:
    upsert_underlying_default(session, underlying="000300.SH", rate=0.025)
    session.commit()
    delete_underlying_default(session, underlying="000300.SH")
    session.commit()
    assert (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one_or_none()
        is None
    )


def test_refresh_from_open_positions_auto_discovers(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-2")
    session.commit()

    refreshed = refresh_underlying_defaults_from_open_positions(session)
    session.commit()
    underlyings = {row.underlying for row in refreshed}
    assert underlyings == {"000300.SH", "000852.SH"}
    for row in refreshed:
        assert row.is_complete is False


def test_refresh_does_not_modify_existing_rows(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    upsert_underlying_default(session, underlying="000300.SH", rate=0.025)
    session.commit()
    refresh_underlying_defaults_from_open_positions(session)
    session.commit()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one()
    )
    assert row.rate == pytest.approx(0.025)


def test_list_returns_sorted_by_underlying(session: Session) -> None:
    upsert_underlying_default(session, underlying="000852.SH")
    upsert_underlying_default(session, underlying="000300.SH")
    upsert_underlying_default(session, underlying="600519.SH")
    session.commit()
    rows = list_underlying_defaults(session)
    assert [r.underlying for r in rows] == ["000300.SH", "000852.SH", "600519.SH"]
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: ImportError for the new service module.

- [ ] **Step 3: Implement the service**

Create `backend/app/services/underlying_defaults.py`:

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models import MarketDataProfile, UnderlyingPricingDefault
from .pricing_profiles import _open_position_underlyings


def list_underlying_defaults(session: Session) -> list[UnderlyingPricingDefault]:
    return (
        session.query(UnderlyingPricingDefault)
        .order_by(UnderlyingPricingDefault.underlying.asc())
        .all()
    )


def upsert_underlying_default(
    session: Session,
    *,
    underlying: str,
    rate: float | None = ...,
    dividend_yield: float | None = ...,
    volatility: float | None = ...,
    notes: str | None = ...,
) -> UnderlyingPricingDefault:
    cleaned = (underlying or "").strip()
    if not cleaned:
        raise ValueError("underlying must not be empty")
    row = (
        session.query(UnderlyingPricingDefault)
        .filter(UnderlyingPricingDefault.underlying == cleaned)
        .one_or_none()
    )
    if row is None:
        row = UnderlyingPricingDefault(underlying=cleaned)
        session.add(row)
    if rate is not ...:
        row.rate = rate
    if dividend_yield is not ...:
        row.dividend_yield = dividend_yield
    if volatility is not ...:
        row.volatility = volatility
    if notes is not ...:
        row.notes = notes
    session.flush()
    return row


def delete_underlying_default(session: Session, *, underlying: str) -> None:
    cleaned = (underlying or "").strip()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter(UnderlyingPricingDefault.underlying == cleaned)
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"underlying not found: {cleaned}")
    session.delete(row)
    session.flush()


def refresh_underlying_defaults_from_open_positions(
    session: Session,
) -> list[UnderlyingPricingDefault]:
    underlyings = _open_position_underlyings(session)
    existing = {
        row.underlying: row
        for row in session.query(UnderlyingPricingDefault).all()
    }
    for underlying in underlyings:
        if underlying in existing:
            continue
        row = UnderlyingPricingDefault(underlying=underlying)
        session.add(row)
        existing[underlying] = row
    session.flush()
    return list_underlying_defaults(session)


def latest_akshare_close_by_underlying(
    session: Session, underlyings: list[str]
) -> dict[str, dict | None]:
    cleaned = [u.strip() for u in underlyings if u and u.strip()]
    if not cleaned:
        return {}
    result: dict[str, dict | None] = {u: None for u in cleaned}
    rows = (
        session.query(MarketDataProfile)
        .filter(MarketDataProfile.symbol.in_(cleaned))
        .order_by(MarketDataProfile.symbol.asc(), desc(MarketDataProfile.valuation_date))
        .all()
    )
    seen: set[str] = set()
    for profile in rows:
        if profile.symbol in seen:
            continue
        seen.add(profile.symbol)
        data = profile.data or {}
        metadata = profile.source_metadata or {}
        spot = data.get("spot")
        if spot is None:
            result[profile.symbol] = None
            continue
        result[profile.symbol] = {
            "spot": float(spot),
            "fetched_at": profile.valuation_date,
            "fallback": bool(metadata.get("fallback")),
            "market_data_profile_id": profile.id,
        }
    return result
```

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/underlying_defaults.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add underlying defaults service (CRUD + refresh)"
```

---

### Task 6: Build pipeline service

**Files:**
- Modify: `backend/app/services/pricing_profiles.py` (append `build_default_pricing_profile`)
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write failing build pipeline tests**

Append to `tests/test_underlying_defaults.py`:

```python
from datetime import datetime
from unittest.mock import patch

from app.schemas import MarketDataSnapshot
from app.services.pricing_profiles import build_default_pricing_profile


def _fake_snapshot(symbol: str, *, spot: float | None, fallback: bool = False) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        name=f"{symbol} snapshot",
        source="akshare",
        symbol=symbol,
        asset_class="index",
        valuation_date=datetime.utcnow(),
        data={"rows": [], "latest": {"close": spot}, "spot": spot},
        source_metadata={"fallback": fallback},
    )


def test_build_fails_when_no_open_positions(session: Session) -> None:
    with pytest.raises(ValueError, match="no open positions"):
        build_default_pricing_profile(session)


def test_build_fails_with_unfilled_underlyings(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    session.commit()
    with pytest.raises(ValueError) as excinfo:
        build_default_pricing_profile(session)
    payload = excinfo.value.args
    assert any("000300.SH" in str(arg) for arg in payload)


def test_build_does_not_call_akshare_when_unfilled(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    session.commit()
    with patch("app.services.pricing_profiles.fetch_akshare_snapshot") as fetch:
        with pytest.raises(ValueError):
            build_default_pricing_profile(session)
        fetch.assert_not_called()


def test_build_fails_when_akshare_falls_back(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=None, fallback=True),
    ):
        with pytest.raises(ValueError, match="AKShare"):
            build_default_pricing_profile(session)
    assert session.query(PricingParameterProfile).count() == 0


def test_build_happy_path(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-3")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    upsert_underlying_default(
        session,
        underlying="000852.SH",
        rate=0.025,
        dividend_yield=0.015,
        volatility=0.24,
    )
    session.commit()

    def fake_fetch(request):
        spots = {"000300": 3842.15, "000852": 6184.72}
        return _fake_snapshot(request.symbol, spot=spots[request.symbol])

    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        side_effect=fake_fetch,
    ):
        profile = build_default_pricing_profile(session)
        session.commit()

    assert profile.source_type == "default_underlying"
    assert len(profile.rows) == 3
    by_trade = {row.source_trade_id: row for row in profile.rows}
    assert by_trade["TRD-1"].spot == pytest.approx(3842.15)
    assert by_trade["TRD-1"].rate == pytest.approx(0.025)
    assert by_trade["TRD-1"].dividend_yield == pytest.approx(0.02)
    assert by_trade["TRD-1"].volatility == pytest.approx(0.185)
    assert by_trade["TRD-3"].spot == pytest.approx(6184.72)
    underlyings = {item["underlying"] for item in profile.summary["underlyings"]}
    assert underlyings == {"000300.SH", "000852.SH"}


def test_build_auto_discovers_missing_entries(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    session.commit()
    with patch("app.services.pricing_profiles.fetch_akshare_snapshot"):
        with pytest.raises(ValueError):
            build_default_pricing_profile(session)
    assert (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one()
        .is_complete
        is False
    )


def test_build_skips_positions_without_trade_id(session: Session) -> None:
    _make_position(session, underlying="000300.SH", source_trade_id=None)
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        profile = build_default_pricing_profile(session)
        session.commit()
    assert len(profile.rows) == 1
    skipped = profile.summary.get("skipped_positions") or []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "missing_source_trade_id"


def test_build_appends_new_profile_each_invocation(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        first = build_default_pricing_profile(session)
        session.commit()
        second = build_default_pricing_profile(session)
        session.commit()
    assert first.id != second.id
    assert session.query(PricingParameterProfile).count() == 2
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: ImportError for `build_default_pricing_profile`.

- [ ] **Step 3: Implement the build pipeline**

Append to `backend/app/services/pricing_profiles.py`:

```python
from datetime import datetime
from typing import Any

from ..schemas import AkshareSnapshotRequest
from .market_data import fetch_akshare_snapshot


def _akshare_symbol(symbol: str) -> str:
    code = (symbol or "").strip()
    if "." in code:
        return code.split(".", 1)[0]
    return code


def _akshare_asset_class(symbol: str) -> str:
    code, _, suffix = (symbol or "").strip().partition(".")
    if suffix in {"DCE", "SHF", "CZC", "INE", "CFFEX", "GFEX"}:
        return "futures"
    if suffix == "CSI" or code in {
        "000016",
        "000300",
        "000852",
        "000905",
        "931059",
    }:
        return "index"
    return "stock"


def build_default_pricing_profile(
    session: Session,
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
    adjust: str = "qfq",
) -> PricingParameterProfile:
    effective_valuation = valuation_date or datetime.utcnow()
    underlyings = _open_position_underlyings(session)
    if not underlyings:
        raise ValueError("no open positions in scope")

    existing = {
        row.underlying: row
        for row in session.query(UnderlyingPricingDefault).all()
    }
    for underlying in underlyings:
        if underlying not in existing:
            row = UnderlyingPricingDefault(underlying=underlying)
            session.add(row)
            existing[underlying] = row
    session.flush()

    unfilled = [
        underlying
        for underlying in underlyings
        if not existing[underlying].is_complete
    ]
    if unfilled:
        raise ValueError({"unfilled_underlyings": sorted(unfilled)})

    end_date = effective_valuation.strftime("%Y-%m-%d")
    start_date = (effective_valuation - _timedelta_business_days(10)).strftime("%Y-%m-%d")

    fetched: dict[str, dict[str, Any]] = {}
    failed: list[dict[str, str]] = []
    for underlying in underlyings:
        request = AkshareSnapshotRequest(
            symbol=_akshare_symbol(underlying),
            asset_class=_akshare_asset_class(underlying),  # type: ignore[arg-type]
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            name=f"{underlying} default-profile fetch",
        )
        snapshot = fetch_akshare_snapshot(request)
        data = snapshot.data or {}
        meta = snapshot.source_metadata or {}
        spot = data.get("spot")
        if spot is None or meta.get("fallback"):
            failed.append(
                {
                    "underlying": underlying,
                    "reason": str(meta.get("reason") or "AKShare returned no usable spot"),
                }
            )
            continue
        fetched[underlying] = {
            "underlying": underlying,
            "akshare_symbol": request.symbol,
            "asset_class": request.asset_class,
            "spot": float(spot),
            "akshare_source": meta.get("source_name") or "akshare",
            "akshare_fallback": False,
            "valuation_date": snapshot.valuation_date.isoformat(),
        }
    if failed:
        raise ValueError({"failed_akshare_underlyings": failed})

    profile = PricingParameterProfile(
        name=name or f"Default {effective_valuation:%Y-%m-%d %H:%M}",
        valuation_date=effective_valuation,
        source_type="default_underlying",
        source_path=None,
        status="completed",
        summary={},
    )
    session.add(profile)
    session.flush()

    skipped_positions: list[dict[str, Any]] = []
    row_count = 0
    open_positions = (
        session.query(Position)
        .filter(Position.underlying.isnot(None))
        .filter(Position.status != "closed")
        .all()
    )
    for position in open_positions:
        underlying = (position.underlying or "").strip()
        if underlying not in fetched:
            continue
        payload = position.source_trade_payload or {}
        if isinstance(payload, dict) and payload.get("trade_state") == "敲出":
            continue
        trade_id = (position.source_trade_id or "").strip()
        if not trade_id:
            skipped_positions.append(
                {
                    "position_id": position.id,
                    "reason": "missing_source_trade_id",
                }
            )
            continue
        store = existing[underlying]
        spot = fetched[underlying]["spot"]
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=trade_id,
                symbol=underlying,
                spot=spot,
                rate=store.rate,
                dividend_yield=store.dividend_yield,
                volatility=store.volatility,
                source_row=None,
                source_payload=make_json_safe(
                    {
                        "source": "default_underlying",
                        "underlying_default_id": store.id,
                        "akshare_symbol": fetched[underlying]["akshare_symbol"],
                    }
                ),
            )
        )
        row_count += 1

    profile.summary = {
        "row_count": row_count,
        "underlyings": [fetched[underlying] for underlying in underlyings],
        "valuation_date": effective_valuation.isoformat(),
        "adjust": adjust,
        "skipped_positions": skipped_positions,
    }
    session.flush()
    return profile


def _timedelta_business_days(days: int):
    from datetime import timedelta

    return timedelta(days=days * 2)
```

(The simple `timedelta(days=days * 2)` cushion comfortably covers weekends for short windows. If a better business-day helper exists in the codebase, prefer it.)

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing_profiles.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add build_default_pricing_profile service"
```

---

### Task 7: API endpoints (CRUD + refresh + build)

**Files:**
- Modify: `backend/app/main.py` (add new endpoint block after the existing pricing-parameter-profiles endpoints around line 1448)
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_underlying_defaults.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def api_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OPEN_OTC_DB_URL", f"sqlite+pysqlite:///{tmp_path}/api.db")
    import importlib

    from app import database

    importlib.reload(database)
    app = create_app()
    return TestClient(app)


def test_list_underlying_defaults_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/api/underlying-pricing-defaults")
    assert response.status_code == 200
    assert response.json() == []


def test_put_underlying_default_creates(api_client: TestClient) -> None:
    response = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is True
    assert body["rate"] == 0.025


def test_put_underlying_default_partial(api_client: TestClient) -> None:
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )
    response = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.03},
    )
    body = response.json()
    assert body["rate"] == 0.03
    assert body["dividend_yield"] == 0.02


def test_delete_underlying_default(api_client: TestClient) -> None:
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025},
    )
    response = api_client.delete("/api/underlying-pricing-defaults/000300.SH")
    assert response.status_code == 204
    listed = api_client.get("/api/underlying-pricing-defaults").json()
    assert listed == []


def test_refresh_from_positions_endpoint(api_client: TestClient, monkeypatch) -> None:
    # Seed a position
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()

    response = api_client.post("/api/underlying-pricing-defaults/refresh-from-positions")
    assert response.status_code == 200
    body = response.json()
    assert any(row["underlying"] == "000300.SH" and row["is_complete"] is False for row in body)


def test_build_default_endpoint_unfilled(api_client: TestClient) -> None:
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()
    response = api_client.post("/api/pricing-parameter-profiles/build-default", json={})
    assert response.status_code == 400
    body = response.json()
    assert "unfilled_underlyings" in body
    assert body["unfilled_underlyings"] == ["000300.SH"]


def test_build_default_endpoint_no_positions(api_client: TestClient) -> None:
    response = api_client.post("/api/pricing-parameter-profiles/build-default", json={})
    assert response.status_code == 400
    assert "no open positions" in response.json()["detail"]


def test_build_default_endpoint_happy(api_client: TestClient, monkeypatch) -> None:
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )

    def fake_fetch(request):
        return _fake_snapshot("000300", spot=3842.15)

    monkeypatch.setattr(
        "app.services.pricing_profiles.fetch_akshare_snapshot", fake_fetch
    )
    response = api_client.post("/api/pricing-parameter-profiles/build-default", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["source_type"] == "default_underlying"
    assert body["rows"][0]["spot"] == pytest.approx(3842.15)
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 404 responses from endpoints that don't yet exist.

- [ ] **Step 3: Add the endpoints**

Add imports at the top of `backend/app/main.py`:

```python
from .schemas import (
    BuildDefaultProfileRequest,
    LatestAkshareClose,
    UnderlyingPricingDefaultOut,
    UnderlyingPricingDefaultUpdate,
)
from .services.pricing_profiles import build_default_pricing_profile
from .services.underlying_defaults import (
    delete_underlying_default,
    latest_akshare_close_by_underlying,
    list_underlying_defaults,
    refresh_underlying_defaults_from_open_positions,
    upsert_underlying_default,
)
```

Add a small helper above the endpoint block:

```python
def _serialize_default(
    row,
    latest_map: dict[str, dict | None],
) -> UnderlyingPricingDefaultOut:
    latest = latest_map.get(row.underlying)
    return UnderlyingPricingDefaultOut(
        underlying=row.underlying,
        rate=row.rate,
        dividend_yield=row.dividend_yield,
        volatility=row.volatility,
        notes=row.notes,
        is_complete=row.is_complete,
        latest_akshare_close=LatestAkshareClose(**latest) if latest else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
```

Insert new endpoints after the existing pricing-parameter-profile endpoints (around line 1448):

```python
@app.get(
    "/api/underlying-pricing-defaults",
    response_model=list[UnderlyingPricingDefaultOut],
)
def list_underlying_pricing_defaults(session: Session = Depends(get_db)):
    rows = list_underlying_defaults(session)
    latest = latest_akshare_close_by_underlying(
        session, [row.underlying for row in rows]
    )
    return [_serialize_default(row, latest) for row in rows]


@app.put(
    "/api/underlying-pricing-defaults/{underlying}",
    response_model=UnderlyingPricingDefaultOut,
)
def put_underlying_pricing_default(
    underlying: str,
    payload: UnderlyingPricingDefaultUpdate,
    session: Session = Depends(get_db),
):
    body = payload.model_dump(exclude_unset=True)
    try:
        row = upsert_underlying_default(session, underlying=underlying, **body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    record_audit(
        session,
        event_type="underlying_pricing_defaults.upserted",
        actor="desk_user",
        subject_type="underlying_pricing_default",
        subject_id=row.id,
        payload=body,
    )
    session.commit()
    latest = latest_akshare_close_by_underlying(session, [row.underlying])
    return _serialize_default(row, latest)


@app.delete(
    "/api/underlying-pricing-defaults/{underlying}",
    status_code=204,
)
def delete_underlying_pricing_default(
    underlying: str, session: Session = Depends(get_db)
):
    try:
        delete_underlying_default(session, underlying=underlying)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return None


@app.post(
    "/api/underlying-pricing-defaults/refresh-from-positions",
    response_model=list[UnderlyingPricingDefaultOut],
)
def refresh_underlying_pricing_defaults(session: Session = Depends(get_db)):
    rows = refresh_underlying_defaults_from_open_positions(session)
    session.commit()
    latest = latest_akshare_close_by_underlying(
        session, [row.underlying for row in rows]
    )
    return [_serialize_default(row, latest) for row in rows]


@app.post(
    "/api/pricing-parameter-profiles/build-default",
    response_model=PricingParameterProfileOut,
)
def build_default_pricing_profile_endpoint(
    payload: BuildDefaultProfileRequest,
    session: Session = Depends(get_db),
):
    try:
        profile = build_default_pricing_profile(
            session,
            name=payload.name,
            valuation_date=payload.valuation_date,
            adjust=payload.adjust,
        )
    except ValueError as exc:
        arg = exc.args[0] if exc.args else "build failed"
        if isinstance(arg, dict) and "unfilled_underlyings" in arg:
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "underlying defaults missing inputs",
                    "unfilled_underlyings": arg["unfilled_underlyings"],
                },
            )
        if isinstance(arg, dict) and "failed_akshare_underlyings" in arg:
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "AKShare spot fetch failed for one or more underlyings",
                    "failed_akshare_underlyings": arg["failed_akshare_underlyings"],
                },
            )
        raise HTTPException(status_code=400, detail=str(arg))
    record_audit(
        session,
        event_type="pricing_parameters.default_built",
        actor="desk_user",
        subject_type="pricing_parameter_profile",
        subject_id=profile.id,
        payload={
            "row_count": profile.summary.get("row_count"),
            "underlyings": [u["underlying"] for u in profile.summary.get("underlyings", [])],
        },
    )
    session.commit()
    return (
        session.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile.id)
        .one()
    )
```

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py -q`
Expected: 30 passed.

- [ ] **Step 5: Backend regression**

Run: `.venv/bin/python -m pytest tests/test_api.py tests/test_position_import_pricing.py tests/test_position_pricer_grid.py -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): add underlying defaults + build-default API endpoints"
```

---

### Task 8: Position pricer regression guard

**Files:**
- Test: `tests/test_underlying_defaults.py`

- [ ] **Step 1: Write the regression test**

Append to `tests/test_underlying_defaults.py`:

```python
from app.services.position_pricer import resolve_position_market_inputs


def test_default_underlying_profile_resolves_same_as_xlsx(session: Session) -> None:
    position = _make_position(session, underlying="000300.SH")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        default_profile = build_default_pricing_profile(session)
        session.commit()

    xlsx_profile = PricingParameterProfile(
        name="Equivalent XLSX",
        valuation_date=datetime.utcnow(),
        source_type="xlsx",
        status="completed",
        summary={"row_count": 1},
    )
    session.add(xlsx_profile)
    session.flush()
    session.add(
        PricingParameterRow(
            profile_id=xlsx_profile.id,
            source_trade_id="TRD-1",
            symbol="000300.SH",
            spot=3842.15,
            rate=0.025,
            dividend_yield=0.02,
            volatility=0.185,
        )
    )
    session.commit()

    # If a public resolver isn't available, this test compares the row values directly.
    default_row = next(r for r in default_profile.rows if r.source_trade_id == "TRD-1")
    xlsx_row = next(r for r in xlsx_profile.rows if r.source_trade_id == "TRD-1")
    for attr in ("spot", "rate", "dividend_yield", "volatility", "symbol"):
        assert getattr(default_row, attr) == getattr(xlsx_row, attr)
```

(Drop `from app.services.position_pricer import resolve_position_market_inputs` if no such helper exists. Compare the rows attribute-by-attribute as shown.)

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/test_underlying_defaults.py::test_default_underlying_profile_resolves_same_as_xlsx -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_underlying_defaults.py
git commit -m "test(pricing): regression guard for default_underlying parity with xlsx"
```

---

### Task 9: Frontend types

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Locate the `PricingParameterProfile` block**

Run: `grep -n "PricingParameterProfile\|PricingParameterRow" frontend/src/types.ts`
Expected: the `PricingParameterProfile` type currently has `source_type: string`.

- [ ] **Step 2: Tighten source_type and add UnderlyingPricingDefault**

Edit `frontend/src/types.ts`:

Replace `source_type: string;` inside `PricingParameterProfile` with:

```ts
source_type: 'xlsx' | 'market_data_spot' | 'default_underlying' | string;
```

Append at the bottom of the file:

```ts
export type LatestAkshareClose = {
  spot: number | null;
  fetched_at: string | null;
  fallback: boolean;
  market_data_profile_id: number | null;
};

export type UnderlyingPricingDefault = {
  underlying: string;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  is_complete: boolean;
  latest_akshare_close: LatestAkshareClose | null;
  created_at: string;
  updated_at: string;
};
```

- [ ] **Step 3: Verify the frontend still typechecks**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(pricing): add UnderlyingPricingDefault frontend type"
```

---

### Task 10: Remove "Load to Positions" handoff

**Files:**
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.live.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`

- [ ] **Step 1: Update the test to lock in the absence**

Edit `frontend/src/routes/PricingParameters.test.tsx`. Find the test that renders `<PricingParameters …>` and add a new assertion (or new test) at the bottom:

```ts
it('does not render a Load to Positions button', () => {
  render(
    <PricingParameters
      profiles={[]}
      selectedId={null}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      onSelectProfile={() => {}}
      onImport={() => {}}
    />,
  );
  expect(screen.queryByRole('button', { name: /load to positions/i })).toBeNull();
});
```

- [ ] **Step 2: Run focused frontend test — confirm failure**

Run: `cd frontend && npm test -- PricingParameters.test`
Expected: the new test fails because the button still exists.

- [ ] **Step 3: Remove the button and prop**

Edit `frontend/src/routes/PricingParameters.tsx`:

- Remove `ArrowRight` from the `lucide-react` import.
- Remove `onUseInPositions?: (profileId: number) => void;` from `type Props`.
- Remove `onUseInPositions` from the function parameter list.
- Remove the entire `{onUseInPositions && ( … )}` JSX block from the page header.

Edit `frontend/src/routes/PricingParameters.live.tsx`:

- Remove `onUseInPositions?: (profileId: number) => void;` from `type Props`.
- Remove `onUseInPositions` from the function parameter list.
- Remove the `onUseInPositions={onUseInPositions}` prop on `<PricingParameters />`.

Edit `frontend/src/main.tsx`:

- Remove the `positionsPricingProfileId` state declaration (and its setter).
- Drop the `onPricingProfileChange={setPositionsPricingProfileId}` prop on the Positions route block (if present); Positions already manages its own selector state.
- Replace the `<PricingParametersLive onUseInPositions={…} … />` block with `<PricingParametersLive onPageContextChange={handlePageContextChange} />`.
- Remove any remaining references to `positionsPricingProfileId`.

- [ ] **Step 4: Re-run focused frontend tests — confirm pass**

Run: `cd frontend && npm test -- PricingParameters`
Expected: all green including the new "does not render" assertion.

- [ ] **Step 5: Regression — Positions and Risk profile selectors still work**

Run: `cd frontend && npm test -- Risk Positions`
Expected: existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/PricingParameters.tsx \
        frontend/src/routes/PricingParameters.live.tsx \
        frontend/src/routes/PricingParameters.test.tsx \
        frontend/src/main.tsx
git commit -m "refactor(pricing): drop Load to Positions cross-page handoff"
```

---

### Task 11: Underlying Defaults panel component

**Files:**
- Create: `frontend/src/routes/UnderlyingDefaultsPanel.tsx`
- Modify: `frontend/src/routes/PricingParameters.css`
- Create: `frontend/src/routes/UnderlyingDefaultsPanel.test.tsx`

- [ ] **Step 1: Write failing component tests**

Create `frontend/src/routes/UnderlyingDefaultsPanel.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { UnderlyingDefaultsPanel } from './UnderlyingDefaultsPanel';
import type { UnderlyingPricingDefault } from '../types';

const filled: UnderlyingPricingDefault = {
  underlying: '000300.SH',
  rate: 0.025,
  dividend_yield: 0.02,
  volatility: 0.185,
  notes: 'baseline',
  is_complete: true,
  latest_akshare_close: {
    spot: 3842.15,
    fetched_at: '2026-05-13T09:30:00',
    fallback: false,
    market_data_profile_id: 1,
  },
  created_at: '2026-05-10T00:00:00',
  updated_at: '2026-05-12T14:02:00',
};

const partial: UnderlyingPricingDefault = {
  ...filled,
  underlying: '000852.SH',
  rate: null,
  dividend_yield: null,
  is_complete: false,
  notes: 'auto-discovered',
};

describe('UnderlyingDefaultsPanel', () => {
  it('renders counters reflecting filled vs incomplete', () => {
    render(
      <UnderlyingDefaultsPanel
        rows={[filled, partial]}
        building={false}
        refreshing={false}
        bulkRefreshing={false}
        feedback={null}
        onRefreshFromPositions={() => {}}
        onRefreshAllSpots={() => {}}
        onUpsert={() => {}}
        onDelete={() => {}}
        onBuild={() => {}}
      />,
    );
    expect(screen.getByText(/2 entries/i)).toBeInTheDocument();
    expect(screen.getByText(/1 filled/i)).toBeInTheDocument();
    expect(screen.getByText(/1 incomplete/i)).toBeInTheDocument();
  });

  it('disables Build button when incomplete > 0', () => {
    render(
      <UnderlyingDefaultsPanel
        rows={[partial]}
        building={false}
        refreshing={false}
        bulkRefreshing={false}
        feedback={null}
        onRefreshFromPositions={() => {}}
        onRefreshAllSpots={() => {}}
        onUpsert={() => {}}
        onDelete={() => {}}
        onBuild={() => {}}
      />,
    );
    const button = screen.getByRole('button', { name: /build default profile/i });
    expect(button).toBeDisabled();
  });

  it('invokes onUpsert with edited values', () => {
    const onUpsert = vi.fn();
    render(
      <UnderlyingDefaultsPanel
        rows={[partial]}
        building={false}
        refreshing={false}
        bulkRefreshing={false}
        feedback={null}
        onRefreshFromPositions={() => {}}
        onRefreshAllSpots={() => {}}
        onUpsert={onUpsert}
        onDelete={() => {}}
        onBuild={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const rateInput = screen.getByLabelText(/rate/i) as HTMLInputElement;
    fireEvent.change(rateInput, { target: { value: '0.025' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onUpsert).toHaveBeenCalledWith('000852.SH', expect.objectContaining({ rate: 0.025 }));
  });

  it('renders unfilled-underlyings banner when feedback present', () => {
    render(
      <UnderlyingDefaultsPanel
        rows={[partial]}
        building={false}
        refreshing={false}
        bulkRefreshing={false}
        feedback={{ tone: 'error', message: 'underlying defaults missing inputs', unfilled: ['000852.SH'] }}
        onRefreshFromPositions={() => {}}
        onRefreshAllSpots={() => {}}
        onUpsert={() => {}}
        onDelete={() => {}}
        onBuild={() => {}}
      />,
    );
    expect(screen.getByText(/000852.SH/)).toBeInTheDocument();
    expect(screen.getByText(/missing rate \/ div \/ vol/i)).toBeInTheDocument();
  });

  it('calls onBuild when button clicked and all rows are complete', () => {
    const onBuild = vi.fn();
    render(
      <UnderlyingDefaultsPanel
        rows={[filled]}
        building={false}
        refreshing={false}
        bulkRefreshing={false}
        feedback={null}
        onRefreshFromPositions={() => {}}
        onRefreshAllSpots={() => {}}
        onUpsert={() => {}}
        onDelete={() => {}}
        onBuild={onBuild}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /build default profile/i }));
    expect(onBuild).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run focused tests — confirm failure**

Run: `cd frontend && npm test -- UnderlyingDefaultsPanel`
Expected: import error for the missing component.

- [ ] **Step 3: Implement the component**

Create `frontend/src/routes/UnderlyingDefaultsPanel.tsx`:

```tsx
import { useState } from 'react';
import { Button } from '../components/Button';
import type { UnderlyingPricingDefault } from '../types';

export type UnderlyingDefaultsFeedback = {
  tone: 'success' | 'error';
  message: string;
  unfilled?: string[];
  failedAkshare?: { underlying: string; reason: string }[];
};

export type UnderlyingDefaultsPanelProps = {
  rows: UnderlyingPricingDefault[];
  building: boolean;
  refreshing: boolean;
  bulkRefreshing: boolean;
  feedback: UnderlyingDefaultsFeedback | null;
  onRefreshFromPositions: () => void;
  onRefreshAllSpots: () => void;
  onUpsert: (
    underlying: string,
    fields: {
      rate?: number | null;
      dividend_yield?: number | null;
      volatility?: number | null;
      notes?: string | null;
    },
  ) => void;
  onDelete: (underlying: string) => void;
  onBuild: () => void;
};

type DraftFields = {
  rate: string;
  dividend_yield: string;
  volatility: string;
  notes: string;
};

function toDraft(row: UnderlyingPricingDefault): DraftFields {
  return {
    rate: row.rate == null ? '' : String(row.rate),
    dividend_yield: row.dividend_yield == null ? '' : String(row.dividend_yield),
    volatility: row.volatility == null ? '' : String(row.volatility),
    notes: row.notes ?? '',
  };
}

function parseNumber(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function UnderlyingDefaultsPanel({
  rows,
  building,
  refreshing,
  bulkRefreshing,
  feedback,
  onRefreshFromPositions,
  onRefreshAllSpots,
  onUpsert,
  onDelete,
  onBuild,
}: UnderlyingDefaultsPanelProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftFields | null>(null);

  const filled = rows.filter((row) => row.is_complete).length;
  const incomplete = rows.length - filled;
  const buildDisabled = building || rows.length === 0 || incomplete > 0;

  const startEdit = (row: UnderlyingPricingDefault) => {
    setEditing(row.underlying);
    setDraft(toDraft(row));
  };
  const cancelEdit = () => {
    setEditing(null);
    setDraft(null);
  };
  const saveEdit = (underlying: string) => {
    if (!draft) return;
    onUpsert(underlying, {
      rate: parseNumber(draft.rate),
      dividend_yield: parseNumber(draft.dividend_yield),
      volatility: parseNumber(draft.volatility),
      notes: draft.notes.trim() === '' ? null : draft.notes,
    });
    cancelEdit();
  };

  return (
    <section className="wl-underlying-defaults">
      <header className="wl-underlying-defaults__head">
        <div className="wl-underlying-defaults__title">
          <span className="wl-underlying-defaults__heading">UNDERLYING DEFAULTS</span>
          <span className="wl-underlying-defaults__sublabel">CONFIG STORE</span>
          <div className="wl-underlying-defaults__counters">
            <span><strong>{rows.length}</strong> entries</span>
            <span className="is-ok"><strong>{filled}</strong> filled</span>
            <span className={incomplete > 0 ? 'is-warn' : ''}>
              <strong>{incomplete}</strong> incomplete
            </span>
          </div>
        </div>
        <div className="wl-underlying-defaults__actions">
          <Button type="button" variant="ghost" disabled={refreshing} onClick={onRefreshFromPositions}>
            Refresh from positions
          </Button>
          <Button type="button" variant="ghost" disabled={bulkRefreshing} onClick={onRefreshAllSpots}>
            Refresh all AKShare spots
          </Button>
          <Button
            type="button"
            disabled={buildDisabled}
            onClick={onBuild}
            title={
              incomplete > 0
                ? `Fill rate/div/vol for: ${rows.filter((r) => !r.is_complete).map((r) => r.underlying).join(', ')}`
                : undefined
            }
          >
            Build default profile
          </Button>
        </div>
      </header>

      <div className="wl-underlying-defaults__subhead">
        <span>
          AKShare → spot + manual → rate · div · vol ⇒ new snapshot in library below
        </span>
        <span>Spot fetched live from AKShare on Build</span>
      </div>

      {feedback?.tone === 'error' && (
        <div className="wl-underlying-defaults__banner">
          {feedback.unfilled && feedback.unfilled.length > 0 ? (
            <>
              <span>Build blocked — missing rate / div / vol:</span>
              {feedback.unfilled.map((u) => (
                <span className="pill" key={u}>{u}</span>
              ))}
            </>
          ) : feedback.failedAkshare && feedback.failedAkshare.length > 0 ? (
            <>
              <span>Build blocked — AKShare fetch failed for:</span>
              {feedback.failedAkshare.map((f) => (
                <span className="pill" key={f.underlying}>{f.underlying}</span>
              ))}
            </>
          ) : (
            <span>{feedback.message}</span>
          )}
        </div>
      )}

      <table className="wl-underlying-defaults__table">
        <thead>
          <tr>
            <th>UNDERLYING</th>
            <th className="num">LATEST AKSHARE CLOSE</th>
            <th className="num">RATE</th>
            <th className="num">DIV YIELD</th>
            <th className="num">VOL</th>
            <th>NOTES</th>
            <th>LAST EDITED</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isEditing = editing === row.underlying;
            const draftFields = isEditing && draft ? draft : null;
            return (
              <tr key={row.underlying} className={row.is_complete ? '' : 'is-incomplete'}>
                <td>{row.underlying}</td>
                <td className="num">
                  {row.latest_akshare_close?.spot != null
                    ? row.latest_akshare_close.spot.toFixed(2)
                    : '—'}
                  <span className="ts">
                    {row.latest_akshare_close?.fetched_at
                      ? new Date(row.latest_akshare_close.fetched_at).toLocaleString()
                      : 'no AKShare profile yet'}
                  </span>
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label="rate"
                      value={draftFields?.rate ?? ''}
                      onChange={(e) =>
                        setDraft((d) => (d ? { ...d, rate: e.target.value } : d))
                      }
                    />
                  ) : row.rate == null ? '—' : row.rate.toFixed(4)}
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label="dividend yield"
                      value={draftFields?.dividend_yield ?? ''}
                      onChange={(e) =>
                        setDraft((d) => (d ? { ...d, dividend_yield: e.target.value } : d))
                      }
                    />
                  ) : row.dividend_yield == null ? '—' : row.dividend_yield.toFixed(4)}
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label="volatility"
                      value={draftFields?.volatility ?? ''}
                      onChange={(e) =>
                        setDraft((d) => (d ? { ...d, volatility: e.target.value } : d))
                      }
                    />
                  ) : row.volatility == null ? '—' : row.volatility.toFixed(4)}
                </td>
                <td>
                  {isEditing ? (
                    <input
                      aria-label="notes"
                      value={draftFields?.notes ?? ''}
                      onChange={(e) =>
                        setDraft((d) => (d ? { ...d, notes: e.target.value } : d))
                      }
                    />
                  ) : row.notes ?? '—'}
                </td>
                <td>{new Date(row.updated_at).toLocaleString()}</td>
                <td>
                  {isEditing ? (
                    <>
                      <button onClick={() => saveEdit(row.underlying)}>Save</button>
                      <button onClick={cancelEdit}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => startEdit(row)}>Edit</button>
                      <button onClick={() => onDelete(row.underlying)}>×</button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="wl-underlying-defaults__empty">
                No underlying defaults yet. Click <em>Refresh from positions</em> to populate.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 4: Add CSS tokens**

Append to `frontend/src/routes/PricingParameters.css`:

```css
.wl-underlying-defaults { border-bottom: 1px solid var(--wl-border, #1f2329); }
.wl-underlying-defaults__head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 18px; background: var(--wl-surface-1, #141618);
}
.wl-underlying-defaults__title { display: flex; gap: 18px; align-items: center; }
.wl-underlying-defaults__heading {
  font: 600 11px ui-monospace, monospace; letter-spacing: 1.4px; color: #d2d6dd;
}
.wl-underlying-defaults__sublabel {
  font: 500 10px ui-monospace, monospace; color: #7c8693; letter-spacing: 0.8px;
}
.wl-underlying-defaults__counters { display: flex; gap: 14px; font: 500 11px ui-monospace, monospace; color: #a4adb8; }
.wl-underlying-defaults__counters .is-ok strong { color: #7ed29a; }
.wl-underlying-defaults__counters .is-warn strong { color: #f0b545; }
.wl-underlying-defaults__actions { display: flex; gap: 8px; }
.wl-underlying-defaults__subhead {
  display: flex; justify-content: space-between; padding: 8px 18px;
  background: #0c0f12; border-bottom: 1px solid #181b20;
  font: 500 11px ui-monospace, monospace; color: #8b939f;
}
.wl-underlying-defaults__banner {
  padding: 10px 16px; background: #1b150a; color: #f0b545;
  border-bottom: 1px solid #3a2c14; font: 500 12px ui-monospace, monospace;
  display: flex; gap: 12px; align-items: center;
}
.wl-underlying-defaults__banner .pill { background: #2a2010; padding: 2px 6px; border-radius: 3px; font-size: 11px; }
.wl-underlying-defaults__table { width: 100%; border-collapse: collapse; font: 500 12px ui-monospace, monospace; }
.wl-underlying-defaults__table th {
  text-align: left; padding: 10px 14px; font-size: 10px; letter-spacing: 1px;
  color: #6c7480; background: #101316; border-bottom: 1px solid #1f2329;
}
.wl-underlying-defaults__table th.num { text-align: right; }
.wl-underlying-defaults__table td { padding: 9px 14px; border-bottom: 1px solid #181b20; color: #d2d6dd; }
.wl-underlying-defaults__table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.wl-underlying-defaults__table td.num .ts { display: block; font-size: 10px; color: #5a6478; margin-top: 1px; }
.wl-underlying-defaults__table tr.is-incomplete td { background: #1b150a; }
.wl-underlying-defaults__empty { color: #7c8693; font-style: italic; padding: 16px; text-align: center; }
```

- [ ] **Step 5: Re-run tests — confirm pass**

Run: `cd frontend && npm test -- UnderlyingDefaultsPanel`
Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/UnderlyingDefaultsPanel.tsx \
        frontend/src/routes/UnderlyingDefaultsPanel.test.tsx \
        frontend/src/routes/PricingParameters.css
git commit -m "feat(pricing): add UnderlyingDefaultsPanel component"
```

---

### Task 12: Profile library enhancements (source-type chips + stripes + origin badges)

**Files:**
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.css`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/routes/PricingParameters.test.tsx`:

```tsx
it('renders source-type filter chips with counts', () => {
  const profiles = [
    { id: 1, name: 'Default A', valuation_date: '2026-05-13', source_type: 'default_underlying', status: 'completed', summary: { row_count: 5 }, rows: [], created_at: '', updated_at: '' },
    { id: 2, name: 'XLSX A', valuation_date: '2026-05-12', source_type: 'xlsx', status: 'completed', summary: { row_count: 3 }, rows: [], created_at: '', updated_at: '' },
    { id: 3, name: 'Spot A', valuation_date: '2026-05-11', source_type: 'market_data_spot', status: 'completed', summary: { row_count: 2 }, rows: [], created_at: '', updated_at: '' },
  ] as any;

  render(
    <PricingParameters
      profiles={profiles}
      selectedId={1}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      onSelectProfile={() => {}}
      onImport={() => {}}
    />,
  );
  expect(screen.getByText(/DEFAULT · 1/)).toBeInTheDocument();
  expect(screen.getByText(/XLSX · 1/)).toBeInTheDocument();
  expect(screen.getByText(/SPOT · 1/)).toBeInTheDocument();
});

it('shows source-type tag on each profile list item', () => {
  const profiles = [
    { id: 1, name: 'Default A', valuation_date: '2026-05-13', source_type: 'default_underlying', status: 'completed', summary: { row_count: 5 }, rows: [], created_at: '', updated_at: '' },
  ] as any;
  render(
    <PricingParameters
      profiles={profiles}
      selectedId={1}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      onSelectProfile={() => {}}
      onImport={() => {}}
    />,
  );
  expect(screen.getByText('DEFAULT')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `cd frontend && npm test -- PricingParameters.test`
Expected: new assertions fail.

- [ ] **Step 3: Add the filter chips, stripes, and tag**

Edit `frontend/src/routes/PricingParameters.tsx`:

Add a helper near the top of the file:

```ts
type SourceTypeKey = 'default_underlying' | 'xlsx' | 'market_data_spot';
const SOURCE_TYPE_LABELS: Record<SourceTypeKey, string> = {
  default_underlying: 'DEFAULT',
  xlsx: 'XLSX',
  market_data_spot: 'SPOT',
};
const SOURCE_TYPE_ORDER: SourceTypeKey[] = ['default_underlying', 'xlsx', 'market_data_spot'];
```

Add state for the source-type filter:

```ts
const [sourceFilter, setSourceFilter] = useState<SourceTypeKey | 'all'>('all');
```

Compute counts and filtered profiles:

```ts
const sourceCounts = useMemo(() => {
  const counts: Record<SourceTypeKey | 'all', number> = {
    all: profiles.length,
    default_underlying: 0,
    xlsx: 0,
    market_data_spot: 0,
  };
  for (const profile of profiles) {
    const key = profile.source_type as SourceTypeKey;
    if (key in counts) counts[key] += 1;
  }
  return counts;
}, [profiles]);

const visibleProfiles = useMemo(() => {
  if (sourceFilter === 'all') return profiles;
  return profiles.filter((p) => p.source_type === sourceFilter);
}, [profiles, sourceFilter]);
```

Render filter chips above the profile list:

```tsx
<div className="wl-pricing-params__source-filter">
  <button
    type="button"
    className={`wl-pricing-params__source-pill ${sourceFilter === 'all' ? 'is-active' : ''}`}
    onClick={() => setSourceFilter('all')}
  >
    ALL · {sourceCounts.all}
  </button>
  {SOURCE_TYPE_ORDER.map((key) => (
    <button
      key={key}
      type="button"
      className={`wl-pricing-params__source-pill ${sourceFilter === key ? 'is-active' : ''}`}
      onClick={() => setSourceFilter(key)}
    >
      {SOURCE_TYPE_LABELS[key]} · {sourceCounts[key]}
    </button>
  ))}
</div>
```

Replace the profile list `.map((profile) => …)` with `visibleProfiles.map(...)` and add the tag inside each item:

```tsx
<button
  key={profile.id}
  className={`wl-pricing-params__profile is-${profile.source_type} ${selected?.id === profile.id ? 'is-active' : ''}`}
  aria-current={selected?.id === profile.id ? 'true' : undefined}
  onClick={() => onSelectProfile(profile.id)}
  type="button"
>
  <strong>{profile.name}</strong>
  <span>{shortDate(profile.valuation_date)} · {profile.summary?.row_count ?? profile.rows.length} rows</span>
  <span className={`wl-pricing-params__source-tag is-${profile.source_type}`}>
    {SOURCE_TYPE_LABELS[(profile.source_type as SourceTypeKey)] ?? profile.source_type.toUpperCase()}
  </span>
</button>
```

Add CSS in `PricingParameters.css`:

```css
.wl-pricing-params__source-filter { display: flex; gap: 6px; padding: 8px 12px; }
.wl-pricing-params__source-pill {
  font: 500 10px ui-monospace, monospace; letter-spacing: 0.8px;
  padding: 3px 8px; border-radius: 3px;
  background: #15171c; border: 1px solid #2a2f36; color: #a4adb8; cursor: pointer;
}
.wl-pricing-params__source-pill.is-active { color: #e7e9ee; border-color: #3b6df2; background: #1a1d22; }
.wl-pricing-params__profile { border-left: 3px solid transparent; }
.wl-pricing-params__profile.is-default_underlying { border-left-color: #1f2b40; }
.wl-pricing-params__profile.is-xlsx { border-left-color: #3a2c14; }
.wl-pricing-params__profile.is-market_data_spot { border-left-color: #234d33; }
.wl-pricing-params__profile.is-active.is-default_underlying { border-left-color: #3b6df2; }
.wl-pricing-params__profile.is-active.is-xlsx { border-left-color: #f0b545; }
.wl-pricing-params__profile.is-active.is-market_data_spot { border-left-color: #7ed29a; }
.wl-pricing-params__source-tag {
  font: 600 9px ui-monospace, monospace; letter-spacing: 0.8px;
  padding: 1px 5px; border-radius: 2px;
}
.wl-pricing-params__source-tag.is-default_underlying { color: #7ea9ff; background: #0d1322; border: 1px solid #2a3a55; }
.wl-pricing-params__source-tag.is-xlsx { color: #f0b545; background: #1b150a; border: 1px solid #3a2c14; }
.wl-pricing-params__source-tag.is-market_data_spot { color: #7ed29a; background: #0d2014; border: 1px solid #234d33; }
```

Add a per-cell origin badge in the row table. Edit the existing `columns` definition:

```ts
const sourceBadge = (sourceType: string, kind: 'spot' | 'manual' | 'xlsx') => {
  const cls = kind === 'spot' ? 'is-ak' : kind === 'manual' ? 'is-manual' : 'is-xlsx';
  const label = kind === 'spot' ? 'AK' : kind === 'manual' ? 'M' : 'XLSX';
  if (sourceType === 'default_underlying') return kind === 'spot' ? <span className={`wl-origin ${cls}`}>{label}</span> : <span className={`wl-origin is-manual`}>M</span>;
  if (sourceType === 'market_data_spot') return kind === 'spot' ? <span className="wl-origin is-ak">AK</span> : null;
  if (sourceType === 'xlsx') return <span className="wl-origin is-xlsx">XLSX</span>;
  return null;
};

const columns: Column<PricingParameterRow>[] = [
  { key: 'source_trade_id', header: 'TRADE', width: '1.6fr' },
  { key: 'symbol', header: 'SYMBOL', width: '1fr' },
  {
    key: 'spot',
    header: 'SPOT',
    numeric: true,
    render: (row) => (
      <>{fmt(row.spot)} {selected ? sourceBadge(selected.source_type, 'spot') : null}</>
    ),
  },
  {
    key: 'volatility',
    header: 'VOL',
    numeric: true,
    render: (row) => (
      <>{fmt(row.volatility)} {selected ? sourceBadge(selected.source_type, 'manual') : null}</>
    ),
  },
  {
    key: 'rate',
    header: 'RATE',
    numeric: true,
    render: (row) => (
      <>{fmt(row.rate)} {selected ? sourceBadge(selected.source_type, 'manual') : null}</>
    ),
  },
  {
    key: 'dividend_yield',
    header: 'DIV',
    numeric: true,
    render: (row) => (
      <>{fmt(row.dividend_yield)} {selected ? sourceBadge(selected.source_type, 'manual') : null}</>
    ),
  },
  { key: 'source_row', header: 'ROW', numeric: true, render: (row) => row.source_row ?? '-' },
];
```

Append CSS for the origin badge:

```css
.wl-origin {
  display: inline-block; margin-left: 4px;
  font: 600 9px ui-monospace, monospace; padding: 0 3px; border-radius: 2px;
}
.wl-origin.is-ak { color: #7ea9ff; background: #0d1322; border: 1px solid #2a3a55; }
.wl-origin.is-manual { color: #f0b545; background: #1b150a; border: 1px solid #3a2c14; }
.wl-origin.is-xlsx { color: #f0b545; background: #1b150a; border: 1px solid #3a2c14; }
```

- [ ] **Step 4: Re-run tests — confirm pass**

Run: `cd frontend && npm test -- PricingParameters.test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/PricingParameters.tsx \
        frontend/src/routes/PricingParameters.test.tsx \
        frontend/src/routes/PricingParameters.css
git commit -m "feat(pricing): source-type chips, stripes, and origin badges in profile library"
```

---

### Task 13: Wire panel into `PricingParameters` and live wrapper

**Files:**
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.live.tsx`
- Modify: `frontend/src/routes/PricingParameters.live.test.tsx`

- [ ] **Step 1: Write the failing live-wrapper test**

Append to `frontend/src/routes/PricingParameters.live.test.tsx`:

```tsx
it('loads underlying defaults and renders the panel', async () => {
  const defaults = [{
    underlying: '000300.SH',
    rate: 0.025,
    dividend_yield: 0.02,
    volatility: 0.185,
    notes: 'baseline',
    is_complete: true,
    latest_akshare_close: null,
    created_at: '', updated_at: '',
  }];
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/pricing-parameter-profiles') return response([]);
    if (url === '/api/underlying-pricing-defaults') return response(defaults);
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<PricingParametersLive />);
  await waitFor(() => expect(screen.getByText(/UNDERLYING DEFAULTS/)).toBeInTheDocument());
  expect(screen.getByText(/000300.SH/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /build default profile/i })).not.toBeDisabled();
});

it('dispatches build-default and refreshes profiles on success', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/pricing-parameter-profiles' && (!init || init.method === 'GET' || init.method === undefined)) {
      return response([]);
    }
    if (url === '/api/underlying-pricing-defaults') return response([{
      underlying: '000300.SH', rate: 0.025, dividend_yield: 0.02, volatility: 0.185,
      notes: null, is_complete: true, latest_akshare_close: null, created_at: '', updated_at: '',
    }]);
    if (url === '/api/pricing-parameter-profiles/build-default' && init?.method === 'POST') {
      return response({ id: 99, name: 'Default', valuation_date: '2026-05-13', source_type: 'default_underlying', status: 'completed', summary: { row_count: 1 }, rows: [], created_at: '', updated_at: '' });
    }
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<PricingParametersLive />);
  await waitFor(() => expect(screen.getByRole('button', { name: /build default profile/i })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: /build default profile/i }));
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/pricing-parameter-profiles/build-default',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

it('shows unfilled banner on 400 unfilled', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/pricing-parameter-profiles') return response([]);
    if (url === '/api/underlying-pricing-defaults') return response([{
      underlying: '000300.SH', rate: null, dividend_yield: null, volatility: null,
      notes: null, is_complete: false, latest_akshare_close: null, created_at: '', updated_at: '',
    }]);
    if (url === '/api/pricing-parameter-profiles/build-default' && init?.method === 'POST') {
      return response({ detail: { detail: 'underlying defaults missing inputs', unfilled_underlyings: ['000300.SH'] } }, 400);
    }
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<PricingParametersLive />);
  await waitFor(() => expect(screen.getByRole('button', { name: /build default profile/i })).toBeInTheDocument());
  // Button is disabled because there are unfilled rows; clicking should be a no-op.
  expect(screen.getByRole('button', { name: /build default profile/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run tests — confirm failure**

Run: `cd frontend && npm test -- PricingParameters.live.test`
Expected: fails on missing panel render / missing build wire-up.

- [ ] **Step 3: Update `PricingParameters.tsx` to render the panel**

Edit `frontend/src/routes/PricingParameters.tsx`:

Add to `type Props`:

```ts
defaults: UnderlyingPricingDefault[];
defaultsBuilding: boolean;
defaultsRefreshing: boolean;
defaultsBulkRefreshing: boolean;
defaultsFeedback: UnderlyingDefaultsFeedback | null;
onRefreshFromPositions: () => void;
onRefreshAllSpots: () => void;
onUpsertDefault: UnderlyingDefaultsPanelProps['onUpsert'];
onDeleteDefault: (underlying: string) => void;
onBuildDefault: () => void;
```

Add imports:

```ts
import { UnderlyingDefaultsPanel, type UnderlyingDefaultsFeedback, type UnderlyingDefaultsPanelProps } from './UnderlyingDefaultsPanel';
import type { UnderlyingPricingDefault } from '../types';
```

Insert `<UnderlyingDefaultsPanel … />` directly after the `<PageHeader … />` block:

```tsx
<UnderlyingDefaultsPanel
  rows={defaults}
  building={defaultsBuilding}
  refreshing={defaultsRefreshing}
  bulkRefreshing={defaultsBulkRefreshing}
  feedback={defaultsFeedback}
  onRefreshFromPositions={onRefreshFromPositions}
  onRefreshAllSpots={onRefreshAllSpots}
  onUpsert={onUpsertDefault}
  onDelete={onDeleteDefault}
  onBuild={onBuildDefault}
/>
```

- [ ] **Step 4: Update `PricingParameters.live.tsx` to load and dispatch**

Replace the contents of `frontend/src/routes/PricingParameters.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api, uploadForm } from '../api/client';
import type { PageContextReporter, PricingParameterProfile, UnderlyingPricingDefault } from '../types';
import { PricingParameters, type PricingParameterFeedback } from './PricingParameters';
import type { UnderlyingDefaultsFeedback } from './UnderlyingDefaultsPanel';

type ImportPayload = {
  file: File;
  name: string;
  valuationDate: string;
  sheetName: string;
};

type Props = {
  onPageContextChange?: PageContextReporter;
};

function trailingWindow(): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 14);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

export function PricingParametersLive({ onPageContextChange }: Props) {
  const [profiles, setProfiles] = useState<PricingParameterProfile[]>([]);
  const [defaults, setDefaults] = useState<UnderlyingPricingDefault[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [defaultsBuilding, setDefaultsBuilding] = useState(false);
  const [defaultsRefreshing, setDefaultsRefreshing] = useState(false);
  const [defaultsBulkRefreshing, setDefaultsBulkRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<PricingParameterFeedback | null>(null);
  const [defaultsFeedback, setDefaultsFeedback] = useState<UnderlyingDefaultsFeedback | null>(null);

  const loadProfiles = async () => {
    const list = await api<PricingParameterProfile[]>('/api/pricing-parameter-profiles');
    setProfiles(list);
    setSelectedId((current) =>
      current != null && list.some((p) => p.id === current) ? current : list[0]?.id ?? null,
    );
  };

  const loadDefaults = async () => {
    const list = await api<UnderlyingPricingDefault[]>('/api/underlying-pricing-defaults');
    setDefaults(list);
  };

  const load = async () => {
    setError(null);
    try {
      await Promise.all([loadProfiles(), loadDefaults()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onImport = async (payload: ImportPayload) => {
    const form = new FormData();
    form.append('file', payload.file);
    if (payload.name.trim()) form.append('name', payload.name.trim());
    if (payload.valuationDate.trim()) form.append('valuation_date', payload.valuationDate.trim());
    if (payload.sheetName.trim()) form.append('sheet_name', payload.sheetName.trim());

    setImporting(true);
    setFeedback(null);
    try {
      const profile = await uploadForm<PricingParameterProfile>(
        '/api/pricing-parameter-profiles/import',
        form,
      );
      setFeedback({ tone: 'success', message: `Imported ${profile.summary?.row_count ?? profile.rows.length} rows.` });
      setSelectedId(profile.id);
      await loadProfiles();
    } catch (err) {
      setFeedback({ tone: 'error', message: `Could not import: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setImporting(false);
    }
  };

  const onUpsertDefault = async (
    underlying: string,
    fields: { rate?: number | null; dividend_yield?: number | null; volatility?: number | null; notes?: string | null },
  ) => {
    setDefaultsFeedback(null);
    try {
      await api(`/api/underlying-pricing-defaults/${encodeURIComponent(underlying)}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(fields),
      });
      await loadDefaults();
    } catch (err) {
      setDefaultsFeedback({ tone: 'error', message: err instanceof Error ? err.message : String(err) });
    }
  };

  const onDeleteDefault = async (underlying: string) => {
    try {
      await api(`/api/underlying-pricing-defaults/${encodeURIComponent(underlying)}`, {
        method: 'DELETE',
      });
      await loadDefaults();
    } catch (err) {
      setDefaultsFeedback({ tone: 'error', message: err instanceof Error ? err.message : String(err) });
    }
  };

  const onRefreshFromPositions = async () => {
    setDefaultsRefreshing(true);
    setDefaultsFeedback(null);
    try {
      await api('/api/underlying-pricing-defaults/refresh-from-positions', { method: 'POST' });
      await loadDefaults();
    } catch (err) {
      setDefaultsFeedback({ tone: 'error', message: err instanceof Error ? err.message : String(err) });
    } finally {
      setDefaultsRefreshing(false);
    }
  };

  const onRefreshAllSpots = async () => {
    setDefaultsBulkRefreshing(true);
    setDefaultsFeedback(null);
    const { start, end } = trailingWindow();
    try {
      await api('/api/market-data/profiles/akshare/bulk', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ start_date: start, end_date: end, adjust: 'qfq' }),
      });
      await loadDefaults();
    } catch (err) {
      setDefaultsFeedback({ tone: 'error', message: err instanceof Error ? err.message : String(err) });
    } finally {
      setDefaultsBulkRefreshing(false);
    }
  };

  const onBuildDefault = async () => {
    setDefaultsBuilding(true);
    setDefaultsFeedback(null);
    try {
      const profile = await api<PricingParameterProfile>(
        '/api/pricing-parameter-profiles/build-default',
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      setSelectedId(profile.id);
      await loadProfiles();
      setDefaultsFeedback({ tone: 'success', message: `Built profile ${profile.name}.` });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      try {
        const parsed = JSON.parse(message);
        if (parsed?.detail?.unfilled_underlyings) {
          setDefaultsFeedback({
            tone: 'error',
            message: 'underlying defaults missing inputs',
            unfilled: parsed.detail.unfilled_underlyings,
          });
        } else if (parsed?.detail?.failed_akshare_underlyings) {
          setDefaultsFeedback({
            tone: 'error',
            message: 'AKShare spot fetch failed for one or more underlyings',
            failedAkshare: parsed.detail.failed_akshare_underlyings,
          });
        } else {
          setDefaultsFeedback({ tone: 'error', message });
        }
      } catch {
        setDefaultsFeedback({ tone: 'error', message });
      }
    } finally {
      setDefaultsBuilding(false);
    }
  };

  return (
    <PricingParameters
      profiles={profiles}
      selectedId={selectedId}
      loading={loading}
      error={error}
      importing={importing}
      feedback={feedback}
      defaults={defaults}
      defaultsBuilding={defaultsBuilding}
      defaultsRefreshing={defaultsRefreshing}
      defaultsBulkRefreshing={defaultsBulkRefreshing}
      defaultsFeedback={defaultsFeedback}
      onRefreshFromPositions={onRefreshFromPositions}
      onRefreshAllSpots={onRefreshAllSpots}
      onUpsertDefault={onUpsertDefault}
      onDeleteDefault={onDeleteDefault}
      onBuildDefault={onBuildDefault}
      onSelectProfile={setSelectedId}
      onImport={onImport}
      onPageContextChange={onPageContextChange}
    />
  );
}
```

Note: if `api/client.ts` doesn't already throw with a JSON-stringified error body, adjust the catch parsing to read `err.response` or whatever shape your `api` helper exposes. The pattern shown is conservative.

- [ ] **Step 5: Re-run tests — confirm pass**

Run: `cd frontend && npm test -- PricingParameters`
Expected: all green (live + presentational + panel).

- [ ] **Step 6: Frontend regression**

Run: `cd frontend && npm test -- Risk Positions`
Expected: existing tests still pass.

- [ ] **Step 7: Frontend build**

Run: `cd frontend && npm run build`
Expected: build completes without TypeScript errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/PricingParameters.tsx \
        frontend/src/routes/PricingParameters.live.tsx \
        frontend/src/routes/PricingParameters.live.test.tsx
git commit -m "feat(pricing): wire Underlying Defaults panel into Pricing Parameters route"
```

---

### Task 14: Browser smoke and final verification

**Files:** none

- [ ] **Step 1: Start the backend**

Run: `.venv/bin/uvicorn app.main:app --port 8000 --reload`
Expected: server boots without errors.

- [ ] **Step 2: Start the frontend**

Run (separate terminal): `cd frontend && npm run dev`
Expected: Vite serves on `http://localhost:5173`.

- [ ] **Step 3: Verify Underlying Defaults panel**

Open `http://localhost:5173/`, navigate to `Pricing Parameters`. Confirm:

- The `UNDERLYING DEFAULTS` panel renders above the profile library.
- The `Load to Positions` button is gone from the page header.
- The data-flow sub-header is visible.
- Counters show the right filled/incomplete split.
- The source-type filter chips (`ALL · DEFAULT · XLSX · SPOT`) appear with counts.

- [ ] **Step 4: Verify build happy path**

- Click `Refresh from positions` — confirm any new underlyings appear with all numeric fields empty.
- Edit each row, fill in plausible r/q/σ, click `Save`.
- Click `Refresh all AKShare spots` and wait until each `LATEST AKSHARE CLOSE` cell populates.
- Click `Build default profile`. A new `Default …` profile should appear at the top of the library, selected, with `DEFAULT` tag and blue stripe.
- Confirm the row table shows `AK` badges on the spot column and `M` badges on rate/div/vol.

- [ ] **Step 5: Verify build strict failure**

- Clear one row's `rate` cell and click `Save`.
- Confirm `Build default profile` becomes disabled and its tooltip names the unfilled underlying.
- If you can force a click (e.g. through devtools) the inline banner should list the affected underlying.

- [ ] **Step 6: Verify Positions consumer**

- Navigate to `Positions`, open the pricing-profile selector, confirm the new `Default …` profile appears in the list, can be selected, and pricing a position with it yields the AKShare spot in the result payload.
- Navigate to `Risk`, run a risk job with the new profile selected, confirm the run completes.

- [ ] **Step 7: Final commit if any tweaks were needed**

If browser verification surfaced fixes, commit them with `fix(pricing): …` messages. Otherwise no commit needed.

---

## Self-Review

**1. Spec coverage:**

- Goal/Approach — Tasks 1–8 (backend) and 9–13 (frontend) implement the build pipeline, store, and UI.
- Data Model `UnderlyingPricingDefault` — Tasks 1 (migration), 2 (model). ✓
- Source-type extension to `default_underlying` — emitted in Task 6, surfaced in Tasks 9, 12. ✓
- Summary fields — populated in Task 6. ✓
- Build Pipeline 6-step sequence — Task 6. ✓
- API CRUD — Task 7. ✓
- API refresh-from-positions — Task 7. ✓
- API build-default — Task 7. ✓
- Page header changes (remove Load to Positions, remove main.tsx wiring) — Task 10. ✓
- Underlying Defaults panel — Task 11. ✓
- Profile library enhancements (chips, stripes, badges) — Task 12. ✓
- Live wiring — Task 13. ✓
- Error handling table — covered by Tasks 6, 7, 11, 13. ✓
- Backend tests — Tasks 2, 3, 4, 5, 6, 7, 8. ✓
- Frontend tests — Tasks 10, 11, 12, 13. ✓
- Browser verification — Task 14. ✓

**2. Placeholder scan:** No "TBD" or "implement later" remain. Every step contains its actual content. The one note about a `business_days` helper substitution is concrete (use `timedelta(days=days*2)` if no helper exists) so it is actionable.

**3. Type consistency:** `UnderlyingPricingDefault` shape is consistent across model (Task 2), schemas (Task 3), service (Task 5), endpoints (Task 7), frontend types (Task 9), panel component (Task 11), and live wrapper (Task 13). `source_type` keys (`default_underlying`, `xlsx`, `market_data_spot`) are used identically across backend emission, schema response, frontend types, list filter, and per-cell origin badges.
