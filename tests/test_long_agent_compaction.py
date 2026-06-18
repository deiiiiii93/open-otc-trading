from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_compaction_batch_preserves_load_bearing_and_recent_messages():
    from app.services.deep_agent.compaction import select_compaction_batch

    messages = [
        HumanMessage(content="review the snowball book"),
        AIMessage(content="old reasoning"),
        ToolMessage(
            content='{"positions": []}',
            name="get_positions",
            tool_call_id="read-1",
        ),
        AIMessage(
            content="planned next step",
            additional_kwargs={"artifact_kind": "plan"},
        ),
        AIMessage(content="later reasoning"),
        ToolMessage(
            content='{"risk_run_id": 10}',
            name="run_batch_pricing",
            tool_call_id="risk-1",
        ),
        AIMessage(content="recent 1"),
        AIMessage(content="recent 2"),
        AIMessage(content="recent 3"),
        AIMessage(content="recent 4"),
        AIMessage(content="recent 5"),
        AIMessage(content="recent 6"),
    ]

    batch = select_compaction_batch(messages, keep_recent=6, max_messages=8)

    assert batch is not None
    assert (batch.start, batch.end) == (0, 3)


def test_compaction_summary_prompt_requires_artifact_and_tool_citations():
    from app.services.deep_agent.compaction import LEDGER_AWARE_SUMMARY_PROMPT

    assert "[artifact:N]" in LEDGER_AWARE_SUMMARY_PROMPT
    assert "[tool_call:id]" in LEDGER_AWARE_SUMMARY_PROMPT
    assert "/large_tool_results/" in LEDGER_AWARE_SUMMARY_PROMPT

