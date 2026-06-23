"""Asian averaging-date weight persists through the position-term store (sub-project D)."""
from datetime import date

from app.models import Portfolio, Position
from app.services.domains import position_terms


def _make_position(session) -> int:
    portfolio = Portfolio(name="Asian PF", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        product_type="AsianOption",
        underlying="000300.SH",
        quantity=1.0,
    )
    session.add(position)
    session.flush()
    return position.id


def test_asian_schedule_round_trips_weight(session):
    pos_id = _make_position(session)
    records = [
        {"observation_date": date(2024, 4, 1), "weight": 1.0},
        {"observation_date": date(2024, 7, 1), "weight": 2.0},
        {"observation_date": date(2024, 10, 1), "weight": 3.0},
    ]
    position_terms._replace_asian_schedule(session, pos_id, records)
    session.flush()

    schedule = position_terms.get_asian_schedule(session, pos_id)
    assert [row["weight"] for row in schedule] == [1.0, 2.0, 3.0]
    assert [row["sequence"] for row in schedule] == [1, 2, 3]


def test_asian_schedule_uniform_weight_is_null(session):
    pos_id = _make_position(session)
    records = [{"observation_date": date(2024, 4, 1)}]  # no weight key
    position_terms._replace_asian_schedule(session, pos_id, records)
    session.flush()

    schedule = position_terms.get_asian_schedule(session, pos_id)
    assert schedule[0]["weight"] is None
