# tests/test_instrument_models.py
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_instrument_table_and_compat_aliases():
    from app.models import Instrument, Underlying, UnderlyingPricingDefault

    assert Instrument.__tablename__ == "instruments"
    # Compatibility aliases keep the wide consumer surface compiling.
    assert Underlying is Instrument
    assert UnderlyingPricingDefault is Instrument
    cols = set(Instrument.__table__.columns.keys())
    assert {
        "symbol", "kind", "contract_code", "series_root", "expiry",
        "multiplier", "strike", "option_type", "parent_id", "loaded_at",
        "rate", "dividend_yield", "volatility", "status",
        "akshare_symbol", "akshare_asset_class",
    } <= cols
    assert "asset_class" not in cols  # renamed to kind; synonym only


def test_kind_synonym_and_parent_link():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        index = Instrument(symbol="000905.SH", kind="index", status="active")
        session.add(index)
        session.flush()
        fut = Instrument(
            symbol="IC2606.CFFEX", kind="futures", contract_code="IC2606",
            exchange="CFFEX", series_root="IC", parent_id=index.id,
            expiry=datetime(2026, 6, 19).date(), multiplier=200.0,
            status="active",
        )
        session.add(fut)
        session.commit()
        assert fut.asset_class == "futures"      # synonym reads kind
        assert fut.parent.symbol == "000905.SH"  # self-relationship


def test_position_fk_targets_instruments():
    from app.models import Position

    fk = next(iter(Position.__table__.columns["underlying_id"].foreign_keys))
    assert fk.target_fullname == "instruments.id"


def test_market_quote_and_assumption_tables():
    from app.models import AssumptionRow, AssumptionSet, MarketQuote

    assert MarketQuote.__tablename__ == "market_quotes"
    assert {"instrument_id", "as_of", "price", "price_type", "source",
            "market_data_profile_id", "meta"} <= set(
        MarketQuote.__table__.columns.keys()
    )
    assert AssumptionSet.__tablename__ == "assumption_sets"
    assert {"set_id", "instrument_id", "rate", "dividend_yield", "volatility"} <= set(
        AssumptionRow.__table__.columns.keys()
    )


def test_hedge_map_entry_gains_instrument_id():
    from app.models import HedgeMapEntry

    assert "instrument_id" in HedgeMapEntry.__table__.columns


def test_asset_class_synonym_is_writable():
    """ensure_underlying constructs rows with asset_class=...; the synonym
    must accept writes and land them in kind."""
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = Instrument(symbol="510500.SH", asset_class="etf", status="active")
        session.add(row)
        session.commit()
        assert row.kind == "etf"


def test_instrument_tags_defaults_to_empty_list():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = Instrument(symbol="TAGTEST.SH", kind="index")
        session.add(row)
        session.flush()
        assert row.tags == []


def test_instrument_tags_round_trips_a_list():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = Instrument(symbol="TAGTEST2.SH", kind="index", tags=["underlying"])
        session.add(row)
        session.flush()
        session.expire(row)
        assert row.tags == ["underlying"]
