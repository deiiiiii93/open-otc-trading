"""The agent's lifecycle write surface.

Found by smoking the agent path after the vocabulary landed: the desk agent had
tools for only 4 of the 17 event types, so `exercise` / `expire` /
`barrier_reset` were reachable from REST and the UI but not from the agent —
the same "registered but not available" gap as `assemble_breach_report`.
"""
from __future__ import annotations

import pytest

from app.models import Instrument, Portfolio, PositionLifecycleEvent, SettlementCashflow
from app.services.domains import lifecycle_vocabulary as vocab
from app.tools.positions import (
    MarkKnockoutInput,
    book_position_tool,
    mark_knockout_tool,
    record_lifecycle_event_tool,
)


@pytest.fixture
def book(session):
    """A container portfolio plus a bookable (active + tagged) underlying."""
    session.add(
        Instrument(
            symbol="AAPL",
            display_name="AAPL",
            kind="stock",
            status="active",
            tags=["underlying"],
        )
    )
    portfolio = Portfolio(name="Lifecycle Agent Surface")
    session.add(portfolio)
    session.commit()
    return portfolio


def _book(portfolio_id: int, quantark_class: str, terms: dict, **kw) -> int:
    result = book_position_tool.invoke(
        {
            "portfolio_id": portfolio_id,
            "product": {
                "asset_class": "equity",
                "product_family": kw.pop("product_family", "vanilla"),
                "quantark_class": quantark_class,
                "underlying": "AAPL",
                "terms": terms,
            },
            "quantity": kw.pop("quantity", 10.0),
            "entry_price": kw.pop("entry_price", 3.0),
        }
    )
    assert result["ok"] is True, result
    return result["position"]["id"]


_VANILLA_TERMS = {
    "strike": 100.0,
    "maturity_years": 1.0,
    "option_type": "CALL",
    "initial_price": 100.0,
}
_BARRIER_TERMS = {**_VANILLA_TERMS, "barrier": 120.0, "barrier_type": "UP_OUT"}


# ---------------------------------------------------------------------------
# A. the agent can reach the whole vocabulary
# ---------------------------------------------------------------------------


def test_the_agent_can_record_every_event_type_its_family_allows(session, book):
    """The three hardcoded tools (close/settle/mark_knockout) covered 3 types.
    One generic tool covers all of them and never needs extending again."""
    covered = {"close", "settle", "knock_out", "fixing"}
    reachable_via_generic = set(vocab.LIFECYCLE_EVENT_TARGETS) - vocab.RETIRED_EVENT_TYPES
    assert reachable_via_generic - covered, "sanity: there were uncovered types"

    position_id = _book(book.id, "AmericanOption", _VANILLA_TERMS)
    result = record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "exercise",
            "event_data": {
                "exercise_date": "2026-09-01",
                "early": True,
                "settlement_amount": 500.0,
            },
        }
    )

    assert result["ok"] is True, result
    assert result["position"]["status"] == "closed"
    types = [
        e.event_type
        for e in session.query(PositionLifecycleEvent)
        .filter_by(position_id=position_id)
        .order_by(PositionLifecycleEvent.id)
    ]
    assert types == ["open", "exercise"]

    rows = {
        c.leg_key: (c.amount, c.status)
        for c in session.query(SettlementCashflow).filter_by(position_id=position_id)
    }
    assert rows["settlement"] == (500.0, "pending")


def test_expire_through_the_agent_books_no_settlement_row(session, book):
    """`expire` is the one terminating event that must NOT open a cash row:
    nothing is owed, and a `needs_amount` row would be a phantom obligation."""
    position_id = _book(book.id, "EuropeanVanillaOption", _VANILLA_TERMS)
    result = record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "expire",
            "event_data": {"expiry_date": "2026-09-01", "reason": "OTM at expiry"},
        }
    )

    assert result["ok"] is True
    assert result["position"]["status"] == "closed"
    legs = {
        c.leg_key
        for c in session.query(SettlementCashflow).filter_by(position_id=position_id)
    }
    assert legs == {"premium"}, "expire must not open a settlement row"


