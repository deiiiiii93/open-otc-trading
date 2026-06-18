"""Runtime-layer tool-error policy for the deep-agent graph.

deepagents/langchain build the ToolNode internally with the default
``handle_tool_errors`` policy, which only converts pydantic *argument* validation
errors (``ToolInvocationError``) into a recoverable ``ToolMessage`` and re-raises
everything else. A domain ``ValueError`` raised inside a tool body (e.g. the
Snowball booking gate's "missing: maturity_years, …") therefore crashes the whole
orchestrator resume instead of reaching the LLM.

``ToolErrorBoundaryMiddleware`` is the runtime-layer fix: a ``wrap_tool_call``
hook that turns tool-body exceptions into error ``ToolMessage``s so the agent can
recover — while still letting control-flow signals (HITL interrupts) propagate.
"""
from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp, GraphInterrupt
from langgraph.types import Interrupt

from app.services.deep_agent.capability_gate import CapabilityDeniedError
from app.services.deep_agent.envelopes import Envelope, ToolGroup
from app.services.deep_agent.orchestrator import _agent_middleware
from app.services.deep_agent.personas import all_personas
from app.services.deep_agent.tool_error_boundary import ToolErrorBoundaryMiddleware


def _request(*, name: str = "book_position", call_id: str = "call_1") -> ToolCallRequest:
    """A minimal-but-real ToolCallRequest; the boundary only reads ``tool_call``."""
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


def test_tool_body_valueerror_becomes_error_tool_message():
    """A domain ValueError raised inside the tool must come back as an error
    ToolMessage (so the LLM sees the precise message), not propagate."""
    mw = ToolErrorBoundaryMiddleware()

    def handler(_request: ToolCallRequest) -> ToolMessage:
        raise ValueError(
            "Incomplete SnowballOption booking terms; missing: maturity_years"
        )

    result = mw.wrap_tool_call(_request(call_id="call_42"), handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_42"
    assert result.name == "book_position"
    assert "maturity_years" in result.content


def test_graph_interrupt_propagates():
    """HITL interrupts ride on GraphBubbleUp and MUST NOT be swallowed — otherwise
    cost-preview / approval interrupts would silently turn into error messages."""
    mw = ToolErrorBoundaryMiddleware()

    def handler(_request: ToolCallRequest) -> ToolMessage:
        raise GraphInterrupt((Interrupt(value="confirm cost"),))

    with pytest.raises(GraphBubbleUp):
        mw.wrap_tool_call(_request(), handler)


def test_capability_denial_propagates():
    """Envelope denials are runtime control-flow, not ordinary tool failures.

    If the boundary converts them into ToolMessages, the persona keeps working
    under the denied envelope and emits blocker prose before the runtime can
    widen the turn.
    """
    mw = ToolErrorBoundaryMiddleware()

    def handler(_request: ToolCallRequest) -> ToolMessage:
        raise CapabilityDeniedError(
            envelope=Envelope.PET_PAGE,
            group=ToolGroup.DOMAIN_READ,
            tool_name="get_latest_risk_run",
        )

    with pytest.raises(CapabilityDeniedError):
        mw.wrap_tool_call(_request(name="get_latest_risk_run"), handler)


def test_successful_tool_call_passes_through():
    """The happy path must be untouched: a normal ToolMessage is returned as-is."""
    mw = ToolErrorBoundaryMiddleware()
    ok = ToolMessage(content="done", name="book_position", tool_call_id="call_1")

    result = mw.wrap_tool_call(_request(), lambda _req: ok)

    assert result is ok


def test_personas_install_boundary_outermost():
    """Every persona subagent is the inner ToolNode where domain tools (e.g.
    book_position) run and where the crash originated — the boundary must be
    wired there, and outermost so it wraps the other persona middleware too."""
    specs = all_personas(model=None, tools=[], skills_backend=object(), yolo_mode=False)

    assert specs
    for spec in specs:
        middleware = spec.get("middleware", [])
        assert middleware, f"{spec['name']} has no middleware"
        assert isinstance(middleware[0], ToolErrorBoundaryMiddleware), (
            f"{spec['name']} boundary not outermost: "
            f"{[type(m).__name__ for m in middleware]}"
        )


def test_orchestrator_installs_boundary_outermost():
    """Defense-in-depth: the orchestrator (task dispatch + propose_reply_options)
    also carries the boundary as its outermost middleware."""
    middleware = _agent_middleware(
        False, model=None, backend=object(), tools=[], yolo_mode=False
    )

    assert middleware
    assert isinstance(middleware[0], ToolErrorBoundaryMiddleware), (
        f"boundary not outermost: {[type(m).__name__ for m in middleware]}"
    )


@pytest.mark.asyncio
async def test_async_tool_body_valueerror_becomes_error_tool_message():
    """Async invocation paths (ainvoke/astream) get the same protection."""
    mw = ToolErrorBoundaryMiddleware()

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise ValueError("boom")

    result = await mw.awrap_tool_call(_request(call_id="call_7"), handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_7"
    assert "boom" in result.content


@pytest.mark.asyncio
async def test_async_capability_denial_propagates():
    mw = ToolErrorBoundaryMiddleware()

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise CapabilityDeniedError(
            envelope=Envelope.PET_PAGE,
            group=ToolGroup.DOMAIN_READ,
            tool_name="get_latest_risk_run",
        )

    with pytest.raises(CapabilityDeniedError):
        await mw.awrap_tool_call(_request(name="get_latest_risk_run"), handler)
