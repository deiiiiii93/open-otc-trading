"""Tests for the Arena per-model scorecard generator (pure, no DB).

Every test runs against the frozen run #94 fixtures captured in
`tests/fixtures/arena_run94_*.json`, so the suite never touches the live DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.arena.scorecard import (
    FailedCheck,
    Target,
    failed_checks,
    latest_transcript_paths,
    load_targets,
    render_scorecard,
    resolve_transcript,
)

REPO = Path(__file__).resolve().parents[1]
TARGETS = REPO / "docs/arena/scorecards/targets.yaml"
FIXTURES = REPO / "tests/fixtures"


@pytest.fixture
def leaderboard() -> list[dict]:
    return json.loads((FIXTURES / "arena_run94_leaderboard.json").read_text())


@pytest.fixture
def breakdowns() -> dict:
    return json.loads((FIXTURES / "arena_run94_breakdowns.json").read_text())


def test_loads_ten_targets():
    targets = load_targets(TARGETS)
    assert len(targets) == 10


def test_every_target_model_exists_on_the_board(leaderboard):
    board = {r["model_id"] for r in leaderboard}
    for t in load_targets(TARGETS):
        for mid in t.model_ids:
            assert mid in board, f"{t.lab}: {mid} is not on run #94"


def test_no_model_is_claimed_by_two_targets():
    seen: set[str] = set()
    for t in load_targets(TARGETS):
        for mid in t.model_ids:
            assert mid not in seen, f"{mid} claimed twice"
            seen.add(mid)


def test_tiers_are_valid_and_glm_carries_the_interop_note():
    targets = {t.lab: t for t in load_targets(TARGETS)}
    assert {t.tier for t in targets.values()} <= {"A", "B1", "B2"}
    # Accuracy rule: GLM's harness fault was found and fixed, so it carries the
    # interop note rather than an open "is my harness broken?" question.
    assert targets["Zhipu"].interop_note is True


def test_interop_note_set_for_exactly_the_four_protocol_pinned_labs():
    labs = {t.lab for t in load_targets(TARGETS) if t.interop_note}
    assert labs == {"Zhipu", "Alibaba", "Meituan", "MiniMax"}


def test_every_target_declares_at_least_one_channel():
    for t in load_targets(TARGETS):
        assert t.channels, f"{t.lab} has no outreach channel"


def test_failed_checks_returns_at_most_limit(breakdowns):
    got = failed_checks(breakdowns["glm-5-2"], limit=3)
    assert len(got) <= 3
    assert all(isinstance(c, FailedCheck) for c in got)


def test_failed_checks_finds_glm_synthesis_failures(breakdowns):
    """GLM scored synthesis 0/5, so at least one synthesis check must surface."""
    got = failed_checks(breakdowns["glm-5-2"], limit=50)
    assert any(c.axis == "synthesis" for c in got)


def test_failed_checks_empty_for_a_perfect_board_row(breakdowns):
    """Gemini 3.6 Flash scored 100.0 objective — nothing should be reported failed."""
    assert failed_checks(breakdowns["gemini-3-6-flash"]) == []


def test_failed_checks_tolerates_a_breakdown_with_no_steps():
    assert failed_checks({"objective": {}}) == []


def test_failed_checks_treats_a_missing_passed_key_as_unknown_not_failed():
    """Fail-honest: an unknown check must never be reported to a lab as a failure."""
    bd = {"objective": {"steps": [{"index": 0, "checks": [{"label": "x", "axis": "a"}]}]}}
    assert failed_checks(bd) == []


def test_resolve_transcript_returns_none_when_path_is_none(tmp_path):
    assert resolve_transcript({"m": None}, "m", tmp_path) is None


def test_resolve_transcript_returns_none_when_model_absent(tmp_path):
    assert resolve_transcript({}, "missing-model", tmp_path) is None


def test_resolve_transcript_returns_none_when_file_missing(tmp_path):
    paths = {"m": "artifacts/arena/85/wf/m/transcript.json"}
    assert resolve_transcript(paths, "m", tmp_path) is None


def test_resolve_transcript_returns_path_when_file_exists(tmp_path):
    rel = "artifacts/arena/85/wf/m/transcript.json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert resolve_transcript({"m": rel}, "m", tmp_path) == rel


def test_latest_transcript_paths_prefers_the_highest_run_id():
    """A re-run supersedes: GLM has a broken run #91 trace and a clean #93 one."""
    rows = [
        (91, "glm-5-2", "artifacts/arena/91/wf/glm-5-2/transcript.json"),
        (93, "glm-5-2", "artifacts/arena/93/wf/glm-5-2/transcript.json"),
    ]
    assert latest_transcript_paths(rows)["glm-5-2"].startswith("artifacts/arena/93/")
    # Order of the input rows must not change the answer.
    assert latest_transcript_paths(rows[::-1])["glm-5-2"].startswith("artifacts/arena/93/")


