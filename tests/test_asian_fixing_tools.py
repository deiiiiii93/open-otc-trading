"""Agent tools + service self-scoping for the Asian fixing lifecycle.

Covers Task 13 follow-up: expose generate_asian_fixing_schedule and
capture_due_asian_fixings to agents, and make capture self-scope its own
transaction when no session is injected (the stateless-tool path).
"""

from __future__ import annotations

from datetime import date, datetime

from app.models import Instrument, MarketQuote, Portfolio, Position
from app.services.domains import positions as positions_svc


def _asian_position(session, *, captured=False):
    """An Asian position with one past observation + a close quote on that date."""
    pf = Portfolio(name="Fixing Tools PF", base_currency="CNY")
    session.add(pf)
    inst = Instrument(symbol="ASN1", display_name="Asian Underlying 1")
    session.add(inst)
    session.flush()
    past = date(2026, 1, 15)
    rec = {
        "observation_date": past.isoformat(),
        "weight": 1.0,
        "observed_price": (100.0 if captured else None),
    }
    pos = Position(
        portfolio_id=pf.id,
        product_type="AsianOption",
        underlying=inst.symbol,
        underlying_id=inst.id,
        quantity=1.0,
        status="open",
        product_kwargs={
            "averaging_frequency": "MONTHLY",
            "maturity_years": 1.0,
            "trade_start_date": "2025-12-15",
            "observation_records": [rec],
        },
    )
    session.add(pos)
    session.flush()
    session.add(
        MarketQuote(
            instrument_id=inst.id,
            as_of=datetime(2026, 1, 15, 15, 0),
            price=123.0,
            price_type="close",
        )
    )
    session.commit()
    return pos.id, inst.id


def test_capture_self_scopes_and_commits_when_session_is_none(session):
    pos_id, _ = _asian_position(session)
    # Tool path: pass session=None so the service owns the transaction.
    captured = positions_svc.capture_due_asian_fixings(None, pos_id)
    assert captured == 1
    # Re-read after expiring — the snapshot must be persisted by the service.
    session.expire_all()
    pos = session.get(Position, pos_id)
    rec = pos.product_kwargs["observation_records"][0]
    assert rec["observed_price"] == 123.0
