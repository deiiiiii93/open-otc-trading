# Position Management: Lifecycle Events & Field Editing

## Overview

Add position management capabilities to the OTC trading app, restricted to container portfolios. This includes full lifecycle event tracking for structured products and direct editing of position fields.

## Goals

- Enable users to manage positions within container portfolios (not views)
- Track structured product lifecycle events (knock-in, knock-out, autocall, coupon, maturity)
- Allow direct editing of all business-level position fields
- Auto-update position status based on lifecycle events

## Non-Goals

- Position management for view portfolios (read-only)
- Workflow/approval gates for position changes
- Bulk lifecycle event application
- Notifications or alerts on lifecycle events

## Data Model

### New Table: `position_lifecycle_events`

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
```

### Event Type Registry

**Generic events (all products):**

| Event Type   | Target Status | Notes                      |
|--------------|---------------|----------------------------|
| `open`       | `open`        | Position created/reopened  |
| `close`      | `closed`      | Manual close               |
| `reopen`     | `open`        | Reopen a closed position   |

**Snowball events:**

| Event Type            | Target Status | Notes                        |
|-----------------------|---------------|------------------------------|
| `knock_in`            | `knocked_in`  | Knock-in barrier hit         |
| `knock_out`           | `closed`      | Knock-out barrier hit        |
| `coupon_observation`  | —             | Observation date reached     |
| `coupon_paid`         | —             | Coupon payment made          |
| `maturity`            | `closed`      | Product matured              |

**Phoenix events:**

| Event Type    | Target Status | Notes                   |
|---------------|---------------|-------------------------|
| `autocall`    | `closed`      | Autocall triggered      |
| `coupon_lock` | —             | Coupon lock event       |
| `coupon_paid` | —             | Coupon payment made     |
| `memory_coupon` | —           | Memory coupon applied   |
| `maturity`    | `closed`      | Product matured         |

**Barrier events:**

| Event Type  | Target Status | Notes                |
|-------------|---------------|----------------------|
| `knock_in`  | `knocked_in`  | Knock-in barrier hit |
| `knock_out` | `closed`      | Knock-out barrier hit|
| `maturity`  | `closed`      | Product matured      |

**Custom:**

| Event Type | Target Status | Notes                                      |
|------------|---------------|--------------------------------------------|
| `custom`   | —             | Informational only; use PATCH to set status |

### Status Values

Valid `Position.status` values: `"open"`, `"knocked_in"`, `"closed"`.

- `"closed"` positions are skipped by the pricer and risk engine (existing behavior preserved)
- `"knocked_in"` positions continue to be priced (alive state with modified terms)
- Default status on creation: `"open"`

### Product-to-Events Mapping

Frontend event type dropdowns filter by the position's `product_type`:

| Product Type            | Available Events                                                               |
|-------------------------|--------------------------------------------------------------------------------|
| `SnowballOption`        | `close`, `knock_in`, `knock_out`, `coupon_observation`, `coupon_paid`, `maturity`, `custom` |
| `PhoenixOption`         | `close`, `autocall`, `coupon_lock`, `coupon_paid`, `memory_coupon`, `maturity`, `custom` |
| `BarrierOption`         | `close`, `knock_in`, `knock_out`, `maturity`, `custom`                         |
| `SingleSharkfinOption`  | `close`, `knock_in`, `knock_out`, `maturity`, `custom`                         |
| `DoubleSharkfinOption`  | `close`, `knock_in`, `knock_out`, `maturity`, `custom`                         |
| All others              | `close`, `custom`                                                              |

Generic events (`open`, `reopen`) are not selectable in the UI — they are system-generated on position creation or triggered via status PATCH.

### Event Data Schema (per Event Type)

The `event_data` JSON is free-form but follows these conventions per event type:

| Event Type            | Suggested `event_data` Fields                     |
|-----------------------|---------------------------------------------------|
| `knock_in`            | `barrier_level`, `observation_date`               |
| `knock_out`           | `barrier_level`, `observation_date`, `payoff`     |
| `autocall`            | `autocall_level`, `observation_date`, `payoff`    |
| `coupon_paid`         | `coupon_amount`, `coupon_date`                    |
| `coupon_observation`  | `observation_date`, `observed_price`              |
| `coupon_lock`         | `lock_date`, `locked_coupon_rate`                 |
| `memory_coupon`       | `coupon_amount`, `memory_periods`                 |
| `maturity`            | `maturity_date`, `final_payoff`                   |
| `close`               | `reason` (e.g., `"manual"`, `"client_request"`)   |
| `custom`              | `reason` (required, free-form text)               |

### Existing Model Changes

No schema changes to existing tables. The `Position.status` column remains `String(40)` with default `"open"`.

## API Design

### New Endpoints

#### POST `/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events`

Create a lifecycle event for a position.

**Request body:**
```json
{
  "event_type": "knock_out",
  "event_data": {
    "barrier_level": 4500,
    "observation_date": "2026-05-12",
    "notes": "KO at month 6 observation"
  }
}
```

**Validation:**
- Portfolio must exist and be `kind == "container"` → 400 if not
- Position must belong to portfolio → 404 if not
- `event_type` must be in the allowed registry → 400 with valid types listed
- `event_data` must be valid JSON object, max 10KB

**Side effects:**
- Creates `PositionLifecycleEvent` record
- Updates `Position.status` to the event's target status (if not null)
- Updates `Portfolio.updated_at`
- Records `position.lifecycle_event` audit event

**Response:** `PositionLifecycleEventOut`

#### GET `/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events`

List lifecycle events for a position, ordered by `created_at DESC`.

**Response:** `list[PositionLifecycleEventOut]`

### Modified Endpoints

#### PATCH `/api/portfolios/{portfolio_id}/positions/{position_id}`

- Add container-only guard: return 400 if portfolio `kind != "container"`
- All fields in `PortfolioPositionSpec` remain editable: `underlying`, `product_type`, `product_kwargs`, `engine_name`, `engine_kwargs`, `quantity`, `entry_price`, `trade_effective_date`
- Also allow editing `status` and `source_trade_id` (extend `PortfolioPositionSpec`)
- Existing audit event recording stays in place

## Frontend Components

### Inline Editing in Positions Table

Three columns become inline-editable via click-to-edit:

- **Quantity** — numeric input, blur/Enter to save, Escape to cancel
- **Entry Price** — numeric input, blur/Enter to save, Escape to cancel
- **Status** — dropdown: `open` | `knocked_in` | `closed`

Inline edits call `PATCH` immediately. Show spinner while saving; revert on error with tooltip.

### Position Detail Modal — Two Tabs

Replace the current read-only detail modal with a tabbed interface.

**Tab 1: "Details"**

Editable form with all business fields:

| Field               | Input Type     |
|---------------------|----------------|
| `underlying`        | Text input     |
| `product_type`      | Dropdown       |
| `quantity`          | Number input   |
| `entry_price`       | Number input   |
| `status`            | Dropdown       |
| `source_trade_id`   | Text input     |
| `trade_effective_date` | Date input  |
| `engine_name`       | Dropdown       |
| `product_kwargs`    | JSON textarea  |
| `engine_kwargs`     | JSON textarea  |

- Save button calls `PATCH`
- Cancel reverts to original values
- Form-level validation for JSON fields

**Tab 2: "Lifecycle"**

Timeline view:
- Chronological list of lifecycle events (newest first)
- Each event card: event type badge, old→new status arrow, timestamp, actor, event_data preview
- "Add Event" button opens inline form:
  - Event type dropdown (filtered by product type)
  - Dynamic fields based on event type (e.g., `barrier_level` for knock_out)
  - Notes textarea
  - Submit creates event, refreshes timeline and position data

## Data Flow

```
User triggers lifecycle event (click "Add Event" → submit)
  → Frontend POST /lifecycle-events
    → Backend: validate container portfolio
    → Backend: resolve target_status from event_type registry
    → Backend: create PositionLifecycleEvent (capturing old_status, new_status)
    → Backend: if target_status is not null, update Position.status
    → Backend: update Portfolio.updated_at
    → Backend: record audit event
    → Backend: commit transaction
    → Backend: return event record
  → Frontend: refresh position data (GET portfolio)
  → Frontend: re-render timeline and position status
