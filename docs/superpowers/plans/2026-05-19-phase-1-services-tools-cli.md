# Phase 1 — Services + Tools + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `backend/app/services/langchain_tools.py` (1608 lines, 37 `@tool` definitions tangled with ORM logic) with three uniformly-shaped layers: pure-Python services under `app/services/domains/`, thin LangChain `@tool` wrappers under `app/tools/`, and a Typer CLI under `app/cli/` mirroring every tool 1:1.

**Architecture:** Three-tier separation. Services are pure functions returning ORM objects or primitives, no LangChain types, session-aware. Tools call services and shape JSON for the LLM. CLI calls services and formats for terminal. The three layers share the service as the single source of truth; tools and CLI are sibling adapters. Existing partially-service-shaped modules (`portfolio_service.py`, `rfq.py`, `quantark.py`, `market_data.py`, `position_pricer.py`, `position_adapter.py`, `portfolio_membership.py`) are re-exposed through `services/domains/` facade modules without rewriting their internals.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x ORM, pytest, LangChain (`@tool` decorator), Typer (new dependency), uv for env management.

**Scope guard:** This plan covers Phase 1 only (8 PRs from the spec). Phase 2 (envelopes, page context) and Phase 3 (skill rewrite) have their own plans, written after Phase 1 lands.

**Reference spec:** `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`.

---

## File Structure

### New files (created)

```
backend/app/
├── services/
│   └── domains/
│       ├── __init__.py
│       ├── portfolios.py          # facade over services/portfolio_service.py + portfolio_membership.py
│       ├── positions.py           # consolidates position_adapter.py + portfolio_membership.resolve_positions + new helpers
│       ├── market_data.py         # facade over services/market_data.py (the existing file is re-namespaced)
│       ├── pricing.py             # facade over services/quantark.py + position_pricer.py + new estimate_*
│       ├── risk.py                # consolidates risk-flavored helpers + run_risk dispatch + estimate_run_seconds
│       ├── rfq.py                 # facade over services/rfq.py
│       ├── reporting.py           # consolidates report list/get/create/batch
│       └── tasks.py               # facade over services/async_agents/tools.py task helpers
├── tools/
│   ├── __init__.py
│   ├── _shaping.py                # shared JSON shaping helpers
│   ├── portfolios.py              # @tool wrappers for portfolio domain
│   ├── positions.py
│   ├── market_data.py
│   ├── pricing.py
│   ├── risk.py
│   ├── rfq.py
│   ├── reporting.py
│   └── async_agents.py            # re-exports existing async-agent @tools (kept as-is)
└── cli/
    ├── __init__.py                # Typer app entry; replaces app/cli.py argparse main
    ├── _format.py                 # terminal-format helpers (table, json)
    ├── portfolios.py
    ├── positions.py
    ├── market_data.py
    ├── pricing.py
    ├── risk.py
    ├── rfq.py
    └── reporting.py
```

### Modified files

```
backend/app/
├── cli.py                          # SHRINK: replaced by app/cli/ package; keep as thin re-export shim during migration
├── services/
│   ├── langchain_tools.py          # SHRINK incrementally; deleted in P1.8
│   ├── agents.py                   # update tool import path in P1.8
│   ├── deep_agent/personas.py      # update tool import path in P1.8
│   ├── async_agents/agent.py       # update tool import path in P1.8
│   └── reply_options/tool.py       # no change; standalone tool
└── pyproject.toml                  # add typer>=0.12 dependency
tests/
├── test_agent_tools.py             # update imports in P1.8
├── test_async_agents_tools.py      # update imports in P1.8
├── test_langchain_report_tools.py  # update imports in P1.8
├── test_position_import_pricing.py # update imports in P1.8
├── test_personas.py                # update imports in P1.8
├── test_quant_services.py          # update imports in P1.8
└── test_reply_options_tool.py      # update imports in P1.8
```

### Deleted files

`backend/app/services/langchain_tools.py` — deleted in P1.8 after all callers cut over.

---

## Conventions used throughout this plan

**Service contract** — every function in `services/domains/<X>.py`:

```python
def some_op(
    *,
    arg1: int,
    arg2: str | None = None,
    session: Session | None = None,    # optional; opens a scoped session if not provided
) -> SomeOrmObject | list[SomeOrmObject] | primitive:
    ...
```

- Keyword-only args.
- No LangChain types in signature.
- No Pydantic tool-schemas in signature.
- Returns ORM objects, lists, or primitives — never JSON-shaped dicts.
- Opens its own session if not given one.

**Tool wrapper pattern** — every `@tool` in `app/tools/<X>.py`:

```python
from langchain_core.tools import tool
from app.services.domains import <X> as <X>_svc
from ._shaping import shape_<thing>

@tool("name", args_schema=NameInput)
def name_tool(...) -> dict[str, Any]:
    """Docstring for the LLM."""
    rows = <X>_svc.some_op(...)
    return shape_<thing>(rows)
```

- ≤30 lines. Parse args → call service → shape output.

**CLI command pattern** — every command in `app/cli/<X>.py`:

```python
import typer
from app.services.domains import <X> as <X>_svc
from ._format import emit

app = typer.Typer(no_args_is_help=True)

@app.command("op")
def cmd_op(
    arg: int = typer.Option(..., help="..."),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    result = <X>_svc.some_op(arg=arg)
    emit(result, as_json=json_output)
```

