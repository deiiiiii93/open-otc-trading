from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import AuditEvent, Instrument


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_register_underlying_creates_new_instrument():
    from app.tools.underlyings import register_underlying_tool

    result = register_underlying_tool.invoke({"symbol": "NEWUL.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "created_new"
    assert result["data"]["symbol"] == "NEWUL.SH"

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol="NEWUL.SH").one()
        assert "underlying" in row.tags
        assert row.status == "active"

        audit = session.query(AuditEvent).filter_by(
            event_type="instrument.underlying_registered", subject_id=str(row.id)
        ).one()
        assert audit.payload["action"] == "created_new"
        assert audit.payload["symbol"] == "NEWUL.SH"


def test_register_underlying_reactivating_a_stock_syncs_hedge_tag():
    """Reactivating an inactive stock instrument is the one non-hedge-map
    write path that can flip its self-hedge eligibility — the tool must
    sync the hedge tag, not just the underlying tag."""
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        stock = Instrument(symbol="600519.SH", kind="stock", status="draft", currency="CNY")
        session.add(stock)
        session.commit()
        stock_id = stock.id

    register_underlying_tool.invoke({"symbol": "600519.SH"})

    with database.SessionLocal() as session:
        row = session.get(Instrument, stock_id)
        assert row.status == "active"
        assert "hedge" in row.tags


def test_register_underlying_tags_existing_instrument():
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        session.add(Instrument(symbol="EXIST.SH", kind="index", status="draft", tags=[]))
        session.commit()

    result = register_underlying_tool.invoke({"symbol": "EXIST.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "tagged_existing"

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol="EXIST.SH").one()
        assert "underlying" in row.tags
        assert row.status == "active"

        audit = session.query(AuditEvent).filter_by(
            event_type="instrument.underlying_registered", subject_id=str(row.id)
        ).one()
        assert audit.payload["action"] == "tagged_existing"


def test_register_underlying_already_registered_is_a_noop():
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        session.add(Instrument(symbol="ALREADY.SH", kind="index", status="active", tags=["underlying"]))
        session.commit()

    result = register_underlying_tool.invoke({"symbol": "ALREADY.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "already_registered"
