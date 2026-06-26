"""Framer output interpretation (spec §B/§C): raw LLM output -> validated
FramerResponseV1 (a parsed contract, or a needs_clarification)."""
import pytest

from app.services.deep_agent.goal_mode import (
    ClarificationResponse,
    ContractResponse,
    ContractValidationError,
    frame_goal,
    interpret_framer_output,
)


class _FakeStructured:
    def __init__(self, payload):
        self._payload = payload

    def invoke(self, _messages):
        return self._payload


class _FakeModel:
    """Stands in for a chat model with structured output."""

    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, _schema):
        return _FakeStructured(self._payload)


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


def test_frame_goal_runs_model_contract_through_the_gate():
    model = _FakeModel({"type": "contract", "contract": _valid_contract()})
    resp = frame_goal(
        "Get the latest risk run onto the Control portfolio",
        model=model,
        grader_tool_allowlist={"get_latest_risk_run"},
    )
    assert isinstance(resp, ContractResponse)
    assert resp.contract.criteria[0].id == "C1"


def test_frame_goal_rejects_an_invalid_contract_from_the_model():
    bad = _valid_contract()
    bad["criteria"][0]["check"] = {"type": "artifact_exists", "kind": "report"}
    model = _FakeModel({"type": "contract", "contract": bad})
    with pytest.raises(ContractValidationError):
        frame_goal("do a thing", model=model)


def test_frame_goal_passes_through_clarification():
    model = _FakeModel(
        {"type": "needs_clarification", "summary": "ambiguous", "questions": ["Which portfolio?"]}
    )
    resp = frame_goal("improve risk", model=model)
    assert isinstance(resp, ClarificationResponse)
    assert resp.questions == ["Which portfolio?"]
