# tests/test_spot_chain.py
"""Transitional spot chain — quote store enters the pricer + risk paths.

These drive the REAL entry points (no stubbed snapshot helpers): the risk
path through ``market_snapshot_for_position`` and the pricer path through
``price_portfolio_positions``. The quote store is consulted WITHOUT changing
the outcome for any input that already has a spot source — every numeric is
NON-default so a silent fallback would flip the assertion.
"""
from __future__ import annotations

from datetime import datetime

from app.models import (
    Instrument,
    MarketQuote,
    Portfolio,
    Position,
)
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import market_snapshot_for_position
from app.services.quotes import record_quote


# --------------------------------------------------------------------------
# Risk path — market_snapshot_for_position(position, fallback, session=...)
# --------------------------------------------------------------------------

def _instrument_and_position(session, *, symbol="000905.SH"):
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    inst = Instrument(symbol=symbol, kind="index", status="active")
    session.add(inst)
    session.flush()
    pos = Position(
        portfolio_id=pf.id,
        underlying=symbol,
        underlying_id=inst.id,
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "expiry": "2026-12-31",
                        "option_type": "call", "initial_price": 100.0},
        engine_name="BlackScholesEngine",
        quantity=1.0,
        entry_price=0.0,
        status="open",
        currency="CNY",
    )
    session.add(pos)
    session.flush()
    return inst, pos


def test_quote_supplies_spot_when_no_market_input(session):
    inst, pos = _instrument_and_position(session)
    record_quote(session, instrument_id=inst.id, price=6412.55,
                 as_of=datetime(2026, 6, 4), source="akshare")
    session.flush()
    fallback = PricingEnvironmentSnapshot(
        spot=999.0, volatility=0.2, rate=0.03,
        valuation_date=datetime(2026, 6, 4, 23),
    )
    snap = market_snapshot_for_position(pos, fallback, session=session)
    assert snap.spot == 6412.55  # quote wins over the bare fallback (999.0)


def test_quote_is_the_only_spot_source(session):
    # Spot is observation-only (T8): the quote store is now the spot source and
    # there is no per-trade market-input override anymore.
    inst, pos = _instrument_and_position(session)
    record_quote(session, instrument_id=inst.id, price=6412.55,
                 as_of=datetime(2026, 6, 4), source="akshare")
    session.flush()
    fallback = PricingEnvironmentSnapshot(
        spot=999.0, volatility=0.2, rate=0.03,
        valuation_date=datetime(2026, 6, 4, 23),
    )
    snap = market_snapshot_for_position(pos, fallback, session=session)
    assert snap.spot == 6412.55  # quote wins over the bare fallback (999.0)


def test_no_session_behaves_as_before(session):
    inst, pos = _instrument_and_position(session)
    record_quote(session, instrument_id=inst.id, price=6412.55,
                 as_of=datetime(2026, 6, 4), source="akshare")
    session.flush()
    fallback = PricingEnvironmentSnapshot(
        spot=999.0, volatility=0.2, rate=0.03,
        valuation_date=datetime(2026, 6, 4, 23),
    )
    snap = market_snapshot_for_position(pos, fallback)  # no session passed
    assert snap.spot == 999.0  # quote NOT consulted -> bare fallback


def test_quote_after_valuation_date_is_invisible(session):
    inst, pos = _instrument_and_position(session)
    # Quote stamped AFTER the valuation date -> latest_quote cutoff excludes it.
    record_quote(session, instrument_id=inst.id, price=6412.55,
                 as_of=datetime(2026, 6, 6), source="akshare")
    session.flush()
    fallback = PricingEnvironmentSnapshot(
        spot=999.0, volatility=0.2, rate=0.03,
        valuation_date=datetime(2026, 6, 4),
    )
    snap = market_snapshot_for_position(pos, fallback, session=session)
    assert snap.spot == 999.0  # future quote invisible -> fallback spot


def test_quote_diagnostics_surface_when_quote_wins(session):
    inst, pos = _instrument_and_position(session)
    record_quote(session, instrument_id=inst.id, price=6412.55,
                 as_of=datetime(2026, 6, 2), source="akshare")
    session.flush()
    fallback = PricingEnvironmentSnapshot(
        spot=999.0, volatility=0.2, rate=0.03,
        valuation_date=datetime(2026, 6, 4),
    )
    diagnostics: dict = {}
    snap = market_snapshot_for_position(
        pos, fallback, session=session, diagnostics=diagnostics
    )
    assert snap.spot == 6412.55
    assert diagnostics["market_input_source"] == "market_quote"
    assert diagnostics["quote_age_days"] == 2  # 2026-06-04 minus 2026-06-02


