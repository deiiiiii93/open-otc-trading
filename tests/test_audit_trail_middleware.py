"""AuditTrailMiddleware: fail-closed capture at wrap_tool_call (audit spec §5.2)."""
import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

from app.models import AgentActionAudit
from app.services import audit_trail
from app.services.deep_agent.audit_trail_middleware import AuditTrailMiddleware
from app.services.deep_agent.capability_gate import CapabilityDeniedError
from app.services.deep_agent.envelopes import Envelope, ToolGroup


class _FakeTool:
    def __init__(self, name, group=None):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


class _Req:
    def __init__(self, name, args=None, call_id="tc1"):
        self.tool_call = {"name": name, "args": args or {}, "id": call_id}


TOOLS = [_FakeTool("book_position", ToolGroup.DOMAIN_WRITE), _FakeTool("list_positions")]


def _mw():
    return AuditTrailMiddleware(tools=TOOLS)


def test_read_tool_passes_through_no_rows(session):
    called = []
    result = _mw().wrap_tool_call(_Req("list_positions"), lambda r: called.append(r) or "ok")
    assert result == "ok" and called
    assert session.query(AgentActionAudit).count() == 0


def test_write_tool_attempt_before_handler_then_ok(session):
    order = []

    def handler(request):
        order.append(
            session.query(AgentActionAudit).filter_by(status="attempted").count()
        )
        return ToolMessage(content="booked", tool_call_id="tc1", name="book_position")

    _mw().wrap_tool_call(_Req("book_position", {"qty": 1}), handler)
    assert order == [1]  # attempted row committed BEFORE the handler ran
    session.expire_all()
    row = session.query(AgentActionAudit).one()
    assert row.status == "ok"
    assert row.tool_class == "domain_write"
    assert row.result_preview == "booked"


def test_error_toolmessage_recorded_as_error(session):
    msg = ToolMessage(
        content="Error: boom", tool_call_id="tc1", name="book_position", status="error"
    )
    _mw().wrap_tool_call(_Req("book_position"), lambda r: msg)
    row = session.query(AgentActionAudit).one()
    assert row.status == "error"
    assert "boom" in row.error


def test_fanout_deny_toolmessage_recorded_as_denied(session):
    msg = ToolMessage(
        content=(
            "Fanned-out subagents are read-only: `book_position` writes (or dispatches "
            "async work), so it is blocked in a dynamic-subagents fan-out."
        ),
        tool_call_id="tc1",
        name="book_position",
        status="error",
    )
    _mw().wrap_tool_call(_Req("book_position"), lambda r: msg)
    row = session.query(AgentActionAudit).one()
    assert row.status == "denied"
    assert row.deny_reason == "fanout_readonly"


def test_capability_denied_recorded_and_reraised(session):
    def handler(request):
        raise CapabilityDeniedError(
            envelope=Envelope.PET_PAGE,
            group=ToolGroup.DOMAIN_WRITE,
            tool_name="book_position",
        )

    with pytest.raises(CapabilityDeniedError):
        _mw().wrap_tool_call(_Req("book_position"), handler)
    row = session.query(AgentActionAudit).one()
    assert row.status == "denied"
    assert row.deny_reason == "capability"


def test_interrupt_recorded_and_reraised(session):
    def handler(request):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        _mw().wrap_tool_call(_Req("book_position"), handler)
    assert session.query(AgentActionAudit).one().status == "interrupted"


def test_raw_exception_recorded_and_reraised(session):
    def handler(request):
        raise ValueError("bad terms")

    with pytest.raises(ValueError):
        _mw().wrap_tool_call(_Req("book_position"), handler)
    row = session.query(AgentActionAudit).one()
    assert row.status == "error"
    assert "bad terms" in row.error


def test_fail_closed_refusal_blocks_handler(session, monkeypatch):
    def _unavailable(**kwargs):
        raise audit_trail.AuditUnavailableError("down")

    monkeypatch.setattr(
        "app.services.deep_agent.audit_trail_middleware.record_attempt", _unavailable
    )
    called = []
    result = _mw().wrap_tool_call(_Req("book_position"), lambda r: called.append(1) or "x")
    assert not called  # business tool NEVER executed
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "audit trail unavailable" in result.content.lower()
    # the refusal itself is recorded (best-effort row)
    session.expire_all()
    assert session.query(AgentActionAudit).filter_by(status="refused").count() == 1


@pytest.mark.asyncio
async def test_awrap_tool_call_ok(session):
    async def handler(request):
        return ToolMessage(content="ok", tool_call_id="tc1", name="book_position")

    await _mw().awrap_tool_call(_Req("book_position"), handler)
    session.expire_all()
    assert session.query(AgentActionAudit).one().status == "ok"
