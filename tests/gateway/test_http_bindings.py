"""TDD tests for GET /api/gateway/bindings + DELETE /api/gateway/bindings/{id}
(sub-task 15b).

Reuses the same _make_client helper pattern as test_http_enroll.py.
Bindings are seeded directly via the ORM (bypassing the HTTP endpoint) to
keep test setup fast and decoupled from the enroll endpoint.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database
from app.config import Settings
from app.main import create_app


def _make_client(tmp_path: Path) -> tuple[TestClient, Settings]:
    """Build a TestClient backed by a fresh temp DB. Returns (client, settings)."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    app = create_app(settings=settings)
    return TestClient(app), settings


def _seed_bindings(n: int, *, status: str = "active") -> None:
    """Seed *n* GatewayBinding rows into the currently-configured DB."""
    from app.models import GatewayBinding
    import datetime as dt

    with database.SessionLocal() as session:
        for i in range(n):
            b = GatewayBinding(
                provider="feishu",
                external_account_id=f"ou_{status}_{i}",
                workspace_id="tk_test",
                desk_user="desk_user",
                persona="trader",
                status=status,
                # stagger bound_at so ordering is deterministic
                bound_at=dt.datetime(2024, 1, 1, 0, 0, 0) + dt.timedelta(seconds=i),
            )
            session.add(b)
        session.commit()


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


def test_list_bindings_returns_page_and_cursor(tmp_path):
    """Seeding > limit rows produces a non-null next_cursor on page 1."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(55)  # default limit = 50

    resp = client.get("/api/gateway/bindings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["bindings"]) == 50
    assert body["next_cursor"] is not None


def test_pagination_pages_are_disjoint(tmp_path):
    """Page 1 and page 2 must share no IDs."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(70)

    r1 = client.get("/api/gateway/bindings", params={"limit": 30})
    assert r1.status_code == 200
    b1 = r1.json()
    assert len(b1["bindings"]) == 30
    cursor = b1["next_cursor"]
    assert cursor is not None

    r2 = client.get("/api/gateway/bindings", params={"limit": 30, "cursor": cursor})
    assert r2.status_code == 200
    b2 = r2.json()
    assert len(b2["bindings"]) >= 1  # at least one row on page 2

    ids1 = {row["id"] for row in b1["bindings"]}
    ids2 = {row["id"] for row in b2["bindings"]}
    assert ids1.isdisjoint(ids2), "Pages must not overlap"


def test_cursor_round_trips(tmp_path):
    """Cursor encode → decode → encode must be stable (idempotent)."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(10)

    r = client.get("/api/gateway/bindings", params={"limit": 5})
    body = r.json()
    cursor = body["next_cursor"]
    assert cursor is not None

    # decode the cursor
    decoded = json.loads(base64.b64decode(cursor + "==").decode())
    assert "bound_at" in decoded
    assert "id" in decoded

    # re-encode and compare
    re_encoded = base64.b64encode(
        json.dumps(decoded, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    assert cursor == re_encoded


def test_last_page_has_no_next_cursor(tmp_path):
    """When no more rows exist, next_cursor must be null."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(5)

    r = client.get("/api/gateway/bindings", params={"limit": 10})
    body = r.json()
    assert len(body["bindings"]) == 5
    assert body["next_cursor"] is None


def test_status_filter_active(tmp_path):
    """status=active returns only active bindings."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(3, status="active")
    _seed_bindings(2, status="revoked")

    r = client.get("/api/gateway/bindings", params={"status": "active"})
    body = r.json()
    assert all(b["status"] == "active" for b in body["bindings"])
    assert len(body["bindings"]) == 3


def test_status_filter_revoked(tmp_path):
    """status=revoked returns only revoked bindings."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(3, status="active")
    _seed_bindings(4, status="revoked")

    r = client.get("/api/gateway/bindings", params={"status": "revoked"})
    body = r.json()
    assert all(b["status"] == "revoked" for b in body["bindings"])
    assert len(body["bindings"]) == 4


def test_limit_clamped_to_200(tmp_path):
    """limit > 200 is clamped to 200."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(10)

    r = client.get("/api/gateway/bindings", params={"limit": 999})
    assert r.status_code == 200
    # Response should not error out and should return <= 200 rows
    assert len(r.json()["bindings"]) <= 200


# ---------------------------------------------------------------------------
# Revoke (DELETE) tests
# ---------------------------------------------------------------------------


def test_revoke_binding_returns_200(tmp_path):
    """DELETE /api/gateway/bindings/{id} returns 200 for an existing binding."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(1)

    # Get the binding id
    r = client.get("/api/gateway/bindings")
    binding_id = r.json()["bindings"][0]["id"]

    resp = client.delete(f"/api/gateway/bindings/{binding_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


def test_revoke_binding_is_idempotent(tmp_path):
    """DELETE an already-revoked binding also returns 200."""
    client, settings = _make_client(tmp_path)
    _seed_bindings(1)

    r = client.get("/api/gateway/bindings")
    binding_id = r.json()["bindings"][0]["id"]

    r1 = client.delete(f"/api/gateway/bindings/{binding_id}")
    r2 = client.delete(f"/api/gateway/bindings/{binding_id}")
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_revoke_unknown_id_returns_404(tmp_path):
    """DELETE /api/gateway/bindings/{id} with a nonexistent id returns 404."""
    client, settings = _make_client(tmp_path)
    resp = client.delete("/api/gateway/bindings/99999")
    assert resp.status_code == 404
