# tests/test_hedging_legs.py
from datetime import date, datetime

from app.models import HedgeMapEntry, Instrument, Underlying
from app.schemas import PricingEnvironmentSnapshot
from app.services import hedging_legs
from app.services.quotes import record_quote


def _underlying(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u); session.flush()
    return u


def _mark(session, u, code, *, itype="future", option_type=None, strike=None,
          family="index_future", mult=200.0, last_price=5600.0):
    # series_root/exchange thread one of the underlying's real hedge families
    # (000905.SH → IC futures on CFFEX, 510500 ETF options on SSE); the legs
    # scoping resolves by (series_root, kind) from resolve_families.
    if family.endswith("_option"):
        series_root, exchange = "510500", "SSE"
    else:
        series_root, exchange = "IC", "CFFEX"
    kind = "listed_option" if itype == "option" else "futures"
    inst = Instrument(
        symbol=f"{code}.{exchange}", kind=kind, series_root=series_root,
        exchange=exchange, contract_code=code, option_type=option_type,
        strike=strike, expiry=date(2026, 6, 21), multiplier=mult,
        parent_id=u.id, status="active", source="hedge_load",
    )
    session.add(inst)
    session.flush()
    if last_price is not None:
        record_quote(session, instrument_id=inst.id, price=last_price,
                     as_of=datetime.utcnow(), source="hedge_load")
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=inst.id, exchange=exchange,
        contract_code=code, family=family, series_root=series_root,
        instrument_type=itype, option_type=option_type, strike=strike,
        reconcile_status="active",
    ))
    session.flush()
    return inst


def test_propose_picks_future_for_delta_and_option_for_gamma(session):
    u = _underlying(session)
    _mark(session, u, "IC2406", itype="future")
    _mark(session, u, "IO2406C", itype="option", option_type="call", strike=5600.0,
          family="index_option", mult=100.0)
    legs = hedging_legs.propose(session, underlying_id=u.id,
                                strategy="delta_gamma_neutral")
    roles = {leg["role"] for leg in legs}
    assert "delta" in roles and "gamma_vega" in roles


def test_future_leg_greeks_are_closed_form(session):
    u = _underlying(session)
    inst = _mark(session, u, "IC2406", itype="future", mult=200.0)
    priced = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "delta"}],
                                spot=5600.0)
    leg = priced[0]
    assert leg["delta"] == 200.0 * 5600.0   # delta_cash per contract = mult * spot
    assert leg["gamma"] == 0.0 and leg["vega"] == 0.0


def test_future_leg_resolves_multiplier_from_series_when_catalog_missing_it(session):
    # Real loaded rows have multiplier=NULL; the per-contract delta_cash must
    # still include the exchange-standard contract multiplier (IC = 200), not 1.
    u = _underlying(session)
    inst = _mark(session, u, "IC2406", itype="future", mult=None)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "delta"}],
                             spot=5600.0)[0]
    assert leg["multiplier"] == 200.0
    assert leg["delta"] == 200.0 * 5600.0


def test_only_active_map_instruments_are_proposed(session):
    u = _underlying(session)
    _mark(session, u, "IC2406", itype="future")
    # an instrument with NO map entry must not be proposed
    session.add(Instrument(
        symbol="IC9999.CFFEX", kind="futures", series_root="IC", exchange="CFFEX",
        contract_code="IC9999", multiplier=200.0, parent_id=u.id,
        expiry=date(2026, 9, 20), status="active", source="hedge_load",
    ))
    session.flush()
    legs = hedging_legs.propose(session, underlying_id=u.id, strategy="delta_neutral")
    assert all(leg["contract_code"] != "IC9999" for leg in legs)


def _option(session, u):
    return _mark(session, u, "IO2406C", itype="option", option_type="call",
                 strike=5600.0, family="index_option", mult=100.0)


def test_option_leg_honors_passed_volatility(session):
    u = _underlying(session)
    inst = _option(session, u)
    args = [{"instrument_id": inst.id, "role": "gamma_vega"}]
    low = hedging_legs.price(session, args, spot=5600.0,
        option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.20))[0]
    high = hedging_legs.price(session, args, spot=5600.0,
        option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.35))[0]
    assert low["priced_ok"] is True and high["priced_ok"] is True
    # Gamma is strongly vol-dependent (~1/sigma); the passed vol really feeds BS.
    assert low["gamma"] != high["gamma"]


def test_option_leg_normalizes_listed_call_code(session):
    u = _underlying(session)
    inst = _mark(session, u, "IO2406C", itype="option", option_type="C",
                 strike=5600.0, family="index_option", mult=100.0)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "gamma_vega"}],
        spot=5600.0, option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.20))[0]
    assert leg["priced_ok"] is True
    assert leg["price_error"] is None


def test_option_leg_normalizes_listed_put_code(session):
    u = _underlying(session)
    inst = _mark(session, u, "IO2406P", itype="option", option_type="P",
                 strike=5600.0, family="index_option", mult=100.0)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "gamma_vega"}],
        spot=5600.0, option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.20))[0]
    assert leg["priced_ok"] is True
    assert leg["price_error"] is None


def test_option_leg_refused_when_market_missing(session):
    u = _underlying(session)
    inst = _option(session, u)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "gamma_vega"}],
        spot=5600.0, option_market=None, option_market_error="no profile params")[0]
    assert leg["priced_ok"] is False
    assert leg["price_error"] == "no profile params"
    assert leg["delta"] == 0.0 and leg["gamma"] == 0.0 and leg["vega"] == 0.0


def test_propose_stock_self_hedge(session):
    u = Instrument(symbol="AAPL", kind="stock", status="active", source="manual")
    session.add(u); session.flush()
    legs = hedging_legs.propose(session, underlying_id=u.id, strategy="delta_neutral")
    assert len(legs) == 1
    assert legs[0]["instrument_id"] == u.id
    assert legs[0]["instrument_type"] == "spot"
    assert legs[0]["family"] == "stock"
    assert legs[0]["role"] == "delta"


def test_stock_spot_leg_priced_delta_one(session):
    u = Instrument(symbol="600519.SH", kind="stock", exchange="SH", status="active", source="manual")
    session.add(u); session.flush()
    record_quote(session, instrument_id=u.id, price=1800.0,
                 as_of=datetime.utcnow(), source="manual")
    priced = hedging_legs.price(session, [{"instrument_id": u.id, "role": "delta"}], spot=1800.0)
    leg = priced[0]
    assert leg["priced_ok"] is True
    assert leg["instrument_type"] == "spot"
    assert leg["delta"] == 1800.0
    assert leg["gamma"] == 0.0
    assert leg["vega"] == 0.0


def test_propose_stock_inactive_returns_no_legs(session):
    u = Instrument(symbol="AAPL", kind="stock", status="draft", source="manual")
    session.add(u); session.flush()
    legs = hedging_legs.propose(session, underlying_id=u.id, strategy="delta_neutral")
    assert legs == []
