"""Read-only /api/audit router (audit spec §6)."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentActionAudit
from app.routers.audit import build_audit_router


@pytest.fixture()
def audit_client(session):
    app = FastAPI()
    app.include_router(build_audit_router())
    return TestClient(app)


def _seed(session):
    rows = [
        AgentActionAudit(
            kind="execution", status="ok", tool_name="book_position",
            tool_class="domain_write", mode="yolo", tool_call_id="c1",
            args_json={"qty": 1},
        ),
        AgentActionAudit(
            kind="execution", status="denied", deny_reason="capability",
            tool_name="delete_position", tool_class="domain_write",
            mode="interactive", args_json={},
        ),
        AgentActionAudit(
            kind="hitl_decision", status="rejected", tool_name="book_position",
            tool_class="domain_write", actor="desk_user", audit_ref="r1",
            args_json={},
        ),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def test_list_filters_and_pagination(audit_client, session):
    _seed(session)
    body = audit_client.get("/api/audit/actions").json()
    assert body["total"] == 3
    assert body["items"][0]["id"] > body["items"][-1]["id"]  # newest first
    assert audit_client.get("/api/audit/actions?status=denied").json()["total"] == 1
    assert audit_client.get("/api/audit/actions?mode=yolo").json()["total"] == 1
    assert audit_client.get("/api/audit/actions?kind=hitl_decision").json()["total"] == 1
    assert (
        audit_client.get("/api/audit/actions?tool_name=book_position").json()["total"]
        == 2
    )
    # tool_name is a SEARCH filter (substring, case-insensitive) — the UI
    # exposes it as a search box (code-review finding).
    assert audit_client.get("/api/audit/actions?tool_name=book").json()["total"] == 2
    assert audit_client.get("/api/audit/actions?tool_name=POSITION").json()["total"] == 3
    assert len(audit_client.get("/api/audit/actions?limit=2").json()["items"]) == 2
    assert audit_client.get("/api/audit/actions?limit=500").status_code == 422  # cap


def test_list_sorted_by_occurred_at_not_id(audit_client, session):
    # Insertion (id) order deliberately contradicts occurred_at order — a
    # backfill or async write can land an older-timestamped row with a
    # higher id. The list must sort by time, not by id.
    base = datetime(2026, 6, 1, 12, 0, 0)
    older = AgentActionAudit(
        kind="execution", status="ok", tool_name="book_position",
        tool_class="domain_write", args_json={}, occurred_at=base,
    )
    newer = AgentActionAudit(
        kind="execution", status="ok", tool_name="close_position",
        tool_class="domain_write", args_json={},
        occurred_at=base + timedelta(days=5),
    )
    session.add_all([older, newer])
    session.commit()
    assert older.id < newer.id  # older row got the lower id, as inserted

    body = audit_client.get("/api/audit/actions").json()
    ids = [row["id"] for row in body["items"]]
    assert ids.index(newer.id) < ids.index(older.id)  # newer timestamp first


def test_detail_related_and_404(audit_client, session):
    rows = _seed(session)
    detail = audit_client.get(f"/api/audit/actions/{rows[0].id}").json()
    assert detail["tool_name"] == "book_position"
    assert detail["args_json"] == {"qty": 1}
    assert detail["related"] == []
    assert audit_client.get("/api/audit/actions/99999").status_code == 404


def test_detail_groups_chain_by_audit_ref(audit_client, session):
    a = AgentActionAudit(kind="hitl_proposal", status="proposed",
                         tool_name="book_position", tool_class="domain_write",
                         audit_ref="chain-1", args_json={})
    b = AgentActionAudit(kind="hitl_decision", status="approved",
                         tool_name="book_position", tool_class="domain_write",
                         audit_ref="chain-1", args_json={})
    c = AgentActionAudit(kind="execution", status="ok",
                         tool_name="book_position", tool_class="domain_write",
                         audit_ref="chain-1", args_json={})
    session.add_all([a, b, c])
    session.commit()
    detail = audit_client.get(f"/api/audit/actions/{c.id}").json()
    assert {r["kind"] for r in detail["related"]} == {"hitl_proposal", "hitl_decision"}


def test_summary_counts(audit_client, session):
    _seed(session)
    body = audit_client.get("/api/audit/summary").json()
    assert body["by_status"]["ok"] == 1
    assert body["by_status"]["denied"] == 1
    assert body["by_mode"]["yolo"] == 1
    assert body["by_class"]["domain_write"] == 3
    assert body["fail_closed_refusals"]["persisted"] == 0
    assert body["fail_closed_refusals"]["unpersisted"] >= 0


def test_read_only_surface(audit_client):
    assert audit_client.post("/api/audit/actions", json={}).status_code == 405
    assert audit_client.delete("/api/audit/actions/1").status_code == 405


def test_registered_in_main_app(client):
    resp = client.get("/api/audit/summary")
    assert resp.status_code == 200
    assert "by_status" in resp.json()
