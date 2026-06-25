"""TDD tests for GET /api/gateway/health + POST /api/gateway/reload
(sub-task 15c).

A fake runtime object is installed on app.state.gateway_runtime so the
real runtime (database, lock, connectors) is NOT started.  This keeps the
test fast and isolated.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


# ---------------------------------------------------------------------------
# Fake runtime helpers
# ---------------------------------------------------------------------------


def _make_owner_runtime() -> object:
    """Return a stub runtime where this process owns the worker lock."""
    runtime = AsyncMock()
    runtime.health = AsyncMock(
        return_value={
            "worker_lock_owner": True,
            "connectors": {
                "fake": {"state": "healthy", "detail": "in-memory fake"},
            },
        }
    )
    runtime.reload = AsyncMock(
        return_value={
            "worker_lock_owner": True,
            "connectors": {
                "fake": {"state": "healthy", "detail": "in-memory fake"},
            },
        }
    )
    return runtime


def _make_standby_runtime() -> object:
    """Return a stub runtime where this process does NOT own the worker lock."""
    runtime = AsyncMock()
    runtime.health = AsyncMock(
        return_value={
            "worker_lock_owner": False,
            "connectors": {},
        }
    )
    runtime.reload = AsyncMock(
        return_value={
            "worker_lock_owner": False,
            "status": "standby — reload skipped",
        }
    )
    return runtime


# ---------------------------------------------------------------------------
# TestClient factory (no real startup lifecycle)
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path, runtime=None) -> tuple[TestClient, object]:
    """Build a TestClient with an optional fake runtime on app.state."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    app = create_app(settings=settings)
    if runtime is not None:
        app.state.gateway_runtime = runtime
    client = TestClient(app)
    return client, runtime


# ---------------------------------------------------------------------------
# GET /api/gateway/health
# ---------------------------------------------------------------------------


def test_health_returns_503_when_runtime_not_set(tmp_path):
    """When no runtime is installed on app.state, health returns 503."""
    client, _ = _make_client(tmp_path, runtime=None)
    # Make sure there's no runtime on app.state
    resp = client.get("/api/gateway/health")
    assert resp.status_code == 503


def test_health_owner_returns_schema(tmp_path):
    """GET /api/gateway/health returns worker_lock_owner=True and connectors LIST."""
    fake = _make_owner_runtime()
    client, _ = _make_client(tmp_path, runtime=fake)

    resp = client.get("/api/gateway/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["worker_lock_owner"] is True
    # connectors must be a LIST
    assert isinstance(body["connectors"], list)
    assert len(body["connectors"]) == 1
    conn = body["connectors"][0]
    assert conn["name"] == "fake"
    assert conn["state"] == "healthy"
    assert "detail" in conn


def test_health_standby_returns_schema(tmp_path):
    """GET /api/gateway/health returns worker_lock_owner=False and empty connectors list."""
    fake = _make_standby_runtime()
    client, _ = _make_client(tmp_path, runtime=fake)

    resp = client.get("/api/gateway/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["worker_lock_owner"] is False
    assert isinstance(body["connectors"], list)
    assert len(body["connectors"]) == 0


# ---------------------------------------------------------------------------
# POST /api/gateway/reload
# ---------------------------------------------------------------------------


def test_reload_503_when_runtime_not_set(tmp_path):
    """POST /api/gateway/reload returns 503 when no runtime is on app.state."""
    client, _ = _make_client(tmp_path, runtime=None)
    resp = client.post("/api/gateway/reload")
    assert resp.status_code == 503


def test_reload_as_owner_returns_200_and_calls_reload(tmp_path):
    """POST /api/gateway/reload as owner → 200 and runtime.reload() was called."""
    fake = _make_owner_runtime()
    client, runtime = _make_client(tmp_path, runtime=fake)

    resp = client.post("/api/gateway/reload")
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_lock_owner"] is True
    assert isinstance(body["connectors"], list)

    # runtime.reload() must have been called exactly once
    runtime.reload.assert_called_once()


def test_reload_as_non_owner_returns_409_and_does_not_call_reload(tmp_path):
    """POST /api/gateway/reload when not owner → 409 and reload() NOT called."""
    fake = _make_standby_runtime()
    client, runtime = _make_client(tmp_path, runtime=fake)

    resp = client.post("/api/gateway/reload")
    assert resp.status_code == 409

    # runtime.reload() must NOT have been called
    runtime.reload.assert_not_called()
