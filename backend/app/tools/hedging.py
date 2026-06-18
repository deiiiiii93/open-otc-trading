# backend/app/tools/hedging.py
"""@tool wrappers for the hedging strategy domain (thin adapters over
services/domains/hedging_strategy)."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services import hedging_greeks
from app.services.domains import hedging_strategy as hs


class HedgeableInput(BaseModel):
    portfolio_id: int


class ProposeHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str
    strategy: str = Field(description="delta_neutral|delta_neutral_enhanced|delta_gamma_neutral|full_neutral")
    legs: list[dict[str, Any]] | None = None
    bands: dict[str, float] | None = None


class BookHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str = Field(
        description="Hedged exposure's underlying symbol (e.g. '000905.SH'), "
        "NOT the hedge instrument's contract code."
    )
    risk_run_id: int = Field(
        description="Source risk run id, from get_hedgeable_underlyings or the proposal."
    )
    strategy: str = Field(
        description="delta_neutral|delta_neutral_enhanced|delta_gamma_neutral|full_neutral "
        "for solver-sized legs, or 'manual' for desk-stated legs."
    )
    spot: float = Field(
        description="Risk-run spot for the hedged underlying, from "
        "get_hedgeable_underlyings or the proposal."
    )
    legs: list[dict[str, Any]] = Field(
        description="Each leg: {instrument_type: 'future'|'spot'|'option', quantity: "
        "signed integer lots, contract_code, exchange, multiplier, expiry (ISO date); "
        "options add strike and option_type}. Zero-quantity legs are skipped."
    )


class BandsInput(BaseModel):
    underlying_id: int | None = Field(
        default=None,
        description="Omit (null) to address the portfolio-wide defaults row.",
    )


class SetBandsInput(BaseModel):
    underlying_id: int | None = Field(
        default=None,
        description="Omit (null) to set the portfolio-wide defaults row.",
    )
    bands: dict[str, float]


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_hedgeable_underlyings", args_schema=HedgeableInput)
def get_hedgeable_underlyings_tool(portfolio_id: int) -> dict[str, Any]:
    """Per-underlying greek exposure + staleness from the latest usable risk run
    (completed, or completed_with_errors — only rows that priced cleanly aggregate)."""
    with database.SessionLocal() as session:
        return hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("propose_hedge", args_schema=ProposeHedgeInput)
def propose_hedge_tool(portfolio_id: int, underlying: str, strategy: str,
                       legs: list[dict[str, Any]] | None = None,
                       bands: dict[str, float] | None = None) -> dict[str, Any]:
    """Propose + size a hedge (staged MILP). No persistence; safe to call repeatedly."""
    with database.SessionLocal() as session:
        return hs.solve_hedge(session, portfolio_id=portfolio_id, underlying=underlying,
                              strategy=strategy, legs=legs, bands=bands)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_hedge", args_schema=BookHedgeInput)
def book_hedge_tool(portfolio_id: int, underlying: str, risk_run_id: int,
                    strategy: str, spot: float, legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically book hedge legs into the portfolio, hedge-tagged (is_hedge,
    risk_run_id, strategy, leg_role) and visible on the Hedging page. HITL —
    requires confirmation. Never book hedge legs via book_position."""
    with database.SessionLocal() as session:
        out = hs.book_hedge(session, portfolio_id=portfolio_id, underlying=underlying,
                            risk_run_id=risk_run_id, strategy=strategy, legs=legs,
                            spot=spot, actor="agent")
        session.commit()
        return out


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_hedge_bands", args_schema=BandsInput)
def get_hedge_bands_tool(underlying_id: int | None = None) -> dict[str, Any]:
    """Resolved hedge band widths. Pass underlying_id for a specific underlying
    (override else defaults); omit it to read the portfolio-wide defaults row."""
    with database.SessionLocal() as session:
        return hs.resolve_bands(session, underlying_id=underlying_id)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("set_hedge_bands", args_schema=SetBandsInput)
def set_hedge_bands_tool(bands: dict[str, float],
                         underlying_id: int | None = None) -> dict[str, Any]:
    """Persist hedge bands. Pass underlying_id for a per-underlying override; omit
    it to set the portfolio-wide defaults row."""
    with database.SessionLocal() as session:
        hs.set_bands(session, underlying_id=underlying_id, bands=bands, actor="agent")
        session.commit()
        return hs.resolve_bands(session, underlying_id=underlying_id)
