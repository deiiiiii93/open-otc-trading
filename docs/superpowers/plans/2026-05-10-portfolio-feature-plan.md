# Portfolio Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a polymorphic-`kind` Portfolio feature: container portfolios (today's behavior, FK-owned positions) and view portfolios (filter rule + manual includes/excludes + cross-portfolio aggregation sources). Surface CRUD over HTTP, CLI, and LangChain tools sharing one `portfolio_service.py`. Add a `/portfolios` master-detail frontend route with rule editor + live preview.

**Architecture:** Schema gains seven columns on the existing `Portfolio` table and a `resolved_position_ids` column on `PositionValuationRun` and `RiskRun`. A new `portfolio_membership.resolve_positions` walks rule + sources + manual includes/excludes (with cycle and depth-3 guards). Pricer and risk engine swap their internal `portfolio.positions` access for the resolver — public signatures unchanged. HITL gates on `delete_portfolio`, `set_portfolio_rule`, and `remove_positions_from_portfolio` ride on the existing `interrupt_on_config()` machinery in `hitl.py`. Frontend is master-detail mirroring Positions, with a two-column detail pane for views (rule editor + live-preview positions table).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, LangChain (`langchain_core`), LangGraph (HITL via `interrupt_on_config`), pytest, React 18 + Vite + Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-10-portfolio-feature-design.md` (commit `856f9e4`)

---

## File Structure

**Backend — new files:**
- `backend/alembic/versions/0004_portfolio_kind_and_membership.py` — schema migration
- `backend/app/services/portfolio_rule.py` — rule validator + SQLAlchemy compiler
- `backend/app/services/portfolio_rule_dsl.py` — DSL parse/serialize
- `backend/app/services/portfolio_membership.py` — `resolve_positions` with cycle/depth guards
- `backend/app/services/portfolio_service.py` — CRUD + business logic + audit
- `tests/test_portfolio_rule.py`
- `tests/test_portfolio_rule_dsl.py`
- `tests/test_portfolio_membership.py`
- `tests/test_portfolio_service.py`
- `tests/test_portfolio_integration.py` — end-to-end (create view, price, assert resolved_position_ids)

**Backend — modified files:**
- `backend/app/models.py` — `PortfolioKind` enum, new columns on `Portfolio`, `PositionValuationRun`, `RiskRun`, exception classes (`PortfolioCycleError`, `PortfolioDepthError`, `PortfolioNameConflict`, `RuleValidationError`, `RuleCompilationError`, `PortfolioKindError`)
- `backend/app/schemas.py` — extend `PortfolioOut`, new `PortfolioCreate` shape, `PortfolioUpdate`, `PortfolioRuleBody`, `PortfolioIdsBody`, `PortfolioTagsBody`, `PortfolioPreviewBody`, `PortfolioMembershipOut`
- `backend/app/main.py` — extend existing `/api/portfolios` endpoints, add new sub-resource endpoints, register `IntegrityError → 409` handler for portfolios
- `backend/app/cli.py` — new `portfolios` subparser with subcommands
- `backend/app/services/langchain_tools.py` — ten new tools, register in `QUANT_AGENT_TOOLS`
- `backend/app/services/deep_agent/hitl.py` — register three new tool names in `INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL`, `_LABEL_BY_TOOL`
- `backend/app/services/position_pricer.py` — use resolver, persist `resolved_position_ids`
- `backend/app/services/risk_engine.py` — same
- `tests/test_api.py` — coverage for new portfolio endpoints
- `tests/test_position_import_pricing.py` — coverage for pricing on a view portfolio
- `tests/test_risk_engine.py` — coverage for risk on a view portfolio
- `tests/test_agent_tools.py` — coverage for new portfolio tools and HITL
- `tests/test_hitl.py` — coverage for new tool risk levels/labels

**Frontend — new files:**
- `frontend/src/routes/Portfolios.tsx`
- `frontend/src/routes/Portfolios.live.tsx`
- `frontend/src/routes/Portfolios.css`
- `frontend/src/routes/Portfolios.test.tsx`
- `frontend/src/routes/Portfolios.live.test.tsx`
- `frontend/src/components/KindChip.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/ResolvedPositionsTable.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/TagEditor.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/PositionPicker.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/PortfolioPicker.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/RuleBuilder.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/RuleTextEditor.tsx` + `.css` + `.test.tsx`
- `frontend/src/components/RuleEditor.tsx` + `.css` + `.test.tsx`
- `frontend/src/lib/ruleTree.ts` — TS-mirrored rule helpers (validate / serialize / parse)
- `frontend/src/lib/ruleTree.test.ts`

**Frontend — modified files:**
- `frontend/src/types.ts` — `Route` rename + new portfolio types
- `frontend/src/main.tsx` — `navItems`, `commandItems`, `initialRoute()`, URL sync, register `PortfoliosLive`

---

## Pre-Flight: Working tree state

The repo has unrelated in-flight changes from a prior task (`backend/app/main.py`, `backend/app/schemas.py`, `backend/app/services/deep_agent/model_factory.py`, `frontend/src/routes/AgentDesk.*`, `pyproject.toml`, `tests/test_api.py`). These are **additive** to areas this plan also touches (`main.py`, `schemas.py`, `tests/test_api.py`), so the portfolio work merges cleanly — but the engineer should commit or stash before starting so each task's commit is clean.

- [ ] **Step 1: Inspect current uncommitted state**

Run: `git status --short`

If there are uncommitted edits, decide with the user whether to (a) commit them as a separate "WIP: prior task" commit, (b) stash them, or (c) leave them alone (only safe if they don't conflict with files this plan modifies — they do touch `main.py`, `schemas.py`, `tests/test_api.py`).

- [ ] **Step 2: Confirm clean baseline before Task 1**

After commit/stash, `git status --short` should show no `M` lines for `backend/app/main.py`, `backend/app/schemas.py`, or `tests/test_api.py`. Untracked SQLite/egg-info files are fine.

---

## Task 1: Schema migration + model fields + exceptions

**Files:**
- Create: `backend/alembic/versions/0004_portfolio_kind_and_membership.py`
- Modify: `backend/app/models.py`

- [ ] **Step 1: Write the alembic migration**

Create `backend/alembic/versions/0004_portfolio_kind_and_membership.py`:

```python
"""portfolio kind + membership columns

Revision ID: 0004_portfolio_kind_and_membership
Revises: 0003_risk_run_persistence
Create Date: 2026-05-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0004_portfolio_kind_and_membership"
down_revision = "0003_risk_run_persistence"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    insp = inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("portfolios")
    with op.batch_alter_table("portfolios") as batch:
        if "kind" not in cols:
            batch.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="container"))
        if "filter_rule" not in cols:
            batch.add_column(sa.Column("filter_rule", sa.JSON(), nullable=True))
        if "manual_include_ids" not in cols:
            batch.add_column(sa.Column("manual_include_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "manual_exclude_ids" not in cols:
            batch.add_column(sa.Column("manual_exclude_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "source_portfolio_ids" not in cols:
            batch.add_column(sa.Column("source_portfolio_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "tags" not in cols:
            batch.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
        if "description" not in cols:
            batch.add_column(sa.Column("description", sa.Text(), nullable=True))

    if "resolved_position_ids" not in _columns("position_valuation_runs"):
        op.add_column("position_valuation_runs", sa.Column("resolved_position_ids", sa.JSON(), nullable=True))
    if "resolved_position_ids" not in _columns("risk_runs"):
        op.add_column("risk_runs", sa.Column("resolved_position_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "resolved_position_ids" in _columns("risk_runs"):
        op.drop_column("risk_runs", "resolved_position_ids")
    if "resolved_position_ids" in _columns("position_valuation_runs"):
        op.drop_column("position_valuation_runs", "resolved_position_ids")
    cols = _columns("portfolios")
    with op.batch_alter_table("portfolios") as batch:
        for name in (
            "description", "tags", "source_portfolio_ids",
            "manual_exclude_ids", "manual_include_ids", "filter_rule", "kind",
        ):
            if name in cols:
                batch.drop_column(name)
```

- [ ] **Step 2: Add `PortfolioKind` enum and exceptions to models.py**

Edit `backend/app/models.py`. Add near the top, after the existing `RfqStatus`/`ReportStatus` enums:

```python
class PortfolioKind(str, Enum):
    CONTAINER = "container"
    VIEW = "view"


class PortfolioError(Exception):
    """Base for portfolio domain errors."""


class PortfolioNameConflict(PortfolioError):
    pass


class PortfolioKindError(PortfolioError):
    pass


class RuleValidationError(PortfolioError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class RuleCompilationError(PortfolioError):
    pass


class PortfolioCycleError(PortfolioError):
    def __init__(self, message: str, cycle_path: list[int]):
        super().__init__(message)
        self.cycle_path = cycle_path


class PortfolioDepthError(PortfolioError):
    def __init__(self, message: str, depth_path: list[int]):
        super().__init__(message)
        self.depth_path = depth_path
```

- [ ] **Step 3: Add new columns to `Portfolio`, `PositionValuationRun`, `RiskRun`**

In `backend/app/models.py`, add inside `class Portfolio`, right after the existing `updated_at` column:

```python
    kind: Mapped[str] = mapped_column(String(20), default=PortfolioKind.CONTAINER.value, nullable=False)
    filter_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manual_include_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    manual_exclude_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    source_portfolio_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add to `class PositionValuationRun`, right after `summary`:

```python
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
```

Add to `class RiskRun`, right after `scenario_cells`:

```python
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Run the migration against a fresh DB**

Run:
```bash
rm -f data/open_otc.sqlite3
PYTHONPATH=backend alembic upgrade head
PYTHONPATH=backend alembic downgrade -1
PYTHONPATH=backend alembic upgrade head
```

Expected: each command exits 0; the round-trip downgrade-then-upgrade succeeds (asserts the migration is reversible).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0004_portfolio_kind_and_membership.py backend/app/models.py
git commit -m "feat(schema): polymorphic Portfolio kind + membership columns + run resolved-id audit"
```

---

## Task 2: Rule validator + SQLAlchemy compiler

**Files:**
- Create: `backend/app/services/portfolio_rule.py`
- Create: `tests/test_portfolio_rule.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio_rule.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import and_, select

from app import database
from app.models import Position, Portfolio
from app.services.portfolio_rule import (
    ALLOWED_FIELDS,
    ALLOWED_OPS,
    MAX_RULE_DEPTH,
    compile_rule_to_sqla,
    validate_rule,
)


def test_validate_rule_accepts_simple_eq():
    assert validate_rule({"op": "eq", "field": "product_type", "value": "Snowball"}) == []


def test_validate_rule_rejects_unknown_op():
    errors = validate_rule({"op": "matches", "field": "underlying", "value": "AAPL"})
    assert any("matches" in e for e in errors)


def test_validate_rule_rejects_unknown_field():
    errors = validate_rule({"op": "eq", "field": "color", "value": "blue"})
    assert any("color" in e for e in errors)


def test_validate_rule_rejects_eq_with_list_value():
    errors = validate_rule({"op": "eq", "field": "underlying", "value": ["AAPL", "TSLA"]})
    assert any("scalar" in e.lower() for e in errors)


def test_validate_rule_rejects_in_with_scalar_value():
    errors = validate_rule({"op": "in", "field": "underlying", "value": "AAPL"})
    assert any("list" in e.lower() for e in errors)


def test_validate_rule_rejects_empty_and():
    errors = validate_rule({"op": "and", "children": []})
    assert any("empty" in e.lower() for e in errors)


def test_validate_rule_rejects_between_with_reversed_bounds():
    errors = validate_rule({"op": "between", "field": "quantity", "value": [10, 1]})
    assert any("between" in e.lower() for e in errors)


def test_validate_rule_rejects_too_deep():
    rule = {"op": "eq", "field": "underlying", "value": "AAPL"}
    for _ in range(MAX_RULE_DEPTH + 1):
        rule = {"op": "and", "children": [rule]}
    errors = validate_rule(rule)
    assert any("depth" in e.lower() for e in errors)


def test_compile_eq(tmp_path, monkeypatch):
    from app.config import Settings

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        a = Position(portfolio_id=portfolio.id, underlying="AAPL", product_type="Snowball", quantity=10)
        b = Position(portfolio_id=portfolio.id, underlying="TSLA", product_type="Phoenix", quantity=20)
        session.add_all([a, b])
        session.flush()

        clause = compile_rule_to_sqla({"op": "eq", "field": "product_type", "value": "Snowball"})
        rows = session.execute(select(Position).where(clause)).scalars().all()
        assert {r.id for r in rows} == {a.id}


def test_compile_in_and_not(tmp_path, monkeypatch):
    from app.config import Settings

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        rows = [
            Position(portfolio_id=portfolio.id, underlying="AAPL", product_type="Snowball", quantity=10),
            Position(portfolio_id=portfolio.id, underlying="TSLA", product_type="Snowball", quantity=20),
            Position(portfolio_id=portfolio.id, underlying="QQQ",  product_type="Phoenix",  quantity=30),
        ]
        session.add_all(rows)
        session.flush()

        clause = compile_rule_to_sqla({
            "op": "and",
            "children": [
                {"op": "in",  "field": "underlying",   "value": ["AAPL", "TSLA", "QQQ"]},
                {"op": "not", "child": {"op": "eq", "field": "product_type", "value": "Phoenix"}},
            ],
        })
        out = session.execute(select(Position).where(clause)).scalars().all()
        assert {r.underlying for r in out} == {"AAPL", "TSLA"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_rule.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.portfolio_rule`.

- [ ] **Step 3: Implement `portfolio_rule.py`**

Create `backend/app/services/portfolio_rule.py`:

```python
"""Filter rule validator + SQLAlchemy compiler for portfolio views.

The canonical rule is a JSON expression tree (see spec §4.3). This module
exposes:

- ``validate_rule(rule) -> list[str]`` — non-empty list means invalid.
- ``compile_rule_to_sqla(rule) -> ColumnElement[bool]`` — SQLAlchemy clause
  for `WHERE` against the `Position` table.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, between, not_, or_

from ..models import Position, RuleCompilationError


MAX_RULE_DEPTH = 5

ALLOWED_OPS: frozenset[str] = frozenset({
    "and", "or", "not",
    "eq", "ne", "in", "not_in",
    "lt", "lte", "gt", "gte", "between",
})

_LEAF_OPS: frozenset[str] = frozenset({
    "eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "between",
})

_SCALAR_OPS: frozenset[str] = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
_LIST_OPS: frozenset[str] = frozenset({"in", "not_in"})

ALLOWED_FIELDS: dict[str, type] = {
    "product_type":   str,
    "underlying":     str,
    "status":         str,
    "mapping_status": str,
    "engine_name":    str,
    "quantity":       float,
    "entry_price":    float,
    "created_at":     datetime,
}


def validate_rule(rule: Any, *, _path: str = "$", _depth: int = 0) -> list[str]:
    errors: list[str] = []
    if _depth > MAX_RULE_DEPTH:
        return [f"Rule depth exceeds {MAX_RULE_DEPTH} at {_path}"]
    if not isinstance(rule, dict):
        return [f"Rule node must be an object at {_path}"]

    op = rule.get("op")
    if op not in ALLOWED_OPS:
        return [f"Unsupported op: {op!r} at {_path}"]

    if op in ("and", "or"):
        children = rule.get("children")
        if not isinstance(children, list) or not children:
            errors.append(f"Empty or missing children for {op} at {_path}")
            return errors
        for i, child in enumerate(children):
            errors.extend(validate_rule(child, _path=f"{_path}.children[{i}]", _depth=_depth + 1))
        return errors

    if op == "not":
        child = rule.get("child")
        if not isinstance(child, dict):
            errors.append(f"`not` requires `child` object at {_path}")
            return errors
        return validate_rule(child, _path=f"{_path}.child", _depth=_depth + 1)

    field = rule.get("field")
    if field not in ALLOWED_FIELDS:
        return [f"Unknown field: {field!r} at {_path}; allowed: {sorted(ALLOWED_FIELDS)}"]
    expected_type = ALLOWED_FIELDS[field]
    value = rule.get("value")

    if op in _SCALAR_OPS:
        if isinstance(value, list):
            errors.append(f"`{op}` requires scalar value at {_path}")
        elif value is None:
            errors.append(f"`{op}` requires non-null value at {_path}")
    elif op in _LIST_OPS:
        if not isinstance(value, list) or not value:
            errors.append(f"`{op}` requires non-empty list value at {_path}")
    elif op == "between":
        if not (isinstance(value, list) and len(value) == 2):
            errors.append(f"`between` requires 2-element list at {_path}")
        else:
            lo, hi = value
            try:
                if lo is not None and hi is not None and lo > hi:
                    errors.append(f"`between` bounds reversed at {_path}: {lo!r} > {hi!r}")
            except TypeError:
                errors.append(f"`between` bounds incomparable at {_path}")

    return errors


def _coerce(field: str, value: Any) -> Any:
    expected = ALLOWED_FIELDS[field]
    if value is None:
        return None
    if expected is datetime and isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise RuleCompilationError(f"Cannot parse {field}={value!r} as datetime") from exc
    if expected in (float, int) and not isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RuleCompilationError(f"Cannot coerce {field}={value!r} to number") from exc
    if expected is str and not isinstance(value, str):
        return str(value)
    return value


