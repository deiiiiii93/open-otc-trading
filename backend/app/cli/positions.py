"""Positions CLI commands (Typer).

Mirrors the legacy ``positions`` argparse subcommands as Typer commands
that call ``services.domains.positions`` directly. The ``positions price``
subcommand remains in ``_legacy.py`` until the pricing PR.

Each command prints a single JSON document on stdout. Use the shared
``--json/--no-json`` flag for human-friendly fallback output.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
from sqlalchemy.orm import Session

from app import database
from app.models import Portfolio
from app.services.domains import positions as positions_svc
from app.tools._shaping import shape_position, shape_valuation_results

from ._format import emit

app = typer.Typer(no_args_is_help=True)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid date: {value}") from exc


def _resolve_portfolio_or_die(
    session: Session, identifier: str, *, create: bool = False, base_currency: str = "CNY"
) -> Portfolio:
    portfolio: Portfolio | None = None
    if identifier.isdigit():
        portfolio = session.get(Portfolio, int(identifier))
    if portfolio is None:
        portfolio = (
            session.query(Portfolio).filter(Portfolio.name == identifier).one_or_none()
        )
    if portfolio is None and create:
        portfolio = Portfolio(name=identifier, base_currency=base_currency)
        session.add(portfolio)
        session.flush()
    if portfolio is None:
        typer.echo(f"Portfolio not found: {identifier}", err=True)
        raise typer.Exit(2)
    return portfolio


@app.command("list")
def list_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    product_type: str = typer.Option(None, "--product-type"),
    status: str = typer.Option("open", "--status"),
    accounting_date: str = typer.Option(None, "--accounting-date"),
    effective_date_from: str = typer.Option(None, "--effective-date-from"),
    effective_date_to: str = typer.Option(None, "--effective-date-to"),
    effective_last_days: int = typer.Option(None, "--effective-last-days", min=1),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """List positions in a portfolio, with optional filters."""
    database.init_db()
    with database.SessionLocal() as session:
        target = _resolve_portfolio_or_die(session, portfolio)
        rows = positions_svc.list_filtered(
            portfolio_id=target.id,
            product_type=product_type,
            status=status,
            accounting_date=_parse_iso_date(accounting_date),
            effective_date_from=_parse_iso_date(effective_date_from),
            effective_date_to=_parse_iso_date(effective_date_to),
            effective_last_days=effective_last_days,
            session=session,
        )
        payload: dict[str, Any] = {
            "portfolio_id": target.id,
            "total_count": len(rows),
            "positions": [shape_position(p) for p in rows],
        }
    emit(payload, as_json=json_output)


@app.command("count")
def count_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Count open positions in a portfolio."""
    database.init_db()
    with database.SessionLocal() as session:
        target = _resolve_portfolio_or_die(session, portfolio)
        total = positions_svc.count(portfolio_id=target.id, session=session)
    emit({"portfolio_id": target.id, "count": total}, as_json=json_output)


@app.command("latest-valuations")
def latest_valuations_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Return the latest completed stored valuation results for a portfolio."""
    database.init_db()
    with database.SessionLocal() as session:
        target = _resolve_portfolio_or_die(session, portfolio)
        run = positions_svc.latest_valuation_run(
            portfolio_id=target.id, session=session
        )
        if run is None:
            payload = shape_valuation_results(target.id, None, [], total_count=0)
        else:
            all_results = sorted(run.results, key=lambda r: r.id)
            payload = shape_valuation_results(
                target.id, run, all_results[:limit], total_count=len(all_results)
            )
    emit(payload, as_json=json_output)


@app.command("import")
def import_cmd(
    xlsx: Path = typer.Option(..., "--xlsx", help="Trade workbook path"),
    portfolio: str = typer.Option(..., "--portfolio", help="Portfolio id or name"),
    sheet: str = typer.Option(positions_svc.TRADE_SHEET, "--sheet"),
    base_currency: str = typer.Option("CNY", "--base-currency"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Import OTC trade workbook rows into a portfolio."""
    database.init_db()
    with database.SessionLocal() as session:
        target = _resolve_portfolio_or_die(
            session, portfolio, create=True, base_currency=base_currency
        )
        batch = positions_svc.import_from_xlsx(
            portfolio_id=target.id,
            xlsx_path=str(xlsx),
            sheet=sheet,
            session=session,
        )
        payload = {
            "import_batch_id": batch.id,
            "portfolio_id": target.id,
            "row_count": batch.row_count,
            "imported_count": batch.imported_count,
            "supported_count": batch.supported_count,
            "unsupported_count": batch.unsupported_count,
            "error_count": batch.error_count,
            "status": batch.status,
        }
        session.commit()
    emit(payload, as_json=json_output)


