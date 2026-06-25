"""TDD tests for POST /api/gateway/linking-codes (sub-task 15a).

Test client pattern: reuses the `client` fixture from tests/conftest.py,
but we need a settings override with a small gateway_code_issue_per_min (2)
so we can trigger 429 deterministically. We build a local `client_ratelimited`
fixture that creates its own TestClient with the custom setting.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _make_client(
    tmp_path: Path, gateway_code_issue_per_min: int = 2
) -> tuple[TestClient, Settings]:
    """Build a TestClient backed by a fresh temp DB. Returns (client, settings)."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        gateway_code_issue_per_min=gateway_code_issue_per_min,
    )
    app = create_app(settings=settings)
    return TestClient(app), settings


# ---------------------------------------------------------------------------
# 15a tests
# ---------------------------------------------------------------------------


def test_issue_linking_code_returns_code_and_expiry(tmp_path):
    """POST /api/gateway/linking-codes returns code + expires_at ISO string."""
    client, _settings = _make_client(tmp_path)
    resp = client.post("/api/gateway/linking-codes", json={"persona": "trader"})
    assert resp.status_code == 200
    body = resp.json()
    assert "code" in body
    assert "expires_at" in body
    # code should be >= 26 chars (base32 of 16 bytes)
    assert len(body["code"]) >= 26
    # expires_at should be a non-empty ISO 8601 string
    from datetime import datetime
    dt = datetime.fromisoformat(body["expires_at"])
    assert dt > datetime.utcnow()


def test_issue_linking_code_code_is_persisted(tmp_path):
    """The issued code must exist in the GatewayLinkingCode table."""
    client, settings = _make_client(tmp_path, gateway_code_issue_per_min=10)
    with client:
        resp = client.post("/api/gateway/linking-codes", json={"persona": "risk_manager"})
        assert resp.status_code == 200
        code = resp.json()["code"]

    # Query the DB directly via a session bound to the same engine that
    # create_app() configured — explicit, not relying on module-global order.
    from app import database
    from app.models import GatewayLinkingCode
    with database.SessionLocal() as session:
        row = session.query(GatewayLinkingCode).filter_by(code=code).one_or_none()
        assert row is not None
        assert row.persona == "risk_manager"


def test_invalid_persona_returns_422(tmp_path):
    """An unknown persona name must return 422."""
    client, _settings = _make_client(tmp_path)
    resp = client.post("/api/gateway/linking-codes", json={"persona": "admin"})
    assert resp.status_code == 422


def test_rate_limit_returns_429(tmp_path):
    """Exceeding gateway_code_issue_per_min within the window returns 429."""
    # Use limit=2 so the 3rd request triggers 429 deterministically
    client, _settings = _make_client(tmp_path, gateway_code_issue_per_min=2)
    r1 = client.post("/api/gateway/linking-codes", json={"persona": "trader"})
    r2 = client.post("/api/gateway/linking-codes", json={"persona": "trader"})
    r3 = client.post("/api/gateway/linking-codes", json={"persona": "trader"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_all_known_personas_accepted(tmp_path):
    """trader, risk_manager, and high_board are all valid."""
    client, _settings = _make_client(tmp_path, gateway_code_issue_per_min=10)
    for persona in ("trader", "risk_manager", "high_board"):
        resp = client.post("/api/gateway/linking-codes", json={"persona": persona})
        assert resp.status_code == 200, f"persona={persona} failed: {resp.json()}"
