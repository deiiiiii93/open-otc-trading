# Settlement Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track, govern and document the cash implied by position lifecycle events — auto-generated cashflows that users and agents can release, block, edit and settle, plus deterministic Markdown settlement notices.

**Architecture:** A pure, total deriver turns a `(Position, PositionLifecycleEvent)` pair into cashflow drafts. Two callers persist them: a best-effort inline hook in `create_lifecycle_event` and an INSERT-only idempotent backfill sweep. A store enforces a six-state machine under optimistic concurrency, writing an append-only transition log. A drift sweep re-derives and *flags* divergence without ever overwriting. Notices render deterministically to Markdown artifacts. REST, agent tools and a React page are thin clients over the same service.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, pydantic v2, LangChain `@tool`, React 19 + Vite + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-08-10-settlement-module-design.md`

## Global Constraints

- **This module never computes payoffs.** It reads amounts out of `event_data`; when absent the cashflow is `needs_amount`. No new pricing math anywhere in `services/settlement/`.
- **No LLM prose in a settlement notice.** Notices are deterministic templates. Do not import the reporting narrator.
- Backend tests: `.venv/bin/python -m pytest` from the repo root. Never pipe pytest through `tail`.
- Frontend: `cd frontend && npm test` (vitest) and `npx tsc --noEmit`. The vitest suite is flaky under load — compare failing-file sets against a same-machine `main` run before blaming this branch.
- Frontend styling is **token-only**. Read `frontend/CLAUDE.md` before touching any UI file. Zero raw hex/rgb colors.
- Alembic migrations use **migration-local Core tables**, never ORM models.
- Every write-class agent tool carries `@capability_gated(group=ToolGroup.DOMAIN_WRITE)`.
- Statuses, verbatim: `needs_amount`, `pending`, `blocked`, `released`, `settled`, `void`.
- Transition-log actions, verbatim: `generated`, `edited`, `released`, `unreleased`, `blocked`, `unblocked`, `settled`, `voided`, `resynced`, `flagged_stale`, `stale_cleared`.
- Migration id: `0055_settlement_cashflows`, `down_revision = "0054_seed_report_templates"`.

## Design amendment — desk decisions, 2026-08-10 (during execution)

The Task 2 Step 5 mapping was answered by the desk, and both answers widen the
design beyond what Tasks 3–6 originally assumed. Everything below is implemented
and tested; later tasks must respect it.

1. **Every terminating event emits a `settlement` leg** (`settle`, `knock_out`,
   `autocall`, `maturity`, `close`) so a trade can never terminate silently with
   untracked cash. They deliberately **share** a `leg_key`.
2. **`open` emits a `premium` leg** computed from the position's recorded
   `entry_price × quantity` (a multiplication of two stored trade fields, not a
   payoff model). This is the one place the deriver reads position state.

Consequences:

- **Same-position dedup is required.** Two different `lifecycle_event_id`s mean
  `UNIQUE(lifecycle_event_id, leg_key)` cannot see the economic duplicate.
  `derive.SINGLETON_LEG_KEYS = {"settlement", "premium"}` marks once-per-position
  legs; `generate._open_singleton` enforces it, ignoring **terminal** rows so a
  settle → reopen → settle cycle legitimately earns a second cashflow.
  `coupon` is deliberately NOT singleton — coupons recur.
- **`generate._fill_if_empty` is a narrow, audited exception to INSERT-only:**
  null → value only, only on a `needs_amount` row, never over a released or
  edited one, idempotent, logged as `filled_from_event` with the source event id.
- **A filled row keeps `derived_amount = None`.** `derived_*` describes the row's
  OWN event, which really carried no amount. Rewriting it would make drift
  detection compare the filled value against a re-derived `None` forever. A
  filled row therefore reads as "overridden" (`amount != derived_amount`), which
  is exactly how `resync` must treat it.
- **Amounts are non-negative magnitudes; `direction` carries the sign.** A
  negative derived amount flips `pay` ↔ `receive`.
- `GenerationResult` gained a **`filled`** field: `(created, skipped, filled,
  cashflow_ids)`.
- Transition-log vocabulary gains **`filled_from_event`**.

---

### Task 1: Data model and migration

**Files:**
- Modify: `backend/app/models.py` (append after `PositionLifecycleEvent`, ~line 1376)
- Create: `backend/alembic/versions/0055_settlement_cashflows.py`
- Test: `tests/test_settlement_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: ORM classes `SettlementCashflow`, `SettlementCashflowEvent`, `SettlementNotice`. Every later task imports these from `app.models`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_models.py`:

```python
"""Schema-level guarantees for the settlement tables."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    SettlementCashflowEvent,
    SettlementNotice,
)


def _event(session) -> PositionLifecycleEvent:
    portfolio = Portfolio(name="Settlement Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        product_type="SnowballOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id,
        event_type="settle",
        event_data={"settlement_amount": 1000.0},
    )
    session.add(event)
    session.flush()
    return event


def test_cashflow_roundtrips_with_defaults(session):
    event = _event(session)
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="principal",
        position_id=event.position_id,
        currency="USD",
        direction="pay",
        derived_amount=1000.0,
        derived_value_date=date(2026, 8, 10),
        derived_basis="event_data.settlement_amount",
        amount=1000.0,
        value_date=date(2026, 8, 10),
        status="pending",
    )
    session.add(cashflow)
    session.flush()

    assert cashflow.row_version == 1
    assert cashflow.stale is False
    assert cashflow.counterparty is None


def test_one_leg_per_event_is_unique(session):
    event = _event(session)
    for _ in range(2):
        session.add(
            SettlementCashflow(
                lifecycle_event_id=event.id,
                leg_key="principal",
                position_id=event.position_id,
                currency="USD",
                direction="pay",
                status="needs_amount",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_distinct_legs_from_one_event_coexist(session):
    event = _event(session)
    for leg in ("principal", "coupon"):
        session.add(
            SettlementCashflow(
                lifecycle_event_id=event.id,
                leg_key=leg,
                position_id=event.position_id,
                currency="USD",
                direction="pay",
                status="needs_amount",
            )
        )
    session.flush()
    assert session.query(SettlementCashflow).count() == 2


def test_notice_versions_are_unique_per_cashflow(session):
    event = _event(session)
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="principal",
        position_id=event.position_id,
        currency="USD",
        direction="pay",
        status="pending",
    )
    session.add(cashflow)
    session.flush()
    for _ in range(2):
        session.add(
            SettlementNotice(
                cashflow_id=cashflow.id,
                version=1,
                artifact_path="notice-1-v1.md",
                content_sha256="a" * 64,
                payload_snapshot={},
                rendered_by="desk_user",
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_transition_log_row_persists(session):
    event = _event(session)
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="principal",
        position_id=event.position_id,
        currency="USD",
        direction="pay",
        status="pending",
    )
    session.add(cashflow)
    session.flush()
    session.add(
        SettlementCashflowEvent(
            cashflow_id=cashflow.id,
            action="generated",
            from_status=None,
            to_status="pending",
            actor="system",
            payload={},
        )
    )
    session.flush()
    assert session.query(SettlementCashflowEvent).count() == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SettlementCashflow' from 'app.models'`

- [ ] **Step 3: Add the ORM models**

In `backend/app/models.py`, insert directly after the `PositionLifecycleEvent` class (before `class PositionImportBatch`):

```python
class SettlementCashflow(Base):
    """One cash leg implied by a position lifecycle event.

    ``derived_*`` is the snapshot of what the deriver produced and is never
    rewritten by generation or drift detection — only an explicit resync
    re-baselines it. ``amount``/``value_date`` are the effective values an
    edit changes. Keeping both is what lets drift be detected on a row a
    human has already overridden.
    """

    __tablename__ = "settlement_cashflows"
    __table_args__ = (
        UniqueConstraint(
            "lifecycle_event_id", "leg_key", name="uq_settlement_cashflow_event_leg"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lifecycle_event_id: Mapped[int] = mapped_column(
        ForeignKey("position_lifecycle_events.id", ondelete="CASCADE"), index=True
    )
    leg_key: Mapped[str] = mapped_column(String(40))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    derived_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    derived_value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    derived_basis: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="needs_amount", index=True)
    stale: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    stale_reason: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    lifecycle_event: Mapped["PositionLifecycleEvent"] = relationship()
    position: Mapped["Position"] = relationship()
    events: Mapped[list["SettlementCashflowEvent"]] = relationship(
        back_populates="cashflow",
        cascade="all, delete-orphan",
        order_by="SettlementCashflowEvent.created_at",
    )
    notices: Mapped[list["SettlementNotice"]] = relationship(
        back_populates="cashflow",
        cascade="all, delete-orphan",
        order_by="SettlementNotice.version",
    )


class SettlementCashflowEvent(Base):
    """Append-only transition log for one settlement cashflow."""

    __tablename__ = "settlement_cashflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashflow_id: Mapped[int] = mapped_column(
        ForeignKey("settlement_cashflows.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(24))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="desk_user")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cashflow: Mapped[SettlementCashflow] = relationship(back_populates="events")


class SettlementNotice(Base):
    """A rendered settlement notice document for one cashflow.

    ``payload_snapshot`` freezes the exact values used at render time, so the
    notice remains provable evidence of what was stated even after the
    cashflow moves on.
    """

    __tablename__ = "settlement_notices"
    __table_args__ = (
        UniqueConstraint("cashflow_id", "version", name="uq_settlement_notice_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashflow_id: Mapped[int] = mapped_column(
        ForeignKey("settlement_cashflows.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    artifact_path: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64))
    payload_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="generated")
    rendered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rendered_by: Mapped[str] = mapped_column(String(120), default="desk_user")

    cashflow: Mapped[SettlementCashflow] = relationship(back_populates="notices")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_settlement_models.py -v`
Expected: PASS (5 tests). The `session` fixture calls `init_db()`, which creates tables from the ORM metadata, so this passes before the migration exists.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0055_settlement_cashflows.py`:

```python
"""settlement cashflows, transition log and notices

