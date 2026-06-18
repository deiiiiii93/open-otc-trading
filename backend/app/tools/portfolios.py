"""@tool wrappers for the portfolios domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/portfolios,
shape JSON. The wire shape is preserved from the legacy ``langchain_tools.py``
(``ok``/``data``/``errors``/``cycle_path``) so the existing agent test suite
continues to exercise this layer without modification.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.models import (
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKindError,
    PortfolioNameConflict,
    RuleCompilationError,
    RuleValidationError,
)
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import portfolios as portfolios_svc

from ._shaping import portfolio_error_response, shape_portfolio


class _ListInput(BaseModel):
    kind: Literal["container", "view"] | None = None
    tags: list[str] | None = None


class _GetInput(BaseModel):
    portfolio_id: int


class _CreateInput(BaseModel):
    name: str
    kind: Literal["container", "view"] = Field(
        default="container",
        description="container explicitly holds positions; view derives "
        "membership from filter_rule/sources and recomputes on query.",
    )
    base_currency: str = Field(
        default="CNY",
        description="ISO-4217; desk convention is CNY.",
    )
    description: str | None = None
    filter_rule: dict[str, Any] | None = Field(
        default=None,
        description="View rule DSL — leaf {op, field, value}; composites "
        "{op: and|or, children: [...]}, {op: not, child: {...}}; ops eq/ne, "
        "in/not_in, lt/lte/gt/gte/between over fields product_type, "
        "underlying, status, mapping_status, engine_name, quantity, "
        "entry_price, created_at. See /skills/references/portfolios/model.md.",
    )
    manual_include_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(
        default_factory=list,
        description="View-only: other portfolios whose resolved positions "
        "feed this view (cycle/depth-checked).",
    )
    tags: list[str] = Field(default_factory=list)


class _UpdateInput(BaseModel):
    portfolio_id: int
    name: str | None = None
    description: str | None = None
    base_currency: str | None = None
    tags: list[str] | None = None


class _DeleteInput(BaseModel):
    portfolio_id: int


class _SetRuleInput(BaseModel):
    portfolio_id: int
    filter_rule: dict[str, Any] | None = None


class _PortfolioIdsInput(BaseModel):
    portfolio_id: int
    position_ids: list[int]


class _PortfolioSourcesInput(BaseModel):
    portfolio_id: int
    source_portfolio_ids: list[int]


_PORTFOLIO_ERRORS = (
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKindError,
    PortfolioNameConflict,
    RuleCompilationError,
    RuleValidationError,
)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_portfolios", args_schema=_ListInput)
def list_portfolios_tool(
    kind: str | None = None, tags: list[str] | None = None
) -> dict[str, Any]:
    """List portfolios with optional kind and tag filters."""
    rows = portfolios_svc.list_all(kind=kind)
    if tags:
        wanted = {t.lower() for t in tags}
        rows = [p for p in rows if wanted.issubset(set(p.tags or []))]
    return {"ok": True, "data": [shape_portfolio(p) for p in rows]}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_portfolio", args_schema=_GetInput)
def get_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Return portfolio detail including resolved positions for views."""
    portfolio = portfolios_svc.get(portfolio_id=portfolio_id)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    try:
        ids = portfolios_svc.preview_membership(portfolio_id=portfolio_id)
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    body = shape_portfolio(portfolio) | {"resolved_position_ids": ids}
    return {"ok": True, "data": body}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("create_portfolio", args_schema=_CreateInput)
