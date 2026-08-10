"""Notices are deterministic, written to disk, and versioned."""
from __future__ import annotations

import hashlib

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
)
from app.services.settlement import notice
from app.services.settlement.errors import (
    SettlementNotFoundError,
    SettlementValidationError,
)


@pytest.fixture
def cashflow(session) -> SettlementCashflow:
    portfolio = Portfolio(name="Notice Test Book")
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
        event_data={"settlement_amount": 1234.56},
    )
    session.add(event)
    session.flush()
    row = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="settlement",
        position_id=position.id,
        currency="USD",
        direction="pay",
        counterparty="Acme Capital",
        derived_amount=1234.56,
        amount=1234.56,
        status="released",
    )
    session.add(row)
    session.flush()
    return row


def test_notice_writes_the_artifact_file(session, settings, cashflow):
    record = notice.generate_notice(
        session,
        cashflow_id=cashflow.id,
        actor="desk_user",
        artifact_dir=settings.artifact_dir,
    )
    path = settings.artifact_dir / record.artifact_path
    assert path.exists(), "a declared artifact must actually be written"
    assert path.read_text(encoding="utf-8").startswith("# Settlement Notice")


def test_recorded_sha256_matches_the_bytes_on_disk(session, settings, cashflow):
    record = notice.generate_notice(
        session,
        cashflow_id=cashflow.id,
        actor="desk_user",
        artifact_dir=settings.artifact_dir,
    )
    body = (settings.artifact_dir / record.artifact_path).read_bytes()
    assert record.content_sha256 == hashlib.sha256(body).hexdigest()


def test_rendering_is_deterministic(session, cashflow):
    payload = notice.notice_payload(session, cashflow)
    assert notice.render_notice_markdown(payload) == notice.render_notice_markdown(
        payload
    )


def test_notice_states_the_amount_currency_and_counterparty(session, cashflow):
    payload = notice.notice_payload(session, cashflow)
    body = notice.render_notice_markdown(payload)
    assert "1,234.56" in body
    assert "USD" in body
    assert "Acme Capital" in body


def test_notice_states_the_direction_unambiguously(session, cashflow):
    body = notice.render_notice_markdown(notice.notice_payload(session, cashflow))
    assert "payable" in body.lower()


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


def test_notice_for_an_unknown_cashflow_is_not_found(session, settings):
    with pytest.raises(SettlementNotFoundError):
        notice.generate_notice(session, cashflow_id=999999, actor="a",
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


def test_a_receivable_reads_as_receivable(session, settings, cashflow):
    cashflow.direction = "receive"
    session.flush()
    body = notice.render_notice_markdown(notice.notice_payload(session, cashflow))
    assert "receivable" in body.lower()
    assert "payable" not in body.lower()