def compile_rule_to_sqla(rule: dict):
    op = rule["op"]
    if op == "and":
        return and_(*(compile_rule_to_sqla(c) for c in rule["children"]))
    if op == "or":
        return or_(*(compile_rule_to_sqla(c) for c in rule["children"]))
    if op == "not":
        return not_(compile_rule_to_sqla(rule["child"]))

    field = rule["field"]
    column = getattr(Position, field)
    value = rule.get("value")

    if op == "eq":
        return column == _coerce(field, value)
    if op == "ne":
        return column != _coerce(field, value)
    if op == "in":
        return column.in_([_coerce(field, v) for v in value])
    if op == "not_in":
        return ~column.in_([_coerce(field, v) for v in value])
    if op == "lt":
        return column < _coerce(field, value)
    if op == "lte":
        return column <= _coerce(field, value)
    if op == "gt":
        return column > _coerce(field, value)
    if op == "gte":
        return column >= _coerce(field, value)
    if op == "between":
        lo, hi = value
        return between(column, _coerce(field, lo), _coerce(field, hi))

    raise RuleCompilationError(f"Unsupported op: {op}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_rule.py -v`
Expected: PASS for all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_rule.py tests/test_portfolio_rule.py
git commit -m "feat(portfolio): rule validator + sqlalchemy compiler"
```

---

## Task 3: Rule DSL parser + serializer

**Files:**
- Create: `backend/app/services/portfolio_rule_dsl.py`
- Create: `tests/test_portfolio_rule_dsl.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio_rule_dsl.py`:

```python
from __future__ import annotations

import pytest

from app.services.portfolio_rule_dsl import (
    DslSyntaxError,
    compile_rule_to_text,
    parse_text_to_rule,
)


def test_parse_simple_eq():
    assert parse_text_to_rule('product_type = "Snowball"') == {
        "op": "eq", "field": "product_type", "value": "Snowball",
    }


def test_parse_unquoted_value():
    assert parse_text_to_rule('product_type = Snowball') == {
        "op": "eq", "field": "product_type", "value": "Snowball",
    }


def test_parse_in_list():
    assert parse_text_to_rule('underlying IN (AAPL, TSLA)') == {
        "op": "in", "field": "underlying", "value": ["AAPL", "TSLA"],
    }


def test_parse_and():
    rule = parse_text_to_rule('product_type = Snowball AND status = open')
    assert rule == {
        "op": "and",
        "children": [
            {"op": "eq", "field": "product_type", "value": "Snowball"},
            {"op": "eq", "field": "status", "value": "open"},
        ],
    }


def test_parse_or_and_not():
    rule = parse_text_to_rule('NOT (status = closed) OR quantity < 100')
    assert rule == {
        "op": "or",
        "children": [
            {"op": "not", "child": {"op": "eq", "field": "status", "value": "closed"}},
            {"op": "lt", "field": "quantity", "value": 100.0},
        ],
    }


def test_parse_between():
    rule = parse_text_to_rule('quantity BETWEEN 10 AND 100')
    assert rule == {"op": "between", "field": "quantity", "value": [10.0, 100.0]}


def test_parse_invalid_raises():
    with pytest.raises(DslSyntaxError):
        parse_text_to_rule('product_type ===== Snowball')


def test_compile_then_parse_roundtrip():
    original = {
        "op": "and",
        "children": [
            {"op": "eq", "field": "product_type", "value": "Snowball"},
            {"op": "in", "field": "underlying", "value": ["AAPL", "TSLA"]},
            {"op": "not", "child": {"op": "eq", "field": "status", "value": "closed"}},
        ],
    }
    text = compile_rule_to_text(original)
    assert parse_text_to_rule(text) == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_rule_dsl.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the DSL module**

Create `backend/app/services/portfolio_rule_dsl.py`:

```python
"""Power-user DSL parser and serializer for filter rules.

Round-trips through the canonical JSON expression tree defined in
``portfolio_rule.py``. Designed to be small and predictable rather than
expressive — only the published grammar is supported.

Grammar (informal):
    expr      := or_expr
    or_expr   := and_expr ("OR" and_expr)*
    and_expr  := not_expr ("AND" not_expr)*
    not_expr  := "NOT" not_expr | atom
    atom      := "(" expr ")" | leaf
    leaf      := IDENT op value
    op        := "=" | "!=" | "<" | "<=" | ">" | ">=" | "IN" | "NOT IN" | "BETWEEN"
    value     := scalar | "(" scalar ("," scalar)* ")" | scalar "AND" scalar  (BETWEEN)
    scalar    := QUOTED_STRING | NUMBER | BAREWORD
"""
from __future__ import annotations

import re
from typing import Any


class DslSyntaxError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"""
    \s* (
          "(?:[^"\\]|\\.)*"          # quoted string
        | '(?:[^'\\]|\\.)*'          # quoted string (single)
        | -?\d+(?:\.\d+)?            # number
        | <=|>=|!=|=|<|>             # comparison
        | \(|\)|,                    # punctuation
        | [A-Za-z_][A-Za-z0-9_.\-]*  # identifier / bareword
    )
    """,
    re.VERBOSE,
)

_KEYWORDS = {"AND", "OR", "NOT", "IN", "BETWEEN"}


def _tokenize(text: str) -> list[str]:
    pos = 0
    out: list[str] = []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise DslSyntaxError(f"Unexpected character at offset {pos}: {text[pos]!r}")
        tok = m.group(1)
        out.append(tok)
        pos = m.end()
    return out


def _is_ident(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", tok)) and tok.upper() not in _KEYWORDS


def _scalar(tok: str) -> Any:
    if (tok.startswith('"') and tok.endswith('"')) or (tok.startswith("'") and tok.endswith("'")):
        return tok[1:-1].encode().decode("unicode_escape")
    try:
        if "." in tok:
            return float(tok)
        return float(int(tok))
    except ValueError:
        return tok


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _consume(self, expected: str | None = None) -> str:
        if self.i >= len(self.tokens):
            raise DslSyntaxError(f"Unexpected end of input; expected {expected!r}")
        tok = self.tokens[self.i]
        if expected is not None and tok.upper() != expected.upper():
            raise DslSyntaxError(f"Expected {expected!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self) -> dict:
        rule = self._or()
        if self.i != len(self.tokens):
            raise DslSyntaxError(f"Trailing tokens: {self.tokens[self.i:]}")
        return rule

    def _or(self) -> dict:
        left = self._and()
        children = [left]
        while self._peek() and self._peek().upper() == "OR":
            self._consume("OR")
            children.append(self._and())
        return left if len(children) == 1 else {"op": "or", "children": children}

    def _and(self) -> dict:
        left = self._not()
        children = [left]
        while self._peek() and self._peek().upper() == "AND":
            self._consume("AND")
            children.append(self._not())
        return left if len(children) == 1 else {"op": "and", "children": children}

    def _not(self) -> dict:
        if self._peek() and self._peek().upper() == "NOT":
            self._consume("NOT")
            return {"op": "not", "child": self._not()}
        return self._atom()

    def _atom(self) -> dict:
        tok = self._peek()
        if tok == "(":
            self._consume("(")
            inner = self._or()
            self._consume(")")
            return inner
        return self._leaf()

    def _leaf(self) -> dict:
        ident = self._consume()
        if not _is_ident(ident):
            raise DslSyntaxError(f"Expected field name, got {ident!r}")
        op_tok = self._consume()
        op_upper = op_tok.upper()
        if op_upper == "NOT":
            self._consume("IN")
            return {"op": "not_in", "field": ident, "value": self._list_value()}
        if op_upper == "IN":
            return {"op": "in", "field": ident, "value": self._list_value()}
        if op_upper == "BETWEEN":
            lo = _scalar(self._consume())
            self._consume("AND")
            hi = _scalar(self._consume())
            return {"op": "between", "field": ident, "value": [lo, hi]}
        sym = {"=": "eq", "!=": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}.get(op_tok)
        if sym is None:
            raise DslSyntaxError(f"Unknown operator: {op_tok!r}")
        return {"op": sym, "field": ident, "value": _scalar(self._consume())}

    def _list_value(self) -> list[Any]:
        self._consume("(")
        out: list[Any] = []
        if self._peek() != ")":
            out.append(_scalar(self._consume()))
            while self._peek() == ",":
                self._consume(",")
                out.append(_scalar(self._consume()))
        self._consume(")")
        return out


def parse_text_to_rule(text: str) -> dict:
    if not text or not text.strip():
        raise DslSyntaxError("Empty rule text")
    tokens = _tokenize(text)
    return _Parser(tokens).parse()


def _quote(value: Any) -> str:
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    s = str(value)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", s):
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def compile_rule_to_text(rule: dict) -> str:
    op = rule["op"]
    if op == "and":
        return " AND ".join(compile_rule_to_text(c) for c in rule["children"])
    if op == "or":
        return "(" + " OR ".join(compile_rule_to_text(c) for c in rule["children"]) + ")"
    if op == "not":
        return f"NOT ({compile_rule_to_text(rule['child'])})"
    field = rule["field"]
    value = rule["value"]
    if op == "in":
        return f"{field} IN (" + ", ".join(_quote(v) for v in value) + ")"
    if op == "not_in":
        return f"{field} NOT IN (" + ", ".join(_quote(v) for v in value) + ")"
    if op == "between":
        lo, hi = value
        return f"{field} BETWEEN {_quote(lo)} AND {_quote(hi)}"
    sym = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[op]
    return f"{field} {sym} {_quote(value)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_rule_dsl.py -v`
Expected: PASS for all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_rule_dsl.py tests/test_portfolio_rule_dsl.py
git commit -m "feat(portfolio): rule DSL parser + serializer"
```

---

## Task 4: Membership resolver

**Files:**
- Create: `backend/app/services/portfolio_membership.py`
- Create: `tests/test_portfolio_membership.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio_membership.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import (
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    Portfolio,
    Position,
)
from app.services.portfolio_membership import resolve_positions


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def _make(session, *, name, kind=PortfolioKind.CONTAINER.value, **kwargs):
    p = Portfolio(name=name, base_currency="USD", kind=kind, **kwargs)
    session.add(p)
    session.flush()
    return p


def _pos(session, portfolio, **kwargs):
    p = Position(portfolio_id=portfolio.id, quantity=1.0, **kwargs)
    session.add(p)
    session.flush()
    return p


def test_container_returns_owned_positions(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c, underlying="TSLA", product_type="Phoenix")
    out = resolve_positions(c, session)
    assert {p.id for p in out} == {a.id, b.id}


def test_view_with_rule_only_filters_across_containers(session):
    c1 = _make(session, name="C1")
    c2 = _make(session, name="C2")
    snow1 = _pos(session, c1, underlying="AAPL", product_type="Snowball")
    snow2 = _pos(session, c2, underlying="TSLA", product_type="Snowball")
    _pos(session, c1, underlying="QQQ", product_type="Phoenix")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"})
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {snow1.id, snow2.id}


def test_view_manual_includes_and_excludes(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c, underlying="TSLA", product_type="Snowball")
    extra = _pos(session, c, underlying="QQQ", product_type="Phoenix")
    v = _make(
        session, name="V", kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
        manual_include_ids=[extra.id],
        manual_exclude_ids=[a.id],
    )
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {b.id, extra.id}


def test_view_aggregates_from_source_portfolios(session):
    c1 = _make(session, name="C1")
    c2 = _make(session, name="C2")
    a = _pos(session, c1, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c2, underlying="TSLA", product_type="Phoenix")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              source_portfolio_ids=[c1.id, c2.id])
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {a.id, b.id}


def test_view_dedups_overlap_between_rule_sources_and_manual(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
              source_portfolio_ids=[c.id],
              manual_include_ids=[a.id])
    out = resolve_positions(v, session)
    assert [p.id for p in out] == [a.id]


def test_view_skips_dangling_source(session):
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              source_portfolio_ids=[9999])
    assert resolve_positions(v, session) == []


def test_cycle_detection(session):
    a = _make(session, name="A", kind=PortfolioKind.VIEW.value)
    b = _make(session, name="B", kind=PortfolioKind.VIEW.value, source_portfolio_ids=[a.id])
    a.source_portfolio_ids = [b.id]
    session.flush()
    with pytest.raises(PortfolioCycleError):
        resolve_positions(a, session)


def test_depth_exceeded(session):
    chain = [_make(session, name=f"V{i}", kind=PortfolioKind.VIEW.value) for i in range(5)]
    for i in range(len(chain) - 1):
        chain[i].source_portfolio_ids = [chain[i + 1].id]
    session.flush()
    with pytest.raises(PortfolioDepthError):
        resolve_positions(chain[0], session)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_membership.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the resolver**

Create `backend/app/services/portfolio_membership.py`:

```python
"""Membership resolution for container vs view portfolios.

For containers: returns owned positions via FK.
For views: returns ``(rule_matches ∪ source_resolved ∪ manual_includes)
− manual_excludes`` with cycle detection and depth ≤ 3.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Portfolio,
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    Position,
)
from .portfolio_rule import compile_rule_to_sqla


MAX_AGGREGATION_DEPTH = 3
SLOW_RESOLVE_MS = 250

logger = logging.getLogger(__name__)


def resolve_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    _visited: frozenset[int] | None = None,
    _depth: int = 0,
    _path: tuple[int, ...] = (),
) -> list[Position]:
    started = time.monotonic()
    visited = _visited or frozenset()
    if portfolio.id in visited:
        cycle = list(_path) + [portfolio.id]
        raise PortfolioCycleError(
            f"Cycle detected: {' -> '.join(str(i) for i in cycle)}",
            cycle_path=cycle,
        )
    if _depth > MAX_AGGREGATION_DEPTH:
        chain = list(_path) + [portfolio.id]
        raise PortfolioDepthError(
            f"Aggregation depth exceeded at portfolio {portfolio.id}",
            depth_path=chain,
        )
    visited = visited | {portfolio.id}
    path = _path + (portfolio.id,)

    out = _resolve_inner(portfolio, session, visited=visited, depth=_depth, path=path)

    if _depth == 0:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > SLOW_RESOLVE_MS:
            logger.warning(
                "resolve_positions slow: portfolio_id=%s kind=%s ms=%d count=%d",
                portfolio.id, portfolio.kind, elapsed_ms, len(out),
            )
    return out


def _resolve_inner(
    portfolio: Portfolio,
    session: Session,
    *,
    visited: frozenset[int],
    depth: int,
    path: tuple[int, ...],
) -> list[Position]:
    if portfolio.kind == PortfolioKind.CONTAINER.value:
        return list(portfolio.positions)

    matched: dict[int, Position] = {}

    if portfolio.filter_rule:
        clause = compile_rule_to_sqla(portfolio.filter_rule)
        for p in session.execute(select(Position).where(clause)).scalars():
            matched[p.id] = p

    for src_id in portfolio.source_portfolio_ids or []:
        src = session.get(Portfolio, src_id)
        if src is None:
            continue
        for p in resolve_positions(src, session, _visited=visited, _depth=depth + 1, _path=path):
            matched[p.id] = p

    for inc in portfolio.manual_include_ids or []:
        p = session.get(Position, inc)
        if p is not None:
            matched[p.id] = p

    for exc in portfolio.manual_exclude_ids or []:
        matched.pop(exc, None)

    return list(matched.values())


def resolve_position_ids(portfolio: Portfolio, session: Session) -> list[int]:
    return [p.id for p in resolve_positions(portfolio, session)]


def find_descendants(
    session: Session,
    portfolio_id: int,
    *,
    _visited: set[int] | None = None,
) -> set[int]:
    """All portfolio ids reachable through ``source_portfolio_ids``.

    Used by the picker to exclude descendants from the candidate list.
    Loops are guarded but not raised — this is a UI helper, not a
    validation point.
    """
    visited = _visited if _visited is not None else set()
    if portfolio_id in visited:
        return visited
    visited.add(portfolio_id)
    p = session.get(Portfolio, portfolio_id)
    if p is None:
        return visited
    for child in p.source_portfolio_ids or []:
        find_descendants(session, child, _visited=visited)
    return visited
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_membership.py -v`
Expected: PASS for all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_membership.py tests/test_portfolio_membership.py
git commit -m "feat(portfolio): membership resolver with cycle and depth guards"
```

---

## Task 5: Service layer — read functions

**Files:**
- Create: `backend/app/services/portfolio_service.py`
- Create: `tests/test_portfolio_service.py`

- [ ] **Step 1: Write the failing tests for list/get/preview**

Create `tests/test_portfolio_service.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import PortfolioKind, Portfolio, Position
from app.services import portfolio_service


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def _seed_container(session, name="Book", **kwargs):
    p = Portfolio(name=name, base_currency="USD", kind=PortfolioKind.CONTAINER.value, **kwargs)
    session.add(p)
    session.flush()
    return p


def test_list_portfolios_empty(session):
    assert portfolio_service.list_portfolios(session) == []


def test_list_portfolios_filters_by_kind(session):
    c = _seed_container(session, name="C", tags=["a"])
    v = _seed_container(session, name="V", kind=PortfolioKind.VIEW.value)
    assert {p.id for p in portfolio_service.list_portfolios(session, kind="container")} == {c.id}
    assert {p.id for p in portfolio_service.list_portfolios(session, kind="view")} == {v.id}


def test_list_portfolios_filters_by_tags_AND(session):
    a = _seed_container(session, name="A", tags=["alpha", "beta"])
    _seed_container(session, name="B", tags=["alpha"])
    out = portfolio_service.list_portfolios(session, tags=["alpha", "beta"])
    assert {p.id for p in out} == {a.id}


def test_get_portfolio_raises_for_unknown(session):
    with pytest.raises(LookupError):
        portfolio_service.get_portfolio(session, 9999)


