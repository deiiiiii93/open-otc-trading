from __future__ import annotations

from datetime import datetime

from app.models import Instrument, Portfolio, Position, Underlying
from app.services.quotes import record_quote
from app import database


def _seed(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    inst = Instrument(
        symbol="IC2406.CFFEX", kind="futures", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", parent_id=u.id,
        status="active", source="hedge_load",
    )
    session.add(inst)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id,
        underlying_id=inst.id,
        underlying=inst.symbol,
        product_type="Futures",
        quantity=1.0,
        entry_price=0.0,
        status="open",
        position_kind="listed",
        source_trade_id="HEDGE:42:1",
        source_payload={
            "hedge": {
                "is_hedge": True,
                "hedged_underlying": u.symbol,
                "instrument_id": inst.id,
            }
        },
    ))
    record_quote(session, instrument_id=inst.id, price=5600.0,
                 as_of=datetime.utcnow(), source="hedge_load")
    session.commit()
    return u


def test_underlyings_endpoint_lists_in_scope(client, session):
    _seed(session)
    resp = client.get("/api/hedging/underlyings")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["symbol"] for row in body] == ["000905.SH"]
    assert body[0]["symbol"] == "000905.SH"
    assert body[0]["families"][0]["family"] == "index_future"


def test_instruments_endpoint_annotates_allowed(client, session):
    u = _seed(session)
    resp = client.get(f"/api/hedging/instruments?underlying_id={u.id}&family=index_future")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["contract_code"] == "IC2406"
    assert rows[0]["allowed"] is False


def test_mark_then_map_then_unmark(client, session):
    u = _seed(session)
    inst_id = session.query(Instrument).filter(
        Instrument.source == "hedge_load").one().id
    mark_resp = client.post("/api/hedging/map/mark", json={"instrument_ids": [inst_id]})
    assert mark_resp.status_code == 200
    assert mark_resp.json()["marked"] == 1
    mp = client.get("/api/hedging/map").json()
    assert mp[0]["entries"][0]["contract_code"] == "IC2406"
    # Verify open_position_count is included in map response
    assert mp[0]["open_position_count"] == 1
    assert client.post("/api/hedging/map/unmark", json={"instrument_ids": [inst_id]}).status_code == 200
    # After unmark, map entries are gone but the exposure-only group remains
    # because the underlying still has an open position.
    after = client.get("/api/hedging/map").json()
    exposure_group = next((g for g in after if g["underlying_id"] == u.id), None)
    assert exposure_group is not None
    assert exposure_group["entries"] == []
    assert exposure_group["open_position_count"] == 1


def test_load_returns_task_id_and_rejects_concurrent(client, session, monkeypatch):
    _seed(session)
    # Do not actually run the threadpool job in the test.
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task", lambda *a, **k: None
    )
    first = client.post("/api/hedging/instruments/load")
    assert first.status_code == 200
    task_id = first.json()["task_id"]
    second = client.post("/api/hedging/instruments/load")
    assert second.status_code == 409
    assert second.json()["detail"]["in_flight_task_id"] == task_id


def test_instruments_endpoint_returns_stock_self_candidate(client, session):
    u = Instrument(symbol="AAPL", kind="stock", status="active", source="manual")
    session.add(u)
    session.flush()
    record_quote(session, instrument_id=u.id, price=150.0,
                 as_of=datetime.utcnow(), source="manual")
    session.commit()
    resp = client.get(f"/api/hedging/instruments?underlying_id={u.id}")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["contract_code"] == "AAPL"
    assert rows[0]["family"] == "stock"
    assert rows[0]["instrument_type"] == "spot"
    assert rows[0]["allowed"] is True


def test_underlyings_endpoint_lists_stock(client, session):
    u = Instrument(symbol="AAPL", kind="stock", status="active", source="manual")
    session.add(u)
    pf = Portfolio(name="pf_stock", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.commit()
    resp = client.get("/api/hedging/underlyings")
    assert resp.status_code == 200
    body = resp.json()
    row = next(r for r in body if r["symbol"] == "AAPL")
    assert row["families"] == [{"family": "stock", "total": 1, "allowed": 1}]


def test_instruments_endpoint_maps_legacy_live_status(client, session):
    u = _seed(session)
    resp = client.get(f"/api/hedging/instruments?underlying_id={u.id}&family=index_future&status=live")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "active"


def test_load_status_endpoint(client, session, monkeypatch):
    _seed(session)
    # Suppress actual background work so the task stays QUEUED.
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task", lambda *a, **k: None
    )
    resp = client.post("/api/hedging/instruments/load")
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    status_resp = client.get(f"/api/hedging/instruments/load/{task_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "queued"
    assert body["progress_current"] == 0
    assert "summary" in body

    # A nonexistent / wrong-kind task_id must return 404.
    not_found = client.get("/api/hedging/instruments/load/999999")
    assert not_found.status_code == 404
