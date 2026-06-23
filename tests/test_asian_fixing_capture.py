"""Component C — immutable observed-price capture from MarketQuote.

Captures a realized fixing's price once its date has passed, sourced from the
MarketQuote close as-of that date. Idempotent (never overwrites a captured
price), and the same function over many positions is the backfill.
"""

from datetime import date, datetime

from app.models import Instrument, MarketQuote, Portfolio, Position
from app.services.domains.positions import capture_due_asian_fixings


def _instrument(session, symbol="000300.SH"):
    inst = Instrument(symbol=symbol)
    session.add(inst)
    session.flush()
    return inst


def _quote(session, instrument_id, on, price):
    session.add(
        MarketQuote(
            instrument_id=instrument_id,
            as_of=datetime.combine(on, datetime.min.time()),
            price=price,
            price_type="close",
        )
    )
    session.flush()


def _asian(session, instrument, records):
    pf = Portfolio(name="Capture PF", base_currency="CNY")
    session.add(pf)
    session.flush()
    pos = Position(
        portfolio_id=pf.id,
        product_type="AsianOption",
        underlying=instrument.symbol,
        underlying_id=instrument.id,
        quantity=1.0,
        status="open",
        product_kwargs={"observation_records": records},
    )
    session.add(pos)
    session.flush()
    return pos


def test_capture_fills_past_only(session):
    inst = _instrument(session)
    _quote(session, inst.id, date(2024, 6, 3), 123.5)
    pos = _asian(
        session,
        inst,
        [
            {"observation_date": "2024-06-03", "weight": None},  # past
            {"observation_date": "2099-06-03", "weight": None},  # future
        ],
    )
    n = capture_due_asian_fixings(session, pos.id, as_of=date(2025, 1, 1))
    assert n == 1
    recs = pos.product_kwargs["observation_records"]
    assert recs[0]["observed_price"] == 123.5
    assert recs[1].get("observed_price") is None


def test_capture_is_immutable_and_idempotent(session):
    inst = _instrument(session)
    _quote(session, inst.id, date(2024, 6, 3), 100.0)
    pos = _asian(session, inst, [{"observation_date": "2024-06-03", "weight": None}])

    assert capture_due_asian_fixings(session, pos.id, as_of=date(2025, 1, 1)) == 1
    # A later, revised quote must NOT change the already-captured fix.
    _quote(session, inst.id, date(2024, 6, 3), 200.0)
    assert capture_due_asian_fixings(session, pos.id, as_of=date(2025, 1, 1)) == 0
    assert pos.product_kwargs["observation_records"][0]["observed_price"] == 100.0


def test_stale_older_quote_is_not_captured(session):
    # No print on the fixing date, only an older quote. Capturing the stale price
    # would be permanent (immutable), so we must wait instead.
    inst = _instrument(session)
    _quote(session, inst.id, date(2024, 5, 1), 77.0)  # older than the fixing date
    pos = _asian(session, inst, [{"observation_date": "2024-06-03", "weight": None}])
    assert capture_due_asian_fixings(session, pos.id, as_of=date(2025, 1, 1)) == 0
    assert pos.product_kwargs["observation_records"][0].get("observed_price") is None


def test_missing_quote_leaves_null(session):
    inst = _instrument(session)
    pos = _asian(session, inst, [{"observation_date": "2024-06-03", "weight": None}])
    assert capture_due_asian_fixings(session, pos.id, as_of=date(2025, 1, 1)) == 0
    assert pos.product_kwargs["observation_records"][0].get("observed_price") is None


def test_capture_rejects_portfolio_mismatch(session):
    import pytest

    inst = _instrument(session)
    pos = _asian(session, inst, [{"observation_date": "2024-06-03", "weight": None}])
    with pytest.raises(LookupError):
        capture_due_asian_fixings(
            session, pos.id, portfolio_id=pos.portfolio_id + 999
        )


def test_booking_eager_captures_already_past_fixings(session):
    # Booking a seasoned position must capture its already-past fixings so it does
    # not carry uncaptured (priceless) past observations into pricing.
    from app.services.domains.position_terms import upsert_position_term_rows

    inst = _instrument(session)
    _quote(session, inst.id, date(2020, 6, 3), 88.0)
    pos = _asian(session, inst, [{"observation_date": "2020-06-03", "weight": None}])
    upsert_position_term_rows(session, pos)
    assert pos.product_kwargs["observation_records"][0]["observed_price"] == 88.0
