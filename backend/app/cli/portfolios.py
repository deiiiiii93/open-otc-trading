"""Portfolios CLI commands (Typer).

Mirrors the subcommands the legacy argparse `app/cli.py` exposed for
`portfolios`. Each command prints a single JSON document on stdout so
scripted callers can pipe the output through `jq` or capture it in tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from app.services.domains import portfolios as portfolios_svc
from app.services.portfolio_rule_dsl import parse_text_to_rule
from app.tools._shaping import shape_portfolio

from ._format import emit

app = typer.Typer(no_args_is_help=True)


def _read_rule_arg(rule_text: str | None, rule_json: str | None) -> dict[str, Any] | None:
    if rule_text and rule_json:
        raise typer.BadParameter("Pass at most one of --rule-text / --rule-json")
    if rule_text:
        return parse_text_to_rule(rule_text)
    if rule_json:
        if rule_json.startswith("@"):
            return json.loads(Path(rule_json[1:]).read_text())
        return json.loads(rule_json)
    return None


def _resolve_or_die(identifier: str) -> Any:
    portfolio = portfolios_svc.resolve(identifier=identifier)
    if portfolio is None:
        typer.echo(f"Portfolio not found: {identifier}", err=True)
        raise typer.Exit(2)
    return portfolio


@app.command("list")
def list_cmd(
    kind: str = typer.Option(None, "--kind"),
    tag: list[str] = typer.Option(None, "--tag"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """List portfolios; optionally filter by kind and tags."""
    rows = portfolios_svc.list_all(kind=kind)
    if tag:
        wanted = {t.lower() for t in tag}
        rows = [p for p in rows if wanted.issubset(set(p.tags or []))]
    payload = {"portfolios": [shape_portfolio(p) for p in rows]}
    emit(payload, as_json=json_output)


@app.command("show")
def show_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
) -> None:
    """Show one portfolio by id or name."""
    target = _resolve_or_die(portfolio)
    emit(shape_portfolio(target), as_json=True)


@app.command("create")
def create_cmd(
    name: str = typer.Option(..., "--name"),
    kind: str = typer.Option("container", "--kind"),
    base_currency: str = typer.Option("USD", "--base-currency"),
    description: str = typer.Option(None, "--description"),
    tag: list[str] = typer.Option(None, "--tag"),
) -> None:
    """Create a portfolio (container by default)."""
    p = portfolios_svc.create(
        name=name,
        kind=kind,
        base_currency=base_currency,
        description=description,
        tags=tag or [],
    )
    emit(shape_portfolio(p), as_json=True)


@app.command("create-view")
def create_view_cmd(
    name: str = typer.Option(..., "--name"),
    base_currency: str = typer.Option("USD", "--base-currency"),
    description: str = typer.Option(None, "--description"),
    rule_text: str = typer.Option(None, "--rule-text"),
    rule_json: str = typer.Option(None, "--rule-json"),
    include_id: list[int] = typer.Option(None, "--include-id"),
    source_id: list[int] = typer.Option(None, "--source-id"),
    tag: list[str] = typer.Option(None, "--tag"),
) -> None:
    """Create a view portfolio with an optional rule."""
    rule = _read_rule_arg(rule_text, rule_json)
    p = portfolios_svc.create(
        name=name,
        kind="view",
        base_currency=base_currency,
        description=description,
        filter_rule=rule,
        manual_include_ids=list(include_id or []),
        source_portfolio_ids=list(source_id or []),
        tags=list(tag or []),
    )
    emit(shape_portfolio(p), as_json=True)


@app.command("update")
def update_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    name: str = typer.Option(None, "--name"),
    description: str = typer.Option(None, "--description"),
    base_currency: str = typer.Option(None, "--base-currency"),
    tag: list[str] = typer.Option(None, "--tag"),
) -> None:
    """Update mutable fields on a portfolio."""
    target = _resolve_or_die(portfolio)
    fields: dict[str, Any] = {
        "name": name,
        "description": description,
        "base_currency": base_currency,
        "tags": list(tag) if tag is not None else None,
    }
    portfolios_svc.update(portfolio_id=target.id, fields=fields)
    refreshed = portfolios_svc.get(portfolio_id=target.id)
    emit(shape_portfolio(refreshed), as_json=True)


@app.command("delete")
def delete_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Delete a portfolio. Requires --confirm."""
    if not confirm:
        typer.echo("Refusing to delete without --confirm", err=True)
        raise typer.Exit(2)
    target = _resolve_or_die(portfolio)
    name, target_id = target.name, target.id
    portfolios_svc.delete(portfolio_id=target_id)
    emit({"deleted": True, "id": target_id, "name": name}, as_json=True)


