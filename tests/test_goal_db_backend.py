"""DB-backed persistence for goal runs (slice 5 surface).

``ThreadColumnBackend`` maps ``str(thread_id) -> a JSON column on AgentThread``,
the dict-shaped backend ``GoalRunStore``/``GoalRunService`` expect. Each op opens
its own short transaction; the store's in-process lock serialises check-then-write.
"""
import sqlalchemy as sa
from sqlalchemy import inspect

from app import database
from app.services.deep_agent.goal_persistence import ThreadColumnBackend


def test_incremental_repair_adds_goal_columns(tmp_path):
    """A pre-goal-mode agent_threads table gets goal_run/goal_contract added by the
    boot-time incremental repair, so live DBs that never run Alembic still work."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'old.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE agent_threads ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title VARCHAR(200),"
            " character VARCHAR(40))"
        ))
    database._ensure_incremental_schema(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("agent_threads")}
    assert "goal_run" in cols
    assert "goal_contract" in cols


def _backend(column):
    return ThreadColumnBackend(database.SessionLocal, column)


def test_get_missing_thread_returns_default(session):
    b = _backend("goal_run")
    assert b.get("999") is None
    assert b.get("999", "fallback") == "fallback"


def test_set_then_get_round_trips(session, agent_thread_factory):
    thread = agent_thread_factory()
    session.commit()
    b = _backend("goal_run")
    b[str(thread.id)] = {"status": "running"}
    assert b.get(str(thread.id)) == {"status": "running"}


def test_pop_clears_and_returns(session, agent_thread_factory):
    thread = agent_thread_factory()
    session.commit()
    b = _backend("goal_contract")
    b[str(thread.id)] = {"schema_version": "goal_contract.v1"}
    assert b.pop(str(thread.id)) == {"schema_version": "goal_contract.v1"}
    assert b.get(str(thread.id)) is None
    assert b.pop(str(thread.id), "gone") == "gone"


def test_set_on_missing_thread_raises_keyerror(session):
    b = _backend("goal_run")
    try:
        b["12345"] = {"x": 1}
    except KeyError:
        return
    raise AssertionError("expected KeyError for a non-existent thread")


def test_non_numeric_thread_id_reads_as_missing(session):
    """A non-integer path segment (e.g. /threads/not-a-number/goal) must read as a
    missing thread — controlled null / default / KeyError — never an uncaught 500."""
    b = _backend("goal_run")
    assert b.get("not-a-number") is None
    assert b.get("not-a-number", "fallback") == "fallback"
    assert b.pop("not-a-number", "gone") == "gone"
    try:
        b["not-a-number"] = {"x": 1}
    except KeyError:
        return
    raise AssertionError("expected KeyError for a non-numeric thread id")


def test_two_columns_are_independent(session, agent_thread_factory):
    thread = agent_thread_factory()
    session.commit()
    runs = _backend("goal_run")
    contracts = _backend("goal_contract")
    runs[str(thread.id)] = {"r": 1}
    contracts[str(thread.id)] = {"c": 2}
    assert runs.get(str(thread.id)) == {"r": 1}
    assert contracts.get(str(thread.id)) == {"c": 2}
