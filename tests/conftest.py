"""Shared pytest fixtures.

The repo's existing tests bootstrap their own DB inline (see
`tests/test_agent_integration.py`). This conftest exposes the common
patterns as reusable fixtures so new tests can request `session` directly.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Run hermetically against the developer's `.env`. Settings is a dataclass whose
# field defaults read the repo-root `.env`, so on a configured machine every
# `Settings()` silently inherits real FEISHU_*/GATEWAY_*/OPEN_OTC_* values and
# any test asserting "this default is None/empty" fails — while the same test
# passes in CI and in a fresh worktree. Worse, `channel_registry.load_from_path`
# used to `load_dotenv(override=True)`, republishing `.env` over os.environ for
# every test that ran after it, including the pins below.
#
# Empty means "no dotenv at all" (see `app.config.dotenv_path`). Tests that need
# a dotenv point this at their own fixture file.
os.environ.setdefault("OPEN_OTC_ENV_FILE", "")

# Default the whole suite to no tracing: agent-driving tests must not write
# trace DBs into data/. Tracing tests opt in explicitly via monkeypatch.
os.environ.setdefault("OPEN_OTC_TRACING", "off")

# Default the whole suite to memory OFF: tests opt in explicitly.
os.environ.setdefault("OPEN_OTC_MEMORY", "off")

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
def registered_underlying(session):
    """Factory for a BOOKABLE underlying: an Instrument that is ACTIVE **and**
    tagged "underlying".

    Booking requires both halves — book_position, book_hedge and the
    confirmations review gate all refuse anything else — so a test that books
    must seed one. It commits because tools that open their own
    `database.SessionLocal()` cannot see uncommitted rows.
    """
    from app.models import Instrument

    def make(symbol: str = "AAPL", *, status: str = "active",
             tags: list[str] | None = None, **kw) -> "Instrument":
        row = Instrument(
            symbol=symbol, display_name=kw.pop("display_name", symbol),
            kind=kw.pop("kind", "stock"), status=status,
            tags=["underlying"] if tags is None else tags, **kw,
        )
        session.add(row)
        session.commit()
        return row

    return make


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
    """FastAPI TestClient with the test DB already configured by `session`.

    `create_app` calls `configure_settings`, which parks this test's Settings in
    a module-level override that `get_settings()` returns in preference to
    reading the environment. Left in place it outlives the test, so a later test
    that sets an env var and expects it to be read — `test_tracing_router`'s
    `monkeypatch.setenv("OPEN_OTC_TRACING", "local")` — silently keeps seeing
    this test's value instead. Individual files worked around it with their own
    `configure_settings(None)`; clearing it here fixes the class at the source.
    """
    from fastapi.testclient import TestClient

    from app.config import configure_settings
    from app.main import create_app

    app = create_app(settings=settings)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        configure_settings(None)


@pytest.fixture
def offline_session_factory(tmp_path):
    """Return a factory producing a fresh, clean, isolated DB per call.

    Shared by the arena determinism gate and the high-board workflow tests.
    """
    from contextlib import contextmanager

    from app import database
    from app.config import Settings

    counter = {"n": 0}

    @contextmanager
    def factory():
        counter["n"] += 1
        n = counter["n"]
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / f'det{n}.sqlite3'}",
            artifact_dir=tmp_path / f"art{n}",
            agent_checkpoint_db_path=":memory:",
        )
        database.configure_database(settings)
        database.init_db()
        with database.SessionLocal() as s:
            yield s

    return factory


@pytest.fixture
def block_network(monkeypatch):
    """Patch the AkShare fetch entrypoints to hard-fail, so any live market-data
    fetch on the golden path raises instead of leaking environment data."""
    def _raise(*_a, **_k):
        raise RuntimeError("network disabled in determinism gate")

    from app.services import backtest_market_history as hist
    monkeypatch.setattr(hist, "_fetch_akshare_spot", _raise)
    monkeypatch.setattr(hist, "_fetch_akshare_futures_contract", _raise)
