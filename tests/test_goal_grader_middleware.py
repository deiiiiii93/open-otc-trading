"""Goal-mode grader middleware builder (spec §D): a RubricMiddleware whose grader
verifies the ledger (not the transcript) under the GOAL_GRADER_READ envelope."""
from deepagents import RubricMiddleware

from app.services.deep_agent.goal_mode import (
    GOAL_GRADER_SYSTEM_PROMPT,
    GOAL_MAX_ITERATIONS,
    build_goal_grader_middleware,
)


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
