"""REST-endpoint tests for /api/backtest/*

Uses the shared conftest fixtures: `client` and `session`.
"""
from __future__ import annotations

import pytest

from app.models import BacktestRun, Portfolio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_portfolio(session) -> Portfolio:
    pf = Portfolio(name="bt_api_test", base_currency="CNY")
    session.add(pf)
    session.commit()
    return pf


# ---------------------------------------------------------------------------
# POST /api/backtest/runs — validation
# ---------------------------------------------------------------------------


def test_create_run_validates_window(client, session):
    """Inverted date window (start >= end) must return 400 before any dispatch."""
    pf = _make_portfolio(session)
    r = client.post(
        "/api/backtest/runs",
        json={
            "portfolio_id": pf.id,
            "spec": {"start": "2024-04-30", "end": "2024-01-02"},
        },
    )
    assert r.status_code == 400, r.text


def test_create_run_missing_portfolio_404(client, session):
    """Nonexistent portfolio returns 404."""
    r = client.post(
        "/api/backtest/runs",
        json={
            "portfolio_id": 999999,
            "spec": {"start": "2024-01-02", "end": "2024-04-30"},
        },
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# GET /api/backtest/runs/{run_id} — 404 for missing
# ---------------------------------------------------------------------------


def test_get_missing_run_404(client, session):
    assert client.get("/api/backtest/runs/999999").status_code == 404


# ---------------------------------------------------------------------------
# GET /api/backtest/runs — list by portfolio
# ---------------------------------------------------------------------------


def test_list_runs_empty(client, session):
    pf = _make_portfolio(session)
    r = client.get("/api/backtest/runs", params={"portfolio_id": pf.id})
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# DELETE /api/backtest/runs/{run_id} — 404 for missing
# ---------------------------------------------------------------------------


def test_delete_missing_run_404(client, session):
    assert client.delete("/api/backtest/runs/999999").status_code == 404


def test_backtest_html_artifact_renders_inline_and_downloads_on_request(client, session, tmp_path):
    pf = _make_portfolio(session)
    artifact = tmp_path / "dashboard_AAPL.html"
    artifact.write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    run = BacktestRun(
        portfolio_id=pf.id,
        status="completed",
        spec={"start": "2024-01-02", "end": "2024-04-30"},
        config={},
        results={},
        excluded_positions=[],
        artifacts={"dashboards": {"AAPL": str(artifact)}},
    )
    session.add(run)
    session.commit()

    inline = client.get(f"/api/backtest/runs/{run.id}/artifacts/{artifact.name}")
    assert inline.status_code == 200
    assert inline.headers["content-type"].startswith("text/html")
    assert "inline" in inline.headers["content-disposition"]

    download = client.get(
        f"/api/backtest/runs/{run.id}/artifacts/{artifact.name}",
        params={"download": "true"},
    )
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
