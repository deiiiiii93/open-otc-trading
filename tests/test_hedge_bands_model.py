# tests/test_hedge_bands_model.py
import pytest
import sqlalchemy.exc

from app.models import HedgeBand, Underlying


def test_hedge_band_round_trips(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    band = HedgeBand(
        underlying_id=u.id, delta_cash_band=500000.0, gamma_cash_band=50000.0,
        vega_band=10000.0, currency="CNY",
    )
    session.add(band)
    session.flush()
    assert session.query(HedgeBand).one().delta_cash_band == 500000.0


def test_hedge_band_defaults_row_has_null_underlying(session):
    band = HedgeBand(
        underlying_id=None, delta_cash_band=300000.0, gamma_cash_band=30000.0,
        vega_band=8000.0, currency="CNY",
    )
    session.add(band)
    session.flush()
    assert session.query(HedgeBand).filter(HedgeBand.underlying_id.is_(None)).one()


def test_hedge_band_duplicate_underlying_rejected(session):
    u = Underlying(symbol="000300.SH", asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    band1 = HedgeBand(
        underlying_id=u.id, delta_cash_band=100000.0, gamma_cash_band=10000.0,
        vega_band=5000.0, currency="CNY",
    )
    session.add(band1)
    session.flush()
    band2 = HedgeBand(
        underlying_id=u.id, delta_cash_band=200000.0, gamma_cash_band=20000.0,
        vega_band=6000.0, currency="CNY",
    )
    session.add(band2)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()


def test_hedge_band_duplicate_defaults_rejected(session):
    # A plain UNIQUE(underlying_id) treats NULLs as distinct, so two
    # portfolio-wide defaults rows (underlying_id=None) would slip through.
    # The partial unique index uq_hedge_bands_default must reject the second.
    session.add(HedgeBand(
        underlying_id=None, delta_cash_band=300000.0, gamma_cash_band=30000.0,
        vega_band=8000.0, currency="CNY",
    ))
    session.flush()
    session.add(HedgeBand(
        underlying_id=None, delta_cash_band=400000.0, gamma_cash_band=40000.0,
        vega_band=9000.0, currency="CNY",
    ))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()
