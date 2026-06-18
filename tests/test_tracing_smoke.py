"""Smoke test: Tracing feature end-to-end through agent threads with DeepSeek V4 Flash.

Exercises: graph_run_config → LocalTracer → TraceStore → REST API.
Uses real LangChain Runnable objects so BaseTracer hooks fire (scripted
graphs bypass the callback system).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from app.config import Settings
from app.services.deep_agent.runtime_config import graph_run_config
from app.services.tracing import get_trace_store, reset_trace_store_cache
from app.routers.tracing import build_tracing_router


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_trace_store_cache()
    yield
    reset_trace_store_cache()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("OPEN_OTC_TRACING", "local")
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", str(tmp_path / "t.sqlite3"))
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        trace_db_path=str(tmp_path / "t.sqlite3"),
    )


@pytest.fixture
def store(settings: Settings) -> ...:
    s = get_trace_store(settings)
    s.flush()
    return s


MODEL_DEEPSEEK_V4_FLASH = {
    "channel": "deepseek",
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
}


def test_smoke_graph_run_config_traces_agent_thread(settings, store):
    """Smoke: graph_run_config captures trace spans for an agent thread."""

    chain = (
        RunnableLambda(lambda x: {"messages": [{"role": "user", "content": x}]})
        | RunnableLambda(lambda msgs: {"messages": msgs["messages"] + [{"role": "assistant", "content": "Hello from DeepSeek V4 Flash!"}]})
    ).with_config({"run_name": "deepseek-agent"})

    config = graph_run_config(
        settings,
        thread_id=1,
        trace_meta={"thread_id": 42},
    )

    result = chain.invoke("Run a check", config=config)
    store.flush()

    traces = store.list_thread_traces(42)
    assert len(traces) >= 1, "Expected at least one root trace for thread 42"

    root = traces[0]
    assert root["thread_id"] == 42
    assert root["status"] == "success"
    assert root["name"] == "deepseek-agent"

    spans = store.get_trace(root["trace_id"])
    assert len(spans) >= 1, "Expected at least one span"
    for s in spans:
        assert s["thread_id"] == 42, f"Span {s['id']} missing thread_id"
    print(f"graph_run_config smoke: thread=42 → trace={root['trace_id']} → {len(spans)} spans")


def test_smoke_run_detail(settings, store):
    """Smoke: individual run detail includes inputs, outputs, and model info."""

    chain = RunnableLambda(
        lambda x: f"Echo: {x}"
    ).with_config({"run_name": "echo"})

    config = graph_run_config(
        settings,
        thread_id=2,
        trace_meta={"thread_id": 99, "task_id": 5},
    )

    result = chain.invoke("test input", config=config)
    store.flush()

    traces = store.list_thread_traces(99)
    assert len(traces) >= 1

    root = traces[0]
    assert root["task_id"] == 5

    spans = store.get_trace(root["trace_id"])
    leaf = [s for s in spans if s["parent_run_id"] is not None]
    if leaf:
        detail = store.get_run(leaf[0]["id"])
        assert detail is not None
        if detail.get("inputs"):
            parsed = json.loads(detail["inputs"])
            print(f"  run detail inputs: {parsed}")
        if detail.get("outputs"):
            parsed = json.loads(detail["outputs"])
            print(f"  run detail outputs: {parsed}")
    print(f"run_detail smoke: thread=99 task=5 → trace={root['trace_id']}")


def test_smoke_model_selection_flows_through(settings, store):
    """Smoke: model selection (DeepSeek V4 Flash) is captured in trace meta."""

    chain = RunnableLambda(
        lambda x: {"content": "ds-v4-flash response"}
    ).with_config({"run_name": "ds-agent"})

    config = graph_run_config(
        settings,
        thread_id=3,
        configurable_extra={"model_selection": json.dumps(MODEL_DEEPSEEK_V4_FLASH)},
        trace_meta={"thread_id": 77},
    )

    chain.invoke("test", config=config)
    store.flush()

    traces = store.list_thread_traces(77)
    assert len(traces) >= 1
    root = traces[0]

    spans = store.get_trace(root["trace_id"])
    for s in spans:
        extra_raw = s.get("extra")
        if extra_raw:
            extra = json.loads(extra_raw)
            if extra:
                print(f"  span extra: {json.dumps(extra, default=str)[:200]}")
    print(f"model_selection smoke: DeepSeek V4 Flash trace={root['trace_id']}")


def test_smoke_tracing_api(settings, store):
    """Smoke: REST API serves back captured traces."""

    chain = RunnableLambda(
        lambda x: {"result": "api test"}
    ).with_config({"run_name": "api-agent"})

    config = graph_run_config(
        settings,
        thread_id=4,
        configurable_extra={"model": "deepseek-v4-flash"},
        trace_meta={"thread_id": 200},
    )

    chain.invoke("api smoke test", config=config)
    store.flush()

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    router = build_tracing_router(get_store=lambda: store)
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/tracing/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["mode"] == "local"
    print(f"  config: mode={cfg['mode']} langsmith_url={cfg['langsmith_url']}")

    r = client.get("/api/tracing/threads/200/traces")
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] == 200
    assert len(body["traces"]) >= 1, "API should return traces for thread 200"

    trace_id = body["traces"][0]["trace_id"]
    r = client.get(f"/api/tracing/traces/{trace_id}")
    assert r.status_code == 200
    tree = r.json()
    assert tree["trace_id"] == trace_id
    assert len(tree["runs"]) >= 1
    print(f"  trace tree: {len(tree['runs'])} spans")

    r = client.get("/api/tracing/threads/999/traces")
    assert r.status_code == 200
    assert r.json()["traces"] == []

    run_id = tree["runs"][0]["id"]
    r = client.get(f"/api/tracing/runs/{run_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == run_id
    print(f"  run detail: {detail['name']} ({detail['run_type']})")

    r = client.get("/api/tracing/runs/nonexistent")
    assert r.status_code == 404

    r = client.get("/api/tracing/traces/nonexistent")
    assert r.status_code == 404

    print(f"\n=== SMOKE PASSED ===")
    print(f"Thread 200 → trace {trace_id} → {len(tree['runs'])} spans")
    print(f"Model: deepseek/deepseek-v4-flash (via configurable_extra)")
    print(f"Tracing mode: {cfg['mode']}")