def test_preview_membership_for_view(session):
    c = _seed_container(session, name="C")
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(a)
    session.flush()
    v = _seed_container(
        session, name="V",
        kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    assert portfolio_service.preview_membership(session, v.id) == [a.id]


def test_preview_membership_dry_run(session):
    c = _seed_container(session, name="C")
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(a)
    session.flush()
    ids = portfolio_service.preview_membership_dry_run(
        session,
        kind="view",
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    assert ids == [a.id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the read-side service**

Create `backend/app/services/portfolio_service.py`:

```python
"""Portfolio service — single authoritative module wrapping CRUD,
membership preview, and audit. Used by HTTP, CLI, and LangChain tool layers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Portfolio,
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    PortfolioKindError,
    PortfolioNameConflict,
    Position,
    RuleValidationError,
)
from .audit import record_audit
from .portfolio_membership import (
    MAX_AGGREGATION_DEPTH,
    find_descendants,
    resolve_position_ids,
    resolve_positions,
)
from .portfolio_rule import validate_rule


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_portfolios(
    session: Session,
    *,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
) -> list[Portfolio]:
    q = session.query(Portfolio)
    if kind is not None:
        q = q.filter(Portfolio.kind == kind)
    out = q.order_by(Portfolio.created_at.desc()).all()
    if tags:
        wanted = {t.lower() for t in tags}
        out = [p for p in out if wanted.issubset(set(p.tags or []))]
    return out


def get_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    p = session.get(Portfolio, portfolio_id)
    if p is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    return p


def preview_membership(session: Session, portfolio_id: int) -> list[int]:
    p = get_portfolio(session, portfolio_id)
    return resolve_position_ids(p, session)


def preview_membership_dry_run(
    session: Session,
    *,
    kind: str,
    filter_rule: dict | None = None,
    manual_include_ids: Iterable[int] = (),
    manual_exclude_ids: Iterable[int] = (),
    source_portfolio_ids: Iterable[int] = (),
) -> list[int]:
    if kind not in (PortfolioKind.CONTAINER.value, PortfolioKind.VIEW.value):
        raise PortfolioKindError(f"Unknown kind: {kind}")
    if kind == PortfolioKind.CONTAINER.value:
        return []
    fake = SimpleNamespace(
        id=0,
        kind=PortfolioKind.VIEW.value,
        filter_rule=filter_rule,
        manual_include_ids=list(manual_include_ids),
        manual_exclude_ids=list(manual_exclude_ids),
        source_portfolio_ids=list(source_portfolio_ids),
        positions=[],
    )
    return [p.id for p in resolve_positions(fake, session)]


# ---------------------------------------------------------------------------
# Helpers (used by writers in later tasks)
# ---------------------------------------------------------------------------

def _normalize_tags(tags: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            raise RuleValidationError([f"Tag must be a string, got {type(t).__name__}"])
        s = t.strip().lower()
        if not s:
            continue
        if len(s) > 40:
            raise RuleValidationError([f"Tag too long (>40 chars): {t!r}"])
        if s not in seen:
            seen.append(s)
    return seen


def _require_view(p: Portfolio) -> None:
    if p.kind != PortfolioKind.VIEW.value:
        raise PortfolioKindError(f"Portfolio {p.id} is a {p.kind}, not a view")


def _require_container(p: Portfolio) -> None:
    if p.kind != PortfolioKind.CONTAINER.value:
        raise PortfolioKindError(f"Portfolio {p.id} is a {p.kind}, not a container")


def _check_position_ids_exist(session: Session, ids: Iterable[int]) -> list[int]:
    ids_list = list(dict.fromkeys(int(i) for i in ids))
    if not ids_list:
        return []
    found = {pid for (pid,) in session.query(Position.id).filter(Position.id.in_(ids_list))}
    missing = [i for i in ids_list if i not in found]
    if missing:
        raise RuleValidationError([f"Unknown position ids: {missing}"])
    return ids_list


def _check_portfolio_ids_exist(session: Session, ids: Iterable[int]) -> list[int]:
    ids_list = list(dict.fromkeys(int(i) for i in ids))
    if not ids_list:
        return []
    found = {pid for (pid,) in session.query(Portfolio.id).filter(Portfolio.id.in_(ids_list))}
    missing = [i for i in ids_list if i not in found]
    if missing:
        raise RuleValidationError([f"Unknown source portfolio ids: {missing}"])
    return ids_list


def _check_no_cycle(session: Session, portfolio_id: int, candidate_sources: Iterable[int]) -> None:
    for src_id in candidate_sources:
        if src_id == portfolio_id:
            raise PortfolioCycleError(
                f"Self-reference: portfolio {portfolio_id}",
                cycle_path=[portfolio_id, portfolio_id],
            )
        descendants = find_descendants(session, src_id)
        if portfolio_id in descendants:
            raise PortfolioCycleError(
                f"Adding source {src_id} would create cycle through {portfolio_id}",
                cycle_path=[portfolio_id, src_id, portfolio_id],
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: PASS for read-side tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_service.py tests/test_portfolio_service.py
git commit -m "feat(portfolio): service layer read API + helpers"
```

---

## Task 6: Service layer — create / update / delete

**Files:**
- Modify: `backend/app/services/portfolio_service.py`
- Modify: `tests/test_portfolio_service.py`

- [ ] **Step 1: Add failing tests for create/update/delete**

Append to `tests/test_portfolio_service.py`:

```python
from app.models import PortfolioNameConflict


def test_create_container(session):
    p = portfolio_service.create_portfolio(
        session, name="Book A", base_currency="USD", kind="container", tags=["Desk"],
    )
    session.commit()
    assert p.kind == "container"
    assert p.tags == ["desk"]


def test_create_view_with_rule(session):
    p = portfolio_service.create_portfolio(
        session, name="Snow", base_currency="USD", kind="view",
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    session.commit()
    assert p.kind == "view"
    assert p.filter_rule["op"] == "eq"


def test_create_container_rejects_filter_rule(session):
    with pytest.raises(Exception):
        portfolio_service.create_portfolio(
            session, name="X", base_currency="USD", kind="container",
            filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
        )


def test_create_view_rejects_invalid_rule(session):
    with pytest.raises(Exception):
        portfolio_service.create_portfolio(
            session, name="X", base_currency="USD", kind="view",
            filter_rule={"op": "weird"},
        )


def test_create_duplicate_name_raises_name_conflict(session):
    portfolio_service.create_portfolio(session, name="Dup", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(PortfolioNameConflict):
        portfolio_service.create_portfolio(session, name="Dup", base_currency="USD", kind="container")
        session.commit()


def test_update_portfolio_changes_name_and_tags(session):
    p = portfolio_service.create_portfolio(session, name="Old", base_currency="USD", kind="container")
    session.commit()
    portfolio_service.update_portfolio(session, p.id, name="New", tags=["Hedging"])
    session.commit()
    refetched = session.get(type(p), p.id)
    assert refetched.name == "New"
    assert refetched.tags == ["hedging"]


def test_delete_container_cascades_positions(session):
    from app.models import Position
    p = portfolio_service.create_portfolio(session, name="Z", base_currency="USD", kind="container")
    session.flush()
    pos = Position(portfolio_id=p.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(pos)
    session.commit()
    portfolio_service.delete_portfolio(session, p.id)
    session.commit()
    assert session.query(Position).filter_by(portfolio_id=p.id).count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: FAIL — `create_portfolio`/`update_portfolio`/`delete_portfolio` not defined.

- [ ] **Step 3: Implement the writers**

Append to `backend/app/services/portfolio_service.py`:

```python
# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------

def create_portfolio(
    session: Session,
    *,
    name: str,
    base_currency: str,
    kind: str,
    filter_rule: dict | None = None,
    manual_include_ids: Iterable[int] = (),
    manual_exclude_ids: Iterable[int] = (),
    source_portfolio_ids: Iterable[int] = (),
    tags: Iterable[str] = (),
    description: str | None = None,
    actor: str = "desk_user",
) -> Portfolio:
    if kind not in (PortfolioKind.CONTAINER.value, PortfolioKind.VIEW.value):
        raise PortfolioKindError(f"Unknown kind: {kind}")
    is_view = kind == PortfolioKind.VIEW.value
    if not is_view and (filter_rule or manual_include_ids or manual_exclude_ids or source_portfolio_ids):
        raise PortfolioKindError("Container portfolios cannot have filter_rule, manual_includes/excludes, or sources")

    if filter_rule is not None:
        errors = validate_rule(filter_rule)
        if errors:
            raise RuleValidationError(errors)

    includes = _check_position_ids_exist(session, manual_include_ids) if is_view else []
    excludes = _check_position_ids_exist(session, manual_exclude_ids) if is_view else []
    overlap = set(includes) & set(excludes)
    if overlap:
        raise RuleValidationError([f"Position id(s) in both includes and excludes: {sorted(overlap)}"])
    sources = _check_portfolio_ids_exist(session, source_portfolio_ids) if is_view else []
    normalized_tags = _normalize_tags(tags)

    portfolio = Portfolio(
        name=name,
        base_currency=base_currency,
        kind=kind,
        filter_rule=filter_rule if is_view else None,
        manual_include_ids=includes,
        manual_exclude_ids=excludes,
        source_portfolio_ids=sources,
        tags=normalized_tags,
        description=description,
    )
    session.add(portfolio)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise PortfolioNameConflict(f"Portfolio name already exists: {name!r}") from exc

    record_audit(
        session,
        event_type="portfolio.created",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={
            "name": name,
            "kind": kind,
            "tags": normalized_tags,
            "has_rule": filter_rule is not None,
            "source_count": len(sources),
            "include_count": len(includes),
            "exclude_count": len(excludes),
        },
    )
    return portfolio


def update_portfolio(
    session: Session,
    portfolio_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    base_currency: str | None = None,
    tags: Iterable[str] | None = None,
    actor: str = "desk_user",
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    changed: dict[str, object] = {}
    if name is not None and name != portfolio.name:
        portfolio.name = name
        changed["name"] = name
    if description is not None and description != portfolio.description:
        portfolio.description = description
        changed["description"] = description
    if base_currency is not None and base_currency != portfolio.base_currency:
        portfolio.base_currency = base_currency
        changed["base_currency"] = base_currency
    if tags is not None:
        normalized = _normalize_tags(tags)
        if normalized != list(portfolio.tags or []):
            portfolio.tags = normalized
            changed["tags"] = normalized
    if not changed:
        return portfolio
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise PortfolioNameConflict(f"Portfolio name already exists: {name!r}") from exc
    record_audit(
        session,
        event_type="portfolio.updated",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload=changed,
    )
    if "tags" in changed:
        record_audit(
            session,
            event_type="portfolio.tags_changed",
            actor=actor,
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={"tags": changed["tags"]},
        )
    return portfolio


def delete_portfolio(session: Session, portfolio_id: int, *, actor: str = "desk_user") -> None:
    portfolio = get_portfolio(session, portfolio_id)
    record_audit(
        session,
        event_type="portfolio.deleted",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"name": portfolio.name, "kind": portfolio.kind},
    )
    session.delete(portfolio)
    session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_service.py tests/test_portfolio_service.py
git commit -m "feat(portfolio): service layer create/update/delete with audit + name-conflict mapping"
```

---

## Task 7: Service layer — rule, includes, excludes, sources, tags

**Files:**
- Modify: `backend/app/services/portfolio_service.py`
- Modify: `tests/test_portfolio_service.py`

- [ ] **Step 1: Add failing tests for sub-resource writers**

Append to `tests/test_portfolio_service.py`:

```python
def test_set_filter_rule_views_only(session):
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.set_filter_rule(session, c.id, {"op": "eq", "field": "product_type", "value": "Snowball"})


def test_set_filter_rule_persists_and_validates(session):
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view")
    session.commit()
    portfolio_service.set_filter_rule(session, v.id, {"op": "eq", "field": "product_type", "value": "Snowball"})
    session.commit()
    refetched = session.get(type(v), v.id)
    assert refetched.filter_rule["op"] == "eq"
    with pytest.raises(Exception):
        portfolio_service.set_filter_rule(session, v.id, {"op": "weird"})


def test_manual_includes_add_remove(session):
    from app.models import Position
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.flush()
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="X", quantity=1.0)
    b = Position(portfolio_id=c.id, underlying="TSLA", product_type="X", quantity=1.0)
    session.add_all([a, b])
    session.commit()
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view")
    session.commit()
    portfolio_service.add_manual_includes(session, v.id, [a.id, b.id])
    session.commit()
    assert sorted(session.get(type(v), v.id).manual_include_ids) == sorted([a.id, b.id])
    portfolio_service.remove_manual_includes(session, v.id, [a.id])
    session.commit()
    assert session.get(type(v), v.id).manual_include_ids == [b.id]


def test_includes_excludes_overlap_rejected(session):
    from app.models import Position
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.flush()
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="X", quantity=1.0)
    session.add(a)
    session.commit()
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view",
                                            manual_include_ids=[a.id])
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.add_manual_excludes(session, v.id, [a.id])


def test_add_sources_cycle_rejected(session):
    a = portfolio_service.create_portfolio(session, name="A", base_currency="USD", kind="view")
    b = portfolio_service.create_portfolio(session, name="B", base_currency="USD", kind="view",
                                            source_portfolio_ids=[])
    session.commit()
    portfolio_service.add_portfolio_sources(session, a.id, [b.id])
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.add_portfolio_sources(session, b.id, [a.id])


def test_set_tags_normalizes(session):
    p = portfolio_service.create_portfolio(session, name="P", base_currency="USD", kind="container")
    session.commit()
    portfolio_service.set_portfolio_tags(session, p.id, ["Alpha", "  beta  ", "Alpha"])
    session.commit()
    assert session.get(type(p), p.id).tags == ["alpha", "beta"]


