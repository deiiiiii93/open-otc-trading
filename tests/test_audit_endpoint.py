from __future__ import annotations

from pathlib import Path

from test_api import make_client


def test_audit_events_listing_returns_recent(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    client.post(
        "/api/batch-pricing/runs", json={"portfolio_id": portfolio["id"]}
    )
    res = client.get("/api/audit/events")
    assert res.status_code == 200
    events = res.json()
    assert len(events) >= 2  # portfolio.created + batch_pricing.queued at minimum
    types = {e["event_type"] for e in events}
    assert "portfolio.created" in types
    assert "batch_pricing.queued" in types


def test_audit_events_filter_by_event_type(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    client.post(
        "/api/batch-pricing/runs", json={"portfolio_id": portfolio["id"]}
    )
    res = client.get("/api/audit/events?event_type=batch_pricing.queued")
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "batch_pricing.queued"


def test_audit_events_filter_by_subject(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    res = client.get(
        f"/api/audit/events?subject_type=portfolio&subject_id={portfolio['id']}"
    )
    assert res.status_code == 200
    events = res.json()
    assert all(
        e["subject_type"] == "portfolio" and e["subject_id"] == str(portfolio["id"])
        for e in events
    )
    assert len(events) >= 1
