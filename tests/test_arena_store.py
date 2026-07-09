"""Tests for backend/app/services/arena/store.py."""
from __future__ import annotations

import pytest
from app.services.arena import store


# ---- helpers ----

def _make_run(session, workflow_ids=None, model_ids=None, weights=None):
    return store.create_run(
        session,
        workflow_ids=workflow_ids or ["wf-a", "wf-b"],
        model_ids=model_ids or ["model-x", "model-y"],
        weights=weights,
    )


def _make_match(session, run_id, *, workflow_id="wf-a", model_id="model-x",
                status="scored", total_score=80.0, objective_score=70.0,
                judged_score=90.0, judge_missing=False, config=None, error=None):
    return store.record_match(
        session,
        run_id,
        workflow_id,
        model_id,
        objective_score=objective_score,
        judged_score=judged_score,
        total_score=total_score,
        judge_missing=judge_missing,
        config=config or {},
        transcript_path=None,
        status=status,
        error=error,
    )


# ---- round-trip tests ----

def test_create_run_returns_id(session):
    rid = _make_run(session)
    assert isinstance(rid, int)
    assert rid > 0


def test_get_run_returns_dict_with_matches(session):
    rid = _make_run(session)
    _make_match(session, rid, workflow_id="wf-a", model_id="model-x")
    _make_match(session, rid, workflow_id="wf-a", model_id="model-y")

    result = store.get_run(session, rid)
    assert result is not None
    assert result["id"] == rid
    assert len(result["matches"]) == 2


def test_score_breakdown_round_trips_through_match_dict(session):
    rid = _make_run(session)
    breakdown = {
        "passed": 1, "total": 2,
        "objective": {"steps": [{"index": 0, "user": "u",
                                  "checks": [{"kind": "skill", "label": "skill: x",
                                              "passed": True, "detail": ""}]}],
                      "success": []},
        "judge": {"rubric_scores": [{"point": "p1", "score": 80}], "judged_score": 80.0},
    }
    store.record_match(
        session, rid, "wf-a", "model-x",
        objective_score=50.0, judged_score=80.0, total_score=65.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
        score_breakdown=breakdown,
    )
    result = store.get_run(session, rid)
    got = result["matches"][0]["score_breakdown"]
    # Derive-on-read adds an ability card; here axes are absent so it is null with
    # a reason. The originally-stored content round-trips unchanged.
    assert {k: got[k] for k in breakdown} == breakdown
    assert got["card"] is None and got["card_reason"] == "legacy_no_axes"


def test_get_run_returns_none_for_missing(session):
    assert store.get_run(session, 99999) is None


def test_record_match_returns_id(session):
    rid = _make_run(session)
    mid = _make_match(session, rid)
    assert isinstance(mid, int)
    assert mid > 0


def test_record_match_upsert(session):
    rid = _make_run(session)
    mid1 = _make_match(session, rid, workflow_id="wf-a", model_id="model-x", total_score=50.0)
    mid2 = store.record_match(
        session, rid, "wf-a", "model-x",
        objective_score=60.0, judged_score=70.0, total_score=65.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
    )
    assert mid1 == mid2  # same row updated
    result = store.get_run(session, rid)
    assert result["matches"][0]["total_score"] == 65.0


def test_set_run_status(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "running")
    run = store.get_run(session, rid)
    assert run["status"] == "running"


def test_set_run_status_with_error(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "failed", error="boom")
    run = store.get_run(session, rid)
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_match_transcript_path(session):
    rid = _make_run(session)
    mid = store.record_match(
        session, rid, "wf-a", "model-x",
        objective_score=50.0, judged_score=60.0, total_score=55.0,
        judge_missing=False, config={}, transcript_path="/some/path.json",
        status="scored",
    )
    assert store.get_match_transcript_path(session, mid) == "/some/path.json"


def test_get_match_transcript_path_none(session):
    assert store.get_match_transcript_path(session, 99999) is None


# ---- list_runs tests ----

def test_list_runs_ordering_and_count(session):
    rid1 = _make_run(session, workflow_ids=["wf-1"])
    rid2 = _make_run(session, workflow_ids=["wf-2"])
    rid3 = _make_run(session, workflow_ids=["wf-3"])

    rows, total = store.list_runs(session)
    assert total == 3
    # Most recent first
    assert rows[0]["id"] == rid3
    assert rows[1]["id"] == rid2
    assert rows[2]["id"] == rid1


