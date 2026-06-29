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
    # already pending → still returns True (idempotent re-enqueue)
    assert runs.enqueue_run(session, _spec()) is True


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
    runs.mark_failed(session, "session:1", "boom")       # attempts=1
    assert runs.enqueue_run(session, _spec()) is True    # iter 1 reset
    runs.mark_failed(session, "session:1", "boom")       # attempts=2
    assert runs.enqueue_run(session, _spec()) is True    # iter 2 reset
    runs.mark_failed(session, "session:1", "boom")       # attempts=3 → terminal
    assert runs.enqueue_run(session, _spec()) is False   # terminal: no more resets
    assert runs.get(session, "session:1").attempts == 3


def test_eligible_runs(session, runs):
    # spec 1: pending — must appear
    runs.enqueue_run(session, _spec(1))

    # spec 2: failed-below-max — must appear (proves filter is not just status=="pending")
    runs.enqueue_run(session, _spec(2))
    runs.mark_failed(session, "session:2", "transient")   # attempts=1 < 3

    # spec 3: succeeded — must NOT appear
    runs.enqueue_run(session, _spec(3))
    runs.mark_succeeded(session, "session:3", 5)

    # spec 4: terminal-failed (attempts==max) — must NOT appear
    runs.enqueue_run(session, _spec(4))
    runs.mark_failed(session, "session:4", "boom")        # attempts=1
    runs.enqueue_run(session, _spec(4))                   # reset
    runs.mark_failed(session, "session:4", "boom")        # attempts=2
    runs.enqueue_run(session, _spec(4))                   # reset
    runs.mark_failed(session, "session:4", "boom")        # attempts=3 → terminal
    assert runs.enqueue_run(session, _spec(4)) is False   # confirm terminal

    eligible_keys = {r.run_key for r in runs.eligible_runs(session)}
    assert eligible_keys == {"session:1", "session:2"}


def test_mark_succeeded_null_cursor_guard(session, runs):
    """mark_succeeded with None must not overwrite an existing cursor."""
    runs.enqueue_run(session, _spec())
    runs.mark_succeeded(session, "session:1", 42)
    runs.mark_succeeded(session, "session:1", None)       # should be a no-op for cursor
    row = runs.get(session, "session:1")
    assert row.last_extracted_message_id == 42
    assert row.status == "succeeded"
