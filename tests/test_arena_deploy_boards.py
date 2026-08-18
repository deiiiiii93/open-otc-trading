"""The leaderboard is DERIVED from the arena DB, never typed into a manifest.

Two separate inputs, tested separately: boards.yaml is editorial curation (which
runs count as boards), boards.json is the exported measurement. The renderer must
keep "no board has been run" distinguishable from "we did not export" — the same
empty-vs-unavailable rule the readership counters follow.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "docs" / "arena" / "deploy"
sys.path.insert(0, str(DEPLOY))

import boards as bd  # noqa: E402


# --------------------------------------------------------------------------
# boards.yaml — curation
# --------------------------------------------------------------------------

def write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "boards.yaml"
    p.write_text(text)
    return p


def test_board_ref_defaults_the_label_to_the_run_number(tmp_path):
    path = write_yaml(tmp_path, "- {run: 20, workflow: risk-manager-control-day}\n")
    (ref,) = bd.load_board_refs(path)
    assert ref.run == 20
    assert ref.workflow == "risk-manager-control-day"
    assert ref.label == "Run #20"
    assert ref.post is None


def test_board_ref_keeps_an_explicit_label_and_post(tmp_path):
    path = write_yaml(
        tmp_path,
        "- run: 94\n"
        "  workflow: high-board-portfolio-review-day\n"
        '  label: "Run #94 (post-audit)"\n'
        "  post: 2026-07-29-run94-otc-desk-agent-arena.md\n",
    )
    (ref,) = bd.load_board_refs(path)
    assert ref.label == "Run #94 (post-audit)"
    assert ref.post == "2026-07-29-run94-otc-desk-agent-arena.md"


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("- {run: 20}\n", "missing required key"),
        ("- {workflow: x}\n", "missing required key"),
        ("- {run: 20, workflow: x, colour: red}\n", "unknown key"),
        ("- {run: not-a-number, workflow: x}\n", "run must be an integer"),
        ("- [1, 2]\n", "must be a mapping"),
    ],
)
def test_board_ref_validation_fails_loud(tmp_path, text, fragment):
    with pytest.raises(bd.BoardsError, match=fragment):
        bd.load_board_refs(write_yaml(tmp_path, text))


def test_the_same_run_may_not_be_declared_twice_for_one_workflow(tmp_path):
    path = write_yaml(
        tmp_path,
        "- {run: 20, workflow: risk-manager-control-day}\n"
        "- {run: 20, workflow: risk-manager-control-day}\n",
    )
    with pytest.raises(bd.BoardsError, match="duplicate"):
        bd.load_board_refs(path)


def test_a_missing_boards_yaml_fails_loud(tmp_path):
    with pytest.raises(bd.BoardsError, match="not found"):
        bd.load_board_refs(tmp_path / "nope.yaml")


# --------------------------------------------------------------------------
# boards.json — the exported measurement
# --------------------------------------------------------------------------

CARDED_ROW = {
    "rank": 1, "model": "gpt-5-6-terra", "effort": None, "ovr": 86,
    "stats": {"GRD": 99, "ADH": 95, "SYN": 90, "PRC": 87, "EFF": 56},
    "con": 96, "objective": 89.8, "trials": 2, "invalid": 0,
}
SNAPSHOT = {
    "version": bd.SNAPSHOT_VERSION,
    "generated_at": "2026-08-18T12:00:00+00:00",
    "workflows": [
        {
            "id": "risk-manager-control-day", "title": "Risk Manager Control Day",
            "persona": "risk_manager", "steps": 9, "par": 24,
            "boards": [{
                "run": 20, "label": "Run #20", "date": "2026-07-08",
                "post": "2026-07-13-run20-otc-desk-agent-arena.md",
                "checks": 39, "carded": True, "models": 17, "rows": [CARDED_ROW],
            }],
        },
        {
            "id": "ops-settlement-day", "title": "Operations Settlement Day",
            "persona": "trader", "steps": 8, "par": None, "boards": [],
        },
    ],
}


def test_load_boards_returns_none_when_absent_or_unreadable(tmp_path):
    assert bd.load_boards(tmp_path / "missing.json") is None
    bad = tmp_path / "boards.json"
    bad.write_text("{not json")
    assert bd.load_boards(bad) is None


def test_load_boards_rejects_an_unknown_snapshot_version(tmp_path):
    p = tmp_path / "boards.json"
    p.write_text(json.dumps({**SNAPSHOT, "version": 99}))
    assert bd.load_boards(p) is None


def test_load_boards_accepts_a_current_snapshot(tmp_path):
    p = tmp_path / "boards.json"
    p.write_text(json.dumps(SNAPSHOT))
    got = bd.load_boards(p)
    assert got is not None and len(got["workflows"]) == 2


def test_a_board_snapshot_never_goes_stale(tmp_path):
    """Unlike readership, a finished board is a historical fact.

    stats.json expires after 14 days because a stale traffic count is a false
    claim about now. A board measured in July is still exactly what happened in
    July, so age is not a reason to hide it.
    """
    p = tmp_path / "boards.json"
    p.write_text(json.dumps({**SNAPSHOT, "generated_at": "2020-01-01T00:00:00+00:00"}))
    assert bd.load_boards(p) is not None


def test_ordered_workflows_puts_measured_workflows_first_newest_board_first():
    wfs = [
        {"id": "zzz-no-board", "boards": []},
        {"id": "old", "boards": [{"date": "2026-06-01"}]},
        {"id": "aaa-no-board", "boards": []},
        {"id": "new", "boards": [{"date": "2026-08-01"}, {"date": "2026-05-01"}]},
    ]
    assert [w["id"] for w in bd.ordered_workflows(wfs)] == [
        "new", "old", "aaa-no-board", "zzz-no-board",
    ]


# --------------------------------------------------------------------------
# Shaping store rows into the published snapshot
# --------------------------------------------------------------------------

REF = bd.BoardRef(run=20, workflow="risk-manager-control-day", label="Run #20")

STORE_ROW = {
    "rank": 1, "model_id": "gpt-5-6-terra", "reasoning_effort": None,
    "mean_objective": 89.8,
    "card_mean": {"ovr": 86, "base_ovr": 88, "con": 96,
                  "GRD": 99, "ADH": 95, "SYN": 90, "PRC": 87, "EFF": 56},
    "carded_count": 2, "match_count": 2, "invalid_count": 0,
    "subjective_mean": None, "subjective_mode": "disabled",
}
LEGACY_ROW = {
    "rank": 1, "model_id": "claude-sonnet-4-6", "reasoning_effort": None,
    "mean_objective": 93.5, "card_mean": None,
    "carded_count": 0, "match_count": 5, "invalid_count": 1,
    "subjective_mean": 66.4, "subjective_mode": "panel",
}


def test_shape_row_publishes_the_card_stats_and_drops_store_internals():
    got = bd.shape_row(STORE_ROW)
    assert got == {
        "rank": 1, "model": "gpt-5-6-terra", "effort": None, "ovr": 86,
        "stats": {"GRD": 99, "ADH": 95, "SYN": 90, "PRC": 87, "EFF": 56},
        "con": 96, "objective": 89.8, "matches": 2, "trials": None, "invalid": 0,
    }
    assert "base_ovr" not in got["stats"] and "subjective_mean" not in got


def test_matches_and_trials_are_reported_as_different_numbers():
    """The runner folds a contestant's trials into ONE aggregate match."""
    got = bd.shape_row(dict(STORE_ROW, match_count=1), trials=2)
    assert got["matches"] == 1 and got["trials"] == 2


