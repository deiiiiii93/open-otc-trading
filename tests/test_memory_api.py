"""REST API tests for /api/memory/* endpoints.

Uses a standalone FastAPI app (not create_app()) to avoid loading
config/agent_channels.yaml, consistent with test_tracing_router.py pattern.
The `session` fixture already calls configure_database() + init_db(), so
database.SessionLocal() in the router resolves to the test DB.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mem_client(session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory.runtime import reset_memory_runtime
    from app.routers.memory import build_memory_router
    reset_memory_runtime()
    app = FastAPI()
    app.include_router(build_memory_router())
    with TestClient(app) as c:
        yield c
    reset_memory_runtime()


def test_create_and_list(mem_client):
    r = mem_client.post("/api/memory/facts", json={
        "scope_type": "user", "content": "books all trades in USD", "confidence": 0.9})
    assert r.status_code == 201
    fact = r.json()
    assert fact["scope_id"] == "desk" and "normalized_content" not in fact
    listed = mem_client.get("/api/memory/facts?scope_type=user").json()
    assert listed["total"] == 1


def test_book_create_requires_scope_id(mem_client):
    r = mem_client.post("/api/memory/facts", json={"scope_type": "book", "content": "x book detail"})
    assert r.status_code == 400


def test_confidence_floor_400(mem_client):
    r = mem_client.post("/api/memory/facts", json={
        "scope_type": "user", "content": "weak fact here", "confidence": 0.5})
    assert r.status_code == 400


def test_dedup_409(mem_client):
    mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "Books in USD"})
    r = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "books   in usd"})
    assert r.status_code == 409


def test_approve_makes_domain_injectable(mem_client):
    created = mem_client.post("/api/memory/facts", json={
        "scope_type": "domain", "content": "CNH fixings use ACT/365"}).json()
    assert created["status"] == "proposed"
    approved = mem_client.post(f"/api/memory/facts/{created['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"


def test_delete_idempotent(mem_client):
    fid = mem_client.post("/api/memory/facts", json={
        "scope_type": "user", "content": "net delta hedger"}).json()["id"]
    assert mem_client.delete(f"/api/memory/facts/{fid}").status_code == 204
    assert mem_client.delete(f"/api/memory/facts/{fid}").status_code == 204
    assert mem_client.delete("/api/memory/facts/999999").status_code == 404


def test_status_shape(mem_client):
    mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "books in USD"})
    body = mem_client.get("/api/memory/status").json()
    assert body["enabled"] is True
    assert body["counts"]["user"]["active"] == 1
    assert "config" in body


def test_api_create_runs_centralized_cap(mem_client, monkeypatch):
    # tiny cap; api rows are pinned -> overflow path runs without rejecting creates.
    from app.services.deep_agent.memory import runtime as rt
    from app.services.deep_agent.memory.config import MemoryConfig
    rt.reset_memory_runtime()
    monkeypatch.setattr(rt, "get_memory_config", lambda: MemoryConfig(enabled=True, max_facts_per_scope=2))
    for i in range(3):
        r = mem_client.post("/api/memory/facts", json={
            "scope_type": "user", "content": f"stable preference number {i}"})
        assert r.status_code == 201
    # Prove _enforce_caps() actually ran on the API path: pinned rows can't be
    # evicted, so the overflow counter MUST have fired.
    assert rt.get_memory_store().counters["memory_cap_pinned_overflow"] >= 1


def test_runtime_reset_changes_api_behavior(mem_client, monkeypatch):
    # default floor 0.7 accepts confidence 0.8
    assert mem_client.post("/api/memory/facts", json={
        "scope_type": "user", "content": "books in USD always", "confidence": 0.8}).status_code == 201
    # raise floor to 0.9 + reset: a per-call store resolve must pick up the new
    # config (a stale build-time store would still accept 0.8).
    from app.services.deep_agent.memory import runtime as rt
    from app.services.deep_agent.memory.config import MemoryConfig
    monkeypatch.setattr(rt, "get_memory_config",
                        lambda: MemoryConfig(enabled=True, confidence_floor=0.9))
    rt.reset_memory_runtime()
    r = mem_client.post("/api/memory/facts", json={
        "scope_type": "user", "content": "hedges by underlying net", "confidence": 0.8})
    assert r.status_code == 400  # below the new floor -> proves no stale store
    rt.reset_memory_runtime()


# --- Memory Console additions (provenance, pin, archived 409, list semantics) ---

def test_factout_includes_provenance(mem_client):
    body = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "usd books", "confidence": 0.9}).json()
    assert body["pinned"] is True            # api+user create is auto-pinned
    assert body["created_by"] == "api"
    assert body["extractor_model"] is None
    assert body["source_session_id"] is None


def test_pin_endpoint_round_trip_and_404(mem_client):
    fid = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "pin target", "confidence": 0.9}).json()["id"]
    assert mem_client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": False}).json()["pinned"] is False
    assert mem_client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": True}).json()["pinned"] is True
    assert mem_client.patch("/api/memory/facts/999999/pin", json={"pinned": True}).status_code == 404


def test_pin_archived_returns_409(mem_client):
    fid = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "to archive", "confidence": 0.9}).json()["id"]
    assert mem_client.delete(f"/api/memory/facts/{fid}").status_code == 204
    assert mem_client.patch(f"/api/memory/facts/{fid}/pin", json={"pinned": True}).status_code == 409


def test_provenance_coerces_malformed_meta(mem_client, session):
    from app.models import MemoryEntry
    from app import database
    with database.SessionLocal() as s:
        s.add(MemoryEntry(scope_type="user", scope_id="desk", content="legacy meta",
                          normalized_content="legacy meta", confidence=0.9, status="active",
                          created_by="extractor", meta={"extractor_model": 123, "session_id": "not-int"}))
        s.commit()
    rows = mem_client.get("/api/memory/facts", params={"scope_type": "user"}).json()["items"]
    legacy = [x for x in rows if x["content"] == "legacy meta"][0]
    assert legacy["extractor_model"] is None and legacy["source_session_id"] is None


def test_patch_category_clear_and_unchanged(mem_client):
    fid = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "cat fact", "confidence": 0.9, "category": "pref"}).json()["id"]
    assert mem_client.patch(f"/api/memory/facts/{fid}", json={"confidence": 0.95}).json()["category"] == "pref"
    assert mem_client.patch(f"/api/memory/facts/{fid}", json={"category": ""}).json()["category"] is None


def test_patch_archived_returns_409(mem_client):
    fid = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "edit archived", "confidence": 0.9}).json()["id"]
    mem_client.delete(f"/api/memory/facts/{fid}")
    assert mem_client.patch(f"/api/memory/facts/{fid}", json={"content": "nope"}).status_code == 409


def test_approve_archived_returns_409(mem_client):
    fid = mem_client.post("/api/memory/facts", json={"scope_type": "domain", "content": "dom approve archived", "confidence": 0.9}).json()["id"]
    mem_client.delete(f"/api/memory/facts/{fid}")
    assert mem_client.post(f"/api/memory/facts/{fid}/approve").status_code == 409


def test_list_status_semantics(mem_client):
    active = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "active one", "confidence": 0.9}).json()["id"]
    arch = mem_client.post("/api/memory/facts", json={"scope_type": "user", "content": "to archive 2", "confidence": 0.9}).json()["id"]
    mem_client.delete(f"/api/memory/facts/{arch}")
    current = [x["id"] for x in mem_client.get("/api/memory/facts", params={"scope_type": "user"}).json()["items"]]
    assert active in current and arch not in current
    all_ids = [x["id"] for x in mem_client.get("/api/memory/facts", params={"scope_type": "user", "status": "all"}).json()["items"]]
    assert active in all_ids and arch in all_ids
    only_arch = [x["id"] for x in mem_client.get("/api/memory/facts", params={"scope_type": "user", "status": "archived"}).json()["items"]]
    assert only_arch == [arch]
