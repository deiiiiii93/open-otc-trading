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
