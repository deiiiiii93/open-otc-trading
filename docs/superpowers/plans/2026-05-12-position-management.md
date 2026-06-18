# Position Management: Lifecycle Events & Field Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add position lifecycle event tracking and field editing, restricted to container portfolios.

**Architecture:** A new `position_lifecycle_events` table tracks structured product events (knock-in, knock-out, autocall, etc.) with auto status updates. The frontend gets inline editing in the positions table and a tabbed detail modal (editable form + lifecycle timeline).

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Alembic, React 18, TypeScript, Vitest, Radix UI Tabs

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/app/models.py` | Add `PositionLifecycleEvent` SQLAlchemy model |
| `backend/alembic/versions/0011_position_lifecycle_events.py` | Migration for new table |
| `backend/app/schemas.py` | Add `PositionLifecycleEventIn`, `PositionLifecycleEventOut`; extend `PortfolioPositionSpec` |
| `backend/app/main.py` | New lifecycle event endpoints; container guard on `patch_position` |
| `tests/test_lifecycle_events.py` | Backend integration tests for lifecycle events |
| `frontend/src/types.ts` | Add `PositionLifecycleEvent` TypeScript type |
| `frontend/src/components/PositionEditForm.tsx` | Editable form for all position business fields |
| `frontend/src/components/PositionLifecycleTimeline.tsx` | Timeline of lifecycle events with add-event form |
| `frontend/src/routes/Positions.tsx` | Inline editing + tabbed detail modal |
| `frontend/src/routes/Positions.live.tsx` | API wiring for PATCH and lifecycle endpoints |
| `frontend/src/routes/Positions.test.tsx` | Frontend unit tests for inline editing and tabs |

---

## Task 1: Database Model & Migration

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0011_position_lifecycle_events.py`

- [ ] **Step 1: Add PositionLifecycleEvent model**

In `backend/app/models.py`, after the `Position` class (after line ~217), add:

```python
class PositionLifecycleEvent(Base):
    __tablename__ = "position_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_data: Mapped[dict] = mapped_column(JSON, default=dict)
    old_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="desk_user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    position: Mapped["Position"] = relationship(back_populates="lifecycle_events")
```

Also add the back-reference to `Position`:

```python
# Inside the Position class, add to the relationship declarations:
lifecycle_events: Mapped[list["PositionLifecycleEvent"]] = relationship(
    back_populates="position", cascade="all, delete-orphan", order_by="PositionLifecycleEvent.created_at.desc()"
)
```

- [ ] **Step 2: Create Alembic migration**

Create `backend/alembic/versions/0011_position_lifecycle_events.py`:

```python
"""position lifecycle events

Revision ID: 0011_position_lifecycle_events
Revises: 0010_risk_run_pricing_parameter_profile
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0011_position_lifecycle_events"
down_revision = "0010_risk_run_pricing_parameter_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "position_lifecycle_events" not in set(inspector.get_table_names()):
        op.create_table(
            "position_lifecycle_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("event_data", sa.JSON(), nullable=False),
            sa.Column("old_status", sa.String(length=40), nullable=True),
            sa.Column("new_status", sa.String(length=40), nullable=True),
            sa.Column("actor", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_position_lifecycle_events_position_id", "position_lifecycle_events", ["position_id"])
        op.create_index("ix_position_lifecycle_events_event_type", "position_lifecycle_events", ["event_type"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "position_lifecycle_events" in set(inspector.get_table_names()):
        op.drop_index("ix_position_lifecycle_events_event_type", table_name="position_lifecycle_events")
        op.drop_index("ix_position_lifecycle_events_position_id", table_name="position_lifecycle_events")
        op.drop_table("position_lifecycle_events")
```

- [ ] **Step 3: Run migration**

```bash
cd /Users/fuxinyao/open-otc-trading/backend && alembic upgrade head
```

Expected: `0011_position_lifecycle_events` runs successfully.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0011_position_lifecycle_events.py
git commit -m "feat(db): add position_lifecycle_events table"
```

---

## Task 2: Schemas

**Files:**
- Modify: `backend/app/schemas.py`

- [ ] **Step 1: Add lifecycle event schemas**

After `AuditEventOut` (after line ~635), add:

```python
class PositionLifecycleEventIn(BaseModel):
    event_type: str
    event_data: dict[str, Any] = Field(default_factory=dict)


class PositionLifecycleEventOut(BaseModel):
    id: int
    position_id: int
    event_type: str
    event_data: dict[str, Any]
    old_status: str | None
    new_status: str | None
    actor: str
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Extend PortfolioPositionSpec**

Modify the existing `PortfolioPositionSpec` (line ~247) to include `status` and `source_trade_id`:

```python
class PortfolioPositionSpec(BaseModel):
    underlying: str = "CSI500"
    product_type: str = "EuropeanVanillaOption"
    product_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
        }
    )
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    quantity: float = 1.0
    entry_price: float = 0.0
    status: str = "open"
    source_trade_id: str | None = None
    trade_effective_date: date | datetime | None = None
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(schemas): add PositionLifecycleEvent in/out, extend PortfolioPositionSpec"
```

---

## Task 3: Backend API Endpoints

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add event type registry and status resolver**

At the top of the API endpoint section in `main.py` (near other constants/imports), add:

```python
# Event type registry: maps event_type -> target_status (None means no status change)
LIFECYCLE_EVENT_TARGETS: dict[str, str | None] = {
    # Generic
    "open": "open",
    "close": "closed",
    "reopen": "open",
    # Snowball
    "knock_in": "knocked_in",
    "knock_out": "closed",
    "coupon_observation": None,
    "coupon_paid": None,
    "maturity": "closed",
    # Phoenix
    "autocall": "closed",
    "coupon_lock": None,
    "coupon_paid": None,
    "memory_coupon": None,
    "maturity": "closed",
    # Barrier / Sharkfin
    "knock_in": "knocked_in",
    "knock_out": "closed",
    "maturity": "closed",
    # Custom
    "custom": None,
}

# Product-type filtering for available events
PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    "SnowballOption": {"close", "knock_in", "knock_out", "coupon_observation", "coupon_paid", "maturity", "custom"},
    "PhoenixOption": {"close", "autocall", "coupon_lock", "coupon_paid", "memory_coupon", "maturity", "custom"},
    "BarrierOption": {"close", "knock_in", "knock_out", "maturity", "custom"},
    "SingleSharkfinOption": {"close", "knock_in", "knock_out", "maturity", "custom"},
    "DoubleSharkfinOption": {"close", "knock_in", "knock_out", "maturity", "custom"},
}


def _valid_lifecycle_event_types(product_type: str) -> set[str]:
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, {"close", "custom"})
```

- [ ] **Step 2: Add lifecycle event endpoints**

After the existing `patch_position` endpoint (after line ~1506), add:

```python
    @app.get(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events",
        response_model=list[PositionLifecycleEventOut],
    )
    def list_position_lifecycle_events(
        portfolio_id: int,
        position_id: int,
        session: Session = Depends(get_db),
    ):
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")
        return (
            session.query(PositionLifecycleEvent)
            .filter_by(position_id=position_id)
            .order_by(PositionLifecycleEvent.created_at.desc())
            .all()
        )

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events",
        response_model=PositionLifecycleEventOut,
    )
    def create_position_lifecycle_event(
        portfolio_id: int,
        position_id: int,
        payload: PositionLifecycleEventIn,
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if portfolio.kind != PortfolioKind.CONTAINER.value:
            raise HTTPException(
                status_code=400,
                detail="Position management is only available for container portfolios",
            )
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")

        valid_types = _valid_lifecycle_event_types(position.product_type)
        if payload.event_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event type '{payload.event_type}'. Valid types: {sorted(valid_types)}",
            )

        old_status = position.status
        target_status = LIFECYCLE_EVENT_TARGETS.get(payload.event_type)
        new_status = target_status if target_status is not None else old_status

        event = PositionLifecycleEvent(
            position_id=position_id,
            event_type=payload.event_type,
            event_data=payload.event_data,
            old_status=old_status,
            new_status=new_status if target_status is not None else None,
            actor="desk_user",
        )
        session.add(event)

        if target_status is not None:
            position.status = target_status

        portfolio.updated_at = datetime.utcnow()
        record_audit(
            session,
            event_type="position.lifecycle_event",
            actor="desk_user",
            subject_type="position",
            subject_id=str(position.id),
            payload={
                "event_type": payload.event_type,
                "old_status": old_status,
                "new_status": new_status,
                "event_data": payload.event_data,
            },
        )
        session.commit()
        return event
```

- [ ] **Step 3: Add container guard to patch_position**

Modify the existing `patch_position` endpoint to add the container check at the top:

```python
    def patch_position(
        portfolio_id: int,
        position_id: int,
        payload: PortfolioPositionSpec,
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if portfolio.kind != PortfolioKind.CONTAINER.value:
            raise HTTPException(
                status_code=400,
                detail="Position management is only available for container portfolios",
            )
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")
        for key, value in payload.model_dump(mode="json").items():
            setattr(position, key, value)
        portfolio.updated_at = datetime.utcnow()
        record_audit(
            session,
            event_type="position.updated",
            actor="desk_user",
            subject_type="position",
            subject_id=position.id,
            payload=payload.model_dump(mode="json"),
        )
        session.commit()
        return _portfolio_response(session, portfolio)
```

- [ ] **Step 4: Verify imports**

Ensure the imports at the top of `main.py` include the new schemas. In the imports section (around line 50), add:

```python
from app.schemas import (
    # ... existing imports ...
    PositionLifecycleEventIn,
    PositionLifecycleEventOut,
)
```

Also ensure `PositionLifecycleEvent` is imported from models in the `get_db` context or at module level. Find where `Position` is imported from `.models` and add `PositionLifecycleEvent`:

```python
from .models import (
    # ... existing imports ...
    PositionLifecycleEvent,
)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(api): position lifecycle events endpoints + container guard"
```

---

## Task 4: Backend Tests

**Files:**
- Create: `tests/test_lifecycle_events.py`

- [ ] **Step 1: Write the test file**

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app import database
from app.models import Portfolio, Position, PositionLifecycleEvent


def _test_settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    return Settings(
        database_url=f"sqlite:///{db_path}",
        secret_key="test-secret",
        agent_channels_path=tmp_path / "channels.yaml",
    )


def make_client(tmp_path: Path) -> TestClient:
    channels_file = tmp_path / "channels.yaml"
    channels_file.write_text(
        """
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: TEST_ZENMUX_KEY
    base_url: https://zenmux.test/api/v1
    models:
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
"""
    )
    settings = _test_settings(tmp_path)
    database.Base.metadata.create_all(bind=database.engine)
    app = create_app(settings=settings)
    return TestClient(app)