def test_list_runs_pagination(session):
    for i in range(5):
        _make_run(session, workflow_ids=[f"wf-{i}"])
    rows, total = store.list_runs(session, limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2

    rows2, _ = store.list_runs(session, limit=2, offset=2)
    assert len(rows2) == 2
    # ids don't overlap
    ids1 = {r["id"] for r in rows}
    ids2 = {r["id"] for r in rows2}
    assert not ids1 & ids2


# ---- leaderboard tests ----

def test_leaderboard_returns_empty_when_no_completed_run(session):
    rid = _make_run(session)
    _make_match(session, rid, status="scored", total_score=80.0)
    # run is still 'queued', not 'completed'
    result = store.leaderboard(session)
    assert result == []


def test_leaderboard_averages_scores_per_model(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")

    _make_match(session, rid, workflow_id="wf-a", model_id="model-x",
                total_score=80.0, objective_score=70.0, status="scored")
    _make_match(session, rid, workflow_id="wf-b", model_id="model-x",
                total_score=60.0, objective_score=50.0, status="scored")
    _make_match(session, rid, workflow_id="wf-a", model_id="model-y",
                total_score=90.0, objective_score=85.0, status="scored")

    rows = store.leaderboard(session)
    # model-y should be first (mean 90 > 70)
    assert rows[0]["model_id"] == "model-y"
    assert rows[0]["mean_objective"] == 85.0
    assert rows[1]["model_id"] == "model-x"
    # model-x: mean of 80 + 60 = 70
    assert rows[1]["mean_objective"] == 60.0
    assert rows[1]["match_count"] == 2


def test_leaderboard_excludes_failed_matches(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")

    _make_match(session, rid, workflow_id="wf-a", model_id="model-x",
                total_score=80.0, objective_score=70.0, status="scored")
    _make_match(session, rid, workflow_id="wf-b", model_id="model-x",
                total_score=10.0, objective_score=5.0, status="failed", error="timeout")

    rows = store.leaderboard(session)
    assert len(rows) == 1
    # Only the scored match counts
    assert rows[0]["mean_objective"] == 70.0
    assert rows[0]["match_count"] == 1


def test_leaderboard_tiebreak_by_objective_then_model_id(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")

    # Two models with same total, different objective
    _make_match(session, rid, workflow_id="wf-a", model_id="model-b",
                total_score=75.0, objective_score=60.0, status="scored")
    _make_match(session, rid, workflow_id="wf-a", model_id="model-a",
                total_score=75.0, objective_score=70.0, status="scored")

    rows = store.leaderboard(session)
    # Same total; model-a wins on objective
    assert rows[0]["model_id"] == "model-a"
    assert rows[1]["model_id"] == "model-b"


def test_leaderboard_tiebreak_by_model_id_when_scores_equal(session):
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")

    _make_match(session, rid, workflow_id="wf-a", model_id="model-z",
                total_score=75.0, objective_score=60.0, status="scored")
    _make_match(session, rid, workflow_id="wf-a", model_id="model-a",
                total_score=75.0, objective_score=60.0, status="scored")

    rows = store.leaderboard(session)
    # Same total + objective; alphabetic model_id wins
    assert rows[0]["model_id"] == "model-a"
    assert rows[1]["model_id"] == "model-z"


def test_leaderboard_with_run_id(session):
    rid1 = _make_run(session)
    rid2 = _make_run(session)
    store.set_run_status(session, rid1, "completed")
    store.set_run_status(session, rid2, "completed")

    _make_match(session, rid1, workflow_id="wf-a", model_id="model-x",
                total_score=50.0, objective_score=40.0, status="scored")
    _make_match(session, rid2, workflow_id="wf-a", model_id="model-x",
                total_score=90.0, objective_score=85.0, status="scored")

    # Explicit run_id=rid1 should show score from run 1, not run 2
    rows = store.leaderboard(session, run_id=rid1)
    assert rows[0]["mean_objective"] == 40.0


def test_leaderboard_tag_filter_matching(session):
    """tag filter: only include scored matches whose workflow has the tag."""
    # This test patches get_workflow to avoid needing real workflow definitions
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")

    _make_match(session, rid, workflow_id="wf-tagged", model_id="model-x",
                total_score=80.0, objective_score=70.0, status="scored")
    _make_match(session, rid, workflow_id="wf-other", model_id="model-x",
                total_score=40.0, objective_score=30.0, status="scored")

    # Patch get_workflow so wf-tagged has tag "demo" and wf-other does not
    import unittest.mock as mock
    from app.golden_workflows import registry as reg

    def fake_get_workflow(wf_id):
        class FakeWF:
            tags = ["demo"] if wf_id == "wf-tagged" else []
        return FakeWF()

    with mock.patch.object(reg, "get_workflow", side_effect=fake_get_workflow):
        rows = store.leaderboard(session, tag="demo")

    assert len(rows) == 1
    assert rows[0]["mean_objective"] == 70.0


def test_leaderboard_tag_filter_no_match(session):
    """tag filter: no workflows carry the tag -> []."""
    rid = _make_run(session)
    store.set_run_status(session, rid, "completed")
    _make_match(session, rid, workflow_id="wf-a", model_id="model-x",
                total_score=80.0, objective_score=70.0, status="scored")

    import unittest.mock as mock
    from app.golden_workflows import registry as reg

    def fake_get_workflow(wf_id):
        class FakeWF:
            tags = []
        return FakeWF()

    with mock.patch.object(reg, "get_workflow", side_effect=fake_get_workflow):
        rows = store.leaderboard(session, tag="nonexistent")

    assert rows == []


def test_leaderboard_default_uses_latest_completed_run(session):
    """leaderboard() with no run_id should select the latest completed run."""
    rid1 = _make_run(session, workflow_ids=["wf-1"])
    rid2 = _make_run(session, workflow_ids=["wf-2"])  # created after rid1
    store.set_run_status(session, rid1, "completed")
    store.set_run_status(session, rid2, "completed")

    # Record the same model in both runs with different scores
    _make_match(session, rid1, workflow_id="wf-1", model_id="model-x",
                total_score=50.0, objective_score=40.0, status="scored")
    _make_match(session, rid2, workflow_id="wf-2", model_id="model-x",
                total_score=90.0, objective_score=85.0, status="scored")

    # Call leaderboard with no run_id; should return rid2's score (the latest)
    rows = store.leaderboard(session)
    assert len(rows) == 1
    assert rows[0]["model_id"] == "model-x"
    assert rows[0]["mean_objective"] == 85.0


# ---------------------------------------------------------------------------
# Invalid matches in the leaderboard (flagship v2)
# ---------------------------------------------------------------------------


def test_leaderboard_excludes_invalid_and_counts_them(session):
    run_id = _make_run(session, workflow_ids=["wf-a", "wf-b"], model_ids=["a"])
    from app.services.arena import store as arena_store
    arena_store.set_run_status(session, run_id, "completed")
    _make_match(session, run_id, workflow_id="wf-a", model_id="a",
                status="scored", objective_score=80.0, total_score=80.0)
    _make_match(session, run_id, workflow_id="wf-b", model_id="a",
                status="invalid", objective_score=None, total_score=None)
    session.commit()

    rows = arena_store.leaderboard(session, run_id=run_id)
    (row,) = rows
    assert row["model_id"] == "a"
    assert row["match_count"] == 1
    assert row["invalid_count"] == 1
    assert row["mean_objective"] == 80.0


def test_leaderboard_invalid_only_model_listed_with_zero_matches(session):
    run_id = _make_run(session, workflow_ids=["wf-a"], model_ids=["a", "b"])
    from app.services.arena import store as arena_store
    arena_store.set_run_status(session, run_id, "completed")
    _make_match(session, run_id, workflow_id="wf-a", model_id="a",
                status="scored", objective_score=70.0, total_score=70.0)
    _make_match(session, run_id, workflow_id="wf-a", model_id="b",
                status="invalid", objective_score=None, total_score=None)
    session.commit()

    rows = arena_store.leaderboard(session, run_id=run_id)
    by_model = {r["model_id"]: r for r in rows}
    assert by_model["b"]["match_count"] == 0
    assert by_model["b"]["invalid_count"] == 1
    assert by_model["b"]["mean_objective"] is None
    # scored model sorts above invalid-only model
    assert rows[0]["model_id"] == "a"


def test_leaderboard_ranks_by_objective_not_blend(session):
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["hi", "lo"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="hi",
        objective_score=80.0, judged_score=40.0, total_score=None, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 40.0, "judged_stdev": 5.0}, "subjective_mode": "panel"})
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="lo",
        objective_score=70.0, judged_score=95.0, total_score=None, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 95.0, "judged_stdev": 5.0}, "subjective_mode": "panel"})
    rows = arena_store.leaderboard(session, run_id=run_id)
    assert [r["model_id"] for r in rows] == ["hi", "lo"]
    assert rows[0]["subjective_mean"] == 40.0 and rows[0]["subjective_stdev"] == 5.0
    assert rows[0]["rank"] == 1 and rows[1]["rank"] == 2


