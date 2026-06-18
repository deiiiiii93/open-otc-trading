# tests/test_instruments_service.py
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _mk(session, **kw):
    from app.models import Instrument
    row = Instrument(**{"status": "active", "kind": "index", **kw})
    session.add(row)
    session.flush()
    return row


def test_resolvable_includes_drafts_excludes_expired():
    """The silent draft-exclusion gotcha dies: resolvable = has AKShare
    mapping AND not expired/retired. Status 'draft' is IN."""
    from app import database
    from app.services.instruments import resolvable_market_data_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="000905.SH", status="active",
            akshare_symbol="000905", akshare_asset_class="index")
        _mk(session, symbol="LH2609.DCE", status="draft", kind="futures",
            akshare_symbol="LH2609", akshare_asset_class="futures")
        _mk(session, symbol="IC2503.CFFEX", status="expired", kind="futures",
            akshare_symbol="IC2503", akshare_asset_class="futures")
        _mk(session, symbol="NOMAP.SH", status="active", akshare_symbol=None)
        # "retired" is the spec'd manual-removal lifecycle state — pin its
        # exclusion so the filter isn't a dead letter.
        _mk(session, symbol="OLD2401.DCE", status="retired", kind="futures",
            akshare_symbol="OLD2401", akshare_asset_class="futures")
        session.commit()
        symbols = {r.symbol for r in resolvable_market_data_instruments(session)}
        assert symbols == {"000905.SH", "LH2609.DCE"}


def test_list_instruments_search_strips_and_matches_case_insensitively():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="LH2609.DCE", kind="futures")
        _mk(session, symbol="000905.SH")
        session.commit()
        # whitespace stripped, lowercase input matches via ilike
        hits = list_instruments(session, search="  lh26 ")
        assert [r.symbol for r in hits] == ["LH2609.DCE"]


def test_listed_option_requires_strike_and_type():
    from app.services.instruments import validate_instrument_terms

    with pytest.raises(ValueError, match="strike"):
        validate_instrument_terms(kind="listed_option", strike=None, option_type="C")
    with pytest.raises(ValueError, match="option_type"):
        validate_instrument_terms(kind="listed_option", strike=4000.0, option_type=None)
    validate_instrument_terms(kind="futures", strike=None, option_type=None)  # ok


def test_list_instruments_filters_kind_and_parent():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        idx = _mk(session, symbol="000905.SH")
        _mk(session, symbol="IC2606.CFFEX", kind="futures", parent_id=idx.id,
            series_root="IC")
        _mk(session, symbol="510500.SH", kind="etf")
        session.commit()
        futs = list_instruments(session, kind="futures")
        assert [r.symbol for r in futs] == ["IC2606.CFFEX"]
        children = list_instruments(session, parent_id=idx.id)
        assert [r.symbol for r in children] == ["IC2606.CFFEX"]
