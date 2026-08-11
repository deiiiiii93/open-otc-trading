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
    SettlementNotFoundError,
    SettlementValidationError,
)


@pytest.fixture
def cashflow(session) -> SettlementCashflow:
    portfolio = Portfolio(name="Store Test Book")
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
    row = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="settlement",
        position_id=position.id,
        currency="USD",
        direction="pay",
        derived_amount=500.0,
        amount=500.0,
        status="pending",
    )
    session.add(row)
    session.flush()
    return row


def test_release_then_settle_is_the_happy_path(session, cashflow):
    released = store.transition(
        session,
        cashflow_id=cashflow.id,
        action="release",
        expected_row_version=cashflow.row_version,
        actor="desk_user",
    )
    assert released.status == "released"
    settled = store.transition(
        session,
        cashflow_id=released.id,
        action="settle",
        expected_row_version=released.row_version,
        actor="desk_user",
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
        session,
        cashflow_id=cashflow.id,
        action="block",
        expected_row_version=cashflow.row_version,
        actor="a",
        reason="counterparty dispute",
    )
    assert blocked.status == "blocked"
    assert blocked.block_reason == "counterparty dispute"


def test_unblock_clears_the_reason(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="block",
                     expected_row_version=cashflow.row_version, actor="a",
                     reason="waiting on ops")
    unblocked = store.transition(session, cashflow_id=cashflow.id, action="unblock",
                                 expected_row_version=cashflow.row_version, actor="a")
    assert unblocked.status == "pending"
    assert unblocked.block_reason is None


def test_stale_row_version_conflicts(session, cashflow):
    with pytest.raises(SettlementConflictError):
        store.transition(session, cashflow_id=cashflow.id, action="release",
                         expected_row_version=cashflow.row_version + 7, actor="a")


def test_unknown_cashflow_is_not_found(session):
    with pytest.raises(SettlementNotFoundError):
        store.transition(session, cashflow_id=999999, action="release",
                         expected_row_version=1, actor="a")


def test_unknown_action_is_rejected(session, cashflow):
    with pytest.raises(SettlementValidationError):
        store.transition(session, cashflow_id=cashflow.id, action="teleport",
                         expected_row_version=cashflow.row_version, actor="a")


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
        session,
        cashflow_id=cashflow.id,
        expected_row_version=cashflow.row_version,
        actor="a",
        amount=750.0,
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


def test_a_no_op_edit_does_not_bump_the_version(session, cashflow):
    before = cashflow.row_version
    store.edit_cashflow(session, cashflow_id=cashflow.id,
                        expected_row_version=before, actor="a", amount=500.0)
    assert cashflow.row_version == before
    assert session.query(SettlementCashflowEvent).filter_by(action="edited").count() == 0


def test_void_is_reachable_from_any_non_settled_status(session, cashflow):
    voided = store.transition(session, cashflow_id=cashflow.id, action="void",
                              expected_row_version=cashflow.row_version, actor="a")
    assert voided.status == "void"


def test_void_is_terminal(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="void",
                     expected_row_version=cashflow.row_version, actor="a")
    with pytest.raises(SettlementValidationError):
        store.transition(session, cashflow_id=cashflow.id, action="release",
                         expected_row_version=cashflow.row_version, actor="a")


def test_unrelease_returns_a_released_row_to_pending(session, cashflow):
    store.transition(session, cashflow_id=cashflow.id, action="release",
                     expected_row_version=cashflow.row_version, actor="a")
    pending = store.transition(session, cashflow_id=cashflow.id, action="unrelease",
                               expected_row_version=cashflow.row_version, actor="a")
    assert pending.status == "pending"


def test_settle_requires_release_first(session, cashflow):
    with pytest.raises(SettlementValidationError):
        store.transition(session, cashflow_id=cashflow.id, action="settle",
                         expected_row_version=cashflow.row_version, actor="a")