def test_leaderboard_objective_only_rows_report_disabled(session):
    """Jury-off rows (subjective_mode='disabled', no judge block) rank by objective
    and report subjective_mean None + mode 'disabled'."""
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["hi", "lo"])
    arena_store.set_run_status(session, run_id, "completed")
    for mid, obj in [("hi", 85.0), ("lo", 70.0)]:
        arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id=mid,
            objective_score=obj, judged_score=None, total_score=obj, judge_missing=True,
            config={}, transcript_path=None, status="scored",
            score_breakdown={"objective": {"axes": {}}, "subjective_mode": "disabled"})
    rows = arena_store.leaderboard(session, run_id=run_id)
    assert [r["model_id"] for r in rows] == ["hi", "lo"]
    assert all(r["subjective_mean"] is None for r in rows)
    assert all(r["subjective_mode"] == "disabled" for r in rows)


def test_leaderboard_jury_all_failed_reports_missing_not_disabled(session):
    """A jury-on row whose judges all failed aggregates to 'missing' (visible outage),
    distinct from a deliberately disabled row."""
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["m1"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="m1",
        objective_score=80.0, judged_score=None, total_score=80.0, judge_missing=True,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": None, "judge_missing": True}, "subjective_mode": "missing"})
    rows = arena_store.leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mode"] == "missing"


def test_leaderboard_legacy_row_infers_panel(session):
    """A legacy row with a judge score but NO stored subjective_mode infers 'panel',
    not 'missing' — a successful legacy jury must not read as an outage."""
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["m1"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="m1",
        objective_score=80.0, judged_score=72.0, total_score=76.0, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 72.0}})
    rows = arena_store.leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mean"] == 72.0
    assert rows[0]["subjective_mode"] == "panel"


