# tests/test_memory_runs.py
import pytest
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.runs import (
    ExtractionRunStore, RunSpec, session_run_key, correction_run_key,
)


@pytest.fixture
def runs():
    return ExtractionRunStore(MemoryConfig())


def _spec(sid=1):
    return RunSpec(run_key=session_run_key(sid), kind="session", session_id=sid,
                   thread_id=10, persona="trader", book_scope_id=None,
                   trigger_message_id=None)


def test_run_keys():
    assert session_run_key(5) == "session:5"
    assert correction_run_key(5, 9) == "corr:5:9"


def test_enqueue_inserts_pending(session, runs):
    assert runs.enqueue_run(session, _spec()) is True
    assert runs.get(session, "session:1").status == "pending"


def test_succeeded_is_noop(session, runs):
    runs.enqueue_run(session, _spec())
    runs.mark_succeeded(session, "session:1", 42)
    assert runs.enqueue_run(session, _spec()) is False
    assert runs.get(session, "session:1").last_extracted_message_id == 42


def test_failed_under_max_reenqueues(session, runs):
    runs.enqueue_run(session, _spec())
    runs.mark_failed(session, "session:1", "boom")
    assert runs.enqueue_run(session, _spec()) is True
    assert runs.get(session, "session:1").status == "pending"


def test_failed_at_max_is_terminal(session, runs):
    runs.enqueue_run(session, _spec())
    for _ in range(3):
        runs.get(session, "session:1").status = "pending"
        runs.mark_failed(session, "session:1", "boom")
    assert runs.get(session, "session:1").attempts == 3
    assert runs.enqueue_run(session, _spec()) is False


def test_eligible_runs(session, runs):
    runs.enqueue_run(session, _spec(1))
    runs.enqueue_run(session, _spec(2))
    runs.mark_succeeded(session, "session:2", 5)
    assert {r.run_key for r in runs.eligible_runs(session)} == {"session:1"}
