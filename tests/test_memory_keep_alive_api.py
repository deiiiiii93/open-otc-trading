"""New fields asserted at the HTTP layer (pydantic response models drop unnamed keys)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import MemoryEntry


@pytest.fixture
def mem_client(session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.routers.memory import build_memory_router
    from app.services.deep_agent.memory.runtime import reset_memory_runtime

    reset_memory_runtime()
    app = FastAPI()
    app.include_router(build_memory_router())
    with TestClient(app) as c:
        yield c
    reset_memory_runtime()


def _entry(content, **kw):
    return MemoryEntry(scope_type="user", scope_id="desk", content=content,
                       normalized_content=content.lower(), confidence=0.9, status="active",
                       created_by="api", pinned=True, meta={}, **kw)


def test_facts_serve_the_keep_alive_fields(mem_client, session):
    session.add_all([
        _entry("books in dollars", keep_alive_score=0.67, keep_alive_confidence=0.8,
               keep_alive_scored_at=datetime(2026, 9, 21)),
        _entry("other fact", keep_alive_unscored_reason="no_key"),
    ])
    session.commit()
    items = {i["content"]: i for i in mem_client.get("/api/memory/facts").json()["items"]}
    scored = items["books in dollars"]
    assert (scored["keep_alive_score"], scored["keep_alive_confidence"]) == (0.67, 0.8)
    assert scored["keep_alive_scored_at"].startswith("2026-09-21")
    assert scored["keep_alive_unscored_reason"] is None
    broken = items["other fact"]
    assert (broken["keep_alive_score"], broken["keep_alive_unscored_reason"]) == (None, "no_key")
