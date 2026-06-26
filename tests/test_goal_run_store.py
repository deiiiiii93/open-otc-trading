"""Thread-scoped goal-run store (spec §H): at most one active goal per thread,
and the service-layer activation gate that decides whether to attach the grader."""
import pytest

from app.services.deep_agent.goal_mode import (
    GoalStateError,
    GoalRunStore,
    mark_goal_satisfied,
    new_goal_run,
    ratify_goal_run,
)


def _awaiting():
    return new_goal_run(goal_run_id="g1", contract_hash=None, mode="yolo")


def test_start_sets_the_active_run_but_grader_not_yet_attached():
    store = GoalRunStore({})
    store.start("t1", _awaiting())
    assert store.active("t1").goal_run_id == "g1"
    assert store.grader_should_attach("t1") is False  # awaiting, not running


def test_grader_attaches_once_running():
    store = GoalRunStore({})
    store.start("t1", _awaiting())
    store.update("t1", lambda s: ratify_goal_run(s, contract_hash="h"))
    assert store.active("t1").status == "running"
    assert store.grader_should_attach("t1") is True


def test_satisfied_run_releases_the_pointer_and_detaches_grader():
    store = GoalRunStore({})
    store.start("t1", _awaiting())
    store.update("t1", lambda s: ratify_goal_run(s, contract_hash="h"))
    store.update("t1", mark_goal_satisfied)
    assert store.active("t1") is None              # pointer cleared on terminal
    assert store.grader_should_attach("t1") is False


def test_only_one_active_goal_per_thread():
    store = GoalRunStore({})
    store.start("t1", _awaiting())
    with pytest.raises(GoalStateError):
        store.start("t1", new_goal_run(goal_run_id="g2", contract_hash=None, mode="yolo"))


def test_threads_are_isolated():
    store = GoalRunStore({})
    store.start("t1", _awaiting())
    assert store.active("t2") is None
    assert store.grader_should_attach("t2") is False