def test_shape_row_of_an_uncarded_contestant_carries_no_invented_zeroes():
    got = bd.shape_row(LEGACY_ROW)
    assert got["ovr"] is None and got["con"] is None and got["stats"] == {}
    assert got["objective"] == 93.5 and got["invalid"] == 1


def test_shape_board_refuses_a_run_that_spans_more_than_its_declared_workflow():
    """#104 and #110 fold every workflow into one row per model.

    Publishing that under a single workflow's heading would present a
    cross-workflow average as a board for one workflow.
    """
    with pytest.raises(bd.BoardsError, match="spans"):
        bd.shape_board(
            REF, [STORE_ROW],
            workflow_ids={"risk-manager-control-day", "ops-settlement-day"},
            date="2026-07-08", checks=39,
        )


def test_shape_board_refuses_a_run_measured_on_a_different_workflow():
    with pytest.raises(bd.BoardsError, match="risk-limit-breach-day"):
        bd.shape_board(
            REF, [STORE_ROW], workflow_ids={"risk-limit-breach-day"},
            date="2026-07-08", checks=39,
        )


def test_shape_board_refuses_a_declared_board_with_no_scored_rows():
    with pytest.raises(bd.BoardsError, match="no scored"):
        bd.shape_board(
            REF, [], workflow_ids={"risk-manager-control-day"},
            date="2026-07-08", checks=39,
        )


def test_shape_board_is_carded_when_any_contestant_has_a_card():
    board = bd.shape_board(
        REF, [STORE_ROW, LEGACY_ROW], workflow_ids={"risk-manager-control-day"},
        date="2026-07-08", checks=39,
    )
    assert board["carded"] is True
    # Surfaced so a partly-carded board cannot look fully measured.
    assert board["carded_rows"] == 1 and board["models"] == 2


def test_shape_board_of_an_all_legacy_field_is_uncarded():
    board = bd.shape_board(
        REF, [LEGACY_ROW], workflow_ids={"risk-manager-control-day"},
        date="2026-06-26", checks=None,
    )
    assert board["carded"] is False and board["checks"] is None