def test_leaderboard_legacy_toplevel_judged_score_no_breakdown(session):
    """Oldest shape: top-level ArenaMatch.judged_score set, score_breakdown None.
    The mean must still surface and infer 'panel' — never vanish as 'disabled'."""
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["m1", "m2"])
    arena_store.set_run_status(session, run_id, "completed")
    _make_match(session, run_id, model_id="m1", objective_score=80.0,
                judged_score=72.0, judge_missing=False, total_score=76.0)
    # a newer disabled row in the same board must not override the legacy panel
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="m2",
        objective_score=90.0, judged_score=None, total_score=90.0, judge_missing=True,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "subjective_mode": "disabled"})
    rows = {r["model_id"]: r for r in arena_store.leaderboard(session, run_id=run_id)}
    assert rows["m1"]["subjective_mean"] == 72.0
    assert rows["m1"]["subjective_mode"] == "panel"
    assert rows["m2"]["subjective_mode"] == "disabled"


def test_leaderboard_partial_outage_keeps_mean_and_flags_missing(session):
    """A model with one panel-scored match and one all-judges-failed match (multi-
    workflow run): the advisory mean survives AND the aggregated mode is 'missing'
    (worst-visibility-wins) so the partial outage stays flagged, not hidden."""
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a", "wf-b"], model_ids=["m1"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="m1",
        objective_score=80.0, judged_score=60.0, total_score=80.0, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 60.0}, "subjective_mode": "panel"})
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-b", model_id="m1",
        objective_score=70.0, judged_score=None, total_score=70.0, judge_missing=True,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": None, "judge_missing": True}, "subjective_mode": "missing"})
    rows = arena_store.leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mean"] == 60.0           # real advisory number survives
    assert rows[0]["subjective_mode"] == "missing"      # partial outage stays flagged


def test_leaderboard_shares_rank_on_exact_objective_tie(session):
    from app.services.arena import store as arena_store
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["a", "b", "c"])
    arena_store.set_run_status(session, run_id, "completed")
    axes = {"axes": {"grounding": {"passed": 5, "total": 5}}}
    for mid, obj, sub in [("a", 80.0, 40.0), ("b", 80.0, 90.0), ("c", 70.0, 99.0)]:
        arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id=mid,
            objective_score=obj, judged_score=sub, total_score=None, judge_missing=False,
            config={}, transcript_path=None, status="scored",
            score_breakdown={"objective": axes, "judge": {"judged_score": sub, "judged_stdev": 0.0}, "subjective_mode": "panel"})
    ranks = {r["model_id"]: r["rank"] for r in arena_store.leaderboard(session, run_id=run_id)}
    assert ranks["a"] == 1 and ranks["b"] == 1  # tied objective -> shared rank, subjective ignored
    assert ranks["c"] == 3


# --- ability card leaderboard (Spec B, Task 5) ------------------------------
def test_leaderboard_ranks_by_ovr_and_excludes_uncarded(session):
    run_id = store.create_run(
        session, ["risk-manager-control-day"],
        ["m-hi", "m-lo", "m-legacy", "m-notool"])

    def bd(axes, tool_calls):
        return {"objective": {"axes": axes},
                "diagnosis": {"counts_detail": {"tool_calls": tool_calls}}}

    hi_axes = {"grounding": {"passed": 10, "total": 10}, "adherence": {"passed": 11, "total": 11},
               "synthesis": {"passed": 5, "total": 5}, "procedural": {"passed": 6, "total": 6}}
    lo_axes = {"grounding": {"passed": 2, "total": 10}, "adherence": {"passed": 2, "total": 11},
               "synthesis": {"passed": 1, "total": 5}, "procedural": {"passed": 6, "total": 6}}
    common = dict(judged_score=None, judge_missing=False, config={}, transcript_path=None,
                  status="scored")
    store.record_match(session, run_id, "risk-manager-control-day", "m-hi",
                       objective_score=90.0, total_score=90.0,
                       score_breakdown=bd(hi_axes, 11), **common)
    store.record_match(session, run_id, "risk-manager-control-day", "m-lo",
                       objective_score=20.0, total_score=20.0,
                       score_breakdown=bd(lo_axes, 11), **common)
    store.record_match(session, run_id, "risk-manager-control-day", "m-legacy",
                       objective_score=50.0, total_score=50.0,
                       score_breakdown={"objective_score": 50.0}, **common)
    store.record_match(session, run_id, "risk-manager-control-day", "m-notool",
                       objective_score=60.0, total_score=60.0,
                       score_breakdown={"objective": {"axes": hi_axes}}, **common)
    store.set_run_status(session, run_id, "completed")

    rows = store.leaderboard(session, run_id=run_id)
    by_model = {r["model_id"]: r for r in rows}
    assert by_model["m-hi"]["card_mean"]["ovr"] > by_model["m-lo"]["card_mean"]["ovr"]
    assert by_model["m-hi"]["rank"] == 1
    assert by_model["m-legacy"]["card_mean"] is None
    assert by_model["m-legacy"]["mean_objective"] == 50.0
    assert by_model["m-notool"]["card_mean"] is None
    assert by_model["m-hi"]["rank"] < by_model["m-legacy"]["rank"]
    assert by_model["m-hi"]["rank"] < by_model["m-notool"]["rank"]


