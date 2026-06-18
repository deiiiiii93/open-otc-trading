"""Risk CLI commands (Typer).

Mirrors the legacy risk operations as Typer commands that call
``services.domains.risk`` directly.
"""
from __future__ import annotations

from typing import Any

import typer

from app.services.domains import portfolios as portfolios_svc
from app.services.domains import risk as risk_svc

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
    """Estimate risk run runtime for a portfolio's positions."""
    target = _resolve_portfolio_or_die(portfolio)
    seconds = risk_svc.estimate_run_seconds(portfolio_id=target.id)
    typer.echo(f"{seconds:.1f}s")


@app.command("run")
def run_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    method: str = typer.Option("summary", "--method"),
    profile_id: int = typer.Option(None, "--pricing-profile-id"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Queue an audited async portfolio risk run."""
    target = _resolve_portfolio_or_die(portfolio)
    result = risk_svc.run(
        portfolio_id=target.id,
        method=method,
        pricing_profile_id=profile_id,
    )
    emit(result, as_json=json_output)


@app.command("latest")
def latest_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show the latest completed risk run for a portfolio."""
    target = _resolve_portfolio_or_die(portfolio)
    run = risk_svc.get_latest_run(portfolio_id=target.id)
    if run is None:
        payload = {
            "portfolio_id": target.id,
            "found": False,
            "message": "No completed stored risk run exists for this portfolio.",
        }
    else:
        payload = {
            "portfolio_id": target.id,
            "found": True,
            "risk_run_id": run.id,
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "metrics": run.metrics or {},
        }
    emit(payload, as_json=json_output)
