"""Positions domain service.

Pure-Python facade over portfolio_membership / position_adapter / position_pricer.
Returns ORM objects; never JSON. Session-aware.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app import database
from app.models import (
    Portfolio,
    PortfolioKind,
    Position,
    PositionLifecycleEvent,
    PositionValuationResult,
    PositionValuationRun,
)
from app.services.audit import record_audit
from app.services import position_adapter, position_pricer
from app.services.domains.products import compatibility_terms_for_position
from app.services.portfolio_membership import resolve_positions

TRADE_SHEET = position_adapter.TRADE_SHEET

LIFECYCLE_EVENT_TARGETS: dict[str, str | None] = {
    "open": "open",
    "close": "closed",
    "settle": "closed",
    "reopen": "open",
    "knock_in": "knocked_in",
    "knock_out": "closed",
    "coupon_observation": None,
    "coupon_paid": None,
    "maturity": "closed",
    "autocall": "closed",
    "coupon_lock": None,
    "memory_coupon": None,
    "fixing": None,
    "custom": None,
}

PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    "SnowballOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "coupon_observation",
        "coupon_paid",
        "maturity",
        "custom",
    },
    "PhoenixOption": {
        "close",
        "settle",
        "autocall",
        "coupon_lock",
        "coupon_paid",
        "memory_coupon",
        "maturity",
        "custom",
    },
    "BarrierOption": {"close", "settle", "knock_in", "knock_out", "maturity", "custom"},
    "SingleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "DoubleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "AsianOption": {"close", "settle", "fixing", "custom"},
}


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """Yield a session; write paths in this module commit explicitly."""
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def valid_lifecycle_event_types(product_type: str) -> set[str]:
    """Return lifecycle events allowed for a product type."""
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, {"close", "settle", "custom"})


# ---------------------------------------------------------------------------
# Date helpers (private)
# ---------------------------------------------------------------------------


def _parse_tool_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _effective_date_window(
    *,
    accounting_date: date | str | None,
    effective_date_from: date | str | None,
    effective_date_to: date | str | None,
    effective_last_days: int | None,
) -> tuple[date | None, date | None]:
    start = _parse_tool_date(effective_date_from)
    end = _parse_tool_date(effective_date_to)
    if effective_last_days is not None:
        end = end or _parse_tool_date(accounting_date) or date.today()
        start = end - timedelta(days=effective_last_days - 1)
    return start, end


def _position_in_effective_window(
    position: Position, start: date | None, end: date | None
) -> bool:
    effective = getattr(position, "trade_effective_date", None)
    if effective is None:
        return False
    effective_date = effective.date() if isinstance(effective, datetime) else effective
    if start and effective_date < start:
        return False
    if end and effective_date > end:
        return False
    return True


def _filter_supplied_rows_by_effective_date(
    rows: list[dict[str, Any]],
    start: date | None,
    end: date | None,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    missing = 0
    for row in rows:
        effective = _parse_tool_date(row.get("trade_effective_date"))
        if effective is None:
            missing += 1
            continue
        if start and effective < start:
            continue
        if end and effective > end:
            continue
        filtered.append(row)
    return filtered, missing


# ---------------------------------------------------------------------------
# Pure (no DB)
# ---------------------------------------------------------------------------


def count_from_snapshot(snapshot: dict) -> int:
    """Return the number of positions in an in-memory snapshot dict."""
    return len(snapshot.get("positions", []))


def list_from_snapshot(
    snapshot_positions: list[dict],
    *,
    product_type: str | None = None,
    accounting_date: date | None = None,
    effective_date_from: date | None = None,
    effective_date_to: date | None = None,
    effective_last_days: int | None = None,
) -> tuple[list[dict], int]:
    """Filter an in-memory list of positions by product_type and effective date.

    Returns (filtered_rows, missing_effective_date_count). Pure: no DB access.
    """
    product_query = (product_type or "").strip().lower()
    rows = list(snapshot_positions)
    if product_query:
        rows = [
            row
            for row in rows
            if product_query in str(row.get("product_type", "")).lower()
        ]
    start, end = _effective_date_window(
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    if start or end:
        return _filter_supplied_rows_by_effective_date(rows, start, end)
    return rows, 0


# ---------------------------------------------------------------------------
# DB-backed reads
# ---------------------------------------------------------------------------


def _resolve_status_filtered(
    sess: Session,
    *,
    portfolio_id: int | None,
    status: str | None,
) -> tuple[Portfolio | None, list[Position]]:
    """Resolve a portfolio and return its (status-filtered) positions.

    Returns (portfolio_or_None, positions). When ``portfolio_id`` is None,
    falls back to the lowest-id portfolio (legacy tool behaviour).
    """
    portfolio: Portfolio | None
    if portfolio_id is not None:
        portfolio = sess.get(Portfolio, portfolio_id)
    else:
        portfolio = sess.query(Portfolio).order_by(Portfolio.id).first()
    if portfolio is None:
        return None, []
    resolved = sorted(resolve_positions(portfolio, sess), key=lambda p: p.id)
    if resolved:
        loaded_by_id = {
            position.id: position
            for position in sess.query(Position)
            .options(selectinload(Position.product))
            .filter(Position.id.in_([position.id for position in resolved]))
            .all()
        }
        resolved = [loaded_by_id.get(position.id, position) for position in resolved]
    if status:
        resolved = [p for p in resolved if p.status == status]
    return portfolio, resolved


def _apply_product_and_date_filters(
    rows: list[Position],
    *,
    product_type: str | None,
    accounting_date: date | None,
    effective_date_from: date | None,
    effective_date_to: date | None,
    effective_last_days: int | None,
) -> tuple[list[Position], int]:
    """Apply product_type + effective-date window filters.

    Returns ``(filtered_rows, missing_effective_date_count)``. The missing
    count is the number of rows skipped by the effective-date window
    because their ``trade_effective_date`` was None (only counted when a
    window is actually active).
    """
    product_query = (product_type or "").strip().lower()
    filtered = rows
    if product_query:
        filtered = [p for p in filtered if product_query in p.product_type.lower()]
    start, end = _effective_date_window(
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    missing = 0
    if start or end:
        before = filtered
        filtered = [
            p for p in before if _position_in_effective_window(p, start, end)
        ]
        missing = sum(1 for p in before if p.trade_effective_date is None)
    return filtered, missing


def list_filtered(
    *,
    portfolio_id: int | None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | None = None,
    effective_date_from: date | None = None,
    effective_date_to: date | None = None,
    effective_last_days: int | None = None,
    session: Session | None = None,
) -> list[Position]:
    """Resolve a portfolio's positions and apply filters.

    Uses portfolio_membership.resolve_positions so view portfolios match
    through their filter_rule / source_portfolio_ids / manual_includes,
    not via Position.portfolio_id (which is only correct for containers).
    """
    with _session_scope(session) as sess:
        _, status_filtered = _resolve_status_filtered(
            sess, portfolio_id=portfolio_id, status=status
        )
        filtered, _ = _apply_product_and_date_filters(
            status_filtered,
            product_type=product_type,
            accounting_date=accounting_date,
            effective_date_from=effective_date_from,
            effective_date_to=effective_date_to,
            effective_last_days=effective_last_days,
        )
        return filtered


def count(*, portfolio_id: int, session: Session | None = None) -> int:
    """Count open positions in a portfolio (delegates to list_filtered)."""
    return len(list_filtered(portfolio_id=portfolio_id, session=session))


# ---------------------------------------------------------------------------
# Position lifecycle writes
# ---------------------------------------------------------------------------


@dataclass
class PositionLifecycleUpdate:
    position: Position
    event: PositionLifecycleEvent


def _resolve_lifecycle_position(
    sess: Session,
    *,
    position_id: int | None,
    source_trade_id: str | None,
    portfolio_id: int | None,
) -> tuple[Portfolio, Position]:
    if position_id is None and not (source_trade_id or "").strip():
        raise ValueError("position_id or source_trade_id is required")

    position: Position | None
    if position_id is not None:
        position = sess.get(Position, position_id)
        if position is None:
            raise LookupError("Position not found")
        if portfolio_id is not None and position.portfolio_id != portfolio_id:
            raise LookupError("Position not found in portfolio")
    else:
        query = sess.query(Position).filter(
            Position.source_trade_id == source_trade_id.strip()
        )
        if portfolio_id is not None:
            query = query.filter(Position.portfolio_id == portfolio_id)
        matches = query.order_by(Position.id).all()
        if not matches:
            raise LookupError("Position not found")
        if len(matches) > 1:
            raise ValueError(
                "Multiple positions match source_trade_id; pass portfolio_id or position_id"
            )
        position = matches[0]

    portfolio = sess.get(Portfolio, position.portfolio_id)
    if portfolio is None:
        raise LookupError("Portfolio not found")
    if portfolio.kind != PortfolioKind.CONTAINER.value:
        raise ValueError(
            "Position management is only available for container portfolios"
        )
    return portfolio, position


def _resolve_lifecycle_event_position(
    sess: Session,
    *,
    lifecycle_event_id: int,
    position_id: int | None,
    source_trade_id: str | None,
    portfolio_id: int | None,
) -> tuple[Portfolio, Position, PositionLifecycleEvent]:
    event = sess.get(PositionLifecycleEvent, lifecycle_event_id)
    if event is None:
        raise LookupError("Lifecycle event not found")

    resolved_position_id = position_id if position_id is not None else event.position_id
    portfolio, position = _resolve_lifecycle_position(
        sess,
        position_id=resolved_position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
    )
    if source_trade_id and (position.source_trade_id or "").strip() != source_trade_id.strip():
        raise LookupError("Lifecycle event not found for source_trade_id")
    if event.position_id != position.id:
        raise LookupError("Lifecycle event not found for position")
    return portfolio, position, event


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_value(values: Any, index: int) -> Any:
    if isinstance(values, list):
        if not values:
            return None
        return values[min(index, len(values) - 1)]
    return values


def _money_close(left: float, right: float) -> bool:
    return abs(left - right) <= 0.01


def _gross_notional(position: Position, kwargs: dict[str, Any]) -> float | None:
    initial_price = _float_value(kwargs.get("initial_price"))
    multiplier = _float_value(kwargs.get("contract_multiplier"))
    quantity = abs(_float_value(position.quantity) or 1.0)
    if initial_price is not None and multiplier is not None:
        return abs(initial_price * multiplier * quantity)

    notional = _float_value(kwargs.get("notional") or kwargs.get("notional_amount"))
    if notional is not None:
        return abs(notional * quantity)
    return None


def _latest_knockout_date(sess: Session, position_id: int) -> date | None:
    event = (
        sess.query(PositionLifecycleEvent)
        .filter(
            PositionLifecycleEvent.position_id == position_id,
            PositionLifecycleEvent.event_type == "knock_out",
            PositionLifecycleEvent.cancelled_at.is_(None),
        )
        .order_by(PositionLifecycleEvent.id.desc())
        .first()
    )
    if event is None:
        return None
    return _parse_tool_date((event.event_data or {}).get("observation_date"))


def _ko_schedule_record(
    kwargs: dict[str, Any],
    settlement_date: date,
) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    records = _schedule_records(
        kwargs,
        "barrier_config.ko_observation_schedule",
        "ko_observation_schedule",
        "observation_schedule",
    )
    target = settlement_date.isoformat()
    for index, record in enumerate(records):
        if (record.get("observation_date") or record.get("date")) == target:
            return index, record
    return None, None


def _enrich_snowball_ko_settlement(
    sess: Session,
    position: Position,
    event_data: dict[str, Any],
) -> dict[str, Any]:
    if position.product_type != "SnowballOption":
        return event_data

    kwargs = dict(compatibility_terms_for_position(position)["product_kwargs"] or {})
    settlement_date = _parse_tool_date(event_data.get("settlement_date"))
    if settlement_date is None:
        settlement_date = _latest_knockout_date(sess, position.id)
    if settlement_date is None:
        return event_data

    schedule_index, record = _ko_schedule_record(kwargs, settlement_date)
    if schedule_index is None or record is None:
        return event_data

    principal = _gross_notional(position, kwargs)
    if principal is None:
        return event_data

    barrier_config = kwargs.get("barrier_config") or {}
    rate = _float_value(record.get("return_rate"))
    if rate is None:
        rate = _float_value(_list_value(barrier_config.get("ko_rate"), schedule_index))
    if rate is None:
        return event_data

    accrual_config = kwargs.get("accrual_config") or {}
    is_annualized = bool(
        record.get(
            "is_rate_annualized",
            accrual_config.get("is_annualized_ko", accrual_config.get("is_annualized")),
        )
    )
    accrual_factor = 1.0
    if is_annualized:
        raw_accrual_factor = _list_value(
            accrual_config.get("accrual_factors"),
            schedule_index,
        )
        accrual_factor = _float_value(raw_accrual_factor) or 1.0

    coupon_amount = principal * rate * accrual_factor
    if abs(coupon_amount) <= 0.01:
        return event_data

    supplied_amount = _float_value(event_data.get("settlement_amount"))
    total_amount = principal + coupon_amount
    enriched = dict(event_data)
    if supplied_amount is None or _money_close(supplied_amount, principal):
        enriched["settlement_amount"] = total_amount
        enriched["settlement_amount_basis"] = "ko_principal_plus_coupon"
    else:
        enriched["settlement_amount_basis"] = "supplied_amount_with_ko_coupon_components"

    enriched.setdefault("settlement_date", settlement_date.isoformat())
    enriched["principal_amount"] = principal
    enriched["coupon_amount"] = coupon_amount
    enriched["ko_return_rate"] = rate
    enriched["ko_accrual_factor"] = accrual_factor
    enriched["ko_coupon_annualized"] = is_annualized
    return enriched


def _enrich_lifecycle_event_data(
    sess: Session,
    position: Position,
    event_type: str,
    event_data: dict[str, Any],
) -> dict[str, Any]:
    if event_type == "settle":
        return _enrich_snowball_ko_settlement(sess, position, event_data)
    return event_data


def _is_cancelled(event: PositionLifecycleEvent) -> bool:
    return event.cancelled_at is not None


def _project_status_from_lifecycle(
    events: list[PositionLifecycleEvent],
    fallback_status: str,
) -> str:
    if not events:
        return fallback_status
    projected = events[0].old_status or fallback_status
    for event in events:
        if _is_cancelled(event):
            continue
        target_status = LIFECYCLE_EVENT_TARGETS.get(event.event_type)
        if target_status is not None:
            projected = target_status
    return projected


def create_lifecycle_event(
    *,
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PositionLifecycleUpdate:
    """Create a persisted lifecycle event and apply its status transition."""
    clean_event_type = (event_type or "").strip()
    if clean_event_type not in LIFECYCLE_EVENT_TARGETS:
        raise ValueError(f"Invalid event type '{event_type}'")
    with _session_scope(session) as sess:
        portfolio, position = _resolve_lifecycle_position(
            sess,
            position_id=position_id,
            source_trade_id=source_trade_id,
            portfolio_id=portfolio_id,
        )
        valid_types = valid_lifecycle_event_types(position.product_type or "")
        if clean_event_type not in valid_types:
            raise ValueError(
                f"Invalid event type '{clean_event_type}'. Valid types: {sorted(valid_types)}"
            )
        clean_event_data = _enrich_lifecycle_event_data(
            sess, position, clean_event_type, dict(event_data or {})
        )
        target_status = LIFECYCLE_EVENT_TARGETS.get(clean_event_type)
        old_status = position.status
        new_status = old_status
        if target_status is not None and old_status != target_status:
            position.status = target_status
            new_status = target_status
        event = PositionLifecycleEvent(
            position_id=position.id,
            event_type=clean_event_type,
            event_data=clean_event_data,
            old_status=old_status,
            new_status=new_status,
            actor=actor,
        )
        sess.add(event)
        portfolio.updated_at = datetime.utcnow()
        record_audit(
            sess,
            event_type="position.lifecycle_event",
            actor=actor,
            subject_type="position",
            subject_id=position.id,
            payload={
                "event_type": clean_event_type,
                "event_data": clean_event_data,
                "old_status": old_status,
                "new_status": new_status,
            },
        )
        sess.commit()
        sess.refresh(event)
        sess.refresh(position)
        return PositionLifecycleUpdate(position=position, event=event)


def _as_date(value: Any) -> date | None:
    """Coerce a date or ISO date string to a date (None if unparseable)."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def generate_asian_fixing_schedule(
    *,
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> int:
    """Generate informational ``fixing`` lifecycle events for an Asian option.

    Derives the SSE-business-day averaging schedule from the position's
    ``averaging_frequency`` + maturity + trade start date and records one
    ``fixing`` event per observation (each carrying observation_date + sequence).
    Returns the number of events created. Callers regenerating a schedule should
    cancel prior ``fixing`` events first.
    """
    from app.services.domains import schedules

    with _session_scope(session) as sess:
        _, position = _resolve_lifecycle_position(
            sess,
            position_id=position_id,
            source_trade_id=source_trade_id,
            portfolio_id=portfolio_id,
        )
        if (position.product_type or "") != "AsianOption":
            raise ValueError(
                "generate_asian_fixing_schedule requires an AsianOption position"
            )
        kwargs = position.product_kwargs or {}
        frequency = str(kwargs.get("averaging_frequency") or "MONTHLY")
        maturity = kwargs.get("maturity_years") or kwargs.get("maturity")
        start = _as_date(
            kwargs.get("trade_start_date")
            or kwargs.get("start_date")
            or kwargs.get("initial_date")
        )
        if maturity is None or start is None:
            raise ValueError(
                "Asian fixing schedule needs maturity_years and a trade start date"
            )
        records = schedules.asian_observation_records(
            start=start, maturity_years=float(maturity), frequency=frequency
        )
        target_id = position.id

        # Serialize concurrent generators for this position: take a row lock
        # before the read-cancel-create sequence so two simultaneous POSTs cannot
        # both see "no active events" and each insert a full schedule. Enforced on
        # Postgres; on SQLite, writes already serialize at commit for this
        # single-desk deployment.
        sess.query(Position).filter(Position.id == target_id).with_for_update().all()

        # Idempotent regeneration: cancel any existing active fixing events so a
        # retried/double POST does not duplicate the schedule.
        existing = (
            sess.query(PositionLifecycleEvent)
            .filter_by(position_id=target_id, event_type="fixing")
            .filter(PositionLifecycleEvent.cancelled_at.is_(None))
            .all()
        )
        for stale in existing:
            stale.cancelled_at = datetime.utcnow()
            stale.cancelled_by = actor
            stale.cancellation_reason = "regenerated"

        created = 0
        for rec in records:
            create_lifecycle_event(
                position_id=target_id,
                event_type="fixing",
                event_data={
                    "observation_date": rec["observation_date"].isoformat(),
                    "sequence": rec["sequence"],
                },
                actor=actor,
                session=sess,
            )
            created += 1
        return created