def test_leaderboard_partial_card_coverage_falls_back_to_objective(session):
    # One carded match + one uncarded scored match for the SAME model: the OVR
    # sample is incomplete, so the model must NOT rank by the carded subset — it
    # drops to the objective fallback group, with carded_count exposing the gap.
    run_id = store.create_run(
        session, ["risk-manager-control-day"], ["m-partial", "m-full"])
    axes = {"grounding": {"passed": 10, "total": 10}, "adherence": {"passed": 11, "total": 11},
            "synthesis": {"passed": 5, "total": 5}, "procedural": {"passed": 6, "total": 6}}
    carded_bd = {"objective": {"axes": axes},
                 "diagnosis": {"counts_detail": {"tool_calls": 11}}}
    common = dict(judged_score=None, judge_missing=False, config={}, transcript_path=None,
                  status="scored")
    # m-partial: one carded, one uncarded (no axes) — partial coverage
    store.record_match(session, run_id, "risk-manager-control-day", "m-partial",
                       objective_score=90.0, total_score=90.0,
                       score_breakdown=carded_bd, **common)
    store.record_match(session, run_id, "trader-rfq-booking-day", "m-partial",
                       objective_score=90.0, total_score=90.0,
                       score_breakdown={"objective_score": 90.0}, **common)
    # m-full: both matches carded
    store.record_match(session, run_id, "risk-manager-control-day", "m-full",
                       objective_score=50.0, total_score=50.0,
                       score_breakdown=carded_bd, **common)
    store.record_match(session, run_id, "trader-rfq-booking-day", "m-full",
                       objective_score=50.0, total_score=50.0,
                       score_breakdown=carded_bd, **common)
    store.set_run_status(session, run_id, "completed")

    rows = store.leaderboard(session, run_id=run_id)
    by_model = {r["model_id"]: r for r in rows}
    # m-partial: high objective but partial card coverage → uncarded, ranked last
    assert by_model["m-partial"]["card_mean"] is None
    assert by_model["m-partial"]["carded_count"] == 1
    assert by_model["m-partial"]["match_count"] == 2
    # m-full: fully carded → ranks first despite lower objective
    assert by_model["m-full"]["card_mean"] is not None
    assert by_model["m-full"]["carded_count"] == 2
    assert by_model["m-full"]["rank"] < by_model["m-partial"]["rank"]


def test_match_serialization_derives_card_on_read(session):
    run_id = store.create_run(session, ["risk-manager-control-day"], ["m1"])
    axes = {"grounding": {"passed": 8, "total": 10}, "adherence": {"passed": 9, "total": 11},
            "synthesis": {"passed": 4, "total": 5}, "procedural": {"passed": 5, "total": 6}}
    bd = {"objective": {"axes": axes}, "diagnosis": {"counts_detail": {"tool_calls": 11}}}
    store.record_match(session, run_id, "risk-manager-control-day", "m1",
                       objective_score=80.0, judged_score=None, total_score=80.0,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown=bd)
    detail = store.get_run(session, run_id)
    card = detail["matches"][0]["score_breakdown"]["card"]
    assert card is not None and set(card["stats"]) == {"GRD", "ADH", "SYN", "PRC", "EFF"}