def test_create_lifecycle_event_updates_status(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1},
    ).json()
    assert position["status"] == "open"

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {"barrier_level": 4500}},
    )
    assert event.status_code == 200
    data = event.json()
    assert data["event_type"] == "knock_in"
    assert data["old_status"] == "open"
    assert data["new_status"] == "knocked_in"

    # Verify position status updated
    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "knocked_in"


def test_knock_out_closes_position(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1, "status": "knocked_in"},
    ).json()

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {"barrier_level": 4500}},
    )
    assert event.status_code == 200
    assert event.json()["new_status"] == "closed"

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "closed"


def test_informational_event_does_not_change_status(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1},
    ).json()

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "coupon_observation", "event_data": {"observation_date": "2026-06-01"}},
    )
    assert event.status_code == 200
    data = event.json()
    assert data["new_status"] is None

    refreshed = client.get(f"/api/portfolios/{portfolio['id']}").json()
    pos = next(p for p in refreshed["positions"] if p["id"] == position["id"])
    assert pos["status"] == "open"


def test_lifecycle_event_rejected_for_view_portfolio(tmp_path: Path):
    client = make_client(tmp_path)

    view = client.post("/api/portfolios", json={"name": "Snowball View", "kind": "view"}).json()
    position = client.post(
        f"/api/portfolios/{view['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1},
    ).json()

    event = client.post(
        f"/api/portfolios/{view['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )
    assert event.status_code == 400
    assert "container portfolios" in event.json()["detail"]


def test_invalid_event_type_rejected(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1},
    ).json()

    event = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "autocall", "event_data": {}},
    )
    assert event.status_code == 400
    assert "Invalid event type" in event.json()["detail"]


def test_list_lifecycle_events_ordered_by_date(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post("/api/portfolios", json={"name": "Desk-Q2", "kind": "container"}).json()
    position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={"underlying": "CSI500", "product_type": "SnowballOption", "quantity": 1},
    ).json()

    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_in", "event_data": {}},
    )
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events",
        json={"event_type": "knock_out", "event_data": {}},
    )

    events = client.get(
        f"/api/portfolios/{portfolio['id']}/positions/{position['id']}/lifecycle-events"
    )
    assert events.status_code == 200
    data = events.json()
    assert len(data) == 2
    assert data[0]["event_type"] == "knock_out"  # newest first
    assert data[1]["event_type"] == "knock_in"


def test_patch_position_rejects_view_portfolio(tmp_path: Path):
    client = make_client(tmp_path)

    view = client.post("/api/portfolios", json={"name": "View-1", "kind": "view"}).json()
    position = client.post(
        f"/api/portfolios/{view['id']}/positions",
        json={"underlying": "CSI500", "product_type": "EuropeanVanillaOption", "quantity": 1},
    ).json()

    patched = client.patch(
        f"/api/portfolios/{view['id']}/positions/{position['id']}",
        json={"quantity": 2},
    )
    assert patched.status_code == 400
    assert "container portfolios" in patched.json()["detail"]
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/fuxinyao/open-otc-trading && pytest tests/test_lifecycle_events.py -v
```

Expected: All 7 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_lifecycle_events.py
git commit -m "test(lifecycle): position lifecycle event CRUD + container guard"
```

---

## Task 5: Frontend Types

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add PositionLifecycleEvent type**

After the `Position` type definition (after line ~163), add:

```typescript
export type PositionLifecycleEvent = {
  id: number;
  position_id: number;
  event_type: string;
  event_data: Record<string, unknown>;
  old_status: string | null;
  new_status: string | null;
  actor: string;
  created_at: string;
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(types): add PositionLifecycleEvent type"
```

---

## Task 6: Frontend Inline Editing

**Files:**
- Modify: `frontend/src/routes/Positions.tsx`

- [ ] **Step 1: Add inline editing callbacks and state**

Add these new props to the `Props` type (after `onPageContextChange`):

```typescript
  onEditPosition?: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  editingPositionId: number | null;
```

Add them to the destructured props in `Positions` component.

- [ ] **Step 2: Add inline editing state management**

Inside `Positions`, after the existing `useState` declarations, add:

```typescript
  const [editingCell, setEditingCell] = useState<{ rowId: number; key: keyof PositionRow } | null>(null);
  const [editValue, setEditValue] = useState<string>('');
```

- [ ] **Step 3: Create inline cell renderer**

Add a helper function before the `columns` definition:

```typescript
  const handleCellClick = (row: PositionRow, key: 'quantity' | 'entry_price' | 'status') => {
    if (!onEditPosition || portfolioKind !== 'container') return;
    setEditingCell({ rowId: row.id, key });
    setEditValue(String(row[key] ?? ''));
  };

  const handleCellBlur = async () => {
    if (!editingCell || !onEditPosition) return;
    const { rowId, key } = editingCell;
    const row = rows.find((r) => r.id === rowId);
    if (!row) return;

    const currentValue = row[key];
    let parsedValue: number | string = editValue;
    if (key === 'quantity' || key === 'entry_price') {
      parsedValue = Number(editValue);
      if (!Number.isFinite(parsedValue)) {
        setEditingCell(null);
        return;
      }
    }

    if (parsedValue !== currentValue) {
      await onEditPosition(row, { [key]: parsedValue });
    }
    setEditingCell(null);
  };

  const handleCellKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      void handleCellBlur();
    } else if (event.key === 'Escape') {
      setEditingCell(null);
    }
  };
```

