"""Deterministic settlement notice documents.

A notice states an amount owed, so **no LLM writes any part of it**. The
template is fixed, every value comes from one cashflow row, and the render is
byte-stable for a given payload. That also means the reporting module's
narrator and its grounding guard are irrelevant here — there is no prose to
ground.
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
    "pay": "Payable by this desk to the counterparty.",
    "receive": "Receivable by this desk from the counterparty.",
}


def _iso(value: Any) -> Any:
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
    amount_text = (
        f"{amount:,.2f}" if isinstance(amount, (int, float)) and not isinstance(
            amount, bool
        ) else "—"
    )
    currency = payload.get("currency") or ""
    lines = [
        "# Settlement Notice",
        "",
        f"**Counterparty:** {payload.get('counterparty') or '—'}",
        f"**Notice reference:** SN-{payload.get('cashflow_id')}",
        "",
        "## Amount",
        "",
        f"**{amount_text} {currency}**".rstrip(),
        "",
        _DIRECTION_PHRASE.get(payload.get("direction", ""), "Settlement amount."),
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

    previous = (
        session.execute(
            select(SettlementNotice)
            .where(SettlementNotice.cashflow_id == cashflow.id)
            .order_by(SettlementNotice.version.desc())
        )
        .scalars()
        .all()
    )
    version = (previous[0].version + 1) if previous else 1
    for stale_notice in previous:
        stale_notice.status = "superseded"

    target_dir = Path(artifact_dir) if artifact_dir else get_settings().artifact_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    basename = f"settlement-notice-{cashflow.id}-v{version}.md"
    # A record that declares an artifact must WRITE it — a declared-but-absent
    # path is a dangling pointer that sends a reader hunting a file that was
    # never created.
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


__all__ = ["generate_notice", "notice_payload", "render_notice_markdown"]