**Commit message** — `refactor(<domain>): extract <thing> into services/domains` (and similar; follow `git log -5` for the repo's prevailing tone).

---

## Tasks

### Task 1 (P1.1) — Portfolios domain

**Files:**
- Create: `backend/app/services/domains/__init__.py`
- Create: `backend/app/services/domains/portfolios.py`
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/_shaping.py`
- Create: `backend/app/tools/portfolios.py`
- Create: `backend/app/cli/__init__.py`
- Create: `backend/app/cli/_format.py`
- Create: `backend/app/cli/portfolios.py`
- Create: `tests/test_services_domains_portfolios.py`
- Create: `tests/test_tools_portfolios.py`
- Modify: `pyproject.toml` (add Typer dep)
- Reference: `backend/app/services/portfolio_service.py` (existing service)
- Reference: `backend/app/services/portfolio_membership.py` (existing service)
- Reference: `backend/app/services/langchain_tools.py:1206–1502` (current portfolio @tool definitions)

**Portfolio tools to migrate (10):** `list_portfolios`, `get_portfolio`, `create_portfolio`, `update_portfolio`, `delete_portfolio`, `set_portfolio_rule`, `add_positions_to_portfolio`, `remove_positions_from_portfolio`, `add_portfolio_sources`, `remove_portfolio_sources`.

#### Steps

- [ ] **1.1: Add Typer to dependencies**

Edit `pyproject.toml` `[project] dependencies` block, append `"typer>=0.12.0"`. Run:

```bash
uv sync
```

Expected: Typer installs successfully; no test breakage.

- [ ] **1.2: Commit dependency addition**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add typer for cli refactor"
```

- [ ] **1.3: Create empty package __init__.py files**

```bash
mkdir -p backend/app/services/domains backend/app/tools backend/app/cli
touch backend/app/services/domains/__init__.py backend/app/tools/__init__.py backend/app/cli/__init__.py
```

Write `backend/app/services/domains/__init__.py`:

```python
"""Pure-Python service facade modules.

Each module re-exposes a domain's operations as pure functions returning ORM
objects or primitives. Tool wrappers in app/tools/ and CLI commands in app/cli/
both call into this layer.
"""
```

- [ ] **1.4: Write failing test for `portfolios.list_all`**

Create `tests/test_services_domains_portfolios.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app import database
from app.config import Settings
from app.models import Portfolio
from app.services.domains import portfolios as portfolios_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert(name: str, kind: str = "container") -> int:
    with database.SessionLocal() as session:
        p = Portfolio(name=name, kind=kind)
        session.add(p)
        session.commit()
        return p.id


def test_list_all_returns_orm_objects():
    _insert("A")
    _insert("B")
    result = portfolios_svc.list_all()
    assert len(result) == 2
    assert all(isinstance(p, Portfolio) for p in result)
    assert {p.name for p in result} == {"A", "B"}


def test_list_all_kind_filter():
    _insert("C", "container")
    _insert("V", "view")
    result = portfolios_svc.list_all(kind="view")
    assert {p.name for p in result} == {"V"}
```

- [ ] **1.5: Run test, verify it fails**

```bash
uv run pytest tests/test_services_domains_portfolios.py -v
```

Expected: ImportError on `from app.services.domains import portfolios as portfolios_svc`.

- [ ] **1.6: Write the portfolios service module (initial skeleton + `list_all`)**

Create `backend/app/services/domains/portfolios.py`:

```python
"""Portfolios domain service.

Pure-Python facade over the existing portfolio_service.py + portfolio_membership.py.
Returns ORM objects; never JSON. Session-aware.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import Portfolio
from app.services import portfolio_service as _ps


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_all(
    *,
    kind: str | None = None,
    session: Session | None = None,
) -> list[Portfolio]:
    """Return all portfolios, optionally filtered by kind ('container' | 'view')."""
    with _session_scope(session) as sess:
        q = sess.query(Portfolio).order_by(Portfolio.id)
        if kind is not None:
            q = q.filter(Portfolio.kind == kind)
        return q.all()
```

- [ ] **1.7: Run test, verify it passes**

```bash
uv run pytest tests/test_services_domains_portfolios.py -v
```

Expected: 2 passed.

- [ ] **1.8: Extend portfolios service with remaining read + write operations**

Append to `backend/app/services/domains/portfolios.py`:

```python
def get(*, portfolio_id: int, session: Session | None = None) -> Portfolio | None:
    """Return one portfolio by id, or None if not found."""
    with _session_scope(session) as sess:
        return sess.get(Portfolio, portfolio_id)


def get_by_name(*, name: str, session: Session | None = None) -> Portfolio | None:
    """Return one portfolio by exact name, or None if not found."""
    with _session_scope(session) as sess:
        return sess.query(Portfolio).filter(Portfolio.name == name).first()


def resolve(*, identifier: int | str, session: Session | None = None) -> Portfolio | None:
    """Accept either an int id or a string name and resolve to a Portfolio."""
    if isinstance(identifier, int):
        return get(portfolio_id=identifier, session=session)
    if identifier.isdigit():
        return get(portfolio_id=int(identifier), session=session)
    return get_by_name(name=identifier, session=session)


def create(
    *,
    name: str,
    kind: str,
    description: str | None = None,
    tags: list[str] | None = None,
    filter_rule: dict | None = None,
    source_portfolio_ids: list[int] | None = None,
    manual_includes: list[int] | None = None,
    manual_excludes: list[int] | None = None,
    session: Session | None = None,
) -> Portfolio:
    """Create a portfolio. Delegates to portfolio_service.create_portfolio for validation."""
    with _session_scope(session) as sess:
        return _ps.create_portfolio(
            sess,
            name=name,
            kind=kind,
            description=description,
            tags=tags or [],
            filter_rule=filter_rule,
            source_portfolio_ids=source_portfolio_ids or [],
            manual_includes=manual_includes or [],
            manual_excludes=manual_excludes or [],
        )


def update(
    *,
    portfolio_id: int,
    fields: dict,
    session: Session | None = None,
) -> Portfolio | None:
    """Update a portfolio's mutable fields. Delegates to portfolio_service.update_portfolio."""
    with _session_scope(session) as sess:
        return _ps.update_portfolio(sess, portfolio_id, fields)


def delete(*, portfolio_id: int, session: Session | None = None) -> bool:
    """Delete a portfolio. Returns True if deleted, False if not found."""
    with _session_scope(session) as sess:
        return _ps.delete_portfolio(sess, portfolio_id)


def set_rule(
    *,
    portfolio_id: int,
    filter_rule: dict | None,
    session: Session | None = None,
) -> Portfolio | None:
    """Replace a portfolio's filter_rule. None clears it."""
    with _session_scope(session) as sess:
        return _ps.set_filter_rule(sess, portfolio_id, filter_rule)


def add_member_positions(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Add manual_includes position ids to a portfolio."""
    with _session_scope(session) as sess:
        return _ps.add_positions(sess, portfolio_id, position_ids)


def remove_member_positions(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Remove position ids from manual_includes or add to manual_excludes."""
    with _session_scope(session) as sess:
        return _ps.remove_positions(sess, portfolio_id, position_ids)


def add_sources(
    *,
    portfolio_id: int,
    source_portfolio_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    with _session_scope(session) as sess:
        return _ps.add_sources(sess, portfolio_id, source_portfolio_ids)


def remove_sources(
    *,
    portfolio_id: int,
    source_portfolio_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    with _session_scope(session) as sess:
        return _ps.remove_sources(sess, portfolio_id, source_portfolio_ids)
```

> If `portfolio_service.py` does not expose any of these helper names (`set_filter_rule`, `add_positions`, etc.), open it (`backend/app/services/portfolio_service.py`) and inline the equivalent body. The goal is for `domains/portfolios.py` to be the single call surface; it may absorb logic from `portfolio_service.py` when needed.

- [ ] **1.9: Add tests for the remaining service functions**

Append to `tests/test_services_domains_portfolios.py`:

```python
def test_create_and_get():
    p = portfolios_svc.create(name="X", kind="container")
    fetched = portfolios_svc.get(portfolio_id=p.id)
    assert fetched is not None and fetched.name == "X"


def test_resolve_by_name_or_id():
    p = portfolios_svc.create(name="Snow", kind="container")
    assert portfolios_svc.resolve(identifier=p.id).id == p.id
    assert portfolios_svc.resolve(identifier="Snow").id == p.id
    assert portfolios_svc.resolve(identifier=str(p.id)).id == p.id
    assert portfolios_svc.resolve(identifier="nope") is None


def test_update_fields():
    p = portfolios_svc.create(name="U", kind="container")
    portfolios_svc.update(portfolio_id=p.id, fields={"description": "edited"})
    assert portfolios_svc.get(portfolio_id=p.id).description == "edited"


def test_delete_returns_true_then_false():
    p = portfolios_svc.create(name="D", kind="container")
    assert portfolios_svc.delete(portfolio_id=p.id) is True
    assert portfolios_svc.delete(portfolio_id=p.id) is False


def test_set_rule_round_trip():
    p = portfolios_svc.create(name="R", kind="view")
    rule = {"op": "eq", "field": "product_type", "value": "Snowball"}
    portfolios_svc.set_rule(portfolio_id=p.id, filter_rule=rule)
    assert portfolios_svc.get(portfolio_id=p.id).filter_rule == rule
    portfolios_svc.set_rule(portfolio_id=p.id, filter_rule=None)
    assert portfolios_svc.get(portfolio_id=p.id).filter_rule is None
```

- [ ] **1.10: Run all service tests, verify pass**

```bash
uv run pytest tests/test_services_domains_portfolios.py -v
```

Expected: 7 passed.

- [ ] **1.11: Commit the portfolios service module**

```bash
git add backend/app/services/domains/__init__.py backend/app/services/domains/portfolios.py tests/test_services_domains_portfolios.py
git commit -m "refactor(portfolios): add services/domains/portfolios facade"
```

- [ ] **1.12: Write shaping helper for portfolio JSON output**

Create `backend/app/tools/__init__.py`:

```python
"""LangChain @tool wrappers calling into app.services.domains.

Each module is a thin LLM adapter: parse args, call service, shape JSON.
Target ≤30 lines per tool body.
"""
```

Create `backend/app/tools/_shaping.py`:

```python
"""JSON shaping helpers shared across tool modules."""
from __future__ import annotations

from typing import Any

from app.models import Portfolio


def shape_portfolio(p: Portfolio) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "description": p.description,
        "tags": list(p.tags or []),
        "filter_rule": p.filter_rule,
        "source_portfolio_ids": list(p.source_portfolio_ids or []),
        "manual_includes": list(p.manual_includes or []),
        "manual_excludes": list(p.manual_excludes or []),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def shape_portfolio_list(rows: list[Portfolio]) -> dict[str, Any]:
    return {"portfolios": [shape_portfolio(p) for p in rows], "total_count": len(rows)}
```

- [ ] **1.13: Write failing test for `list_portfolios_tool`**

Create `tests/test_tools_portfolios.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.services.domains import portfolios as portfolios_svc
from app.tools.portfolios import list_portfolios_tool


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_list_portfolios_tool_returns_shape():
    portfolios_svc.create(name="A", kind="container")
    portfolios_svc.create(name="B", kind="view")
    result = list_portfolios_tool.invoke({})
    assert result["total_count"] == 2
    names = {p["name"] for p in result["portfolios"]}
    assert names == {"A", "B"}
    assert all("filter_rule" in p for p in result["portfolios"])
```

- [ ] **1.14: Run test, verify it fails**

```bash
uv run pytest tests/test_tools_portfolios.py -v
```

Expected: ImportError on `from app.tools.portfolios import list_portfolios_tool`.

- [ ] **1.15: Write portfolio tool wrappers**

Create `backend/app/tools/portfolios.py`:

```python
"""@tool wrappers for portfolios domain."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.domains import portfolios as portfolios_svc

from ._shaping import shape_portfolio, shape_portfolio_list


class _ListInput(BaseModel):
    kind: str | None = Field(default=None, description="'container' or 'view'")


class _GetInput(BaseModel):
    portfolio_id: int = Field(..., description="Portfolio id")


class _CreateInput(BaseModel):
    name: str
    kind: str = Field(default="container")
    description: str | None = None
    tags: list[str] | None = None
    filter_rule: dict | None = None
    source_portfolio_ids: list[int] | None = None
    manual_includes: list[int] | None = None
    manual_excludes: list[int] | None = None


class _UpdateInput(BaseModel):
    portfolio_id: int
    fields: dict


class _DeleteInput(BaseModel):
    portfolio_id: int


class _SetRuleInput(BaseModel):
    portfolio_id: int
    filter_rule: dict | None


class _IdsInput(BaseModel):
    portfolio_id: int
    position_ids: list[int] | None = None
    source_portfolio_ids: list[int] | None = None


@tool("list_portfolios", args_schema=_ListInput)
def list_portfolios_tool(kind: str | None = None) -> dict[str, Any]:
    """List all portfolios; optionally filter by kind ('container' or 'view')."""
    rows = portfolios_svc.list_all(kind=kind)
    return shape_portfolio_list(rows)


@tool("get_portfolio", args_schema=_GetInput)
def get_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Return a single portfolio by id."""
    p = portfolios_svc.get(portfolio_id=portfolio_id)
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("create_portfolio", args_schema=_CreateInput)
def create_portfolio_tool(**kwargs: Any) -> dict[str, Any]:
    """Create a new portfolio."""
    p = portfolios_svc.create(**kwargs)
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("update_portfolio", args_schema=_UpdateInput)
def update_portfolio_tool(portfolio_id: int, fields: dict) -> dict[str, Any]:
    """Update mutable fields on a portfolio."""
    p = portfolios_svc.update(portfolio_id=portfolio_id, fields=fields)
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("delete_portfolio", args_schema=_DeleteInput)
def delete_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Delete a portfolio."""
    ok = portfolios_svc.delete(portfolio_id=portfolio_id)
    return {"ok": ok, "deleted_id": portfolio_id if ok else None}


@tool("set_portfolio_rule", args_schema=_SetRuleInput)
def set_portfolio_rule_tool(portfolio_id: int, filter_rule: dict | None) -> dict[str, Any]:
    """Set or clear a view portfolio's filter_rule."""
    p = portfolios_svc.set_rule(portfolio_id=portfolio_id, filter_rule=filter_rule)
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("add_positions_to_portfolio", args_schema=_IdsInput)
def add_positions_to_portfolio_tool(
    portfolio_id: int, position_ids: list[int] | None = None, **_: Any
) -> dict[str, Any]:
    p = portfolios_svc.add_member_positions(
        portfolio_id=portfolio_id, position_ids=position_ids or []
    )
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("remove_positions_from_portfolio", args_schema=_IdsInput)
def remove_positions_from_portfolio_tool(
    portfolio_id: int, position_ids: list[int] | None = None, **_: Any
) -> dict[str, Any]:
    p = portfolios_svc.remove_member_positions(
        portfolio_id=portfolio_id, position_ids=position_ids or []
    )
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("add_portfolio_sources", args_schema=_IdsInput)
def add_portfolio_sources_tool(
    portfolio_id: int, source_portfolio_ids: list[int] | None = None, **_: Any
) -> dict[str, Any]:
    p = portfolios_svc.add_sources(
        portfolio_id=portfolio_id, source_portfolio_ids=source_portfolio_ids or []
    )
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}


@tool("remove_portfolio_sources", args_schema=_IdsInput)
def remove_portfolio_sources_tool(
    portfolio_id: int, source_portfolio_ids: list[int] | None = None, **_: Any
) -> dict[str, Any]:
    p = portfolios_svc.remove_sources(
        portfolio_id=portfolio_id, source_portfolio_ids=source_portfolio_ids or []
    )
    if p is None:
        return {"ok": False, "error": f"portfolio {portfolio_id} not found"}
    return {"ok": True, "portfolio": shape_portfolio(p)}
```

- [ ] **1.16: Run tool tests, verify pass**

```bash
uv run pytest tests/test_tools_portfolios.py -v
```

Expected: 1 passed.

- [ ] **1.17: Run the existing portfolio tool tests against the new module by import shim**

The existing `tests/test_agent_tools.py` imports from `langchain_tools`. To verify behavior parity without touching that file yet, add a one-line forwarding from `langchain_tools.py` to the new tools. Edit `backend/app/services/langchain_tools.py` lines 1231–1502: remove the old `@tool` blocks for the 10 portfolio tools, and replace them with:

```python
# Portfolio tools migrated to app/tools/portfolios.py
from app.tools.portfolios import (
    list_portfolios_tool as list_portfolios_tool,
    get_portfolio_tool as get_portfolio_tool,
    create_portfolio_tool as create_portfolio_tool,
    update_portfolio_tool as update_portfolio_tool,
    delete_portfolio_tool as delete_portfolio_tool,
    set_portfolio_rule_tool as set_portfolio_rule_tool,
    add_positions_to_portfolio_tool as add_positions_to_portfolio_tool,
    remove_positions_from_portfolio_tool as remove_positions_from_portfolio_tool,
    add_portfolio_sources_tool as add_portfolio_sources_tool,
    remove_portfolio_sources_tool as remove_portfolio_sources_tool,
)
```

Old `_ListPortfoliosInput`, `_GetPortfolioInput`, etc. classes between lines 1231 and 1502 are deleted (their replacements live in `app/tools/portfolios.py`).

- [ ] **1.18: Run existing portfolio integration + tool tests**

```bash
uv run pytest tests/test_portfolio_integration.py tests/test_portfolio_service.py tests/test_agent_tools.py -v
```

Expected: all pass (no behavior change; the existing tests now exercise the new wrappers through the shim).

- [ ] **1.19: Commit the tool wrappers**

```bash
git add backend/app/tools/__init__.py backend/app/tools/_shaping.py backend/app/tools/portfolios.py tests/test_tools_portfolios.py backend/app/services/langchain_tools.py
git commit -m "refactor(portfolios): move @tool wrappers to app/tools/portfolios"
```

- [ ] **1.20: Write Typer CLI for portfolios**

Create `backend/app/cli/__init__.py`:

```python
"""open-otc CLI — Typer-based, calls services/domains directly.

Each subcommand module is a Typer app registered against the top-level app.
The CLI is the developer + scripts surface. It does NOT shell out to anything;
it imports services directly.
"""
from __future__ import annotations

import typer

from . import portfolios as portfolios_cmd

app = typer.Typer(no_args_is_help=True, name="otc")
app.add_typer(portfolios_cmd.app, name="portfolios", help="Portfolio operations")


def main(argv: list[str] | None = None) -> int:
    """Compatibility entry point; mirrors the old app/cli.py signature."""
    try:
        app(args=argv, prog_name="otc", standalone_mode=False)
    except typer.Exit as e:
        return e.exit_code
    except SystemExit as e:
        return int(e.code or 0)
    return 0
```

Create `backend/app/cli/_format.py`:

```python
"""Terminal formatting helpers for CLI commands."""
from __future__ import annotations

import json
from typing import Any

import typer

from app.models import Portfolio


def emit(data: Any, *, as_json: bool = False) -> None:
    """Emit data to stdout. JSON mode produces machine-readable output;
    default mode produces a human-friendly representation."""
    if as_json:
        typer.echo(json.dumps(_jsonable(data), default=str, indent=2))
        return
    if isinstance(data, list):
        for item in data:
            typer.echo(_human_line(item))
    else:
        typer.echo(_human_line(data))


def _jsonable(obj: Any) -> Any:
    from ..tools._shaping import shape_portfolio, shape_portfolio_list
    if isinstance(obj, Portfolio):
        return shape_portfolio(obj)
    if isinstance(obj, list) and obj and isinstance(obj[0], Portfolio):
        return shape_portfolio_list(obj)
    return obj


def _human_line(obj: Any) -> str:
    if isinstance(obj, Portfolio):
        return f"#{obj.id:<4} {obj.name:<24} kind={obj.kind} positions=?"
    return str(obj)
```

Create `backend/app/cli/portfolios.py`:

```python
"""Portfolios CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from app.services.domains import portfolios as portfolios_svc

from ._format import emit

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_cmd(
    kind: str = typer.Option(None, "--kind", help="container | view"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    rows = portfolios_svc.list_all(kind=kind)
    emit(rows, as_json=json_output)


@app.command("show")
def show_cmd(
    portfolio: str = typer.Option(..., "--portfolio", help="portfolio id or name"),
    json_output: bool = typer.Option(True, "--json"),
) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    if p is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    emit(p, as_json=json_output)


@app.command("create")
def create_cmd(
    name: str = typer.Option(..., "--name"),
    kind: str = typer.Option("container", "--kind"),
    description: str = typer.Option(None, "--description"),
) -> None:
    p = portfolios_svc.create(name=name, kind=kind, description=description)
    emit(p, as_json=True)


@app.command("create-view")
def create_view_cmd(
    name: str = typer.Option(..., "--name"),
    rule_text: str = typer.Option(None, "--rule-text", help="DSL rule string"),
    rule_json: str = typer.Option(None, "--rule-json", help="JSON rule file path"),
) -> None:
    """Create a view portfolio with a filter rule."""
    from app.services.portfolio_rule_dsl import parse_rule_text
    if rule_text:
        rule = parse_rule_text(rule_text)
    elif rule_json:
        rule = json.loads(Path(rule_json).read_text())
    else:
        typer.echo("provide --rule-text or --rule-json", err=True)
        raise typer.Exit(1)
    p = portfolios_svc.create(name=name, kind="view", filter_rule=rule)
    emit(p, as_json=True)


@app.command("delete")
def delete_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
) -> None:
    target = portfolios_svc.resolve(identifier=portfolio)
    if target is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    portfolios_svc.delete(portfolio_id=target.id)
    typer.echo(json.dumps({"deleted_id": target.id}))


@app.command("set-rule")
def set_rule_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    rule_text: str = typer.Option(None, "--rule-text"),
    clear: bool = typer.Option(False, "--clear"),
) -> None:
    target = portfolios_svc.resolve(identifier=portfolio)
    if target is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    if clear:
        rule = None
    elif rule_text:
        from app.services.portfolio_rule_dsl import parse_rule_text
        rule = parse_rule_text(rule_text)
    else:
        typer.echo("provide --rule-text or --clear", err=True)
        raise typer.Exit(1)
    p = portfolios_svc.set_rule(portfolio_id=target.id, filter_rule=rule)
    emit(p, as_json=True)
```

- [ ] **1.21: Replace the old argparse `app/cli.py` portfolios subcommand with a shim that delegates to the new Typer app**

Edit `backend/app/cli.py`. Replace the `portfolios` subcommand block (find via `portfolios_parser = subparsers.add_parser("portfolios")`) with delegation:

```python
def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if argv and argv[0] == "portfolios":
        from app.cli import main as new_main
        return new_main(argv)
    # ... existing argparse fall-through for other subcommands
```

Drop the old portfolios argparse subparser block entirely. This is the bridge so existing callers (and `tests/test_cli_portfolios.py`) keep working while we migrate the rest.

- [ ] **1.22: Run existing CLI tests**

```bash
uv run pytest tests/test_cli_portfolios.py -v
```

Expected: all existing tests pass against the new Typer command structure. If any fail because of small output-format differences (JSON shape), tighten `_format.py` to match the existing test assertions.

- [ ] **1.23: Commit the CLI module**

```bash
git add backend/app/cli/__init__.py backend/app/cli/_format.py backend/app/cli/portfolios.py backend/app/cli.py
git commit -m "refactor(portfolios): split cli into app/cli package with typer"
```

- [ ] **1.24: Open the PR for P1.1**

```bash
git push -u origin HEAD
gh pr create --title "refactor(portfolios): extract services/domains, tools, cli (P1.1)" --body "$(cat <<'EOF'
## Summary
- Add `app/services/domains/portfolios.py` as the portfolios service facade
- Move 10 portfolio `@tool` wrappers from `langchain_tools.py` to `app/tools/portfolios.py`
- Add `app/cli/portfolios.py` (Typer-based) replacing the argparse block

Phase 1 PR #1 of 8. See `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`.

## Test plan
- [ ] tests/test_services_domains_portfolios.py passes
- [ ] tests/test_tools_portfolios.py passes
- [ ] tests/test_cli_portfolios.py passes
- [ ] tests/test_portfolio_integration.py passes (no regression)
- [ ] tests/test_agent_tools.py passes (no regression)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

### Task 2 (P1.2) — Positions domain

**Files:**
- Create: `backend/app/services/domains/positions.py`
- Create: `backend/app/tools/positions.py`
- Create: `backend/app/cli/positions.py`
- Create: `tests/test_services_domains_positions.py`
- Create: `tests/test_tools_positions.py`
- Reference: `backend/app/services/portfolio_membership.py` (existing `resolve_positions`)
- Reference: `backend/app/services/position_adapter.py` (existing `import_positions_from_xlsx`)
- Reference: `backend/app/services/langchain_tools.py:329–490, 643–768` (current position @tool definitions)

**Position tools to migrate (4):** `get_positions`, `get_latest_position_valuations`, `import_otc_positions`, `import_position_market_inputs`.

**Service surface to build:**

```python
# backend/app/services/domains/positions.py
def list_filtered(
    *,
    portfolio_id: int | None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | None = None,
    effective_date_from: date | None = None,
    effective_date_to: date | None = None,
    effective_last_days: int | None = None,
    session: Session | None = None,
) -> list[Position]: ...

def count(*, portfolio_id: int, session: Session | None = None) -> int: ...
def count_from_snapshot(snapshot: dict) -> int: ...  # pure, no DB

def latest_valuations(
    *,
    portfolio_id: int,
    limit: int = 500,
    session: Session | None = None,
) -> list[PositionValuationResult]: ...

def import_from_xlsx(
    *,
    portfolio_id: int,
    xlsx_path: Path,
    sheet: str = TRADE_SHEET,
    base_currency: str = "CNY",
    session: Session | None = None,
) -> dict[str, int]: ...

def import_market_inputs_from_xlsx(
    *,
    portfolio_id: int,
    xlsx_path: Path,
    sheet: str | None = None,
    valuation_date: date | None = None,
    session: Session | None = None,
) -> dict[str, int]: ...
```

#### Steps

- [ ] **2.1: Write failing test for `positions.count_from_snapshot`**

Create `tests/test_services_domains_positions.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from app import database
from app.config import Settings
from app.models import Portfolio, Position
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import positions as positions_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_count_from_snapshot_pure_no_db():
    snapshot = {"positions": [{"id": 1}, {"id": 2}, {"id": 3}], "total_count": 3}
    assert positions_svc.count_from_snapshot(snapshot) == 3


def test_count_from_snapshot_empty():
    assert positions_svc.count_from_snapshot({}) == 0
    assert positions_svc.count_from_snapshot({"positions": []}) == 0
```

- [ ] **2.2: Run test, verify fails**

```bash
uv run pytest tests/test_services_domains_positions.py::test_count_from_snapshot_pure_no_db -v
```

Expected: ImportError.

- [ ] **2.3: Write `positions.py` initial skeleton + `count_from_snapshot`**

Create `backend/app/services/domains/positions.py`:

```python
"""Positions domain service.

Pure-Python facade. Consolidates position list/filter/count/import operations
from langchain_tools.py + position_adapter.py + portfolio_membership.py.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import Portfolio, Position, PositionValuationResult
from app.services import portfolio_membership as _membership
from app.services.position_adapter import TRADE_SHEET as _TRADE_SHEET
from app.services.position_adapter import import_positions_from_xlsx as _import_positions
from app.services.position_pricer import import_market_inputs_from_xlsx as _import_market

TRADE_SHEET = _TRADE_SHEET


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def count_from_snapshot(snapshot: dict[str, Any]) -> int:
    """Pure: count positions in a frontend-provided snapshot. No DB."""
    return len(snapshot.get("positions", []) or [])
```

- [ ] **2.4: Run test, verify pass**

```bash
uv run pytest tests/test_services_domains_positions.py -v
```

Expected: 2 passed.

- [ ] **2.5: Extend positions service with DB-backed reads**

Append to `backend/app/services/domains/positions.py`:

```python
def list_filtered(
    *,
    portfolio_id: int | None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | None = None,
    effective_date_from: date | None = None,
    effective_date_to: date | None = None,
    effective_last_days: int | None = None,
    session: Session | None = None,
) -> list[Position]:
    """Resolve a portfolio's positions through portfolio_membership and apply filters."""
    with _session_scope(session) as sess:
        if portfolio_id is None:
            portfolio = sess.query(Portfolio).order_by(Portfolio.id).first()
        else:
            portfolio = sess.get(Portfolio, portfolio_id)
        if portfolio is None:
            return []
        rows = _membership.resolve_positions(portfolio, sess)
        if status:
            rows = [p for p in rows if p.status == status]
        if product_type:
            q = product_type.lower()
            rows = [p for p in rows if q in p.product_type.lower()]
        if effective_date_from or effective_date_to or effective_last_days:
            from app.services.langchain_tools import (
                _effective_date_window,
                _position_in_effective_window,
            )
            start, end = _effective_date_window(
                accounting_date=accounting_date,
                effective_date_from=effective_date_from,
                effective_date_to=effective_date_to,
                effective_last_days=effective_last_days,
            )
            rows = [p for p in rows if _position_in_effective_window(p, start, end)]
        return sorted(rows, key=lambda p: p.id)


def count(*, portfolio_id: int, session: Session | None = None) -> int:
    """Count positions in a portfolio (status=open)."""
    return len(list_filtered(portfolio_id=portfolio_id, session=session))


def latest_valuations(
    *,
    portfolio_id: int,
    limit: int = 500,
    session: Session | None = None,
) -> list[PositionValuationResult]:
    """Return latest stored valuation result per position, ordered by position_id, capped at limit."""
    with _session_scope(session) as sess:
        rows = _membership.resolve_positions(sess.get(Portfolio, portfolio_id), sess)
        position_ids = {p.id for p in rows}
        if not position_ids:
            return []
        subq = (
            sess.query(
                PositionValuationResult.position_id,
                PositionValuationResult.id.label("max_id"),
            )
            .filter(PositionValuationResult.position_id.in_(position_ids))
            .order_by(PositionValuationResult.position_id, PositionValuationResult.id.desc())
            .all()
        )
        seen, latest_ids = set(), []
        for row in subq:
            if row.position_id in seen:
                continue
            seen.add(row.position_id)
            latest_ids.append(row.max_id)
        if not latest_ids:
            return []
        results = (
            sess.query(PositionValuationResult)
            .filter(PositionValuationResult.id.in_(latest_ids))
            .order_by(PositionValuationResult.position_id)
            .limit(limit)
            .all()
        )
        return results
```

> Note: The `from app.services.langchain_tools import _effective_date_window` is a temporary internal import. P1.8 will inline or move these helpers into `services/domains/positions.py` proper.

- [ ] **2.6: Add list/count/latest_valuations tests**

Append to `tests/test_services_domains_positions.py`:

```python
def _make_portfolio_with_n_positions(n: int) -> int:
    p = portfolios_svc.create(name="P", kind="container")
    with database.SessionLocal() as sess:
        for i in range(n):
            sess.add(Position(
                portfolio_id=p.id,
                product_type="Snowball",
                underlying=f"U{i}.SH",
                base_currency="CNY",
                status="open",
                product_kwargs={},
                engine_kwargs={},
                quantity=1,
                trade_date=date(2026, 1, 1),
                effective_date=date(2026, 1, 1),
            ))
        sess.commit()
    return p.id


def test_list_and_count_basic():
    pid = _make_portfolio_with_n_positions(3)
    rows = positions_svc.list_filtered(portfolio_id=pid)
    assert len(rows) == 3
    assert positions_svc.count(portfolio_id=pid) == 3


def test_list_filter_by_product_type():
    pid = _make_portfolio_with_n_positions(2)
    rows = positions_svc.list_filtered(portfolio_id=pid, product_type="snowball")
    assert len(rows) == 2
    rows_other = positions_svc.list_filtered(portfolio_id=pid, product_type="other")
    assert rows_other == []
```

- [ ] **2.7: Run tests, verify pass**

```bash
uv run pytest tests/test_services_domains_positions.py -v
```

Expected: 4 passed.

- [ ] **2.8: Add the import-from-xlsx service functions**

Append to `backend/app/services/domains/positions.py`:

```python
def import_from_xlsx(
    *,
    portfolio_id: int,
    xlsx_path: Path,
    sheet: str = TRADE_SHEET,
    base_currency: str = "CNY",
    session: Session | None = None,
) -> dict[str, int]:
    """Import OTC positions from an Excel workbook into a portfolio."""
    with _session_scope(session) as sess:
        return _import_positions(
            sess,
            portfolio_id=portfolio_id,
            xlsx_path=xlsx_path,
            sheet=sheet,
            base_currency=base_currency,
        )


def import_market_inputs_from_xlsx(
    *,
    portfolio_id: int,
    xlsx_path: Path,
    sheet: str | None = None,
    valuation_date: date | None = None,
    session: Session | None = None,
) -> dict[str, int]:
    """Import per-position market inputs from an Excel workbook."""
    with _session_scope(session) as sess:
        return _import_market(
            sess,
            portfolio_id=portfolio_id,
            xlsx_path=xlsx_path,
            sheet=sheet,
            valuation_date=valuation_date,
        )
```

- [ ] **2.9: Commit positions service**

```bash
git add backend/app/services/domains/positions.py tests/test_services_domains_positions.py
git commit -m "refactor(positions): add services/domains/positions facade"
```

- [ ] **2.10: Write position tool wrappers**

Create `backend/app/tools/positions.py` mirroring the structure from `app/tools/portfolios.py` (see Task 1.15). Tool list and their signatures:

```python
@tool("get_positions", args_schema=GetPositionsInput)
def get_positions_tool(
    portfolio_id: int | None = None,
    product_type: str | None = None,
    status: str | None = "open",
    accounting_date: date | str | None = None,
    effective_date_from: date | str | None = None,
    effective_date_to: date | str | None = None,
    effective_last_days: int | None = None,
    positions: list[PortfolioPositionSpec] | None = None,
    market: PricingEnvironmentSnapshot = PricingEnvironmentSnapshot(),
) -> dict[str, Any]:
    # If `positions` is supplied (provided_context mode), shape the supplied list (no DB).
    # Otherwise call positions_svc.list_filtered and shape.
    ...

@tool("get_latest_position_valuations", args_schema=LatestValuationsInput)
def get_latest_position_valuations_tool(
    portfolio_id: int,
    limit: int = 50,
) -> dict[str, Any]:
    capped = min(max(limit, 1), 500)
    rows = positions_svc.latest_valuations(portfolio_id=portfolio_id, limit=capped)
    return shape_valuation_results(rows, limit=capped)

@tool("import_otc_positions", args_schema=ImportPositionsInput)
def import_otc_positions_tool(...) -> dict[str, Any]:
    counts = positions_svc.import_from_xlsx(...)
    return {"ok": True, **counts}

@tool("import_position_market_inputs", args_schema=ImportMarketInputsInput)
def import_position_market_inputs_tool(...) -> dict[str, Any]:
    counts = positions_svc.import_market_inputs_from_xlsx(...)
    return {"ok": True, **counts}
```

Open `backend/app/services/langchain_tools.py:329–490` and 643–768. Copy the existing `args_schema` Pydantic classes (`GetPositionsInput`, `LatestValuationsInput`, `ImportPositionsInput`, `ImportMarketInputsInput`) into `backend/app/tools/positions.py` (or import them from `app.schemas` if they live there). Copy the existing output-shaping code (the dict-building portions of each old @tool) into `app/tools/_shaping.py` under names like `shape_position_list`, `shape_valuation_results`. The tool bodies become 3-line `service call → shape → return`.

- [ ] **2.11: Write tool tests**

Create `tests/test_tools_positions.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.services.domains import portfolios as portfolios_svc
from app.tools.positions import get_positions_tool


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_get_positions_empty_portfolio():
    p = portfolios_svc.create(name="P", kind="container")
    result = get_positions_tool.invoke({"portfolio_id": p.id})
    assert result["total_count"] == 0
    assert result["positions"] == []
```

- [ ] **2.12: Run tests, verify pass**

```bash
uv run pytest tests/test_tools_positions.py tests/test_services_domains_positions.py -v
```

Expected: all pass.

- [ ] **2.13: Cut over the langchain_tools.py positions section**

In `backend/app/services/langchain_tools.py:329–490, 643–768`, replace the four @tool blocks with import-forwards from `app.tools.positions`:

```python
from app.tools.positions import (
    get_positions_tool as get_positions_tool,
    get_latest_position_valuations_tool as get_latest_position_valuations_tool,
    import_otc_positions_tool as import_otc_positions_tool,
    import_position_market_inputs_tool as import_position_market_inputs_tool,
)
```

Delete the old @tool function bodies, the now-unused helper `_filter_supplied_rows_by_effective_date`, `_position_in_effective_window`, and `_effective_date_window` (move these into `services/domains/positions.py` private helpers before deletion).

- [ ] **2.14: Run the existing positions tests**

```bash
uv run pytest tests/test_agent_tools.py tests/test_position_import_pricing.py -v
```

Expected: all pass.

- [ ] **2.15: Write positions CLI**

Create `backend/app/cli/positions.py`:

```python
"""Positions CLI commands."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from app.services.domains import portfolios as portfolios_svc
from app.services.domains import positions as positions_svc

from ._format import emit

app = typer.Typer(no_args_is_help=True)


@app.command("count")
def count_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    if p is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    n = positions_svc.count(portfolio_id=p.id)
    typer.echo(str(n))


@app.command("list")
def list_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    product_type: str = typer.Option(None, "--product-type"),
    status: str = typer.Option("open", "--status"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    if p is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    rows = positions_svc.list_filtered(
        portfolio_id=p.id, product_type=product_type, status=status,
    )
    emit(rows, as_json=json_output)


@app.command("import")
def import_cmd(
    xlsx: Path = typer.Option(..., "--xlsx"),
    portfolio: str = typer.Option(..., "--portfolio"),
    sheet: str = typer.Option(None, "--sheet"),
    base_currency: str = typer.Option("CNY", "--base-currency"),
) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    if p is None:
        typer.echo(f"portfolio {portfolio!r} not found", err=True)
        raise typer.Exit(1)
    counts = positions_svc.import_from_xlsx(
        portfolio_id=p.id,
        xlsx_path=xlsx,
        sheet=sheet or positions_svc.TRADE_SHEET,
        base_currency=base_currency,
    )
    typer.echo(str(counts))
```

Update `backend/app/cli/__init__.py` to register positions:

```python
from . import positions as positions_cmd
app.add_typer(positions_cmd.app, name="positions", help="Position operations")
```

Update `backend/app/cli.py` argparse main: delegate `positions` to the new Typer app (same pattern as 1.21 for portfolios).

- [ ] **2.16: Run CLI tests**

```bash
uv run pytest tests/ -k cli -v
```

Expected: all pass; existing position CLI behavior preserved.

- [ ] **2.17: Commit and open PR**

```bash
git add backend/app/tools/positions.py backend/app/cli/positions.py backend/app/cli/__init__.py backend/app/cli.py backend/app/services/langchain_tools.py backend/app/tools/_shaping.py tests/test_tools_positions.py
git commit -m "refactor(positions): extract services/domains, tools, cli (P1.2)"
git push
gh pr create --title "refactor(positions): extract services/domains, tools, cli (P1.2)" --body "Phase 1 PR #2 of 8. See spec docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md"
```

---

### Task 3 (P1.3) — Market Data domain

**Files:**
- Create: `backend/app/services/domains/market_data.py`
- Create: `backend/app/tools/market_data.py`
- Create: `backend/app/cli/market_data.py`
- Create: `tests/test_services_domains_market_data.py`
- Create: `tests/test_tools_market_data.py`
- Reference: `backend/app/services/market_data.py` (existing — repurposed as the engine; the new `services/domains/market_data.py` is a thin facade)
- Reference: `backend/app/services/langchain_tools.py:770–776` (current `fetch_market_snapshot` @tool)

**Market data tools to migrate (2):** `fetch_market_snapshot`, plus a new `list_market_data_profiles` for parity with the page contract.

**Service surface:**

```python
def fetch_snapshot(
    *,
    symbol: str,
    asset_class: str = "index",
    start_date: date,
    end_date: date,
    use_proxy: bool = False,
    session: Session | None = None,
) -> dict[str, Any]: ...  # raw snapshot dict (akshare's output shape)

def list_profiles(*, session: Session | None = None) -> list[MarketDataProfile]: ...
def get_profile(*, profile_id: int, session: Session | None = None) -> MarketDataProfile | None: ...
```

#### Steps

- [ ] **3.1: Write a failing test for `fetch_snapshot` (mocked akshare)**

Create `tests/test_services_domains_market_data.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.services.domains import market_data as md_svc


def test_fetch_snapshot_calls_akshare():
    fake_payload = {"symbol": "000852.SH", "close": 5300.0, "as_of": "2026-05-19"}
    with patch("app.services.market_data.fetch_akshare_snapshot", return_value=fake_payload) as m:
        result = md_svc.fetch_snapshot(
            symbol="000852.SH",
            asset_class="index",
            start_date=date(2026, 5, 19),
            end_date=date(2026, 5, 19),
        )
    assert result == fake_payload
    m.assert_called_once()
```

- [ ] **3.2: Run, verify fails**

```bash
uv run pytest tests/test_services_domains_market_data.py -v
```

Expected: ImportError.

- [ ] **3.3: Create the market_data domain service**

Create `backend/app/services/domains/market_data.py`:

```python
"""Market data domain service.

Thin facade over the existing services/market_data.py (akshare integration)
and market_data_profiles model.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import MarketDataProfile
from app.services.market_data import fetch_akshare_snapshot as _fetch


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def fetch_snapshot(
    *,
    symbol: str,
    asset_class: str = "index",
    start_date: date,
    end_date: date,
    use_proxy: bool = False,
    session: Session | None = None,
) -> dict[str, Any]:
    """Fetch a market snapshot from akshare for the given window."""
    return _fetch(
        symbol=symbol,
        asset_class=asset_class,
        start_date=start_date,
        end_date=end_date,
        use_proxy=use_proxy,
    )


def list_profiles(*, session: Session | None = None) -> list[MarketDataProfile]:
    with _session_scope(session) as sess:
        return sess.query(MarketDataProfile).order_by(MarketDataProfile.id.desc()).all()


def get_profile(*, profile_id: int, session: Session | None = None) -> MarketDataProfile | None:
    with _session_scope(session) as sess:
        return sess.get(MarketDataProfile, profile_id)
```

- [ ] **3.4: Run test, verify pass**

```bash
uv run pytest tests/test_services_domains_market_data.py -v
```

Expected: 1 passed.

- [ ] **3.5: Write tool wrapper**

Create `backend/app/tools/market_data.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool

from app.schemas import AkshareSnapshotRequest
from app.services.domains import market_data as md_svc


@tool("fetch_market_snapshot", args_schema=AkshareSnapshotRequest)
def fetch_market_snapshot_tool(**kwargs: Any) -> dict[str, Any]:
    """Fetch akshare snapshot for a symbol in a date window. Reads only."""
    return md_svc.fetch_snapshot(**kwargs)
```

- [ ] **3.6: Write tool test**

Create `tests/test_tools_market_data.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from app.tools.market_data import fetch_market_snapshot_tool


def test_fetch_market_snapshot_tool():
    with patch("app.services.market_data.fetch_akshare_snapshot", return_value={"close": 1.0}):
        result = fetch_market_snapshot_tool.invoke({
            "symbol": "000852.SH",
            "asset_class": "index",
            "start_date": date(2026, 5, 19),
            "end_date": date(2026, 5, 19),
        })
    assert result == {"close": 1.0}
```

- [ ] **3.7: Run tool test**

```bash
uv run pytest tests/test_tools_market_data.py -v
```

Expected: 1 passed.

- [ ] **3.8: Cut over langchain_tools.py**

In `backend/app/services/langchain_tools.py:770–776`, replace the @tool block with:

```python
from app.tools.market_data import fetch_market_snapshot_tool as fetch_market_snapshot_tool
```

- [ ] **3.9: Run existing market data tests**

```bash
uv run pytest tests/test_market_data.py tests/test_agent_tools.py -v
```

Expected: all pass.

- [ ] **3.10: Write CLI**

Create `backend/app/cli/market_data.py`:

```python
from __future__ import annotations

from datetime import date

import typer

from app.services.domains import market_data as md_svc

from ._format import emit

app = typer.Typer(no_args_is_help=True)


@app.command("fetch")
def fetch_cmd(
    symbol: str = typer.Option(..., "--symbol"),
    asset_class: str = typer.Option("index", "--asset-class"),
    start: date = typer.Option(..., "--start"),
    end: date = typer.Option(..., "--end"),
    use_proxy: bool = typer.Option(False, "--proxy"),
    json_output: bool = typer.Option(True, "--json"),
) -> None:
    result = md_svc.fetch_snapshot(
        symbol=symbol,
        asset_class=asset_class,
        start_date=start,
        end_date=end,
        use_proxy=use_proxy,
    )
    emit(result, as_json=json_output)


@app.command("profiles")
def profiles_cmd(json_output: bool = typer.Option(True, "--json")) -> None:
    rows = md_svc.list_profiles()
    emit([{"id": p.id, "name": p.name} for p in rows], as_json=json_output)
```

Register in `backend/app/cli/__init__.py`:

```python
from . import market_data as market_data_cmd
app.add_typer(market_data_cmd.app, name="market-data", help="Market data operations")
```

- [ ] **3.11: Commit and open PR**

```bash
git add backend/app/services/domains/market_data.py backend/app/tools/market_data.py backend/app/cli/market_data.py backend/app/cli/__init__.py backend/app/services/langchain_tools.py tests/test_services_domains_market_data.py tests/test_tools_market_data.py
git commit -m "refactor(market-data): extract services/domains, tools, cli (P1.3)"
git push
gh pr create --title "refactor(market-data): extract services/domains, tools, cli (P1.3)" --body "Phase 1 PR #3 of 8."
```

---

### Task 4 (P1.4) — Pricing domain

**Depends on:** P1.2 (uses `positions_svc.list_filtered`).

**Files:**
- Create: `backend/app/services/domains/pricing.py`
- Create: `backend/app/tools/pricing.py`
- Create: `backend/app/cli/pricing.py`
- Create: `tests/test_services_domains_pricing.py`
- Create: `tests/test_tools_pricing.py`
- Reference: `backend/app/services/quantark.py` (existing pricing engine module)
- Reference: `backend/app/services/position_pricer.py` (existing pricer)
- Reference: `backend/app/services/langchain_tools.py:229–262, 597–642` (pricing @tool definitions)

**Pricing tools to migrate (4):** `price_product`, `solve_rfq` (kept here for now; can move to rfq in P1.6 if cleaner — DECIDE during execution based on actual call graph), `price_positions`, `get_latest_position_valuations` (moved earlier in P1.2, so 3 net new here).

Net new: `price_product`, `price_positions`, plus the new `estimate_price_seconds` cost estimator.

**Service surface:**

```python
def price_product(
    *,
    spec: dict,
    market: PricingEnvironmentSnapshot,
    session: Session | None = None,
) -> dict[str, Any]: ...

def price_positions(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_profile_id: int | None = None,
    valuation_date: date | None = None,
    market_overrides: dict | None = None,
    session: Session | None = None,
) -> dict[str, Any]: ...

def estimate_price_seconds(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    session: Session | None = None,
) -> float:
    """Cost estimate: ~0.3s per position. Used by pet/desk cost-preview."""
```

#### Steps

- [ ] **4.1: Write failing test for `estimate_price_seconds`**

Create `tests/test_services_domains_pricing.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.services.domains import pricing as pricing_svc
from app.services.domains import portfolios as portfolios_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_estimate_price_seconds_empty_portfolio():
    p = portfolios_svc.create(name="P", kind="container")
    assert pricing_svc.estimate_price_seconds(portfolio_id=p.id) == 0.0


def test_estimate_price_seconds_proportional_to_count():
    # 10 positions × 0.3s/position == 3.0s
    p = portfolios_svc.create(name="P", kind="container")
    # ... insert 10 Position rows directly (similar pattern to Task 2.6)
    ...
    est = pricing_svc.estimate_price_seconds(portfolio_id=p.id)
    assert est == pytest.approx(3.0, rel=0.01)
```

- [ ] **4.2: Run test, verify fails**

```bash
uv run pytest tests/test_services_domains_pricing.py::test_estimate_price_seconds_empty_portfolio -v
```

Expected: ImportError.

- [ ] **4.3: Write pricing service skeleton + estimator + `price_product`**

Create `backend/app/services/domains/pricing.py`:

```python
"""Pricing domain service.

Facade over quantark.py + position_pricer.py + estimate helpers.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.schemas import PricingEnvironmentSnapshot
from app.services import quantark as _quantark
from app.services import position_pricer as _pricer
from app.services.domains import positions as positions_svc


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


_SECONDS_PER_POSITION = 0.3


def estimate_price_seconds(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    session: Session | None = None,
) -> float:
    """Cost estimate for `price_positions`. Used by cost-preview policy."""
    with _session_scope(session) as sess:
        if position_ids:
            return len(position_ids) * _SECONDS_PER_POSITION
        rows = positions_svc.list_filtered(portfolio_id=portfolio_id, session=sess)
        return len(rows) * _SECONDS_PER_POSITION


def price_product(
    *,
    spec: dict[str, Any],
    market: PricingEnvironmentSnapshot,
) -> dict[str, Any]:
    """Price an ad-hoc product spec under a market environment. Pure compute."""
    return _quantark.price_product_spec(spec, market.model_dump())


def price_positions(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_profile_id: int | None = None,
    valuation_date: date | None = None,
    market_overrides: dict | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Run a persisted pricing run on a portfolio's positions. HITL upstream."""
    with _session_scope(session) as sess:
        return _pricer.price_portfolio_positions(
            sess,
            portfolio_id=portfolio_id,
            position_ids=position_ids,
            pricing_profile_id=pricing_profile_id,
            valuation_date=valuation_date,
            market_overrides=market_overrides or {},
        )
```

- [ ] **4.4: Run tests, verify pass**

```bash
uv run pytest tests/test_services_domains_pricing.py -v
```

Expected: 2 passed.

- [ ] **4.5: Write pricing tool wrappers**

Create `backend/app/tools/pricing.py`. Migrate `@tool("price_product")` and `@tool("price_positions")` from `langchain_tools.py:229–262, 597–642`. Each wrapper:

```python
from app.services.domains import pricing as pricing_svc
from ._shaping import shape_pricing_run, shape_price_product_result

@tool("price_product", args_schema=PriceProductInput)
def price_product_tool(...) -> dict[str, Any]:
    result = pricing_svc.price_product(spec=..., market=...)
    return shape_price_product_result(result)

@tool("price_positions", args_schema=PricePositionsInput)
def price_positions_tool(...) -> dict[str, Any]:
    result = pricing_svc.price_positions(...)
    return shape_pricing_run(result)
```

Copy `PriceProductInput`, `PricePositionsInput` from `langchain_tools.py` or `app.schemas` into the tool module (or re-export from `app.schemas`).

- [ ] **4.6: Run tool tests**

```bash
uv run pytest tests/test_tools_pricing.py -v
```

- [ ] **4.7: Cut over langchain_tools.py**

In `backend/app/services/langchain_tools.py:229–262, 597–642`, replace the @tool blocks with import-forwards from `app.tools.pricing`. Delete `_derive_summary` helper if unused after migration (it shapes pricing results — likely moves to `_shaping.shape_pricing_run`).

- [ ] **4.8: Run existing pricing tests**

```bash
uv run pytest tests/test_position_import_pricing.py tests/test_position_pricer_adaptive.py tests/test_position_pricer_grid.py tests/test_quant_services.py tests/test_agent_tools.py -v
```

Expected: all pass.

- [ ] **4.9: Write pricing CLI**

Create `backend/app/cli/pricing.py`:

```python
import typer
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import pricing as pricing_svc
from ._format import emit

app = typer.Typer(no_args_is_help=True)


@app.command("estimate")
def estimate_cmd(portfolio: str = typer.Option(..., "--portfolio")) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    typer.echo(f"{pricing_svc.estimate_price_seconds(portfolio_id=p.id):.1f}s")


@app.command("run")
def run_cmd(
    portfolio: str = typer.Option(..., "--portfolio"),
    position_id: list[int] = typer.Option(None, "--position-id"),
    profile_id: int = typer.Option(None, "--pricing-profile-id"),
    valuation_date: str = typer.Option(None, "--valuation-date"),
    json_output: bool = typer.Option(True, "--json"),
) -> None:
    p = portfolios_svc.resolve(identifier=portfolio)
    from datetime import date
    vd = date.fromisoformat(valuation_date) if valuation_date else None
    result = pricing_svc.price_positions(
        portfolio_id=p.id,
        position_ids=list(position_id) if position_id else None,
        pricing_profile_id=profile_id,
        valuation_date=vd,
    )
    emit(result, as_json=json_output)
```

Register in `backend/app/cli/__init__.py`.

- [ ] **4.10: Commit and open PR**

```bash
git add backend/app/services/domains/pricing.py backend/app/tools/pricing.py backend/app/cli/pricing.py backend/app/cli/__init__.py backend/app/services/langchain_tools.py backend/app/tools/_shaping.py tests/test_services_domains_pricing.py tests/test_tools_pricing.py
git commit -m "refactor(pricing): extract services/domains, tools, cli (P1.4)"
git push
gh pr create --title "refactor(pricing): extract services/domains, tools, cli (P1.4)" --body "Phase 1 PR #4 of 8. Depends on P1.2."
```

---

### Task 5 (P1.5) — Risk domain

**Depends on:** P1.2 (positions count for estimator).

**Files:**
- Create: `backend/app/services/domains/risk.py`
- Create: `backend/app/tools/risk.py`
- Create: `backend/app/cli/risk.py`
- Create: `tests/test_services_domains_risk.py`
- Create: `tests/test_tools_risk.py`
- Reference: `backend/app/services/langchain_tools.py:561–576, 777–807, 973–1025` (risk @tool definitions)

**Risk tools to migrate (3 + estimator):** `calculate_risk`, `run_risk`, `get_latest_risk_run`, plus new `estimate_run_seconds`.

**Service surface:**

```python
def calculate_risk(*, snapshot: PortfolioSnapshotInput) -> dict[str, Any]: ...
def run(
    *,
    portfolio_id: int,
    pricing_profile_id: int | None = None,
    valuation_date: date | None = None,
    method: str = "summary",
    session: Session | None = None,
) -> dict[str, Any]: ...
def get_latest_run(*, portfolio_id: int, session: Session | None = None) -> RiskRun | None: ...
def estimate_run_seconds(*, portfolio_id: int, session: Session | None = None) -> float:
    """~0.5s per position."""
```

#### Steps

- [ ] **5.1: Write failing test for `estimate_run_seconds`**

Create `tests/test_services_domains_risk.py`:

```python
import pytest
from app import database
from app.config import Settings
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import risk as risk_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_estimate_run_seconds_zero_for_empty():
    p = portfolios_svc.create(name="P", kind="container")
    assert risk_svc.estimate_run_seconds(portfolio_id=p.id) == 0.0
```

- [ ] **5.2: Run, verify fails**

```bash
uv run pytest tests/test_services_domains_risk.py -v
```

- [ ] **5.3: Create risk service**

Create `backend/app/services/domains/risk.py`:

```python
"""Risk domain service."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RiskRun
from app.schemas import PortfolioSnapshotInput
from app.services.domains import positions as positions_svc
from app.services.deep_agent import risk as _risk  # existing risk dispatcher; adjust if path differs


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


_SECONDS_PER_POSITION = 0.5


def estimate_run_seconds(
    *,
    portfolio_id: int,
    session: Session | None = None,
) -> float:
    with _session_scope(session) as sess:
        rows = positions_svc.list_filtered(portfolio_id=portfolio_id, session=sess)
        return len(rows) * _SECONDS_PER_POSITION


def calculate_risk(*, snapshot: PortfolioSnapshotInput) -> dict[str, Any]:
    """In-memory risk calc on a supplied portfolio snapshot. No DB."""
    return _risk.calculate(snapshot)


def run(
    *,
    portfolio_id: int,
    pricing_profile_id: int | None = None,
    valuation_date: date | None = None,
    method: str = "summary",
    session: Session | None = None,
) -> dict[str, Any]:
    """Persisted risk run. Returns risk_run_id + task_id."""
    with _session_scope(session) as sess:
        return _risk.dispatch_run(
            sess,
            portfolio_id=portfolio_id,
            pricing_profile_id=pricing_profile_id,
            valuation_date=valuation_date,
            method=method,
        )


def get_latest_run(*, portfolio_id: int, session: Session | None = None) -> RiskRun | None:
    with _session_scope(session) as sess:
        return (
            sess.query(RiskRun)
            .filter(RiskRun.portfolio_id == portfolio_id)
            .order_by(RiskRun.id.desc())
            .first()
        )
```

> Important: `_risk.calculate` and `_risk.dispatch_run` may not exist under those names. Open `backend/app/services/langchain_tools.py:561–576, 973–1025` and trace what the old `calculate_risk_tool` and `run_risk_tool` call. Extract those calls into helper functions in `services/risk_dispatch.py` (new file) if needed.

- [ ] **5.4: Run test, verify pass**

```bash
uv run pytest tests/test_services_domains_risk.py -v
```

- [ ] **5.5: Write risk tool wrappers**

Create `backend/app/tools/risk.py`. Migrate three @tool functions from `langchain_tools.py:561–576, 777–807, 973–1025`. Pattern (5–10 lines each):

```python
@tool("calculate_risk", args_schema=PortfolioSnapshotInput)
def calculate_risk_tool(...) -> dict[str, Any]:
    return risk_svc.calculate_risk(snapshot=...)

@tool("run_risk", args_schema=RunRiskInput)
def run_risk_tool(...) -> dict[str, Any]:
    return risk_svc.run(...)

@tool("get_latest_risk_run", args_schema=LatestRiskRunInput)
def get_latest_risk_run_tool(portfolio_id: int) -> dict[str, Any]:
    rr = risk_svc.get_latest_run(portfolio_id=portfolio_id)
    return shape_risk_run(rr)

@tool("recommend_hedge", args_schema=HedgeInput)
def recommend_hedge_tool(risk: dict[str, Any]) -> dict[str, Any]:
    return risk_svc.recommend_hedge(risk=risk)  # if used; otherwise drop
```

- [ ] **5.6: Cut over + run tests**

```bash
# Replace @tool blocks in langchain_tools.py with imports from app.tools.risk
# Run:
uv run pytest tests/test_agent_tools.py tests/test_async_agents_tools.py -v
```

- [ ] **5.7: CLI**

Create `backend/app/cli/risk.py` with `estimate`, `run`, `latest` commands. Pattern as in Task 4.9.

- [ ] **5.8: Commit and open PR**

```bash
git commit -am "refactor(risk): extract services/domains, tools, cli (P1.5)"
git push
gh pr create --title "refactor(risk): extract services/domains, tools, cli (P1.5)" --body "Phase 1 PR #5 of 8. Depends on P1.2."
```

---

### Task 6 (P1.6) — RFQ domain

**Files:**
- Create: `backend/app/services/domains/rfq.py`
- Create: `backend/app/tools/rfq.py`
- Create: `backend/app/cli/rfq.py`
- Create: `tests/test_services_domains_rfq.py`
- Create: `tests/test_tools_rfq.py`
- Reference: `backend/app/services/rfq.py` (existing 1301-line service module — re-namespaced)
- Reference: `backend/app/services/langchain_tools.py:241–328, 1078–1205` (RFQ @tool definitions)

**RFQ tools to migrate (12):** `solve_rfq`, `get_rfq_catalog`, `draft_rfq_from_natural_language`, `validate_rfq_terms`, `create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`, `approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`, `book_rfq_to_position`.

This is the largest single PR in Phase 1. The existing `services/rfq.py` already implements most logic; `services/domains/rfq.py` is a near-facade.

#### Steps

- [ ] **6.1: Create the rfq domain service as a thin re-export**

Create `backend/app/services/domains/rfq.py`:

```python
"""RFQ domain service — thin facade over services/rfq.py."""
from __future__ import annotations

from app.services import rfq as _rfq

# Re-export the existing service functions under domain-style names.
solve = _rfq.solve_request
catalog = _rfq.get_catalog
draft_from_natural_language = _rfq.draft_from_natural_language
validate_terms = _rfq.validate_terms
draft_create_or_update = _rfq.create_or_update_draft
quote = _rfq.quote
submit_for_approval = _rfq.submit_for_approval
approve = _rfq.approve
reject = _rfq.reject
release = _rfq.release
mark_client_accepted = _rfq.mark_client_accepted
book_to_position = _rfq.book_to_position
```

> If the existing `services/rfq.py` does NOT expose any of these function names, open the file and either rename or add a thin wrapper. The domain facade exposes a STABLE name set; the underlying implementation can keep its internal naming.

- [ ] **6.2: Write a smoke test verifying the facade compiles and dispatches**

Create `tests/test_services_domains_rfq.py`:

```python
from unittest.mock import patch
from app.services.domains import rfq as rfq_svc


def test_catalog_calls_underlying():
    with patch("app.services.rfq.get_catalog", return_value={"products": []}) as m:
        result = rfq_svc.catalog()
    assert result == {"products": []}
    m.assert_called_once()
```

```bash
uv run pytest tests/test_services_domains_rfq.py -v
```

Expected: 1 passed.

- [ ] **6.3: Write all 12 tool wrappers**

Create `backend/app/tools/rfq.py`. For each of the 12 tools, copy the existing args_schema class from `langchain_tools.py` or `app.schemas`, and write a wrapper of the form:

```python
@tool("solve_rfq", args_schema=RFQRequestDraft)
def solve_rfq_tool(**kwargs: Any) -> dict[str, Any]:
    return rfq_svc.solve(**kwargs)


@tool("get_rfq_catalog")
def get_rfq_catalog_tool() -> dict[str, Any]:
    return rfq_svc.catalog()


@tool("create_or_update_rfq_draft", args_schema=CreateOrUpdateRfqDraftInput)
def create_or_update_rfq_draft_tool(...) -> dict[str, Any]:
    return rfq_svc.draft_create_or_update(...)


# ... 9 more, each 3-5 lines
```

For each, include the `args_schema=...` exactly as in `langchain_tools.py` so existing tests pass unchanged.

- [ ] **6.4: Cut over langchain_tools.py and run all RFQ-touching tests**

Replace lines 241–328 and 1078–1205 in `langchain_tools.py` with re-exports from `app.tools.rfq`. Run:

```bash
uv run pytest tests/ -k "rfq or async_agents_hitl or agent_tools" -v
```

Expected: all pass.

- [ ] **6.5: Write RFQ CLI (5 most common operations)**

Create `backend/app/cli/rfq.py` with commands: `catalog`, `draft`, `quote`, `approve`, `reject`. Each is ~10 lines, following the Task 4.9 pattern. Less-common lifecycle actions (`release`, `mark-client-accepted`, `book-to-position`) are deferred to a follow-up if not requested.

- [ ] **6.6: Commit and open PR**

```bash
git commit -am "refactor(rfq): extract services/domains, tools, cli (P1.6)"
git push
gh pr create --title "refactor(rfq): extract services/domains, tools, cli (P1.6)" --body "Phase 1 PR #6 of 8. Migrates 12 RFQ tools."
```

---

### Task 7 (P1.7) — Reporting domain

**Files:**
- Create: `backend/app/services/domains/reporting.py`
- Create: `backend/app/tools/reporting.py`
- Create: `backend/app/cli/reporting.py`
- Create: `tests/test_services_domains_reporting.py`
- Create: `tests/test_tools_reporting.py`
- Reference: `backend/app/services/langchain_tools.py:582–596, 809–972, 1026–1077` (reporting @tool definitions)

**Reporting tools to migrate (4):** `run_report_batch`, `list_reports`, `get_report`, `create_report`.

**Service surface:**

```python
def list_reports(*, kind: str | None = None, limit: int = 50, session: Session | None = None) -> list[ReportJob]: ...
def get_report(*, report_id: int, session: Session | None = None) -> ReportJob | None: ...
def create_report(
    *,
    portfolio_id: int,
    kind: str,
    valuation_date: date | None = None,
    pricing_profile_id: int | None = None,
    title: str | None = None,
    session: Session | None = None,
) -> dict[str, Any]: ...
def run_batch(*, batch_spec: dict, session: Session | None = None) -> dict[str, Any]: ...
```

#### Steps

- [ ] **7.1: Service + tests** (follow the pattern from Task 1.4–1.10)

Create `backend/app/services/domains/reporting.py` with the four functions above. Tests: write at minimum `test_list_reports_empty`, `test_get_report_not_found`, `test_create_report_returns_id`.

- [ ] **7.2: Tool wrappers**

Create `backend/app/tools/reporting.py` with four `@tool` definitions, each ≤10 lines.

- [ ] **7.3: Cut over langchain_tools.py**

Replace lines 582–596, 809–972, 1026–1077 with import-forwards.

- [ ] **7.4: CLI**

Create `backend/app/cli/reporting.py` with `list`, `show`, `create`, `batch-run` commands.

- [ ] **7.5: Run all reporting tests**

```bash
uv run pytest tests/test_langchain_report_tools.py tests/test_agent_tools.py -v
```

- [ ] **7.6: Commit and open PR**

```bash
git commit -am "refactor(reporting): extract services/domains, tools, cli (P1.7)"
git push
gh pr create --title "refactor(reporting): extract services/domains, tools, cli (P1.7)" --body "Phase 1 PR #7 of 8."
```

---

### Task 8 (P1.8) — Cleanup: delete `langchain_tools.py`

**Depends on:** P1.1–P1.7 all merged.

**Files:**
- Delete: `backend/app/services/langchain_tools.py`
- Modify: `backend/app/services/agents.py` — update tool imports
- Modify: `backend/app/services/deep_agent/personas.py` — update tool imports
- Modify: `backend/app/services/async_agents/agent.py` — update tool imports
- Modify: `tests/test_agent_tools.py` — update imports
- Modify: `tests/test_async_agents_tools.py` — update imports
- Modify: `tests/test_langchain_report_tools.py` — update imports
- Modify: `tests/test_position_import_pricing.py` — update imports
- Modify: `tests/test_personas.py` — update imports
- Modify: `tests/test_quant_services.py` — update imports
- Modify: `tests/test_reply_options_tool.py` — update imports

#### Steps

- [ ] **8.1: Verify langchain_tools.py is now ONLY import-forwards**

Run:

```bash
grep -nE "^@tool|^def [a-z]+_tool" backend/app/services/langchain_tools.py
```

Expected: zero results. If any remain, finish migrating them via the appropriate P1.x PR before proceeding.

- [ ] **8.2: List all importers**

```bash
grep -rn "from .*langchain_tools" backend/app/ tests/
```

Save this list. Each line will be edited in steps 8.3–8.5.

- [ ] **8.3: Build an import map**

For each tool currently imported from `langchain_tools`, find its new home in `app/tools/<domain>`. The mapping is:

| Tool | New module |
|---|---|
| `list_portfolios_tool`, `get_portfolio_tool`, `create_portfolio_tool`, `update_portfolio_tool`, `delete_portfolio_tool`, `set_portfolio_rule_tool`, `add_positions_to_portfolio_tool`, `remove_positions_from_portfolio_tool`, `add_portfolio_sources_tool`, `remove_portfolio_sources_tool` | `app.tools.portfolios` |
| `get_positions_tool`, `get_latest_position_valuations_tool`, `import_otc_positions_tool`, `import_position_market_inputs_tool` | `app.tools.positions` |
| `fetch_market_snapshot_tool` | `app.tools.market_data` |
| `price_product_tool`, `price_positions_tool` | `app.tools.pricing` |
| `calculate_risk_tool`, `run_risk_tool`, `get_latest_risk_run_tool`, `recommend_hedge_tool` | `app.tools.risk` |
| `solve_rfq_tool`, `get_rfq_catalog_tool`, `draft_rfq_from_natural_language_tool`, `validate_rfq_terms_tool`, `create_or_update_rfq_draft_tool`, `quote_rfq_tool`, `submit_rfq_for_approval_tool`, `approve_rfq_tool`, `reject_rfq_tool`, `release_rfq_tool`, `mark_rfq_client_accepted_tool`, `book_rfq_to_position_tool` | `app.tools.rfq` |
| `list_reports_tool`, `get_report_tool`, `create_report_tool`, `run_report_batch_tool` | `app.tools.reporting` |

- [ ] **8.4: Write a codemod script to rewrite imports**

Create `scripts/codemod_langchain_tools_imports.py`:

```python
"""Rewrite `from app.services.langchain_tools import X, Y` to per-domain imports.

Usage: uv run python scripts/codemod_langchain_tools_imports.py
"""
from __future__ import annotations

import re
from pathlib import Path

IMPORT_MAP = {
    # portfolios
    "list_portfolios_tool": "app.tools.portfolios",
    "get_portfolio_tool": "app.tools.portfolios",
    "create_portfolio_tool": "app.tools.portfolios",
    "update_portfolio_tool": "app.tools.portfolios",
    "delete_portfolio_tool": "app.tools.portfolios",
    "set_portfolio_rule_tool": "app.tools.portfolios",
    "add_positions_to_portfolio_tool": "app.tools.portfolios",
    "remove_positions_from_portfolio_tool": "app.tools.portfolios",
    "add_portfolio_sources_tool": "app.tools.portfolios",
    "remove_portfolio_sources_tool": "app.tools.portfolios",
    # positions
    "get_positions_tool": "app.tools.positions",
    "get_latest_position_valuations_tool": "app.tools.positions",
    "import_otc_positions_tool": "app.tools.positions",
    "import_position_market_inputs_tool": "app.tools.positions",
    # market_data
    "fetch_market_snapshot_tool": "app.tools.market_data",
    # pricing
    "price_product_tool": "app.tools.pricing",
    "price_positions_tool": "app.tools.pricing",
    # risk
    "calculate_risk_tool": "app.tools.risk",
    "run_risk_tool": "app.tools.risk",
    "get_latest_risk_run_tool": "app.tools.risk",
    "recommend_hedge_tool": "app.tools.risk",
    # rfq
    "solve_rfq_tool": "app.tools.rfq",
    "get_rfq_catalog_tool": "app.tools.rfq",
    "draft_rfq_from_natural_language_tool": "app.tools.rfq",
    "validate_rfq_terms_tool": "app.tools.rfq",
    "create_or_update_rfq_draft_tool": "app.tools.rfq",
    "quote_rfq_tool": "app.tools.rfq",
    "submit_rfq_for_approval_tool": "app.tools.rfq",
    "approve_rfq_tool": "app.tools.rfq",
    "reject_rfq_tool": "app.tools.rfq",
    "release_rfq_tool": "app.tools.rfq",
    "mark_rfq_client_accepted_tool": "app.tools.rfq",
    "book_rfq_to_position_tool": "app.tools.rfq",
    # reporting
    "list_reports_tool": "app.tools.reporting",
    "get_report_tool": "app.tools.reporting",
    "create_report_tool": "app.tools.reporting",
    "run_report_batch_tool": "app.tools.reporting",
}

IMPORT_RE = re.compile(
    r"from\s+(?:app\.services|\.\.services|\.)\.langchain_tools\s+import\s+\(([^)]*)\)|"
    r"from\s+(?:app\.services|\.\.services|\.)\.langchain_tools\s+import\s+(.+)"
)


def rewrite(src: str) -> str:
    def replace(match: re.Match[str]) -> str:
        names_block = match.group(1) or match.group(2)
        names = [n.strip().split(" as ")[0] for n in names_block.replace("\n", "").split(",") if n.strip()]
        by_module: dict[str, list[str]] = {}
        for n in names:
            mod = IMPORT_MAP.get(n)
            if mod is None:
                raise ValueError(f"unknown tool {n!r}")
            by_module.setdefault(mod, []).append(n)
        lines = []
        for mod in sorted(by_module):
            lines.append(f"from {mod} import {', '.join(sorted(by_module[mod]))}")
        return "\n".join(lines)
    return IMPORT_RE.sub(replace, src)


def main() -> None:
    roots = [Path("backend/app"), Path("tests")]
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text()
            new_text = rewrite(text)
            if new_text != text:
                path.write_text(new_text)
                print(f"updated {path}")


if __name__ == "__main__":
    main()
```

- [ ] **8.5: Run the codemod**

```bash
uv run python scripts/codemod_langchain_tools_imports.py
```

Verify the diff:

```bash
git diff --stat
```

Expected: all of the files from step 8.2 have updated import statements.

- [ ] **8.6: Delete langchain_tools.py**

```bash
git rm backend/app/services/langchain_tools.py
```

- [ ] **8.7: Run the full agent test suite**

```bash
uv run pytest tests/ -v
```

Expected: ALL tests pass. If any fail with `ImportError`, the codemod missed a case — add the missing tool to `IMPORT_MAP` and re-run.

- [ ] **8.8: Run a smoke test against the live agent**

Start the dev server briefly and run a single agent prompt to verify imports resolve at runtime:

```bash
uv run uvicorn backend.app.main:app --port 8000 &
sleep 3
curl -X POST http://localhost:8000/agent/respond \
    -H "Content-Type: application/json" \
    -d '{"thread_id":1,"message":"list portfolios","character":"trader"}' \
    && kill %1
```

Expected: 200 response, no `ImportError` in server logs.

- [ ] **8.9: Commit and open PR**

```bash
git add -A
git commit -m "refactor: delete langchain_tools.py; all imports use app/tools (P1.8)"
git push
gh pr create --title "refactor: delete langchain_tools.py (P1.8)" --body "$(cat <<'EOF'
## Summary
- Delete the 1608-line `backend/app/services/langchain_tools.py`
- Update all 8 callers to import from `app/tools/<domain>` directly
- Codemod script at `scripts/codemod_langchain_tools_imports.py` (for reference)

This is the load-bearing flip for Phase 1. After this PR, the service layer is the source of truth for every business operation.

## Test plan
- [ ] Full pytest suite passes (`uv run pytest tests/`)
- [ ] Live agent smoke test (list portfolios, count positions, get latest risk run)
- [ ] No `ImportError` in production logs for 24 hours after deploy

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

Before declaring this plan done, walk back through it:

**Spec coverage**

| Spec requirement | Plan task |
|---|---|
| `services/domains/<domain>.py` exists for portfolios, positions, market_data, pricing, risk, rfq, reporting | Tasks 1–7 |
| Service contract (pure I/O, ORM objects, session-aware) | Conventions section + every service module |
| `app/tools/<domain>.py` thin wrappers (≤30 lines) | Tasks 1.15, 2.10, 3.5, 4.5, 5.5, 6.3, 7.2 |
| Typer-based CLI mirroring tools 1:1 | Tasks 1.20, 2.15, 3.10, 4.9, 5.7, 6.5, 7.4 |
| CLI is a sibling to tools (both call services); does NOT shell out | Conventions section; CLI commands import services directly |
| `tests/test_agent_tools.py`, `tests/test_async_agents_*.py` pass after each PR | Each PR's gate step |
| `langchain_tools.py` deleted at end | Task 8 |
| Codemod for atomic import flip | Task 8.4 |
| `estimate_run_seconds`, `estimate_price_seconds` cost estimators | Tasks 4.3, 5.3 |
| `count_from_snapshot` (pure, no DB) | Task 2.3 |
| Existing service modules (`portfolio_service.py`, `rfq.py`, `quantark.py`, `market_data.py`, etc.) NOT migrated; new facades call into them | Conventions + each task references the existing modules |

**Placeholder scan**

- [x] No "TBD" or "TODO" placeholders.
- [x] One soft handwave in Task 6.1 ("If the existing services/rfq.py does NOT expose any of these function names…") — engineer must inspect and adapt. This is acceptable because the rfq.py file is 1301 lines; enumerating every name in advance is brittle.
- [x] One in Task 5.3 ("`_risk.calculate` and `_risk.dispatch_run` may not exist under those names…"). Same justification.
- [x] One in Task 4.5 ("DECIDE during execution based on actual call graph") for `solve_rfq` placement. This is a real judgement call between domains — best made in flight.

**Type consistency**

- Service functions always return ORM objects, lists, or primitives. Verified in every service module's docstring/body.
- Tool wrappers always return `dict[str, Any]`. Verified in every `@tool` body.
- Estimator names: `estimate_price_seconds` (Task 4) and `estimate_run_seconds` (Task 5). Consistent.
- Resolver names: `portfolios.resolve(identifier=...)` and `portfolios.get(portfolio_id=...)` and `portfolios.get_by_name(name=...)`. Used consistently in CLI commands.

**Gaps found and fixed inline**

- None major. The handwaves above are noted but not resolvable without code inspection at execution time.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-phase-1-services-tools-cli.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each PR's subagent gets ONLY that task's section plus the conventions section + the spec.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Better for a hands-on day where you want to see each test run.

**Which approach?**
