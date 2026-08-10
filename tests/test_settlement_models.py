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
