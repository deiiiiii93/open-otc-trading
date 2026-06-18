"""/api/tracing/* endpoints over a seeded trace store."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.tracing import build_tracing_router
from app.services.tracing.store import SpanEnd, SpanStart, TraceStore


@pytest.fixture()
def store(tmp_path):
    s = TraceStore(tmp_path / "traces.sqlite3")
    s.enqueue_insert(SpanStart(
        id="root", trace_id="root", parent_run_id=None,
        dotted_order="20260611T0900000000Zroot",
        thread_id=7, task_id=None, workflow_id=None, message_id=None,
        name="orchestrator", run_type="chain",
        start_time="2026-06-11T09:00:00",
        inputs=json.dumps({"q": "x" * 5000}),   # > preview cap
        extra="{}"))
    s.enqueue_insert(SpanStart(
        id="tool1", trace_id="root", parent_run_id="root",
        dotted_order="20260611T0900000000Zroot.20260611T0900010000Ztool1",
        thread_id=7, task_id=None, workflow_id=None, message_id=None,
        name="price_position", run_type="tool",
        start_time="2026-06-11T09:00:01", inputs='{"sym": "AAPL"}', extra="{}"))
    s.enqueue_finalize(SpanEnd(
        id="tool1", trace_id="root", parent_run_id="root",
        end_time="2026-06-11T09:00:02", status="success",
        outputs='{"pv": 1.23}', error=None,
        prompt_tokens=None, completion_tokens=None, total_tokens=None))
    s.enqueue_finalize(SpanEnd(
        id="root", trace_id="root", parent_run_id=None,
        end_time="2026-06-11T09:00:03", status="success",
        outputs='{"a": "done"}', error=None,
        prompt_tokens=None, completion_tokens=None, total_tokens=None))
    s.flush()
    yield s
    s.close()


@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_TRACING", "local")
    app = FastAPI()
    app.include_router(build_tracing_router(get_store=lambda: store))
    return TestClient(app)


def test_config_endpoint(client):
    body = client.get("/api/tracing/config").json()
    assert body["mode"] == "local"
    assert body["langsmith_url"].startswith("https://")


def test_thread_traces(client):
    body = client.get("/api/tracing/threads/7/traces").json()
    assert body["thread_id"] == 7
    assert len(body["traces"]) == 1
    assert body["traces"][0]["name"] == "orchestrator"
    assert client.get("/api/tracing/threads/999/traces").json()["traces"] == []


def test_trace_tree_truncates_previews(client):
    body = client.get("/api/tracing/traces/root").json()
    runs = body["runs"]
    assert [r["id"] for r in runs] == ["root", "tool1"]
    root = runs[0]
    assert root["inputs_truncated"] is True
    assert len(root["inputs_preview"]) <= 2000
    tool = runs[1]
    assert tool["inputs_truncated"] is False
    assert json.loads(tool["inputs_preview"]) == {"sym": "AAPL"}


def test_trace_tree_404(client):
    assert client.get("/api/tracing/traces/nope").status_code == 404


def test_run_detail_full_payload(client):
    body = client.get("/api/tracing/runs/root").json()
    assert len(json.loads(body["inputs"])["q"]) == 5000  # untruncated
    assert client.get("/api/tracing/runs/nope").status_code == 404
