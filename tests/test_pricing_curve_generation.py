from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Instrument
from app.services.underlyings import ensure_underlying


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_instrument_curve_columns_roundtrip(session: Session) -> None:
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.03}]
    inst.dividend_yield_curve = None
    inst.volatility_curve = [{"tenor": "6M", "value": 0.2}]
    session.flush()
    # Force a genuine DB round-trip on a FRESH instance — a plain (unmapped)
    # Python attribute would survive an identity-map re-fetch and pass falsely.
    session.expunge_all()
    reloaded = session.query(Instrument).filter(Instrument.symbol == "000300.SH").one()
    assert reloaded.rate_curve == [
        {"tenor": "3M", "value": 0.02},
        {"tenor": "1Y", "value": 0.03},
    ]
    assert reloaded.dividend_yield_curve is None
    assert reloaded.volatility_curve == [{"tenor": "6M", "value": 0.2}]


from datetime import datetime

from app.models import Portfolio, Position
from app.services.domains.pricing_profiles import generate_curve_param_rows


def _seed_open_option(
    session: Session,
    *,
    underlying: str,
    maturity: str,
    source_trade_id: str = "TRD-1",
) -> Position:
    # Product-less on purpose: with product_id=None, open_otc_positions loads
    # no product, so compatibility_terms_for_position takes its fallback branch
    # and reads maturity_date straight off position.product_kwargs — independent
    # of the Product model's normalized-column schema. (Real booked positions
    # carry a product; that branch reads the product's terms, the proven
    # termsheet seam.)
    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    pos = Position(
        portfolio_id=portfolio.id,
        product_id=None,
        underlying=underlying,
        product_type="VanillaCall",
        product_kwargs={"underlying": underlying, "maturity_date": maturity},
        quantity=1.0,
        source_trade_id=source_trade_id,
        status="open",
        position_kind="otc",
        engine_name="quantark.vanilla",
        engine_kwargs={},
        source_payload={},
        mapping_status="supported",
    )
    session.add(pos)
    session.flush()
    return pos


def test_generate_curve_rows_interpolates_at_trade_tenor(session: Session) -> None:
    # valuation 2026-01-01, maturity 2026-07-02 -> ~0.5y ACT/365.
    _seed_open_option(session, underlying="000300.SH", maturity="2026-07-02")
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01           # flat-scalar fallback (no curve)
    inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
    session.flush()

    rows = generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "000300.SH"
    assert row.source_trade_id == "TRD-1"
    # 182 days / 365 = 0.4986y; between 3M(0.25) and 1Y(1.0):
    #   0.02 + (0.4986-0.25)/(0.75) * 0.03 = 0.02995...
    assert row.rate == pytest.approx(0.02995, abs=1e-4)
    assert row.dividend_yield == pytest.approx(0.01)       # flat scalar
    assert row.volatility == pytest.approx(0.22)           # single-point curve
    assert row.source_payload["interp"]["rate"]["source"] == "curve"
    assert row.source_payload["interp"]["dividend_yield"]["source"] == "flat_scalar"
    assert row.source_payload["tenor_years"] == pytest.approx(0.4986, abs=1e-3)


def test_generate_curve_rows_skips_delta_one(session: Session) -> None:
    pos = _seed_open_option(session, underlying="IF2609.CFE", maturity="2026-09-01")
    pos.engine_name = "DeltaOneEngine"
    inst = ensure_underlying(session, "IF2609.CFE", source="manual", status="active")
    inst.rate = 0.02
    inst.dividend_yield = 0.0
    inst.volatility = 0.2
    session.flush()
    with pytest.raises(ValueError, match="no open positions in scope"):
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))


def test_generate_curve_rows_unfilled_when_no_curve_no_scalar(session: Session) -> None:
    _seed_open_option(session, underlying="000905.SH", maturity="2026-07-02",
                      source_trade_id="U-1")
    ensure_underlying(session, "000905.SH", source="manual", status="active")  # all None
    session.flush()
    with pytest.raises(ValueError) as exc_info:
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    payload = exc_info.value.args[0]
    assert isinstance(payload, dict)
    assert payload["unfilled_trades"][0]["source_trade_id"] == "U-1"
    assert set(payload["unfilled_trades"][0]["missing_params"]) == {
        "rate", "dividend_yield", "volatility"
    }


def test_generate_curve_rows_no_open_positions_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="no open positions in scope"):
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
