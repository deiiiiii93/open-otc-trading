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


def test_generate_curve_rows_resolves_exercise_date_maturity(session: Session) -> None:
    """Real booked vanillas carry `exercise_date`, not `maturity_date` — the
    maturity extraction must find it (regression: live book has no maturity_date)."""
    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    pos = Position(
        portfolio_id=portfolio.id, product_id=None, underlying="000300.SH",
        product_type="EuropeanVanillaOption",
        # No maturity_date key — only exercise_date / settlement_date, as on the live book.
        product_kwargs={"exercise_date": "2026-07-02", "settlement_date": "2026-07-02",
                        "option_type": "CALL", "strike": 4931.386},
        quantity=1.0, source_trade_id="EX-1", status="open", position_kind="otc",
        engine_name="quantark.vanilla", engine_kwargs={}, source_payload={},
        mapping_status="supported",
    )
    session.add(pos)
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01
    inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
    session.flush()

    rows = generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    assert len(rows) == 1
    assert rows[0].source_trade_id == "EX-1"
    assert rows[0].volatility == pytest.approx(0.22)
    # 182 days / 365 = 0.4986y from exercise_date.
    assert rows[0].source_payload["tenor_years"] == pytest.approx(0.4986, abs=1e-3)


def test_generate_curve_rows_resolves_numeric_maturity_year_fraction(session: Session) -> None:
    """Positions can carry maturity as a numeric year-fraction ("maturity": 0.5,
    the QuantArk T) instead of a date — use it directly as the tenor (regression:
    live book's US-stock trades store maturity this way, not as a date)."""
    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    pos = Position(
        portfolio_id=portfolio.id, product_id=None, underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={"maturity": 0.5, "option_type": "CALL", "strike": 100.0,
                        "contract_multiplier": 1.0},
        quantity=1.0, source_trade_id="YF-1", status="open", position_kind="otc",
        engine_name="BlackScholesEngine", engine_kwargs={}, source_payload={},
        mapping_status="supported",
    )
    session.add(pos)
    inst = ensure_underlying(session, "AAPL", source="manual", status="active")
    # 6M knot -> value read directly at tenor 0.5.
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "6M", "value": 0.03}]
    inst.dividend_yield = 0.01
    inst.volatility_curve = [{"tenor": "6M", "value": 0.25}]
    session.flush()

    # valuation_date is irrelevant for a numeric year-fraction maturity.
    rows = generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    assert len(rows) == 1
    assert rows[0].source_trade_id == "YF-1"
    assert rows[0].source_payload["tenor_years"] == pytest.approx(0.5)
    assert rows[0].rate == pytest.approx(0.03)        # 6M rate knot
    assert rows[0].volatility == pytest.approx(0.25)  # single-point vol curve


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


from app.models import PricingParameterProfile, PricingParameterRow
from app.services.domains._errors import DomainWriteError
from app.services.domains.pricing_profiles import generate_profile_from_curves


def test_generate_profile_from_curves_writes_flat_profile(session: Session) -> None:
    _seed_open_option(session, underlying="000300.SH", maturity="2026-07-02")
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01
    inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
    session.flush()

    profile = generate_profile_from_curves(
        name="Curve Set", valuation_date=datetime(2026, 1, 1), session=session
    )
    assert profile.source_type == "curve"
    assert profile.name == "Curve Set"
    assert profile.status == "completed"
    assert len(profile.rows) == 1
    row = profile.rows[0]
    assert row.symbol == "000300.SH"
    assert row.source_trade_id == "TRD-1"
    assert row.volatility == pytest.approx(0.22)
    assert row.source_payload["generated_from"] == "instrument_curves"
    # Persisted and reloadable.
    stored = (
        session.query(PricingParameterProfile)
        .filter(PricingParameterProfile.id == profile.id)
        .one()
    )
    assert stored.source_type == "curve"


def test_generate_profile_from_curves_unfilled_raises_domain_error(session: Session) -> None:
    _seed_open_option(session, underlying="000905.SH", maturity="2026-07-02",
                      source_trade_id="U-9")
    ensure_underlying(session, "000905.SH", source="manual", status="active")
    session.flush()
    with pytest.raises(DomainWriteError) as exc_info:
        generate_profile_from_curves(valuation_date=datetime(2026, 1, 1), session=session)
    assert exc_info.value.error == "unfilled_trades"
    assert exc_info.value.detail["unfilled_trades"][0]["source_trade_id"] == "U-9"


def test_generate_profile_from_curves_no_positions_raises_domain_error(session: Session) -> None:
    with pytest.raises(DomainWriteError) as exc_info:
        generate_profile_from_curves(valuation_date=datetime(2026, 1, 1), session=session)
    assert exc_info.value.error == "no_open_positions"


def test_generate_binds_rows_to_positions_and_resolves_without_trade_id(session: Session) -> None:
    """Two positions on the SAME underlying with NO source_trade_id: generated
    rows must bind by position_id so each still resolves uniquely (not ambiguous
    — the live-book failure mode)."""
    from app.services.pricing_profiles import resolve_pricing_parameter_row_for_position

    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()

    def _aapl(maturity_yf: float) -> Position:
        p = Position(
            portfolio_id=portfolio.id, product_id=None, underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"maturity": maturity_yf, "option_type": "CALL",
                            "strike": 100.0, "contract_multiplier": 1.0},
            quantity=1.0, source_trade_id=None,  # <-- no trade id, like the live book
            status="open", position_kind="otc", engine_name="BlackScholesEngine",
            engine_kwargs={}, source_payload={}, mapping_status="supported",
        )
        session.add(p)
        session.flush()
        return p

    p1 = _aapl(0.25)   # 3M tenor
    p2 = _aapl(1.0)    # 1Y tenor
    inst = ensure_underlying(session, "AAPL", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01
    inst.volatility_curve = [{"tenor": "3M", "value": 0.20}, {"tenor": "1Y", "value": 0.28}]
    session.flush()

    profile = generate_profile_from_curves(valuation_date=datetime(2026, 1, 1), session=session)
    rows = list(profile.rows)
    assert len(rows) == 2
    # Every generated row is bound to its position, even with no trade id.
    assert {r.position_id for r in rows} == {p1.id, p2.id}
    assert all(r.source_trade_id == "" for r in rows)
    # Distinct interpolated values prove per-trade materialization.
    by_pos = {r.position_id: r for r in rows}
    assert by_pos[p1.id].rate == pytest.approx(0.02)   # 3M knot
    assert by_pos[p2.id].rate == pytest.approx(0.05)   # 1Y knot

    # The resolver binds each position to ITS row — not ambiguous.
    res1 = resolve_pricing_parameter_row_for_position(rows, p1)
    res2 = resolve_pricing_parameter_row_for_position(rows, p2)
    assert res1.ok and res2.ok, (res1.match_type, res2.match_type)
    assert res1.row.position_id == p1.id
    assert res2.row.position_id == p2.id
    assert res1.row.id != res2.row.id