def capture_due_asian_fixings(
    session: Session, position_id: int, *, as_of: date | None = None
) -> int:
    """Capture observed prices for past Asian fixings (immutable snapshots).

    For each ``observation_records`` entry whose ``observation_date <= as_of``
    (default today) and whose ``observed_price`` is still null, write the
    ``MarketQuote`` close as-of that date for the position's underlying
    instrument. Already-captured prices are never overwritten, so the same call
    is idempotent and, run across all Asian positions, serves as the backfill.

    Returns the number of fixings newly captured.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.quotes import latest_quote

    as_of = as_of or datetime.utcnow().date()
    position = session.get(Position, position_id)
    if position is None or position.underlying_id is None:
        return 0
    kwargs = dict(position.product_kwargs or {})
    records = kwargs.get("observation_records")
    if not isinstance(records, list):
        return 0

    captured = 0
    new_records: list[Any] = []
    for record in records:
        if isinstance(record, dict):
            record = dict(record)
            if record.get("observed_price") is None:
                obs = _as_date(record.get("observation_date"))
                if obs is not None and obs <= as_of:
                    cutoff = datetime.combine(obs, datetime.max.time())
                    quote = latest_quote(
                        session, position.underlying_id, as_of=cutoff
                    )
                    # Only snapshot a print on the fixing date itself. A stale
                    # earlier quote must not be captured — the snapshot is
                    # immutable, so we wait for the correct-date close instead.
                    if quote is not None and quote.as_of.date() == obs:
                        record["observed_price"] = float(quote.price)
                        captured += 1
        new_records.append(record)

    if captured:
        kwargs["observation_records"] = new_records
        position.product_kwargs = kwargs
        flag_modified(position, "product_kwargs")
        session.flush()
    return captured


def cancel_lifecycle_event(
    *,
    lifecycle_event_id: int,
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
    reason: str | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PositionLifecycleUpdate:
    """Mark a lifecycle event cancelled and replay active events for status."""
    with _session_scope(session) as sess:
        portfolio, position, event = _resolve_lifecycle_event_position(
            sess,
            lifecycle_event_id=lifecycle_event_id,
            position_id=position_id,
            source_trade_id=source_trade_id,
            portfolio_id=portfolio_id,
        )
        if event.cancelled_at is not None:
            raise ValueError("Lifecycle event is already cancelled")

        old_position_status = position.status
        event.cancelled_at = datetime.utcnow()
        event.cancelled_by = actor
        event.cancellation_reason = (reason or "").strip() or None

        events = (
            sess.query(PositionLifecycleEvent)
            .filter(PositionLifecycleEvent.position_id == position.id)
            .order_by(PositionLifecycleEvent.created_at.asc(), PositionLifecycleEvent.id.asc())
            .all()
        )
        new_position_status = _project_status_from_lifecycle(
            events,
            fallback_status=old_position_status,
        )
        if position.status != new_position_status:
            position.status = new_position_status
        portfolio.updated_at = datetime.utcnow()
        record_audit(
            sess,
            event_type="position.lifecycle_event.cancelled",
            actor=actor,
            subject_type="position",
            subject_id=position.id,
            payload={
                "lifecycle_event_id": event.id,
                "cancelled_event_type": event.event_type,
                "reason": event.cancellation_reason,
                "old_status": old_position_status,
                "new_status": new_position_status,
            },
        )
        sess.commit()
        sess.refresh(event)
        sess.refresh(position)
        return PositionLifecycleUpdate(position=position, event=event)


# ---------------------------------------------------------------------------
# View aggregates for get_positions_tool
# ---------------------------------------------------------------------------


@dataclass
class PositionsView:
    """Aggregate view returned by :func:`get_positions_view`.

    Fields mirror the legacy ``get_positions_tool`` payload (database
    branch). ``portfolio_total_count`` and
    ``portfolio_counts_by_product_type`` are computed BEFORE the
    product_type filter (so the agent can see how the filter narrowed the
    result set); both are None for the snapshot variant where there is no
    enclosing portfolio.
    """

    positions: list[Any] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    counts_by_product_type: dict[str, int] = field(default_factory=dict)
    missing_effective_date_count: int = 0
    resolved_portfolio_id: int | None = None
    portfolio_total_count: int | None = None
    portfolio_counts_by_product_type: dict[str, int] | None = None


def _build_filters_dict(
    *,
    portfolio_id: int | None,
    product_type: str | None,
    status: str | None,
    accounting_date: date | str | None,
    effective_date_from: date | str | None,
    effective_date_to: date | str | None,
    effective_last_days: int | None,
) -> dict[str, Any]:
    start, end = _effective_date_window(
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    return {
        "portfolio_id": portfolio_id,
        "product_type": product_type,
        "status": status,
        "accounting_date": _date_iso(_parse_tool_date(accounting_date)),
        "effective_date_from": _date_iso(start),
        "effective_date_to": _date_iso(end),
        "effective_last_days": effective_last_days,
    }


def _counts_by_product_type(rows: list[Position]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for position in rows:
        counts[position.product_type] = counts.get(position.product_type, 0) + 1
    return counts


def _nested_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first_present(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _nested_value(data, path)
        if value is not None:
            return value
    return None


def _schedule_records(data: dict[str, Any], *paths: str) -> list[dict[str, Any]]:
    for path in paths:
        value = _nested_value(data, path)
        if isinstance(value, dict):
            records = value.get("records")
            if isinstance(records, list):
                return [row for row in records if isinstance(row, dict)]
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _first_schedule_value(records: list[dict[str, Any]], *keys: str) -> Any:
    if not records:
        return None
    first = records[0]
    for key in keys:
        value = first.get(key)
        if value is not None:
            return value
    return None


def _position_summary(position: Position) -> dict[str, Any]:
    """Return a compact, term-promoted row without raw product_kwargs."""
    compat = compatibility_terms_for_position(position)
    kwargs = dict(compat["product_kwargs"] or {})
    loaded_product = getattr(position, "__dict__", {}).get("product")
    effective = getattr(position, "trade_effective_date", None)
    if isinstance(effective, datetime):
        effective_iso: str | None = effective.date().isoformat()
    elif effective is not None:
        effective_iso = effective.isoformat()
    else:
        effective_iso = None

    ko_records = _schedule_records(
        kwargs,
        "barrier_config.ko_observation_schedule",
        "ko_observation_schedule",
        "observation_schedule",
    )
    row: dict[str, Any] = {
        "id": position.id,
        "portfolio_id": position.portfolio_id,
        "product_id": position.product_id,
        "source_trade_id": position.source_trade_id,
        "product": {
            "id": position.product_id,
            "product_family": getattr(loaded_product, "product_family", None),
            "quantark_class": getattr(loaded_product, "quantark_class", None),
            "underlying": compat["underlying"],
            "currency": getattr(loaded_product, "currency", None),
        }
        if position.product_id is not None
        else None,
        "underlying": compat["underlying"],
        "product_type": compat["product_type"],
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "position_kind": position.position_kind,
        "status": position.status,
        "trade_effective_date": effective_iso,
        "strike": _first_present(kwargs, "strike"),
        "expiry_date": _first_present(
            kwargs,
            "expiry_date",
            "expiry",
            "maturity_date",
        ),
        "option_type": _first_present(kwargs, "option_type"),
        "side": _first_present(kwargs, "side"),
        "currency": _first_present(kwargs, "currency"),
        "notional": _first_present(kwargs, "notional", "notional_amount"),
        "barrier": _first_present(kwargs, "barrier", "barrier_config.barrier"),
        "barrier_type": _first_present(
            kwargs,
            "barrier_type",
            "barrier_config.barrier_type",
        ),
        "upper_barrier": _first_present(
            kwargs,
            "upper_barrier",
            "barrier_config.upper_barrier",
        ),
        "lower_barrier": _first_present(
            kwargs,
            "lower_barrier",
            "barrier_config.lower_barrier",
        ),
        "initial_price": _first_present(kwargs, "initial_price"),
        "ki_barrier": _first_present(kwargs, "barrier_config.ki_barrier", "ki_barrier"),
        "coupon": _first_present(
            kwargs,
            "accrual_config.coupon_rate",
            "coupon_rate",
            "coupon",
        ),
        "participation_rate": _first_present(kwargs, "participation_rate"),
        "ko_observation_count": len(ko_records) if ko_records else None,
        "next_ko_date": _first_schedule_value(
            ko_records,
            "observation_date",
            "date",
        ),
        "next_ko_level": _first_schedule_value(
            ko_records,
            "barrier",
            "ko_level",
            "return_rate",
        ),
    }
    return {key: value for key, value in row.items() if value is not None}


def position_summaries(
    rows: list[Position],
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return compact position rows for agent reads.

    This is the first structured-read surface for Section A: agents get
    promoted terms and schedule counts without the raw product_kwargs blob.
    """
    return [_position_summary(position) for position in rows[:limit]]


