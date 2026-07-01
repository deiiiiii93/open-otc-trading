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


def _assemble(portfolio_id: str, records: list[dict]) -> dict:
    from ..database import SessionLocal

    with SessionLocal() as session:
        scoped = enumerate_limit_breaches(session, portfolio_id)
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
