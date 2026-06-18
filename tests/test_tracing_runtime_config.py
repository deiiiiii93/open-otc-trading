"""graph_run_config attaches tracing callbacks + metadata; end-to-end via a runnable."""
from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda

from app.config import Settings
from app.services.tracing.store import get_trace_store, reset_trace_store_cache
from app.services.tracing.tracer import LocalTracer
from app.services.deep_agent.runtime_config import graph_run_config


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_trace_store_cache()
    yield
    reset_trace_store_cache()


def _settings(monkeypatch, tmp_path, mode="local"):
    monkeypatch.setenv("OPEN_OTC_TRACING", mode)
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", str(tmp_path / "t.sqlite3"))
    return Settings()


def test_off_mode_keeps_config_shape(monkeypatch, tmp_path):
    config = graph_run_config(_settings(monkeypatch, tmp_path, "off"), thread_id=1)
    assert "callbacks" not in config
    assert config["configurable"]["thread_id"] == "1"
    assert "recursion_limit" in config


def test_local_mode_attaches_callbacks_and_metadata(monkeypatch, tmp_path):
    config = graph_run_config(
        _settings(monkeypatch, tmp_path),
        thread_id="wf:1:orchestrator",          # checkpointer key, NOT AgentThread id
        trace_meta={"thread_id": 7, "workflow_id": 3},
    )
    assert isinstance(config["callbacks"][0], LocalTracer)
    assert config["metadata"] == {"thread_id": 7, "workflow_id": 3}
    # configurable untouched by trace_meta
    assert config["configurable"]["thread_id"] == "wf:1:orchestrator"


def test_end_to_end_runnable_lands_in_store(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    config = graph_run_config(settings, thread_id=1, trace_meta={"thread_id": 7})
    chain = (RunnableLambda(lambda x: x + 1) | RunnableLambda(lambda x: x * 2)).with_config(
        {"run_name": "audit-me"}
    )
    # graph_run_config carries recursion_limit etc. — pass through unchanged
    assert chain.invoke(1, config=config) == 4

    store = get_trace_store(settings)
    store.flush()
    traces = store.list_thread_traces(7)
    assert len(traces) == 1
    root = traces[0]
    assert root["name"] == "audit-me"
    assert root["status"] == "success"
    runs = store.get_trace(root["trace_id"])
    assert len(runs) >= 3  # sequence root + two lambda children
    assert all(r["thread_id"] == 7 for r in runs)