def test_latest_transcript_paths_skips_null_paths():
    rows = [(94, "m", None), (85, "m", "artifacts/arena/85/wf/m/transcript.json")]
    assert latest_transcript_paths(rows)["m"].startswith("artifacts/arena/85/")


def test_latest_transcript_paths_omits_a_model_with_only_nulls():
    assert latest_transcript_paths([(94, "m", None)]) == {}


def _target(**kw) -> Target:
    base = dict(lab="Zhipu", tier="B1", model_ids=("glm-5-2",),
                channels=("https://example.invalid/issues",), anomaly=None,
                interop_note=False)
    base.update(kw)
    return Target(**base)  # type: ignore[arg-type]


def _rows(model_id="glm-5-2"):
    return [{"model_id": model_id, "mean_objective": 62.9, "match_count": 2,
             "card_mean": {"ovr": 64, "base_ovr": 64, "con": 99, "GRD": 70,
                           "ADH": 99, "SYN": 0, "EFF": 64, "PRC": 58}}]


def test_render_includes_the_card_stats():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={}, run_id=94)
    for stat in ("OVR", "GRD", "ADH", "SYN", "EFF", "PRC", "CON"):
        assert stat in md
    # ovr 64 and con 99 must be read from the lowercase keys, not rendered n/a.
    assert "| 64 |" in md
    assert "n/a" not in md


def test_render_asks_the_serving_question():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={}, run_id=94)
    assert "serve" in md.lower()


def test_render_never_pitches():
    """Etiquette rule: no job or consulting ask, and no velocity claims, ever."""
    md = render_scorecard(target=_target(interop_note=True,
                                         anomaly="something anomalous"),
                          rows=_rows(),
                          checks={"glm-5-2": [FailedCheck("l", "synthesis", "5")]},
                          transcripts={"glm-5-2": None}, run_id=94).lower()
    for banned in ("hiring", "hire", "consult", "opportunit", "resume", "cv",
                   "lines of code", "commits", "portfolio of work"):
        assert banned not in md, f"banned phrase {banned!r} appeared in a card"


def test_interop_note_rendered_only_when_flagged():
    plain = render_scorecard(target=_target(interop_note=False), rows=_rows(),
                             checks={}, transcripts={}, run_id=94)
    flagged = render_scorecard(target=_target(interop_note=True), rows=_rows(),
                               checks={}, transcripts={}, run_id=94)
    assert "empty-string" not in plain
    assert "empty-string" in flagged


def test_missing_transcript_states_the_absence():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={"glm-5-2": None}, run_id=94)
    assert "not banked" in md.lower()


def test_present_transcript_is_linked():
    md = render_scorecard(target=_target(), rows=_rows(), checks={},
                          transcripts={"glm-5-2": "artifacts/arena/93/x.json"},
                          run_id=94)
    assert "artifacts/arena/93/x.json" in md
    assert "not banked" not in md.lower()


def test_missing_card_is_reported_not_fabricated():
    rows = [{"model_id": "glm-5-2", "mean_objective": 62.9, "match_count": 2,
             "card_mean": None}]
    md = render_scorecard(target=_target(), rows=rows, checks={},
                          transcripts={}, run_id=94)
    assert "not available" in md.lower()
    assert "OVR 0" not in md


def test_failed_checks_are_listed_with_axis_and_step():
    md = render_scorecard(
        target=_target(), rows=_rows(),
        checks={"glm-5-2": [FailedCheck("answer quotes 238.04", "grounding", "5")]},
        transcripts={}, run_id=94)
    assert "answer quotes 238.04" in md
    assert "grounding" in md
    assert "5" in md


def test_unknown_tier_is_rejected(tmp_path):
    bad = tmp_path / "targets.yaml"
    bad.write_text(
        "targets:\n"
        "  - lab: Nowhere\n"
        "    tier: Z\n"
        "    model_ids: [x]\n"
        "    channels: [y]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown tier"):
        load_targets(bad)