def test_the_tool_refuses_a_type_the_family_does_not_allow(session, book):
    """The family allowlist stays the single source of truth — the tool does not
    keep its own list, so it can never drift from the vocabulary."""
    position_id = _book(book.id, "EuropeanVanillaOption", _VANILLA_TERMS)
    result = record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "barrier_reset"}
    )

    assert result["ok"] is False
    assert "barrier_reset" in result["error"]
    # The error names what IS legal, so a model can recover without guessing.
    assert "exercise" in result["error"]


def _bare_position(session, portfolio_id: int, product_type: str) -> int:
    """A position of a given family without going through the booking gate.

    The snowball families need a full term sheet to book, which these two tests
    do not care about — they exercise the tool's vocabulary handling, and the
    only thing that decides it is `Position.product_type`.
    """
    from app.models import Position

    position = Position(
        portfolio_id=portfolio_id,
        underlying="AAPL",
        product_type=product_type,
        product_kwargs={},
        quantity=1.0,
        entry_price=1.0,
        currency="CNY",
    )
    session.add(position)
    session.commit()
    return position.id


def test_the_tool_refuses_a_retired_type(session, book):
    """`autocall` still resolves for historical rows but no family may record a
    new one."""
    position_id = _bare_position(session, book.id, "SnowballOption")
    result = record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "autocall"}
    )

    assert result["ok"] is False
    assert "autocall" in result["error"]


def test_barrier_reset_is_recordable_on_a_ko_reset_snowball(session, book):
    """The event that motivated the family split, driven end-to-end."""
    position_id = _bare_position(session, book.id, "KnockOutResetSnowballOption")
    result = record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "barrier_reset",
            "event_data": {"reset_date": "2026-09-01", "new_barrier_level": 98.0},
        }
    )

    assert result["ok"] is True
    # Non-transitioning: the position stays alive and no cash is implied.
    assert result["position"]["status"] == "open"
    legs = {
        c.leg_key
        for c in session.query(SettlementCashflow).filter_by(position_id=position_id)
    }
    assert legs == set(), "barrier_reset implies no cash"


# ---------------------------------------------------------------------------
# B. mark_knockout's `payoff` must reach the settlement leg
# ---------------------------------------------------------------------------


def test_mark_knockout_payoff_prices_the_settlement_row(session, book):
    """`payoff` used to be written to event_data and read by nothing, so an
    agent that correctly supplied the KO payoff still produced a `needs_amount`
    row and silently dropped the number it had just reported."""
    position_id = _book(
        book.id, "BarrierOption", _BARRIER_TERMS, product_family="barrier", quantity=1.0
    )
    result = mark_knockout_tool.invoke(
        {
            "position_id": position_id,
            "ko_level": 120.0,
            "observation_date": "2026-08-20",
            "payoff": 900.0,
        }
    )

    assert result["ok"] is True
    row = (
        session.query(SettlementCashflow)
        .filter_by(position_id=position_id, leg_key="settlement")
        .one()
    )
    assert row.amount == 900.0
    assert row.status == "pending"


def test_an_explicit_settlement_amount_still_wins_over_payoff(session, book):
    """`payoff` is a FALLBACK. Order matters: settlement_amount is the key the
    settle path and the snowball enrichment both write."""
    position_id = _book(
        book.id, "BarrierOption", _BARRIER_TERMS, product_family="barrier", quantity=1.0
    )
    record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "knock_out",
            "event_data": {"settlement_amount": 750.0, "payoff": 900.0},
        }
    )

    row = (
        session.query(SettlementCashflow)
        .filter_by(position_id=position_id, leg_key="settlement")
        .one()
    )
    assert row.amount == 750.0