def test_shape_board_publishes_a_common_trial_depth_but_not_a_mixed_one():
    arms = {("gpt-5-6-terra", None): 2, ("claude-sonnet-4-6", None): 2}
    same = bd.shape_board(REF, [STORE_ROW, LEGACY_ROW],
                          workflow_ids={"risk-manager-control-day"},
                          date="2026-07-08", checks=39, trials_by_arm=arms)
    assert same["trials"] == 2

    arms[("claude-sonnet-4-6", None)] = 1
    mixed = bd.shape_board(REF, [STORE_ROW, LEGACY_ROW],
                           workflow_ids={"risk-manager-control-day"},
                           date="2026-07-08", checks=39, trials_by_arm=arms)
    assert mixed["trials"] is None
    assert [r["trials"] for r in mixed["rows"]] == [2, 1]


# --------------------------------------------------------------------------
# Consolidated (all-workflow) model cards
# --------------------------------------------------------------------------

def _row(model, ovr, rank=1, con=90, eff=50, **kw):
    return {"rank": rank, "model": model, "effort": kw.get("effort"), "ovr": ovr,
            "stats": {"GRD": 90, "ADH": 90, "SYN": 90, "PRC": 90, "EFF": eff},
            "con": con, "objective": 90.0, "matches": 1, "trials": 2, "invalid": 0}


def _wf(wid, label, rows):
    return {"id": wid, "title": wid.title(), "persona": "trader", "steps": 8,
            "par": None, "boards": [{"run": 1, "label": label, "date": "2026-07-01",
                                     "post": None, "checks": 39, "carded": True,
                                     "carded_rows": len(rows), "models": len(rows),
                                     "trials": 2, "rows": rows}]}


TWO_WORKFLOWS = [
    _wf("alpha-day", "Run #1", [_row("wide", 96, 1), _row("steady", 84, 2)]),
    _wf("beta-day", "Run #2", [_row("steady", 88, 1), _row("wide", 72, 2),
                               _row("newcomer", 90, 3)]),
]


def test_consolidated_card_averages_a_model_across_the_boards_it_contested():
    cards = {c["model"]: c for c in bd.consolidated_cards(TWO_WORKFLOWS)}
    assert cards["wide"]["ovr"] == 84          # (96 + 72) / 2
    assert cards["steady"]["ovr"] == 86        # (84 + 88) / 2


def test_consolidated_card_publishes_the_spread_because_the_mean_hides_it():
    """deepseek-v4-pro spans 84-89 and minimax-m3 spans 42-85; a single mean
    presents those as the same kind of measurement."""
    cards = {c["model"]: c for c in bd.consolidated_cards(TWO_WORKFLOWS)}
    assert (cards["wide"]["ovr_min"], cards["wide"]["ovr_max"]) == (72, 96)
    assert (cards["steady"]["ovr_min"], cards["steady"]["ovr_max"]) == (84, 88)


def test_consolidated_card_states_coverage_and_marks_the_boards_not_contested():
    cards = {c["model"]: c for c in bd.consolidated_cards(TWO_WORKFLOWS)}
    newcomer = cards["newcomer"]
    assert newcomer["coverage"] == 1 and newcomer["boards_total"] == 2
    # An entry per board, so a gap is visible rather than inferred from a shorter list.
    assert [e["ovr"] for e in newcomer["per_board"]] == [None, 90]
    assert [e["workflow"] for e in newcomer["per_board"]] == ["alpha-day", "beta-day"]


def test_consolidated_cards_rank_by_mean_then_coverage_then_name():
    order = [c["model"] for c in bd.consolidated_cards(TWO_WORKFLOWS)]
    assert order == ["newcomer", "steady", "wide"]   # 90, 86, 84

    tied = bd.consolidated_cards([
        _wf("alpha-day", "Run #1", [_row("full", 80, 1), _row("partial", 80, 2)]),
        _wf("beta-day", "Run #2", [_row("full", 80, 1)]),
    ])
    # Same mean; the model measured on more boards is the better-evidenced one.
    assert [c["model"] for c in tied] == ["full", "partial"]


def test_a_contestant_is_model_plus_effort_arm_in_the_consolidation_too():
    arms = bd.consolidated_cards([
        _wf("alpha-day", "Run #1", [_row("luna", 85, 1, effort="low"),
                                    _row("luna", 78, 2, effort="max")]),
    ])
    assert [(c["model"], c["effort"]) for c in arms] == [("luna", "low"), ("luna", "max")]


def test_an_uncarded_contestant_gets_no_consolidated_card():
    """A pre-card board contributes no OVR, so there is nothing to average."""
    legacy = _wf("alpha-day", "Run #1", [dict(_row("old", 0), ovr=None, stats={}, con=None)])
    assert bd.consolidated_cards([legacy]) == []
