"""GUARD_POLICY is desk policy that fails like code (spec §1)."""
from __future__ import annotations

import math

import pytest

from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL
from app.services.deep_agent.tool_guard_policy import (
    GUARD_POLICY, GuardPredicate, validate_policy,
)

NINE = {
    "void_settlement_cashflow", "close_position", "settle_position", "mark_knockout",
    "waive_limit_incident", "resolve_limit_incident", "delete_pricing_parameter_rows",
    "remove_portfolio_sources", "import_otc_positions",
}
NAMED = "by id, by name, or by a description that identifies it unambiguously"


def test_shipped_policy_validates_against_the_live_risk_table():
    validate_policy(GUARD_POLICY, _RISK_LEVEL_BY_TOOL)


def test_scope_is_exactly_the_nine_destroy_and_terminate_tools():
    """D1 is a user decision; widening it needs a new decision, not a drive-by."""
    assert set(GUARD_POLICY) == NINE


def test_only_the_void_wording_claims_evidence():
    for tool, predicates in GUARD_POLICY.items():
        for p in predicates:
            expected = ("tested-posthoc" if (tool, p.key) == (
                "void_settlement_cashflow", "unnamed_target") else "untested")
            assert p.evidence == expected, (tool, p.key)


def test_predicate_sets_per_tool():
    for tool, predicates in GUARD_POLICY.items():
        keys = [p.key for p in predicates]
        assert keys[0] == "unnamed_target"
        assert "from_document" in keys
        assert ("clears_blocker" in keys) == (
            tool in {"void_settlement_cashflow", "close_position", "settle_position"})
        assert all(p.threshold == 0.5 for p in predicates)


def test_record_tools_define_what_named_means():
    record_tools = NINE - {"delete_pricing_parameter_rows", "remove_portfolio_sources",
                           "import_otc_positions"}
    for tool in record_tools:
        assert NAMED in GUARD_POLICY[tool][0].instructions, tool


def _good():
    return {"void_settlement_cashflow": (GuardPredicate("k", "a crisp predicate"),)}


@pytest.mark.parametrize("policy, match", [
    ({"no_such_tool": (GuardPredicate("k", "p"),)}, "dead policy"),
    ({"book_extracted_trade": (GuardPredicate("k", "p"),)}, "already gated"),
    ({"void_settlement_cashflow": ()}, "no predicates"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p"), GuardPredicate("k", "q"))}, "duplicate"),
    ({"void_settlement_cashflow": (GuardPredicate("", "p"),)}, "empty key"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "  "),)}, "empty instructions"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=0.0),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=1.0),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=math.nan),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", evidence="tested"),)}, "evidence"),
])
def test_invalid_shapes_are_rejected(policy, match):
    with pytest.raises(ValueError, match=match):
        validate_policy(policy, _RISK_LEVEL_BY_TOOL)


def test_a_minimal_valid_policy_passes():
    validate_policy(_good(), _RISK_LEVEL_BY_TOOL)
