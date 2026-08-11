"""Tool registration, gating, and the approval-card summary."""
from __future__ import annotations

_WRITE_TOOLS = {
    "generate_settlement_cashflows",
    "update_settlement_cashflow",
    "release_settlement_cashflow",
    "unrelease_settlement_cashflow",
    "block_settlement_cashflow",
    "unblock_settlement_cashflow",
    "void_settlement_cashflow",
    "resync_settlement_cashflow",
    "settle_settlement_cashflow",
    "generate_settlement_notice",
}
_READ_TOOLS = {
    "get_settlement_cashflows",
    "get_settlement_cashflow",
    "get_settlement_summary",
}
_ALL_TOOLS = _WRITE_TOOLS | _READ_TOOLS


def test_every_settlement_tool_is_in_quant_agent_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert _ALL_TOOLS <= names


def test_every_settlement_tool_is_allowlisted_for_deep_agents():
    """Registered but not allowlisted means silently dropped from every
    persona's toolset — the assemble_breach_report failure mode."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert _ALL_TOOLS <= DEEP_AGENT_TOOL_NAMES


def test_every_write_tool_is_gated():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    for name in _WRITE_TOOLS:
        assert name in INTERRUPT_TOOL_NAMES, f"{name} is not in the interrupt map"
        assert name in _RISK_LEVEL_BY_TOOL, f"{name} has no risk level"
        assert name in _LABEL_BY_TOOL, f"{name} has no label"


def test_no_read_tool_is_gated():
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert not (_READ_TOOLS & set(INTERRUPT_TOOL_NAMES))


def test_only_settle_is_irreversible():
    """Desk decision: release is recallable (unrelease exists, block is
    reachable from released), so only the unrecallable assertion that money
    moved is hard-gated against AUTO mode."""
    from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL

    assert _RISK_LEVEL_BY_TOOL["settle_settlement_cashflow"] == "irreversible"
    for name in _WRITE_TOOLS - {"settle_settlement_cashflow"}:
        assert _RISK_LEVEL_BY_TOOL[name] == "write"


def test_settle_has_a_summary_builder():
    """A gated tool whose args are bare ids needs a preflight summary, or the
    approval card asks a human to approve two integers."""
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    assert "settle_settlement_cashflow" in _SUMMARY_BUILDERS


def test_summary_builder_states_the_money(session, settings):
    from app.models import (
        Portfolio,
        Position,
        PositionLifecycleEvent,
        SettlementCashflow,
    )
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    portfolio = Portfolio(name="Summary Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        product_type="SnowballOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle", event_data={}
    )
    session.add(event)
    session.flush()
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id,
        leg_key="settlement",
        position_id=position.id,
        currency="USD",
        direction="pay",
        amount=1234.56,
        counterparty="Acme Capital",
        status="released",
    )
    session.add(cashflow)
    session.commit()

    text = _SUMMARY_BUILDERS["settle_settlement_cashflow"](
        {"cashflow_id": cashflow.id, "expected_row_version": 1}
    )
    assert "1,234.56" in text
    assert "USD" in text
    assert "Acme Capital" in text


def test_summary_builder_never_raises_on_a_missing_row():
    from app.services.deep_agent.hitl import _SUMMARY_BUILDERS

    text = _SUMMARY_BUILDERS["settle_settlement_cashflow"]({"cashflow_id": 999999})
    assert isinstance(text, str) and text


def test_write_tools_are_domain_write_capability():
    from app.services.deep_agent.envelopes import ToolGroup
    from app.tools import QUANT_AGENT_TOOLS

    by_name = {t.name: t for t in QUANT_AGENT_TOOLS}
    for name in _WRITE_TOOLS:
        assert by_name[name].__capability_group__ is ToolGroup.DOMAIN_WRITE, (
            f"{name} must be DOMAIN_WRITE — the audit taxonomy and "
            f"FanoutReadOnlyMiddleware both key off it"
        )


def test_read_tools_are_domain_read_capability():
    from app.services.deep_agent.envelopes import ToolGroup
    from app.tools import QUANT_AGENT_TOOLS

    by_name = {t.name: t for t in QUANT_AGENT_TOOLS}
    for name in _READ_TOOLS:
        assert by_name[name].__capability_group__ is ToolGroup.DOMAIN_READ


def test_the_agent_surface_covers_every_mutating_rest_route():
    """The two surfaces must not drift: anything a user can do over HTTP, an
    agent can do with a tool.

    Derived from the REAL router, not a literal — add a mutating route without
    a tool and this fails, which is the whole point. `get_db` is a closure
    inside create_app, so a stand-in is passed; only route metadata is read.
    """
    from app.routers.settlement import build_settlement_router
    from app.tools import QUANT_AGENT_TOOLS

    router = build_settlement_router(get_db=lambda: iter(()))
    mutating_paths = {
        route.path
        for route in router.routes
        if set(getattr(route, "methods", set()) or set())
        & {"POST", "PATCH", "PUT", "DELETE"}
    }

    route_to_tool = {
        "/api/settlement/cashflows/generate": "generate_settlement_cashflows",
        "/api/settlement/cashflows/refresh": "generate_settlement_cashflows",
        "/api/settlement/cashflows/{cashflow_id}": "update_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/release":
            "release_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/unrelease":
            "unrelease_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/block":
            "block_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/unblock":
            "unblock_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/settle":
            "settle_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/void":
            "void_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/resync":
            "resync_settlement_cashflow",
        "/api/settlement/cashflows/{cashflow_id}/notice":
            "generate_settlement_notice",
    }

    assert mutating_paths == set(route_to_tool), (
        "a mutating REST route has no declared tool counterpart (or vice versa)"
    )
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert set(route_to_tool.values()) <= names


def test_release_tool_runs_end_to_end_and_reports_conflict(session, settings):
    """The tool layer must surface a version conflict as structured data the
    model can act on, not as an exception."""
    from app.models import (
        Portfolio,
        Position,
        PositionLifecycleEvent,
        SettlementCashflow,
    )
    from app.tools.settlement import release_settlement_cashflow_tool

    portfolio = Portfolio(name="Tool Book")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id, underlying="AAPL",
        product_type="SnowballOption", product_kwargs={},
        quantity=1.0, entry_price=0.0, currency="USD",
    )
    session.add(position)
    session.flush()
    event = PositionLifecycleEvent(
        position_id=position.id, event_type="settle", event_data={}
    )
    session.add(event)
    session.flush()
    cashflow = SettlementCashflow(
        lifecycle_event_id=event.id, leg_key="settlement",
        position_id=position.id, currency="USD", direction="pay",
        amount=10.0, status="pending",
    )
    session.add(cashflow)
    session.commit()

    ok = release_settlement_cashflow_tool.invoke(
        {"cashflow_id": cashflow.id, "expected_row_version": 1}
    )
    assert ok["ok"] is True
    assert ok["status"] == "released"

    conflict = release_settlement_cashflow_tool.invoke(
        {"cashflow_id": cashflow.id, "expected_row_version": 1}
    )
    assert conflict["ok"] is False
    assert conflict["error"] in {"conflict", "invalid"}