```

## Status Transition Rules

| Current Status | Event                          | New Status     |
|----------------|--------------------------------|----------------|
| any            | `open`, `reopen`               | `open`         |
| any            | `close`, `knock_out`, `autocall`, `maturity` | `closed` |
| `open`         | `knock_in`                     | `knocked_in`   |
| `knocked_in`   | `knock_out`, `maturity`        | `closed`       |
| `closed`       | `reopen`                       | `open`         |

No additional transition validation — users have full flexibility.

## Error Handling

| Scenario                              | Status | Response Body                          |
|---------------------------------------|--------|----------------------------------------|
| Portfolio is not a container          | 400    | `"Position management is only available for container portfolios"` |
| Position not found in portfolio       | 404    | `"Position not found"`                 |
| Invalid event_type                    | 400    | `"Invalid event type. Valid types: [...]"` |
| Invalid JSON in product_kwargs        | 400    | Pydantic validation error              |
| Inline edit fails (network/server)    | —      | Cell reverts, shows error tooltip      |
| Modal save fails                      | —      | Form-level error banner, modal stays   |

## Testing Strategy

### Backend

- **Unit:** Event type→status mapping correctness, container guard, invalid event rejection
- **Integration:** Full lifecycle event creation flow, status auto-update, audit record

### Frontend

- **Unit:** Inline editing (blur/save/cancel), modal form validation, lifecycle tab rendering, event type dropdown filtering by product
- **Integration:** Modal tab switching, form state management
- **Live (e2e):** Edit a position field via inline and modal, add lifecycle event, verify timeline updates and status changes

## Files to Create / Modify

### New Files
- `backend/app/models.py` — add `PositionLifecycleEvent` model
- Alembic migration for `position_lifecycle_events` table
- `backend/app/schemas.py` — add `PositionLifecycleEventIn`, `PositionLifecycleEventOut`
- Frontend: Lifecycle tab component and event creation form

### Modified Files
- `backend/app/main.py` — new lifecycle event endpoints, container guard on patch_position
- `backend/app/services/position_pricer.py` — verify closed skip logic still correct
- `frontend/src/routes/Positions.tsx` — add inline editing, tabbed detail modal
- `frontend/src/routes/Positions.live.tsx` — wire up PATCH and lifecycle event API calls
- `frontend/src/types.ts` — add `PositionLifecycleEvent` type