@app.command("set-rule")
def set_rule_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    rule_text: str = typer.Option(None, "--rule-text"),
    rule_json: str = typer.Option(None, "--rule-json"),
) -> None:
    """Replace a view portfolio's filter rule."""
    target = _resolve_or_die(portfolio)
    rule = _read_rule_arg(rule_text, rule_json)
    portfolios_svc.set_rule(portfolio_id=target.id, filter_rule=rule)
    refreshed = portfolios_svc.get(portfolio_id=target.id)
    emit(shape_portfolio(refreshed), as_json=True)


@app.command("resolve")
def resolve_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
) -> None:
    """Resolve a portfolio's effective position id list."""
    target = _resolve_or_die(portfolio)
    ids = portfolios_svc.preview_membership(portfolio_id=target.id)
    emit({"portfolio_id": target.id, "position_ids": ids}, as_json=True)


def _modify_id_list_cmd(
    portfolio: str,
    action: str,
    position_ids: list[int],
    *,
    attr: str,
) -> None:
    target = _resolve_or_die(portfolio)
    helpers = {
        ("add", "manual_include_ids"): portfolios_svc.add_member_positions,
        ("remove", "manual_include_ids"): portfolios_svc.remove_member_positions,
        ("add", "manual_exclude_ids"): portfolios_svc.add_member_excludes,
        ("remove", "manual_exclude_ids"): portfolios_svc.remove_member_excludes,
    }
    helpers[(action, attr)](portfolio_id=target.id, position_ids=position_ids)
    refreshed = portfolios_svc.get(portfolio_id=target.id)
    emit(shape_portfolio(refreshed), as_json=True)


@app.command("includes")
def includes_cmd(
    action: str = typer.Argument(..., metavar="ACTION", help="add|remove"),
    portfolio: str = typer.Option(..., "--portfolio"),
    position_id: list[int] = typer.Option(..., "--position-id"),
) -> None:
    """Add or remove positions on a view's manual_include_ids."""
    if action not in {"add", "remove"}:
        raise typer.BadParameter("ACTION must be 'add' or 'remove'")
    _modify_id_list_cmd(
        portfolio, action, list(position_id), attr="manual_include_ids"
    )


@app.command("excludes")
def excludes_cmd(
    action: str = typer.Argument(..., metavar="ACTION", help="add|remove"),
    portfolio: str = typer.Option(..., "--portfolio"),
    position_id: list[int] = typer.Option(..., "--position-id"),
) -> None:
    """Add or remove positions on a view's manual_exclude_ids."""
    if action not in {"add", "remove"}:
        raise typer.BadParameter("ACTION must be 'add' or 'remove'")
    _modify_id_list_cmd(
        portfolio, action, list(position_id), attr="manual_exclude_ids"
    )


@app.command("sources")
def sources_cmd(
    action: str = typer.Argument(..., metavar="ACTION", help="add|remove"),
    portfolio: str = typer.Option(..., "--portfolio"),
    source: list[int] = typer.Option(..., "--source"),
) -> None:
    """Add or remove cross-portfolio sources on a view."""
    if action not in {"add", "remove"}:
        raise typer.BadParameter("ACTION must be 'add' or 'remove'")
    target = _resolve_or_die(portfolio)
    if action == "add":
        portfolios_svc.add_sources(
            portfolio_id=target.id, source_portfolio_ids=list(source)
        )
    else:
        portfolios_svc.remove_sources(
            portfolio_id=target.id, source_portfolio_ids=list(source)
        )
    refreshed = portfolios_svc.get(portfolio_id=target.id)
    emit(shape_portfolio(refreshed), as_json=True)


@app.command("tags")
def tags_cmd(
    action: str = typer.Argument(..., metavar="ACTION", help="set"),
    portfolio: str = typer.Option(..., "--portfolio"),
    tag: list[str] = typer.Option(..., "--tag"),
) -> None:
    """Replace a portfolio's tag set."""
    if action != "set":
        raise typer.BadParameter("ACTION must be 'set'")
    target = _resolve_or_die(portfolio)
    portfolios_svc.set_tags(portfolio_id=target.id, tags=list(tag))
    refreshed = portfolios_svc.get(portfolio_id=target.id)
    emit(shape_portfolio(refreshed), as_json=True)
