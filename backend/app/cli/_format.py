"""Terminal formatting helpers for CLI commands.

Contract: dicts in, terminal text out. Domain CLI commands should shape ORM
rows via ``app.tools._shaping`` first, then route the resulting dict through
``emit`` here.
"""
from __future__ import annotations

import json
from typing import Any

import typer


def emit(data: Any, *, as_json: bool = True) -> None:
    """Emit a shaped dict (or list of dicts) to stdout.

    JSON mode produces machine-readable output, the default in scripted CLI
    flows. Non-JSON mode falls back to a human-friendly one-liner.
    """
    if as_json:
        typer.echo(json.dumps(data, default=str, indent=2, ensure_ascii=False))
        return
    if isinstance(data, list):
        for item in data:
            typer.echo(_human_line(item))
    else:
        typer.echo(_human_line(data))


def _human_line(obj: Any) -> str:
    if isinstance(obj, dict) and "id" in obj and "name" in obj:
        kind = obj.get("kind", "")
        return f"#{obj['id']:<4} {obj['name']:<24} kind={kind}"
    return str(obj)