- [ ] **Step 4: Update column definitions for inline editing**

Replace the quantity, price, and add a status column in the `columns` array:

```typescript
  const columns: Column<PositionRow>[] = [
    { key: 'trade_id', header: 'TRADE', width: '1.6fr' },
    { key: 'underlying', header: 'UNDER', width: '1fr' },
    { key: 'product_type', header: 'TYPE', width: '1.3fr' },
    {
      key: 'quantity',
      header: 'QTY',
      width: '0.7fr',
      numeric: true,
      render: (r) => {
        if (editingCell?.rowId === r.id && editingCell.key === 'quantity') {
          return (
            <input
              type="number"
              step="any"
              value={editValue}
              autoFocus
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleCellBlur}
              onKeyDown={handleCellKeyDown}
              className="wl-positions__inline-input"
            />
          );
        }
        return (
          <span
            className={portfolioKind === 'container' ? 'wl-positions__editable-cell' : ''}
            onClick={() => handleCellClick(r, 'quantity')}
          >
            {formatSigned(r.quantity, 0)}
          </span>
        );
      },
    },
    { key: 'price', header: 'PRICE', width: '0.8fr', numeric: true, render: (r) => formatNullableNumber(r.price, 3) },
    { key: 'pnl', header: 'P&L', width: '0.8fr', numeric: true, render: (r) => formatNullableSigned(r.pnl, 2) },
    {
      key: 'status',
      header: 'STATUS',
      width: '0.8fr',
      render: (r) => {
        if (editingCell?.rowId === r.id && editingCell.key === 'status') {
          return (
            <select
              value={editValue}
              autoFocus
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleCellBlur}
              onKeyDown={handleCellKeyDown}
              className="wl-positions__inline-select"
            >
              <option value="open">open</option>
              <option value="knocked_in">knocked_in</option>
              <option value="closed">closed</option>
            </select>
          );
        }
        return (
          <span
            className={portfolioKind === 'container' ? 'wl-positions__editable-cell' : ''}
            onClick={() => handleCellClick(r, 'status')}
          >
            {r.status}
          </span>
        );
      },
    },
    { key: 'mapping_status', header: 'MAP', width: '0.7fr', render: (r) => r.pricing_error ? 'pricing error' : r.mapping_status },
  ];
```

- [ ] **Step 5: Add CSS for editable cells**

In `frontend/src/routes/Positions.css`, add:

```css
.wl-positions__editable-cell {
  cursor: pointer;
  border-bottom: 1px dashed var(--color-border, #ccc);
}
.wl-positions__editable-cell:hover {
  border-bottom-color: var(--color-primary, #007acc);
}
.wl-positions__inline-input,
.wl-positions__inline-select {
  width: 100%;
  padding: 2px 4px;
  font: inherit;
  border: 1px solid var(--color-primary, #007acc);
  border-radius: 2px;
  background: var(--color-surface, #fff);
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Positions.tsx frontend/src/routes/Positions.css
git commit -m "feat(ui): inline editing for quantity, entry_price, status"
```

---

## Task 7: Position Edit Form Component

