"""Legacy argparse CLI for non-Typer subcommands.

This module preserves the original ``app/cli.py`` behaviour for the
``agent`` subcommands and for ``positions price``, which has not yet
migrated to Typer. The ``portfolios`` block and the ``positions
import`` / ``positions import-market`` / ``positions list`` /
``positions count`` / ``positions latest-valuations`` blocks have been
removed; calls with those resources/commands are dispatched to the
Typer apps in ``app/cli/__init__.py`` before reaching this module.

``positions price`` will migrate in the pricing PR.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .. import database
from ..config import get_settings
from ..models import Portfolio, PositionValuationResult
from ..services.position_pricer import (
    MarketOverrides,
    price_portfolio_positions,
)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="open-otc")
    subparsers = parser.add_subparsers(dest="resource", required=True)

    agent_parser = subparsers.add_parser("agent")
    agent_commands = agent_parser.add_subparsers(dest="command", required=True)
    agent_commands.add_parser(
        "reset-state",
        help="Delete the LangGraph checkpoint store (next request creates a fresh DB).",
    )

    positions_parser = subparsers.add_parser("positions")
    position_commands = positions_parser.add_subparsers(dest="command", required=True)

    price_parser = position_commands.add_parser("price")
    price_parser.add_argument("--portfolio", required=True, help="Portfolio id or name")
    price_parser.add_argument("--position-id", dest="position_ids", action="append", type=int)
    price_parser.add_argument("--valuation-date")
    price_parser.add_argument("--spot", type=float)
    price_parser.add_argument("--rate", "--r", dest="rate", type=float)
    price_parser.add_argument("--dividend-yield", "--q", dest="dividend_yield", type=float)
    price_parser.add_argument("--volatility", "--vol", dest="volatility", type=float)
    price_parser.add_argument("--output-json")
    price_parser.add_argument("--output-csv")

    args = parser.parse_args(argv)

    if args.resource == "agent" and args.command == "reset-state":
        payload = _run_agent_reset_state()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    database.init_db()
    with database.SessionLocal() as session:
        if args.resource == "positions" and args.command == "price":
            payload = _run_price(session, args)
        else:
            parser.error("Unsupported command")
        session.commit()

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _run_agent_reset_state() -> dict[str, Any]:
    """Delete the agent checkpoint sqlite file. Next request creates a fresh DB."""
    settings = get_settings()
    path = settings.agent_checkpoint_db_path
    if path == ":memory:":
        return {"action": "noop", "reason": "agent_checkpoint_db_path is :memory:"}
    if os.path.exists(path):
        os.remove(path)
        return {"action": "removed", "path": path}
    return {"action": "noop", "reason": "no checkpoint file present", "path": path}


def _run_price(session: Session, args: argparse.Namespace) -> dict[str, Any]:
    portfolio = _resolve_portfolio(session, args.portfolio, create=False)
    valuation_date = _parse_valuation_date(args.valuation_date)
    overrides = MarketOverrides(
        spot=args.spot,
        rate=args.rate,
        dividend_yield=args.dividend_yield,
        volatility=args.volatility,
    )
    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        position_ids=args.position_ids,
        valuation_date=valuation_date,
        overrides=overrides,
    )
    results = (
        session.query(PositionValuationResult)
        .filter(PositionValuationResult.valuation_run_id == run.id)
        .order_by(PositionValuationResult.id)
        .all()
    )
    payload = {
        "valuation_run_id": run.id,
        "portfolio_id": portfolio.id,
        "status": run.status,
        "summary": run.summary,
    }
    if args.output_json:
        _write_json(Path(args.output_json), payload, results)
    if args.output_csv:
        _write_csv(Path(args.output_csv), results)
    return payload


def _resolve_portfolio(session: Session, value: str, *, create: bool, base_currency: str = "CNY") -> Portfolio:
    portfolio = session.get(Portfolio, int(value)) if value.isdigit() else None
    if portfolio is None:
        portfolio = session.query(Portfolio).filter(Portfolio.name == value).one_or_none()
    if portfolio is None and create:
        portfolio = Portfolio(name=value, base_currency=base_currency)
        session.add(portfolio)
        session.flush()
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {value}")
    return portfolio


def _parse_valuation_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def _write_json(path: Path, payload: dict[str, Any], results: list[PositionValuationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expanded = payload | {
        "results": [
            {
                "position_id": result.position_id,
                "source_trade_id": result.source_trade_id,
                "ok": result.ok,
                "price": result.price,
                "market_value": result.market_value,
                "pnl": result.pnl,
                "market_inputs": result.market_inputs,
                "error": result.error,
            }
            for result in results
        ]
    }
    path.write_text(json.dumps(expanded, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, results: list[PositionValuationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["position_id", "source_trade_id", "ok", "price", "market_value", "pnl", "error"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "position_id": result.position_id,
                    "source_trade_id": result.source_trade_id,
                    "ok": result.ok,
                    "price": result.price,
                    "market_value": result.market_value,
                    "pnl": result.pnl,
                    "error": result.error,
                }
            )