# --------------------------------------------------------------------------
# Pricer path — fetch-and-record (fetch once, then read the recorded quote)
# --------------------------------------------------------------------------

def _priceable_position(session, *, symbol="000905.SH"):
    pf = Portfolio(name="pricer-pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    inst = Instrument(symbol=symbol, kind="index", status="active")
    session.add(inst)
    session.flush()
    pos = Position(
        portfolio_id=pf.id,
        underlying=symbol,
        underlying_id=inst.id,
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name="BlackScholesEngine",
        quantity=1.0,
        entry_price=0.0,
        status="open",
        mapping_status="supported",
        currency="CNY",
    )
    session.add(pos)
    session.flush()
    return inst, pos


def test_pricer_fallback_fetch_is_recorded_then_reused(session):
    from app.services import position_pricer

    inst, pos = _priceable_position(session)
    valuation_date = datetime(2026, 6, 4)

    calls = {"n": 0}

    def stub_fetch(symbol, vdate):
        calls["n"] += 1
        return 117.25, {"source_metadata": {"stub": True}}

    # rate/q/vol supplied via overrides so ONLY the spot slot exercises the
    # quote-store -> fetch -> record chain.
    rqv = position_pricer.MarketOverrides(rate=0.02, dividend_yield=0.03, volatility=0.22)
    run = position_pricer.price_portfolio_positions(
        session,
        portfolio_id=pos.portfolio_id,
        valuation_date=valuation_date,
        overrides=rqv,
        spot_fetcher=stub_fetch,
    )
    session.flush()
    assert run.summary["priced"] == 1
    assert calls["n"] == 1  # fetched exactly once

    quote = (
        session.query(MarketQuote)
        .filter(MarketQuote.instrument_id == inst.id)
        .order_by(MarketQuote.id.desc())
        .first()
    )
    assert quote is not None
    assert quote.price == 117.25
    assert quote.source == "pricer_fallback"

    # Second run with a NOW-FAILING fetcher still succeeds via the recorded quote.
    def boom_fetch(symbol, vdate):
        raise AssertionError("fetcher must not be called once a quote is recorded")

    run2 = position_pricer.price_portfolio_positions(
        session,
        portfolio_id=pos.portfolio_id,
        valuation_date=valuation_date,
        overrides=rqv,
        spot_fetcher=boom_fetch,
    )
    session.flush()
    assert run2.summary["priced"] == 1  # served from the recorded quote, not a fetch


def test_pricer_missing_everything_is_missing_quote_diagnostic(session):
    from app.services import position_pricer
    from app.models import PositionValuationResult

    inst, pos = _priceable_position(session, symbol="999999.SH")
    valuation_date = datetime(2026, 6, 4)

    run = position_pricer.price_portfolio_positions(
        session,
        portfolio_id=pos.portfolio_id,
        valuation_date=valuation_date,
        spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
    )
    session.flush()
    result = (
        session.query(PositionValuationResult)
        .filter_by(valuation_run_id=run.id)
        .one()
    )
    assert not result.ok
    assert "no market quote for" in result.error
    # error_type lives on the in-memory result dict (the row stores only `error`);
    # _price_position is unit-tested for the "missing_quote" type below.


def test_price_position_missing_quote_error_type(session):
    """The in-memory result carries error_type='missing_quote' for the no-source case."""
    from app.services import position_pricer

    inst, pos = _priceable_position(session, symbol="888888.SH")
    valuation_date = datetime(2026, 6, 4)
    result = position_pricer._price_position(
        position=pos,
        pricing_rows=[],
        valuation_date=valuation_date,
        overrides=position_pricer.MarketOverrides(),
        engine_name=None,
        engine_kwargs=None,
        spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
        symbol_spot_cache={},
    )
    assert not result["ok"]
    assert result["error_type"] == "missing_quote"
    assert "no market quote for" in result["error"]


# --------------------------------------------------------------------------
# Risk run — no-profile path still consults the quote store (T6 review)
# --------------------------------------------------------------------------

def test_no_profile_risk_run_reads_quote_store(session):
    """The default (no pricing_parameter_profile_id) persisted risk path must
    thread the session into market resolution so a recorded quote feeds spot."""
    from app.services.risk_engine import run_portfolio_risk

    inst, pos = _instrument_and_position(session, symbol="000300.SH")
    record_quote(
        session,
        instrument_id=inst.id,
        price=7777.25,
        as_of=datetime(2026, 6, 4),
        source="akshare",
    )
    session.flush()

    run = run_portfolio_risk(
        session,
        portfolio_id=pos.portfolio_id,
        pricing_parameter_profile_id=None,  # default no-profile risk path
    )
    session.flush()

    rows = run.metrics["positions"]
    row = next(r for r in rows if r["position_id"] == pos.id)
    assert row["spot"] == 7777.25  # quote store reached the no-profile risk run
    assert row["market_input_source"] == "market_quote"


# --------------------------------------------------------------------------
# Scalar spot override scope guard — one spot cannot span underlyings
# --------------------------------------------------------------------------

def _second_position(session, portfolio_id, *, symbol):
    inst = Instrument(symbol=symbol, kind="index", status="active")
    session.add(inst)
    session.flush()
    pos = Position(
        portfolio_id=portfolio_id,
        underlying=symbol,
        underlying_id=inst.id,
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 95.0, "option_type": "PUT", "maturity": 0.5},
        engine_name="BlackScholesEngine",
        quantity=2.0,
        entry_price=0.0,
        status="open",
        mapping_status="supported",
        currency="CNY",
    )
    session.add(pos)
    session.flush()
    return inst, pos