def test_knockout_with_payoff_then_settle_still_books_one_row(session, book):
    """The singleton guard must survive a knock_out that is already priced —
    the later settle finds a filled row and must not add a second."""
    position_id = _book(
        book.id, "BarrierOption", _BARRIER_TERMS, product_family="barrier", quantity=1.0
    )
    mark_knockout_tool.invoke({"position_id": position_id, "payoff": 900.0})
    record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "settle",
            "event_data": {"settlement_amount": 900.0},
        }
    )

    rows = (
        session.query(SettlementCashflow)
        .filter_by(position_id=position_id, leg_key="settlement")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount == 900.0


# ---------------------------------------------------------------------------
# C. lifecycle tool inputs must reject unknown arguments
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# D. reopen must not strand a live settlement row
# ---------------------------------------------------------------------------


def _settlement_rows(session, position_id: int):
    return (
        session.query(SettlementCashflow)
        .filter_by(position_id=position_id, leg_key="settlement")
        .order_by(SettlementCashflow.id)
        .all()
    )


def test_reopen_is_refused_while_a_live_settlement_row_exists(session, book):
    """Measured before this guard: settle(500) -> reopen -> settle(650) left the
    cashflow at 500. The singleton guard absorbed the second settle and the new
    amount reached nothing, so the lifecycle log said 650 while the blotter said
    500. Refusing the reopen is fail-closed and mutates nothing.
    """
    position_id = _book(book.id, "EuropeanVanillaOption", _VANILLA_TERMS)
    record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "settle",
            "event_data": {"settlement_amount": 500.0},
        }
    )

    result = record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "reopen", "event_data": {"reason": "oops"}}
    )

    assert result["ok"] is False
    assert "settlement" in result["error"].lower()
    # The hint must say what to do, not merely that it failed.
    assert any(word in result["error"].lower() for word in ("settle", "void", "edit"))
    # Nothing moved.
    session.expire_all()
    assert [(c.amount, c.status) for c in _settlement_rows(session, position_id)] == [
        (500.0, "pending")
    ]


def test_reopen_is_allowed_once_the_settlement_is_terminal(session, book):
    """A settled or voided row no longer blocks: the position may legitimately
    reopen and earn a SECOND settlement row."""
    position_id = _book(book.id, "EuropeanVanillaOption", _VANILLA_TERMS)
    record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "settle",
            "event_data": {"settlement_amount": 500.0},
        }
    )
    row = _settlement_rows(session, position_id)[0]
    row.status = "settled"
    session.commit()

    result = record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "reopen", "event_data": {"reason": "restruck"}}
    )
    assert result["ok"] is True, result
    assert result["position"]["status"] == "open"

    record_lifecycle_event_tool.invoke(
        {
            "position_id": position_id,
            "event_type": "settle",
            "event_data": {"settlement_amount": 650.0},
        }
    )
    session.expire_all()
    assert [(c.amount, c.status) for c in _settlement_rows(session, position_id)] == [
        (500.0, "settled"),
        (650.0, "pending"),
    ]


def test_reopen_is_allowed_when_the_position_never_settled(session, book):
    """The guard is about a STRANDED settlement row, not about reopening at all.
    A position closed without cash (expire) must still be reopenable — and the
    always-present `premium` row must not block it."""
    position_id = _book(book.id, "EuropeanVanillaOption", _VANILLA_TERMS)
    record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "expire", "event_data": {}}
    )

    result = record_lifecycle_event_tool.invoke(
        {"position_id": position_id, "event_type": "reopen", "event_data": {}}
    )

    assert result["ok"] is True, result
    assert result["position"]["status"] == "open"


# ---------------------------------------------------------------------------
# E. the approval card must not be two integers
# ---------------------------------------------------------------------------


def test_the_approval_card_states_the_event_and_the_trade(session, book):
    """The interrupt fires BEFORE the tool body, so the card can only see raw
    args — `position_id=27` tells a human nothing about what they are approving.
    """
    from app.services.deep_agent import hitl

    position_id = _book(book.id, "AmericanOption", _VANILLA_TERMS)
    card = hitl._summary_for(
        {
            "name": "record_lifecycle_event",
            "args": {
                "position_id": position_id,
                "event_type": "exercise",
                "event_data": {"settlement_amount": 500.0, "early": True},
            },
        }
    )

    assert "exercise" in card.lower()
    assert "AmericanOption" in card or "American" in card
    assert "AAPL" in card
    assert "500" in card
    assert f"position_id={position_id}" not in card, "raw args are the bug"


