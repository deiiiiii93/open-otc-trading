"""A binary `read_file` result must never reach the provider.

Background (measured 2026-08-29, arena run #1): deepagents' `read_file` returns
a media content block for a binary file, and three of four ZenMux routes reject
that block with a 400 when it arrives in a tool message. The rejected message
stays in the history, so the run cannot recover -- `deepseek-v4-flash-vision`
read one PDF and then made zero tool calls for the remaining five steps.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from app.services.deep_agent.binary_read_guard import BinaryReadGuardMiddleware


def _request(name: str = "read_file", **args):
    class _Req:
        tool_call = {"name": name, "args": args, "id": "call_1"}

    return _Req()


def _pdf_message(path: str = "/artifacts/uploads/confirmations/conf-09.pdf") -> ToolMessage:
    """The exact shape deepagents emits for a binary read."""
    return ToolMessage(
        content=[
            {"type": "file", "base64": "JVBERi0xLjQK", "mime_type": "application/pdf"}
        ],
        name="read_file",
        tool_call_id="call_1",
        additional_kwargs={"read_file_path": path, "read_file_media_type": "application/pdf"},
        status="success",
    )


def _run(middleware, request, result):
    return middleware.wrap_tool_call(request, lambda _req: result)


def test_binary_block_is_replaced_with_text():
    mw = BinaryReadGuardMiddleware()
    path = "/artifacts/uploads/confirmations/conf-09-amended-strike-nvda.pdf"
    out = _run(mw, _request(file_path=path), _pdf_message(path))

    assert isinstance(out.content, str), "content must be plain text, not blocks"
    assert path in out.content
    assert "application/pdf" in out.content
    # The point of the guard is recovery, so it must name the tool to use.
    assert "parse_trade_confirmation" in out.content
    assert out.status == "error", "an error status lets the agent retry differently"


def test_text_reads_are_untouched():
    """The guard must not disturb the ordinary path -- skills are read constantly."""
    mw = BinaryReadGuardMiddleware()
    original = ToolMessage(
        content="     1\t# Skill\n     2\tbody",
        name="read_file",
        tool_call_id="call_1",
        status="success",
    )
    out = _run(mw, _request(file_path="/skills/x/SKILL.md"), original)
    assert out is original


def test_empty_media_block_is_not_treated_as_a_payload():
    """A block with no bytes never poisoned anything; leave it alone."""
    mw = BinaryReadGuardMiddleware()
    original = ToolMessage(
        content=[{"type": "file", "mime_type": "application/pdf"}],
        name="read_file",
        tool_call_id="call_1",
        status="success",
    )
    out = _run(mw, _request(file_path="/x.pdf"), original)
    assert out is original


def test_non_confirmation_binary_gets_generic_guidance():
    mw = BinaryReadGuardMiddleware()
    out = _run(mw, _request(file_path="/artifacts/report-7.xlsx"), _pdf_message("/artifacts/report-7.xlsx"))
    assert "list_artifacts" in out.content


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def test_guard_registered_in_orchestrator_stack():
    from app.services.deep_agent.orchestrator import _agent_middleware

    names = _names(_agent_middleware(False, model=None, backend=object(), tools=[]))
    assert "BinaryReadGuardMiddleware" in names


def test_guard_registered_in_every_persona_stack():
    """Run #1 poisoned a persona subagent's history, not just the orchestrator's.

    A persona runs in its own checkpoint namespace, so registering the guard
    once upstream would not have saved gemini-3.7-flash's `trader` subagent.
    """
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(model=None, tools=[], skills_backend=object())
    assert specs
    for spec in specs:
        assert "BinaryReadGuardMiddleware" in _names(spec["middleware"])


def test_guard_registered_in_async_agent_stack(monkeypatch):
    import deepagents

    import app.services.async_agents.agent as agent_mod

    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured["middleware"] = kwargs["middleware"]
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", _fake_create_deep_agent)
    agent_mod.build_async_agent(
        model=MagicMock(), tools=[], checkpointer=None, task_id=1
    )
    assert "BinaryReadGuardMiddleware" in _names(captured["middleware"])


def test_general_purpose_subagent_is_ours_and_carries_the_guards():
    """deepagents' auto-added general-purpose subagent gets NONE of our middleware.

    Its stack is built inside `create_deep_agent` from a fixed list, so it is a
    fourth agent stack the "all three stacks" tests never covered — and it holds
    the parent's full toolset. On the first confirmation-desk-day board it
    issued three `read_file` calls, one of them a PDF, and was running
    unaudited. We claim the name so our guards apply.
    """
    from app.services.deep_agent.orchestrator import _general_purpose_subagent

    spec = _general_purpose_subagent(tools=[])
    assert spec["name"] == "general-purpose", "must claim the exact name to override"

    names = _names(spec["middleware"])
    assert "BinaryReadGuardMiddleware" in names
    assert "AuditTrailMiddleware" in names
    assert "ToolErrorBoundaryMiddleware" in names


def test_general_purpose_override_inherits_tools_and_interrupts():
    """Omitting `tools`/`interrupt_on` is what makes the override safe.

    deepagents resolves a caller spec with
    `spec.get("interrupt_on", interrupt_on)` and
    `spec.get("tools") if "tools" in spec else tools`, so omitting both inherits
    exactly what the auto-added agent would have received -- including the
    filesystem-permission interrupt merge. Declaring either here would silently
    narrow write gating on a subagent that can book.
    """
    from app.services.deep_agent.orchestrator import _general_purpose_subagent

    spec = _general_purpose_subagent(tools=[])
    assert "interrupt_on" not in spec, "declaring interrupt_on would narrow HITL"
    assert "tools" not in spec, "declaring tools would diverge from the parent set"


def test_orchestrator_supplies_general_purpose_so_deepagents_does_not(monkeypatch):
    """The override only works if OUR spec reaches create_deep_agent."""
    import deepagents
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage

    from app.services.deep_agent.hitl import interrupt_on_config
    from app.services.deep_agent.orchestrator import build_orchestrator

    class _FakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    captured: dict = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)

        class _Dummy:
            name = kwargs.get("name", "")

        return _Dummy()

    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create)
    assert deepagents.create_deep_agent is _fake_create

    build_orchestrator(
        model=_FakeModel(responses=[AIMessage(content="ok")]),
        tools=[],
        checkpointer=None,
        interrupt_on=interrupt_on_config(),
    )

    subagent_names = [s["name"] for s in captured["subagents"]]
    assert "general-purpose" in subagent_names, (
        "without this, create_deep_agent auto-adds an unguarded one"
    )
    gp = next(s for s in captured["subagents"] if s["name"] == "general-purpose")
    assert "BinaryReadGuardMiddleware" in _names(gp["middleware"])


def test_general_purpose_gets_the_yolo_cost_gate_like_every_persona():
    """It inherits the parent's toolset, so it can start a long-running priced run.

    The auto-added version never carried this gate. Two existing suite
    assertions walk EVERY subagent and require it; they passed before only
    because the auto-added agent was invisible to them.
    """
    from app.services.deep_agent.orchestrator import _general_purpose_subagent

    off = _names(_general_purpose_subagent(tools=[])["middleware"])
    assert "LongRunningCostHITLMiddleware" not in off

    on = _names(_general_purpose_subagent(tools=[], yolo_mode=True)["middleware"])
    assert "LongRunningCostHITLMiddleware" in on
