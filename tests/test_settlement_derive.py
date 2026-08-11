"""The deriver is pure, total, and never raises."""
from __future__ import annotations

from datetime import date

from app.models import Position, PositionLifecycleEvent
from app.services.settlement.derive import (
    CASH_LEG_RULES,
    SINGLETON_LEG_KEYS,
    derive_cashflows,
)


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


def test_every_terminating_event_emits_the_same_singleton_settlement_leg():
    """Desk policy: a trade must never terminate silently, so knock_out /
    autocall / maturity / close each emit a settlement leg, and the later
    `settle` fills it.

    They therefore SHARE a leg_key by design. Two different lifecycle events
    means the UNIQUE(lifecycle_event_id, leg_key) constraint cannot see the
    economic duplicate, so the real one-per-position guard lives in
    generate.py — see test_settlement_generate.py. What this test pins is that
    the deriver keeps them recognisably the same leg, which is what makes that
    dedup possible at all.
    """
    position = _position()
    terminating = ("settle", "knock_out", "autocall", "maturity", "close")
    legs = {
        event_type: {
            d.leg_key
            for d in derive_cashflows(
                position, _event(event_type, {"settlement_amount": 900.0})
            )
        }
        for event_type in terminating
    }
    assert all(keys == {"settlement"} for keys in legs.values()), legs
    assert "settlement" in SINGLETON_LEG_KEYS, (
        "settlement must be singleton-per-position or terminations double count"
    )


def test_coupons_are_not_singleton_so_a_schedule_is_not_collapsed():
    assert "coupon" not in SINGLETON_LEG_KEYS
    drafts = derive_cashflows(
        _position(), _event("coupon_paid", {"coupon_amount": 42.5})
    )
    assert [d.leg_key for d in drafts] == ["coupon"]
    assert drafts[0].amount == 42.5
    assert drafts[0].basis == "event_data.coupon_amount"


def test_open_derives_premium_from_the_position():
    drafts = derive_cashflows(_position(quantity=100.0, entry_price=2.5), _event("open"))
    assert len(drafts) == 1
    assert drafts[0].leg_key == "premium"
    assert drafts[0].amount == 250.0
    assert drafts[0].direction == "pay"
    assert drafts[0].basis == "position.entry_price*quantity"


def test_a_short_position_receives_its_premium():
    drafts = derive_cashflows(_position(quantity=-100.0, entry_price=2.5), _event("open"))
    assert drafts[0].amount == 250.0
    assert drafts[0].direction == "receive"


def test_zero_premium_is_needs_amount_not_a_zero_cashflow():
    drafts = derive_cashflows(_position(entry_price=0.0), _event("open"))
    assert drafts[0].amount is None
    assert drafts[0].basis == "none"


def test_premium_ignores_unrelated_event_data_keys():
    """`open` declares no amount_keys, so the position resolver is its sole
    source today. Pinned so that adding amount_keys later — which WOULD let an
    explicitly recorded premium override the computed one — is a deliberate
    change rather than an accident.
    """
    drafts = derive_cashflows(
        _position(quantity=100.0, entry_price=2.5),
        _event("open", {"premium_amount": 999.0}),
    )
    assert drafts[0].amount == 250.0
    assert drafts[0].basis == "position.entry_price*quantity"


def test_a_negative_amount_flips_direction_rather_than_storing_a_negative():
    drafts = derive_cashflows(
        _position(), _event("settle", {"settlement_amount": -1250.0})
    )
    assert drafts[0].amount == 1250.0
    assert drafts[0].direction == "receive"


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
