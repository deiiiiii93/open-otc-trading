"""Goal-mode criteria contract (spec §C) — the trust hinge.

TDD for backend/app/services/deep_agent/goal_mode.py.
"""
import pytest

from app.services.deep_agent.goal_mode import (
    ContractValidationError,
    goal_contract_hash,
    parse_goal_contract,
    render_goal_rubric,
)


def _valid_write_contract() -> dict:
    """A well-formed allowed_by_mode contract with a named-target predicate."""
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Get the latest risk run onto the Control portfolio",
        "summary": "Refresh risk on the named Control portfolio.",
        "domain_write_policy": "allowed_by_mode",
        "criteria": [
            {
                "id": "C1",
                "text": "The latest risk run used the Control portfolio and Control Profile.",
                "required": True,
                "check": {
                    "type": "ledger_predicate",
                    "tool": "get_latest_risk_run",
                    "args": {},
                    "expect": [
                        {"path": "portfolio", "op": "eq", "value": "Control"},
                        {"path": "profile", "op": "eq", "value": "Control Profile"},
                    ],
                },
            },
            {
                "id": "C2",
                "text": "A report artifact exists.",
                "required": True,
                "check": {"type": "artifact_exists", "kind": "report"},
            },
        ],
    }


def _artifact_only_allowed_by_mode() -> dict:
    """An allowed_by_mode contract whose only criterion is artifact existence."""
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Produce the control-day risk report",
        "summary": "Refresh risk and write the report.",
        "domain_write_policy": "allowed_by_mode",
        "criteria": [
            {
                "id": "C1",
                "text": "A report artifact exists",
                "required": True,
                "check": {"type": "artifact_exists", "kind": "report"},
            }
        ],
    }


def test_allowed_by_mode_contract_requires_an_end_state_predicate():
    """artifact existence is gameable; a write-capable goal must verify an
    end-state via ledger_predicate or measurable (spec §C validation)."""
    with pytest.raises(ContractValidationError, match="end-state"):
        parse_goal_contract(_artifact_only_allowed_by_mode())


def test_render_goal_rubric_lists_every_criterion_deterministically():
    contract = parse_goal_contract(_valid_write_contract())
    rubric = render_goal_rubric(contract)
    assert rubric == render_goal_rubric(contract)  # deterministic
    lines = [ln for ln in rubric.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert "Control Profile" in rubric  # criterion text surfaced
    assert "report artifact exists" in rubric


def test_render_goal_rubric_surfaces_the_verifying_tool_for_predicates():
    """The grader only sees the rubric string, so a predicate criterion must
    name its verifying tool for the grader to act on it."""
    rubric = render_goal_rubric(parse_goal_contract(_valid_write_contract()))
    assert "get_latest_risk_run" in rubric


def test_goal_contract_hash_is_stable_and_content_sensitive():
    h1 = goal_contract_hash(parse_goal_contract(_valid_write_contract()))
    h2 = goal_contract_hash(parse_goal_contract(_valid_write_contract()))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
    drifted = _valid_write_contract()
    drifted["criteria"][0]["check"]["expect"][0]["value"] = "Default"
    assert goal_contract_hash(parse_goal_contract(drifted)) != h1


def test_forbidden_contract_may_be_artifact_only():
    """The end-state rule is write-capable-only; a read-only/advisory goal may
    accept on artifact existence alone."""
    data = _artifact_only_allowed_by_mode()
    data["domain_write_policy"] = "forbidden"
    contract = parse_goal_contract(data)  # must not raise
    assert contract.domain_write_policy == "forbidden"


def test_contract_rejects_empty_and_oversized_criteria():
    empty = _valid_write_contract()
    empty["criteria"] = []
    with pytest.raises(ContractValidationError):
        parse_goal_contract(empty)

    oversized = _valid_write_contract()
    one = oversized["criteria"][1]
    oversized["criteria"] = [dict(one, id=f"C{i}") for i in range(11)]
    with pytest.raises(ContractValidationError):
        parse_goal_contract(oversized)
