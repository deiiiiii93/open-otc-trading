"""Deterministic ``assemble_breach_report`` tool for the dynamic-subagents pilot.

Re-derives the authoritative breach list SERVER-SIDE from ``portfolio_id`` (a workflow
launch param, not model text) and reconciles the fan-out records against it, so every
breach gets exactly one terminal record and uncovered breaches surface as ``failed``.
"""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..services.deep_agent.dynamic_subagents import reconcile_fanout_coverage
from ..services.risk_limits import enumerate_limit_breaches


class AssembleBreachReportInput(BaseModel):
    portfolio_id: str = Field(description="Portfolio whose breaches to reconcile (launch param).")
    records: list[dict] = Field(
        default_factory=list,
        description="Per-breach records the fan-out produced: {position_id, severity, commentary}.",
    )


def _server_portfolio_id(model_arg: str) -> str:
    """Prefer the server-stamped launch arg over the model-supplied tool argument,
    so scope selection is server-authoritative (a hallucinated/injected portfolio_id
    from the model cannot redirect the report to the wrong book)."""
    try:
        from langgraph.config import get_config

        from ..services.deep_agent.dynamic_subagents import FANOUT_LAUNCH_ARGS_KEY

        cfg = get_config().get("configurable") or {}
        server = (cfg.get(FANOUT_LAUNCH_ARGS_KEY) or {}).get("portfolio_id")
    except Exception:
        server = None
    return str(server) if server is not None else model_arg


def _assemble(portfolio_id: str, records: list[dict]) -> dict:
    from ..database import SessionLocal

    resolved = _server_portfolio_id(portfolio_id)
    with SessionLocal() as session:
        scoped = enumerate_limit_breaches(session, resolved)
    return reconcile_fanout_coverage(scoped, records)


def _run(portfolio_id: str, records: list[dict] | None = None) -> str:
    return json.dumps(_assemble(portfolio_id, records or []))


def build_assemble_breach_report_tool() -> StructuredTool:
    return StructuredTool.from_function(
        name="assemble_breach_report",
        description=(
            "Reconcile fan-out breach commentaries against the portfolio's authoritative "
            "breach list (derived server-side from the latest risk run). Every breach gets "
            "exactly one record; uncovered breaches are marked failed."
        ),
        func=_run,
        args_schema=AssembleBreachReportInput,
    )