**Files:**
- Create: `frontend/src/components/PositionEditForm.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useState, type FormEvent } from 'react';
import type { PositionRow } from '../routes/Positions';
import { Button } from './Button';

const PRODUCT_TYPES = [
  'EuropeanVanillaOption',
  'AmericanOption',
  'CashOrNothingDigitalOption',
  'BarrierOption',
  'SingleSharkfinOption',
  'DoubleSharkfinOption',
  'SnowballOption',
  'PhoenixOption',
  'AsianOption',
];

const ENGINE_OPTIONS: Record<string, string[]> = {
  EuropeanVanillaOption: ['BlackScholesEngine', 'EuropeanMCEngine', 'EuropeanQuadEngine', 'PDEEngine'],
  BarrierOption: ['BarrierAnalyticalEngine', 'BarrierOptionMCEngine', 'BarrierQuadEngine', 'PDEEngine'],
  SnowballOption: ['SnowballQuadEngine', 'SnowballMCEngine', 'PDEEngine', 'KOResetSnowballQuadEngine'],
  PhoenixOption: ['PhoenixQuadEngine', 'PhoenixMCEngine', 'PDEEngine'],
  CashOrNothingDigitalOption: ['DigitalOptionAnalyticalEngine', 'DigitalOptionMCEngine'],
  AsianOption: ['AsianOptionAnalyticalEngine', 'AsianOptionMCEngine'],
};

type Props = {
  row: PositionRow;
  onSave: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  saving: boolean;
};

export function PositionEditForm({ row, onSave, saving }: Props) {
  const [form, setForm] = useState({
    underlying: row.underlying,
    product_type: row.product_type,
    quantity: String(row.quantity),
    entry_price: String(row.entry_price ?? 0),
    status: row.status,
    source_trade_id: row.trade_id ?? '',
    engine_name: row.engine_name ?? '',
    product_kwargs: JSON.stringify(row.product_kwargs ?? {}, null, 2),
    engine_kwargs: JSON.stringify(row.engine_kwargs ?? {}, null, 2),
  });
  const [error, setError] = useState<string | null>(null);

  const engineOptions = ENGINE_OPTIONS[row.product_type] ?? [];

  const update = (key: keyof typeof form, value: string) => {
    setForm((f) => ({ ...f, [key]: value }));
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    let product_kwargs: Record<string, unknown>;
    let engine_kwargs: Record<string, unknown>;
    try {
      product_kwargs = JSON.parse(form.product_kwargs);
    } catch {
      setError('Product terms JSON is invalid');
      return;
    }
    try {
      engine_kwargs = JSON.parse(form.engine_kwargs);
    } catch {
      setError('Engine kwargs JSON is invalid');
      return;
    }

    const quantity = Number(form.quantity);
    const entry_price = Number(form.entry_price);
    if (!Number.isFinite(quantity)) {
      setError('Quantity must be a valid number');
      return;
    }
    if (!Number.isFinite(entry_price)) {
      setError('Entry price must be a valid number');
      return;
    }

    await onSave(row, {
      underlying: form.underlying,
      product_type: form.product_type,
      quantity,
      entry_price,
      status: form.status,
      trade_id: form.source_trade_id || undefined,
      engine_name: form.engine_name || undefined,
      product_kwargs,
      engine_kwargs,
    });
  };

  return (
    <form className="wl-positions__edit-form" onSubmit={handleSubmit}>
      <div className="wl-positions__edit-grid">
        <label className="wl-positions__term-field">
          <span>Underlying</span>
          <input value={form.underlying} onChange={(e) => update('underlying', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Product Type</span>
          <select value={form.product_type} onChange={(e) => update('product_type', e.target.value)}>
            {PRODUCT_TYPES.map((pt) => (
              <option key={pt} value={pt}>{pt}</option>
            ))}
          </select>
        </label>
        <label className="wl-positions__term-field">
          <span>Quantity</span>
          <input type="number" step="any" value={form.quantity} onChange={(e) => update('quantity', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Entry Price</span>
          <input type="number" step="any" value={form.entry_price} onChange={(e) => update('entry_price', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Status</span>
          <select value={form.status} onChange={(e) => update('status', e.target.value)}>
            <option value="open">open</option>
            <option value="knocked_in">knocked_in</option>
            <option value="closed">closed</option>
          </select>
        </label>
        <label className="wl-positions__term-field">
          <span>Trade ID</span>
          <input value={form.source_trade_id} onChange={(e) => update('source_trade_id', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Engine</span>
          <select value={form.engine_name} onChange={(e) => update('engine_name', e.target.value)}>
            <option value="">—</option>
            {engineOptions.map((eng) => (
              <option key={eng} value={eng}>{eng}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="wl-positions__term-field wl-positions__term-field--wide">
        <span>Product Terms (JSON)</span>
        <textarea
          value={form.product_kwargs}
          onChange={(e) => update('product_kwargs', e.target.value)}
          rows={6}
        />
      </label>
      <label className="wl-positions__term-field wl-positions__term-field--wide">
        <span>Engine Kwargs (JSON)</span>
        <textarea
          value={form.engine_kwargs}
          onChange={(e) => update('engine_kwargs', e.target.value)}
          rows={4}
        />
      </label>
      {error && <div className="wl-positions__ticket-error" role="alert">{error}</div>}
      <div className="wl-positions__edit-actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? 'Saving...' : 'Save Changes'}
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/PositionEditForm.tsx
git commit -m "feat(ui): PositionEditForm component"
```

---

## Task 8: Lifecycle Timeline Component

