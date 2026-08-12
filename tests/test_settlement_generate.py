"""Generation is INSERT-only (with one narrow, audited fill), idempotent, and
never blocks lifecycle events."""
from __future__ import annotations

import pytest

from app.models import (
    Portfolio,
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    SettlementCashflowEvent,
)
from app.services.settlement import generate


@pytest.fixture
def book(session):
    portfolio = Portfolio(name="Generation Test Book")
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
    return portfolio, position


def _event(session, position, event_type, data=None) -> PositionLifecycleEvent:
    event = PositionLifecycleEvent(
        position_id=position.id, event_type=event_type, event_data=data or {}
    )
    session.add(event)
    session.flush()
    return event


def _settle_event(session, position, amount=500.0) -> PositionLifecycleEvent:
    return _event(
        session,
        position,
        "settle",
        {"settlement_amount": amount, "settlement_date": "2026-08-20"},
    )


# ---------------------------------------------------------------------------
# Core sweep behaviour
# ---------------------------------------------------------------------------


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
    assert (
        generate.generate_missing(session, portfolio_id=position.portfolio_id).created
        == 1
    )


def test_generation_stamps_currency_from_the_position(session, book):
    _, position = book
    _settle_event(session, position)
    generate.generate_missing(session)
    assert session.query(SettlementCashflow).one().currency == "USD"


def test_amountless_event_lands_as_needs_amount(session, book):
    _, position = book
    _event(session, position, "settle")
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    assert row.status == "needs_amount"
    assert row.amount is None


def test_generation_writes_a_generated_log_row(session, book):
    _, position = book
    _settle_event(session, position)
    generate.generate_missing(session)
    logged = session.query(SettlementCashflowEvent).one()
    assert logged.action == "generated"
    assert logged.to_status in {"pending", "needs_amount"}


def test_sweep_fills_a_gap_the_hook_left(session, book):
    """The sweep is the safety net that makes the lenient hook acceptable."""
    _, position = book
    _settle_event(session, position)
    assert session.query(SettlementCashflow).count() == 0
    generate.generate_missing(session)
    assert session.query(SettlementCashflow).count() == 1


# ---------------------------------------------------------------------------
# Same-position dedup of singleton legs (desk decision 2026-08-10)
# ---------------------------------------------------------------------------


def test_knockout_then_settle_yields_exactly_one_settlement_row(session, book):
    """Two DIFFERENT lifecycle events both derive a `settlement` leg, so the
    UNIQUE(lifecycle_event_id, leg_key) constraint cannot see the duplicate.
    Generation must."""
    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)

    rows = session.query(SettlementCashflow).all()
    assert len(rows) == 1, [(r.id, r.leg_key, r.status) for r in rows]


def test_settle_fills_the_needs_amount_row_the_termination_left(session, book):
    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    assert row.status == "needs_amount" and row.amount is None

    _settle_event(session, position, amount=900.0)
    result = generate.generate_missing(session)

    session.refresh(row)
    assert result.filled == 1
    assert row.amount == 900.0
    assert row.status == "pending"


def test_the_fill_is_recorded_with_its_source_event(session, book):
    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    settle = _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)

    logged = (
        session.query(SettlementCashflowEvent)
        .filter_by(action="filled_from_event")
        .one()
    )
    assert logged.payload["source_lifecycle_event_id"] == settle.id
    assert logged.payload["amount"] == 900.0


def test_filling_leaves_derived_amount_alone_so_the_row_is_not_permanently_stale(
    session, book
):
    """derived_* is the snapshot of the row's OWN event. knock_out really did
    carry no amount, so rewriting derived_amount here would make drift
    detection compare 900 against a re-derived None forever."""
    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)

    row = session.query(SettlementCashflow).one()
    assert row.amount == 900.0
    assert row.derived_amount is None


