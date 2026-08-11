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
from app.services.settlement import drift, generate, store
from app.services.settlement.errors import SettlementValidationError


@pytest.fixture
def generated(session):
    portfolio = Portfolio(name="Drift Test Book")
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
    event, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()

    drift.refresh_drift(session)

    assert cashflow.stale is True
    assert cashflow.status == "released"
    assert cashflow.amount == 500.0


def test_drift_does_not_bump_row_version(session, generated):
    """Flagging is not a user mutation; a UI holding a row_version must stay
    able to act on the row it is looking at."""
    event, cashflow = generated
    before = cashflow.row_version
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    drift.refresh_drift(session)
    assert cashflow.row_version == before


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
    _, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="void",
                     expected_row_version=cashflow.row_version, actor="a")
    assert drift.refresh_drift(session).checked == 0


def test_settled_rows_are_not_checked(session, generated):
    _, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    store.transition(session, cashflow_id=cashflow.id, action="settle",
                     expected_row_version=cashflow.row_version, actor="a")
    assert drift.refresh_drift(session).checked == 0


def test_resync_rebaselines_derived_and_effective_values(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    drift.refresh_drift(session)

    resynced = drift.resync_cashflow(
        session,
        cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version,
        actor="desk_user",
    )

    assert resynced.derived_amount == 750.0
    assert resynced.amount == 750.0
    assert resynced.stale is False


def test_resync_is_illegal_on_a_settled_row(session, generated):
    _, cashflow = generated
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    store.transition(session, cashflow_id=cashflow.id, action="settle",
                     expected_row_version=cashflow.row_version, actor="a")
    with pytest.raises(SettlementValidationError):
        drift.resync_cashflow(session, cashflow_id=cashflow.id,
                              expected_row_version=cashflow.row_version, actor="a")


def test_resync_preserves_an_explicit_human_override(session, generated):
    event, cashflow = generated
    store.edit_cashflow(session, cashflow_id=cashflow.id,
                        expected_row_version=cashflow.row_version,
                        actor="a", amount=600.0)
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    drift.refresh_drift(session)

    resynced = drift.resync_cashflow(
        session,
        cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version,
        actor="a",
    )

    assert resynced.amount == 600.0, "an explicit human override must survive resync"
    assert resynced.derived_amount == 750.0
    assert resynced.stale is False


def test_a_filled_row_is_not_permanently_stale(session):
    """A row filled by a later `settle` keeps derived_amount=None, so
    re-deriving its OWN (knock_out) event must still agree."""
    portfolio = Portfolio(name="Fill Drift Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    for event_type, data in (("knock_out", {}), ("settle", {"settlement_amount": 900.0})):
        session.add(
            PositionLifecycleEvent(
                position_id=position.id, event_type=event_type, event_data=data
            )
        )
    session.flush()
    generate.generate_missing(session)

    row = session.query(SettlementCashflow).one()
    assert row.amount == 900.0 and row.derived_amount is None

    result = drift.refresh_drift(session)

    assert result.flagged == 0
    assert row.stale is False


def test_drift_can_be_scoped_to_specific_cashflows(session, generated):
    event, cashflow = generated
    event.event_data = {**event.event_data, "settlement_amount": 750.0}
    session.flush()
    assert drift.refresh_drift(session, cashflow_ids=[cashflow.id + 999]).checked == 0
    assert drift.refresh_drift(session, cashflow_ids=[cashflow.id]).checked == 1
