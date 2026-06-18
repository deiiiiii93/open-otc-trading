# tests/test_quotes_service.py
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


@pytest.fixture()
def instrument():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = Instrument(symbol="000905.SH", kind="index", status="active")
        session.add(row)
        session.commit()
        yield row.id


def test_record_and_latest_quote(instrument):
    from app import database
    from app.services.quotes import latest_quote, record_quote

    with database.SessionLocal() as session:
        record_quote(session, instrument_id=instrument, price=6400.0,
                     as_of=datetime(2026, 6, 2), source="akshare")
        record_quote(session, instrument_id=instrument, price=6412.55,
                     as_of=datetime(2026, 6, 4), source="xlsx_import")
        session.commit()
        q = latest_quote(session, instrument, as_of=datetime(2026, 6, 4, 23))
        assert q.price == 6412.55
        # valuation-date cutoff: looking back before the second observation
        q2 = latest_quote(session, instrument, as_of=datetime(2026, 6, 3))
        assert q2.price == 6400.0


def test_latest_quote_tie_break_is_last_id(instrument):
    """Same as_of (e.g. conflicting xlsx rows): max id wins — last file row."""
    from app import database
    from app.services.quotes import latest_quote, record_quote

    as_of = datetime(2026, 6, 4)
    with database.SessionLocal() as session:
        record_quote(session, instrument_id=instrument, price=6410.0, as_of=as_of,
                     source="xlsx_import")
        record_quote(session, instrument_id=instrument, price=6412.55, as_of=as_of,
                     source="xlsx_import")
        session.commit()
        assert latest_quote(session, instrument, as_of=as_of).price == 6412.55


def test_latest_quote_none_when_empty(instrument):
    from app import database
    from app.services.quotes import latest_quote

    with database.SessionLocal() as session:
        assert latest_quote(session, instrument, as_of=datetime(2026, 6, 4)) is None


def test_latest_quotes_bulk(instrument):
    from app import database
    from app.models import Instrument
    from app.services.quotes import latest_quotes, record_quote

    with database.SessionLocal() as session:
        other = Instrument(symbol="LH2609.DCE", kind="futures", status="draft")
        session.add(other)
        session.flush()
        record_quote(session, instrument_id=instrument, price=6412.55,
                     as_of=datetime(2026, 6, 4), source="akshare")
        record_quote(session, instrument_id=other.id, price=13905.0,
                     as_of=datetime(2026, 6, 3), source="hedge_load")
        session.commit()
        out = latest_quotes(session, [instrument, other.id, 999999],
                            as_of=datetime(2026, 6, 4))
        assert out[instrument].price == 6412.55
        assert out[other.id].price == 13905.0
        assert 999999 not in out
