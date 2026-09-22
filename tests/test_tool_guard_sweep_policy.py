"""SWEEP_POLICY: candidate over-execution predicates, never read by the live guard (D2)."""
from __future__ import annotations

import math

import pytest

from app.services.deep_agent import tool_guard_policy as policy
from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL
from app.services.deep_agent.tool_guard_policy import (
    EVIDENCE_LEVELS, GUARD_POLICY, SWEEP_POLICY, GuardPredicate, policy_for, policy_sha256,
    swept_tools, validate_sweep_policy,
)

GROUPS = {
    "settlement": {"generate_settlement_cashflows", "update_settlement_cashflow",
                   "release_settlement_cashflow", "resync_settlement_cashflow",
                   "generate_settlement_notice", "settle_settlement_cashflow"},
    "rfq": {"create_or_update_rfq_draft", "quote_rfq", "submit_rfq_for_approval", "approve_rfq",
            "reject_rfq", "release_rfq", "mark_rfq_client_accepted", "book_rfq_to_position"},
    "lifecycle": {"record_lifecycle_event", "cancel_lifecycle_event"},
    "booking": {"book_position", "book_hedge", "book_extracted_trade"},
}
CREATION = {"create_or_update_rfq_draft", "book_position", "book_hedge", "book_extracted_trade"}
KEYS = ["beyond_named_scope", "repeats_completed_action", "from_document", "clears_blocker"]
NAMED = "by id, by name, or by a description that identifies it unambiguously"


def test_shipped_sweep_policy_validates_against_the_live_risk_table():
    validate_sweep_policy(SWEEP_POLICY, _RISK_LEVEL_BY_TOOL)


def test_scope_is_exactly_the_nineteen_money_path_tools():
    """D2 is a user decision; widening it is a data edit that cites a new select (F7)."""
    assert set(SWEEP_POLICY) == set().union(*GROUPS.values())
    assert len(SWEEP_POLICY) == 19


def test_every_tool_asks_the_family_untested_at_one_half():
    for tool, predicates in SWEEP_POLICY.items():
        assert [p.key for p in predicates] == KEYS, tool
        assert all(p.evidence == "untested" and p.threshold == 0.5 for p in predicates), tool


def test_record_tools_define_what_named_means():
    for tool in set(SWEEP_POLICY) - CREATION:
        wording = SWEEP_POLICY[tool][0].instructions
        assert NAMED in wording, tool
        assert "not a necessary step of what the user asked for" in wording, tool


def test_wordings_are_written_per_tool():
    """The words are the policy: no two tools share a sentence."""
    for index in (0, 1):
        sentences = [preds[index].instructions for preds in SWEEP_POLICY.values()]
        assert len(set(sentences)) == len(sentences)


def test_shared_predicates_are_the_guards_own_constants():
    void = {p.key: p for p in GUARD_POLICY["void_settlement_cashflow"]}
    for predicates in SWEEP_POLICY.values():
        assert predicates[2] == void["from_document"]
        assert predicates[3] == void["clears_blocker"]


def test_tables_are_disjoint_and_the_sweep_covers_both():
    assert not set(SWEEP_POLICY) & set(GUARD_POLICY)
    assert swept_tools() == frozenset(GUARD_POLICY) | frozenset(SWEEP_POLICY)
    assert policy_for("void_settlement_cashflow") is GUARD_POLICY["void_settlement_cashflow"]
    assert policy_for("quote_rfq") is SWEEP_POLICY["quote_rfq"]
    assert policy_for("create_report") is None


def test_heldout_is_an_evidence_level():
    assert EVIDENCE_LEVELS == {"tested-heldout", "tested-posthoc", "untested"}


def test_irreversible_tools_are_allowed_in_the_sweep():
    validate_sweep_policy({"book_position": (GuardPredicate("k", "p"),)}, _RISK_LEVEL_BY_TOOL)


@pytest.mark.parametrize("bad, match", [
    ({"void_settlement_cashflow": (GuardPredicate("k", "p"),)}, "disjoint"),
    ({"run_python": (GuardPredicate("k", "p"),)}, "only 'write' and 'irreversible'"),
    ({"no_such_tool": (GuardPredicate("k", "p"),)}, "dead policy"),
    ({"quote_rfq": ()}, "no predicates"),
    ({"quote_rfq": (GuardPredicate("k", "p"), GuardPredicate("k", "q"))}, "duplicate"),
    ({"quote_rfq": (GuardPredicate("k", "p", threshold=0.0),)}, "threshold"),
    ({"quote_rfq": (GuardPredicate("k", "p", threshold=1.0),)}, "threshold"),
    ({"quote_rfq": (GuardPredicate("k", "p", threshold=math.nan),)}, "threshold"),
    ({"quote_rfq": (GuardPredicate("k", "p", evidence="tested"),)}, "evidence"),
])
def test_invalid_sweep_shapes_are_rejected(bad, match):
    with pytest.raises(ValueError, match=match):
        validate_sweep_policy(bad, _RISK_LEVEL_BY_TOOL)


def test_policy_identity_is_stable_and_moves_with_any_wording(monkeypatch):
    first = policy_sha256()
    assert first == policy_sha256() and len(first) == 64
    moved = (GuardPredicate("beyond_named_scope", "a different sentence"),
             *SWEEP_POLICY["quote_rfq"][1:])
    monkeypatch.setitem(policy.SWEEP_POLICY, "quote_rfq", moved)
    assert policy_sha256() != first