def get_positions_view(
    *,
    portfolio_id: int | None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | str | None = None,
    effective_date_from: date | str | None = None,
    effective_date_to: date | str | None = None,
    effective_last_days: int | None = None,
    session: Session | None = None,
) -> PositionsView:
    """Resolve a portfolio, filter, and compute the full ``get_positions`` view.

    Owns the filtering, product-type bucketing, and missing-effective-date
    accounting that previously lived in ``tools/positions.py`` as
    ``_handle_database``. Returns a :class:`PositionsView` aggregate; the
    tool layer converts that into the legacy wire shape.
    """
    filters = _build_filters_dict(
        portfolio_id=portfolio_id,
        product_type=product_type,
        status=status,
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    with _session_scope(session) as sess:
        portfolio, status_filtered = _resolve_status_filtered(
            sess, portfolio_id=portfolio_id, status=status
        )
        resolved_id = portfolio.id if portfolio is not None else portfolio_id
        filters = {**filters, "portfolio_id": resolved_id}
        if portfolio is None:
            return PositionsView(
                filters=filters,
                resolved_portfolio_id=resolved_id,
                portfolio_total_count=0,
                portfolio_counts_by_product_type={},
            )
        portfolio_counts = _counts_by_product_type(status_filtered)
        filtered, missing = _apply_product_and_date_filters(
            status_filtered,
            product_type=product_type,
            accounting_date=_parse_tool_date(accounting_date),
            effective_date_from=_parse_tool_date(effective_date_from),
            effective_date_to=_parse_tool_date(effective_date_to),
            effective_last_days=effective_last_days,
        )
        return PositionsView(
            positions=filtered,
            filters=filters,
            counts_by_product_type=_counts_by_product_type(filtered),
            missing_effective_date_count=missing,
            resolved_portfolio_id=resolved_id,
            portfolio_total_count=len(status_filtered),
            portfolio_counts_by_product_type=portfolio_counts,
        )


def get_positions_view_from_snapshot(
    snapshot_positions: list[dict[str, Any]],
    *,
    product_type: str | None = None,
    accounting_date: date | str | None = None,
    effective_date_from: date | str | None = None,
    effective_date_to: date | str | None = None,
    effective_last_days: int | None = None,
    portfolio_id: int | None = None,
    status: str | None = "open",
) -> PositionsView:
    """Filter an in-memory positions list into the same aggregate shape.

    Parallel to :func:`get_positions_view` but operates on the
    LLM-supplied snapshot (``provided_context`` source). No portfolio
    counts because there is no enclosing portfolio.
    """
    filters = _build_filters_dict(
        portfolio_id=portfolio_id,
        product_type=product_type,
        status=status,
        accounting_date=accounting_date,
        effective_date_from=effective_date_from,
        effective_date_to=effective_date_to,
        effective_last_days=effective_last_days,
    )
    filtered, missing = list_from_snapshot(
        snapshot_positions,
        product_type=product_type,
        accounting_date=_parse_tool_date(accounting_date),
        effective_date_from=_parse_tool_date(effective_date_from),
        effective_date_to=_parse_tool_date(effective_date_to),
        effective_last_days=effective_last_days,
    )
    counts: dict[str, int] = {}
    for row in filtered:
        key = str(row.get("product_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return PositionsView(
        positions=filtered,
        filters=filters,
        counts_by_product_type=counts,
        missing_effective_date_count=missing,
    )


def latest_valuation_run(
    *,
    portfolio_id: int,
    session: Session | None = None,
) -> PositionValuationRun | None:
    """Return the most recent completed valuation run for a portfolio."""
    with _session_scope(session) as sess:
        return (
            sess.query(PositionValuationRun)
            .options(
                selectinload(PositionValuationRun.results).selectinload(
                    PositionValuationResult.position
                )
            )
            .filter(
                PositionValuationRun.portfolio_id == portfolio_id,
                PositionValuationRun.status.in_(
                    ("completed", "completed_with_errors")
                ),
            )
            .order_by(
                PositionValuationRun.created_at.desc(),
                PositionValuationRun.id.desc(),
            )
            .first()
        )


def latest_valuations(
    *,
    portfolio_id: int,
    limit: int = 500,
    session: Session | None = None,
) -> list[PositionValuationResult]:
    """Return the latest stored valuation results for a portfolio.

    Picks the most recent completed PositionValuationRun for the portfolio,
    deduplicates so each position contributes only its latest result, and
    truncates to ``limit`` rows.
    """
    run = latest_valuation_run(portfolio_id=portfolio_id, session=session)
    if run is None:
        return []
    # Deduplicate: keep only the latest result per position_id (latest = highest id)
    latest_by_position: dict[int, PositionValuationResult] = {}
    for result in run.results:
        existing = latest_by_position.get(result.position_id)
        if existing is None or result.id > existing.id:
            latest_by_position[result.position_id] = result
    rows = sorted(latest_by_position.values(), key=lambda r: r.id)
    return rows[:limit]


# ---------------------------------------------------------------------------
# XLSX import delegation
# ---------------------------------------------------------------------------


def import_from_xlsx(
    *,
    portfolio_id: int,
    xlsx_path: str | Path,
    sheet: str = TRADE_SHEET,
    session: Session | None = None,
) -> Any:
    """Import an OTC trade workbook into Position rows for the given portfolio."""
    with _session_scope(session) as sess:
        result = position_adapter.import_positions_from_xlsx(
            sess,
            portfolio_id=portfolio_id,
            xlsx_path=xlsx_path,
            sheet_name=sheet,
        )
        sess.commit()
        return result