def test_the_fill_is_idempotent(session, book):
    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)
    again = generate.generate_missing(session)

    assert again.filled == 0
    assert session.query(SettlementCashflow).count() == 1
    assert (
        session.query(SettlementCashflowEvent)
        .filter_by(action="filled_from_event")
        .count()
        == 1
    )


def test_a_fill_never_overwrites_an_amount_that_is_already_set(session, book):
    _, position = book
    _event(session, position, "knock_out", {"settlement_amount": 100.0})
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    assert row.amount == 100.0

    _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)

    session.refresh(row)
    assert row.amount == 100.0, "an existing number must never be rewritten"


def test_a_released_row_is_never_filled_by_a_later_event(session, book):
    from app.services.settlement import store

    _, position = book
    _event(session, position, "knock_out")
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    store.edit_cashflow(session, cashflow_id=row.id,
                        expected_row_version=row.row_version, actor="a", amount=50.0)
    store.transition(session, cashflow_id=row.id, action="release",
                     expected_row_version=row.row_version, actor="a")

    _settle_event(session, position, amount=900.0)
    generate.generate_missing(session)

    session.refresh(row)
    assert row.status == "released"
    assert row.amount == 50.0


def test_a_settled_row_does_not_block_a_new_settlement_after_reopen(session, book):
    """Dedup considers only NON-terminal rows, so a position that settles,
    reopens and settles again gets a second cashflow."""
    from app.services.settlement import store

    _, position = book
    _settle_event(session, position, amount=500.0)
    generate.generate_missing(session)
    row = session.query(SettlementCashflow).one()
    store.transition(session, cashflow_id=row.id, action="release",
                     expected_row_version=row.row_version, actor="a")
    store.transition(session, cashflow_id=row.id, action="settle",
                     expected_row_version=row.row_version, actor="a")

    _event(session, position, "reopen")
    _settle_event(session, position, amount=700.0)
    generate.generate_missing(session)

    assert session.query(SettlementCashflow).count() == 2


def test_coupons_are_not_deduped_so_a_schedule_survives(session, book):
    _, position = book
    for amount in (10.0, 20.0, 30.0):
        _event(session, position, "coupon_paid", {"coupon_amount": amount})
    generate.generate_missing(session)

    rows = session.query(SettlementCashflow).filter_by(leg_key="coupon").all()
    assert len(rows) == 3
    assert sorted(r.amount for r in rows) == [10.0, 20.0, 30.0]


def test_open_is_reachable_through_the_real_constructor(session, book):
    """The premium leg was dead for the life of this module because no product
    could record `open` — and the unit tests missed it by building the ORM
    object directly, proving satisfiability rather than reachability. This test
    goes through the gate."""
    from app.services.domains import positions as positions_svc

    _, position = book
    position.entry_price = 2.5
    position.quantity = 100.0
    session.flush()

    update = positions_svc.create_lifecycle_event(
        position_id=position.id,
        event_type="open",
        event_data={"source": "test"},
        session=session,
    )

    assert update.event.event_type == "open"
    row = session.query(SettlementCashflow).filter_by(leg_key="premium").one()
    assert row.amount == 250.0
    assert row.status == "pending"


def _vanilla_booking(portfolio_id: int, *, entry_price: float):
    """A minimal bookable vanilla. `ProductBookingSpec` is a `ProductSpec`:
    asset_class / product_family / quantark_class / underlying / currency /
    terms — there is no `family=` shorthand."""
    from app.services.domains.booking import BookingRequest, ProductBookingSpec

    return BookingRequest(
        portfolio_id=portfolio_id,
        product=ProductBookingSpec(
            asset_class="equity",
            product_family="vanilla",
            quantark_class="EuropeanVanillaOption",
            underlying="AAPL",
            currency="USD",
            terms={
                "strike": 100.0,
                "maturity_years": 1.0,
                "option_type": "CALL",
                "initial_price": 100.0,
            },
        ),
        quantity=10.0,
        entry_price=entry_price,
        engine_name="BlackScholesEngine",
    )