def create_portfolio_tool(
    name: str,
    kind: str = "container",
    base_currency: str = "CNY",
    description: str | None = None,
    filter_rule: dict[str, Any] | None = None,
    manual_include_ids: list[int] | None = None,
    source_portfolio_ids: list[int] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create a portfolio (container or view). Returns the created record."""
    try:
        portfolio = portfolios_svc.create(
            name=name,
            kind=kind,
            base_currency=base_currency,
            description=description,
            filter_rule=filter_rule,
            manual_include_ids=manual_include_ids or [],
            source_portfolio_ids=source_portfolio_ids or [],
            tags=tags or [],
        )
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    return {"ok": True, "data": shape_portfolio(portfolio)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("update_portfolio", args_schema=_UpdateInput)
def update_portfolio_tool(
    portfolio_id: int,
    name: str | None = None,
    description: str | None = None,
    base_currency: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update portfolio fields (name/description/base_currency/tags)."""
    fields = {
        "name": name,
        "description": description,
        "base_currency": base_currency,
        "tags": tags,
    }
    try:
        portfolio = portfolios_svc.update(portfolio_id=portfolio_id, fields=fields)
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    return {"ok": True, "data": shape_portfolio(portfolio)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("delete_portfolio", args_schema=_DeleteInput)
def delete_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Delete a portfolio. Container kind cascades positions; view leaves them. HITL-gated."""
    deleted = portfolios_svc.delete(portfolio_id=portfolio_id)
    if not deleted:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    return {"ok": True, "data": {"deleted": True, "id": portfolio_id}}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("set_portfolio_rule", args_schema=_SetRuleInput)
def set_portfolio_rule_tool(
    portfolio_id: int, filter_rule: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Replace the filter rule on a view portfolio. HITL-gated."""
    try:
        portfolio = portfolios_svc.set_rule(
            portfolio_id=portfolio_id, filter_rule=filter_rule
        )
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    return {"ok": True, "data": shape_portfolio(portfolio)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("add_positions_to_portfolio", args_schema=_PortfolioIdsInput)
def add_positions_to_portfolio_tool(
    portfolio_id: int, position_ids: list[int]
) -> dict[str, Any]:
    """Add positions to a portfolio. View: append manual_include_ids.
    Container: caller must pass full PortfolioPositionSpec dicts via the HTTP API.
    """
    portfolio = portfolios_svc.get(portfolio_id=portfolio_id)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    if portfolio.kind != "view":
        return {
            "ok": False,
            "error": "Container portfolios add positions via /api/portfolios/{id}/positions; "
            "the agent should pass full position specs through that endpoint.",
        }
    try:
        portfolio = portfolios_svc.add_member_positions(
            portfolio_id=portfolio_id, position_ids=position_ids
        )
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    return {
        "ok": True,
        "data": shape_portfolio(portfolio),
        "kind_resolved_as": "view",
    }


def _remove_view_positions(portfolio_id: int, position_ids: list[int]) -> dict[str, Any]:
    portfolio = portfolios_svc.remove_member_positions(
        portfolio_id=portfolio_id, position_ids=position_ids
    )
    return {
        "ok": True,
        "data": shape_portfolio(portfolio),
        "kind_resolved_as": "view",
    }


def _remove_container_positions(
    portfolio_id: int, position_ids: list[int]
) -> dict[str, Any]:
    result = portfolios_svc.physically_delete_positions(
        portfolio_id=portfolio_id, position_ids=position_ids
    )
    if result is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    portfolio, deleted = result
    return {
        "ok": True,
        "data": shape_portfolio(portfolio),
        "kind_resolved_as": "container",
        "deleted_position_ids": deleted,
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("remove_positions_from_portfolio", args_schema=_PortfolioIdsInput)
def remove_positions_from_portfolio_tool(
    portfolio_id: int, position_ids: list[int]
) -> dict[str, Any]:
    """Remove positions. View: pulls from manual_include_ids.
    Container: physically deletes the Position rows (HITL-gated upstream).
    """
    portfolio = portfolios_svc.get(portfolio_id=portfolio_id)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    if portfolio.kind == "view":
        return _remove_view_positions(portfolio_id, position_ids)
    return _remove_container_positions(portfolio_id, position_ids)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("add_portfolio_sources", args_schema=_PortfolioSourcesInput)
def add_portfolio_sources_tool(
    portfolio_id: int, source_portfolio_ids: list[int]
) -> dict[str, Any]:
    """Add cross-portfolio sources to a view (cycle/depth-checked)."""
    try:
        portfolio = portfolios_svc.add_sources(
            portfolio_id=portfolio_id,
            source_portfolio_ids=source_portfolio_ids,
        )
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    return {"ok": True, "data": shape_portfolio(portfolio)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("remove_portfolio_sources", args_schema=_PortfolioSourcesInput)
def remove_portfolio_sources_tool(
    portfolio_id: int, source_portfolio_ids: list[int]
) -> dict[str, Any]:
    """Remove sources from a view portfolio."""
    try:
        portfolio = portfolios_svc.remove_sources(
            portfolio_id=portfolio_id,
            source_portfolio_ids=source_portfolio_ids,
        )
    except _PORTFOLIO_ERRORS as exc:
        return portfolio_error_response(exc)
    if portfolio is None:
        return {"ok": False, "error": f"Portfolio {portfolio_id} not found"}
    return {"ok": True, "data": shape_portfolio(portfolio)}
