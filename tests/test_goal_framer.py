"""Framer output interpretation (spec §B/§C): raw LLM output -> validated
FramerResponseV1 (a parsed contract, or a needs_clarification)."""
import pytest

from app.services.deep_agent.goal_mode import (
    ClarificationResponse,
    ContractResponse,
    ContractValidationError,
    interpret_framer_output,
)


def _valid_contract() -> dict:
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Refresh risk on the Control portfolio",
        "summary": "Run risk on the named Control portfolio.",
        "domain_write_policy": "allowed_by_mode",
        "criteria": [
            {
                "id": "C1",
                "text": "Latest risk run used the Control portfolio.",
                "required": True,
                "check": {
                    "type": "ledger_predicate",
                    "tool": "get_latest_risk_run",
                    "args": {},
                    "expect": [{"path": "portfolio", "op": "eq", "value": "Control"}],
                },
            }
        ],
    }


def test_interpret_needs_clarification():
    raw = {
        "type": "needs_clarification",
        "summary": "Target portfolio is ambiguous.",
        "questions": ["Which portfolio?", "By when?"],
    }
    resp = interpret_framer_output(raw)
    assert isinstance(resp, ClarificationResponse)
    assert resp.questions == ["Which portfolio?", "By when?"]


def test_interpret_valid_contract():
    resp = interpret_framer_output(
        {"type": "contract", "contract": _valid_contract()},
        grader_tool_allowlist={"get_latest_risk_run"},
    )
    assert isinstance(resp, ContractResponse)
    assert resp.contract.domain_write_policy == "allowed_by_mode"
    assert resp.contract.criteria[0].id == "C1"


def test_interpret_rejects_invalid_contract():
    bad = _valid_contract()
    bad["criteria"][0]["check"] = {"type": "artifact_exists", "kind": "report"}
    with pytest.raises(ContractValidationError):
        interpret_framer_output({"type": "contract", "contract": bad})


def test_interpret_enforces_grader_tool_allowlist():
    with pytest.raises(ContractValidationError):
        interpret_framer_output(
            {"type": "contract", "contract": _valid_contract()},
            grader_tool_allowlist={"some_other_tool"},
        )
