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


def test_frame_goal_rejects_an_unknown_response_type():
    """A malformed framer type must be rejected, not coerced to clarification."""
    model = _FakeModel({"type": "typo", "questions": ["x"]})
    with pytest.raises(ContractValidationError):
        frame_goal("do a thing", model=model)


class _CapturingModel:
    """Captures the messages frame_goal sends so we can assert the prompt content."""

    def __init__(self, payload):
        self._payload = payload
        self.seen_messages = None

    def with_structured_output(self, _schema):
        outer = self

        class _S:
            def invoke(self, messages):
                outer.seen_messages = messages
                return outer._payload

        return _S()


def test_frame_goal_prompt_carries_schema_and_allowed_tools():
    """The framer must be told the exact GoalContractV1 field names AND which read
    tools it may reference — without this the live model invents field names / tools
    and every contract 422s (found by the live e2e)."""
    model = _CapturingModel({"type": "contract", "contract": _valid_contract()})
    frame_goal(
        "Get the latest risk run onto the Control portfolio",
        model=model,
        grader_tool_allowlist={"get_latest_risk_run", "get_report"},
    )
    system = model.seen_messages[0].content
    # exact contract schema field names + check shapes are spelled out
    for token in ("schema_version", "goal_text", "criteria", "domain_write_policy",
                  "artifact_exists", "ledger_predicate", "measurable"):
        assert token in system, f"framer prompt missing {token!r}"
    # the allowed read tools are injected so the model can only name real tools
    assert "get_latest_risk_run" in system
    assert "get_report" in system


def test_frame_goal_prompt_handles_empty_allowlist():
    # A clarification payload avoids the allowlist gate so we can inspect the prompt.
    model = _CapturingModel({"type": "needs_clarification", "summary": "?", "questions": ["?"]})
    frame_goal("do a thing", model=model, grader_tool_allowlist=set())
    system = model.seen_messages[0].content
    assert "No read tools are available" in system


def test_frame_goal_wraps_structured_output_parse_errors():
    """A malformed structured output (pydantic ValidationError) must surface as
    ContractValidationError, the error callers handle to reject without a run."""
    from pydantic import ValidationError

    from app.services.deep_agent.goal_mode import GoalContractV1

    class _BadStructured:
        def invoke(self, _messages):
            GoalContractV1.model_validate({})  # raises pydantic ValidationError

    class _BadModel:
        def with_structured_output(self, _schema):
            return _BadStructured()

    with pytest.raises(ContractValidationError):
        frame_goal("x", model=_BadModel())
    # sanity: the underlying error really is a ValidationError
    assert issubclass(ValidationError, Exception)


def test_frame_goal_does_not_swallow_transport_errors():
    """An API/transport failure must propagate, not be masked as a contract error."""

    class _Structured:
        def invoke(self, _messages):
            raise RuntimeError("api unreachable")

    class _Model:
        def with_structured_output(self, _schema):
            return _Structured()

    with pytest.raises(RuntimeError):
        frame_goal("x", model=_Model())


def test_frame_goal_passes_through_clarification():
    model = _FakeModel(
        {"type": "needs_clarification", "summary": "ambiguous", "questions": ["Which portfolio?"]}
    )
    resp = frame_goal("improve risk", model=model)
    assert isinstance(resp, ClarificationResponse)
    assert resp.questions == ["Which portfolio?"]
