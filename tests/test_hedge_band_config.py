# tests/test_hedge_band_config.py
from app.models import HedgeBand, Underlying
from app.services.domains import hedging_strategy as hs


def _underlying(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u); session.flush()
    return u


def test_resolve_falls_back_to_defaults(session):
    session.add(HedgeBand(underlying_id=None, delta_cash_band=300000.0,
                          gamma_cash_band=30000.0, vega_band=8000.0, currency="CNY"))
    u = _underlying(session)
    session.flush()
    bands = hs.resolve_bands(session, underlying_id=u.id)
    assert bands == {"delta": 300000.0, "gamma": 30000.0, "vega": 8000.0}


def test_resolve_prefers_underlying_override(session):
    u = _underlying(session)
    session.add(HedgeBand(underlying_id=None, delta_cash_band=300000.0,
                          gamma_cash_band=30000.0, vega_band=8000.0, currency="CNY"))
    session.add(HedgeBand(underlying_id=u.id, delta_cash_band=500000.0,
                          gamma_cash_band=50000.0, vega_band=10000.0, currency="CNY"))
    session.flush()
    bands = hs.resolve_bands(session, underlying_id=u.id)
    assert bands["delta"] == 500000.0


def test_defaults_row_addressable_via_none(session):
    # The portfolio-wide defaults row is addressed by underlying_id=None on both
    # read and write — no per-underlying id required.
    hs.set_bands(session, underlying_id=None,
                 bands={"delta": 333.0, "gamma": 22.0, "vega": 11.0}, actor="desk_user")
    session.flush()
    assert hs.resolve_bands(session, underlying_id=None) == {
        "delta": 333.0, "gamma": 22.0, "vega": 11.0}
    # Re-addressing None upserts the same single defaults row (no duplicate).
    hs.set_bands(session, underlying_id=None,
                 bands={"delta": 999.0, "gamma": 22.0, "vega": 11.0}, actor="desk_user")
    session.flush()
    assert session.query(HedgeBand).filter(HedgeBand.underlying_id.is_(None)).count() == 1
    assert hs.resolve_bands(session, underlying_id=None)["delta"] == 999.0


def test_resolve_none_with_no_row_falls_back_to_builtin(session):
    assert hs.resolve_bands(session, underlying_id=None) == hs._BUILTIN_DEFAULTS


def test_set_bands_upserts(session):
    u = _underlying(session)
    hs.set_bands(session, underlying_id=u.id,
                 bands={"delta": 1.0, "gamma": 2.0, "vega": 3.0}, actor="desk_user")
    session.flush()
    hs.set_bands(session, underlying_id=u.id,
                 bands={"delta": 9.0, "gamma": 2.0, "vega": 3.0}, actor="desk_user")
    session.flush()
    assert session.query(HedgeBand).filter_by(underlying_id=u.id).one().delta_cash_band == 9.0
