"""Shared pytest fixtures.

The repo's existing tests bootstrap their own DB inline (see
`tests/test_agent_integration.py`). This conftest exposes the common
patterns as reusable fixtures so new tests can request `session` directly.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Default the whole suite to no tracing: agent-driving tests must not write
# trace DBs into data/. Tracing tests opt in explicitly via monkeypatch.
os.environ.setdefault("OPEN_OTC_TRACING", "off")

from app import database
from app.config import Settings


# Files that exercise the capability gate directly. Outside these, tests
# call @tool wrappers without a RunnableConfig, so the gate fails closed
# at pet_page and blocks every domain_write tool. Auto-bypass the gate
# (resolve to desk_workflow) so existing tests keep targeting the service
# layer rather than the gate. Gate behaviour is covered by the files in
# this set.
_GATE_TEST_FILES = frozenset({
    "test_capability_gate.py",
    "test_capability_assignments.py",
    "test_envelopes.py",
    "test_cost_preview.py",
    # Exercises the REAL gate end-to-end (subagent denial -> envelope escalation);
    # bypassing the gate would make the denial — and the whole test — vanish.
    "test_envelope_escalation_integration.py",
})


@pytest.fixture(autouse=True)
def _bypass_capability_gate(request, monkeypatch):
    test_file = Path(request.node.fspath).name
    if test_file in _GATE_TEST_FILES:
        return
    from app.services.deep_agent import capability_gate as _cg
    from app.services.deep_agent.envelopes import Envelope as _Env

    monkeypatch.setattr(_cg, "_envelope_from_config", lambda _config: _Env.DESK_WORKFLOW)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )


@pytest.fixture
def session(settings: Settings):
    """Configure the DB for this test and yield a session bound to it."""
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        yield session


@pytest.fixture
def agent_thread_factory(session):
    """Factory returning new AgentThread rows on the test session."""
    from app.models import AgentThread

    counter = {"n": 0}

    def make(title: str | None = None, character: str = "auto"):
        counter["n"] += 1
        thread = AgentThread(
            title=title or f"thread-{counter['n']}",
            character=character,
        )
        session.add(thread)
        session.flush()
        return thread

    return make


@pytest.fixture
def client(session, settings):
    """FastAPI TestClient with the test DB already configured by `session`."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c