def test_match_serialization_uncarded_row_carries_reason(session):
    run_id = store.create_run(session, ["risk-manager-control-day"], ["m2"])
    store.record_match(session, run_id, "risk-manager-control-day", "m2",
                       objective_score=50.0, judged_score=None, total_score=50.0,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown={"objective_score": 50.0})
    detail = store.get_run(session, run_id)
    sb = detail["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "legacy_no_axes"


# ---- Consistency (CON) from multi-trial aggregates ----

_WF = "risk-manager-control-day"


def _trial(passed_by_axis, tool_calls=11):
    """A per-trial breakdown _derive_card can card (axes + tool_calls)."""
    axes = {ax: {"passed": p, "total": t} for ax, (p, t) in passed_by_axis.items()}
    return {"objective": {"axes": axes},
            "diagnosis": {"counts_detail": {"tool_calls": tool_calls}}}


# Two trials with clearly different base OVRs (strong vs weak grounding/synthesis).
_STRONG = {"grounding": (10, 10), "adherence": (9, 10), "synthesis": (3, 3), "procedural": (5, 6)}
_WEAK = {"grounding": (3, 10), "adherence": (4, 10), "synthesis": (0, 3), "procedural": (4, 6)}


def _agg_match(session, rid, model, trials, *, wf=_WF, status="scored"):
    store.record_match(session, rid, wf, model,
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status=status,
                       score_breakdown={"n_trials": len(trials), "aggregate": trials})


def test_get_run_cards_aggregate_from_trials_with_con(session):
    from app.services.arena import scoring
    rid = _make_run(session, model_ids=["m"])
    trials = [_trial(_STRONG), _trial(_WEAK)]
    _agg_match(session, rid, "m", trials)

    match = store.get_run(session, rid)["matches"][0]
    sb = match["score_breakdown"]
    # Per-trial cards attached for the drilldown tabs.
    tcards = [t["card"] for t in sb["aggregate"]]
    assert all(c is not None for c in tcards)
    base_ovrs = [c["ovr"] for c in tcards]
    assert base_ovrs[0] > base_ovrs[1]           # strong trial out-scores weak
    # Top-level aggregate card = averaged stats + trial-dispersion CON.
    agg = sb["card"]
    assert agg["con"] == scoring.consistency_stat(base_ovrs)
    assert agg["base_ovr"] == round(sum(base_ovrs) / len(base_ovrs))
    assert agg["ovr"] == scoring.blend_ovr(sum(base_ovrs) / len(base_ovrs), agg["con"])


def test_get_run_single_trial_aggregate_is_grey(session):
    rid = _make_run(session, model_ids=["m"])
    _agg_match(session, rid, "m", [_trial(_STRONG)])   # n_trials == 1
    agg = store.get_run(session, rid)["matches"][0]["score_breakdown"]["card"]
    assert agg["con"] is None                          # no dispersion → grey
    assert agg["ovr"] == agg["base_ovr"]               # OVR at base


def test_get_run_partial_trial_coverage_is_uncarded(session):
    # If ANY trial can't be carded (no axes), the whole aggregate is uncarded — a
    # biased subset must not masquerade as the full multi-trial sample.
    rid = _make_run(session, model_ids=["m"])
    _agg_match(session, rid, "m",
               [_trial(_STRONG), {"objective_score": 40.0}])   # 2nd trial: no axes
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None
    assert sb["card_reason"] == "partial_trial_cards"


@pytest.mark.parametrize("aggregate", [
    [_trial(_STRONG), _trial(_WEAK)],          # short: 2 stored, claims 3
    [],                                         # empty
    None,                                       # missing entirely
    {"objective": {}},                          # not a list
], ids=["short", "empty", "missing", "non_list"])
def test_get_run_n_trials_mismatch_is_uncarded(session, aggregate):
    # A row DECLARING n_trials=3 must carry exactly 3 trials. Any short / empty /
    # missing / non-list aggregate (retry, partial write) must NOT card — and in
    # particular must NOT fall through to the top-level representative objective.
    rid = _make_run(session, model_ids=["m"])
    bd = {"n_trials": 3,
          # a top-level derivable objective the guard must refuse to card from:
          "objective": {"axes": {ax: {"passed": p, "total": t}
                                 for ax, (p, t) in _STRONG.items()}},
          "diagnosis": {"counts_detail": {"tool_calls": 11}}}
    if aggregate is not None:
        bd["aggregate"] = aggregate
    store.record_match(session, rid, _WF, "m",
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown=bd)
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None
    assert sb["card_reason"] == "partial_trial_coverage"


def test_get_run_partial_aggregate_exposes_no_trial_cards(session):
    # The coverage guard must not be bypassed via the per-trial path: a declared
    # 3-trial row with only 2 derivable trials is uncarded AND its trials carry NO
    # per-trial cards (so the drilldown can't render scored trial tabs for a remnant).
    rid = _make_run(session, model_ids=["m"])
    store.record_match(session, rid, _WF, "m",
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored",
                       score_breakdown={"n_trials": 3,
                                        "aggregate": [_trial(_STRONG), _trial(_WEAK)]})
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "partial_trial_coverage"
    assert all("card" not in t for t in sb["aggregate"])   # no scored trial cards


def test_get_run_uncardable_trial_exposes_no_partial_ovrs(session):
    # Complete count (n_trials == len) but ONE trial can't be carded (no axes/tool
    # count): the aggregate is uncarded, and NEITHER trial exposes a derived card —
    # a cardable subset must not masquerade as trustworthy scored OVRs.
    rid = _make_run(session, model_ids=["m"])
    store.record_match(session, rid, _WF, "m",
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored",
                       score_breakdown={"n_trials": 2,
                                        "aggregate": [_trial(_STRONG),
                                                      {"objective_score": 40.0}]})
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "partial_trial_cards"
    assert all("card" not in t for t in sb["aggregate"])   # not even the cardable one


def test_get_run_uncarded_aggregate_strips_prestored_trial_cards(session):
    # Even if a producer persisted per-trial `card` fields, a refused aggregate must
    # NOT leak them (strip on the uncarded path).
    rid = _make_run(session, model_ids=["m"])
    strong_with_card = {**_trial(_STRONG),
                        "card": {"ovr": 88, "stats": {}, "jdg": None, "position": "x"}}
    store.record_match(session, rid, _WF, "m",
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored",
                       score_breakdown={"n_trials": 3,        # count mismatch → refused
                                        "aggregate": [strong_with_card, _trial(_WEAK)]})
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "partial_trial_coverage"
    assert all("card" not in t for t in sb["aggregate"])   # pre-stored card stripped


def test_get_run_does_not_mutate_stored_breakdown(session):
    # Derivation copies — the ORM-owned JSON must stay pristine (read path).
    rid = _make_run(session, model_ids=["m"])
    _agg_match(session, rid, "m", [_trial(_STRONG), _trial(_WEAK)])
    store.get_run(session, rid)
    from app.models import ArenaMatch
    stored = session.query(ArenaMatch).filter_by(run_id=rid, model_id="m").one()
    assert "card" not in stored.score_breakdown          # top-level card not persisted
    assert "card" not in stored.score_breakdown["aggregate"][0]


def test_leaderboard_ranks_by_aggregate_final_ovr(session):
    from app.services.arena import scoring
    rid = _make_run(session, model_ids=["steady", "erratic"])
    # steady: two identical strong trials → CON 99, OVR ≈ base.
    _agg_match(session, rid, "steady", [_trial(_STRONG), _trial(_STRONG)])
    # erratic: strong then weak → high dispersion → CON low → OVR discounted.
    _agg_match(session, rid, "erratic", [_trial(_STRONG), _trial(_WEAK)])
    store.set_run_status(session, rid, "completed")

    rows = {r["model_id"]: r for r in store.leaderboard(session, run_id=rid)}
    steady, erratic = rows["steady"], rows["erratic"]
    # Each card_mean.ovr is the match's aggregate final OVR (single match per model).
    base_strong = store._derive_card(_trial(_STRONG), _WF)[0]["ovr"]
    assert steady["card_mean"]["con"] == 99
    assert steady["card_mean"]["ovr"] == base_strong          # perfect consistency
    # The erratic model is discounted below its own base mean and below steady.
    assert erratic["card_mean"]["con"] < 99
    assert erratic["card_mean"]["ovr"] < erratic["card_mean"]["base_ovr"]
    assert steady["rank"] < erratic["rank"]


def test_read_recomputes_over_a_stored_card_that_disagrees_with_axes(session):
    # A stored card whose OVR contradicts its own axes (stale / corrupted JSON) must
    # be RECOMPUTED, never trusted — else the public board ranks a fabricated number.
    from app.services.arena import scoring
    rid = _make_run(session, model_ids=["m"])
    zero_axes = {ax: {"passed": 0, "total": t} for ax, (_p, t) in _STRONG.items()}
    bd = {"objective": {"axes": zero_axes},
          "diagnosis": {"counts_detail": {"tool_calls": 11}},
          "card": {"ovr": 99, "base_ovr": 99,
                   "stats": {"GRD": 99, "ADH": 99, "SYN": 99, "PRC": 99, "EFF": 99},
                   "jdg": None, "position": "Sniper"}}
    store.record_match(session, rid, _WF, "m",
                       objective_score=0.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown=bd)
    derived = store._derive_card(bd, _WF)[0]
    assert derived["ovr"] == 0                # zero-pass axes → OVR 0, not the stored 99
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"]["ovr"] == 0             # read recomputed, ignored the stored 99


def test_get_run_null_aggregate_entry_fails_closed(session):
    # A null / primitive trial placeholder (partial write) must fail closed as
    # uncarded, never crash the read path on _derive_card(None).
    rid = _make_run(session, model_ids=["m"])
    store.record_match(session, rid, _WF, "m",
                       objective_score=60.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored",
                       score_breakdown={"n_trials": 2,
                                        "aggregate": [None, _trial(_STRONG)]})
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]   # no crash
    assert sb["card"] is None and sb["card_reason"] == "invalid_trial_shape"


