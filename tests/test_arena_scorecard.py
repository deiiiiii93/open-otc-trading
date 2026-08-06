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
    load_targets,
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
