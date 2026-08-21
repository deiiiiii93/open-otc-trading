"""Output-budget arms: launch validation, the cross product, and the carrier.

Budget is a REGIME, not a detail — runs #118 (4096) and #119 (32768) produced an
artifact in 0/8 and 7/8 trials on the budget alone — so it is part of the
contestant key and must behave exactly like reasoning_effort everywhere.
"""
from __future__ import annotations

import pytest

from app.services.arena.task import arms_for, budget_arms_for


# ---------------------------------------------------------------------------
# budget_arms_for / arms_for
# ---------------------------------------------------------------------------

def test_absent_model_runs_one_arm_at_the_process_default():
    assert budget_arms_for(None, "m") == [None]
    assert budget_arms_for({}, "m") == [None]
    assert budget_arms_for({"other": [4096]}, "m") == [None]


def test_explicit_null_is_the_unpinned_arm():
    """So a board can rank "as we have always run it" against a pin."""
    assert budget_arms_for({"m": [None, 32768]}, "m") == [None, 32768]


def test_scalar_is_read_as_a_one_element_list():
    """Derive on read, never migrate the JSON column."""
    assert budget_arms_for({"m": 32768}, "m") == [32768]


def test_arms_are_the_cross_product_of_both_axes():
    """A model at two efforts and two budgets is FOUR contestants.

    The execution loop, the progress total and the launch-time count all read
    this one definition, so a run cannot execute a different number of
    contestants than it counted — which would leave the progress bar short of
    its total forever, reading as stuck.
    """
    got = arms_for({"m": ["low", "high"]}, {"m": [4096, 32768]}, "m")
    assert got == [
        ("low", 4096), ("low", 32768),
        ("high", 4096), ("high", 32768),
    ]


def test_unpinned_model_yields_exactly_one_default_arm():
    assert arms_for(None, None, "m") == [(None, None)]


# ---------------------------------------------------------------------------
# Launch-time validation (queue_arena_run)
# ---------------------------------------------------------------------------

def test_duplicate_budget_is_rejected_at_launch(session):
    """Two arms sharing one contestant key: the second would upsert over the
    first and the run would report completed with an arm silently missing."""
    from app.services.arena.task import queue_arena_run
    with pytest.raises(ValueError, match="duplicate budget"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            max_output_tokens={"gpt-5-5": [32768, 32768]},
        )


def test_budget_for_a_model_not_in_the_run_is_rejected(session):
    """Otherwise the board is silently left unpinned — the map names a model
    that never runs, and nothing says so."""
    from app.services.arena.task import queue_arena_run
    with pytest.raises(ValueError, match="not one of this run's models"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            max_output_tokens={"claude-opus-4-8": [32768]},
        )


@pytest.mark.parametrize("bad", [0, -1, "abc"])
def test_invalid_budget_is_refused_not_coerced(session, bad):
    """Refuse rather than clamp: a value silently coerced into something else
    would report a regime that never ran. 0 is also the DB sentinel for
    "unpinned", so accepting it would make a pin indistinguishable from none."""
    from app.services.arena.task import queue_arena_run
    with pytest.raises(ValueError):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            max_output_tokens={"gpt-5-5": [bad]},
        )


def test_all_default_arms_persist_as_absent(session):
    """[None] is exactly "absent" — persisting it would claim a pin that is not
    one, and read back identically anyway."""
    from app.services.arena.task import queue_arena_run
    from app.services.arena import store
    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        max_output_tokens={"gpt-5-5": [None]},
    )
    session.flush()
    assert store.get_run(session, run_id)["max_output_tokens"] == {}


# ---------------------------------------------------------------------------
# The carrier: the model-selection dict
# ---------------------------------------------------------------------------

def test_selection_omits_the_budget_when_unset():
    """Omitted-when-unset is load-bearing, not tidiness: the resolved selection
    is compared by EQUALITY against AgentService.default_model_selection to
    decide whether the prebuilt orchestrator can be reused, so a key present on
    every turn — even as None — silently ends that reuse."""
    from app.services.arena.models import arena_model_to_selection, get_model
    sel = arena_model_to_selection(get_model("gpt-5-5"))
    assert "max_output_tokens" not in sel
    assert set(sel) == {"channel", "provider", "model"}


def test_selection_carries_a_pinned_budget():
    from app.services.arena.models import arena_model_to_selection, get_model
    sel = arena_model_to_selection(get_model("gpt-5-5"), None, 32768)
    assert sel["max_output_tokens"] == 32768