def test_stored_card_ranks_never_but_still_presents_when_unrecomputable(session):
    # Non-empty axes + a stored card BUT missing tool count → EFF can't be recomputed.
    # The leaderboard (ranking) must NOT rank the unverifiable stored OVR; the drilldown
    # (presentation) still shows the write-time card. This is the deliberate split.
    rid = _make_run(session, model_ids=["m"])
    axes = {ax: {"passed": p, "total": t} for ax, (p, t) in _STRONG.items()}
    bd = {"objective": {"axes": axes},          # non-empty axes, but…
          "diagnosis": {"counts_detail": {}},   # …no tool_calls → missing_tool_count
          "card": {"ovr": 99, "base_ovr": 99,
                   "stats": {"GRD": 99, "ADH": 99, "SYN": 99, "PRC": 99, "EFF": 99},
                   "jdg": None, "position": "Sniper"}}
    store.record_match(session, rid, _WF, "m",
                       objective_score=80.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown=bd)
    store.set_run_status(session, rid, "completed")
    # Ranking: uncarded — the stored 99 never reaches card_mean.
    row = next(r for r in store.leaderboard(session, run_id=rid) if r["model_id"] == "m")
    assert row["card_mean"] is None and row["carded_count"] == 0
    # Presentation: the drilldown still renders the write-time card.
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is not None and sb["card"]["ovr"] == 99


