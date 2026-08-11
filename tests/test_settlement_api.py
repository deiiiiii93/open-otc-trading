"""HTTP boundary.

These tests assert writes actually PERSIST across requests — a write service
that copies a read-only ``_session_scope`` answers 200 and saves nothing, and
only an HTTP-level test catches it.
"""
from __future__ import annotations

import pytest

from app.models import Portfolio, Position, PositionLifecycleEvent


@pytest.fixture
def seeded(session):
    portfolio = Portfolio(name="API Test Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        product_type="SnowballOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id,
        event_type="settle",
        event_data={"settlement_amount": 500.0, "settlement_date": "2026-08-20"},
    )
    session.add(event)
    session.commit()
    return portfolio, position, event


def _generate(client, portfolio):
    return client.post(
        "/api/settlement/cashflows/generate", json={"portfolio_id": portfolio.id}
    )


def _first_row(client, portfolio):
    return client.get(
        "/api/settlement/cashflows", params={"portfolio_id": portfolio.id}
    ).json()["items"][0]


def test_generate_then_list(client, seeded):
    portfolio, _, _ = seeded
    created = _generate(client, portfolio)
    assert created.status_code == 200
    assert created.json()["created"] == 1

    listed = client.get(
        "/api/settlement/cashflows", params={"portfolio_id": portfolio.id}
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["status"] == "pending"
    assert row["row_version"] == 1
    assert row["amount"] == 500.0
    assert row["underlying"] == "AAPL"


def test_generate_is_idempotent_over_http(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    second = _generate(client, portfolio)
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 1


def test_patch_persists_across_requests(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)

    patched = client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={"amount": 640.5, "expected_row_version": row["row_version"]},
    )
    assert patched.status_code == 200

    refetched = client.get(f"/api/settlement/cashflows/{row['id']}").json()
    assert refetched["amount"] == 640.5, "the write did not persist"
    assert refetched["row_version"] == row["row_version"] + 1


def test_stale_row_version_returns_409(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    response = client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={"amount": 1.0, "expected_row_version": row["row_version"] + 5},
    )
    assert response.status_code == 409


def test_illegal_transition_returns_422(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    response = client.post(
        f"/api/settlement/cashflows/{row['id']}/settle",
        json={"expected_row_version": row["row_version"]},
    )
    assert response.status_code == 422


def test_release_then_settle_over_http(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)

    released = client.post(
        f"/api/settlement/cashflows/{row['id']}/release",
        json={"expected_row_version": row["row_version"]},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"

    settled = client.post(
        f"/api/settlement/cashflows/{row['id']}/settle",
        json={"expected_row_version": released.json()["row_version"]},
    )
    assert settled.status_code == 200
    assert settled.json()["status"] == "settled"


def test_block_records_its_reason(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    blocked = client.post(
        f"/api/settlement/cashflows/{row['id']}/block",
        json={"expected_row_version": row["row_version"], "reason": "cpty dispute"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["block_reason"] == "cpty dispute"


def test_detail_includes_history_and_notices(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    detail = client.get(f"/api/settlement/cashflows/{row['id']}").json()
    assert detail["events"][0]["action"] == "generated"
    assert detail["notices"] == []


def test_missing_cashflow_returns_404(client):
    assert client.get("/api/settlement/cashflows/99999").status_code == 404


def test_summary_counts_by_status(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    summary = client.get(
        "/api/settlement/summary", params={"portfolio_id": portfolio.id}
    ).json()
    assert summary["by_status"]["pending"] == 1
    assert summary["stale_count"] == 0
    assert summary["totals_by_currency"]["USD"] == 500.0


def test_refresh_reports_what_it_checked(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    refreshed = client.post(
        "/api/settlement/cashflows/refresh", json={"portfolio_id": portfolio.id}
    ).json()
    assert refreshed["checked"] == 1
    assert refreshed["flagged"] == 0


def test_notice_endpoint_writes_and_returns_the_artifact(client, seeded, settings):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={
            "counterparty": "Acme Capital",
            "expected_row_version": row["row_version"],
        },
    )

    created = client.post(f"/api/settlement/cashflows/{row['id']}/notice", json={})
    assert created.status_code == 200
    body = created.json()
    assert body["version"] == 1
    assert (settings.artifact_dir / body["artifact_path"]).exists()


def test_notice_without_a_counterparty_returns_422(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    response = client.post(f"/api/settlement/cashflows/{row['id']}/notice", json={})
    assert response.status_code == 422


def test_status_filter_narrows_the_list(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    assert (
        client.get(
            "/api/settlement/cashflows",
            params={"portfolio_id": portfolio.id, "status": "released"},
        ).json()["total"]
        == 0
    )
    assert (
        client.get(
            "/api/settlement/cashflows",
            params={"portfolio_id": portfolio.id, "status": "pending"},
        ).json()["total"]
        == 1
    )


def test_patch_rejects_unknown_fields(client, seeded):
    portfolio, _, _ = seeded
    _generate(client, portfolio)
    row = _first_row(client, portfolio)
    response = client.patch(
        f"/api/settlement/cashflows/{row['id']}",
        json={"expected_row_version": row["row_version"], "sneaky": 1},
    )
    assert response.status_code == 422