def test_the_approval_card_degrades_honestly_for_an_unknown_position():
    from app.services.deep_agent import hitl

    card = hitl._summary_for(
        {
            "name": "record_lifecycle_event",
            "args": {"position_id": 999_999, "event_type": "close"},
        }
    )
    assert "close" in card.lower()
    assert "999999" in card or "not found" in card.lower()


def test_all_four_lifecycle_cards_name_the_trade(session, book):
    """close_position / settle_position / mark_knockout each hardcode one event
    type and showed a bare `position_id=27` — the same "gate is theater" defect
    the general tool was fixed for, at smaller scale.
    """
    from app.services.deep_agent import hitl

    position_id = _book(
        book.id, "BarrierOption", _BARRIER_TERMS, product_family="barrier", quantity=1.0
    )
    cards = {
        "close_position": {"position_id": position_id, "reason": "client unwind"},
        "settle_position": {
            "position_id": position_id,
            "settlement_amount": 1250.0,
            "settlement_date": "2026-09-01",
        },
        "mark_knockout": {"position_id": position_id, "payoff": 900.0},
        "record_lifecycle_event": {
            "position_id": position_id,
            "event_type": "expire",
            "event_data": {},
        },
    }
    for name, args in cards.items():
        card = hitl._summary_for({"name": name, "args": args})
        assert "BarrierOption" in card, f"{name}: {card}"
        assert "AAPL" in card, f"{name}: {card}"
        assert f"position_id={position_id}" not in card, f"{name} still shows raw args"

    assert "client unwind" in hitl._summary_for(
        {"name": "close_position", "args": cards["close_position"]}
    )
    settle_card = hitl._summary_for(
        {"name": "settle_position", "args": cards["settle_position"]}
    )
    assert "1250" in settle_card and "2026-09-01" in settle_card
    assert "900" in hitl._summary_for(
        {"name": "mark_knockout", "args": cards["mark_knockout"]}
    )


def test_lifecycle_cards_beat_the_middlewares_boilerplate_description(session, book):
    """The documented trap: HumanInTheLoopMiddleware stamps every action request
    with a generic `description`, and a builder that loses to it is unreachable
    in the live path even though its unit test passes.
    """
    from app.services.deep_agent import hitl

    position_id = _book(book.id, "AmericanOption", _VANILLA_TERMS)
    for name, args in (
        ("close_position", {"position_id": position_id}),
        ("settle_position", {"position_id": position_id, "settlement_amount": 10.0}),
        ("mark_knockout", {"position_id": position_id}),
    ):
        card = hitl._summary_for(
            {
                "name": name,
                "description": (
                    "Tool execution requires approval\n\nTool: "
                    f"{name}\nArgs: {{'position_id': {position_id}}}"
                ),
                "args": args,
            }
        )
        assert "Tool execution requires approval" not in card, name
        assert "AAPL" in card, name


def test_lifecycle_cards_degrade_honestly_for_an_unknown_position():
    """A card must never break the gate it exists to serve."""
    from app.services.deep_agent import hitl

    for name in ("close_position", "settle_position", "mark_knockout"):
        card = hitl._summary_for({"name": name, "args": {"position_id": 999_999}})
        assert "999999" in card or "not found" in card.lower(), name


def test_a_lifecycle_card_can_describe_a_position_by_source_trade_id():
    """The tools accept source_trade_id instead of position_id; the card must
    still say something more useful than the tool name."""
    from app.services.deep_agent import hitl

    card = hitl._summary_for(
        {"name": "close_position", "args": {"source_trade_id": "SB-2026-014"}}
    )
    assert "SB-2026-014" in card


def test_lifecycle_inputs_reject_unknown_arguments():
    """A silently-dropped argument is worse than an error: `settlement_amount`
    is a plausible guess for mark_knockout, and pydantic's default `ignore`
    discarded it with no signal. BookPositionInput already forbids extras."""
    with pytest.raises(Exception) as excinfo:
        MarkKnockoutInput(position_id=1, settlement_amount=900.0)
    assert "settlement_amount" in str(excinfo.value)
