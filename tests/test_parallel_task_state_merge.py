"""Two ``task()`` calls in ONE orchestrator step must not crash the turn.

deepagents' ``task`` tool returns every non-excluded key of the subagent's final
state as a ``Command`` update to the parent. Two tasks run in the same tools
superstep, so every such key reaches the parent twice; a key without a reducer
(a ``LastValue`` channel) then raises ``InvalidUpdateError`` and the whole
orchestrator turn dies after the subagents already did their work. Run #143
match thread 1132 (deepseek, high-board step 6) lost its turn this way on
``desk_context``.
"""
from __future__ import annotations

import asyncio

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.services.agents import select_deep_agent_tools
from app.services.deep_agent.desk_context import merge_scope
from app.services.deep_agent.orchestrator import build_orchestrator


class _SeqModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _task(call_id: str, description: str) -> dict:
    return {"name": "task", "id": call_id, "type": "tool_call",
            "args": {"subagent_type": "trader", "description": description}}


def test_parallel_task_fanout_completes_with_inherited_desk_context():
    model = _SeqModel(responses=[
        AIMessage(content="", tool_calls=[_task("t1", "Book A"), _task("t2", "Book B")]),
        AIMessage(content="sub done"),
        AIMessage(content="sub done"),
        AIMessage(content="Both checks are done."),
    ])
    graph = build_orchestrator(model=model, tools=select_deep_agent_tools(),
                               checkpointer=InMemorySaver())

    async def run():
        return await graph.ainvoke(
            {"messages": [HumanMessage(content="check both books")],
             # Inherited by both subagents, so both return it to the parent.
             "desk_context": {"portfolio_id": 9101}},
            config={"configurable": {"thread_id": "parallel-task"}},
        )

    state = asyncio.run(run())

    assert state["messages"][-1].content == "Both checks are done."
    assert state["desk_context"] == {"portfolio_id": 9101}


def test_merge_scope_reducer_is_last_write_wins_per_key():
    assert merge_scope({"portfolio_id": 1, "start_date": "a"},
                              {"portfolio_id": 2}) == {"portfolio_id": 2, "start_date": "a"}
    assert merge_scope(None, {"portfolio_id": 2}) == {"portfolio_id": 2}
    assert merge_scope({"portfolio_id": 1}, None) == {"portfolio_id": 1}
