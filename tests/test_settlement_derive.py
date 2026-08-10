"""The deriver is pure, total, and never raises."""
from __future__ import annotations

from datetime import date

from app.models import Position, PositionLifecycleEvent
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
        _event(
            "settle",
            {
                "settlement_amount": "not-a-number",
                "settlement_date": "31st of Neveruary",
            },
        ),
    )
    assert drafts == [] or drafts[0].amount is None


def test_deriver_never_raises_when_event_data_is_not_a_dict():
    event = _event("settle")
    event.event_data = "corrupted"
    assert derive_cashflows(_position(), event) == []


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


def test_direction_is_always_pay_or_receive():
    for event_type in CASH_LEG_RULES:
        drafts = derive_cashflows(
            _position(), _event(event_type, {"settlement_amount": 1.0})
        )
        assert all(d.direction in {"pay", "receive"} for d in drafts), event_type


def test_leg_keys_are_unique_within_one_event():
    """Two legs sharing a leg_key would collide on the unique constraint and
    silently drop one of them at generation time."""
    for event_type in CASH_LEG_RULES:
        drafts = derive_cashflows(
            _position(), _event(event_type, {"settlement_amount": 1.0})
        )
        keys = [d.leg_key for d in drafts]
        assert len(keys) == len(set(keys)), f"{event_type} derives duplicate legs {keys}"
