"""Market data CLI commands (Typer).

Mirrors the legacy market data operations as Typer commands that call
``services.domains.market_data`` directly.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import typer

from app.services.domains import market_data as md_svc
from app.tools._shaping import shape_market_data_profile

from ._format import emit

app = typer.Typer(no_args_is_help=True)


def _parse_iso_date(value: str) -> date:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid date: {value}") from exc


@app.command("fetch")
def fetch_cmd(
    symbol: str = typer.Option(..., "--symbol", help="Underlying symbol (e.g. 000852.SH)"),
    asset_class: str = typer.Option("index", "--asset-class"),
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)"),
    use_proxy: bool = typer.Option(False, "--proxy"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Fetch a market snapshot from AKShare for the given window."""
    snapshot = md_svc.fetch_snapshot(
        symbol=symbol,
        asset_class=asset_class,
        start_date=_parse_iso_date(start),
        end_date=_parse_iso_date(end),
        use_proxy=use_proxy,
    )
    emit(snapshot.model_dump(mode="json"), as_json=json_output)


@app.command("profiles")
def profiles_cmd(
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """List all stored market data profiles."""
    rows = md_svc.list_profiles()
    payload: list[dict[str, Any]] = [shape_market_data_profile(p) for p in rows]
    emit(payload, as_json=json_output)
