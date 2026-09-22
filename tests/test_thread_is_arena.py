"""D14: one arena lookup, three answers; each caller picks its fail direction."""
from __future__ import annotations

import pytest

from app.models import AgentThread
from app.services.thread_access import thread_is_arena


@pytest.fixture
def threads(session):
    desk = AgentThread(title="desk", character="trader", source="desk")
    arena = AgentThread(title="[arena] risk-limit-breach-day · m", character="risk_manager",
                        source="arena", arena_run_id=1)
    session.add_all([desk, arena])
    session.commit()
    return desk.id, arena.id


def test_known_sources_answer_true_or_false(session, threads):
    desk_id, arena_id = threads
    assert thread_is_arena(session, arena_id) is True
    assert thread_is_arena(session, desk_id) is False


def test_no_thread_id_is_a_desk_action_not_unknown(session):
    assert thread_is_arena(session, None) is False


def test_a_missing_row_cannot_be_told(session):
    assert thread_is_arena(session, 999_999) is None


def test_a_failing_lookup_cannot_be_told():
    class Broken:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("db gone")

    assert thread_is_arena(Broken(), 5) is None


def test_memory_queue_stays_fail_open(session):
    """Extraction proceeds on None: the pre-existing behaviour of _is_arena_thread."""
    from app.services.deep_agent.memory.queue import MemoryWriteQueue

    assert MemoryWriteQueue._is_arena_thread(session, 999_999) is False