Revision ID: 0055_settlement_cashflows
Revises: 0054_seed_report_templates
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_settlement_cashflows"
down_revision = "0054_seed_report_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settlement_cashflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lifecycle_event_id",
            sa.Integer(),
            sa.ForeignKey("position_lifecycle_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leg_key", sa.String(length=40), nullable=False),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("positions.id"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=8), nullable=False,
                  server_default="CNY"),
        sa.Column("counterparty", sa.String(length=255), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("derived_amount", sa.Float(), nullable=True),
        sa.Column("derived_value_date", sa.Date(), nullable=True),
        sa.Column("derived_basis", sa.String(length=80), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="needs_amount"),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("stale_reason", sa.JSON(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "lifecycle_event_id", "leg_key", name="uq_settlement_cashflow_event_leg"
        ),
    )
    op.create_index(
        "ix_settlement_cashflows_lifecycle_event_id",
        "settlement_cashflows",
        ["lifecycle_event_id"],
    )
    op.create_index(
        "ix_settlement_cashflows_position_id", "settlement_cashflows", ["position_id"]
    )
    op.create_index(
        "ix_settlement_cashflows_status", "settlement_cashflows", ["status"]
    )

    op.create_table(
        "settlement_cashflow_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cashflow_id",
            sa.Integer(),
            sa.ForeignKey("settlement_cashflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=False,
                  server_default="desk_user"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_settlement_cashflow_events_cashflow_id",
        "settlement_cashflow_events",
        ["cashflow_id"],
    )

    op.create_table(
        "settlement_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cashflow_id",
            sa.Integer(),
            sa.ForeignKey("settlement_cashflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("artifact_path", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="generated"),
        sa.Column("rendered_at", sa.DateTime(), nullable=False),
        sa.Column("rendered_by", sa.String(length=120), nullable=False,
                  server_default="desk_user"),
        sa.UniqueConstraint(
            "cashflow_id", "version", name="uq_settlement_notice_version"
        ),
    )
    op.create_index(
        "ix_settlement_notices_cashflow_id", "settlement_notices", ["cashflow_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_settlement_notices_cashflow_id", table_name="settlement_notices")
    op.drop_table("settlement_notices")
    op.drop_index(
        "ix_settlement_cashflow_events_cashflow_id",
        table_name="settlement_cashflow_events",
    )
    op.drop_table("settlement_cashflow_events")
    op.drop_index(
        "ix_settlement_cashflows_status", table_name="settlement_cashflows"
    )
    op.drop_index(
        "ix_settlement_cashflows_position_id", table_name="settlement_cashflows"
    )
    op.drop_index(
        "ix_settlement_cashflows_lifecycle_event_id", table_name="settlement_cashflows"
    )
    op.drop_table("settlement_cashflows")
```

- [ ] **Step 6: Verify the migration applies and reverses on a scratch DB**

⚠️ **A full fresh-chain `upgrade head` CANNOT work in this repo, and that is
pre-existing.** The chain breaks at **0051** (`duplicate column name:
position_id` on `pricing_parameter_rows` — a migration written against ORM
models, the drift this repo already documents). Verified identical on `main`,
and it is why `test_migration_fresh_chain.py`, `test_migration_0024.py`,
`test_migration_0046.py` and `test_migration_0047.py` are all in the
pre-existing failure baseline. Do **not** try to fix that here.

Also: the env var is **`OPEN_OTC_DATABASE_URL`** (a `validation_alias`), not
`DATABASE_URL`. Getting it wrong does not error — it silently falls back to
`./data/open_otc.sqlite3`, i.e. **the live DB**, and reports `exit=0`.

So verify `0055` in isolation by stamping at its parent:

```bash
cd /Users/fuxinyao/open-otc-trading/.claude/worktrees/settlement-module
TMPDB=$(mktemp -d)
export OPEN_OTC_DATABASE_URL="sqlite+pysqlite:///$TMPDB/only55.sqlite3"
PY=/Users/fuxinyao/open-otc-trading/.venv/bin/python
$PY -m alembic stamp 0054_seed_report_templates
$PY -m alembic upgrade head      # runs ONLY 0055
$PY -m alembic downgrade -1      # and reverses it
$PY -m alembic heads             # exactly one head
```

Expected: upgrade creates `settlement_cashflows`, `settlement_cashflow_events`,
`settlement_notices` plus 5 indexes; downgrade removes all three and returns the
version to `0054_seed_report_templates`; `heads` prints exactly
`0055_settlement_cashflows (head)`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0055_settlement_cashflows.py tests/test_settlement_models.py
git commit -m "feat(settlement): add cashflow, transition-log and notice tables"
```

---

### Task 2: The pure deriver

**Files:**
- Create: `backend/app/services/settlement/__init__.py`
- Create: `backend/app/services/settlement/contracts.py`
- Create: `backend/app/services/settlement/derive.py`
- Test: `tests/test_settlement_derive.py`

**Interfaces:**
- Consumes: `app.models.Position`, `app.models.PositionLifecycleEvent`.
- Produces:
  - `CashflowDraft` — frozen dataclass with fields `leg_key: str`, `direction: str`, `amount: float | None`, `value_date: date | None`, `basis: str`.
  - `derive_cashflows(position: Position, event: PositionLifecycleEvent) -> list[CashflowDraft]` — pure, no session, **never raises**.
  - `CASH_LEG_RULES: dict[str, tuple[LegRule, ...]]` keyed by lifecycle event type.

> **This task contains the one deliberate hand-off to the domain expert (Step 5).** Do not invent the mapping.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_derive.py`:

```python
"""The deriver is pure, total, and never raises."""
from __future__ import annotations

from datetime import date

import pytest

from app.models import Position, PositionLifecycleEvent
from app.services.settlement.contracts import CashflowDraft
from app.services.settlement.derive import CASH_LEG_RULES, derive_cashflows


def _position(**overrides) -> Position:
    defaults = dict(
        id=1,
        portfolio_id=1,
        underlying="AAPL",
        product_type="SnowballOption",
        product_kwargs={},
        quantity=100.0,
        entry_price=2.5,
        currency="USD",
        status="open",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _event(event_type: str, data: dict | None = None) -> PositionLifecycleEvent:
    return PositionLifecycleEvent(
        id=1, position_id=1, event_type=event_type, event_data=data or {}
    )


def test_settle_with_amount_yields_a_priced_leg():
    drafts = derive_cashflows(
        _position(),
        _event(
            "settle",
            {"settlement_amount": 1250.0, "settlement_date": "2026-08-14"},
        ),
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.leg_key == "settlement"
    assert draft.amount == 1250.0
    assert draft.value_date == date(2026, 8, 14)
    assert draft.basis == "event_data.settlement_amount"


def test_settle_without_amount_yields_a_needs_amount_leg():
    drafts = derive_cashflows(_position(), _event("settle", {}))
    assert len(drafts) == 1
    assert drafts[0].amount is None
    assert drafts[0].basis == "none"


def test_non_cash_event_types_yield_nothing():
    for event_type in ("reopen", "knock_in", "coupon_observation", "fixing"):
        assert derive_cashflows(_position(), _event(event_type)) == []


def test_deriver_never_raises_on_garbage_event_data():
    drafts = derive_cashflows(
        _position(),
        _event("settle", {"settlement_amount": "not-a-number",
                          "settlement_date": "31st of Neveruary"}),
    )
    assert drafts == [] or drafts[0].amount is None


def test_deriver_is_deterministic():
    args = (_position(), _event("settle", {"settlement_amount": 10.0}))
    assert derive_cashflows(*args) == derive_cashflows(*args)


def test_every_rule_targets_a_real_lifecycle_event_type():
    from app.services.domains.positions import LIFECYCLE_EVENT_TARGETS

    unknown = set(CASH_LEG_RULES) - set(LIFECYCLE_EVENT_TARGETS)
    assert unknown == set(), f"rules reference non-existent event types: {unknown}"


def test_knockout_and_settle_do_not_both_book_the_settlement():
    """A snowball fires knock_out and THEN settle. If both produce a
    settlement leg the money is booked twice."""
    position = _position()
    ko = derive_cashflows(position, _event("knock_out", {"settlement_amount": 900.0}))
    settle = derive_cashflows(position, _event("settle", {"settlement_amount": 900.0}))
    ko_legs = {d.leg_key for d in ko}
    settle_legs = {d.leg_key for d in settle}
    assert not (ko_legs & settle_legs), (
        f"knock_out and settle both derive {ko_legs & settle_legs} — double count"
    )


@pytest.mark.parametrize("direction_field", ["pay", "receive"])
def test_direction_is_always_one_of_two_values(direction_field):
    drafts = derive_cashflows(_position(), _event("settle", {"settlement_amount": 1.0}))
    assert all(d.direction in {"pay", "receive"} for d in drafts)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_derive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.settlement'`

- [ ] **Step 3: Create the package and contracts**

Create `backend/app/services/settlement/__init__.py`:

```python
"""Settlement: governance over the cash that lifecycle events imply.

This package tracks, approves and documents cashflows. It deliberately does
NOT compute payoffs — amounts come from the lifecycle event's own data, and
when absent the cashflow is honestly recorded as ``needs_amount``. Pricing
math belongs to QuantArk.
"""
```

Create `backend/app/services/settlement/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeAlias

Direction: TypeAlias = Literal["pay", "receive"]

CashflowStatus: TypeAlias = Literal[
    "needs_amount", "pending", "blocked", "released", "settled", "void"
]

STATUSES: frozenset[str] = frozenset(
    {"needs_amount", "pending", "blocked", "released", "settled", "void"}
)

TERMINAL_STATUSES: frozenset[str] = frozenset({"settled", "void"})

#: Statuses in which an edit to amount / value_date is legal.
EDITABLE_STATUSES: frozenset[str] = frozenset({"needs_amount", "pending", "blocked"})


@dataclass(frozen=True, slots=True)
class CashflowDraft:
    """What the deriver produces. Not persisted directly."""

    leg_key: str
    direction: Direction
    amount: float | None
    value_date: date | None
    basis: str
```

- [ ] **Step 4: Write the deriver scaffolding**

Create `backend/app/services/settlement/derive.py`:

```python
"""Pure, total derivation of cash legs from a position lifecycle event.

Contract, in order of importance:

1. **Pure.** No session, no I/O, no clock. Given the same position and event
   it returns the same drafts.
2. **Total.** It NEVER raises. Malformed ``event_data`` yields a draft with
   ``amount=None`` (persisted as ``needs_amount``) or no draft at all. This
   is what lets the inline generation hook be best-effort without risking
   the lifecycle event it is attached to.
3. **It does not compute payoffs.** It reads amounts the event already
   carries. Today only ``settle`` on a ``SnowballOption`` carries one
   (written by ``_enrich_snowball_ko_settlement``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ...models import Position, PositionLifecycleEvent
from .contracts import CashflowDraft, Direction


@dataclass(frozen=True, slots=True)
class LegRule:
    """One cash leg a lifecycle event type may produce.

    ``amount_keys`` are tried in order against ``event_data``; the first
    present, finite number wins. If none match, the leg is still emitted
    with ``amount=None`` so the desk sees that cash is owed but unquantified.
    """

    leg_key: str
    direction: Direction
    amount_keys: tuple[str, ...]
    date_keys: tuple[str, ...] = ("settlement_date", "value_date", "payment_date")


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _first_amount(data: dict[str, Any], keys: tuple[str, ...]) -> tuple[float | None, str]:
    for key in keys:
        amount = _finite_float(data.get(key))
        if amount is not None:
            return amount, f"event_data.{key}"
    return None, "none"


def _first_date(data: dict[str, Any], keys: tuple[str, ...]) -> date | None:
    for key in keys:
        parsed = _as_date(data.get(key))
        if parsed is not None:
            return parsed
    return None


def derive_cashflows(
    position: Position, event: PositionLifecycleEvent
) -> list[CashflowDraft]:
    """Return the cash legs implied by one lifecycle event. Never raises."""
    try:
        rules = CASH_LEG_RULES.get(event.event_type or "", ())
        if not rules:
            return []
        data = event.event_data if isinstance(event.event_data, dict) else {}
        drafts: list[CashflowDraft] = []
        for rule in rules:
            amount, basis = _first_amount(data, rule.amount_keys)
            drafts.append(
                CashflowDraft(
                    leg_key=rule.leg_key,
                    direction=rule.direction,
                    amount=amount,
                    value_date=_first_date(data, rule.date_keys),
                    basis=basis,
                )
            )
        return drafts
    except Exception:  # noqa: BLE001 - totality is the contract
        return []
```

- [ ] **Step 5: 🧑 DOMAIN DECISION — write the event→leg mapping**

Append `CASH_LEG_RULES` to the bottom of `derive.py`. **This is the desk's call, not the implementer's.** Fill in the rule table below.

The 14 legal event types are in `LIFECYCLE_EVENT_TARGETS` (`backend/app/services/domains/positions.py:33`): `open`, `close`, `settle`, `reopen`, `knock_in`, `knock_out`, `coupon_observation`, `coupon_paid`, `maturity`, `autocall`, `coupon_lock`, `memory_coupon`, `fixing`, `custom`.

Hazards the mapping must resolve:
- **Double count.** A snowball fires `knock_out` (status → closed) and then `settle` (carrying the enriched `settlement_amount`). If both emit a `settlement` leg the money is booked twice. `test_knockout_and_settle_do_not_both_book_the_settlement` enforces whatever you decide here.
- `autocall` and `maturity` both target status `closed` and may or may not be followed by a separate `settle`.
- `open` arguably implies a premium leg of `entry_price × quantity` — but note the deriver reads only `event_data`, so a premium leg would come back `needs_amount` unless the amount is written into the event.
- `coupon_observation` is an observation; `coupon_paid` is cash.
- Non-cash types must map to nothing.

Keys already written into `event_data` by the existing enrichment (`_enrich_snowball_ko_settlement`, `positions.py:481`): `settlement_amount`, `principal_amount`, `coupon_amount`, `settlement_date`, `ko_return_rate`, `ko_accrual_factor`.

```python
#: Which lifecycle event types produce cash, and what legs.
#: An event type absent from this map produces no cashflow at all.
CASH_LEG_RULES: dict[str, tuple[LegRule, ...]] = {
    "settle": (
        LegRule(
            leg_key="settlement",
            direction="pay",
            amount_keys=("settlement_amount",),
        ),
    ),
    # TODO(desk): add the remaining cash-generating event types.
    #   Decide for each of: open, close, knock_out, coupon_paid, maturity,
    #   autocall, memory_coupon, custom.
    #   Leave non-cash types out entirely.
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_derive.py -v`
Expected: PASS (all tests). If `test_knockout_and_settle_do_not_both_book_the_settlement` fails, the mapping from Step 5 double-counts — fix the mapping, not the test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/settlement/ tests/test_settlement_derive.py
git commit -m "feat(settlement): pure, total cashflow deriver"
```

---

### Task 3: Store — state machine under optimistic concurrency

**Files:**
- Create: `backend/app/services/settlement/errors.py`
- Create: `backend/app/services/settlement/store.py`
- Test: `tests/test_settlement_store.py`

**Interfaces:**
- Consumes: `CashflowDraft`, `EDITABLE_STATUSES`, `TERMINAL_STATUSES` (Task 2); ORM models (Task 1).
- Produces:
  - Errors: `SettlementError`, `SettlementNotFoundError`, `SettlementValidationError`, `SettlementConflictError`.
  - `ALLOWED_TRANSITIONS: dict[str, frozenset[str]]` — action → source statuses.
  - `edit_cashflow(session, *, cashflow_id, expected_row_version, actor, amount=..., value_date=..., counterparty=..., notes=...) -> SettlementCashflow`
  - `transition(session, *, cashflow_id, action, expected_row_version, actor, reason=None) -> SettlementCashflow` where `action ∈ {"release","unrelease","block","unblock","settle","void"}`
  - `log_event(session, *, cashflow, action, from_status, to_status, actor, reason=None, payload=None) -> SettlementCashflowEvent`

All functions take an explicit `session` and **flush but do not commit** — the caller (router / tool / sweep) owns the transaction boundary. `_UNSET` sentinel distinguishes "not supplied" from "set to None".

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_store.py`:

```python
"""State-machine legality and optimistic concurrency."""
from __future__ import annotations

from datetime import date

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    SettlementCashflowEvent,
)
from app.services.settlement import store
from app.services.settlement.errors import (
    SettlementConflictError,
    SettlementValidationError,
)


@pytest.fixture
def cashflow(session) -> SettlementCashflow:
    portfolio = Portfolio(name="Store Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle",
        event_data={"settlement_amount": 500.0},
    )
    session.add(event)
    session.flush()
    row = SettlementCashflow(
        lifecycle_event_id=event.id, leg_key="settlement",
        position_id=position.id, currency="USD", direction="pay",
        derived_amount=500.0, amount=500.0, status="pending",
    )
    session.add(row)
    session.flush()
    return row


def test_release_then_settle_is_the_happy_path(session, cashflow):
    released = store.transition(
        session, cashflow_id=cashflow.id, action="release",
        expected_row_version=cashflow.row_version, actor="desk_user",
    )
    assert released.status == "released"
    settled = store.transition(
        session, cashflow_id=released.id, action="settle",
        expected_row_version=released.row_version, actor="desk_user",
    )
    assert settled.status == "settled"


def test_settled_is_terminal(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    store.transition(session, cashflow_id=cashflow.id, action="settle",
                     expected_row_version=cashflow.row_version, actor="a")
    with pytest.raises(SettlementValidationError):
        store.transition(session, cashflow_id=cashflow.id, action="release",
                         expected_row_version=cashflow.row_version, actor="a")


def test_block_is_reachable_from_released(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    blocked = store.transition(
        session, cashflow_id=cashflow.id, action="block",
        expected_row_version=cashflow.row_version, actor="a",
        reason="counterparty dispute",
    )
    assert blocked.status == "blocked"
    assert blocked.block_reason == "counterparty dispute"


def test_stale_row_version_conflicts(session, cashflow):
    with pytest.raises(SettlementConflictError):
        store.transition(session, cashflow_id=cashflow.id, action="release",
                         expected_row_version=cashflow.row_version + 7, actor="a")


def test_every_mutation_bumps_row_version_and_logs(session, cashflow):
    before = cashflow.row_version
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=before, actor="desk_user")
    assert cashflow.row_version == before + 1
    logged = session.query(SettlementCashflowEvent).filter_by(
        cashflow_id=cashflow.id
    ).all()
    assert [e.action for e in logged] == ["released"]
    assert logged[0].from_status == "pending"
    assert logged[0].to_status == "released"


def test_edit_is_illegal_once_released(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    with pytest.raises(SettlementValidationError):
        store.edit_cashflow(session, cashflow_id=cashflow.id,
                            expected_row_version=cashflow.row_version,
                            actor="a", amount=999.0)


def test_supplying_an_amount_promotes_needs_amount_to_pending(session, cashflow):
    cashflow.status = "needs_amount"
    cashflow.amount = None
    session.flush()
    edited = store.edit_cashflow(
        session, cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version, actor="a", amount=750.0,
    )
    assert edited.status == "pending"
    assert edited.amount == 750.0


def test_amount_cannot_be_cleared_back_to_null(session, cashflow):
    with pytest.raises(SettlementValidationError):
        store.edit_cashflow(session, cashflow_id=cashflow.id,
                            expected_row_version=cashflow.row_version,
                            actor="a", amount=None)


def test_edit_records_before_and_after_in_the_log(session, cashflow):
    store.edit_cashflow(session, cashflow_id=cashflow.id,
                        expected_row_version=cashflow.row_version,
                        actor="a", amount=600.0, value_date=date(2026, 9, 1))
    logged = session.query(SettlementCashflowEvent).filter_by(
        cashflow_id=cashflow.id, action="edited"
    ).one()
    assert logged.payload["amount"] == {"from": 500.0, "to": 600.0}


def test_editing_only_notes_leaves_amount_untouched(session, cashflow):
    store.edit_cashflow(session, cashflow_id=cashflow.id,
                        expected_row_version=cashflow.row_version,
                        actor="a", notes="chased ops")
    assert cashflow.amount == 500.0
    assert cashflow.notes == "chased ops"


def test_void_is_reachable_from_any_non_settled_status(session, cashflow):
    voided = store.transition(session, cashflow_id=cashflow.id, action="void",
                              expected_row_version=cashflow.row_version, actor="a")
    assert voided.status == "void"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.settlement.store'`

- [ ] **Step 3: Write the errors module**

Create `backend/app/services/settlement/errors.py`:

```python
from __future__ import annotations


class SettlementError(Exception):
    """Base class for typed Settlement domain failures."""


class SettlementNotFoundError(SettlementError):
    pass


class SettlementValidationError(SettlementError):
    pass


class SettlementConflictError(SettlementError):
    pass
```

- [ ] **Step 4: Write the store**

Create `backend/app/services/settlement/store.py`:

```python
"""Persistence and the cashflow state machine.

Every mutation is guarded by ``expected_row_version`` and writes an
append-only ``SettlementCashflowEvent``. Functions flush but never commit —
the caller owns the transaction boundary.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ...models import SettlementCashflow, SettlementCashflowEvent, utcnow
from .contracts import EDITABLE_STATUSES
from .errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)

_UNSET: Any = object()

#: action -> the statuses it may be applied from.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "release": frozenset({"pending"}),
    "unrelease": frozenset({"released"}),
    # Reachable from `released` on purpose: pulling a payment back must never
    # be harder than releasing it.
    "block": frozenset({"needs_amount", "pending", "released"}),
    "unblock": frozenset({"blocked"}),
    "settle": frozenset({"released"}),
    "void": frozenset({"needs_amount", "pending", "blocked", "released"}),
}

_RESULT_STATUS: dict[str, str] = {
    "release": "released",
    "unrelease": "pending",
    "block": "blocked",
    "unblock": "pending",
    "settle": "settled",
    "void": "void",
}

_LOG_ACTION: dict[str, str] = {
    "release": "released",
    "unrelease": "unreleased",
    "block": "blocked",
    "unblock": "unblocked",
    "settle": "settled",
    "void": "voided",
}


def get_cashflow(session: Session, cashflow_id: int) -> SettlementCashflow:
    row = session.get(SettlementCashflow, cashflow_id)
    if row is None:
        raise SettlementNotFoundError(f"settlement cashflow {cashflow_id} not found")
    return row


def log_event(
    session: Session,
    *,
    cashflow: SettlementCashflow,
    action: str,
    from_status: str | None,
    to_status: str | None,
    actor: str,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> SettlementCashflowEvent:
    row = SettlementCashflowEvent(
        cashflow_id=cashflow.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor=actor or "desk_user",
        reason=reason,
        payload=payload or {},
    )
    session.add(row)
    session.flush()
    return row


def _apply(
    session: Session,
    *,
    cashflow: SettlementCashflow,
    expected_row_version: int,
    values: dict[str, Any],
) -> SettlementCashflow:
    """Compare-and-swap on row_version. Raises on a stale expectation."""
    if isinstance(expected_row_version, bool) or not isinstance(
        expected_row_version, int
    ) or expected_row_version <= 0:
        raise SettlementValidationError(
            "expected_row_version must be a positive integer"
        )
    result = session.execute(
        update(SettlementCashflow)
        .where(
            SettlementCashflow.id == cashflow.id,
            SettlementCashflow.row_version == expected_row_version,
        )
        .values(
            **values,
            row_version=SettlementCashflow.row_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise SettlementConflictError(
            f"settlement cashflow {cashflow.id} row version is stale"
        )
    session.flush()
    session.refresh(cashflow)
    return cashflow


def transition(
    session: Session,
    *,
    cashflow_id: int,
    action: str,
    expected_row_version: int,
    actor: str,
    reason: str | None = None,
) -> SettlementCashflow:
    if action not in ALLOWED_TRANSITIONS:
        raise SettlementValidationError(f"unknown settlement action '{action}'")
    cashflow = get_cashflow(session, cashflow_id)
    from_status = cashflow.status
    if from_status not in ALLOWED_TRANSITIONS[action]:
        raise SettlementValidationError(
            f"cannot {action} a cashflow in status '{from_status}'"
        )
    to_status = _RESULT_STATUS[action]
    values: dict[str, Any] = {"status": to_status}
    if action == "block":
        values["block_reason"] = reason
    elif action == "unblock":
        values["block_reason"] = None
    _apply(
        session,
        cashflow=cashflow,
        expected_row_version=expected_row_version,
        values=values,
    )
    log_event(
        session,
        cashflow=cashflow,
        action=_LOG_ACTION[action],
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=reason,
    )
    return cashflow


def edit_cashflow(
    session: Session,
    *,
    cashflow_id: int,
    expected_row_version: int,
    actor: str,
    amount: float | None = _UNSET,
    value_date: date | None = _UNSET,
    counterparty: str | None = _UNSET,
    notes: str | None = _UNSET,
    reason: str | None = None,
) -> SettlementCashflow:
    cashflow = get_cashflow(session, cashflow_id)
    if cashflow.status not in EDITABLE_STATUSES:
        raise SettlementValidationError(
            f"cannot edit a cashflow in status '{cashflow.status}'"
        )

    values: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    for field, supplied in (
        ("amount", amount),
        ("value_date", value_date),
        ("counterparty", counterparty),
        ("notes", notes),
    ):
        if supplied is _UNSET:
            continue
        current = getattr(cashflow, field)
        if current == supplied:
            continue
        values[field] = supplied
        payload[field] = {"from": current, "to": supplied}

    if "amount" in values:
        if values["amount"] is None:
            raise SettlementValidationError(
                "amount cannot be cleared once supplied; void the cashflow instead"
            )
        if not isinstance(values["amount"], (int, float)) or isinstance(
            values["amount"], bool
        ):
            raise SettlementValidationError("amount must be a number")

    if not values:
        return cashflow

    from_status = cashflow.status
    to_status = from_status
    if from_status == "needs_amount" and values.get("amount") is not None:
        to_status = "pending"
        values["status"] = to_status

    _apply(
        session,
        cashflow=cashflow,
        expected_row_version=expected_row_version,
        values=values,
    )
    log_event(
        session,
        cashflow=cashflow,
        action="edited",
        from_status=from_status,
        to_status=to_status if to_status != from_status else None,
        actor=actor,
        reason=reason,
        payload=payload,
    )
    return cashflow
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_store.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/settlement/errors.py backend/app/services/settlement/store.py tests/test_settlement_store.py
git commit -m "feat(settlement): cashflow state machine with optimistic concurrency"
```

---

### Task 4: Generation — backfill sweep and inline hook

**Files:**
- Create: `backend/app/services/settlement/generate.py`
- Modify: `backend/app/services/domains/positions.py` (inside `create_lifecycle_event`, ~line 622)
- Test: `tests/test_settlement_generate.py`

**Interfaces:**
- Consumes: `derive_cashflows`, `CashflowDraft` (Task 2); `log_event` (Task 3); ORM models (Task 1).
- Produces:
  - `GenerationResult` — frozen dataclass with `created: int`, `skipped: int`, `cashflow_ids: tuple[int, ...]`.
  - `generate_for_event(session, *, event, actor="system") -> GenerationResult` — INSERT-only for one event.
  - `generate_missing(session, *, portfolio_id=None, since=None, actor="system") -> GenerationResult` — the sweep.
  - `resolve_counterparty(session, position) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_generate.py`:

```python
"""Generation is INSERT-only, idempotent, and never blocks lifecycle events."""
from __future__ import annotations

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
)
from app.services.settlement import generate


@pytest.fixture
def book(session):
    portfolio = Portfolio(name="Generation Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    return portfolio, position


def _settle_event(session, position, amount=500.0) -> PositionLifecycleEvent:
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle",
        event_data={"settlement_amount": amount, "settlement_date": "2026-08-20"},
    )
    session.add(event)
    session.flush()
    return event


def test_sweep_creates_cashflows_for_existing_events(session, book):
    _, position = book
    _settle_event(session, position)
    result = generate.generate_missing(session)
    assert result.created == 1
    assert session.query(SettlementCashflow).count() == 1


def test_sweep_is_idempotent(session, book):
    _, position = book
    _settle_event(session, position)
    first = generate.generate_missing(session)
    second = generate.generate_missing(session)
    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    assert session.query(SettlementCashflow).count() == 1


def test_sweep_never_updates_an_existing_row(session, book):
    _, position = book
    event = _settle_event(session, position)
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    row.amount = 12345.0
    row.status = "released"
    session.flush()

    event.event_data = {**event.event_data, "settlement_amount": 999.0}
    session.flush()
    generate.generate_missing(session)

    session.refresh(row)
    assert row.amount == 12345.0
    assert row.status == "released"


def test_sweep_filters_by_portfolio(session, book):
    _, position = book
    _settle_event(session, position)
    other = Portfolio(name="Other Book")
    session.add(other)
    session.flush()
    assert generate.generate_missing(session, portfolio_id=other.id).created == 0
    assert generate.generate_missing(session, portfolio_id=position.portfolio_id).created == 1


def test_generation_stamps_currency_from_the_position(session, book):
    _, position = book
    _settle_event(session, position)
    generate.generate_missing(session)
    assert session.query(SettlementCashflow).one().currency == "USD"


def test_amountless_event_lands_as_needs_amount(session, book):
    _, position = book
    session.add(
        PositionLifecycleEvent(
            position_id=position.id, event_type="settle", event_data={}
        )
    )
    session.flush()
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    assert row.status == "needs_amount"
    assert row.amount is None


def test_generation_writes_a_generated_log_row(session, book):
    from app.models import SettlementCashflowEvent

    _, position = book
    _settle_event(session, position)
    generate.generate_missing(session)
    logged = session.query(SettlementCashflowEvent).one()
    assert logged.action == "generated"
    assert logged.to_status in {"pending", "needs_amount"}


def test_lifecycle_event_survives_a_broken_deriver(session, book, monkeypatch):
    """The inline hook is best-effort: cashflow derivation must never be able
    to prevent a lifecycle event from being recorded."""
    from app.services.domains import positions as positions_domain

    def _explode(*_args, **_kwargs):
        raise RuntimeError("deriver exploded")

    monkeypatch.setattr(positions_domain, "generate_for_event", _explode)

    _, position = book
    update = positions_domain.create_lifecycle_event(
        position_id=position.id,
        event_type="settle",
        event_data={"settlement_amount": 100.0},
        actor="desk_user",
        session=session,
    )
    assert update is not None
    assert session.query(PositionLifecycleEvent).count() == 1
    assert session.query(SettlementCashflow).count() == 0


def test_sweep_fills_a_gap_the_hook_left(session, book):
    """The sweep is the safety net that makes the lenient hook acceptable."""
    _, position = book
    _settle_event(session, position)
    assert session.query(SettlementCashflow).count() == 0
    generate.generate_missing(session)
    assert session.query(SettlementCashflow).count() == 1


def test_counterparty_resolves_from_a_booked_confirmation(session, book):
    from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade

    _, position = book
    batch = ConfirmationBatch()
    session.add(batch)
    session.flush()
    document = ConfirmationDocument(
        batch_id=batch.id, filename="conf.pdf", stored_path="/tmp/conf.pdf",
        sha256="b" * 64, byte_len=1024, mime="application/pdf",
    )
    session.add(document)
    session.flush()
    session.add(
        ExtractedTrade(
            document_id=document.id, family="SnowballOption",
            counterparty="Acme Capital", booked_position_id=position.id,
        )
    )
    session.flush()
    assert generate.resolve_counterparty(session, position) == "Acme Capital"


def test_counterparty_is_none_when_unknown(session, book):
    _, position = book
    assert generate.resolve_counterparty(session, position) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.settlement.generate'`

- [ ] **Step 3: (RESOLVED during execution) ConfirmationDocument column names**

Verified against `backend/app/models.py:1415`. The column is **`sha256`, not
`content_sha256`**, and `stored_path` / `byte_len` / `mime` are all NOT NULL.
The Step 1 fixture above already reflects this. No further action.

- [ ] **Step 4: Write the generation module**

Create `backend/app/services/settlement/generate.py`:

```python
"""Turning lifecycle events into cashflow rows.

Two callers, one deriver:

* ``generate_for_event`` runs inline inside ``create_lifecycle_event``.
* ``generate_missing`` is the backfill sweep over events that have no rows.

Both are **INSERT-only**. Neither ever updates an existing cashflow, which is
what makes re-running safe and what guarantees a human edit or a released row
is never silently rewritten. The ``(lifecycle_event_id, leg_key)`` unique
constraint is the enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    ExtractedTrade,
    Position,
    PositionLifecycleEvent,
    RFQ,
    SettlementCashflow,
)
from .derive import derive_cashflows
from .store import log_event


@dataclass(frozen=True, slots=True)
class GenerationResult:
    created: int
    skipped: int
    cashflow_ids: tuple[int, ...]


def resolve_counterparty(session: Session, position: Position) -> str | None:
    """Best-effort counterparty for a position.

    Positions carry no counterparty of their own, so this walks back to
    whichever origin recorded one: a booked confirmation first, then the RFQ
    the position was booked from.
    """
    trade_counterparty = session.execute(
        select(ExtractedTrade.counterparty)
        .where(ExtractedTrade.booked_position_id == position.id)
        .where(ExtractedTrade.counterparty.is_not(None))
        .limit(1)
    ).scalar_one_or_none()
    if trade_counterparty:
        return trade_counterparty

    if position.rfq_id is not None:
        client = session.execute(
            select(RFQ.client_name).where(RFQ.id == position.rfq_id)
        ).scalar_one_or_none()
        if client:
            return client
    return None


def _existing_leg_keys(session: Session, event_id: int) -> set[str]:
    return set(
        session.execute(
            select(SettlementCashflow.leg_key).where(
                SettlementCashflow.lifecycle_event_id == event_id
            )
        ).scalars()
    )


def generate_for_event(
    session: Session,
    *,
    event: PositionLifecycleEvent,
    actor: str = "system",
) -> GenerationResult:
    """Insert any cash legs this event implies that do not exist yet."""
    position = session.get(Position, event.position_id)
    if position is None:
        return GenerationResult(created=0, skipped=0, cashflow_ids=())

    drafts = derive_cashflows(position, event)
    if not drafts:
        return GenerationResult(created=0, skipped=0, cashflow_ids=())

    already = _existing_leg_keys(session, event.id)
    counterparty = resolve_counterparty(session, position)
    created_ids: list[int] = []
    skipped = 0

    for draft in drafts:
        if draft.leg_key in already:
            skipped += 1
            continue
        status = "needs_amount" if draft.amount is None else "pending"
        row = SettlementCashflow(
            lifecycle_event_id=event.id,
            leg_key=draft.leg_key,
            position_id=position.id,
            currency=position.currency or "CNY",
            counterparty=counterparty,
            direction=draft.direction,
            derived_amount=draft.amount,
            derived_value_date=draft.value_date,
            derived_basis=draft.basis,
            amount=draft.amount,
            value_date=draft.value_date,
            status=status,
        )
        session.add(row)
        session.flush()
        log_event(
            session,
            cashflow=row,
            action="generated",
            from_status=None,
            to_status=status,
            actor=actor,
            payload={"basis": draft.basis, "leg_key": draft.leg_key},
        )
        created_ids.append(row.id)

    return GenerationResult(
        created=len(created_ids), skipped=skipped, cashflow_ids=tuple(created_ids)
    )


def generate_missing(
    session: Session,
    *,
    portfolio_id: int | None = None,
    since: datetime | None = None,
    actor: str = "system",
) -> GenerationResult:
    """Backfill sweep. Idempotent: re-running inserts nothing new."""
    query = select(PositionLifecycleEvent).join(
        Position, Position.id == PositionLifecycleEvent.position_id
    )
    if portfolio_id is not None:
        query = query.where(Position.portfolio_id == portfolio_id)
    if since is not None:
        query = query.where(PositionLifecycleEvent.created_at >= since)
    query = query.order_by(PositionLifecycleEvent.id)

    created = 0
    skipped = 0
    ids: list[int] = []
    for event in session.execute(query).scalars():
        result = generate_for_event(session, event=event, actor=actor)
        created += result.created
        skipped += result.skipped
        ids.extend(result.cashflow_ids)
    return GenerationResult(created=created, skipped=skipped, cashflow_ids=tuple(ids))
```

- [ ] **Step 5: Wire the best-effort inline hook**

In `backend/app/services/domains/positions.py`, add near the other service imports at the top of the file:

```python
from ..settlement.generate import generate_for_event
```

Then in `create_lifecycle_event`, immediately after `sess.add(event)` and before `portfolio.updated_at = datetime.utcnow()` (~line 622), insert:

```python
        sess.flush()  # event.id is needed by cashflow generation
        try:
            generate_for_event(sess, event=event, actor=actor)
        except Exception:  # noqa: BLE001
            # Best-effort by design. Lifecycle is the source of truth for
            # position status and must never be held hostage to cashflow
            # derivation; generate_missing() is the safety net that fills
            # any gap this leaves.
            logger.exception(
                "settlement cashflow generation failed for lifecycle event"
            )
```

Confirm a module-level `logger` exists in `positions.py`; if not, add `logger = logging.getLogger(__name__)` beside the other module-level definitions and `import logging` at the top.

> The test monkeypatches `positions_domain.generate_for_event`, so the import **must** be a module-level `from ... import generate_for_event` binding as written, not `settlement_generate.generate_for_event(...)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_generate.py -v`
Expected: PASS (11 tests)

- [ ] **Step 7: Run the full positions suite to confirm the hook broke nothing**

Run: `.venv/bin/python -m pytest tests/ -k "position or lifecycle" -q`
Expected: no new failures versus `main`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/settlement/generate.py backend/app/services/domains/positions.py tests/test_settlement_generate.py
git commit -m "feat(settlement): idempotent generation sweep and best-effort lifecycle hook"
```

---

### Task 5: Drift detection

**Files:**
- Create: `backend/app/services/settlement/drift.py`
- Test: `tests/test_settlement_drift.py`

**Interfaces:**
- Consumes: `derive_cashflows` (Task 2); `log_event`, `get_cashflow`, `SettlementValidationError` (Task 3).
- Produces:
  - `DriftResult` — frozen dataclass with `checked: int`, `flagged: int`, `cleared: int`.
  - `refresh_drift(session, *, portfolio_id=None, cashflow_ids=None, actor="system") -> DriftResult`
  - `resync_cashflow(session, *, cashflow_id, expected_row_version, actor) -> SettlementCashflow`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_drift.py`:

```python
"""Drift is flagged, never silently applied."""
from __future__ import annotations

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    utcnow,
)
from app.services.settlement import drift, generate
from app.services.settlement.errors import SettlementValidationError


@pytest.fixture
def generated(session):
    portfolio = Portfolio(name="Drift Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle",
        event_data={"settlement_amount": 500.0},
    )
    session.add(event)
    session.flush()
    generate.generate_for_event(session, event=event)
    return event, session.query(SettlementCashflow).one()


def test_unchanged_event_is_not_stale(session, generated):
    _, cashflow = generated
    result = drift.refresh_drift(session)
    assert result.checked == 1
    assert result.flagged == 0
    assert cashflow.stale is False
    assert cashflow.last_checked_at is not None


def test_amended_amount_flags_stale_with_a_delta(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()

    result = drift.refresh_drift(session)

    assert result.flagged == 1
    assert cashflow.stale is True
    assert cashflow.stale_reason["kind"] == "derived_values_changed"
    assert cashflow.stale_reason["old"]["amount"] == 500.0
    assert cashflow.stale_reason["new"]["amount"] == 750.0


def test_cancelled_event_flags_stale(session, generated):
    event, cashflow = generated
    event.cancelled_at = utcnow()
    event.cancellation_reason = "booked in error"
    session.flush()

    drift.refresh_drift(session)

    assert cashflow.stale is True
    assert cashflow.stale_reason["kind"] == "source_event_cancelled"


def test_drift_never_mutates_the_effective_amount(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()

    drift.refresh_drift(session)

    assert cashflow.amount == 500.0
    assert cashflow.derived_amount == 500.0
    assert cashflow.status == "pending"


def test_a_released_row_is_flagged_but_untouched(session, generated):
    from app.services.settlement import store

    event, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()

    drift.refresh_drift(session)

    assert cashflow.stale is True
    assert cashflow.status == "released"
    assert cashflow.amount == 500.0


def test_clearing_the_amendment_clears_the_flag(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    drift.refresh_drift(session)
    assert cashflow.stale is True

    event.event_data = {**event.event_data, "settlement_amount": 500.0}
    session.flush()
    result = drift.refresh_drift(session)

    assert result.cleared == 1
    assert cashflow.stale is False
    assert cashflow.stale_reason is None


def test_void_rows_are_not_checked(session, generated):
    from app.services.settlement import store

    _, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="void",
                     expected_row_version=cashflow.row_version, actor="a")
    assert drift.refresh_drift(session).checked == 0


def test_resync_rebaselines_derived_and_effective_values(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    drift.refresh_drift(session)

    resynced = drift.resync_cashflow(
        session, cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version, actor="desk_user",
    )

    assert resynced.derived_amount == 750.0
    assert resynced.amount == 750.0
    assert resynced.stale is False


def test_resync_is_illegal_on_a_settled_row(session, generated):
    from app.services.settlement import store

    _, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    store.transition(session, cashflow_id=cashflow.id, action="settle",
                     expected_row_version=cashflow.row_version, actor="a")
    with pytest.raises(SettlementValidationError):
        drift.resync_cashflow(session, cashflow_id=cashflow.id,
                              expected_row_version=cashflow.row_version, actor="a")


def test_resync_keeps_a_human_override_when_only_the_date_moved(session, generated):
    from app.services.settlement import store

    event, cashflow = generated
    store.edit_cashflow(session, cashflow_id=cashflow.id,
                        expected_row_version=cashflow.row_version,
                        actor="a", amount=600.0)
    event.event_data = {**event.event_data, "settlement_date": "2026-12-01"}
    session.flush()
    drift.refresh_drift(session)

    resynced = drift.resync_cashflow(
        session, cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version, actor="a",
    )

    assert resynced.amount == 600.0, "an explicit human override must survive resync"
    assert resynced.derived_amount == 500.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_drift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.settlement.drift'`

- [ ] **Step 3: Write the drift module**

Create `backend/app/services/settlement/drift.py`:

```python
"""Detecting divergence between a cashflow and its source lifecycle event.

The rule is absolute: drift detection **flags**, it never applies. A cashflow
may have been edited by a human or already released, and silently rewriting
either is exactly the failure this module exists to prevent. Adopting new
values is a separate, explicit ``resync``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    utcnow,
)
from .contracts import TERMINAL_STATUSES
from .derive import derive_cashflows
from .errors import SettlementValidationError
from .store import _apply, get_cashflow, log_event

#: Rows in these statuses are no longer worth checking.
_UNCHECKED_STATUSES = frozenset({"void", "settled"})


@dataclass(frozen=True, slots=True)
class DriftResult:
    checked: int
    flagged: int
    cleared: int


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _recompute(
    session: Session, cashflow: SettlementCashflow
) -> tuple[float | None, Any, str] | None:
    """Re-derive this cashflow's leg from the current event. None if gone."""
    event = session.get(PositionLifecycleEvent, cashflow.lifecycle_event_id)
    if event is None:
        return None
    position = session.get(Position, cashflow.position_id)
    if position is None:
        return None
    for draft in derive_cashflows(position, event):
        if draft.leg_key == cashflow.leg_key:
            return draft.amount, draft.value_date, draft.basis
    return None


def _evaluate(
    session: Session, cashflow: SettlementCashflow
) -> dict[str, Any] | None:
    """Return a stale_reason dict, or None when the row is in step."""
    event = session.get(PositionLifecycleEvent, cashflow.lifecycle_event_id)
    if event is None:
        return {"kind": "source_event_missing", "detail": "lifecycle event deleted"}
    if event.cancelled_at is not None:
        return {
            "kind": "source_event_cancelled",
            "detail": event.cancellation_reason or "lifecycle event cancelled",
        }

    recomputed = _recompute(session, cashflow)
    if recomputed is None:
        return {
            "kind": "leg_no_longer_derived",
            "detail": f"event no longer produces leg '{cashflow.leg_key}'",
        }

    amount, value_date, basis = recomputed
    if amount == cashflow.derived_amount and value_date == cashflow.derived_value_date:
        return None
    return {
        "kind": "derived_values_changed",
        "detail": basis,
        "old": {
            "amount": cashflow.derived_amount,
            "value_date": _iso(cashflow.derived_value_date),
        },
        "new": {"amount": amount, "value_date": _iso(value_date)},
    }


def refresh_drift(
    session: Session,
    *,
    portfolio_id: int | None = None,
    cashflow_ids: list[int] | None = None,
    actor: str = "system",
) -> DriftResult:
    query = select(SettlementCashflow).where(
        SettlementCashflow.status.not_in(_UNCHECKED_STATUSES)
    )
    if portfolio_id is not None:
        query = query.join(
            Position, Position.id == SettlementCashflow.position_id
        ).where(Position.portfolio_id == portfolio_id)
    if cashflow_ids:
        query = query.where(SettlementCashflow.id.in_(cashflow_ids))

    checked = flagged = cleared = 0
    now = utcnow()
    for cashflow in session.execute(query).scalars():
        checked += 1
        reason = _evaluate(session, cashflow)
        was_stale = bool(cashflow.stale)

        cashflow.last_checked_at = now
        if reason is None:
            if was_stale:
                cashflow.stale = False
                cashflow.stale_reason = None
                cleared += 1
                log_event(
                    session, cashflow=cashflow, action="stale_cleared",
                    from_status=cashflow.status, to_status=None, actor=actor,
                )
            continue

        if not was_stale or cashflow.stale_reason != reason:
            cashflow.stale = True
            cashflow.stale_reason = reason
            flagged += 1
            log_event(
                session, cashflow=cashflow, action="flagged_stale",
                from_status=cashflow.status, to_status=None, actor=actor,
                reason=reason.get("kind"), payload=reason,
            )
    session.flush()
    return DriftResult(checked=checked, flagged=flagged, cleared=cleared)


def resync_cashflow(
    session: Session,
    *,
    cashflow_id: int,
    expected_row_version: int,
    actor: str,
) -> SettlementCashflow:
    """Adopt the newly derived values as the baseline.

    The effective ``amount`` follows only when it was never overridden — an
    explicit human number outranks a re-derivation.
    """
    cashflow = get_cashflow(session, cashflow_id)
    if cashflow.status in TERMINAL_STATUSES:
        raise SettlementValidationError(
            f"cannot resync a cashflow in status '{cashflow.status}'"
        )
    recomputed = _recompute(session, cashflow)
    if recomputed is None:
        raise SettlementValidationError(
            "source event no longer derives this leg; void the cashflow instead"
        )

    amount, value_date, basis = recomputed
    was_overridden = cashflow.amount != cashflow.derived_amount
    values: dict[str, Any] = {
        "derived_amount": amount,
        "derived_value_date": value_date,
        "derived_basis": basis,
        "stale": False,
        "stale_reason": None,
        "last_checked_at": utcnow(),
    }
    if not was_overridden:
        values["amount"] = amount
        values["value_date"] = value_date
        if cashflow.status == "needs_amount" and amount is not None:
            values["status"] = "pending"

    from_status = cashflow.status
    _apply(
        session, cashflow=cashflow,
        expected_row_version=expected_row_version, values=values,
    )
    log_event(
        session, cashflow=cashflow, action="resynced",
        from_status=from_status, to_status=cashflow.status, actor=actor,
        payload={"adopted_amount": amount, "kept_override": was_overridden},
    )
    return cashflow
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_drift.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/settlement/drift.py tests/test_settlement_drift.py
git commit -m "feat(settlement): drift detection that flags without overwriting"
```

---

### Task 6: Settlement notices

**Files:**
- Create: `backend/app/services/settlement/notice.py`
- Test: `tests/test_settlement_notice.py`

**Interfaces:**
- Consumes: `get_cashflow`, `log_event` (Task 3); `settings.artifact_dir`.
- Produces:
  - `render_notice_markdown(payload: dict) -> str` — pure.
  - `notice_payload(session, cashflow) -> dict` — the frozen snapshot.
  - `generate_notice(session, *, cashflow_id, actor, artifact_dir=None) -> SettlementNotice`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_notice.py`:

```python
"""Notices are deterministic, written to disk, and versioned."""
from __future__ import annotations

import hashlib

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    SettlementNotice,
)
from app.services.settlement import notice
from app.services.settlement.errors import SettlementValidationError


@pytest.fixture
def cashflow(session):
    portfolio = Portfolio(name="Notice Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle",
        event_data={"settlement_amount": 1234.56},
    )
    session.add(event)
    session.flush()
    row = SettlementCashflow(
        lifecycle_event_id=event.id, leg_key="settlement",
        position_id=position.id, currency="USD", direction="pay",
        counterparty="Acme Capital", derived_amount=1234.56,
        amount=1234.56, status="released",
    )
    session.add(row)
    session.flush()
    return row


def test_notice_writes_the_artifact_file(session, settings, cashflow):
    record = notice.generate_notice(
        session, cashflow_id=cashflow.id, actor="desk_user",
        artifact_dir=settings.artifact_dir,
    )
    path = settings.artifact_dir / record.artifact_path
    assert path.exists(), "a declared artifact must actually be written"
    assert path.read_text(encoding="utf-8").startswith("# Settlement Notice")


def test_recorded_sha256_matches_the_bytes_on_disk(session, settings, cashflow):
    record = notice.generate_notice(
        session, cashflow_id=cashflow.id, actor="desk_user",
        artifact_dir=settings.artifact_dir,
    )
    body = (settings.artifact_dir / record.artifact_path).read_bytes()
    assert record.content_sha256 == hashlib.sha256(body).hexdigest()


def test_rendering_is_deterministic(session, cashflow):
    payload = notice.notice_payload(session, cashflow)
    assert notice.render_notice_markdown(payload) == notice.render_notice_markdown(payload)


def test_notice_states_the_amount_currency_and_counterparty(session, cashflow):
    payload = notice.notice_payload(session, cashflow)
    body = notice.render_notice_markdown(payload)
    assert "1234.56" in body
    assert "USD" in body
    assert "Acme Capital" in body


def test_regenerating_supersedes_and_increments_version(session, settings, cashflow):
    first = notice.generate_notice(
        session, cashflow_id=cashflow.id, actor="a",
        artifact_dir=settings.artifact_dir,
    )
    second = notice.generate_notice(
        session, cashflow_id=cashflow.id, actor="a",
        artifact_dir=settings.artifact_dir,
    )
    session.refresh(first)
    assert first.version == 1 and second.version == 2
    assert first.status == "superseded"
    assert second.status == "generated"
    assert (settings.artifact_dir / first.artifact_path).exists(), (
        "the superseded artifact stays on disk as evidence"
    )


def test_notice_is_refused_without_a_counterparty(session, settings, cashflow):
    cashflow.counterparty = None
    session.flush()
    with pytest.raises(SettlementValidationError):
        notice.generate_notice(session, cashflow_id=cashflow.id, actor="a",
                               artifact_dir=settings.artifact_dir)


def test_notice_is_refused_without_an_amount(session, settings, cashflow):
    cashflow.amount = None
    cashflow.status = "needs_amount"
    session.flush()
    with pytest.raises(SettlementValidationError):
        notice.generate_notice(session, cashflow_id=cashflow.id, actor="a",
                               artifact_dir=settings.artifact_dir)


def test_snapshot_freezes_the_values_at_render_time(session, settings, cashflow):
    record = notice.generate_notice(
        session, cashflow_id=cashflow.id, actor="a",
        artifact_dir=settings.artifact_dir,
    )
    cashflow.amount = 9999.0
    session.flush()
    assert record.payload_snapshot["amount"] == 1234.56


def test_generation_is_logged_on_the_cashflow(session, settings, cashflow):
    from app.models import SettlementCashflowEvent

    notice.generate_notice(session, cashflow_id=cashflow.id, actor="desk_user",
                           artifact_dir=settings.artifact_dir)
    actions = [
        e.action
        for e in session.query(SettlementCashflowEvent).filter_by(
            cashflow_id=cashflow.id
        )
    ]
    assert "notice_generated" in actions
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_notice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.settlement.notice'`

- [ ] **Step 3: Add `notice_generated` to the transition-log vocabulary**

In `backend/app/services/settlement/store.py`, extend the module docstring's action list is not required, but the DB column is `String(24)` — confirm `"notice_generated"` fits (17 chars, it does). No code change needed.

- [ ] **Step 4: Write the notice module**

Create `backend/app/services/settlement/notice.py`:

```python
"""Deterministic settlement notice documents.

A notice states an amount owed, so **no LLM writes any part of it**. The
template is fixed, every value comes from one cashflow row, and the render is
byte-stable for a given payload. That also means the reporting module's
narrator and its grounding guard are irrelevant here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    SettlementNotice,
    utcnow,
)
from .errors import SettlementValidationError
from .store import get_cashflow, log_event

_DIRECTION_PHRASE = {
    "pay": "payable by this desk to the counterparty",
    "receive": "receivable by this desk from the counterparty",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def notice_payload(session: Session, cashflow: SettlementCashflow) -> dict[str, Any]:
    """Freeze every value the notice states, at render time."""
    position = session.get(Position, cashflow.position_id)
    portfolio = (
        session.get(Portfolio, position.portfolio_id) if position is not None else None
    )
    event = session.get(PositionLifecycleEvent, cashflow.lifecycle_event_id)
    return {
        "cashflow_id": cashflow.id,
        "counterparty": cashflow.counterparty,
        "portfolio": portfolio.name if portfolio is not None else None,
        "portfolio_id": position.portfolio_id if position is not None else None,
        "position_id": cashflow.position_id,
        "underlying": position.underlying if position is not None else None,
        "product_type": position.product_type if position is not None else None,
        "quantity": position.quantity if position is not None else None,
        "lifecycle_event_id": cashflow.lifecycle_event_id,
        "event_type": event.event_type if event is not None else None,
        "event_recorded_at": _iso(event.created_at) if event is not None else None,
        "leg_key": cashflow.leg_key,
        "direction": cashflow.direction,
        "amount": cashflow.amount,
        "currency": cashflow.currency,
        "value_date": _iso(cashflow.value_date),
        "status": cashflow.status,
        "derived_basis": cashflow.derived_basis,
    }


def render_notice_markdown(payload: dict[str, Any]) -> str:
    """Pure render. Same payload in, same bytes out."""
    amount = payload.get("amount")
    amount_text = f"{amount:,.2f}" if isinstance(amount, (int, float)) else "—"
    lines = [
        "# Settlement Notice",
        "",
        f"**Counterparty:** {payload.get('counterparty') or '—'}",
        f"**Notice reference:** SN-{payload.get('cashflow_id')}",
        "",
        "## Amount",
        "",
        f"**{amount_text} {payload.get('currency') or ''}**".rstrip(),
        "",
        _DIRECTION_PHRASE.get(
            payload.get("direction", ""), "settlement amount"
        ).capitalize()
        + ".",
        "",
        f"**Value date:** {payload.get('value_date') or 'to be confirmed'}",
        "",
        "## Trade",
        "",
        f"- Portfolio: {payload.get('portfolio') or '—'} "
        f"(id {payload.get('portfolio_id')})",
        f"- Position: #{payload.get('position_id')} — "
        f"{payload.get('product_type') or '—'} on {payload.get('underlying') or '—'}",
        f"- Quantity: {payload.get('quantity')}",
        "",
        "## Basis",
        "",
        f"- Lifecycle event: #{payload.get('lifecycle_event_id')} "
        f"({payload.get('event_type') or '—'}), recorded "
        f"{payload.get('event_recorded_at') or '—'}",
        f"- Cash leg: {payload.get('leg_key')}",
        f"- Amount source: {payload.get('derived_basis') or 'manual'}",
        f"- Settlement status at issue: {payload.get('status')}",
        "",
    ]
    return "\n".join(lines)


def generate_notice(
    session: Session,
    *,
    cashflow_id: int,
    actor: str,
    artifact_dir: Path | None = None,
) -> SettlementNotice:
    cashflow = get_cashflow(session, cashflow_id)
    if not cashflow.counterparty:
        raise SettlementValidationError(
            "cannot issue a notice for a cashflow with no counterparty"
        )
    if cashflow.amount is None:
        raise SettlementValidationError(
            "cannot issue a notice for a cashflow with no amount"
        )

    payload = notice_payload(session, cashflow)
    body = render_notice_markdown(payload)
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    previous = session.execute(
        select(SettlementNotice)
        .where(SettlementNotice.cashflow_id == cashflow.id)
        .order_by(SettlementNotice.version.desc())
    ).scalars().all()
    version = (previous[0].version + 1) if previous else 1
    for stale_notice in previous:
        stale_notice.status = "superseded"

    target_dir = Path(artifact_dir) if artifact_dir else get_settings().artifact_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    basename = f"settlement-notice-{cashflow.id}-v{version}.md"
    (target_dir / basename).write_bytes(encoded)

    record = SettlementNotice(
        cashflow_id=cashflow.id,
        version=version,
        artifact_path=basename,
        content_sha256=digest,
        payload_snapshot=payload,
        status="generated",
        rendered_at=utcnow(),
        rendered_by=actor or "desk_user",
    )
    session.add(record)
    session.flush()

    log_event(
        session,
        cashflow=cashflow,
        action="notice_generated",
        from_status=cashflow.status,
        to_status=None,
        actor=actor,
        payload={"notice_version": version, "artifact_path": basename},
    )
    return record
```

- [ ] **Step 5: Confirm the settings accessor name**

Run: `grep -n "^def get_settings\|^def configure_settings" backend/app/config.py`
If the accessor is not `get_settings`, correct the import in `notice.py` to the real name.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_notice.py -v`
Expected: PASS (9 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/settlement/notice.py tests/test_settlement_notice.py
git commit -m "feat(settlement): deterministic Markdown settlement notices"
```

---

### Task 7: REST API

**Files:**
- Create: `backend/app/routers/settlement.py`
- Modify: `backend/app/schemas.py` (append settlement schemas)
- Modify: `backend/app/main.py:268` (import) and `main.py:4203` (include)
- Test: `tests/test_settlement_api.py`

**Interfaces:**
- Consumes: `store`, `generate`, `drift`, `notice` (Tasks 3–6).
- Produces: `build_settlement_router(get_db) -> APIRouter` mounted at `/api/settlement`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_api.py`:

```python
"""HTTP boundary. These tests assert writes actually PERSIST — a service that
copies a read-only _session_scope answers 200 and saves nothing."""
from __future__ import annotations

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
)


@pytest.fixture
def seeded(session):
    portfolio = Portfolio(name="API Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle",
        event_data={"settlement_amount": 500.0, "settlement_date": "2026-08-20"},
    )
    session.add(event)
    session.commit()
    return portfolio, position, event


def test_generate_then_list(client, seeded):
    portfolio, _, _ = seeded
    created = client.post("/api/settlement/cashflows/generate",
                          json={"portfolio_id": portfolio.id})
    assert created.status_code == 200
    assert created.json()["created"] == 1

    listed = client.get("/api/settlement/cashflows",
                        params={"portfolio_id": portfolio.id})
    assert listed.status_code == 200
    rows = listed.json()["items"]
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["row_version"] == 1


def test_patch_persists_across_requests(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]

    patched = client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={"amount": 640.5, "expected_row_version": row["row_version"]},
    )
    assert patched.status_code == 200

    refetched = client.get(f"/api/settlement/cashflows/{row['id']}").json()
    assert refetched["amount"] == 640.5, "the write did not persist"
    assert refetched["row_version"] == row["row_version"] + 1


def test_stale_row_version_returns_409(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]
    response = client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={"amount": 1.0, "expected_row_version": row["row_version"] + 5},
    )
    assert response.status_code == 409


def test_illegal_transition_returns_422(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]
    response = client.post(
        f"/api/settlement/cashflows/{row['id']}/settle",
        json={"expected_row_version": row["row_version"]},
    )
    assert response.status_code == 422


def test_release_then_settle_over_http(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]

    released = client.post(f"/api/settlement/cashflows/{row['id']}/release",
                           json={"expected_row_version": row["row_version"]})
    assert released.status_code == 200
    assert released.json()["status"] == "released"

    settled = client.post(
        f"/api/settlement/cashflows/{row['id']}/settle",
        json={"expected_row_version": released.json()["row_version"]},
    )
    assert settled.status_code == 200
    assert settled.json()["status"] == "settled"


def test_detail_includes_history_and_notices(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]
    detail = client.get(f"/api/settlement/cashflows/{row['id']}").json()
    assert detail["events"][0]["action"] == "generated"
    assert detail["notices"] == []


def test_missing_cashflow_returns_404(client):
    assert client.get("/api/settlement/cashflows/99999").status_code == 404


def test_summary_counts_by_status(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    summary = client.get("/api/settlement/summary",
                         params={"portfolio_id": portfolio.id}).json()
    assert summary["by_status"]["pending"] == 1
    assert summary["stale_count"] == 0


def test_refresh_reports_what_it_checked(client, seeded):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    refreshed = client.post("/api/settlement/cashflows/refresh",
                            json={"portfolio_id": portfolio.id}).json()
    assert refreshed["checked"] == 1
    assert refreshed["flagged"] == 0


def test_notice_endpoint_writes_and_returns_the_artifact(client, seeded, settings):
    portfolio, _, _ = seeded
    client.post("/api/settlement/cashflows/generate",
                json={"portfolio_id": portfolio.id})
    row = client.get("/api/settlement/cashflows",
                     params={"portfolio_id": portfolio.id}).json()["items"][0]
    client.patch(f"/api/settlement/cashflows/{row['id']}",
                 json={"counterparty": "Acme Capital",
                       "expected_row_version": row["row_version"]})

    created = client.post(f"/api/settlement/cashflows/{row['id']}/notice", json={})
    assert created.status_code == 200
    body = created.json()
    assert body["version"] == 1
    assert (settings.artifact_dir / body["artifact_path"]).exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_api.py -v`
Expected: FAIL — 404 on every route (router not mounted).

- [ ] **Step 3: Add the schemas**

Append to `backend/app/schemas.py`:

```python
# --- Settlement ------------------------------------------------------------


class SettlementCashflowOut(BaseModel):
    id: int
    lifecycle_event_id: int
    leg_key: str
    position_id: int
    portfolio_id: int | None = None
    underlying: str | None = None
    product_type: str | None = None
    event_type: str | None = None
    currency: str
    counterparty: str | None = None
    direction: str
    derived_amount: float | None = None
    derived_value_date: date | None = None
    derived_basis: str | None = None
    amount: float | None = None
    value_date: date | None = None
    status: str
    stale: bool
    stale_reason: dict | None = None
    last_checked_at: datetime | None = None
    block_reason: str | None = None
    notes: str | None = None
    row_version: int
    created_at: datetime
    updated_at: datetime


class SettlementCashflowEventOut(BaseModel):
    id: int
    action: str
    from_status: str | None = None
    to_status: str | None = None
    actor: str
    reason: str | None = None
    payload: dict
    created_at: datetime


class SettlementNoticeOut(BaseModel):
    id: int
    version: int
    artifact_path: str
    content_sha256: str
    status: str
    rendered_at: datetime
    rendered_by: str


class SettlementCashflowDetailOut(SettlementCashflowOut):
    events: list[SettlementCashflowEventOut] = Field(default_factory=list)
    notices: list[SettlementNoticeOut] = Field(default_factory=list)


class SettlementCashflowListOut(BaseModel):
    items: list[SettlementCashflowOut]
    total: int
    limit: int
    offset: int


class SettlementSweepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None


class SettlementGenerateOut(BaseModel):
    created: int
    skipped: int


class SettlementRefreshOut(BaseModel):
    checked: int
    flagged: int
    cleared: int


class SettlementCashflowPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: int
    amount: float | None = None
    value_date: date | None = None
    counterparty: str | None = None
    notes: str | None = None


class SettlementActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: int
    reason: str | None = None


class SettlementNoticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = "desk_user"


class SettlementSummaryOut(BaseModel):
    by_status: dict[str, int]
    totals_by_currency: dict[str, float]
    stale_count: int
```

The router distinguishes "not supplied" from "explicitly null" using pydantic's
`model_fields_set`, so no sentinel field is needed on the schema. Confirm `date`,
`datetime`, `Field` and `ConfigDict` are already imported at the top of
`schemas.py`; add any that are missing.

Note on settings: `create_app(settings=...)` calls `configure_settings(settings)`
(`main.py:704`), so `get_settings().artifact_dir` inside the notice service
resolves to the test's `tmp_path` artifact dir. The API notice test can assert
against `settings.artifact_dir` directly.

- [ ] **Step 4: Write the router**

Create `backend/app/routers/settlement.py`:

```python
"""HTTP boundary for the Settlement module.

The services own validation, the state machine and persistence. This module
translates requests, maps typed domain errors onto status codes, and
serializes read models. It commits — unlike the read-only domain facades.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
)
from app.schemas import (
    SettlementActionIn,
    SettlementCashflowDetailOut,
    SettlementCashflowListOut,
    SettlementCashflowOut,
    SettlementCashflowPatchIn,
    SettlementGenerateOut,
    SettlementNoticeIn,
    SettlementNoticeOut,
    SettlementRefreshOut,
    SettlementSummaryOut,
    SettlementSweepIn,
)
from app.services.settlement import drift, generate, notice as notice_service, store
from app.services.settlement.errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)


def _raise(error: Exception) -> None:
    if isinstance(error, SettlementNotFoundError):
        raise HTTPException(status_code=404, detail=str(error))
    if isinstance(error, SettlementConflictError):
        raise HTTPException(status_code=409, detail=str(error))
    if isinstance(error, SettlementValidationError):
        raise HTTPException(status_code=422, detail=str(error))
    raise error


def _serialize(session: Session, row: SettlementCashflow) -> dict[str, Any]:
    position = session.get(Position, row.position_id)
    event = session.get(PositionLifecycleEvent, row.lifecycle_event_id)
    return {
        "id": row.id,
        "lifecycle_event_id": row.lifecycle_event_id,
        "leg_key": row.leg_key,
        "position_id": row.position_id,
        "portfolio_id": position.portfolio_id if position else None,
        "underlying": position.underlying if position else None,
        "product_type": position.product_type if position else None,
        "event_type": event.event_type if event else None,
        "currency": row.currency,
        "counterparty": row.counterparty,
        "direction": row.direction,
        "derived_amount": row.derived_amount,
        "derived_value_date": row.derived_value_date,
        "derived_basis": row.derived_basis,
        "amount": row.amount,
        "value_date": row.value_date,
        "status": row.status,
        "stale": bool(row.stale),
        "stale_reason": row.stale_reason,
        "last_checked_at": row.last_checked_at,
        "block_reason": row.block_reason,
        "notes": row.notes,
        "row_version": row.row_version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def build_settlement_router(
    *, get_db: Callable[[], Generator[Session, None, None]]
) -> APIRouter:
    router = APIRouter(prefix="/api/settlement", tags=["settlement"])

    @router.get("/cashflows", response_model=SettlementCashflowListOut)
    def list_cashflows(
        portfolio_id: int | None = None,
        position_id: int | None = None,
        status: str | None = None,
        counterparty: str | None = None,
        stale: bool | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
    ) -> SettlementCashflowListOut:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        if position_id is not None:
            query = query.where(SettlementCashflow.position_id == position_id)
        if status is not None:
            query = query.where(SettlementCashflow.status == status)
        if counterparty is not None:
            query = query.where(SettlementCashflow.counterparty == counterparty)
        if stale is not None:
            query = query.where(SettlementCashflow.stale.is_(stale))

        total = db.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
        rows = db.execute(
            query.order_by(SettlementCashflow.id.desc()).limit(limit).offset(offset)
        ).scalars().all()
        return SettlementCashflowListOut(
            items=[SettlementCashflowOut(**_serialize(db, r)) for r in rows],
            total=int(total),
            limit=limit,
            offset=offset,
        )

    @router.get("/cashflows/{cashflow_id}", response_model=SettlementCashflowDetailOut)
    def get_cashflow(
        cashflow_id: int, db: Session = Depends(get_db)
    ) -> SettlementCashflowDetailOut:
        row = db.execute(
            select(SettlementCashflow)
            .where(SettlementCashflow.id == cashflow_id)
            .options(
                selectinload(SettlementCashflow.events),
                selectinload(SettlementCashflow.notices),
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="cashflow not found")
        payload = _serialize(db, row)
        payload["events"] = [
            {
                "id": e.id, "action": e.action, "from_status": e.from_status,
                "to_status": e.to_status, "actor": e.actor, "reason": e.reason,
                "payload": e.payload or {}, "created_at": e.created_at,
            }
            for e in row.events
        ]
        payload["notices"] = [
            {
                "id": n.id, "version": n.version, "artifact_path": n.artifact_path,
                "content_sha256": n.content_sha256, "status": n.status,
                "rendered_at": n.rendered_at, "rendered_by": n.rendered_by,
            }
            for n in row.notices
        ]
        return SettlementCashflowDetailOut(**payload)

    @router.post("/cashflows/generate", response_model=SettlementGenerateOut)
    def generate_cashflows(
        body: SettlementSweepIn, db: Session = Depends(get_db)
    ) -> SettlementGenerateOut:
        result = generate.generate_missing(
            db, portfolio_id=body.portfolio_id, actor="desk_user"
        )
        db.commit()
        return SettlementGenerateOut(created=result.created, skipped=result.skipped)

    @router.post("/cashflows/refresh", response_model=SettlementRefreshOut)
    def refresh_cashflows(
        body: SettlementSweepIn, db: Session = Depends(get_db)
    ) -> SettlementRefreshOut:
        result = drift.refresh_drift(
            db, portfolio_id=body.portfolio_id, actor="desk_user"
        )
        db.commit()
        return SettlementRefreshOut(
            checked=result.checked, flagged=result.flagged, cleared=result.cleared
        )

    @router.patch("/cashflows/{cashflow_id}", response_model=SettlementCashflowOut)
    def patch_cashflow(
        cashflow_id: int,
        body: SettlementCashflowPatchIn,
        db: Session = Depends(get_db),
    ) -> SettlementCashflowOut:
        supplied = body.model_fields_set
        kwargs: dict[str, Any] = {}
        for field in ("amount", "value_date", "counterparty", "notes"):
            if field in supplied:
                kwargs[field] = getattr(body, field)
        try:
            row = store.edit_cashflow(
                db,
                cashflow_id=cashflow_id,
                expected_row_version=body.expected_row_version,
                actor="desk_user",
                **kwargs,
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(row)
        return SettlementCashflowOut(**_serialize(db, row))

    def _transition_route(action: str):
        def handler(
            cashflow_id: int,
            body: SettlementActionIn,
            db: Session = Depends(get_db),
        ) -> SettlementCashflowOut:
            try:
                row = store.transition(
                    db,
                    cashflow_id=cashflow_id,
                    action=action,
                    expected_row_version=body.expected_row_version,
                    actor="desk_user",
                    reason=body.reason,
                )
                db.commit()
            except Exception as error:  # noqa: BLE001
                db.rollback()
                _raise(error)
            db.refresh(row)
            return SettlementCashflowOut(**_serialize(db, row))

        return handler

    for action in ("release", "unrelease", "block", "unblock", "settle", "void"):
        router.add_api_route(
            f"/cashflows/{{cashflow_id}}/{action}",
            _transition_route(action),
            methods=["POST"],
            response_model=SettlementCashflowOut,
            name=f"settlement_{action}",
        )

    @router.post("/cashflows/{cashflow_id}/resync", response_model=SettlementCashflowOut)
    def resync(
        cashflow_id: int,
        body: SettlementActionIn,
        db: Session = Depends(get_db),
    ) -> SettlementCashflowOut:
        try:
            row = drift.resync_cashflow(
                db,
                cashflow_id=cashflow_id,
                expected_row_version=body.expected_row_version,
                actor="desk_user",
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(row)
        return SettlementCashflowOut(**_serialize(db, row))

    @router.post("/cashflows/{cashflow_id}/notice", response_model=SettlementNoticeOut)
    def create_notice(
        cashflow_id: int,
        body: SettlementNoticeIn,
        db: Session = Depends(get_db),
    ) -> SettlementNoticeOut:
        try:
            record = notice_service.generate_notice(
                db, cashflow_id=cashflow_id, actor=body.actor
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(record)
        return SettlementNoticeOut(
            id=record.id, version=record.version,
            artifact_path=record.artifact_path,
            content_sha256=record.content_sha256, status=record.status,
            rendered_at=record.rendered_at, rendered_by=record.rendered_by,
        )

    @router.get("/summary", response_model=SettlementSummaryOut)
    def summary(
        portfolio_id: int | None = None, db: Session = Depends(get_db)
    ) -> SettlementSummaryOut:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        rows = db.execute(query).scalars().all()
        by_status: dict[str, int] = {}
        totals: dict[str, float] = {}
        stale_count = 0
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            if row.amount is not None:
                totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
            if row.stale:
                stale_count += 1
        return SettlementSummaryOut(
            by_status=by_status, totals_by_currency=totals, stale_count=stale_count
        )

    return router
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`, after line 268 (`from .routers.reports import build_reports_router`) add:

```python
from .routers.settlement import build_settlement_router
```

And after line 4203 (`app.include_router(build_reports_router())`) add:

```python
    app.include_router(build_settlement_router(get_db=get_db))
```

`get_db` is a **closure defined inside `create_app`** (`main.py:759`), not a
module-level function — so this line must stay inside `create_app` alongside the
other `include_router` calls, and nothing outside can import it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_api.py -v`
Expected: PASS (10 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/settlement.py backend/app/schemas.py backend/app/main.py tests/test_settlement_api.py
git commit -m "feat(settlement): REST API for cashflows, transitions and notices"
```

---

### Task 8: Agent tools, registration and HITL

**Files:**
- Create: `backend/app/tools/settlement.py`
- Modify: `backend/app/tools/__init__.py` (imports + `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py` (`DEEP_AGENT_TOOL_NAMES`)
- Modify: `backend/app/services/deep_agent/hitl.py` (4 structures)
- Test: `tests/test_settlement_tools.py`, plus updates to `tests/test_hitl.py` and `tests/test_capability_assignments.py`

**Interfaces:**
- Consumes: `store`, `generate`, `drift`, `notice` (Tasks 3–6).
- Produces: 11 tools. Reads — `get_settlement_cashflows`, `get_settlement_cashflow`, `get_settlement_summary`. Writes — `generate_settlement_cashflows`, `update_settlement_cashflow`, `release_settlement_cashflow`, `unrelease_settlement_cashflow`, `block_settlement_cashflow`, `unblock_settlement_cashflow`, `void_settlement_cashflow`, `resync_settlement_cashflow`, `settle_settlement_cashflow`, `generate_settlement_notice`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_tools.py`:

```python
"""Tool registration, gating, and the approval-card summary."""
from __future__ import annotations

import pytest

_WRITE_TOOLS = {
    "generate_settlement_cashflows",
    "update_settlement_cashflow",
    "release_settlement_cashflow",
    "unrelease_settlement_cashflow",
    "block_settlement_cashflow",
    "unblock_settlement_cashflow",
    "void_settlement_cashflow",
    "resync_settlement_cashflow",
    "settle_settlement_cashflow",
    "generate_settlement_notice",
}
_READ_TOOLS = {
    "get_settlement_cashflows",
    "get_settlement_cashflow",
    "get_settlement_summary",
}
_ALL_TOOLS = _WRITE_TOOLS | _READ_TOOLS


def test_every_settlement_tool_is_in_quant_agent_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert _ALL_TOOLS <= names


def test_every_settlement_tool_is_allowlisted_for_deep_agents():
    """Registered but not allowlisted means silently dropped from every
    persona — the assemble_breach_report failure mode."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert _ALL_TOOLS <= DEEP_AGENT_TOOL_NAMES


def test_every_write_tool_is_gated():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    for name in _WRITE_TOOLS:
        assert name in INTERRUPT_TOOL_NAMES, f"{name} is not in the interrupt map"
        assert name in _RISK_LEVEL_BY_TOOL, f"{name} has no risk level"
        assert name in _LABEL_BY_TOOL, f"{name} has no label"


def test_only_settle_is_irreversible():
    from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL

    assert _RISK_LEVEL_BY_TOOL["settle_settlement_cashflow"] == "irreversible"
    for name in _WRITE_TOOLS - {"settle_settlement_cashflow"}:
        assert _RISK_LEVEL_BY_TOOL[name] == "write"


def test_settle_has_a_summary_builder():
    """A gated tool whose args are bare ids needs a preflight summary, or the
    approval card asks a human to approve two integers."""
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    assert "settle_settlement_cashflow" in _SUMMARY_BUILDERS


def test_summary_builder_states_the_money(session, settings):
    from app.models import (
        Portfolio, Position, PositionLifecycleEvent, SettlementCashflow,
    )
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    portfolio = Portfolio(name="Summary Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle", event_data={}
    )
    session.add(event)
    session.flush()
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id, leg_key="settlement",
        position_id=position.id, currency="USD", direction="pay",
        amount=1234.56, counterparty="Acme Capital", status="released",
    )
    session.add(cashflow)
    session.commit()

    text = _SUMMARY_BUILDERS["settle_settlement_cashflow"](
        {"cashflow_id": cashflow.id, "expected_row_version": 1}
    )
    assert "1234.56" in text
    assert "USD" in text
    assert "Acme Capital" in text


def test_summary_builder_never_raises_on_a_missing_row():
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    text = _SUMMARY_BUILDERS["settle_settlement_cashflow"]({"cashflow_id": 999999})
    assert isinstance(text, str) and text


def test_write_tools_are_domain_write_capability():
    from app.tools import QUANT_AGENT_TOOLS

    by_name = {t.name: t for t in QUANT_AGENT_TOOLS}
    for name in _WRITE_TOOLS:
        assert getattr(by_name[name], "__capability_group__", None) is not None, (
            f"{name} carries no capability group — the audit taxonomy and "
            f"FanoutReadOnlyMiddleware both key off it"
        )


def test_the_agent_surface_covers_every_mutating_rest_route():
    """The two surfaces must not drift: anything a user can do over HTTP, an
    agent can do with a tool.

    Derived from the REAL router, not a literal — add a mutating route without
    a tool and this fails, which is the whole point. `get_db` is a closure
    inside create_app, so a stand-in is passed; only route metadata is read.
    """
    from app.routers.settlement import build_settlement_router
    from app.tools import QUANT_AGENT_TOOLS

    router = build_settlement_router(get_db=lambda: iter(()))
    mutating_paths = {
        route.path
        for route in router.routes
        if set(getattr(route, "methods", set()) or set())
        & {"POST", "PATCH", "PUT", "DELETE"}
    }

    route_to_tool = {
        "/api/settlement/cashflows/generate": "generate_settlement_cashflows",
        "/api/settlement/cashflows/refresh": "generate_settlement_cashflows",
        "/api/settlement/cashflows/{cashflow_id}": "update_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/release":
            "release_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/unrelease":
            "unrelease_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/block":
            "block_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/unblock":
            "unblock_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/settle":
            "settle_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/void":
            "void_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/resync":
            "resync_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/notice":
            "generate_settlement_notice",
    }

    assert mutating_paths == set(route_to_tool), (
        "a mutating REST route has no declared tool counterpart (or vice versa)"
    )
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert set(route_to_tool.values()) <= names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_tools.py -v`
Expected: FAIL — the tool names are absent from `QUANT_AGENT_TOOLS`.

- [ ] **Step 3: Write the tool module**

Create `backend/app/tools/settlement.py`:

```python
"""@tool wrappers over the Settlement module.

Thin: no logic is reimplemented here. Every read returns ``row_version``,
because every mutation requires it — optimistic concurrency is never
bypassed server-side.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .. import database
from ..models import Position, PositionLifecycleEvent, SettlementCashflow
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup
from ..services.settlement import drift, generate, notice as notice_service, store
from ..services.settlement.errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)


def _row_out(session, row: SettlementCashflow) -> dict[str, Any]:
    position = session.get(Position, row.position_id)
    event = session.get(PositionLifecycleEvent, row.lifecycle_event_id)
    return {
        "cashflow_id": row.id,
        "position_id": row.position_id,
        "portfolio_id": position.portfolio_id if position else None,
        "underlying": position.underlying if position else None,
        "product_type": position.product_type if position else None,
        "event_type": event.event_type if event else None,
        "lifecycle_event_id": row.lifecycle_event_id,
        "leg_key": row.leg_key,
        "direction": row.direction,
        "amount": row.amount,
        "currency": row.currency,
        "value_date": row.value_date.isoformat() if row.value_date else None,
        "counterparty": row.counterparty,
        "status": row.status,
        "stale": bool(row.stale),
        "stale_reason": row.stale_reason,
        "derived_amount": row.derived_amount,
        "derived_basis": row.derived_basis,
        "row_version": row.row_version,
    }


def _mutate(operation, **kwargs) -> dict[str, Any]:
    """Run one store/drift mutation in its own transaction, mapping typed
    domain failures onto structured tool results the model can act on."""
    database.init_db()
    with database.SessionLocal() as session:
        try:
            row = operation(session, **kwargs)
            session.commit()
        except SettlementConflictError as error:
            session.rollback()
            return {
                "ok": False, "error": "conflict", "hint": str(error),
                "next": "re-read the cashflow and retry with its current row_version",
            }
        except SettlementValidationError as error:
            session.rollback()
            return {"ok": False, "error": "invalid", "hint": str(error)}
        except SettlementNotFoundError as error:
            session.rollback()
            return {"ok": False, "error": "not_found", "hint": str(error)}
        session.refresh(row)
        return {"ok": True, **_row_out(session, row)}


class GetSettlementCashflowsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None
    position_id: int | None = None
    status: str | None = None
    stale_only: bool = False
    limit: int = 50


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_cashflows", args_schema=GetSettlementCashflowsInput)
def get_settlement_cashflows_tool(
    portfolio_id: int | None = None,
    position_id: int | None = None,
    status: str | None = None,
    stale_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List settlement cashflows implied by position lifecycle events, with
    their status (needs_amount/pending/blocked/released/settled/void), amount,
    currency, value date and counterparty. Each row carries row_version —
    every mutation requires it."""
    database.init_db()
    with database.SessionLocal() as session:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        if position_id is not None:
            query = query.where(SettlementCashflow.position_id == position_id)
        if status is not None:
            query = query.where(SettlementCashflow.status == status)
        if stale_only:
            query = query.where(SettlementCashflow.stale.is_(True))
        rows = session.execute(
            query.order_by(SettlementCashflow.id.desc()).limit(max(1, min(limit, 200)))
        ).scalars().all()
        return {"cashflows": [_row_out(session, r) for r in rows], "count": len(rows)}


class SettlementCashflowIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_cashflow", args_schema=SettlementCashflowIdInput)
def get_settlement_cashflow_tool(cashflow_id: int) -> dict[str, Any]:
    """Fetch one settlement cashflow with its full transition history, any
    notices issued, and the current row_version required by every mutation."""
    database.init_db()
    with database.SessionLocal() as session:
        row = session.get(SettlementCashflow, cashflow_id)
        if row is None:
            return {"ok": False, "error": "not_found",
                    "hint": f"no settlement cashflow {cashflow_id}"}
        payload = _row_out(session, row)
        payload["events"] = [
            {"action": e.action, "from_status": e.from_status,
             "to_status": e.to_status, "actor": e.actor, "reason": e.reason,
             "at": e.created_at.isoformat()}
            for e in row.events
        ]
        payload["notices"] = [
            {"version": n.version, "artifact_path": n.artifact_path,
             "status": n.status, "rendered_at": n.rendered_at.isoformat()}
            for n in row.notices
        ]
        return payload


class SettlementSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_summary", args_schema=SettlementSummaryInput)
def get_settlement_summary_tool(portfolio_id: int | None = None) -> dict[str, Any]:
    """Settlement position at a glance: cashflow counts by status, total
    amount per currency, and how many rows have drifted from their source
    lifecycle event."""
    database.init_db()
    with database.SessionLocal() as session:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        rows = session.execute(query).scalars().all()
        by_status: dict[str, int] = {}
        totals: dict[str, float] = {}
        stale = 0
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            if row.amount is not None:
                totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
            if row.stale:
                stale += 1
        return {"by_status": by_status, "totals_by_currency": totals,
                "stale_count": stale, "total": len(rows)}


class GenerateSettlementCashflowsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None
    refresh_drift: bool = True


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_settlement_cashflows", args_schema=GenerateSettlementCashflowsInput)
def generate_settlement_cashflows_tool(
    portfolio_id: int | None = None, refresh_drift: bool = True
) -> dict[str, Any]:
    """Backfill settlement cashflows for lifecycle events that have none, and
    optionally re-check every existing cashflow against its source event.
    INSERT-only and idempotent: it never rewrites an existing row, so running
    it twice is safe. HITL — requires confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        created = generate.generate_missing(
            session, portfolio_id=portfolio_id, actor="agent"
        )
        drifted = (
            drift.refresh_drift(session, portfolio_id=portfolio_id, actor="agent")
            if refresh_drift
            else None
        )
        session.commit()
        return {
            "ok": True,
            "created": created.created,
            "skipped": created.skipped,
            "checked": drifted.checked if drifted else 0,
            "flagged_stale": drifted.flagged if drifted else 0,
        }


class UpdateSettlementCashflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int
    expected_row_version: int
    amount: float | None = None
    value_date: date | None = None
    counterparty: str | None = None
    notes: str | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("update_settlement_cashflow", args_schema=UpdateSettlementCashflowInput)
def update_settlement_cashflow_tool(
    cashflow_id: int,
    expected_row_version: int,
    amount: float | None = None,
    value_date: date | None = None,
    counterparty: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edit a settlement cashflow's amount, value date, counterparty or notes.
    Legal only while needs_amount/pending/blocked — a released or settled row
    cannot be edited. Supplying an amount on a needs_amount row promotes it to
    pending. Requires expected_row_version from a preceding read.
    HITL — requires confirmation."""
    kwargs: dict[str, Any] = {}
    if amount is not None:
        kwargs["amount"] = amount
    if value_date is not None:
        kwargs["value_date"] = value_date
    if counterparty is not None:
        kwargs["counterparty"] = counterparty
    if notes is not None:
        kwargs["notes"] = notes
    return _mutate(
        store.edit_cashflow,
        cashflow_id=cashflow_id,
        expected_row_version=expected_row_version,
        actor="agent",
        **kwargs,
    )


class SettlementTransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int
    expected_row_version: int
    reason: str | None = None


def _transition_tool(action: str):
    def run(
        cashflow_id: int, expected_row_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        return _mutate(
            store.transition,
            cashflow_id=cashflow_id,
            action=action,
            expected_row_version=expected_row_version,
            actor="agent",
            reason=reason,
        )

    return run


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("release_settlement_cashflow", args_schema=SettlementTransitionInput)
def release_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Clear a pending settlement cashflow for payment. Reversible via
    unrelease_settlement_cashflow while it has not yet been marked settled.
    Requires expected_row_version. HITL — requires confirmation."""
    return _transition_tool("release")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("unrelease_settlement_cashflow", args_schema=SettlementTransitionInput)
def unrelease_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Pull a released settlement cashflow back to pending before it settles.
    Requires expected_row_version. HITL — requires confirmation."""
    return _transition_tool("unrelease")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("block_settlement_cashflow", args_schema=SettlementTransitionInput)
def block_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Stop a settlement cashflow from being paid, recording why. Reachable
    even from released — pulling a payment back is always allowed. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition_tool("block")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("unblock_settlement_cashflow", args_schema=SettlementTransitionInput)
def unblock_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Lift a block, returning the cashflow to pending. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition_tool("unblock")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("void_settlement_cashflow", args_schema=SettlementTransitionInput)
def void_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Terminate a settlement cashflow that should never be paid — typically
    because its source lifecycle event was cancelled. Terminal. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition_tool("void")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("settle_settlement_cashflow", args_schema=SettlementTransitionInput)
def settle_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Record that a released settlement cashflow has actually been paid.
    TERMINAL and unrecallable — this asserts money moved. Requires
    expected_row_version. HITL — requires confirmation in every mode."""
    return _transition_tool("settle")(cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("resync_settlement_cashflow", args_schema=SettlementTransitionInput)
def resync_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Adopt the freshly derived amount and value date from the source
    lifecycle event, clearing the stale flag. An explicitly overridden amount
    is preserved. Illegal on settled or void rows. Requires
    expected_row_version. HITL — requires confirmation."""
    return _mutate(
        drift.resync_cashflow,
        cashflow_id=cashflow_id,
        expected_row_version=expected_row_version,
        actor="agent",
    )


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_settlement_notice", args_schema=SettlementCashflowIdInput)
def generate_settlement_notice_tool(cashflow_id: int) -> dict[str, Any]:
    """Render a settlement notice document for one cashflow as a Markdown
    artifact and record its sha256. Requires a counterparty and an amount.
    Regenerating supersedes the previous version. HITL — requires
    confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        try:
            record = notice_service.generate_notice(
                session, cashflow_id=cashflow_id, actor="agent"
            )
            session.commit()
        except SettlementValidationError as error:
            session.rollback()
            return {"ok": False, "error": "invalid", "hint": str(error)}
        except SettlementNotFoundError as error:
            session.rollback()
            return {"ok": False, "error": "not_found", "hint": str(error)}
        return {
            "ok": True,
            "notice_version": record.version,
            "artifact_path": record.artifact_path,
            "content_sha256": record.content_sha256,
        }
```

- [ ] **Step 4: Register in `QUANT_AGENT_TOOLS`**

In `backend/app/tools/__init__.py`, add the import block alongside the other domain tool imports:

```python
from app.tools.settlement import (
    block_settlement_cashflow_tool,
    generate_settlement_cashflows_tool,
    generate_settlement_notice_tool,
    get_settlement_cashflow_tool,
    get_settlement_cashflows_tool,
    get_settlement_summary_tool,
    release_settlement_cashflow_tool,
    resync_settlement_cashflow_tool,
    settle_settlement_cashflow_tool,
    unblock_settlement_cashflow_tool,
    unrelease_settlement_cashflow_tool,
    update_settlement_cashflow_tool,
    void_settlement_cashflow_tool,
)
```

and append all thirteen to the `QUANT_AGENT_TOOLS` list.

- [ ] **Step 5: Allowlist in `DEEP_AGENT_TOOL_NAMES`**

In `backend/app/services/agents.py`, add to the `DEEP_AGENT_TOOL_NAMES` frozenset:

```python
        # settlement: registered in QUANT_AGENT_TOOLS AND allowlisted here —
        # absent from this allowlist a tool is silently dropped from every
        # persona's toolset.
        "get_settlement_cashflows",
        "get_settlement_cashflow",
        "get_settlement_summary",
        "generate_settlement_cashflows",
        "update_settlement_cashflow",
        "release_settlement_cashflow",
        "unrelease_settlement_cashflow",
        "block_settlement_cashflow",
        "unblock_settlement_cashflow",
        "void_settlement_cashflow",
        "resync_settlement_cashflow",
        "settle_settlement_cashflow",
        "generate_settlement_notice",
```

- [ ] **Step 6: Wire HITL — all four structures**

In `backend/app/services/deep_agent/hitl.py`:

Add to `INTERRUPT_TOOL_NAMES` (line 23):

```python
    "generate_settlement_cashflows",
    "update_settlement_cashflow",
    "release_settlement_cashflow",
    "unrelease_settlement_cashflow",
    "block_settlement_cashflow",
    "unblock_settlement_cashflow",
    "void_settlement_cashflow",
    "resync_settlement_cashflow",
    "settle_settlement_cashflow",
    "generate_settlement_notice",
```

Add to `_RISK_LEVEL_BY_TOOL` (line 71):

```python
    # Settlement. "write" = interactive only; AUTO strips these from the
    # interrupt map. Deliberate: release is recallable (unrelease exists,
    # block is reachable from released), and every transition is audited.
    "generate_settlement_cashflows": "write",
    "update_settlement_cashflow": "write",
    "release_settlement_cashflow": "write",
    "unrelease_settlement_cashflow": "write",
    "block_settlement_cashflow": "write",
    "unblock_settlement_cashflow": "write",
    "void_settlement_cashflow": "write",
    "resync_settlement_cashflow": "write",
    "generate_settlement_notice": "write",
    # "irreversible", NOT "write": marking a cashflow settled asserts money
    # moved and cannot be recalled. AUTO mode must still stop here.
    "settle_settlement_cashflow": "irreversible",
```

Add to `_LABEL_BY_TOOL` (line 141):

```python
    "generate_settlement_cashflows": "Generate settlement cashflows",
    "update_settlement_cashflow": "Edit settlement cashflow",
    "release_settlement_cashflow": "Release cashflow for payment",
    "unrelease_settlement_cashflow": "Recall released cashflow",
    "block_settlement_cashflow": "Block settlement cashflow",
    "unblock_settlement_cashflow": "Unblock settlement cashflow",
    "void_settlement_cashflow": "Void settlement cashflow",
    "resync_settlement_cashflow": "Resync cashflow to its source event",
    "settle_settlement_cashflow": "Mark cashflow SETTLED",
    "generate_settlement_notice": "Generate settlement notice",
```

Add the summary builder above `_SUMMARY_BUILDERS` (line 354):

```python
def _summarize_settle_settlement_cashflow(args: dict[str, Any]) -> str:
    """The interrupt fires before the tool body runs, so the raw args are just
    ``{cashflow_id, expected_row_version}`` — meaningless to a human asked to
    confirm that money moved. Reads the row so the card states the amount."""
    cashflow_id = args.get("cashflow_id")
    if not isinstance(cashflow_id, int):
        return "Mark settlement cashflow SETTLED"

    from app import database
    from app.models import Position, SettlementCashflow

    try:
        database.init_db()
        with database.SessionLocal() as session:
            row = session.get(SettlementCashflow, cashflow_id)
            if row is None:
                return f"Mark settlement cashflow #{cashflow_id} SETTLED (not found)"
            amount = "unknown amount" if row.amount is None else f"{row.amount:,.2f}"
            head = f"Mark SETTLED: {amount} {row.currency} {row.direction}"
            extras: list[str] = []
            if row.counterparty:
                extras.append(f"cpty {row.counterparty}")
            if row.value_date:
                extras.append(f"value {row.value_date.isoformat()}")
            position = session.get(Position, row.position_id)
            if position is not None:
                extras.append(
                    f"position #{position.id} {position.product_type} "
                    f"on {position.underlying}"
                )
            extras.append(f"currently {row.status}")
            if row.stale:
                extras.append("STALE vs source event")
            return head + (" — " + ", ".join(extras) if extras else "")
    except Exception:
        # Card rendering must never 500 the turn over a preview lookup.
        return f"Mark settlement cashflow #{cashflow_id} SETTLED"
```

and register it:

```python
    "settle_settlement_cashflow": _summarize_settle_settlement_cashflow,
```

- [ ] **Step 7: Update the exact-set pins**

Run: `.venv/bin/python -m pytest tests/test_hitl.py tests/test_capability_assignments.py -v`
These assert exact sets and will fail with a diff naming every new tool. Update the expected sets in both files to include the ten gated settlement tools (and thirteen total in the capability count).

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settlement_tools.py tests/test_hitl.py tests/test_capability_assignments.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/tools/settlement.py backend/app/tools/__init__.py backend/app/services/agents.py backend/app/services/deep_agent/hitl.py tests/test_settlement_tools.py tests/test_hitl.py tests/test_capability_assignments.py
git commit -m "feat(settlement): agent tools with HITL gating and approval-card summary"
```

---

### Task 9: Skill and persona routing

**Files:**
- Create: `skills/workflows/settlement/manage-settlement-cashflows/SKILL.md`
- Modify: `backend/app/services/deep_agent/persona_domains.py`
- Test: catalog test updates (enumerate first — see Step 1)

**Interfaces:**
- Consumes: the tool names from Task 8.
- Produces: a routable skill under the `settlement` domain, visible to the `trader` persona.

- [ ] **Step 1: Enumerate the exact-set catalog tests that will break**

Run: `grep -rln "book-position" tests/`
Every file listed asserts an exact skill catalog and will need the new skill path added. Note them before writing the skill.

- [ ] **Step 2: Add the domain to the trader persona**

In `backend/app/services/deep_agent/persona_domains.py`, add `"settlement"` to the `"trader"` tuple, directly after `"positions"`:

```python
    "trader": (
        "positions",
        "settlement",
        "products",
        "try-solve",
        "pricing",
        "hedging",
        "market-data",
        "portfolios",
        "reporting",
        "rfq",
        "snowballs",
        "desk-workflows",
    ),
```

Tuple order is load-bearing — it is preserved into the persona's skill source list and controls catalog listing order in subagent prompts. Settlement sits beside positions because it follows booking.

- [ ] **Step 3: Write the skill**

Create `skills/workflows/settlement/manage-settlement-cashflows/SKILL.md`:

```markdown
---
name: manage-settlement-cashflows
description: Work the settlement queue — review cashflows generated from position lifecycle events, fill missing amounts, release or block them, mark them settled, and issue settlement notices.
routing:
  personas: [trader]
  when: >
    The user asks about settlement, cashflows, what the desk owes or is owed,
    releasing or blocking a payment, marking something settled, chasing an
    unpaid amount, or producing a settlement notice for a counterparty.
---

# Manage settlement cashflows

Settlement tracks the cash that position lifecycle events imply. It does not
calculate payoffs: an amount either came with the lifecycle event or a human
supplied it.

## The states

| Status | Meaning |
|---|---|
| `needs_amount` | The event generated cash and nobody has said how much. |
| `pending` | Has an amount, awaiting release. |
| `blocked` | Explicitly stopped, with a reason. |
| `released` | Cleared to pay. Recallable until settled. |
| `settled` | Confirmed paid. Terminal. |
| `void` | Should never be paid. Terminal. |

## Procedure

1. **Survey.** `get_settlement_summary(portfolio_id=...)` for counts by status
   and how many rows have drifted from their source event.
2. **Backfill if the queue looks empty.** `generate_settlement_cashflows(
   portfolio_id=...)` picks up lifecycle events with no cashflow yet. It is
   INSERT-only and idempotent — running it twice is safe and rewrites nothing.
3. **List the work.** `get_settlement_cashflows(portfolio_id=..., status=...)`.
   Every row carries `row_version`; you need it for every mutation.
4. **Resolve `needs_amount` rows.** Never invent a number. Ask the desk, or
   read it from the lifecycle event. Then
   `update_settlement_cashflow(cashflow_id, expected_row_version, amount=...)`.
5. **Handle stale rows before releasing anything.** A `stale` row has diverged
   from its source lifecycle event — read `stale_reason` for the delta. Either
   `resync_settlement_cashflow` to adopt the new derived values, or
   `void_settlement_cashflow` if the source event was cancelled. Do not release
   a stale row without resolving it.
6. **Release.** `release_settlement_cashflow(cashflow_id, expected_row_version)`.
7. **Notify.** `generate_settlement_notice(cashflow_id)` renders a Markdown
   notice. It requires a counterparty and an amount; set the counterparty with
   `update_settlement_cashflow` first if it is missing.
8. **Confirm.** `settle_settlement_cashflow` ONLY when the desk confirms the
   money actually moved. This is terminal and unrecallable — never mark a
   cashflow settled to tidy up a queue.

## Rules

- **Never invent an amount.** `needs_amount` is a correct, honest state. A
  fabricated figure is worse than an empty one.
- **On `error: conflict`,** re-read the cashflow and retry with the current
  `row_version`. Someone else changed the row.
- **Blocking is always allowed,** including on a released row. If something
  looks wrong, block first and ask afterwards.
- **Report what you did in the reply text,** naming the cashflow ids and
  amounts. A settlement action the desk cannot see in the transcript is a
  settlement action nobody can check.
```

- [ ] **Step 4: Update the catalog tests**

Add `skills/workflows/settlement/manage-settlement-cashflows` (matching each file's existing path convention) to the exact-set assertions in every file enumerated in Step 1.

- [ ] **Step 5: Verify the skill lints and routes**

Run: `.venv/bin/python -m pytest tests/ -k "skill" -q`
Expected: PASS. `skill_lint.py` cross-checks that the routing persona actually has the `settlement` domain — a failure here means Step 2 was skipped or the persona name is wrong.

- [ ] **Step 6: Commit**

```bash
git add skills/workflows/settlement backend/app/services/deep_agent/persona_domains.py tests/
git commit -m "feat(settlement): routable settlement skill for the trader persona"
```

---

### Task 10: Frontend Settlement page

**Files:**
- Create: `frontend/src/routes/Settlement.tsx`, `Settlement.live.tsx`, `Settlement.css`
- Create: `frontend/src/routes/Settlement.test.tsx`
- Modify: `frontend/src/main.tsx` (nav entry + route render)
- Modify: `frontend/src/lib/routing.ts` (route union)

**Interfaces:**
- Consumes: `/api/settlement/*` (Task 7).
- Produces: the `settlement` route.

- [ ] **Step 1: Read the frontend guide and an existing page**

Run:
```bash
cat frontend/CLAUDE.md
sed -n 1,80p frontend/src/routes/Audit.tsx
sed -n 1,60p frontend/src/routes/Audit.live.tsx
```
Token-only styling is non-negotiable. `Settlement.tsx` holds the pure presentational component and its props type; `Settlement.live.tsx` holds data fetching and wires it. Mirror that split exactly.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/routes/Settlement.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Settlement, type SettlementCashflowRow } from './Settlement';

const row = (over: Partial<SettlementCashflowRow> = {}): SettlementCashflowRow => ({
  id: 1,
  position_id: 7,
  underlying: 'AAPL',
  product_type: 'SnowballOption',
  event_type: 'settle',
  leg_key: 'settlement',
  direction: 'pay',
  amount: 1234.56,
  currency: 'USD',
  value_date: '2026-08-20',
  counterparty: 'Acme Capital',
  status: 'pending',
  stale: false,
  stale_reason: null,
  derived_amount: 1234.56,
  row_version: 1,
  ...over,
});

describe('Settlement', () => {
  it('renders a cashflow row with its amount and counterparty', () => {
    render(<Settlement rows={[row()]} total={1} summary={null} />);
    expect(screen.getByText(/Acme Capital/)).toBeInTheDocument();
    expect(screen.getByText(/1,234.56/)).toBeInTheDocument();
  });

  it('marks a needs_amount row distinctly from a zero amount', () => {
    render(
      <Settlement
        rows={[row({ amount: null, status: 'needs_amount' })]}
        total={1}
        summary={null}
      />,
    );
    expect(screen.getByText(/needs amount/i)).toBeInTheDocument();
  });

  it('flags a stale row', () => {
    render(
      <Settlement
        rows={[row({ stale: true, stale_reason: { kind: 'derived_values_changed' } })]}
        total={1}
        summary={null}
      />,
    );
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
  });

  it('shows an empty state rather than a bare table', () => {
    render(<Settlement rows={[]} total={0} summary={null} />);
    expect(screen.getByText(/no settlement cashflows/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/Settlement.test.tsx`
Expected: FAIL — cannot resolve `./Settlement`.

- [ ] **Step 4: Build the presentational component**

Create `frontend/src/routes/Settlement.tsx`:

```tsx
import { useMemo } from 'react';
import { DataTablePage } from '../components/templates/DataTablePage';
import type { Column } from '../components/Table';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Empty } from '../components/Empty';
import './Settlement.css';

export interface SettlementCashflowRow {
  id: number;
  position_id: number;
  underlying: string | null;
  product_type: string | null;
  event_type: string | null;
  leg_key: string;
  direction: string;
  amount: number | null;
  currency: string;
  value_date: string | null;
  counterparty: string | null;
  status: string;
  stale: boolean;
  stale_reason: Record<string, unknown> | null;
  derived_amount: number | null;
  row_version: number;
}

export interface SettlementSummary {
  by_status: Record<string, number>;
  totals_by_currency: Record<string, number>;
  stale_count: number;
}

export interface SettlementProps {
  rows: SettlementCashflowRow[];
  total: number;
  summary: SettlementSummary | null;
  loading?: boolean;
  error?: string | null;
  selectedId?: number | null;
  onRowClick?: (row: SettlementCashflowRow) => void;
  toolbar?: React.ComponentProps<typeof DataTablePage>['toolbar'];
  actions?: React.ReactNode;
  overlays?: React.ReactNode;
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  needs_amount: 'warn',
  pending: 'ink',
  blocked: 'neg',
  released: 'pos',
  settled: 'pos',
  void: 'ink',
};

const STATUS_LABEL: Record<string, string> = {
  needs_amount: 'needs amount',
  pending: 'pending',
  blocked: 'blocked',
  released: 'released',
  settled: 'settled',
  void: 'void',
};

function money(amount: number | null): string {
  if (amount === null || amount === undefined) return '—';
  return amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function Settlement({
  rows, total, summary, loading, error, selectedId, onRowClick,
  toolbar, actions, overlays,
}: SettlementProps) {
  const columns = useMemo<Column<SettlementCashflowRow>[]>(() => [
    {
      key: 'position',
      header: 'Position',
      // minmax(0, fr) only. The Table primitive renders each row as its own
      // CSS grid, so max-content/auto tracks resolve per row and columns
      // stop lining up.
      width: 'minmax(0, 1.6fr)',
      render: (r) => (
        <span className="wl-settlement__position">
          #{r.position_id} {r.underlying ?? '—'}
          <span className="wl-settlement__muted"> {r.product_type ?? ''}</span>
        </span>
      ),
    },
    { key: 'event', header: 'Event', width: 'minmax(0, 1fr)',
      render: (r) => r.event_type ?? '—' },
    { key: 'leg', header: 'Leg', width: 'minmax(0, 1fr)', render: (r) => r.leg_key },
    { key: 'direction', header: 'Dir', width: '72px', render: (r) => r.direction },
    {
      key: 'amount',
      header: 'Amount',
      width: '140px',
      numeric: true,
      render: (r) =>
        r.amount === null
          ? <span className="wl-settlement__muted">needs amount</span>
          : money(r.amount),
    },
    { key: 'currency', header: 'Ccy', width: '64px', render: (r) => r.currency },
    { key: 'value_date', header: 'Value date', width: '124px',
      render: (r) => r.value_date ?? '—' },
    { key: 'counterparty', header: 'Counterparty', width: 'minmax(0, 1.4fr)',
      render: (r) => r.counterparty ?? '—' },
    {
      key: 'status',
      header: 'Status',
      width: '132px',
      render: (r) => (
        <Badge variant={STATUS_VARIANT[r.status] ?? 'ink'}>
          {STATUS_LABEL[r.status] ?? r.status}
        </Badge>
      ),
    },
    {
      key: 'flags',
      header: 'Flags',
      width: '92px',
      render: (r) => (r.stale ? <Badge variant="warn">stale</Badge> : null),
    },
  ], []);

  const chips = useMemo(() => {
    if (!summary) return [`${total} cashflows`];
    const parts = [`${total} cashflows`];
    for (const key of ['needs_amount', 'pending', 'released', 'settled']) {
      const count = summary.by_status[key];
      if (count) parts.push(`${count} ${STATUS_LABEL[key] ?? key}`);
    }
    if (summary.stale_count) parts.push(`${summary.stale_count} stale`);
    return parts;
  }, [summary, total]);

  return (
    <DataTablePage<SettlementCashflowRow>
      title="Settlement"
      chips={chips}
      actions={actions}
      feedback={error ?? (loading ? 'Loading…' : null)}
      toolbar={toolbar}
      table={{
        columns,
        rows,
        rowKey: (r) => r.id,
        selectedKey: selectedId ?? null,
        onRowClick,
      }}
      empty={
        <Empty
          message="No settlement cashflows"
          hint="Run Generate to pick up lifecycle events that have not been swept yet."
        />
      }
      overlays={overlays}
    />
  );
}
```

Verified during execution: `BadgeVariant` is `'pos' | 'neg' | 'warn' | 'info' |
'ink'` (all four used above are valid), and **`Empty` takes a `message` prop, not
children** — the code above already uses `message` + `hint`. All colors come from
tokens via `Badge`; add no raw hex in `Settlement.css`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/routes/Settlement.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 6: Build the live container**

Create `frontend/src/routes/Settlement.live.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Settlement, type SettlementCashflowRow, type SettlementSummary }
  from './Settlement';
import { Button } from '../components/Button';
import { Select } from '../components/Select';
import { Modal } from '../components/Modal';

interface CashflowDetail extends SettlementCashflowRow {
  derived_value_date: string | null;
  derived_basis: string | null;
  notes: string | null;
  block_reason: string | null;
  last_checked_at: string | null;
  events: Array<{
    action: string; from_status: string | null; to_status: string | null;
    actor: string; reason: string | null; created_at: string;
  }>;
  notices: Array<{
    id: number; version: number; artifact_path: string; status: string;
    rendered_at: string; rendered_by: string;
  }>;
}

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  ...['needs_amount', 'pending', 'blocked', 'released', 'settled', 'void'].map(
    (value) => ({ value, label: value }),
  ),
];

export function SettlementLive({
  onPageContextChange,
}: { onPageContextChange?: (ctx: unknown) => void }) {
  const [rows, setRows] = useState<SettlementCashflowRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<SettlementSummary | null>(null);
  const [status, setStatus] = useState('');
  const [staleOnly, setStaleOnly] = useState(false);
  const [detail, setDetail] = useState<CashflowDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (status) params.set('status', status);
      if (staleOnly) params.set('stale', 'true');
      const [listRes, summaryRes] = await Promise.all([
        fetch(`/api/settlement/cashflows?${params}`),
        fetch('/api/settlement/summary'),
      ]);
      if (!listRes.ok) throw new Error(`list failed (${listRes.status})`);
      const list = await listRes.json();
      setRows(list.items);
      setTotal(list.total);
      setSummary(summaryRes.ok ? await summaryRes.json() : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status, staleOnly]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    onPageContextChange?.({ page: 'settlement', cashflow_count: total });
  }, [onPageContextChange, total]);

  const openDetail = useCallback(async (row: SettlementCashflowRow) => {
    const res = await fetch(`/api/settlement/cashflows/${row.id}`);
    if (res.ok) setDetail(await res.json());
  }, []);

  const sweep = useCallback(async (path: 'generate' | 'refresh') => {
    setLoading(true);
    try {
      await fetch(`/api/settlement/cashflows/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      await load();
    } finally {
      setLoading(false);
    }
  }, [load]);

  const act = useCallback(async (
    row: SettlementCashflowRow, action: string, reason?: string,
  ) => {
    const res = await fetch(`/api/settlement/cashflows/${row.id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_row_version: row.row_version, reason }),
    });
    if (res.status === 409) {
      // Someone else moved the row. Never retry blind against a stale version.
      setError('This cashflow changed elsewhere — reloaded with current state.');
      await load();
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? `${action} failed (${res.status})`);
      return;
    }
    setDetail(null);
    await load();
  }, [load]);

  return (
    <Settlement
      rows={rows}
      total={total}
      summary={summary}
      loading={loading}
      error={error}
      selectedId={detail?.id ?? null}
      onRowClick={openDetail}
      actions={
        <>
          <Button onClick={() => void sweep('generate')}>Generate</Button>
          <Button onClick={() => void sweep('refresh')}>Refresh drift</Button>
        </>
      }
      toolbar={{
        left: (
          <>
            <Select
              value={status}
              options={STATUS_OPTIONS}
              onChange={setStatus}
              aria-label="Filter by status"
            />
            <label>
              <input
                type="checkbox"
                checked={staleOnly}
                onChange={(e) => setStaleOnly(e.target.checked)}
              />
              Stale only
            </label>
          </>
        ),
      }}
      overlays={
        detail && (
          <Modal onClose={() => setDetail(null)} title={`Cashflow #${detail.id}`}>
            <dl>
              <dt>Effective amount</dt>
              <dd>{detail.amount ?? '—'} {detail.currency}</dd>
              <dt>Derived amount</dt>
              <dd>{detail.derived_amount ?? '—'} ({detail.derived_basis ?? 'manual'})</dd>
              <dt>Last drift check</dt>
              <dd>{detail.last_checked_at ?? 'never checked'}</dd>
            </dl>
            {detail.stale && (
              <pre>{JSON.stringify(detail.stale_reason, null, 2)}</pre>
            )}
            <h3>History</h3>
            <ul>
              {detail.events.map((e, i) => (
                <li key={i}>
                  {e.created_at} — {e.action} by {e.actor}
                  {e.reason ? ` (${e.reason})` : ''}
                </li>
              ))}
            </ul>
            <h3>Notices</h3>
            {detail.notices.length === 0 ? <p>None issued.</p> : (
              <ul>
                {detail.notices.map((n) => (
                  <li key={n.id}>
                    <a href={`/artifacts/${n.artifact_path}`}>v{n.version}</a>
                    {' '}({n.status}, {n.rendered_at})
                  </li>
                ))}
              </ul>
            )}
            <div>
              <Button onClick={() => void act(detail, 'release')}>Release</Button>
              <Button onClick={() => void act(detail, 'block')}>Block</Button>
              <Button onClick={() => void act(detail, 'settle')}>Mark settled</Button>
              {detail.stale && (
                <Button onClick={() => void act(detail, 'resync')}>Resync</Button>
              )}
            </div>
          </Modal>
        )
      }
    />
  );
}
```

Check `TableToolbar`'s prop shape (`left`/`right` vs something else), `Select`'s
`onChange` signature (value vs event) and `Modal`'s props against their component
files, and adjust. Style the drawer in `Settlement.css` with tokens only.

- [ ] **Step 7: Register the route**

In `frontend/src/lib/routing.ts` add `'settlement'` to the route union. In `frontend/src/main.tsx` add the nav entry directly after `positions`:

```tsx
  { route: 'settlement' as const,  label: 'Settlement' },
```

and the render branch alongside the others:

```tsx
        {route === 'settlement' && <SettlementLive onPageContextChange={handlePageContextChange} />}
```

- [ ] **Step 8: Type-check and run the frontend suite**

Run:
```bash
cd frontend && npx tsc --noEmit && npm test
```
Expected: clean type-check. For vitest, compare the failing-file set against a same-machine `main` run — the suite is flaky under load and `main` alone varies 12→18 failures.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/routes/Settlement.tsx frontend/src/routes/Settlement.live.tsx frontend/src/routes/Settlement.css frontend/src/routes/Settlement.test.tsx frontend/src/main.tsx frontend/src/lib/routing.ts
git commit -m "feat(settlement): Settlement page with cashflow queue and detail drawer"
```

---

### Task 11: Full-suite verification and documentation

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Run the whole backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: no new failures versus `main`. Note that `main` itself carries 16 pre-existing failures (11 environment traps, 5 unexplained) — capture a `main` baseline first if you have not, and compare failing-test *names*, not counts. Never pipe pytest through `tail`.

- [ ] **Step 2: Verify migrations apply to the live DB path**

Run: `.venv/bin/python -m alembic upgrade head`
Expected: applies `0055_settlement_cashflows` cleanly.

- [ ] **Step 3: Update `CHANGELOG.md`**

Under `## [Unreleased]` → `### Added`:

```markdown
- **Settlement module** — cashflows auto-generated from position lifecycle
  events, governed through a `needs_amount → pending → released → settled`
  state machine with block/void/resync, optimistic concurrency, and an
  append-only transition log. Drift against the source event is flagged, never
  silently applied. Deterministic Markdown settlement notices. New
  `/api/settlement` REST surface, 13 agent tools, a routable
  `manage-settlement-cashflows` skill, and the **Settlement** nav page.
  Migration `0055`.
```

- [ ] **Step 4: Update `README.md`**

Add **Settlement** to the page/module list, one sentence: the desk's queue of cash implied by lifecycle events, with release/block/settle governance and settlement notices.

- [ ] **Step 5: Add the `CLAUDE.md` subsystem section**

Append a `## Settlement module` section after the report module section, covering:

- **Package map:** `services/settlement/{contracts,derive,store,generate,drift,notice,errors}.py`, `routers/settlement.py`, `tools/settlement.py`, skill path, `frontend/src/routes/Settlement.*`, migration `0055`.
- **The deriver is pure and total — it never raises.** That is what lets the inline hook in `create_lifecycle_event` be best-effort. `generate_missing` is the safety net; both are INSERT-only, enforced by `UNIQUE(lifecycle_event_id, leg_key)`.
- **`derived_amount` vs `amount` is load-bearing.** Without the frozen derived snapshot, drift cannot be detected on a row a human has edited — the recompute would be compared against the human's number and every edited row would read as drifted forever.
- **Drift flags, never applies.** `refresh_drift` never touches `amount`/`status`; `resync_cashflow` is the only re-baseline path and it preserves an explicit human override.
- **`needs_amount` is an honest state, not a bug.** The module governs cash, it never computes payoffs — that stays QuantArk's job.
- **`settle_settlement_cashflow` is `irreversible`; everything else is `"write"`.** Record why: release is recallable (`unrelease` exists, `block` is reachable from `released`), so only the unrecallable assertion is hard-gated. Note the standing caveat that `"write"` means AUTO mode executes it unattended.
- Any gotcha discovered during implementation — especially anything only a live run surfaced.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md README.md CLAUDE.md
git commit -m "docs: document the Settlement module"
```

---

## Deferred / explicitly out of scope

Netting across cashflows · a `Counterparty` entity or standing settlement instructions · payment-file or instruction export · PDF/DOCX rendering · per-desk configurable notice templates · FX conversion into a reporting currency · payoff calculation for any product family · a `settlement` domain for the `risk_manager` persona (trader only for now).
