"""Goal run lifecycle & activation gate (spec §H/§F).

The activation gate is the P1 the review loop surfaced: the rubric (and thus the
grader) must be active ONLY while the run is `running`, else a finished goal would
re-grade every subsequent ordinary turn on the thread.
"""
import pytest

from app.services.deep_agent.goal_mode import (
    GoalStateError,
    cancel_goal_run,
    escalate_goal_run,
    mark_goal_satisfied,
    new_goal_run,
    pointer_held,
    ratify_goal_run,
    resume_goal_run,
    rubric_active,
)


def _fresh():
    return new_goal_run(goal_run_id="g1", contract_hash="abc123", mode="yolo")


def test_rubric_active_only_while_running():
    state = _fresh()
    assert state.status == "awaiting_ratification"
    assert rubric_active(state) is False          # not yet running

    state = ratify_goal_run(state)
    assert state.status == "running"
    assert rubric_active(state) is True           # grader attaches here

    state = mark_goal_satisfied(state)
    assert state.status == "satisfied"
    assert rubric_active(state) is False          # finished -> never re-grade


def test_pointer_held_through_running_and_stuck_cleared_on_terminal():
    state = ratify_goal_run(_fresh())
    assert pointer_held(state) is True            # running

    stuck = escalate_goal_run(state, terminal_reason="max_iterations_reached")
    assert stuck.status == "stuck_needs_human"
    assert stuck.terminal_reason == "max_iterations_reached"
    assert pointer_held(stuck) is True            # awaits resume/cancel

    done = mark_goal_satisfied(resume_goal_run(stuck))
    assert pointer_held(done) is False            # cleared on satisfied

    cancelled = cancel_goal_run(ratify_goal_run(_fresh()))
    assert cancelled.status == "cancelled"
    assert pointer_held(cancelled) is False


def test_ratify_freezes_the_contract_hash():
    """spec §H: the frozen contract_hash is established at ratification, binding
    the running run to the accepted contract/rubric."""
    state = new_goal_run(goal_run_id="g1", contract_hash=None, mode="yolo")
    assert state.contract_hash is None
    running = ratify_goal_run(state, contract_hash="deadbeef")
    assert running.status == "running"
    assert running.contract_hash == "deadbeef"


def test_ratify_requires_a_contract_hash():
    """A run cannot reach `running` without a freeze identity."""
    unfrozen = new_goal_run(goal_run_id="g1", contract_hash=None, mode="yolo")
    with pytest.raises(GoalStateError):
        ratify_goal_run(unfrozen)


def test_invalid_transitions_raise():
    fresh = _fresh()
    with pytest.raises(GoalStateError):
        mark_goal_satisfied(fresh)                # can't satisfy before running
    with pytest.raises(GoalStateError):
        ratify_goal_run(ratify_goal_run(fresh))   # can't ratify a running run
