"""Goal-mode grader middleware builder (spec §D): a RubricMiddleware whose grader
verifies the ledger (not the transcript) under the GOAL_GRADER_READ envelope."""
from deepagents import RubricMiddleware

from app.services.deep_agent.goal_mode import (
    GOAL_GRADER_SYSTEM_PROMPT,
    GOAL_MAX_ITERATIONS,
    build_goal_grader_middleware,
    goal_grader_state,
    parse_goal_contract,
    render_goal_rubric,
)


def _valid_contract() -> dict:
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Refresh risk on Control",
        "summary": "...",
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


def test_goal_grader_state_carries_the_rendered_rubric():
    """RubricMiddleware reads `rubric` from invocation state; goal_grader_state is
    the fragment the kickoff merges into the orchestrator invoke payload."""
    contract = parse_goal_contract(_valid_contract())
    state = goal_grader_state(contract)
    assert state["rubric"] == render_goal_rubric(contract)


def test_builder_returns_configured_rubric_middleware():
    tools: list = []
    mw = build_goal_grader_middleware(model="anthropic:claude-haiku-4-5", tools=tools)
    assert isinstance(mw, RubricMiddleware)
    assert mw.max_iterations == GOAL_MAX_ITERATIONS
    assert mw._tools == tools


def test_builder_honours_explicit_max_iterations():
    mw = build_goal_grader_middleware(
        model="anthropic:claude-haiku-4-5", tools=[], max_iterations=5
    )
    assert mw.max_iterations == 5


def test_ledger_grader_prompt_distrusts_the_transcript():
    prompt = GOAL_GRADER_SYSTEM_PROMPT.lower()
    assert "ledger" in prompt
    assert "transcript" in prompt  # it tells the grader the transcript is untrusted