**Files:**
- Create: `frontend/src/components/PositionLifecycleTimeline.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import type { PositionLifecycleEvent } from '../types';
import type { PositionRow } from '../routes/Positions';
import { Button } from './Button';

const PRODUCT_EVENTS: Record<string, string[]> = {
  SnowballOption: ['close', 'knock_in', 'knock_out', 'coupon_observation', 'coupon_paid', 'maturity', 'custom'],
  PhoenixOption: ['close', 'autocall', 'coupon_lock', 'coupon_paid', 'memory_coupon', 'maturity', 'custom'],
  BarrierOption: ['close', 'knock_in', 'knock_out', 'maturity', 'custom'],
  SingleSharkfinOption: ['close', 'knock_in', 'knock_out', 'maturity', 'custom'],
  DoubleSharkfinOption: ['close', 'knock_in', 'knock_out', 'maturity', 'custom'],
};

type Props = {
  row: PositionRow;
  events: PositionLifecycleEvent[];
  onAddEvent: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  adding: boolean;
};

export function PositionLifecycleTimeline({ row, events, onAddEvent, adding }: Props) {
  const [showForm, setShowForm] = useState(false);
  const [eventType, setEventType] = useState('');
  const [eventDataJson, setEventDataJson] = useState('{}');
  const [formError, setFormError] = useState<string | null>(null);

  const availableEvents = useMemo(() => PRODUCT_EVENTS[row.product_type] ?? ['close', 'custom'], [row.product_type]);

  const handleSubmit = async () => {
    setFormError(null);
    let eventData: Record<string, unknown>;
    try {
      eventData = JSON.parse(eventDataJson);
    } catch {
      setFormError('Event data must be valid JSON');
      return;
    }
    if (!eventType) {
      setFormError('Please select an event type');
      return;
    }
    await onAddEvent(row, eventType, eventData);
    setShowForm(false);
    setEventType('');
    setEventDataJson('{}');
  };

  return (
    <div className="wl-positions__lifecycle">
      <div className="wl-positions__lifecycle-header">
        <h4>Lifecycle Events</h4>
        <Button type="button" variant="ghost" onClick={() => setShowForm((s) => !s)} disabled={adding}>
          <Plus size={14} />
          {showForm ? 'Cancel' : 'Add Event'}
        </Button>
      </div>

      {showForm && (
        <div className="wl-positions__lifecycle-form">
          <label className="wl-positions__term-field">
            <span>Event Type</span>
            <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
              <option value="">Select...</option>
              {availableEvents.map((et) => (
                <option key={et} value={et}>{et}</option>
              ))}
            </select>
          </label>
          <label className="wl-positions__term-field wl-positions__term-field--wide">
            <span>Event Data (JSON)</span>
            <textarea
              value={eventDataJson}
              onChange={(e) => setEventDataJson(e.target.value)}
              rows={3}
            />
          </label>
          {formError && <div className="wl-positions__ticket-error">{formError}</div>}
          <Button type="button" variant="primary" onClick={handleSubmit} disabled={adding}>
            {adding ? 'Adding...' : 'Add Event'}
          </Button>
        </div>
      )}

      {events.length === 0 ? (
        <div className="wl-positions__lifecycle-empty">No lifecycle events recorded.</div>
      ) : (
        <ul className="wl-positions__lifecycle-list">
          {events.map((event) => (
            <li key={event.id} className="wl-positions__lifecycle-item">
              <div className="wl-positions__lifecycle-meta">
                <span className={`wl-positions__lifecycle-badge wl-positions__lifecycle-badge--${event.event_type}`}>
                  {event.event_type}
                </span>
                {event.old_status != null && event.new_status != null && (
                  <span className="wl-positions__lifecycle-transition">
                    {event.old_status} → {event.new_status}
                  </span>
                )}
                <time>{new Date(event.created_at).toLocaleString()}</time>
              </div>
              {Object.keys(event.event_data).length > 0 && (
                <pre className="wl-positions__lifecycle-data">
                  {JSON.stringify(event.event_data, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for timeline**

In `frontend/src/routes/Positions.css`, add:

```css
.wl-positions__lifecycle {
  margin-top: 16px;
}
.wl-positions__lifecycle-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.wl-positions__lifecycle-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.wl-positions__lifecycle-form {
  background: var(--color-surface-raised, #f5f5f5);
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
}
.wl-positions__lifecycle-empty {
  color: var(--color-text-muted, #888);
  font-size: 13px;
  padding: 16px;
  text-align: center;
}
.wl-positions__lifecycle-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.wl-positions__lifecycle-item {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border, #e5e5e5);
}
.wl-positions__lifecycle-item:last-child {
  border-bottom: none;
}
.wl-positions__lifecycle-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}
.wl-positions__lifecycle-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  text-transform: uppercase;
  background: var(--color-primary-subtle, #e3f2fd);
  color: var(--color-primary, #1976d2);
}
.wl-positions__lifecycle-badge--knock_out,
.wl-positions__lifecycle-badge--close {
  background: var(--color-error-subtle, #ffebee);
  color: var(--color-error, #c62828);
}
.wl-positions__lifecycle-badge--knock_in {
  background: var(--color-warning-subtle, #fff3e0);
  color: var(--color-warning, #ef6c00);
}
.wl-positions__lifecycle-transition {
  color: var(--color-text-muted, #666);
  font-size: 12px;
}
.wl-positions__lifecycle-data {
  margin: 6px 0 0;
  padding: 6px 8px;
  background: var(--color-surface-raised, #f5f5f5);
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PositionLifecycleTimeline.tsx frontend/src/routes/Positions.css
git commit -m "feat(ui): PositionLifecycleTimeline component"
```

---

## Task 9: Detail Modal Tabs

**Files:**
- Modify: `frontend/src/routes/Positions.tsx`

- [ ] **Step 1: Add tab state and new props**

Add to `Props`:

```typescript
  lifecycleEvents?: PositionLifecycleEvent[];
  onAddLifecycleEvent?: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  addingLifecycleEvent: boolean;
```

Import the new components at the top:

```typescript
import { PositionEditForm } from '../components/PositionEditForm';
import { PositionLifecycleTimeline } from '../components/PositionLifecycleTimeline';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/Tabs';
```

Also import `PositionLifecycleEvent` from types.

- [ ] **Step 2: Replace PositionDetail with tabbed version**

Replace the existing `PositionDetail` function (lines ~525-572) with:

```typescript
function PositionDetail({
  row,
  onPricePosition,
  pricing,
  selectedPricingProfile,
  onSave,
  saving,
  lifecycleEvents,
  onAddLifecycleEvent,
  addingLifecycleEvent,
}: {
  row: PositionRow;
  onPricePosition: (row: PositionRow, request: PositionPricingRequest) => void | Promise<void>;
  pricing: boolean;
  selectedPricingProfile: PricingProfileOption | null;
  onSave?: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  saving: boolean;
  lifecycleEvents?: PositionLifecycleEvent[];
  onAddLifecycleEvent?: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  addingLifecycleEvent: boolean;
}) {
  const pricingParameterRow = useMemo(
    () => pricingParameterRowForPosition(row, selectedPricingProfile),
    [row.trade_id, row.underlying, selectedPricingProfile],
  );

  return (
    <Tabs defaultValue="details">
      <TabsList>
        <TabsTrigger value="details">Details</TabsTrigger>
        <TabsTrigger value="lifecycle">Lifecycle</TabsTrigger>
        <TabsTrigger value="pricing">Pricing</TabsTrigger>
      </TabsList>

      <TabsContent value="details">
        <div className="wl-positions__detail">
          {onSave && (
            <PositionEditForm row={row} onSave={onSave} saving={saving} />
          )}
          <dl className="wl-positions__detail-grid">
            <DetailItem label="Trade" value={row.trade_id} />
            <DetailItem label="Position" value={`#${row.id}`} />
            <DetailItem label="Underlying" value={row.underlying} />
            <DetailItem label="Product" value={row.product_type} />
            <DetailItem label="Engine" value={row.engine_name ?? '—'} />
            <DetailItem label="Mapping" value={row.mapping_status} />
          </dl>
          {(row.mapping_error || row.pricing_error) && (
            <div className="wl-positions__error">
              {row.mapping_error || row.pricing_error}
            </div>
          )}
          <PositionGreeks row={row} />
        </div>
      </TabsContent>

      <TabsContent value="lifecycle">
        <PositionLifecycleTimeline
          row={row}
          events={lifecycleEvents ?? []}
          onAddEvent={onAddLifecycleEvent ?? (() => {})}
          adding={addingLifecycleEvent}
        />
      </TabsContent>

      <TabsContent value="pricing">
        <div className="wl-positions__detail">
          <PricingTicket
            row={row}
            onPricePosition={onPricePosition}
            pricing={pricing}
            selectedPricingProfile={selectedPricingProfile}
            pricingParameterRow={pricingParameterRow}
          />
          <ReadonlyObjectForm title="Product Terms" value={row.product_kwargs ?? {}} idPrefix={`product-${row.id}`} />
          <ReadonlyObjectForm title="Market Inputs" value={row.market_inputs ?? {}} idPrefix={`market-${row.id}`} />
        </div>
      </TabsContent>
    </Tabs>
  );
}
```

- [ ] **Step 3: Update Modal rendering**

In the Modal that renders `PositionDetail`, pass the new props:

```tsx
<PositionDetail
  row={selectedRow}
  onPricePosition={onPricePosition}
  pricing={pricingPositionId === selectedRow.id}
  selectedPricingProfile={selectedPricingProfile}
  onSave={portfolioKind === 'container' ? onEditPosition : undefined}
  saving={editingPositionId === selectedRow.id}
  lifecycleEvents={lifecycleEvents?.filter((e) => e.position_id === selectedRow.id)}
  onAddLifecycleEvent={onAddLifecycleEvent}
  addingLifecycleEvent={addingLifecycleEvent}
/>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Positions.tsx
git commit -m "feat(ui): tabbed position detail modal with edit and lifecycle"
```

---

## Task 10: Frontend Live API Wiring

**Files:**
- Modify: `frontend/src/routes/Positions.live.tsx`

- [ ] **Step 1: Add state for lifecycle events and editing**

Add to the state declarations:

```typescript
  const [lifecycleEvents, setLifecycleEvents] = useState<PositionLifecycleEvent[]>([]);
  const [editingPositionId, setEditingPositionId] = useState<number | null>(null);
  const [addingLifecycleEvent, setAddingLifecycleEvent] = useState(false);
```

- [ ] **Step 2: Load lifecycle events when portfolio changes**

In the `load` function, after loading runs and market inputs, add:

```typescript
      // Load lifecycle events for all positions
      const positionIds = (desk.positions ?? []).map((p) => p.id);
      const lifecycleEventsList: PositionLifecycleEvent[] = [];
      for (const positionId of positionIds) {
        try {
          const events = await api<PositionLifecycleEvent[]>(
            `/api/portfolios/${desk.id}/positions/${positionId}/lifecycle-events`
          );
          lifecycleEventsList.push(...events);
        } catch {
          // Skip positions without lifecycle events
        }
      }
      setLifecycleEvents(lifecycleEventsList);
```

- [ ] **Step 3: Add edit handler**

Add handler function:

```typescript
  const handleEditPosition = async (row: PositionRow, updates: Partial<PositionRow>) => {
    if (!portfolio || portfolio.kind !== 'container') return;
    setEditingPositionId(row.id);
    try {
      const patchBody: Record<string, unknown> = {};
      if (updates.underlying !== undefined) patchBody.underlying = updates.underlying;
      if (updates.product_type !== undefined) patchBody.product_type = updates.product_type;
      if (updates.quantity !== undefined) patchBody.quantity = updates.quantity;
      if (updates.entry_price !== undefined) patchBody.entry_price = updates.entry_price;
      if (updates.status !== undefined) patchBody.status = updates.status;
      if (updates.trade_id !== undefined) patchBody.source_trade_id = updates.trade_id;
      if (updates.engine_name !== undefined) patchBody.engine_name = updates.engine_name;
      if (updates.product_kwargs !== undefined) patchBody.product_kwargs = updates.product_kwargs;
      if (updates.engine_kwargs !== undefined) patchBody.engine_kwargs = updates.engine_kwargs;

      await api(`/api/portfolios/${portfolio.id}/positions/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify(patchBody),
      });
      setFeedback(`Updated ${row.trade_id}`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not update ${row.trade_id}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEditingPositionId(null);
    }
  };
```

- [ ] **Step 4: Add lifecycle event handler**

Add handler function:

```typescript
  const handleAddLifecycleEvent = async (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => {
    if (!portfolio || portfolio.kind !== 'container') return;
    setAddingLifecycleEvent(true);
    try {
      await api(`/api/portfolios/${portfolio.id}/positions/${row.id}/lifecycle-events`, {
        method: 'POST',
        body: JSON.stringify({ event_type: eventType, event_data: eventData }),
      });
      setFeedback(`Added ${eventType} event to ${row.trade_id}`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not add event: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAddingLifecycleEvent(false);
    }
  };
```

- [ ] **Step 5: Pass new props to Positions component**

Update the `<Positions ... />` JSX to include:

```tsx
<Positions
  // ... existing props ...
  onEditPosition={handleEditPosition}
  editingPositionId={editingPositionId}
  lifecycleEvents={lifecycleEvents}
  onAddLifecycleEvent={handleAddLifecycleEvent}
  addingLifecycleEvent={addingLifecycleEvent}
/>
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Positions.live.tsx
git commit -m "feat(ui): wire up position editing and lifecycle events API"
```

---

## Task 11: Frontend Tests

**Files:**
- Modify: `frontend/src/routes/Positions.test.tsx`

- [ ] **Step 1: Add inline editing tests**

Add to `Positions.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Positions } from './Positions';

describe('Positions inline editing', () => {
  const baseProps = {
    portfolios: [{ id: 1, name: 'Desk-Q2', kind: 'container' as const }],
    containerPortfolios: [{ id: 1, name: 'Desk-Q2', kind: 'container' as const }],
    selectedPortfolioId: 1,
    importPortfolioId: 1,
    portfolioName: 'Desk-Q2',
    portfolioKind: 'container' as const,
    nav: '1.25M',
    pnl: '+12.50K',
    pnlVariant: 'pos' as const,
    delta: '—',
    deltaVariant: 'default' as const,
    vega: '—',
    valuationDate: '2026-04-30',
    onSelectPortfolio: vi.fn(),
    onSelectImportPortfolio: vi.fn(),
    onRunPricing: vi.fn(),
    onPricePosition: vi.fn(),
    onImportPositions: vi.fn(),
    onEditPosition: vi.fn(),
    editingPositionId: null,
    importingPositions: false,
    pricingPositionId: null,
    importFeedback: null,
    lifecycleEvents: [],
    onAddLifecycleEvent: vi.fn(),
    addingLifecycleEvent: false,
  };

  const positionRows = [
    {
      id: 42,
      trade_id: 'T-SNOWBALL',
      underlying: 'CSI500',
      product_type: 'SnowballOption',
      quantity: -1,
      status: 'open',
      mapping_status: 'supported',
      price: null,
      market_value: null,
      pnl: null,
      delta: null,
      gamma: null,
      vega: null,
      theta: null,
      rho: null,
      rho_q: null,
    },
  ];

  it('renders editable cells for container portfolio', () => {
    render(<Positions {...baseProps} rows={positionRows} />);
    const qtyCell = screen.getByText('-1');
    expect(qtyCell).toHaveClass('wl-positions__editable-cell');
  });

  it('calls onEditPosition when quantity is edited', async () => {
    const user = userEvent.setup();
    render(<Positions {...baseProps} rows={positionRows} />);
    const qtyCell = screen.getByText('-1');
    await user.click(qtyCell);
    const input = screen.getByDisplayValue('-1');
    await user.clear(input);
    await user.type(input, '2');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(baseProps.onEditPosition).toHaveBeenCalled();
    });
  });

  it('does not show editable cells for view portfolio', () => {
    render(<Positions {...baseProps} portfolioKind="view" rows={positionRows} />);
    const qtyCell = screen.getByText('-1');
    expect(qtyCell).not.toHaveClass('wl-positions__editable-cell');
  });
});
```

- [ ] **Step 2: Run tests**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend && npm test -- Positions.test.tsx
```

Expected: Tests pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Positions.test.tsx
git commit -m "test(ui): position inline editing tests"
```

---

## Self-Review

### Spec Coverage Check

| Spec Requirement | Implementing Task |
|---|---|
| New `position_lifecycle_events` table | Task 1 |
| `PositionLifecycleEvent` model with relationships | Task 1 |
| Event type registry with status mapping | Task 3 |
| Product-to-events mapping | Task 3 (backend), Task 8 (frontend) |
| `POST /lifecycle-events` endpoint | Task 3 |
| `GET /lifecycle-events` endpoint | Task 3 |
| Container-only guard on mutations | Task 3 |
| Auto-update position status from events | Task 3 |
| Extend `PortfolioPositionSpec` | Task 2 |
| Frontend inline editing | Task 6 |
| Tabbed detail modal | Task 9 |
| Editable position form | Task 7 |
| Lifecycle timeline with add-event | Task 8 |
| Backend tests | Task 4 |
| Frontend tests | Task 11 |

**No gaps found.**

### Placeholder Scan

- No TBD, TODO, or "implement later" found
- All code blocks are complete
- All commands have expected output
- No "similar to Task N" references

### Type Consistency

- `PositionLifecycleEventOut` matches `PositionLifecycleEvent` type in frontend
- `PortfolioPositionSpec` extended consistently in backend and frontend
- Event type names consistent across backend registry and frontend mapping
- Prop names consistent: `onEditPosition`, `editingPositionId`, `onAddLifecycleEvent`, `addingLifecycleEvent`

**Plan passes self-review.**