def test_booking_a_position_emits_open_and_its_premium(session, registered_underlying):
    """Every booking path funnels through book_position, so the cash lifecycle
    is covered from inception rather than from termination."""
    from app.services.domains.booking import book_position

    registered_underlying("AAPL")
    portfolio = Portfolio(name="Booking Emits Open")
    session.add(portfolio)
    session.flush()

    position = book_position(session, _vanilla_booking(portfolio.id, entry_price=3.0))
    session.flush()

    events = (
        session.query(PositionLifecycleEvent).filter_by(position_id=position.id).all()
    )
    assert [e.event_type for e in events] == ["open"]

    row = session.query(SettlementCashflow).filter_by(position_id=position.id).one()
    assert row.leg_key == "premium"
    assert row.amount == 30.0
    assert row.direction == "pay"


def test_booking_at_zero_entry_price_yields_needs_amount_not_a_zero_row(
    session, registered_underlying
):
    """`BookingRequest.entry_price` defaults to 0.0, so this is the common case,
    not an edge one. `needs_amount` honestly says 'premium owed, amount not
    recorded'; a zero row would claim the trade was free."""
    from app.services.domains.booking import book_position

    registered_underlying("AAPL")
    portfolio = Portfolio(name="No Entry Price")
    session.add(portfolio)
    session.flush()

    position = book_position(session, _vanilla_booking(portfolio.id, entry_price=0.0))
    session.flush()

    row = session.query(SettlementCashflow).filter_by(position_id=position.id).one()
    assert row.amount is None
    assert row.status == "needs_amount"


def test_open_generates_a_premium_leg_from_the_position(session, book):
    _, position = book
    position.entry_price = 2.5
    position.quantity = 100.0
    session.flush()
    _event(session, position, "open")
    generate.generate_missing(session)

    row = session.query(SettlementCashflow).filter_by(leg_key="premium").one()
    assert row.amount == 250.0
    assert row.direction == "pay"
    assert row.status == "pending"


# ---------------------------------------------------------------------------
# The inline hook must never be able to break lifecycle recording
# ---------------------------------------------------------------------------


def test_lifecycle_event_survives_a_broken_deriver(session, book, monkeypatch):
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


def test_the_inline_hook_generates_on_a_normal_lifecycle_event(session, book):
    from app.services.domains import positions as positions_domain

    _, position = book
    positions_domain.create_lifecycle_event(
        position_id=position.id,
        event_type="settle",
        event_data={"settlement_amount": 321.0},
        actor="desk_user",
        session=session,
    )
    row = session.query(SettlementCashflow).one()
    assert row.amount == 321.0
    assert row.status == "pending"


# ---------------------------------------------------------------------------
# Counterparty resolution
# ---------------------------------------------------------------------------


def test_counterparty_resolves_from_a_booked_confirmation(session, book):
    from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade

    _, position = book
    batch = ConfirmationBatch()
    session.add(batch)
    session.flush()
    document = ConfirmationDocument(
        batch_id=batch.id,
        filename="conf.pdf",
        stored_path="/tmp/conf.pdf",
        sha256="b" * 64,
        byte_len=1024,
        mime="application/pdf",
    )
    session.add(document)
    session.flush()
    session.add(
        ExtractedTrade(
            document_id=document.id,
            family="SnowballOption",
            counterparty="Acme Capital",
            booked_position_id=position.id,
        )
    )
    session.flush()
    assert generate.resolve_counterparty(session, position) == "Acme Capital"


def test_counterparty_falls_back_to_the_rfq_client(session, book):
    from app.models import RFQ

    _, position = book
    rfq = RFQ(client_name="Zenith Partners")
    session.add(rfq)
    session.flush()
    position.rfq_id = rfq.id
    session.flush()
    assert generate.resolve_counterparty(session, position) == "Zenith Partners"


def test_counterparty_is_none_when_unknown(session, book):
    _, position = book
    assert generate.resolve_counterparty(session, position) is None
