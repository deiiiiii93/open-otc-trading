"""TDD tests for runtime lifecycle wiring in create_app (sub-task 15d).

Drives the REAL startup/shutdown lifecycle via the TestClient context
manager.  The fake connector (gateway_enabled_connectors="fake") is used
so no external platform credentials are required.

Asserts:
- While inside the `with` block (startup ran), GET /api/gateway/health
  returns worker_lock_owner=True and the "fake" connector is in the list
  with state="healthy".
- After the `with` block exits (shutdown ran → lock released), the worker
  lock can be re-acquired by a different token, proving stop() released it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database
from app.config import Settings
from app.main import create_app
from app.services.gateway.runtime import acquire_worker_lock


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_lifecycle_app(tmp_path: Path):
    """Build the FastAPI app with a real fake connector and a temp DB."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'lifecycle_test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        gateway_enabled_connectors="fake",
        # Short lease so the test can reacquire quickly
        gateway_lock_lease_s=10,
    )
    app = create_app(settings=settings)
    return app, settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_lifecycle_health_while_running(tmp_path):
    """During startup the fake connector should appear as healthy."""
    app, _ = _make_lifecycle_app(tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/gateway/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # This process acquired the lock
        assert body["worker_lock_owner"] is True

        # Fake connector is in the list and started (state == "healthy")
        connector_names = [c["name"] for c in body["connectors"]]
        assert "fake" in connector_names

        fake_entry = next(c for c in body["connectors"] if c["name"] == "fake")
        assert fake_entry["state"] == "healthy"


def test_lifecycle_lock_released_after_shutdown(tmp_path):
    """After shutdown the worker lock must be free for re-acquisition."""
    app, settings = _make_lifecycle_app(tmp_path)

    with TestClient(app) as client:
        # Confirm we own the lock while running
        resp = client.get("/api/gateway/health")
        assert resp.status_code == 200
        assert resp.json()["worker_lock_owner"] is True

    # After `with` exits: startup ran then shutdown ran.
    # The runtime's stop() should have called release_worker_lock, which sets
    # lease_expires_at to the past.  A new acquire attempt with a different
    # token should succeed.
    with database.SessionLocal() as session:
        acquired = acquire_worker_lock(session, "new-owner-token-xyz", settings)

    assert acquired is True, (
        "Worker lock was not released during shutdown — "
        "stop() may not have called release_worker_lock"
    )
