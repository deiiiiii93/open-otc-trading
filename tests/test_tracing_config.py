"""Tracing settings + mode resolution + callback composition."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.tracing.config import (
    TracingMode,
    resolve_tracing_mode,
    tracing_callbacks,
)
from app.services.tracing.store import reset_trace_store_cache
from app.services.tracing.tracer import LocalTracer


def test_settings_default_tracing_mode_is_off_under_pytest(monkeypatch):
    # tests/conftest.py pins OPEN_OTC_TRACING=off for the whole suite so
    # agent-driving tests don't write trace DBs. Verify the pin works.
    settings = Settings()
    assert settings.tracing_mode == "off"


def test_settings_tracing_mode_reads_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_TRACING", "both")
    settings = Settings()
    assert settings.tracing_mode == "both"


def test_settings_trace_db_path_default_and_env(monkeypatch):
    settings = Settings()
    assert settings.trace_db_path == "./data/agent_traces.sqlite3"
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", "/tmp/x/traces.sqlite3")
    assert Settings().trace_db_path == "/tmp/x/traces.sqlite3"


@pytest.fixture(autouse=True)
def _fresh_store_cache():
    reset_trace_store_cache()
    yield
    reset_trace_store_cache()


def _settings(monkeypatch, tmp_path, mode: str):
    monkeypatch.setenv("OPEN_OTC_TRACING", mode)
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", str(tmp_path / "t.sqlite3"))
    return Settings()


@pytest.mark.parametrize("raw,expected", [
    ("local", TracingMode.LOCAL),
    ("langsmith", TracingMode.LANGSMITH),
    ("both", TracingMode.BOTH),
    ("off", TracingMode.OFF),
    ("LOCAL", TracingMode.LOCAL),       # case-insensitive
    ("nonsense", TracingMode.LOCAL),    # unknown -> local with warning
])
def test_resolve_tracing_mode(monkeypatch, tmp_path, raw, expected):
    assert resolve_tracing_mode(_settings(monkeypatch, tmp_path, raw)) is expected


def test_callbacks_off_is_empty(monkeypatch, tmp_path):
    assert tracing_callbacks(_settings(monkeypatch, tmp_path, "off")) == []


def test_callbacks_local_carries_trace_meta(monkeypatch, tmp_path):
    handlers = tracing_callbacks(
        _settings(monkeypatch, tmp_path, "local"), thread_id=7, workflow_id=3
    )
    assert len(handlers) == 1
    tracer = handlers[0]
    assert isinstance(tracer, LocalTracer)
    assert tracer._thread_id == 7
    assert tracer._workflow_id == 3


def test_callbacks_both_includes_langsmith(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    handlers = tracing_callbacks(_settings(monkeypatch, tmp_path, "both"))
    kinds = [type(h).__name__ for h in handlers]
    assert "LocalTracer" in kinds
    assert "LangChainTracer" in kinds


def test_each_call_returns_fresh_tracer(monkeypatch, tmp_path):
    # BaseTracer keeps per-run state; concurrent runs need fresh instances
    # sharing one store (one writer thread per DB file).
    settings = _settings(monkeypatch, tmp_path, "local")
    a = tracing_callbacks(settings)[0]
    b = tracing_callbacks(settings)[0]
    assert a is not b
    assert a._store is b._store
