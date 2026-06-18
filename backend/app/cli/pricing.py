"""Pricing CLI commands (Typer).

Mirrors the legacy pricing operations as Typer commands that call
``services.domains.pricing`` directly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import typer

from app import database
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import pricing as pricing_svc

from ._format import emit

app = typer.Typer(no_args_is_help=True)


def _resolve_portfolio_or_die(identifier: str) -> Any:
    portfolio = portfolios_svc.resolve(identifier=identifier)
    if portfolio is None:
        raise typer.BadParameter(f"Portfolio not found: {identifier}")
    return portfolio


@app.command("estimate")
def estimate_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
) -> None:
    """Estimate pricing runtime for a portfolio's positions."""
    target = _resolve_portfolio_or_die(portfolio)
    seconds = pricing_svc.estimate_price_seconds(portfolio_id=target.id)
    typer.echo(f"{seconds:.1f}s")


@app.command("run")
def run_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    position_id: list[int] = typer.Option([], "--position-id"),
    profile_id: int = typer.Option(None, "--pricing-profile-id"),
    valuation_date: str = typer.Option(None, "--valuation-date"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Batch price persisted OTC positions."""
    target = _resolve_portfolio_or_die(portfolio)
    parsed_date = datetime.fromisoformat(valuation_date) if valuation_date else None
    run = pricing_svc.price_positions(
        portfolio_id=target.id,
        position_ids=position_id or None,
        pricing_profile_id=profile_id,
        valuation_date=parsed_date,
    )
    payload = {
        "valuation_run_id": run.id,
        "portfolio_id": target.id,
        "pricing_parameter_profile_id": run.pricing_parameter_profile_id,
        "status": run.status,
        "summary": run.summary,
    }
    emit(payload, as_json=json_output)
