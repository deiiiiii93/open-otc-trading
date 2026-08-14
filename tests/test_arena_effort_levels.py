"""Per-model effort LISTS: the read seam and launch validation."""
from __future__ import annotations

import pytest
from app.services.arena.task import effort_levels_for, queue_arena_run


# ---- the read seam ----

def test_absent_model_runs_once_at_vendor_default():
    assert effort_levels_for({}, "gpt-5-5") == [None]
    assert effort_levels_for(None, "gpt-5-5") == [None]


def test_legacy_scalar_reads_as_a_one_element_list():
    """Boards launched before per-effort contestants stored {slug: "high"}."""
    assert effort_levels_for({"gpt-5-5": "high"}, "gpt-5-5") == ["high"]


def test_list_form_round_trips_including_the_unpinned_arm():
    assert effort_levels_for({"gpt-5-5": [None, "high"]}, "gpt-5-5") == [None, "high"]


def test_empty_list_means_vendor_default():
    assert effort_levels_for({"gpt-5-5": []}, "gpt-5-5") == [None]


# ---- launch validation ----

def test_duplicate_level_is_rejected(session):
    """Two identical levels would mint two contestants with one key — the second
    upserts over the first and the board reports a completed run with one arm."""
    with pytest.raises(ValueError, match="duplicate"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            reasoning_efforts={"gpt-5-5": ["high", "high"]},
        )


def test_each_level_is_validated_against_its_own_model(session):
    """'minimal' is rejected by every OpenAI model (measured)."""
    with pytest.raises(ValueError, match="not accepted"):
        queue_arena_run(
            session,
            workflow_ids=["risk-manager-control-day"],
            model_ids=["gpt-5-5"],
            reasoning_efforts={"gpt-5-5": ["low", "minimal"]},
        )


def test_unpinned_arm_is_always_legal(session):
    """A null arm bypasses the ladder check — every model can run unpinned,
    including the wire-protocol models whose client carries no effort at all."""
    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        reasoning_efforts={"gpt-5-5": [None, "high"]},
    )
    from app.services.arena import store
    assert store.get_run(session, run_id)["reasoning_efforts"] == {
        "gpt-5-5": [None, "high"]
    }


def test_all_default_map_entry_is_dropped(session):
    """[None] is exactly 'absent' — don't persist a map that says nothing."""
    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        reasoning_efforts={"gpt-5-5": [None]},
    )
    from app.services.arena import store
    assert store.get_run(session, run_id)["reasoning_efforts"] == {}


def test_progress_total_counts_arms_not_models(session):
    """Two arms on one model is two units of work, not one."""
    _run_id, task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5"],
        trials=2,
        reasoning_efforts={"gpt-5-5": [None, "high"]},
    )
    assert task.progress_total == 1 * 2 * 2      # workflows × arms × trials
