"""Asian fixing lifecycle event + schedule generator (sub-project B)."""
from datetime import date

import pytest

from app.models import Portfolio, Position
from app.services.domains import positions as positions_svc


def _asian_position(session, *, product_kwargs=None) -> Position:
    portfolio = Portfolio(name="Asian Lifecycle PF", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    pos = Position(
        portfolio_id=portfolio.id,
        product_type="AsianOption",
        underlying="000300.SH",
        quantity=1.0,
        status="open",
        product_kwargs=product_kwargs or {},
    )
    session.add(pos)
    session.flush()
    return pos


def test_asian_allows_fixing_event_type():
    valid = positions_svc.valid_lifecycle_event_types("AsianOption")
    assert "fixing" in valid


def test_fixing_event_is_informational(session):
    pos = _asian_position(session)
    update = positions_svc.create_lifecycle_event(
        position_id=pos.id,
        event_type="fixing",
        event_data={"observation_date": "2024-04-01", "observed_price": 101.5, "sequence": 1},
        session=session,
    )
    # informational: status is unchanged
    assert update.event.new_status == update.event.old_status == "open"
    assert update.position.status == "open"


def test_non_asian_does_not_get_fixing():
    valid = positions_svc.valid_lifecycle_event_types("EuropeanVanillaOption")
    assert "fixing" not in valid


def test_generate_asian_fixing_schedule_creates_events(session):
    pos = _asian_position(
        session,
        product_kwargs={
            "averaging_frequency": "QUARTERLY",
            "maturity_years": 1.0,
            "trade_start_date": "2024-01-02",
        },
    )
    count = positions_svc.generate_asian_fixing_schedule(
        position_id=pos.id, session=session
    )
    assert count == 4  # quarterly over 1y

    from app.models import PositionLifecycleEvent

    events = (
        session.query(PositionLifecycleEvent)
        .filter_by(position_id=pos.id, event_type="fixing")
        .all()
    )
    assert len(events) == 4
    # sequence + observation_date carried in event_data
    seqs = sorted(e.event_data.get("sequence") for e in events)
    assert seqs == [1, 2, 3, 4]


def test_generate_asian_fixing_schedule_endpoint(client, session):
    pos = _asian_position(
        session,
        product_kwargs={
            "averaging_frequency": "QUARTERLY",
            "maturity_years": 1.0,
            "trade_start_date": "2024-01-02",
        },
    )
    portfolio_id = pos.portfolio_id
    pos_id = pos.id
    session.commit()

    resp = client.post(
        f"/api/portfolios/{portfolio_id}/positions/{pos_id}/asian-fixing-schedule"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events_created"] == 4