def test_spot_override_refused_across_underlyings(session):
    """A scalar spot override over positions on different underlyings is a
    guaranteed mispricing (the 6048.224-on-live-hogs incident) — refuse it."""
    import pytest

    from app.services import position_pricer

    inst_a, pos_a = _priceable_position(session, symbol="000852.SH")
    inst_b, pos_b = _second_position(session, pos_a.portfolio_id, symbol="LH2609.DCE")

    with pytest.raises(ValueError, match="single underlying"):
        position_pricer.price_portfolio_positions(
            session,
            portfolio_id=pos_a.portfolio_id,
            valuation_date=datetime(2026, 6, 6),
            overrides=position_pricer.MarketOverrides(
                spot=6048.224, rate=0.014, dividend_yield=0.016, volatility=0.234
            ),
            spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
        )


def test_spot_override_allowed_for_single_underlying_selection(session):
    """Narrowing the selection to one underlying keeps the override usable —
    the guard scopes the SELECTION, not the whole portfolio."""
    from app.services import position_pricer

    inst_a, pos_a = _priceable_position(session, symbol="000852.SH")
    _second_position(session, pos_a.portfolio_id, symbol="LH2609.DCE")

    run = position_pricer.price_portfolio_positions(
        session,
        portfolio_id=pos_a.portfolio_id,
        position_ids=[pos_a.id],
        valuation_date=datetime(2026, 6, 6),
        overrides=position_pricer.MarketOverrides(
            spot=101.5, rate=0.014, dividend_yield=0.016, volatility=0.234
        ),
        spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
    )
    session.flush()
    assert run.summary["priced"] == 1


def test_rqv_overrides_still_span_underlyings(session):
    """Only spot is per-underlying by construction; r/q/vol overrides remain
    portfolio-wide (existing behaviour, pinned)."""
    from app.services import position_pricer

    inst_a, pos_a = _priceable_position(session, symbol="000852.SH")
    inst_b, pos_b = _second_position(session, pos_a.portfolio_id, symbol="LH2609.DCE")
    record_quote(session, instrument_id=inst_a.id, price=101.5,
                 as_of=datetime(2026, 6, 4), source="akshare")
    record_quote(session, instrument_id=inst_b.id, price=96.75,
                 as_of=datetime(2026, 6, 4), source="akshare")
    session.flush()

    run = position_pricer.price_portfolio_positions(
        session,
        portfolio_id=pos_a.portfolio_id,
        valuation_date=datetime(2026, 6, 6),
        overrides=position_pricer.MarketOverrides(
            rate=0.014, dividend_yield=0.016, volatility=0.234
        ),
        spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
    )
    session.flush()
    assert run.summary["priced"] == 2
