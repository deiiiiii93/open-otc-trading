"""Unit tests for TermGroundingMiddleware - one-shot ungrounded-verdict nudge."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.deep_agent.term_grounding import (
    NUDGE_MARKER,
    TermGroundingMiddleware,
)

MW = TermGroundingMiddleware()

VERDICT = (
    "The SingleSharkfinOption row is not complete enough to price - missing "
    "the knock-in level and coupon."
)


def _state(messages: list) -> dict:
    return {"messages": messages}


def test_ungrounded_verdict_is_nudged_once() -> None:
    result = MW.after_model(
        _state([HumanMessage(content="is this sharkfin complete?"),
                AIMessage(content=VERDICT)]),
        None,
    )
    assert result is not None
    assert result["jump_to"] == "model"
    assert NUDGE_MARKER in result["messages"][0].content


def test_no_second_nudge_in_same_turn() -> None:
    result = MW.after_model(
        _state([
            HumanMessage(content="is this sharkfin complete?"),
            AIMessage(content=VERDICT),
            HumanMessage(content=f"{NUDGE_MARKER} verify first"),
            AIMessage(content=VERDICT),
        ]),
        None,
    )
    assert result is None


def test_grounded_verdict_passes_through() -> None:
    result = MW.after_model(
        _state([
            HumanMessage(content="is this sharkfin complete?"),
            AIMessage(content="", tool_calls=[
                {"name": "check_term_completeness", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"missing_required": ["barrier"]}',
                        name="check_term_completeness", tool_call_id="c1"),
            AIMessage(content="Not complete: the sharkfin is missing the barrier term."),
        ]),
        None,
    )
    assert result is None


def test_non_final_answer_is_ignored() -> None:
    result = MW.after_model(
        _state([HumanMessage(content="q"),
                AIMessage(content=VERDICT, tool_calls=[
                    {"name": "task", "args": {}, "id": "t1"}])]),
        None,
    )
    assert result is None


def test_non_product_prose_is_ignored() -> None:
    result = MW.after_model(
        _state([HumanMessage(content="status?"),
                AIMessage(content="The report is incomplete; two sections are missing.")]),
        None,
    )
    assert result is None


def test_grounding_resets_after_new_user_message() -> None:
    # A nudge in a PREVIOUS turn must not suppress protection in a new turn.
    result = MW.after_model(
        _state([
            HumanMessage(content="first question"),
            HumanMessage(content=f"{NUDGE_MARKER} verify first"),
            AIMessage(content="grounded answer"),
            HumanMessage(content="new sharkfin question"),
            AIMessage(content=VERDICT),
        ]),
        None,
    )
    assert result is not None and result["jump_to"] == "model"
