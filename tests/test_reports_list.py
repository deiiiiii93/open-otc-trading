from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def test_list_reports_returns_empty_initially(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/reports/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_reports_returns_created_jobs_newest_first(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio_response = client.post(
        "/api/portfolios",
        json={"name": "Desk-Q2", "base_currency": "USD"},
    )
    assert portfolio_response.status_code == 200
    portfolio_id = portfolio_response.json()["id"]

    first = client.post(
        "/api/reports/jobs",
        json={"report_type": "portfolio", "portfolio_id": portfolio_id, "title": "First report"},
    )
    second = client.post(
        "/api/reports/jobs",
        json={"report_type": "risk", "portfolio_id": portfolio_id, "title": "Second report"},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    listing = client.get("/api/reports/jobs")
    assert listing.status_code == 200
    jobs = listing.json()
    assert len(jobs) == 2
    assert jobs[0]["request_payload"]["title"] == "Second report"
    assert jobs[1]["request_payload"]["title"] == "First report"