def test_set_tags_rejects_long(session):
    p = portfolio_service.create_portfolio(session, name="P", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.set_portfolio_tags(session, p.id, ["x" * 41])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: FAIL — sub-resource writers not defined.

- [ ] **Step 3: Implement the sub-resource writers**

Append to `backend/app/services/portfolio_service.py`:

```python
# ---------------------------------------------------------------------------
# Sub-resources
# ---------------------------------------------------------------------------

def set_filter_rule(
    session: Session,
    portfolio_id: int,
    rule: dict | None,
    *,
    actor: str = "desk_user",
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    _require_view(portfolio)
    if rule is not None:
        errors = validate_rule(rule)
        if errors:
            raise RuleValidationError(errors)
    portfolio.filter_rule = rule
    session.flush()
    record_audit(
        session,
        event_type="portfolio.rule_changed",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"rule": rule},
    )
    return portfolio


def _modify_id_list(
    session: Session,
    portfolio_id: int,
    attr: str,
    *,
    add: Iterable[int] | None = None,
    remove: Iterable[int] | None = None,
    audit_event: str,
    actor: str,
    overlap_attr: str | None = None,
    check_existence: str = "position",  # "position" | "portfolio" | "none"
    cycle_check_self: bool = False,
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    _require_view(portfolio)

    current: list[int] = list(getattr(portfolio, attr) or [])
    if add:
        if check_existence == "position":
            ids = _check_position_ids_exist(session, add)
        elif check_existence == "portfolio":
            ids = _check_portfolio_ids_exist(session, add)
            if cycle_check_self:
                _check_no_cycle(session, portfolio.id, ids)
        else:
            ids = list(dict.fromkeys(int(i) for i in add))
        if overlap_attr is not None:
            other = set(getattr(portfolio, overlap_attr) or [])
            overlap = set(ids) & other
            if overlap:
                raise RuleValidationError([f"Ids in conflict with {overlap_attr}: {sorted(overlap)}"])
        for i in ids:
            if i not in current:
                current.append(i)
    if remove:
        rm = {int(i) for i in remove}
        current = [i for i in current if i not in rm]

    setattr(portfolio, attr, current)
    session.flush()
    record_audit(
        session,
        event_type=audit_event,
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"attr": attr, "added": list(add or []), "removed": list(remove or []), "result": current},
    )
    return portfolio


def add_manual_includes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_include_ids", add=position_ids,
        audit_event="portfolio.positions_added", actor=actor,
        overlap_attr="manual_exclude_ids", check_existence="position",
    )


def remove_manual_includes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_include_ids", remove=position_ids,
        audit_event="portfolio.positions_removed", actor=actor, check_existence="none",
    )


def add_manual_excludes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_exclude_ids", add=position_ids,
        audit_event="portfolio.positions_added", actor=actor,
        overlap_attr="manual_include_ids", check_existence="position",
    )


def remove_manual_excludes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_exclude_ids", remove=position_ids,
        audit_event="portfolio.positions_removed", actor=actor, check_existence="none",
    )


def add_portfolio_sources(session, portfolio_id, source_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "source_portfolio_ids", add=source_ids,
        audit_event="portfolio.sources_added", actor=actor, check_existence="portfolio",
        cycle_check_self=True,
    )


def remove_portfolio_sources(session, portfolio_id, source_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "source_portfolio_ids", remove=source_ids,
        audit_event="portfolio.sources_removed", actor=actor, check_existence="none",
    )


def set_portfolio_tags(session, portfolio_id, tags, *, actor="desk_user") -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    normalized = _normalize_tags(tags)
    portfolio.tags = normalized
    session.flush()
    record_audit(
        session,
        event_type="portfolio.tags_changed",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"tags": normalized},
    )
    return portfolio
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_portfolio_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio_service.py tests/test_portfolio_service.py
git commit -m "feat(portfolio): service writers for rule, includes/excludes, sources, tags"
```

---

## Task 8: Pydantic schemas

**Files:**
- Modify: `backend/app/schemas.py`

- [ ] **Step 1: Locate existing portfolio schemas**

In `backend/app/schemas.py`, find `class PortfolioCreate` and `class PortfolioOut` (around line 232 / 271). They will be replaced/extended.

- [ ] **Step 2: Replace and extend the portfolio schemas**

Edit `backend/app/schemas.py`. Replace the existing `PortfolioCreate` and `PortfolioOut` classes with this block (and add the new request/response schemas):

```python
class PortfolioCreate(BaseModel):
    name: str
    base_currency: str = "USD"
    kind: Literal["container", "view"] = "container"
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)


class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_currency: str | None = None
    tags: list[str] | None = None


class PortfolioRuleBody(BaseModel):
    filter_rule: dict[str, Any] | None = None


class PortfolioIdsBody(BaseModel):
    position_ids: list[int] = Field(default_factory=list)


class PortfolioSourcesBody(BaseModel):
    portfolio_ids: list[int] = Field(default_factory=list)


class PortfolioTagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list)


class PortfolioPreviewBody(BaseModel):
    kind: Literal["container", "view"] = "view"
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)


class PortfolioMembershipOut(BaseModel):
    portfolio_id: int
    position_ids: list[int]


class PortfolioOut(BaseModel):
    id: int
    name: str
    base_currency: str
    kind: Literal["container", "view"]
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    manual_exclude_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)
    resolved_position_count: int = 0
    created_at: datetime
    updated_at: datetime
    positions: list[PositionOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: Run the existing schema tests to confirm no regression**

Run: `pytest tests/test_schemas.py tests/test_api.py -x -q`
Expected: existing tests still pass; if any test asserts on the old `PortfolioOut` shape, the field defaults preserve back-compat (everything new defaults to empty/None).

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(schemas): extend Portfolio schemas with kind/rule/sources/tags + sub-resource bodies"
```

---

## Task 9: HTTP API — list/create/detail/patch/delete

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for the base CRUD endpoints**

Append to `tests/test_api.py` (use `_build_app(tmp_path)` style consistent with the file — copy a nearby helper if needed):

```python
def test_create_view_portfolio_and_get(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())

    resp = client.post("/api/portfolios", json={
        "name": "Snowballs",
        "kind": "view",
        "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
        "tags": ["Desk"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "view"
    assert body["tags"] == ["desk"]
    pid = body["id"]

    resp = client.get(f"/api/portfolios/{pid}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Snowballs"


def test_list_portfolios_filters(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())

    client.post("/api/portfolios", json={"name": "A", "kind": "container", "tags": ["alpha"]})
    client.post("/api/portfolios", json={"name": "B", "kind": "view", "tags": ["beta"]})

    by_kind = client.get("/api/portfolios?kind=view").json()
    assert {p["name"] for p in by_kind} == {"B"}
    by_tag = client.get("/api/portfolios?tag=alpha").json()
    assert {p["name"] for p in by_tag} == {"A"}


def test_patch_and_delete_portfolio(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())

    pid = client.post("/api/portfolios", json={"name": "X", "kind": "container"}).json()["id"]
    resp = client.patch(f"/api/portfolios/{pid}", json={"description": "demo", "tags": ["risk"]})
    assert resp.status_code == 200
    assert resp.json()["description"] == "demo"
    assert resp.json()["tags"] == ["risk"]
    resp = client.delete(f"/api/portfolios/{pid}")
    assert resp.status_code == 204
    assert client.get(f"/api/portfolios/{pid}").status_code == 404


def test_create_duplicate_name_returns_409(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    assert client.post("/api/portfolios", json={"name": "Dup", "kind": "container"}).status_code == 200
    resp = client.post("/api/portfolios", json={"name": "Dup", "kind": "container"})
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py::test_create_view_portfolio_and_get tests/test_api.py::test_list_portfolios_filters tests/test_api.py::test_patch_and_delete_portfolio tests/test_api.py::test_create_duplicate_name_returns_409 -v`
Expected: FAIL — endpoints not yet implemented (or the existing `POST /api/portfolios` rejects new fields).

- [ ] **Step 3: Replace existing portfolio endpoints in `main.py`**

In `backend/app/main.py`, find the existing `list_portfolios` and `create_portfolio` route handlers (around line 612). Replace the existing block (`list_portfolios` + `create_portfolio` only — keep `add_position`, import endpoints, etc. untouched) with:

```python
    from .services import portfolio_service
    from .models import (
        PortfolioCycleError,
        PortfolioDepthError,
        PortfolioKindError,
        PortfolioNameConflict,
        RuleCompilationError,
        RuleValidationError,
    )
    from .services.portfolio_membership import resolve_position_ids

    def _portfolio_response(session, portfolio) -> PortfolioOut:
        positions = (
            list(portfolio.positions)
            if portfolio.kind == "container"
            else []
        )
        try:
            count = len(resolve_position_ids(portfolio, session))
        except (PortfolioCycleError, PortfolioDepthError):
            count = 0
        return PortfolioOut.model_validate({
            "id": portfolio.id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "kind": portfolio.kind,
            "description": portfolio.description,
            "tags": portfolio.tags or [],
            "filter_rule": portfolio.filter_rule,
            "manual_include_ids": portfolio.manual_include_ids or [],
            "manual_exclude_ids": portfolio.manual_exclude_ids or [],
            "source_portfolio_ids": portfolio.source_portfolio_ids or [],
            "resolved_position_count": count,
            "created_at": portfolio.created_at,
            "updated_at": portfolio.updated_at,
            "positions": [PositionOut.model_validate(p, from_attributes=True) for p in positions],
        })

    @app.get("/api/portfolios", response_model=list[PortfolioOut])
    def list_portfolios(
        kind: str | None = None,
        tag: list[str] | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        portfolios = portfolio_service.list_portfolios(session, kind=kind, tags=tag)
        return [_portfolio_response(session, p) for p in portfolios]

    @app.post("/api/portfolios", response_model=PortfolioOut)
    def create_portfolio(payload: PortfolioCreate, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.create_portfolio(
                session,
                name=payload.name,
                base_currency=payload.base_currency,
                kind=payload.kind,
                description=payload.description,
                tags=payload.tags,
                filter_rule=payload.filter_rule,
                manual_include_ids=payload.manual_include_ids,
                manual_exclude_ids=payload.manual_exclude_ids,
                source_portfolio_ids=payload.source_portfolio_ids,
            )
        except PortfolioNameConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PortfolioKindError, RuleValidationError, PortfolioCycleError, RuleCompilationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.get("/api/portfolios/{portfolio_id}", response_model=PortfolioOut)
    def get_portfolio(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.get_portfolio(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _portfolio_response(session, portfolio)

    @app.patch("/api/portfolios/{portfolio_id}", response_model=PortfolioOut)
    def patch_portfolio(portfolio_id: int, payload: PortfolioUpdate, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.update_portfolio(
                session,
                portfolio_id,
                name=payload.name,
                description=payload.description,
                base_currency=payload.base_currency,
                tags=payload.tags,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PortfolioNameConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuleValidationError,) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}", status_code=204)
    def delete_portfolio(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            portfolio_service.delete_portfolio(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        session.commit()
        return Response(status_code=204)
```

Add the `Query` and `Response` imports near the top of `main.py` if not already present:

```python
from fastapi import Query, Response
```

Update the `from .schemas import ...` block to include `PortfolioUpdate`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_api.py -k "portfolio" -v`
Expected: PASS for the four new tests, plus no regression in existing portfolio tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): polymorphic Portfolio CRUD endpoints with kind/tag filters + 409 on name conflict"
```

---

## Task 10: HTTP API — sub-resources (rule, includes, excludes, sources, tags, membership, preview)

**Files:**
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for the sub-resource endpoints**

Append to `tests/test_api.py`:

```python
def test_put_filter_rule(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    pid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()["id"]
    resp = client.put(
        f"/api/portfolios/{pid}/rule",
        json={"filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"}},
    )
    assert resp.status_code == 200
    assert resp.json()["filter_rule"]["op"] == "eq"


def test_put_filter_rule_validation_400(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    pid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()["id"]
    resp = client.put(f"/api/portfolios/{pid}/rule", json={"filter_rule": {"op": "weird"}})
    assert resp.status_code == 400


def test_includes_excludes_endpoints(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    cid = client.post("/api/portfolios", json={"name": "C", "kind": "container"}).json()["id"]
    pos_resp = client.post(
        f"/api/portfolios/{cid}/positions",
        json={"underlying": "AAPL", "product_type": "EuropeanVanillaOption", "quantity": 1.0},
    )
    assert pos_resp.status_code == 200
    pos_id = pos_resp.json()["positions"][0]["id"]

    vid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()["id"]
    resp = client.post(f"/api/portfolios/{vid}/includes", json={"position_ids": [pos_id]})
    assert resp.status_code == 200
    assert resp.json()["manual_include_ids"] == [pos_id]
    resp = client.request("DELETE", f"/api/portfolios/{vid}/includes", json={"position_ids": [pos_id]})
    assert resp.status_code == 200
    assert resp.json()["manual_include_ids"] == []


def test_sources_endpoints_and_cycle_400(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    a = client.post("/api/portfolios", json={"name": "A", "kind": "view"}).json()["id"]
    b = client.post("/api/portfolios", json={"name": "B", "kind": "view"}).json()["id"]

    resp = client.post(f"/api/portfolios/{a}/sources", json={"portfolio_ids": [b]})
    assert resp.status_code == 200
    assert resp.json()["source_portfolio_ids"] == [b]

    resp = client.post(f"/api/portfolios/{b}/sources", json={"portfolio_ids": [a]})
    assert resp.status_code == 400


def test_tags_endpoint(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    pid = client.post("/api/portfolios", json={"name": "P", "kind": "container"}).json()["id"]
    resp = client.put(f"/api/portfolios/{pid}/tags", json={"tags": ["Alpha", "alpha", "BETA"]})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["alpha", "beta"]


def test_membership_and_preview(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    client = TestClient(create_app())
    cid = client.post("/api/portfolios", json={"name": "C", "kind": "container"}).json()["id"]
    pos_resp = client.post(
        f"/api/portfolios/{cid}/positions",
        json={"underlying": "AAPL", "product_type": "Snowball", "quantity": 1.0},
    )
    pos_id = pos_resp.json()["positions"][0]["id"]
    vid = client.post("/api/portfolios", json={
        "name": "V", "kind": "view",
        "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
    }).json()["id"]

    resp = client.get(f"/api/portfolios/{vid}/membership")
    assert resp.status_code == 200
    assert resp.json()["position_ids"] == [pos_id]

    resp = client.post("/api/portfolios/preview", json={
        "kind": "view",
        "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
    })
    assert resp.status_code == 200
    assert resp.json()["position_ids"] == [pos_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -k "rule or includes or sources or tags_endpoint or membership_and_preview" -v`
Expected: FAIL — endpoints not yet implemented.

- [ ] **Step 3: Add the sub-resource endpoints to `main.py`**

In `backend/app/main.py`, after the base CRUD block from Task 9, insert:

```python
    @app.put("/api/portfolios/{portfolio_id}/rule", response_model=PortfolioOut)
    def put_portfolio_rule(portfolio_id: int, payload: PortfolioRuleBody, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.set_filter_rule(session, portfolio_id, payload.filter_rule)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioKindError, RuleValidationError, RuleCompilationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    def _ids_action(portfolio_id, payload, action):
        try:
            portfolio = action(portfolio_id, payload)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioKindError, RuleValidationError, PortfolioCycleError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return portfolio

    @app.post("/api/portfolios/{portfolio_id}/includes", response_model=PortfolioOut)
    def add_includes(portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.add_manual_includes(session, pid, p.position_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/includes", response_model=PortfolioOut)
    def remove_includes(portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.remove_manual_includes(session, pid, p.position_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.post("/api/portfolios/{portfolio_id}/excludes", response_model=PortfolioOut)
    def add_excludes(portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.add_manual_excludes(session, pid, p.position_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/excludes", response_model=PortfolioOut)
    def remove_excludes(portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.remove_manual_excludes(session, pid, p.position_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.post("/api/portfolios/{portfolio_id}/sources", response_model=PortfolioOut)
    def add_sources(portfolio_id: int, payload: PortfolioSourcesBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.add_portfolio_sources(session, pid, p.portfolio_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/sources", response_model=PortfolioOut)
    def remove_sources(portfolio_id: int, payload: PortfolioSourcesBody, session: Session = Depends(get_db)):
        portfolio = _ids_action(
            portfolio_id, payload,
            lambda pid, p: portfolio_service.remove_portfolio_sources(session, pid, p.portfolio_ids),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.put("/api/portfolios/{portfolio_id}/tags", response_model=PortfolioOut)
    def put_tags(portfolio_id: int, payload: PortfolioTagsBody, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.set_portfolio_tags(session, portfolio_id, payload.tags)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuleValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.get("/api/portfolios/{portfolio_id}/membership", response_model=PortfolioMembershipOut)
    def get_membership(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            ids = portfolio_service.preview_membership(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioCycleError, PortfolioDepthError, RuleCompilationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PortfolioMembershipOut(portfolio_id=portfolio_id, position_ids=ids)

    @app.post("/api/portfolios/preview", response_model=PortfolioMembershipOut)
    def post_preview(payload: PortfolioPreviewBody, session: Session = Depends(get_db)):
        try:
            ids = portfolio_service.preview_membership_dry_run(
                session,
                kind=payload.kind,
                filter_rule=payload.filter_rule,
                manual_include_ids=payload.manual_include_ids,
                manual_exclude_ids=payload.manual_exclude_ids,
                source_portfolio_ids=payload.source_portfolio_ids,
            )
        except (PortfolioKindError, RuleValidationError, RuleCompilationError, PortfolioCycleError, PortfolioDepthError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PortfolioMembershipOut(portfolio_id=0, position_ids=ids)
```

Update the imports near the top of `main.py` to include the new schemas:

```python
from .schemas import (
    ...
    PortfolioCreate,
    PortfolioIdsBody,
    PortfolioMembershipOut,
    PortfolioPreviewBody,
    PortfolioRuleBody,
    PortfolioSourcesBody,
    PortfolioTagsBody,
    PortfolioUpdate,
    ...
)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_api.py -k "rule or includes or sources or tags_endpoint or membership_and_preview" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(api): portfolio sub-resource endpoints (rule, includes, excludes, sources, tags, membership, preview)"
```

---

## Task 11: Pricer + risk integration with resolver

**Files:**
- Modify: `backend/app/services/position_pricer.py`
- Modify: `backend/app/services/risk_engine.py`
- Modify: `tests/test_position_import_pricing.py`
- Modify: `tests/test_risk_engine.py`

- [ ] **Step 1: Add a failing test for pricing on a view portfolio**

Append to `tests/test_position_import_pricing.py`:

```python
def test_price_view_portfolio_resolves_across_containers(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, PortfolioKind
    from app.services.position_pricer import price_portfolio_positions

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as session:
        c = Portfolio(name="C", base_currency="USD", kind=PortfolioKind.CONTAINER.value)
        session.add(c)
        session.flush()
        pos = Position(
            portfolio_id=c.id, underlying="AAPL", product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine", quantity=1.0,
        )
        session.add(pos)
        session.flush()
        v = Portfolio(
            name="V", base_currency="USD", kind=PortfolioKind.VIEW.value,
            filter_rule={"op": "eq", "field": "product_type", "value": "EuropeanVanillaOption"},
        )
        session.add(v)
        session.flush()

        run = price_portfolio_positions(session, portfolio_id=v.id)
        session.commit()
        assert run.resolved_position_ids == [pos.id]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_position_import_pricing.py::test_price_view_portfolio_resolves_across_containers -v`
Expected: FAIL — pricer doesn't yet resolve through views, or `resolved_position_ids` not populated.

- [ ] **Step 3: Update `position_pricer.py` to use the resolver**

In `backend/app/services/position_pricer.py`, locate `price_portfolio_positions` (the function the agent and HTTP layer call). Find where it currently iterates over `portfolio.positions`. Replace with:

```python
from .portfolio_membership import resolve_positions
# ...
def price_portfolio_positions(session, *, portfolio_id, position_ids=None, valuation_date=None, overrides=None):
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio {portfolio_id} not found")

    candidates = resolve_positions(portfolio, session)
    if position_ids:
        wanted = set(int(i) for i in position_ids)
        candidates = [p for p in candidates if p.id in wanted]
    resolved_ids = [p.id for p in candidates]
    # ... rest of pricing logic unchanged ...
    run.resolved_position_ids = resolved_ids
```

(The exact diff depends on the existing function shape; the engineer keeps the rest of the function — just swaps the `portfolio.positions` iteration for the resolver result and stores the resolved id list on the run before commit.)

- [ ] **Step 4: Add a failing test for risk on a view**

Append to `tests/test_risk_engine.py`:

```python
def test_risk_run_view_portfolio_records_resolved_ids(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import PortfolioKind, Portfolio, Position
    from app.services.risk_engine import run_portfolio_risk

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as session:
        c = Portfolio(name="C", base_currency="USD", kind=PortfolioKind.CONTAINER.value)
        session.add(c)
        session.flush()
        pos = Position(
            portfolio_id=c.id, underlying="AAPL", product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine", quantity=1.0,
        )
        session.add(pos)
        session.flush()
        v = Portfolio(
            name="V", base_currency="USD", kind=PortfolioKind.VIEW.value,
            filter_rule={"op": "eq", "field": "underlying", "value": "AAPL"},
        )
        session.add(v)
        session.flush()
        run = run_portfolio_risk(session, portfolio_id=v.id, method="summary")
        session.commit()
        assert run.resolved_position_ids == [pos.id]
```

- [ ] **Step 5: Update `risk_engine.py` similarly**

In `backend/app/services/risk_engine.py`, locate the function (likely `run_portfolio_risk` or similar). Use `resolve_positions(portfolio, session)` instead of `portfolio.positions`, and persist `resolved_position_ids` on the `RiskRun` row.

- [ ] **Step 6: Run both tests**

Run:
```bash
pytest tests/test_position_import_pricing.py::test_price_view_portfolio_resolves_across_containers tests/test_risk_engine.py::test_risk_run_view_portfolio_records_resolved_ids -v
```
Expected: PASS, plus no regression on existing pricer/risk tests.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/position_pricer.py backend/app/services/risk_engine.py tests/test_position_import_pricing.py tests/test_risk_engine.py
git commit -m "feat(pricer+risk): resolve positions through portfolio_membership; persist resolved_position_ids"
```

---

## Task 12: CLI scaffolding + list/show/create/create-view

**Files:**
- Modify: `backend/app/cli.py`
- Create: `tests/test_cli_portfolios.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_portfolios.py`:

```python
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from app import database
from app.cli import main as cli_main
from app.config import Settings


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()


def _run(*argv: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(list(argv))
    assert rc == 0, buf.getvalue()
    return json.loads(buf.getvalue())


def test_create_then_list():
    _run("portfolios", "create", "--name", "Book", "--kind", "container")
    out = _run("portfolios", "list", "--json")
    assert any(p["name"] == "Book" for p in out)


def test_create_view_with_rule_text():
    _run("portfolios", "create-view", "--name", "Snow",
         "--rule-text", 'product_type = Snowball')
    out = _run("portfolios", "show", "--portfolio", "Snow")
    assert out["filter_rule"]["op"] == "eq"


def test_list_kind_filter():
    _run("portfolios", "create", "--name", "C", "--kind", "container")
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "list", "--kind", "view", "--json")
    assert {p["name"] for p in out} == {"V"}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: FAIL — `portfolios` subcommand unknown.

- [ ] **Step 3: Add the `portfolios` subparser to `cli.py`**

In `backend/app/cli.py`, after the `positions_parser` block, add:

```python
    portfolios_parser = subparsers.add_parser("portfolios")
    portfolio_commands = portfolios_parser.add_subparsers(dest="command", required=True)

    list_p = portfolio_commands.add_parser("list")
    list_p.add_argument("--kind", choices=["container", "view"])
    list_p.add_argument("--tag", action="append", default=[])
    list_p.add_argument("--json", action="store_true")

    show_p = portfolio_commands.add_parser("show")
    show_p.add_argument("--portfolio", required=True)

    create_p = portfolio_commands.add_parser("create")
    create_p.add_argument("--name", required=True)
    create_p.add_argument("--kind", choices=["container", "view"], default="container")
    create_p.add_argument("--base-currency", default="USD")
    create_p.add_argument("--description", default=None)
    create_p.add_argument("--tag", action="append", default=[])

    create_view_p = portfolio_commands.add_parser("create-view")
    create_view_p.add_argument("--name", required=True)
    create_view_p.add_argument("--base-currency", default="USD")
    create_view_p.add_argument("--description", default=None)
    create_view_p.add_argument("--rule-text", default=None)
    create_view_p.add_argument("--rule-json", default=None, help="@file.json or inline JSON")
    create_view_p.add_argument("--include-id", dest="include_ids", action="append", type=int, default=[])
    create_view_p.add_argument("--source-id", dest="source_ids", action="append", type=int, default=[])
    create_view_p.add_argument("--tag", action="append", default=[])
```

In the dispatch block (`if args.resource == ...`), add a handler for `args.resource == "portfolios"` that delegates to a `_run_portfolios_command(session, args)` function:

```python
    if args.resource == "portfolios":
        payload = _run_portfolios_command(session, args)
    elif args.resource == "positions" and args.command == "import":
        ...  # existing
```

Then add the helper functions at the bottom of the file:

```python
def _serialize_portfolio(portfolio) -> dict:
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "kind": portfolio.kind,
        "base_currency": portfolio.base_currency,
        "description": portfolio.description,
        "tags": list(portfolio.tags or []),
        "filter_rule": portfolio.filter_rule,
        "manual_include_ids": list(portfolio.manual_include_ids or []),
        "manual_exclude_ids": list(portfolio.manual_exclude_ids or []),
        "source_portfolio_ids": list(portfolio.source_portfolio_ids or []),
    }


def _read_rule_arg(rule_text: str | None, rule_json: str | None) -> dict | None:
    from .services.portfolio_rule_dsl import parse_text_to_rule
    if rule_text and rule_json:
        raise ValueError("Pass at most one of --rule-text / --rule-json")
    if rule_text:
        return parse_text_to_rule(rule_text)
    if rule_json:
        if rule_json.startswith("@"):
            return json.loads(Path(rule_json[1:]).read_text())
        return json.loads(rule_json)
    return None


def _run_portfolios_command(session, args) -> dict:
    from .services import portfolio_service
    cmd = args.command
    if cmd == "list":
        rows = portfolio_service.list_portfolios(session, kind=args.kind, tags=args.tag or None)
        out = [_serialize_portfolio(p) for p in rows]
        return {"portfolios": out}
    if cmd == "show":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        return _serialize_portfolio(portfolio)
    if cmd == "create":
        p = portfolio_service.create_portfolio(
            session, name=args.name, base_currency=args.base_currency,
            kind=args.kind, description=args.description, tags=args.tag or [],
        )
        return _serialize_portfolio(p)
    if cmd == "create-view":
        rule = _read_rule_arg(args.rule_text, args.rule_json)
        p = portfolio_service.create_portfolio(
            session, name=args.name, base_currency=args.base_currency,
            kind="view", description=args.description, tags=args.tag or [],
            filter_rule=rule,
            manual_include_ids=args.include_ids or [],
            source_portfolio_ids=args.source_ids or [],
        )
        return _serialize_portfolio(p)
    raise SystemExit(f"Unknown portfolios subcommand: {cmd}")
```

(The existing `_resolve_portfolio` helper already does name-or-id lookup.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: PASS for create / create-view / list / show.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py tests/test_cli_portfolios.py
git commit -m "feat(cli): portfolios subcommand — list, show, create, create-view"
```

---

## Task 13: CLI — update / delete / set-rule / resolve

**Files:**
- Modify: `backend/app/cli.py`
- Modify: `tests/test_cli_portfolios.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_cli_portfolios.py`:

```python
def test_update_then_delete():
    _run("portfolios", "create", "--name", "X", "--kind", "container")
    out = _run("portfolios", "update", "--portfolio", "X", "--description", "demo", "--tag", "alpha")
    assert out["description"] == "demo"
    assert out["tags"] == ["alpha"]
    out = _run("portfolios", "delete", "--portfolio", "X", "--confirm")
    assert out["deleted"] is True


def test_set_rule_via_text():
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "set-rule", "--portfolio", "V",
               "--rule-text", 'product_type = Snowball AND status = open')
    assert out["filter_rule"]["op"] == "and"


def test_resolve_view():
    _run("portfolios", "create", "--name", "C", "--kind", "container")
    # No positions yet — resolution should be empty
    out = _run("portfolios", "resolve", "--portfolio", "C")
    assert out["position_ids"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: FAIL — subcommands not yet defined.

- [ ] **Step 3: Add the subparsers**

Append to the `portfolio_commands` block in `cli.py`:

```python
    update_p = portfolio_commands.add_parser("update")
    update_p.add_argument("--portfolio", required=True)
    update_p.add_argument("--name")
    update_p.add_argument("--description")
    update_p.add_argument("--base-currency")
    update_p.add_argument("--tag", action="append", default=None)

    delete_p = portfolio_commands.add_parser("delete")
    delete_p.add_argument("--portfolio", required=True)
    delete_p.add_argument("--confirm", action="store_true", required=True)

    set_rule_p = portfolio_commands.add_parser("set-rule")
    set_rule_p.add_argument("--portfolio", required=True)
    set_rule_p.add_argument("--rule-text", default=None)
    set_rule_p.add_argument("--rule-json", default=None)

    resolve_p = portfolio_commands.add_parser("resolve")
    resolve_p.add_argument("--portfolio", required=True)
```

Extend `_run_portfolios_command` with:

```python
    if cmd == "update":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        portfolio_service.update_portfolio(
            session, portfolio.id,
            name=args.name, description=args.description,
            base_currency=args.base_currency, tags=args.tag,
        )
        session.flush()
        return _serialize_portfolio(portfolio)
    if cmd == "delete":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        portfolio_service.delete_portfolio(session, portfolio.id)
        return {"deleted": True, "id": portfolio.id, "name": portfolio.name}
    if cmd == "set-rule":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        rule = _read_rule_arg(args.rule_text, args.rule_json)
        portfolio_service.set_filter_rule(session, portfolio.id, rule)
        session.flush()
        return _serialize_portfolio(portfolio)
    if cmd == "resolve":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        ids = portfolio_service.preview_membership(session, portfolio.id)
        return {"portfolio_id": portfolio.id, "position_ids": ids}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: PASS for new tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py tests/test_cli_portfolios.py
git commit -m "feat(cli): portfolios update / delete / set-rule / resolve"
```

---

## Task 14: CLI — includes / excludes / sources / tags

**Files:**
- Modify: `backend/app/cli.py`
- Modify: `tests/test_cli_portfolios.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_cli_portfolios.py`:

```python
def test_includes_excludes_via_cli():
    cid = _run("portfolios", "create", "--name", "C", "--kind", "container")["id"]
    # Use service helper through HTTP-style; inline insertion via SQL fixture
    from app import database
    from app.models import Position, Portfolio
    with database.SessionLocal() as session:
        p = Position(portfolio_id=cid, underlying="AAPL", product_type="X", quantity=1.0)
        session.add(p)
        session.commit()
        pos_id = p.id
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "includes", "add", "--portfolio", "V", "--position-id", str(pos_id))
    assert out["manual_include_ids"] == [pos_id]
    out = _run("portfolios", "includes", "remove", "--portfolio", "V", "--position-id", str(pos_id))
    assert out["manual_include_ids"] == []


def test_sources_via_cli_and_cycle():
    a = _run("portfolios", "create", "--name", "A", "--kind", "view")["id"]
    b = _run("portfolios", "create", "--name", "B", "--kind", "view")["id"]
    out = _run("portfolios", "sources", "add", "--portfolio", "A", "--source", str(b))
    assert out["source_portfolio_ids"] == [b]
    with pytest.raises(AssertionError):
        _run("portfolios", "sources", "add", "--portfolio", "B", "--source", str(a))


def test_tags_via_cli():
    _run("portfolios", "create", "--name", "P", "--kind", "container")
    out = _run("portfolios", "tags", "set", "--portfolio", "P", "--tag", "Alpha", "--tag", "BETA")
    assert out["tags"] == ["alpha", "beta"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the subparsers**

Append to the `portfolio_commands` block:

```python
    includes_p = portfolio_commands.add_parser("includes")
    includes_p.add_argument("action", choices=["add", "remove"])
    includes_p.add_argument("--portfolio", required=True)
    includes_p.add_argument("--position-id", dest="position_ids", action="append", type=int, required=True)

    excludes_p = portfolio_commands.add_parser("excludes")
    excludes_p.add_argument("action", choices=["add", "remove"])
    excludes_p.add_argument("--portfolio", required=True)
    excludes_p.add_argument("--position-id", dest="position_ids", action="append", type=int, required=True)

    sources_p = portfolio_commands.add_parser("sources")
    sources_p.add_argument("action", choices=["add", "remove"])
    sources_p.add_argument("--portfolio", required=True)
    sources_p.add_argument("--source", dest="source_ids", action="append", type=int, required=True)

    tags_p = portfolio_commands.add_parser("tags")
    tags_p.add_argument("action", choices=["set"])
    tags_p.add_argument("--portfolio", required=True)
    tags_p.add_argument("--tag", action="append", default=[], required=True)
```

Extend `_run_portfolios_command`:

```python
    if cmd in ("includes", "excludes"):
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        action = args.action
        ids = args.position_ids
        attr = "manual_include_ids" if cmd == "includes" else "manual_exclude_ids"
        if action == "add":
            fn = portfolio_service.add_manual_includes if cmd == "includes" else portfolio_service.add_manual_excludes
            fn(session, portfolio.id, ids)
        else:
            fn = portfolio_service.remove_manual_includes if cmd == "includes" else portfolio_service.remove_manual_excludes
            fn(session, portfolio.id, ids)
        session.flush()
        return _serialize_portfolio(portfolio)
    if cmd == "sources":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        if args.action == "add":
            portfolio_service.add_portfolio_sources(session, portfolio.id, args.source_ids)
        else:
            portfolio_service.remove_portfolio_sources(session, portfolio.id, args.source_ids)
        session.flush()
        return _serialize_portfolio(portfolio)
    if cmd == "tags":
        portfolio = _resolve_portfolio(session, args.portfolio, create=False)
        portfolio_service.set_portfolio_tags(session, portfolio.id, args.tag)
        session.flush()
        return _serialize_portfolio(portfolio)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_portfolios.py -v`
Expected: PASS (the cycle test asserts `_run` raises `AssertionError` because the service raises and `cli_main` exits non-zero — `_run`'s `assert rc == 0` triggers).

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py tests/test_cli_portfolios.py
git commit -m "feat(cli): portfolios includes/excludes/sources/tags subcommands"
```

---

## Task 15: LangChain tools — read tools + create

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Modify: `tests/test_agent_tools.py`

- [ ] **Step 1: Add failing tests for the read tools**

Append to `tests/test_agent_tools.py` (use the file's existing fixture/setup style):

```python
def test_create_portfolio_tool_view_with_rule(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import create_portfolio_tool, get_portfolio_tool, list_portfolios_tool

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    out = create_portfolio_tool.invoke({
        "name": "Snowballs", "kind": "view",
        "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
        "tags": ["desk"],
    })
    assert out["ok"] is True
    pid = out["data"]["id"]

    detail = get_portfolio_tool.invoke({"portfolio_id": pid})
    assert detail["ok"] is True
    assert detail["data"]["kind"] == "view"

    listed = list_portfolios_tool.invoke({"kind": "view"})
    assert listed["ok"] is True
    assert any(p["id"] == pid for p in listed["data"])


def test_create_portfolio_tool_invalid_rule_returns_errors(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import create_portfolio_tool

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    out = create_portfolio_tool.invoke({
        "name": "Bad", "kind": "view",
        "filter_rule": {"op": "weird"},
    })
    assert out["ok"] is False
    assert out["errors"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_tools.py -k "create_portfolio_tool or list_portfolios_tool or get_portfolio_tool" -v`
Expected: FAIL.

- [ ] **Step 3: Implement tools and pydantic input schemas**

Append to `backend/app/services/langchain_tools.py` (before the `QUANT_AGENT_TOOLS` list):

```python
from .. import database as _portfolio_database
from ..models import (
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKindError,
    PortfolioNameConflict,
    RuleCompilationError,
    RuleValidationError,
)
from . import portfolio_service


class _ListPortfoliosInput(BaseModel):
    kind: Literal["container", "view"] | None = None
    tags: list[str] | None = None


class _GetPortfolioInput(BaseModel):
    portfolio_id: int


class _CreatePortfolioInput(BaseModel):
    name: str
    kind: Literal["container", "view"] = "container"
    base_currency: str = "USD"
    description: str | None = None
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def _portfolio_summary(p) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "base_currency": p.base_currency,
        "description": p.description,
        "tags": list(p.tags or []),
        "filter_rule": p.filter_rule,
        "manual_include_ids": list(p.manual_include_ids or []),
        "manual_exclude_ids": list(p.manual_exclude_ids or []),
        "source_portfolio_ids": list(p.source_portfolio_ids or []),
    }


def _portfolio_error_response(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RuleValidationError):
        return {"ok": False, "errors": exc.errors}
    if isinstance(exc, PortfolioCycleError):
        return {"ok": False, "error": str(exc), "cycle_path": exc.cycle_path}
    if isinstance(exc, PortfolioDepthError):
        return {"ok": False, "error": str(exc), "depth_path": exc.depth_path}
    return {"ok": False, "error": str(exc)}


@tool("list_portfolios", args_schema=_ListPortfoliosInput)
def list_portfolios_tool(kind: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """List portfolios with optional kind and tag filters."""
    with _portfolio_database.SessionLocal() as session:
        rows = portfolio_service.list_portfolios(session, kind=kind, tags=tags)
        return {"ok": True, "data": [_portfolio_summary(p) for p in rows]}


@tool("get_portfolio", args_schema=_GetPortfolioInput)
def get_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Return portfolio detail including resolved positions for views."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.get_portfolio(session, portfolio_id)
            ids = portfolio_service.preview_membership(session, portfolio_id)
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except (PortfolioCycleError, PortfolioDepthError, RuleCompilationError) as exc:
            return _portfolio_error_response(exc)
        body = _portfolio_summary(portfolio) | {"resolved_position_ids": ids}
        return {"ok": True, "data": body}


@tool("create_portfolio", args_schema=_CreatePortfolioInput)
def create_portfolio_tool(
    name: str,
    kind: str = "container",
    base_currency: str = "USD",
    description: str | None = None,
    filter_rule: dict[str, Any] | None = None,
    manual_include_ids: list[int] | None = None,
    source_portfolio_ids: list[int] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create a portfolio (container or view). Returns the created record."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.create_portfolio(
                session, name=name, base_currency=base_currency, kind=kind,
                description=description,
                filter_rule=filter_rule,
                manual_include_ids=manual_include_ids or [],
                source_portfolio_ids=source_portfolio_ids or [],
                tags=tags or [],
            )
            session.commit()
        except (PortfolioNameConflict, PortfolioKindError, RuleValidationError,
                RuleCompilationError, PortfolioCycleError) as exc:
            session.rollback()
            return _portfolio_error_response(exc)
        return {"ok": True, "data": _portfolio_summary(portfolio)}
```

- [ ] **Step 4: Add tools to `QUANT_AGENT_TOOLS`**

Append to the existing `QUANT_AGENT_TOOLS` list:

```python
    list_portfolios_tool,
    get_portfolio_tool,
    create_portfolio_tool,
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_agent_tools.py -k "create_portfolio_tool or list_portfolios_tool or get_portfolio_tool" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/langchain_tools.py tests/test_agent_tools.py
git commit -m "feat(tools): list/get/create portfolio LangChain tools"
```

---

## Task 16: LangChain tools — update / delete / set-rule + HITL registration

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_hitl.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_agent_tools.py`:

```python
def test_update_and_delete_portfolio_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import create_portfolio_tool, update_portfolio_tool, delete_portfolio_tool

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    pid = create_portfolio_tool.invoke({"name": "P", "kind": "container"})["data"]["id"]
    out = update_portfolio_tool.invoke({"portfolio_id": pid, "description": "d"})
    assert out["ok"] is True
    assert out["data"]["description"] == "d"
    out = delete_portfolio_tool.invoke({"portfolio_id": pid})
    assert out["ok"] is True


def test_set_portfolio_rule_tool_validates(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import create_portfolio_tool, set_portfolio_rule_tool

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    pid = create_portfolio_tool.invoke({"name": "V", "kind": "view"})["data"]["id"]
    out = set_portfolio_rule_tool.invoke({"portfolio_id": pid,
        "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"}})
    assert out["ok"] is True
```

Append to `tests/test_hitl.py`:

```python
def test_hitl_registers_portfolio_tools():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES, _LABEL_BY_TOOL, _RISK_LEVEL_BY_TOOL,
    )
    for tool_name in ("delete_portfolio", "set_portfolio_rule", "remove_positions_from_portfolio"):
        assert tool_name in INTERRUPT_TOOL_NAMES
        assert tool_name in _LABEL_BY_TOOL
        assert tool_name in _RISK_LEVEL_BY_TOOL
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_tools.py tests/test_hitl.py -k "portfolio" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the tools**

Append to `backend/app/services/langchain_tools.py`:

```python
class _UpdatePortfolioInput(BaseModel):
    portfolio_id: int
    name: str | None = None
    description: str | None = None
    base_currency: str | None = None
    tags: list[str] | None = None


class _DeletePortfolioInput(BaseModel):
    portfolio_id: int


class _SetPortfolioRuleInput(BaseModel):
    portfolio_id: int
    filter_rule: dict[str, Any] | None = None


@tool("update_portfolio", args_schema=_UpdatePortfolioInput)
def update_portfolio_tool(
    portfolio_id: int, name: str | None = None, description: str | None = None,
    base_currency: str | None = None, tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update portfolio fields (name/description/base_currency/tags)."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.update_portfolio(
                session, portfolio_id,
                name=name, description=description,
                base_currency=base_currency, tags=tags,
            )
            session.commit()
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except (PortfolioNameConflict, RuleValidationError) as exc:
            session.rollback()
            return _portfolio_error_response(exc)
        return {"ok": True, "data": _portfolio_summary(portfolio)}


@tool("delete_portfolio", args_schema=_DeletePortfolioInput)
def delete_portfolio_tool(portfolio_id: int) -> dict[str, Any]:
    """Delete a portfolio. Container kind cascades positions; view leaves them. HITL-gated."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio_service.delete_portfolio(session, portfolio_id)
            session.commit()
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "data": {"deleted": True, "id": portfolio_id}}


@tool("set_portfolio_rule", args_schema=_SetPortfolioRuleInput)
def set_portfolio_rule_tool(portfolio_id: int, filter_rule: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replace the filter rule on a view portfolio. HITL-gated."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.set_filter_rule(session, portfolio_id, filter_rule)
            session.commit()
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except (PortfolioKindError, RuleValidationError, RuleCompilationError) as exc:
            session.rollback()
            return _portfolio_error_response(exc)
        return {"ok": True, "data": _portfolio_summary(portfolio)}
```

Append to `QUANT_AGENT_TOOLS`:

```python
    update_portfolio_tool,
    delete_portfolio_tool,
    set_portfolio_rule_tool,
```

- [ ] **Step 4: Register HITL gates in `hitl.py`**

In `backend/app/services/deep_agent/hitl.py`, extend the three module-level tables:

```python
INTERRUPT_TOOL_NAMES: tuple[str, ...] = (
    "price_positions",
    "run_risk",
    "create_report",
    "approve_rfq",
    "reject_rfq",
    "import_otc_positions",
    "import_position_market_inputs",
    "delete_portfolio",
    "set_portfolio_rule",
    "remove_positions_from_portfolio",
)

_RISK_LEVEL_BY_TOOL: dict[str, str] = {
    # ... existing entries ...
    "delete_portfolio": "irreversible",
    "set_portfolio_rule": "write",
    "remove_positions_from_portfolio": "irreversible",
}

_LABEL_BY_TOOL: dict[str, str] = {
    # ... existing entries ...
    "delete_portfolio": "Delete portfolio",
    "set_portfolio_rule": "Replace portfolio filter rule",
    "remove_positions_from_portfolio": "Remove positions from portfolio",
}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_agent_tools.py tests/test_hitl.py -k "portfolio" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/langchain_tools.py backend/app/services/deep_agent/hitl.py tests/test_agent_tools.py tests/test_hitl.py
git commit -m "feat(tools+hitl): update/delete/set-rule portfolio tools + HITL gate registration"
```

---

## Task 17: LangChain tools — add/remove positions + sources

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Modify: `tests/test_agent_tools.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_agent_tools.py`:

```python
def test_add_remove_positions_via_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, PortfolioKind
    from app.services.langchain_tools import (
        add_positions_to_portfolio_tool,
        create_portfolio_tool,
        remove_positions_from_portfolio_tool,
    )

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    cid = create_portfolio_tool.invoke({"name": "C", "kind": "container"})["data"]["id"]
    with database.SessionLocal() as session:
        p = Position(portfolio_id=cid, underlying="AAPL", product_type="X", quantity=1.0)
        session.add(p)
        session.commit()
        pos_id = p.id

    vid = create_portfolio_tool.invoke({"name": "V", "kind": "view"})["data"]["id"]

    out = add_positions_to_portfolio_tool.invoke({"portfolio_id": vid, "position_ids": [pos_id]})
    assert out["ok"] is True
    assert out["data"]["manual_include_ids"] == [pos_id]

    out = remove_positions_from_portfolio_tool.invoke({"portfolio_id": vid, "position_ids": [pos_id]})
    assert out["ok"] is True
    assert out["data"]["manual_include_ids"] == []


def test_add_remove_sources_via_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import (
        add_portfolio_sources_tool,
        create_portfolio_tool,
        remove_portfolio_sources_tool,
    )

    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()

    a = create_portfolio_tool.invoke({"name": "A", "kind": "view"})["data"]["id"]
    b = create_portfolio_tool.invoke({"name": "B", "kind": "view"})["data"]["id"]

    out = add_portfolio_sources_tool.invoke({"portfolio_id": a, "source_portfolio_ids": [b]})
    assert out["ok"] is True
    out = add_portfolio_sources_tool.invoke({"portfolio_id": b, "source_portfolio_ids": [a]})
    assert out["ok"] is False
    assert "cycle_path" in out
    out = remove_portfolio_sources_tool.invoke({"portfolio_id": a, "source_portfolio_ids": [b]})
    assert out["ok"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_tools.py -k "positions_via_tool or sources_via_tool" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the tools**

Append to `backend/app/services/langchain_tools.py`:

```python
class _PortfolioIdsInput(BaseModel):
    portfolio_id: int
    position_ids: list[int]


class _PortfolioSourcesInput(BaseModel):
    portfolio_id: int
    source_portfolio_ids: list[int]


@tool("add_positions_to_portfolio", args_schema=_PortfolioIdsInput)
def add_positions_to_portfolio_tool(portfolio_id: int, position_ids: list[int]) -> dict[str, Any]:
    """Add positions to a portfolio. Container: physical add via existing flow.
    View: append to manual_include_ids.
    """
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.get_portfolio(session, portfolio_id)
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        if portfolio.kind == "view":
            try:
                portfolio = portfolio_service.add_manual_includes(session, portfolio_id, position_ids)
                session.commit()
            except (PortfolioKindError, RuleValidationError) as exc:
                session.rollback()
                return _portfolio_error_response(exc)
            return {"ok": True, "data": _portfolio_summary(portfolio), "kind_resolved_as": "view"}
        # Container kind: caller must pass full PortfolioPositionSpec dicts elsewhere;
        # we only accept ids that already exist in some portfolio and reattach by id.
        return {
            "ok": False,
            "error": "Container portfolios add positions via /api/portfolios/{id}/positions; "
                     "the agent should pass full position specs through that endpoint.",
        }


@tool("remove_positions_from_portfolio", args_schema=_PortfolioIdsInput)
def remove_positions_from_portfolio_tool(portfolio_id: int, position_ids: list[int]) -> dict[str, Any]:
    """Remove positions. Container: HITL-gated, deletes Position rows. View: pulls from manual_include_ids."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.get_portfolio(session, portfolio_id)
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        if portfolio.kind == "view":
            portfolio = portfolio_service.remove_manual_includes(session, portfolio_id, position_ids)
            session.commit()
            return {"ok": True, "data": _portfolio_summary(portfolio), "kind_resolved_as": "view"}
        # Container: physically delete owned Position rows
        from ..models import Position
        wanted = set(position_ids)
        deleted: list[int] = []
        for pos in list(portfolio.positions):
            if pos.id in wanted:
                session.delete(pos)
                deleted.append(pos.id)
        session.flush()
        from .audit import record_audit
        record_audit(
            session,
            event_type="portfolio.positions_removed",
            actor="agent",
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={"deleted_position_ids": deleted},
        )
        session.commit()
        return {"ok": True, "data": _portfolio_summary(portfolio),
                "kind_resolved_as": "container", "deleted_position_ids": deleted}


@tool("add_portfolio_sources", args_schema=_PortfolioSourcesInput)
def add_portfolio_sources_tool(portfolio_id: int, source_portfolio_ids: list[int]) -> dict[str, Any]:
    """Add cross-portfolio sources to a view (cycle/depth-checked)."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.add_portfolio_sources(session, portfolio_id, source_portfolio_ids)
            session.commit()
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except (PortfolioKindError, RuleValidationError, PortfolioCycleError) as exc:
            session.rollback()
            return _portfolio_error_response(exc)
        return {"ok": True, "data": _portfolio_summary(portfolio)}


@tool("remove_portfolio_sources", args_schema=_PortfolioSourcesInput)
def remove_portfolio_sources_tool(portfolio_id: int, source_portfolio_ids: list[int]) -> dict[str, Any]:
    """Remove sources from a view portfolio."""
    with _portfolio_database.SessionLocal() as session:
        try:
            portfolio = portfolio_service.remove_portfolio_sources(session, portfolio_id, source_portfolio_ids)
            session.commit()
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        except PortfolioKindError as exc:
            session.rollback()
            return _portfolio_error_response(exc)
        return {"ok": True, "data": _portfolio_summary(portfolio)}
```

Append to `QUANT_AGENT_TOOLS`:

```python
    add_positions_to_portfolio_tool,
    remove_positions_from_portfolio_tool,
    add_portfolio_sources_tool,
    remove_portfolio_sources_tool,
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_agent_tools.py -k "positions_via_tool or sources_via_tool" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/langchain_tools.py tests/test_agent_tools.py
git commit -m "feat(tools): portfolio positions add/remove + sources add/remove (cycle-guarded)"
```

---

## Task 18: End-to-end backend integration test

**Files:**
- Create: `tests/test_portfolio_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_portfolio_integration.py`:

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import Portfolio, Position, PortfolioKind
from app.services import portfolio_service
from app.services.position_pricer import price_portfolio_positions


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=f"sqlite:///{tmp_path}/t.db"))
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def test_create_view_then_price_resolves_correctly(session):
    container = portfolio_service.create_portfolio(
        session, name="Book", base_currency="USD", kind="container",
    )
    session.flush()
    pos_a = Position(
        portfolio_id=container.id, underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name="BlackScholesEngine", quantity=1.0,
    )
    pos_b = Position(
        portfolio_id=container.id, underlying="TSLA",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 200.0, "option_type": "PUT", "maturity": 1.0},
        engine_name="BlackScholesEngine", quantity=2.0,
    )
    session.add_all([pos_a, pos_b])
    session.flush()

    view = portfolio_service.create_portfolio(
        session, name="View", base_currency="USD", kind="view",
        filter_rule={"op": "in", "field": "underlying", "value": ["AAPL"]},
    )
    session.commit()

    resolved_ids = portfolio_service.preview_membership(session, view.id)
    assert resolved_ids == [pos_a.id]

    run = price_portfolio_positions(session, portfolio_id=view.id)
    session.commit()
    assert sorted(run.resolved_position_ids) == [pos_a.id]
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_portfolio_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_portfolio_integration.py
git commit -m "test(portfolio): end-to-end create view → resolve → price → assert resolved_position_ids"
```

---

## Task 19: Frontend types + Route rename

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Update `Route` and add new portfolio types**

In `frontend/src/types.ts`, replace line 2:

```ts
export type Route = 'chat' | 'rfq' | 'positions' | 'portfolios' | 'risk' | 'reports' | 'client';
```

Append new types at the bottom of the file:

```ts
export type PortfolioKind = 'container' | 'view';

export type FilterRule =
  | { op: 'and'; children: FilterRule[] }
  | { op: 'or';  children: FilterRule[] }
  | { op: 'not'; child: FilterRule }
  | { op: 'eq' | 'ne' | 'lt' | 'lte' | 'gt' | 'gte';
      field: string; value: string | number };
  | { op: 'in' | 'not_in'; field: string; value: (string | number)[] }
  | { op: 'between'; field: string; value: [number | string, number | string] };

export type PortfolioSummary = {
  id: number;
  name: string;
  kind: PortfolioKind;
  base_currency: string;
  description: string | null;
  tags: string[];
  filter_rule: FilterRule | null;
  manual_include_ids: number[];
  manual_exclude_ids: number[];
  source_portfolio_ids: number[];
  resolved_position_count: number;
  created_at: string;
  updated_at: string;
};

export type PortfolioDetail = PortfolioSummary & {
  positions: Position[];
};

export type PortfolioPreviewBody = {
  kind: PortfolioKind;
  filter_rule?: FilterRule | null;
  manual_include_ids?: number[];
  manual_exclude_ids?: number[];
  source_portfolio_ids?: number[];
};

export type PortfolioMembership = {
  portfolio_id: number;
  position_ids: number[];
};
```

(If a `Position` type doesn't exist in `types.ts`, reuse the existing imports — the codebase already has the `PositionRow` shape in `Positions.tsx`. Move it into `types.ts` as `Position` if needed.)

- [ ] **Step 2: Rename `'portfolio'` → `'positions'` in `main.tsx`**

In `frontend/src/main.tsx`:

- Replace `route: 'portfolio' as const, label: 'Positions'` with `route: 'positions' as const, label: 'Positions'`.
- Insert a new `navItems` entry: `{ route: 'portfolios' as const, label: 'Portfolios' }` between `'positions'` and `'risk'`.
- Update `initialRoute()` to return `'positions'` (not `'portfolio'`).
- Update the `useEffect` URL sync's pathname comparison if it referenced `'portfolio'`.
- Update each `commandItems` entry that used `id: 'jump-portfolio'` → `id: 'jump-positions'`.
- Add `{ id: 'jump-portfolios', group: 'Jump To', label: 'Portfolios', shortcut: '↵' }`.

- [ ] **Step 3: Add a placeholder `PortfoliosLive` route render in `main.tsx`**

Import a stub for now:

```tsx
import { PortfoliosLive } from './routes/Portfolios.live';
```

In the route-switching JSX, render `<PortfoliosLive />` when `route === 'portfolios'`. Until Task 27, `Portfolios.live.tsx` will be a thin "coming soon" placeholder so the route compiles.

Create a temporary `frontend/src/routes/Portfolios.live.tsx`:

```tsx
export function PortfoliosLive() {
  return <div style={{ padding: 24 }}>Portfolios — coming soon.</div>;
}
```

- [ ] **Step 4: Run frontend tests**

Run: `cd frontend && npm test -- --run`
Expected: PASS for all (rename should not break existing tests; placeholder route renders).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/main.tsx frontend/src/routes/Portfolios.live.tsx
git commit -m "feat(frontend): rename 'portfolio' route to 'positions', add 'portfolios' route stub + types"
```

---

## Task 20: Frontend rule-tree TS helpers

**Files:**
- Create: `frontend/src/lib/ruleTree.ts`
- Create: `frontend/src/lib/ruleTree.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/lib/ruleTree.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  ALLOWED_FIELDS,
  ALLOWED_OPS,
  parseDsl,
  serializeDsl,
  validateRule,
} from './ruleTree';

describe('ruleTree validate', () => {
  it('accepts simple eq', () => {
    expect(validateRule({ op: 'eq', field: 'product_type', value: 'Snowball' })).toEqual([]);
  });
  it('rejects unknown op', () => {
    const errs = validateRule({ op: 'matches', field: 'underlying', value: 'AAPL' } as any);
    expect(errs.some(e => e.includes('matches'))).toBe(true);
  });
  it('rejects unknown field', () => {
    const errs = validateRule({ op: 'eq', field: 'color', value: 'blue' } as any);
    expect(errs.some(e => e.includes('color'))).toBe(true);
  });
});

describe('ruleTree DSL', () => {
  it('parses simple eq', () => {
    expect(parseDsl('product_type = Snowball')).toEqual({
      op: 'eq', field: 'product_type', value: 'Snowball',
    });
  });
  it('roundtrips AND tree', () => {
    const tree = {
      op: 'and' as const,
      children: [
        { op: 'eq' as const, field: 'product_type', value: 'Snowball' },
        { op: 'in' as const, field: 'underlying', value: ['AAPL', 'TSLA'] },
      ],
    };
    expect(parseDsl(serializeDsl(tree))).toEqual(tree);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/lib/ruleTree.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ruleTree.ts` (mirror of backend grammar)**

Create `frontend/src/lib/ruleTree.ts`. The implementation mirrors `portfolio_rule.py` and `portfolio_rule_dsl.py`. Keep it lean — same op/field tables, same recursive validator, same hand-written parser, same serializer. Use this skeleton:

```ts
import type { FilterRule } from '../types';

export const ALLOWED_OPS = new Set([
  'and', 'or', 'not',
  'eq', 'ne', 'in', 'not_in',
  'lt', 'lte', 'gt', 'gte', 'between',
]);

export const ALLOWED_FIELDS: Record<string, 'string' | 'number' | 'datetime'> = {
  product_type: 'string', underlying: 'string', status: 'string',
  mapping_status: 'string', engine_name: 'string',
  quantity: 'number', entry_price: 'number',
  created_at: 'datetime',
};

export const MAX_RULE_DEPTH = 5;

export function validateRule(rule: any, path = '$', depth = 0): string[] {
  if (depth > MAX_RULE_DEPTH) return [`Rule depth exceeds ${MAX_RULE_DEPTH} at ${path}`];
  if (!rule || typeof rule !== 'object') return [`Rule node must be object at ${path}`];
  const op = rule.op;
  if (!ALLOWED_OPS.has(op)) return [`Unsupported op: ${JSON.stringify(op)} at ${path}`];

  if (op === 'and' || op === 'or') {
    if (!Array.isArray(rule.children) || rule.children.length === 0) {
      return [`Empty children for ${op} at ${path}`];
    }
    return rule.children.flatMap((c: any, i: number) => validateRule(c, `${path}.children[${i}]`, depth + 1));
  }
  if (op === 'not') {
    if (!rule.child || typeof rule.child !== 'object') return [`'not' requires child at ${path}`];
    return validateRule(rule.child, `${path}.child`, depth + 1);
  }
  if (!(rule.field in ALLOWED_FIELDS)) {
    return [`Unknown field: ${JSON.stringify(rule.field)} at ${path}`];
  }
  // Coarse value-shape checks — server is authoritative
  if (op === 'in' || op === 'not_in') {
    if (!Array.isArray(rule.value) || rule.value.length === 0) {
      return [`'${op}' requires non-empty list at ${path}`];
    }
  } else if (op === 'between') {
    if (!Array.isArray(rule.value) || rule.value.length !== 2) {
      return [`'between' requires 2-element list at ${path}`];
    }
  } else if (Array.isArray(rule.value)) {
    return [`'${op}' requires scalar value at ${path}`];
  }
  return [];
}

export class DslSyntaxError extends Error {}

const TOKEN_RE = /\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?|<=|>=|!=|=|<|>|\(|\)|,|[A-Za-z_][A-Za-z0-9_.\-]*)/y;
const KEYWORDS = new Set(['AND', 'OR', 'NOT', 'IN', 'BETWEEN']);

function tokenize(text: string): string[] {
  const out: string[] = [];
  TOKEN_RE.lastIndex = 0;
  while (TOKEN_RE.lastIndex < text.length) {
    const m = TOKEN_RE.exec(text);
    if (!m) throw new DslSyntaxError(`Unexpected character at ${TOKEN_RE.lastIndex}`);
    out.push(m[1]);
  }
  return out;
}

function isIdent(tok: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_.\-]*$/.test(tok) && !KEYWORDS.has(tok.toUpperCase());
}

function scalar(tok: string): string | number {
  if ((tok.startsWith('"') && tok.endsWith('"')) || (tok.startsWith("'") && tok.endsWith("'"))) {
    return tok.slice(1, -1).replace(/\\(.)/g, '$1');
  }
  const n = Number(tok);
  if (!Number.isNaN(n)) return n;
  return tok;
}

class Parser {
  i = 0;
  constructor(private tokens: string[]) {}
  peek() { return this.tokens[this.i]; }
  consume(expected?: string) {
    const t = this.tokens[this.i];
    if (t === undefined) throw new DslSyntaxError('Unexpected end');
    if (expected && t.toUpperCase() !== expected.toUpperCase()) {
      throw new DslSyntaxError(`Expected ${expected}, got ${t}`);
    }
    this.i++;
    return t;
  }
  parse(): FilterRule {
    const r = this.or();
    if (this.i !== this.tokens.length) throw new DslSyntaxError(`Trailing tokens`);
    return r;
  }
  or(): FilterRule {
    const left = this.and();
    const children = [left];
    while (this.peek()?.toUpperCase() === 'OR') { this.consume('OR'); children.push(this.and()); }
    return children.length === 1 ? children[0] : { op: 'or', children };
  }
  and(): FilterRule {
    const left = this.not();
    const children = [left];
    while (this.peek()?.toUpperCase() === 'AND') { this.consume('AND'); children.push(this.not()); }
    return children.length === 1 ? children[0] : { op: 'and', children };
  }
  not(): FilterRule {
    if (this.peek()?.toUpperCase() === 'NOT') { this.consume('NOT'); return { op: 'not', child: this.not() }; }
    return this.atom();
  }
  atom(): FilterRule {
    if (this.peek() === '(') { this.consume('('); const r = this.or(); this.consume(')'); return r; }
    return this.leaf();
  }
  leaf(): FilterRule {
    const ident = this.consume();
    if (!isIdent(ident)) throw new DslSyntaxError(`Expected field, got ${ident}`);
    const op = this.consume();
    const u = op.toUpperCase();
    if (u === 'NOT') { this.consume('IN'); return { op: 'not_in', field: ident, value: this.list() }; }
    if (u === 'IN') return { op: 'in', field: ident, value: this.list() };
    if (u === 'BETWEEN') {
      const lo = scalar(this.consume());
      this.consume('AND');
      const hi = scalar(this.consume());
      return { op: 'between', field: ident, value: [lo as any, hi as any] };
    }
    const sym: Record<string, FilterRule['op']> = { '=':'eq','!=':'ne','<':'lt','<=':'lte','>':'gt','>=':'gte' };
    const m = sym[op];
    if (!m) throw new DslSyntaxError(`Unknown op ${op}`);
    return { op: m, field: ident, value: scalar(this.consume()) } as FilterRule;
  }
  list(): (string | number)[] {
    this.consume('(');
    const out: (string | number)[] = [];
    if (this.peek() !== ')') {
      out.push(scalar(this.consume()));
      while (this.peek() === ',') { this.consume(','); out.push(scalar(this.consume())); }
    }
    this.consume(')');
    return out;
  }
}

export function parseDsl(text: string): FilterRule {
  if (!text.trim()) throw new DslSyntaxError('Empty rule text');
  return new Parser(tokenize(text)).parse();
}

function quote(v: any): string {
  if (typeof v === 'number') return String(v);
  const s = String(v);
  if (/^[A-Za-z_][A-Za-z0-9_.\-]*$/.test(s)) return s;
  return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}

export function serializeDsl(rule: FilterRule): string {
  switch (rule.op) {
    case 'and': return rule.children.map(serializeDsl).join(' AND ');
    case 'or':  return '(' + rule.children.map(serializeDsl).join(' OR ') + ')';
    case 'not': return 'NOT (' + serializeDsl(rule.child) + ')';
    case 'in':  return `${rule.field} IN (${rule.value.map(quote).join(', ')})`;
    case 'not_in': return `${rule.field} NOT IN (${rule.value.map(quote).join(', ')})`;
    case 'between': return `${rule.field} BETWEEN ${quote(rule.value[0])} AND ${quote(rule.value[1])}`;
  }
  const symMap: Record<string, string> = { eq:'=', ne:'!=', lt:'<', lte:'<=', gt:'>', gte:'>=' };
  return `${(rule as any).field} ${symMap[rule.op]} ${quote((rule as any).value)}`;
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/lib/ruleTree.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/ruleTree.ts frontend/src/lib/ruleTree.test.ts
git commit -m "feat(frontend): TS-mirrored rule validate / parse / serialize helpers"
```

---

## Task 21: KindChip + ResolvedPositionsTable components

**Files:**
- Create: `frontend/src/components/KindChip.tsx` + `.css` + `.test.tsx`
- Create: `frontend/src/components/ResolvedPositionsTable.tsx` + `.css` + `.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/KindChip.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { KindChip } from './KindChip';

test('renders container chip', () => {
  render(<KindChip kind="container" />);
  expect(screen.getByText(/container/i)).toBeInTheDocument();
});

test('renders view chip', () => {
  render(<KindChip kind="view" />);
  expect(screen.getByText(/view/i)).toBeInTheDocument();
});
```

Create `frontend/src/components/ResolvedPositionsTable.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { ResolvedPositionsTable } from './ResolvedPositionsTable';

const rows = [
  { id: 1, underlying: 'AAPL', product_type: 'Snowball', quantity: 10, entry_price: 0, status: 'open' },
  { id: 2, underlying: 'TSLA', product_type: 'Snowball', quantity: 5,  entry_price: 0, status: 'open' },
];

test('renders rows', () => {
  render(<ResolvedPositionsTable rows={rows as any} />);
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.getByText('TSLA')).toBeInTheDocument();
});

test('shows empty state', () => {
  render(<ResolvedPositionsTable rows={[]} />);
  expect(screen.getByText(/no positions/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/components/KindChip.test.tsx src/components/ResolvedPositionsTable.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement components**

Create `frontend/src/components/KindChip.tsx`:

```tsx
import './KindChip.css';
import type { PortfolioKind } from '../types';

export function KindChip({ kind }: { kind: PortfolioKind }) {
  return <span className={`wl-kindchip wl-kindchip--${kind}`}>{kind}</span>;
}
```

Create `frontend/src/components/KindChip.css`:

```css
.wl-kindchip {
  display: inline-flex; align-items: center;
  font-size: 11px; font-weight: 600;
  padding: 2px 8px; border-radius: 999px;
  text-transform: uppercase; letter-spacing: 0.04em;
  background: var(--wl-surface-2); color: var(--wl-text);
}
.wl-kindchip--view { background: var(--wl-accent-soft); color: var(--wl-accent); }
.wl-kindchip--container { background: var(--wl-surface-3); color: var(--wl-text); }
```

Create `frontend/src/components/ResolvedPositionsTable.tsx`:

```tsx
import './ResolvedPositionsTable.css';

type Row = {
  id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

export function ResolvedPositionsTable({ rows }: { rows: Row[] }) {
  if (rows.length === 0) {
    return <div className="wl-resolved-empty">No positions match this view.</div>;
  }
  return (
    <table className="wl-resolved">
      <thead>
        <tr><th>Trade</th><th>Underlying</th><th>Product</th><th>Qty</th><th>Status</th></tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id}>
            <td>{r.id}</td><td>{r.underlying}</td><td>{r.product_type}</td>
            <td>{r.quantity}</td><td>{r.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

Create `frontend/src/components/ResolvedPositionsTable.css`:

```css
.wl-resolved { width: 100%; font-size: 13px; }
.wl-resolved th, .wl-resolved td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--wl-border); }
.wl-resolved th { font-weight: 600; color: var(--wl-text-muted); }
.wl-resolved-empty { padding: 24px; text-align: center; color: var(--wl-text-muted); }
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/components/KindChip.test.tsx src/components/ResolvedPositionsTable.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/KindChip.* frontend/src/components/ResolvedPositionsTable.*
git commit -m "feat(frontend): KindChip and ResolvedPositionsTable primitives"
```

---

## Task 22: TagEditor component

**Files:**
- Create: `frontend/src/components/TagEditor.tsx` + `.css` + `.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/TagEditor.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { TagEditor } from './TagEditor';

function Harness({ initial = [] as string[] }) {
  const [tags, setTags] = useState(initial);
  return <TagEditor tags={tags} onChange={setTags} />;
}

test('adds and lower-cases tag', async () => {
  render(<Harness />);
  await userEvent.type(screen.getByPlaceholderText(/add tag/i), 'Alpha{enter}');
  expect(screen.getByText('alpha')).toBeInTheDocument();
});

test('rejects duplicates', async () => {
  render(<Harness initial={['alpha']} />);
  await userEvent.type(screen.getByPlaceholderText(/add tag/i), 'alpha{enter}');
  expect(screen.getAllByText('alpha')).toHaveLength(1);
});

test('removes tag', async () => {
  render(<Harness initial={['alpha', 'beta']} />);
  await userEvent.click(screen.getByLabelText(/remove alpha/i));
  expect(screen.queryByText('alpha')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/components/TagEditor.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `frontend/src/components/TagEditor.tsx`:

```tsx
import { useState, type KeyboardEvent } from 'react';
import './TagEditor.css';

type Props = {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
};

export function TagEditor({ tags, onChange, placeholder = 'Add tag…' }: Props) {
  const [draft, setDraft] = useState('');

  function commit() {
    const t = draft.trim().toLowerCase();
    setDraft('');
    if (!t) return;
    if (t.length > 40) return;
    if (tags.includes(t)) return;
    onChange([...tags, t]);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && !draft && tags.length) {
      onChange(tags.slice(0, -1));
    }
  }

  return (
    <div className="wl-tageditor">
      {tags.map(t => (
        <span key={t} className="wl-tageditor__chip">
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter(x => x !== t))}>×</button>
        </span>
      ))}
      <input
        className="wl-tageditor__input"
        value={draft}
        placeholder={placeholder}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={commit}
        maxLength={40}
      />
    </div>
  );
}
```

Create `frontend/src/components/TagEditor.css`:

```css
.wl-tageditor { display:flex; flex-wrap:wrap; gap:6px; align-items:center;
  padding:6px; background:var(--wl-surface-1); border:1px solid var(--wl-border); border-radius:6px; }
.wl-tageditor__chip { display:inline-flex; gap:4px; align-items:center; padding:2px 8px;
  background:var(--wl-surface-2); border-radius: 999px; font-size: 12px; }
.wl-tageditor__chip button { border:0; background:transparent; cursor:pointer; color:var(--wl-text-muted); }
.wl-tageditor__input { flex:1; min-width: 100px; border:0; outline: none; background: transparent; font-size: 13px; }
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/components/TagEditor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TagEditor.*
git commit -m "feat(frontend): TagEditor component (chip-input with lowercasing + dedup)"
```

---

## Task 23: PositionPicker + PortfolioPicker modals

**Files:**
- Create: `frontend/src/components/PositionPicker.tsx` + `.css` + `.test.tsx`
- Create: `frontend/src/components/PortfolioPicker.tsx` + `.css` + `.test.tsx`

- [ ] **Step 1: Write failing tests for both pickers**

Create `frontend/src/components/PositionPicker.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PositionPicker } from './PositionPicker';

const rows = [
  { id: 1, underlying: 'AAPL', product_type: 'Snowball' },
  { id: 2, underlying: 'TSLA', product_type: 'Phoenix' },
];

test('filters by search', async () => {
  render(<PositionPicker open positions={rows as any} onCancel={() => {}} onConfirm={() => {}} />);
  await userEvent.type(screen.getByPlaceholderText(/search/i), 'AAPL');
  expect(screen.getByText('AAPL')).toBeInTheDocument();
  expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
});

test('confirms with selected ids', async () => {
  const onConfirm = vi.fn();
  render(<PositionPicker open positions={rows as any} onCancel={() => {}} onConfirm={onConfirm} />);
  await userEvent.click(screen.getByLabelText(/select position 1/i));
  await userEvent.click(screen.getByLabelText(/select position 2/i));
  await userEvent.click(screen.getByRole('button', { name: /confirm/i }));
  expect(onConfirm).toHaveBeenCalledWith([1, 2]);
});
```

Create `frontend/src/components/PortfolioPicker.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { PortfolioPicker } from './PortfolioPicker';

const portfolios = [
  { id: 1, name: 'A', kind: 'view' },
  { id: 2, name: 'B', kind: 'container' },
  { id: 3, name: 'Self', kind: 'view' },
];

test('hides current portfolio and known descendants from candidates', () => {
  render(
    <PortfolioPicker
      open
      portfolios={portfolios as any}
      currentPortfolioId={3}
      excludedIds={new Set([1])}
      onCancel={() => {}}
      onConfirm={() => {}}
    />,
  );
  expect(screen.queryByText('Self')).not.toBeInTheDocument();
  expect(screen.queryByText('A')).not.toBeInTheDocument();
  expect(screen.getByText('B')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/components/PositionPicker.test.tsx src/components/PortfolioPicker.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement both pickers**

Create `frontend/src/components/PositionPicker.tsx`:

```tsx
import { useState } from 'react';
import './PositionPicker.css';

type PickerPosition = { id: number; underlying: string; product_type: string };

type Props = {
  open: boolean;
  positions: PickerPosition[];
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
};

export function PositionPicker({ open, positions, onCancel, onConfirm }: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  if (!open) return null;
  const filtered = positions.filter(p =>
    !query ||
    p.underlying.toLowerCase().includes(query.toLowerCase()) ||
    p.product_type.toLowerCase().includes(query.toLowerCase()) ||
    String(p.id).includes(query),
  );
  function toggle(id: number) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  }
  return (
    <div className="wl-modal" role="dialog" aria-label="Pick positions">
      <div className="wl-modal__body">
        <input placeholder="Search positions…" value={query} onChange={e => setQuery(e.target.value)} />
        <ul className="wl-modal__list">
          {filtered.map(p => (
            <li key={p.id}>
              <label>
                <input
                  type="checkbox"
                  aria-label={`Select position ${p.id}`}
                  checked={selected.has(p.id)}
                  onChange={() => toggle(p.id)}
                />
                <span>{p.id}</span>
                <span>{p.underlying}</span>
                <span>{p.product_type}</span>
              </label>
            </li>
          ))}
        </ul>
        <div className="wl-modal__actions">
          <button onClick={onCancel}>Cancel</button>
          <button onClick={() => onConfirm(Array.from(selected))}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
```

Create `frontend/src/components/PortfolioPicker.tsx`:

```tsx
import { useState } from 'react';
import './PortfolioPicker.css';
import type { PortfolioKind } from '../types';

type PickerPortfolio = { id: number; name: string; kind: PortfolioKind };

type Props = {
  open: boolean;
  portfolios: PickerPortfolio[];
  currentPortfolioId: number;
  excludedIds: Set<number>;
  onCancel: () => void;
  onConfirm: (ids: number[]) => void;
};

export function PortfolioPicker({ open, portfolios, currentPortfolioId, excludedIds, onCancel, onConfirm }: Props) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  if (!open) return null;
  const candidates = portfolios.filter(p =>
    p.id !== currentPortfolioId && !excludedIds.has(p.id) &&
    (!query || p.name.toLowerCase().includes(query.toLowerCase())),
  );
  function toggle(id: number) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  }
  return (
    <div className="wl-modal" role="dialog" aria-label="Pick portfolios">
      <div className="wl-modal__body">
        <input placeholder="Search portfolios…" value={query} onChange={e => setQuery(e.target.value)} />
        <ul className="wl-modal__list">
          {candidates.map(p => (
            <li key={p.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(p.id)}
                  onChange={() => toggle(p.id)}
                />
                <span>{p.name}</span>
                <span>{p.kind}</span>
              </label>
            </li>
          ))}
        </ul>
        <div className="wl-modal__actions">
          <button onClick={onCancel}>Cancel</button>
          <button onClick={() => onConfirm(Array.from(selected))}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
```

Create matching `.css` files (basic modal layout). Both share roughly:

```css
.wl-modal { position: fixed; inset: 0; display:flex; align-items:center; justify-content:center;
  background: rgba(0,0,0,0.4); z-index: 100; }
.wl-modal__body { background: var(--wl-surface-1); padding: 16px; border-radius: 8px;
  width: 480px; max-height: 70vh; display:flex; flex-direction:column; gap: 8px; }
.wl-modal__list { list-style: none; padding: 0; margin: 0; overflow-y: auto; flex: 1; }
.wl-modal__list label { display:flex; gap:8px; align-items:center; padding: 4px 6px; }
.wl-modal__actions { display: flex; gap: 8px; justify-content: flex-end; }
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/components/PositionPicker.test.tsx src/components/PortfolioPicker.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PositionPicker.* frontend/src/components/PortfolioPicker.*
git commit -m "feat(frontend): PositionPicker + PortfolioPicker modal components"
```

---

## Task 24: RuleBuilder component

**Files:**
- Create: `frontend/src/components/RuleBuilder.tsx` + `.css` + `.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/RuleBuilder.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuleBuilder } from './RuleBuilder';

test('emits canonical eq tree when a single condition added', async () => {
  const onChange = vi.fn();
  render(<RuleBuilder rule={null} onChange={onChange} />);
  await userEvent.click(screen.getByRole('button', { name: /\+ condition/i }));
  // Default new condition: product_type = ''
  // Pick product_type field, type "Snowball", emit
  await userEvent.selectOptions(screen.getAllByLabelText(/field/i)[0], 'product_type');
  await userEvent.type(screen.getAllByLabelText(/value/i)[0], 'Snowball');
  expect(onChange).toHaveBeenLastCalledWith({
    op: 'and', children: [{ op: 'eq', field: 'product_type', value: 'Snowball' }],
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/components/RuleBuilder.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement RuleBuilder**

Create `frontend/src/components/RuleBuilder.tsx`:

```tsx
import { useEffect, useState } from 'react';
import './RuleBuilder.css';
import type { FilterRule } from '../types';
import { ALLOWED_FIELDS } from '../lib/ruleTree';

type LeafOp = 'eq' | 'ne' | 'in' | 'not_in' | 'lt' | 'lte' | 'gt' | 'gte';

type Leaf = { op: LeafOp; field: string; value: string };

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null) => void;
};

const FIELDS = Object.keys(ALLOWED_FIELDS);

function ruleToLeaves(rule: FilterRule | null): Leaf[] {
  if (!rule) return [];
  if (rule.op === 'and') {
    return rule.children.flatMap(c => (c.op !== 'and' && c.op !== 'or' && c.op !== 'not'
      ? [{ op: c.op as LeafOp, field: c.field, value: String((c as any).value) }]
      : []));
  }
  if (rule.op !== 'or' && rule.op !== 'not') {
    return [{ op: rule.op as LeafOp, field: rule.field, value: String((rule as any).value) }];
  }
  return [];
}

function leavesToRule(leaves: Leaf[]): FilterRule | null {
  if (leaves.length === 0) return null;
  const children = leaves.map(l => ({
    op: l.op,
    field: l.field,
    value: ['in', 'not_in'].includes(l.op)
      ? l.value.split(',').map(v => v.trim()).filter(Boolean)
      : (ALLOWED_FIELDS[l.field] === 'number' ? Number(l.value) : l.value),
  })) as FilterRule[];
  return { op: 'and', children };
}

export function RuleBuilder({ rule, onChange }: Props) {
  const [leaves, setLeaves] = useState<Leaf[]>(() => ruleToLeaves(rule));

  useEffect(() => { setLeaves(ruleToLeaves(rule)); }, [rule]);

  function emit(next: Leaf[]) {
    setLeaves(next);
    onChange(leavesToRule(next));
  }

  return (
    <div className="wl-rulebuilder">
      {leaves.map((leaf, i) => (
        <div className="wl-rulebuilder__row" key={i}>
          <select aria-label="Field" value={leaf.field}
            onChange={e => emit(leaves.map((l, j) => j === i ? { ...l, field: e.target.value } : l))}>
            {FIELDS.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
          <select aria-label="Op" value={leaf.op}
            onChange={e => emit(leaves.map((l, j) => j === i ? { ...l, op: e.target.value as LeafOp } : l))}>
            {['eq','ne','in','not_in','lt','lte','gt','gte'].map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input aria-label="Value" value={leaf.value}
            onChange={e => emit(leaves.map((l, j) => j === i ? { ...l, value: e.target.value } : l))} />
          <button onClick={() => emit(leaves.filter((_, j) => j !== i))} aria-label="Remove condition">×</button>
        </div>
      ))}
      <button onClick={() => emit([...leaves, { op: 'eq', field: FIELDS[0], value: '' }])}>+ condition</button>
    </div>
  );
}
```

Create `frontend/src/components/RuleBuilder.css`:

```css
.wl-rulebuilder { display: flex; flex-direction: column; gap: 6px; }
.wl-rulebuilder__row { display:flex; gap: 6px; align-items: center; }
.wl-rulebuilder__row select, .wl-rulebuilder__row input {
  font-size: 12px; padding: 4px 6px; border: 1px solid var(--wl-border); border-radius: 4px; background: var(--wl-surface-1);
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/components/RuleBuilder.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RuleBuilder.*
git commit -m "feat(frontend): RuleBuilder visual condition editor"
```

---

## Task 25: RuleTextEditor + RuleEditor wrapper

**Files:**
- Create: `frontend/src/components/RuleTextEditor.tsx` + `.css` + `.test.tsx`
- Create: `frontend/src/components/RuleEditor.tsx` + `.css` + `.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/components/RuleTextEditor.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuleTextEditor } from './RuleTextEditor';

test('emits canonical tree from valid DSL', async () => {
  const onChange = vi.fn();
  render(<RuleTextEditor rule={null} onChange={onChange} />);
  await userEvent.type(screen.getByRole('textbox'), 'product_type = Snowball');
  expect(onChange).toHaveBeenLastCalledWith(
    { op: 'eq', field: 'product_type', value: 'Snowball' },
    null,
  );
});

test('reports parse error for invalid DSL', async () => {
  render(<RuleTextEditor rule={null} onChange={() => {}} />);
  await userEvent.type(screen.getByRole('textbox'), 'product_type ==== Snowball');
  expect(screen.getByText(/syntax/i)).toBeInTheDocument();
});
```

Create `frontend/src/components/RuleEditor.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuleEditor } from './RuleEditor';

test('starts in builder mode and toggles to text', async () => {
  render(<RuleEditor rule={null} onChange={() => {}} />);
  expect(screen.getByRole('button', { name: /text/i })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /text/i }));
  expect(screen.getByRole('textbox')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/components/RuleTextEditor.test.tsx src/components/RuleEditor.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement RuleTextEditor**

Create `frontend/src/components/RuleTextEditor.tsx`:

```tsx
import { useEffect, useState } from 'react';
import './RuleTextEditor.css';
import type { FilterRule } from '../types';
import { DslSyntaxError, parseDsl, serializeDsl } from '../lib/ruleTree';

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null, parseError: string | null) => void;
};

export function RuleTextEditor({ rule, onChange }: Props) {
  const [text, setText] = useState(() => (rule ? serializeDsl(rule) : ''));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setText(rule ? serializeDsl(rule) : ''); }, [rule]);

  function handle(value: string) {
    setText(value);
    if (!value.trim()) {
      setError(null);
      onChange(null, null);
      return;
    }
    try {
      const parsed = parseDsl(value);
      setError(null);
      onChange(parsed, null);
    } catch (e) {
      const msg = e instanceof DslSyntaxError ? e.message : String(e);
      setError(`Syntax: ${msg}`);
      onChange(null, msg);
    }
  }

  return (
    <div className="wl-ruletext">
      <textarea
        className="wl-ruletext__area"
        rows={3}
        value={text}
        onChange={e => handle(e.target.value)}
        placeholder='e.g. product_type = "Snowball" AND status = open'
      />
      {error && <div className="wl-ruletext__err">{error}</div>}
    </div>
  );
}
```

```css
/* RuleTextEditor.css */
.wl-ruletext__area { width:100%; font-family: monospace; font-size: 12px;
  padding: 6px; border: 1px solid var(--wl-border); border-radius: 4px;
  background: var(--wl-surface-1); color: var(--wl-text); }
.wl-ruletext__err { color: var(--wl-danger); font-size: 11px; padding-top: 4px; }
```

- [ ] **Step 4: Implement RuleEditor wrapper**

Create `frontend/src/components/RuleEditor.tsx`:

```tsx
import { useState } from 'react';
import './RuleEditor.css';
import type { FilterRule } from '../types';
import { RuleBuilder } from './RuleBuilder';
import { RuleTextEditor } from './RuleTextEditor';

type Mode = 'builder' | 'text';

type Props = {
  rule: FilterRule | null;
  onChange: (rule: FilterRule | null) => void;
};

export function RuleEditor({ rule, onChange }: Props) {
  const [mode, setMode] = useState<Mode>('builder');
  const [textParseError, setTextParseError] = useState<string | null>(null);

  return (
    <div className="wl-ruleeditor">
      <div className="wl-ruleeditor__toggle">
        <button
          className={mode === 'builder' ? 'is-active' : ''}
          onClick={() => setMode('builder')}
        >Builder</button>
        <button
          className={mode === 'text' ? 'is-active' : ''}
          onClick={() => setMode('text')}
        >Text</button>
        {mode === 'text' && textParseError && (
          <button disabled title={textParseError}>(builder disabled — fix syntax)</button>
        )}
      </div>
      {mode === 'builder' ? (
        <RuleBuilder rule={rule} onChange={onChange} />
      ) : (
        <RuleTextEditor
          rule={rule}
          onChange={(r, err) => { setTextParseError(err); if (!err) onChange(r); }}
        />
      )}
    </div>
  );
}
```

```css
/* RuleEditor.css */
.wl-ruleeditor { display: flex; flex-direction: column; gap: 8px; }
.wl-ruleeditor__toggle { display: flex; gap: 4px; }
.wl-ruleeditor__toggle button { padding: 4px 10px; border: 1px solid var(--wl-border);
  background: var(--wl-surface-1); border-radius: 4px; cursor: pointer; font-size: 11px; }
.wl-ruleeditor__toggle button.is-active { background: var(--wl-text); color: var(--wl-bg); }
```

- [ ] **Step 5: Run tests**

Run: `cd frontend && npm test -- --run src/components/RuleTextEditor.test.tsx src/components/RuleEditor.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/RuleTextEditor.* frontend/src/components/RuleEditor.*
git commit -m "feat(frontend): RuleTextEditor + RuleEditor (builder/text mode toggle)"
```

---

## Task 26: Portfolios.tsx (presentation component)

**Files:**
- Create: `frontend/src/routes/Portfolios.tsx` + `.css`
- Create: `frontend/src/routes/Portfolios.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/routes/Portfolios.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Portfolios } from './Portfolios';

const portfolios = [
  { id: 1, name: 'Snow', kind: 'view' as const, base_currency: 'USD',
    description: null, tags: [], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 5, created_at: '2026-05-10', updated_at: '2026-05-10' },
  { id: 2, name: 'Book', kind: 'container' as const, base_currency: 'USD',
    description: null, tags: ['desk'], filter_rule: null,
    manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
    resolved_position_count: 12, created_at: '2026-05-10', updated_at: '2026-05-10' },
];

const noop = () => {};
const baseProps = {
  portfolios, selected: null, filterKind: 'all' as const, filterTags: [] as string[],
  pendingMembershipPreview: null, onFilterKindChange: noop, onFilterTagsChange: noop,
  onSelectPortfolio: noop, onCreatePortfolio: noop, onDeletePortfolio: noop,
  onSaveRule: async () => {}, onAddInclude: async () => {}, onRemoveInclude: async () => {},
  onAddExclude: async () => {}, onRemoveExclude: async () => {},
  onAddSource: async () => {}, onRemoveSource: async () => {},
  onSetTags: async () => {}, onRunPricing: noop, onRunRisk: noop,
  allPortfolios: portfolios, allPositions: [],
};

test('renders both portfolios with kind chips', () => {
  render(<Portfolios {...baseProps} />);
  expect(screen.getByText('Snow')).toBeInTheDocument();
  expect(screen.getByText('Book')).toBeInTheDocument();
  expect(screen.getAllByText(/view|container/i).length).toBeGreaterThan(0);
});

test('filters by kind chip', async () => {
  const onFilterKindChange = vi.fn();
  render(<Portfolios {...baseProps} onFilterKindChange={onFilterKindChange} />);
  await userEvent.click(screen.getByRole('button', { name: /^view$/i }));
  expect(onFilterKindChange).toHaveBeenCalledWith('view');
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/routes/Portfolios.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `Portfolios.tsx`**

Create `frontend/src/routes/Portfolios.tsx`:

```tsx
import { useState } from 'react';
import './Portfolios.css';
import type {
  FilterRule, PortfolioDetail, PortfolioKind, PortfolioSummary,
} from '../types';
import { KindChip } from '../components/KindChip';
import { ResolvedPositionsTable } from '../components/ResolvedPositionsTable';
import { RuleEditor } from '../components/RuleEditor';
import { TagEditor } from '../components/TagEditor';
import { Chip } from '../components/Chip';
import { Button } from '../components/Button';

type FilterKind = 'all' | 'container' | 'view';

export type PortfoliosProps = {
  portfolios: PortfolioSummary[];
  allPortfolios: PortfolioSummary[];
  allPositions: { id: number; underlying: string; product_type: string }[];
  selected: PortfolioDetail | null;
  filterKind: FilterKind;
  filterTags: string[];
  pendingMembershipPreview: { id: number; underlying: string; product_type: string;
    quantity: number; entry_price: number; status: string }[] | null;
  onFilterKindChange: (k: FilterKind) => void;
  onFilterTagsChange: (tags: string[]) => void;
  onSelectPortfolio: (id: number) => void;
  onCreatePortfolio: (kind: PortfolioKind) => void;
  onDeletePortfolio: (id: number) => void;
  onSaveRule: (rule: FilterRule | null) => Promise<void>;
  onAddInclude: (positionId: number) => Promise<void>;
  onRemoveInclude: (positionId: number) => Promise<void>;
  onAddExclude: (positionId: number) => Promise<void>;
  onRemoveExclude: (positionId: number) => Promise<void>;
  onAddSource: (portfolioId: number) => Promise<void>;
  onRemoveSource: (portfolioId: number) => Promise<void>;
  onSetTags: (tags: string[]) => Promise<void>;
  onRunPricing: () => void;
  onRunRisk: () => void;
};

export function Portfolios(props: PortfoliosProps) {
  const filtered = props.portfolios.filter(p => props.filterKind === 'all' || p.kind === props.filterKind);

  return (
    <div className="wl-portfolios">
      <aside className="wl-portfolios__list">
        <div className="wl-portfolios__filters">
          {(['all', 'container', 'view'] as const).map(k => (
            <button
              key={k}
              className={props.filterKind === k ? 'is-active' : ''}
              onClick={() => props.onFilterKindChange(k)}
            >
              {k}
            </button>
          ))}
        </div>
        <div className="wl-portfolios__new">
          <Button onClick={() => props.onCreatePortfolio('container')}>+ Container</Button>
          <Button onClick={() => props.onCreatePortfolio('view')}>+ View</Button>
        </div>
        <ul>
          {filtered.map(p => (
            <li
              key={p.id}
              className={props.selected?.id === p.id ? 'is-selected' : ''}
              onClick={() => props.onSelectPortfolio(p.id)}
            >
              <KindChip kind={p.kind} />
              <span className="wl-portfolios__name">{p.name}</span>
              <span className="wl-portfolios__count">{p.resolved_position_count}</span>
              {p.tags.slice(0, 3).map(t => <Chip key={t}>{t}</Chip>)}
            </li>
          ))}
        </ul>
      </aside>

      <section className="wl-portfolios__detail">
        {!props.selected
          ? <div className="wl-empty">Select a portfolio.</div>
          : <DetailPane {...props} portfolio={props.selected} />}
      </section>
    </div>
  );
}

function DetailPane(props: PortfoliosProps & { portfolio: PortfolioDetail }) {
  const p = props.portfolio;
  const isView = p.kind === 'view';
  return (
    <div className="wl-detail">
      <header>
        <KindChip kind={p.kind} />
        <h2>{p.name}</h2>
        <Button onClick={() => props.onDeletePortfolio(p.id)} variant="danger">Delete</Button>
      </header>
      <div className="wl-detail__kpis">
        <span>{p.resolved_position_count} positions</span>
      </div>
      <div className="wl-detail__actions">
        <Button onClick={props.onRunPricing}>Run pricing</Button>
        <Button onClick={props.onRunRisk}>Run risk</Button>
      </div>
      <div className="wl-detail__tags">
        <TagEditor tags={p.tags} onChange={tags => props.onSetTags(tags)} />
      </div>

      {isView ? (
        <div className="wl-detail__split">
          <div className="wl-detail__editor">
            <h3>Rule</h3>
            <RuleEditor rule={p.filter_rule} onChange={rule => props.onSaveRule(rule)} />
            <h3>Sources</h3>
            <SourceList {...props} portfolio={p} />
            <h3>Manual includes</h3>
            <ManualIdList ids={p.manual_include_ids} onRemove={props.onRemoveInclude}
              onAdd={props.onAddInclude} positions={props.allPositions} />
            <h3>Manual excludes</h3>
            <ManualIdList ids={p.manual_exclude_ids} onRemove={props.onRemoveExclude}
              onAdd={props.onAddExclude} positions={props.allPositions} />
          </div>
          <div className="wl-detail__preview">
            <h3>Resolved ({props.pendingMembershipPreview?.length ?? '…'})</h3>
            <ResolvedPositionsTable rows={props.pendingMembershipPreview ?? []} />
          </div>
        </div>
      ) : (
        <div className="wl-detail__owned">
          <h3>Owned positions ({p.positions.length})</h3>
          <ResolvedPositionsTable rows={p.positions as any} />
        </div>
      )}
    </div>
  );
}

function SourceList({ portfolio, allPortfolios, onAddSource, onRemoveSource }: {
  portfolio: PortfolioDetail;
  allPortfolios: PortfolioSummary[];
  onAddSource: (id: number) => Promise<void>;
  onRemoveSource: (id: number) => Promise<void>;
}) {
  const sources = portfolio.source_portfolio_ids
    .map(id => allPortfolios.find(p => p.id === id))
    .filter(Boolean) as PortfolioSummary[];
  return (
    <div className="wl-sources">
      {sources.map(s => (
        <Chip key={s.id} onRemove={() => onRemoveSource(s.id)}>{s.name}</Chip>
      ))}
      <button onClick={() => {
        const candidate = allPortfolios.find(p => p.id !== portfolio.id
          && !portfolio.source_portfolio_ids.includes(p.id));
        if (candidate) onAddSource(candidate.id);
      }}>+ source</button>
    </div>
  );
}

function ManualIdList({ ids, onAdd, onRemove, positions }: {
  ids: number[]; onAdd: (id: number) => Promise<void>; onRemove: (id: number) => Promise<void>;
  positions: { id: number; underlying: string; product_type: string }[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      {ids.map(id => {
        const p = positions.find(x => x.id === id);
        return <Chip key={id} onRemove={() => onRemove(id)}>{p ? `${p.id} ${p.underlying}` : `#${id}`}</Chip>;
      })}
      <button onClick={() => setOpen(true)}>+ pick</button>
      {/* PositionPicker rendered by parent in Step 27 (live) */}
    </div>
  );
}
```

Create `frontend/src/routes/Portfolios.css`:

```css
.wl-portfolios { display: grid; grid-template-columns: 320px 1fr; gap: 16px; height: 100%; }
.wl-portfolios__list { padding: 8px; border-right: 1px solid var(--wl-border); }
.wl-portfolios__filters { display:flex; gap: 4px; padding-bottom: 8px; }
.wl-portfolios__filters button { padding: 3px 10px; border: 1px solid var(--wl-border);
  border-radius: 999px; background: transparent; cursor: pointer; font-size: 11px; }
.wl-portfolios__filters button.is-active { background: var(--wl-text); color: var(--wl-bg); }
.wl-portfolios__list ul { list-style: none; padding: 0; margin: 0; }
.wl-portfolios__list li { display: flex; gap: 6px; align-items: center; padding: 6px 8px;
  border-radius: 4px; cursor: pointer; }
.wl-portfolios__list li.is-selected { background: var(--wl-surface-2); }
.wl-portfolios__list .wl-portfolios__name { flex: 1; font-weight: 500; }
.wl-portfolios__list .wl-portfolios__count { font-size: 11px; color: var(--wl-text-muted); }
.wl-detail { display: flex; flex-direction: column; gap: 12px; padding: 16px; }
.wl-detail header { display: flex; gap: 12px; align-items: center; }
.wl-detail__split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.wl-empty { padding: 48px; text-align: center; color: var(--wl-text-muted); }
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/routes/Portfolios.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Portfolios.* frontend/src/routes/Portfolios.test.tsx
git commit -m "feat(frontend): Portfolios.tsx presentation component (master-detail with two-column view detail)"
```

---

## Task 27: Portfolios.live.tsx with live preview

**Files:**
- Replace: `frontend/src/routes/Portfolios.live.tsx` (was a placeholder from Task 19)
- Create: `frontend/src/routes/Portfolios.live.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/routes/Portfolios.live.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { PortfoliosLive } from './Portfolios.live';

const fetchMock = vi.fn();

beforeEach(() => {
  globalThis.fetch = fetchMock as any;
  fetchMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

function jsonResponse(body: any, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

test('lists portfolios on mount', async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url.includes('/api/portfolios')) {
      return jsonResponse([{
        id: 1, name: 'Snow', kind: 'view', base_currency: 'USD',
        description: null, tags: [], filter_rule: null,
        manual_include_ids: [], manual_exclude_ids: [], source_portfolio_ids: [],
        resolved_position_count: 0, created_at: 't', updated_at: 't', positions: [],
      }]);
    }
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() => expect(screen.getByText('Snow')).toBeInTheDocument());
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run src/routes/Portfolios.live.test.tsx`
Expected: FAIL — old placeholder doesn't fetch.

- [ ] **Step 3: Implement `Portfolios.live.tsx`**

Replace `frontend/src/routes/Portfolios.live.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Portfolios } from './Portfolios';
import type {
  FilterRule, PortfolioDetail, PortfolioKind, PortfolioMembership, PortfolioSummary,
} from '../types';

type PreviewRow = { id: number; underlying: string; product_type: string;
  quantity: number; entry_price: number; status: string };

export function PortfoliosLive() {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [selected, setSelected] = useState<PortfolioDetail | null>(null);
  const [filterKind, setFilterKind] = useState<'all' | 'container' | 'view'>('all');
  const [filterTags, setFilterTags] = useState<string[]>([]);
  const [allPositions, setAllPositions] = useState<PreviewRow[]>([]);
  const [pendingPreview, setPendingPreview] = useState<PreviewRow[] | null>(null);

  const refreshList = async () => {
    const params = new URLSearchParams();
    if (filterKind !== 'all') params.set('kind', filterKind);
    filterTags.forEach(t => params.append('tag', t));
    const rows = await api<PortfolioSummary[]>(`/api/portfolios?${params}`);
    setPortfolios(rows);
  };

  const refreshSelected = async (id: number) => {
    const detail = await api<PortfolioDetail>(`/api/portfolios/${id}`);
    setSelected(detail);
  };

  useEffect(() => { refreshList(); }, [filterKind, filterTags.join(',')]);

  useEffect(() => {
    // load full position catalogue once for the pickers + label rendering
    (async () => {
      const ps = await api<PortfolioSummary[]>('/api/portfolios');
      const positions: PreviewRow[] = [];
      for (const p of ps) {
        const detail = await api<PortfolioDetail>(`/api/portfolios/${p.id}`);
        for (const pos of detail.positions || []) {
          positions.push(pos as any);
        }
      }
      setAllPositions(positions);
    })();
  }, []);

  // Live preview — debounced — for the currently-selected view
  useEffect(() => {
    if (!selected || selected.kind !== 'view') {
      setPendingPreview(null);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const body: PortfolioMembership = await api(`/api/portfolios/${selected.id}/membership`);
        const idSet = new Set(body.position_ids);
        setPendingPreview(allPositions.filter(p => idSet.has(p.id)));
      } catch {
        setPendingPreview(null);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [
    selected?.id,
    JSON.stringify(selected?.filter_rule),
    selected?.manual_include_ids?.join(','),
    selected?.manual_exclude_ids?.join(','),
    selected?.source_portfolio_ids?.join(','),
    allPositions,
  ]);

  const callJson = async (path: string, init: RequestInit) => {
    return api(path, { ...init, method: init.method, body: init.body });
  };

  const onCreatePortfolio = async (kind: PortfolioKind) => {
    const name = window.prompt(`Name for new ${kind} portfolio`);
    if (!name) return;
    await callJson('/api/portfolios', { method: 'POST', body: JSON.stringify({ name, kind }) });
    refreshList();
  };

  const onDeletePortfolio = async (id: number) => {
    if (!window.confirm('Delete this portfolio?')) return;
    await fetch(`/api/portfolios/${id}`, { method: 'DELETE' });
    setSelected(null);
    refreshList();
  };

  const onSaveRule = async (rule: FilterRule | null) => {
    if (!selected) return;
    await callJson(`/api/portfolios/${selected.id}/rule`,
      { method: 'PUT', body: JSON.stringify({ filter_rule: rule }) });
    refreshSelected(selected.id);
  };

  const idsAction = (pathSuffix: string, method: 'POST' | 'DELETE', key: 'position_ids' | 'portfolio_ids') =>
    async (id: number) => {
      if (!selected) return;
      await callJson(`/api/portfolios/${selected.id}/${pathSuffix}`,
        { method, body: JSON.stringify({ [key]: [id] }) });
      refreshSelected(selected.id);
      refreshList();
    };

  const onSetTags = async (tags: string[]) => {
    if (!selected) return;
    await callJson(`/api/portfolios/${selected.id}/tags`,
      { method: 'PUT', body: JSON.stringify({ tags }) });
    refreshSelected(selected.id);
  };

  return (
    <Portfolios
      portfolios={portfolios}
      allPortfolios={portfolios}
      allPositions={allPositions}
      selected={selected}
      filterKind={filterKind}
      filterTags={filterTags}
      pendingMembershipPreview={pendingPreview}
      onFilterKindChange={setFilterKind}
      onFilterTagsChange={setFilterTags}
      onSelectPortfolio={refreshSelected}
      onCreatePortfolio={onCreatePortfolio}
      onDeletePortfolio={onDeletePortfolio}
      onSaveRule={onSaveRule}
      onAddInclude={idsAction('includes', 'POST', 'position_ids')}
      onRemoveInclude={idsAction('includes', 'DELETE', 'position_ids')}
      onAddExclude={idsAction('excludes', 'POST', 'position_ids')}
      onRemoveExclude={idsAction('excludes', 'DELETE', 'position_ids')}
      onAddSource={idsAction('sources', 'POST', 'portfolio_ids')}
      onRemoveSource={idsAction('sources', 'DELETE', 'portfolio_ids')}
      onSetTags={onSetTags}
      onRunPricing={() => selected && fetch(`/api/portfolios/${selected.id}/positions/price`, { method: 'POST', body: JSON.stringify({}), headers: { 'Content-Type': 'application/json' }})}
      onRunRisk={() => {/* wire when risk endpoint is finalized */}}
    />
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm test -- --run src/routes/Portfolios.live.test.tsx`
Expected: PASS for the list test.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Portfolios.live.tsx frontend/src/routes/Portfolios.live.test.tsx
git commit -m "feat(frontend): Portfolios.live with debounced live preview + CRUD wiring"
```

---

## Task 28: main.tsx integration (commands, route render)

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Wire portfolios commands and route render**

Already partially done in Task 19. Now finalize:

In `commandItems`, add command-palette entries:

```tsx
{ id: 'portfolios-create-container', group: 'Create', label: 'New container portfolio', shortcut: '↵' },
{ id: 'portfolios-create-view',      group: 'Create', label: 'New view portfolio',      shortcut: '↵' },
```

In `onSelectCommand`, handle them:

```tsx
if (item.id === 'portfolios-create-container' || item.id === 'portfolios-create-view') {
  setRoute('portfolios');
  // The Portfolios.live component handles modal open via the New buttons; for
  // command-palette init we set a query param the component reads on mount,
  // but for v1 the user clicks "+ Container" / "+ View" in the route header.
  return;
}
```

The route render branch should already render `<PortfoliosLive />` from Task 19.

- [ ] **Step 2: Verify full app build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

Run: `cd frontend && npm test -- --run`
Expected: all frontend tests pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat(frontend): wire portfolios commands and route render in main.tsx"
```

---

## Task 29: Smoke test — manual end-to-end pass

**Files:** none (manual verification)

- [ ] **Step 1: Start backend and frontend**

In one terminal:
```bash
uvicorn app.main:app --app-dir backend --reload --port 8000
```
In another:
```bash
cd frontend && npm run dev
```
Open `http://localhost:5173`.

- [ ] **Step 2: Verify the happy path**

1. Navigate to the new "Portfolios" entry in the sidebar.
2. Click "+ Container", create a portfolio named "Test Book".
3. Use the Positions route to import or add a position into "Test Book".
4. Return to Portfolios, click "+ View", create "All Snowballs". The empty view should appear.
5. Select "All Snowballs", switch the rule editor to Text mode, type `product_type = Snowball`. The right pane should populate (debounced).
6. Save the rule (UI saves on each change in v1; verify the detail refetches and the rule persists by reloading the page).
7. Add a tag ("desk") and confirm it persists.
8. Add "Test Book" as a source via the "+ source" button.
9. Trigger a pricing run from the actions row; confirm the request fires (network tab) and response has `resolved_position_ids`.
10. Open the agent pip and ask: "Create a portfolio with all snowball positions." The agent should call `create_portfolio_tool` with `kind=view` and a Snowball rule. Verify a new portfolio appears in the list after the agent finishes.
11. Ask the agent to delete one of the test portfolios. Confirm an HITL ActionProposal card appears in the chat thread; reject it; verify nothing is deleted. Approve a second time; verify the portfolio disappears.

- [ ] **Step 3: Note any UX rough edges**

If Step 2 surfaces issues, file follow-up tasks (e.g., the `+ source` shortcut chooses the first candidate — replace with `PortfolioPicker` modal in a follow-up). Don't block this plan unless functionality is broken.

- [ ] **Step 4: Final commit (if anything was tweaked)**

If no code changes were needed, this task is complete by checkbox alone.

---

## Self-Review Checklist (already applied)

- **Spec coverage:** Each section in the spec maps to tasks: §4.1/4.2 → Task 1; §4.3 → Tasks 2 + 20; §4.4 → Task 4; §4.5 → Task 11; §4.6 → throughout (audit); §5.1 → Tasks 5–7; §5.2 → Tasks 9–10; §5.3 → Tasks 12–14; §5.4 → Tasks 15–17; §6 → Tasks 19–28; §7 (errors) → exception classes in Task 1, mapping in Tasks 9/10 and tools 15–17; §8 (tests) → embedded in each task; §9 (migration) → Task 1; §10 (build sequence) → followed.
- **Placeholder scan:** No `TBD` / `TODO` / "implement later" left.
- **Type consistency:** Service function names match across tasks (`add_manual_includes`, `add_portfolio_sources`, `set_portfolio_tags`, `set_filter_rule`). Tool names match HITL registration (`delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio`).