def test_leaderboard_ignores_stored_card_without_scoring_evidence(session):
    # Fail-honest: a single-match row carrying a persisted `card` but NO axes / tool
    # count (stale / schema drift) must be re-derived, fail, and NOT rank on the board.
    rid = _make_run(session, model_ids=["ghost"])
    store.record_match(session, rid, _WF, "ghost",
                       objective_score=90.0, judged_score=None, total_score=None,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored",
                       score_breakdown={"card": {"ovr": 99, "base_ovr": 99,
                                                 "stats": {"GRD": 99, "ADH": 99, "SYN": 99,
                                                           "PRC": 99, "EFF": 99},
                                                 "jdg": None, "position": "Sniper"}})
    store.set_run_status(session, rid, "completed")
    row = next(r for r in store.leaderboard(session, run_id=rid)
               if r["model_id"] == "ghost")
    assert row["card_mean"] is None            # not ranked from an unvalidated card
    assert row["carded_count"] == 0
    # And the drilldown agrees — uncarded with a fail-honest reason.
    sb = store.get_run(session, rid)["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "legacy_no_axes"


def test_leaderboard_single_trial_match_has_no_con(session):
    from app.services.arena import scoring
    rid = _make_run(session, model_ids=["solo"])
    _agg_match(session, rid, "solo", [_trial(_STRONG)])
    store.set_run_status(session, rid, "completed")
    cm = next(r for r in store.leaderboard(session, run_id=rid)
              if r["model_id"] == "solo")["card_mean"]
    base = store._derive_card(_trial(_STRONG), _WF)[0]["ovr"]
    assert cm["con"] is None
    assert cm["ovr"] == base == scoring.blend_ovr(base, None)


# ---- merge_runs (fold single-trial runs into one multi-trial aggregate) ----

def _single(session, rid, model, breakdown, obj_score):
    """Record one SCORED single-trial match with a trial-shaped breakdown."""
    store.record_match(
        session, rid, _WF, model,
        objective_score=obj_score, judged_score=None, total_score=obj_score,
        judge_missing=False, config={}, transcript_path=None, status="scored",
        score_breakdown={**breakdown, "objective_score": obj_score,
                         "subjective_mode": "disabled"})


def test_merge_runs_folds_pairs_into_trial_aggregates_with_con(session):
    from app.services.arena import scoring
    r1 = _make_run(session, model_ids=["m"])
    _single(session, r1, "m", _trial(_STRONG), 84.6)
    store.set_run_status(session, r1, "completed")
    r2 = _make_run(session, model_ids=["m"])
    _single(session, r2, "m", _trial(_WEAK), 48.7)
    store.set_run_status(session, r2, "completed")

    new_id = store.merge_runs(session, [r1, r2])
    matches = store.get_run(session, new_id)["matches"]
    assert len(matches) == 1
    sb = matches[0]["score_breakdown"]
    assert sb["n_trials"] == 2
    # Trials ordered by source-run position (r1 then r2).
    assert [t["objective_score"] for t in sb["aggregate"]] == [84.6, 48.7]
    assert sb["objective_score"] == round((84.6 + 48.7) / 2, 1)
    # Carded on read with a trial-dispersion CON.
    base = [store._derive_card(_trial(_STRONG), _WF)[0]["ovr"],
            store._derive_card(_trial(_WEAK), _WF)[0]["ovr"]]
    assert sb["card"]["con"] == scoring.consistency_stat(base)
    # Non-destructive: the sources are untouched.
    assert store.get_run(session, r1)["matches"][0]["score_breakdown"]["objective_score"] == 84.6


def test_merge_runs_lone_pair_becomes_single_trial_grey_con(session):
    r1 = _make_run(session, model_ids=["a", "b"])
    _single(session, r1, "a", _trial(_STRONG), 84.6)
    _single(session, r1, "b", _trial(_STRONG), 80.0)   # "b" only ever runs here
    store.set_run_status(session, r1, "completed")
    r2 = _make_run(session, model_ids=["a"])
    _single(session, r2, "a", _trial(_WEAK), 48.7)     # only model "a" appears twice
    store.set_run_status(session, r2, "completed")

    new_id = store.merge_runs(session, [r1, r2])
    by_model = {m["model_id"]: m["score_breakdown"]
                for m in store.get_run(session, new_id)["matches"]}
    assert by_model["a"]["n_trials"] == 2 and by_model["a"]["card"]["con"] is not None
    # "b" ran in only one source → single-trial aggregate → CON greys.
    assert by_model["b"]["n_trials"] == 1 and by_model["b"]["card"]["con"] is None


def test_merge_runs_requires_two_runs(session):
    r1 = _make_run(session, model_ids=["m"])
    _single(session, r1, "m", _trial(_STRONG), 84.6)
    with pytest.raises(ValueError):
        store.merge_runs(session, [r1])
    with pytest.raises(ValueError):
        store.merge_runs(session, [r1, r1])            # de-duped → only one distinct


# ---- delete_runs (DB-pure hard delete) ----

def test_delete_runs_removes_runs_matches_and_returns_paths(session):
    rid = store.create_run(session, ["wf"], ["m"])
    store.record_match(session, rid, "wf", "m", objective_score=80.0,
                       judged_score=None, total_score=80.0, judge_missing=False,
                       config={}, transcript_path="/x/t.json", status="scored",
                       score_breakdown={"objective_score": 80.0})
    session.commit()
    out = store.delete_runs(session, [rid, 999999])  # 999999 does not exist
    session.commit()
    assert out["deleted_run_ids"] == [rid]
    assert out["match_count"] == 1
    assert "/x/t.json" in out["transcript_paths"]
    assert store.get_run(session, rid) is None
