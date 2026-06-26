# Desk Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface workflows as a frontend-managed module — users author reproducible Python-script workflows (LLM-assisted) and run them auto-pilot in any Agent Thread via a slash command — with `risk-manager-control-day` seeded and runnable.

**Architecture:** A new DB-backed `DeskWorkflow` (Python script = source of truth, self-describing via a `meta` literal extracted into cache columns). A restricted in-process runner execs the script with injected `step()`/`log()` helpers that each drive one real `stream_and_persist` turn, settle queued tasks (arena's util), and forward SSE to the browser via an `asyncio.Queue`. A bespoke Workflow Builder UI drafts scripts through the desk agent (`build-workflow` skill + `save_desk_workflow` tool). A composer slash-picker launches runs.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, Pydantic v2, LangGraph desk orchestrator, React + TypeScript + Vite + vitest, pytest.

## Global Constraints

- Backend models use SQLAlchemy 2.0 style: `Mapped[T]` + `mapped_column(...)`, `Base` from `app.database`, timestamps `default=utcnow` / `onupdate=utcnow` (helper `utcnow` in `app/models.py`). Copy verbatim.
- Alembic migration: `down_revision = "0034_arena_match_score_breakdown"`; new revision id `0035_desk_workflows`. Migrations use **migration-local Core tables / raw SQL**, never ORM models/services.
- Pydantic v2: response models use `model_config = {"from_attributes": True}`.
- SSE framing helper: `_sse(event, data)` → `f"event: {event}\ndata: {json}\n\n"` (in `app/services/agents.py`). Reuse exactly.
- Execution mode is resolved by `resolve_execution_mode(mode, yolo_mode) -> (mode, clear_hitl, allow_reply_options)`. Valid modes: `interactive|auto|yolo`.
- `stream_and_persist` is keyword-only and accepts `mode: str | None`; pass `mode=` (not the deprecated `yolo_mode`).
- A new agent tool must be added to BOTH `QUANT_AGENT_TOOLS` (`app/tools/__init__.py`) and the `DEEP_AGENT_TOOL_NAMES` frozenset (`app/services/agents.py`).
- A new workflow SKILL.md must lint clean (`skill_lint.py`): all required frontmatter keys, `workflow_type ∈ {diagnostic,action,read,compound}`, body ≤ 500 tokens, a `## Example` section. Adding one updates SIX coupled test files (see Task 11).
- Restricted exec is a footgun-reducer, not a security boundary (single-user MVP). The AST guard bans `import`, dunder attribute access; only safe builtins + `step`/`log`/`meta` are exposed.
- Reserved composer commands (currently `goal`, owned by the goal-mode spec) take precedence over the workflow picker and are rejected as workflow slugs. Shared constant: backend `RESERVED_WORKFLOW_SLUGS`, frontend `RESERVED_COMPOSER_COMMANDS`.
- Frontend: API calls via `api<T>(path, init)` from `src/api/client.ts`. New route added to the `Route` union (`types.ts`), `ROUTE_PATHS` (`lib/routing.ts`), `navItems` + render switch + `commandItems` (`main.tsx`).
- Run tests in this worktree. Backend: `cd backend && python -m pytest <path>`. Frontend: `cd frontend && npx vitest run <path>`.

---

## Phase A — Backend data model + CRUD

### Task 1: `DeskWorkflow` model + migration 0035 (+ seed flagship)

**Files:**
- Modify: `backend/app/models.py` (add `DeskWorkflow` class near other models)
- Create: `backend/alembic/versions/0035_desk_workflows.py`
- Test: `backend/tests/test_desk_workflows_model.py`

**Interfaces:**
- Produces: `DeskWorkflow` ORM model with columns `id, slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at`. Table `desk_workflows`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_desk_workflows_model.py
from app import database
from app.models import DeskWorkflow


def test_desk_workflow_roundtrip(tmp_path, monkeypatch):
    database.init_db()
    with database.SessionLocal() as session:
        wf = DeskWorkflow(
            slug="t-wf",
            title="T WF",
            persona="risk_manager",
            description="desc",
            scope="local",
            default_mode="auto",
            script="meta = {}\n",
            source="user",
        )
        session.add(wf)
        session.commit()
        got = session.query(DeskWorkflow).filter_by(slug="t-wf").one()
        assert got.title == "T WF"
        assert got.scope == "local"
        assert got.created_at is not None and got.updated_at is not None


def test_seed_flagship_present():
    database.init_db()
    with database.SessionLocal() as session:
        wf = session.query(DeskWorkflow).filter_by(slug="risk-manager-control-day").one()
        assert wf.source == "seed"
        assert wf.persona == "risk_manager"
        assert wf.script.count("await step(") == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflows_model.py -v`
Expected: FAIL (`ImportError: cannot import name 'DeskWorkflow'`)

- [ ] **Step 3: Add the model**

In `backend/app/models.py`, after an existing model class (e.g. after `AgentThread`), add:

```python
class DeskWorkflow(Base):
    __tablename__ = "desk_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    persona: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16), default="local")
    default_mode: Mapped[str] = mapped_column(String(16), default="auto")
    script: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
```

- [ ] **Step 4: Write the migration with seed**

Create `backend/alembic/versions/0035_desk_workflows.py`:

```python
"""desk workflows table + seed flagship

Revision ID: 0035_desk_workflows
Revises: 0034_arena_match_score_breakdown
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0035_desk_workflows"
down_revision = "0034_arena_match_score_breakdown"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


FLAGSHIP_SCRIPT = '''meta = {
    "name": "risk-manager-control-day",
    "title": "Risk Manager Control Day",
    "persona": "risk_manager",
    "mode": "yolo",
    "scope": "shared",
    "description": "Full desk-control loop: stale-check, refresh, hotspot, Greeks landscape, stress test, backtest, governance report.",
}

await step("What does the latest risk say for the control portfolio?")
await step("Run a fresh risk calculation for the control portfolio using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step("Run a Greeks landscape across spot shifts for the control portfolio.")
await step("Stress-test the control portfolio using the market-crash scenario set with the Control Profile.")
await step("Run a historical backtest of the delta-hedge strategy from 2026-03-24 to 2026-06-24.")
await step("Generate a governance risk report for today's control session.")
'''


def upgrade() -> None:
    if not _has_table("desk_workflows"):
        op.create_table(
            "desk_workflows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("persona", sa.String(length=40), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="local"),
            sa.Column("default_mode", sa.String(length=16), nullable=False, server_default="auto"),
            sa.Column("script", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_desk_workflows_slug", "desk_workflows", ["slug"], unique=True)

    bind = op.get_bind()
    exists = bind.execute(
        text("SELECT id FROM desk_workflows WHERE slug = 'risk-manager-control-day' LIMIT 1")
    ).first()
    if exists is None:
        bind.execute(
            text(
                "INSERT INTO desk_workflows "
                "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
                "VALUES (:slug, :title, :persona, :description, :scope, :default_mode, :script, 'seed', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "slug": "risk-manager-control-day",
                "title": "Risk Manager Control Day",
                "persona": "risk_manager",
                "description": "Full desk-control loop: stale-check, refresh, hotspot, Greeks landscape, stress test, backtest, governance report.",
                "scope": "shared",
                "default_mode": "yolo",
                "script": FLAGSHIP_SCRIPT,
            },
        )


def downgrade() -> None:
    if _has_table("desk_workflows"):
        op.drop_table("desk_workflows")
```

> NOTE: `database.init_db()` runs an incremental schema repair from migrations; the seed test relies on the migration's seed running. If `init_db()` does not run migrations in tests, add the same seed insert into the app's boot schema-repair path (mirror how `0028` engine-config default is seeded). Confirm by running Step 5; if the flagship row is absent, replicate the seed in the boot repair seam used by other seeds.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_desk_workflows_model.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0035_desk_workflows.py backend/tests/test_desk_workflows_model.py
git commit -m "feat(desk-workflows): DeskWorkflow model + migration 0035 + seed flagship"
```

---

### Task 2: Script `meta` extraction + AST safety guard

**Files:**
- Create: `backend/app/services/desk_workflows_script.py`
- Test: `backend/tests/test_desk_workflows_script.py`

**Interfaces:**
- Produces:
  - `extract_meta(script: str) -> dict` — literal-eval the top-level `meta = {...}`; raises `WorkflowScriptError` if missing/non-literal.
  - `guard_script(script: str) -> None` — AST walk; raises `WorkflowScriptError` on `import`, `from … import`, or dunder attribute access.
  - `validate_script(script: str, *, slug: str) -> dict` — runs guard + extract, validates required meta keys + enums + `meta["name"] == slug`; returns the normalized meta dict.
  - `class WorkflowScriptError(ValueError)`.
  - Constants: `VALID_PERSONAS = {"trader","risk_manager","sales","quant"}`, `VALID_MODES = {"auto","yolo"}`, `VALID_SCOPES = {"local","shared"}`, `RESERVED_WORKFLOW_SLUGS = {"goal"}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_desk_workflows_script.py
import pytest
from app.services.desk_workflows_script import (
    WorkflowScriptError,
    extract_meta,
    guard_script,
    validate_script,
)

GOOD = (
    'meta = {"name": "x", "title": "X", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("hello")\n'
)


def test_extract_meta_ok():
    assert extract_meta(GOOD)["name"] == "x"


def test_extract_meta_missing():
    with pytest.raises(WorkflowScriptError):
        extract_meta('await step("hi")\n')


def test_extract_meta_non_literal():
    with pytest.raises(WorkflowScriptError):
        extract_meta('meta = dict(name="x")\nawait step("hi")\n')


def test_guard_rejects_import():
    with pytest.raises(WorkflowScriptError):
        guard_script('import os\nawait step("hi")\n')


def test_guard_rejects_dunder():
    with pytest.raises(WorkflowScriptError):
        guard_script('x = ().__class__\nawait step("hi")\n')


def test_validate_slug_mismatch():
    with pytest.raises(WorkflowScriptError):
        validate_script(GOOD, slug="other")


def test_validate_ok():
    meta = validate_script(GOOD, slug="x")
    assert meta["persona"] == "trader" and meta["mode"] == "auto"


def test_validate_bad_enum():
    bad = GOOD.replace('"persona": "trader"', '"persona": "wizard"')
    with pytest.raises(WorkflowScriptError):
        validate_script(bad, slug="x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflows_script.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement**

```python
# backend/app/services/desk_workflows_script.py
"""Validation + safe metadata extraction for DeskWorkflow Python scripts."""
from __future__ import annotations

import ast

VALID_PERSONAS = {"trader", "risk_manager", "sales", "quant"}
VALID_MODES = {"auto", "yolo"}
VALID_SCOPES = {"local", "shared"}
RESERVED_WORKFLOW_SLUGS = {"goal"}
_REQUIRED_META = {"name", "title", "persona", "mode", "scope"}


class WorkflowScriptError(ValueError):
    """Raised when a workflow script is malformed or unsafe."""


def extract_meta(script: str) -> dict:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise WorkflowScriptError(f"script does not parse: {exc}") from exc
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "meta"
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError) as exc:
                raise WorkflowScriptError(
                    "meta must be a pure dict literal"
                ) from exc
            if not isinstance(value, dict):
                raise WorkflowScriptError("meta must be a dict literal")
            return value
    raise WorkflowScriptError("script must define a top-level `meta = {...}` literal")


def guard_script(script: str) -> None:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise WorkflowScriptError(f"script does not parse: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise WorkflowScriptError("import statements are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise WorkflowScriptError(f"access to dunder attribute {node.attr!r} is not allowed")


def validate_script(script: str, *, slug: str) -> dict:
    guard_script(script)
    meta = extract_meta(script)
    missing = _REQUIRED_META - set(meta)
    if missing:
        raise WorkflowScriptError(f"meta missing keys: {sorted(missing)}")
    if meta["name"] != slug:
        raise WorkflowScriptError(f"meta['name'] ({meta['name']!r}) must equal slug ({slug!r})")
    if meta["persona"] not in VALID_PERSONAS:
        raise WorkflowScriptError(f"invalid persona {meta['persona']!r}")
    if meta["mode"] not in VALID_MODES:
        raise WorkflowScriptError(f"invalid mode {meta['mode']!r}")
    if meta["scope"] not in VALID_SCOPES:
        raise WorkflowScriptError(f"invalid scope {meta['scope']!r}")
    if slug in RESERVED_WORKFLOW_SLUGS:
        raise WorkflowScriptError(f"slug {slug!r} is reserved")
    return meta
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_desk_workflows_script.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/desk_workflows_script.py backend/tests/test_desk_workflows_script.py
git commit -m "feat(desk-workflows): meta extraction + AST safety guard"
```

---

### Task 3: CRUD service + Pydantic schemas

**Files:**
- Create: `backend/app/services/desk_workflows.py`
- Modify: `backend/app/schemas.py` (add `DeskWorkflowOut`, `DeskWorkflowSummaryOut`, `DeskWorkflowSave`)
- Test: `backend/tests/test_desk_workflows_service.py`

**Interfaces:**
- Consumes: `validate_script` (Task 2), `DeskWorkflow` (Task 1).
- Produces:
  - `upsert_desk_workflow(session, *, slug, script, source="user") -> DeskWorkflow` — validates, writes columns from meta + script.
  - `list_desk_workflows(session) -> list[DeskWorkflow]`
  - `get_desk_workflow(session, slug) -> DeskWorkflow | None`
  - `delete_desk_workflow(session, slug) -> None` — raises `WorkflowScriptError` if `source == "seed"`.
  - Schemas `DeskWorkflowOut` (full incl. `script`), `DeskWorkflowSummaryOut` (no script), `DeskWorkflowSave` (`{script: str}`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_desk_workflows_service.py
import pytest
from app import database
from app.services.desk_workflows import (
    delete_desk_workflow,
    get_desk_workflow,
    list_desk_workflows,
    upsert_desk_workflow,
)
from app.services.desk_workflows_script import WorkflowScriptError

SCRIPT = (
    'meta = {"name": "wf-a", "title": "WF A", "persona": "trader", '
    '"mode": "auto", "scope": "local", "description": "d"}\n'
    'await step("one")\n'
)


def _session():
    database.init_db()
    return database.SessionLocal()


def test_upsert_creates_then_updates():
    with _session() as s:
        wf = upsert_desk_workflow(s, slug="wf-a", script=SCRIPT)
        s.commit()
        assert wf.title == "WF A" and wf.persona == "trader" and wf.description == "d"
        updated = SCRIPT.replace('"title": "WF A"', '"title": "WF A2"')
        wf2 = upsert_desk_workflow(s, slug="wf-a", script=updated)
        s.commit()
        assert wf2.id == wf.id and wf2.title == "WF A2"


def test_upsert_rejects_bad_script():
    with _session() as s:
        with pytest.raises(WorkflowScriptError):
            upsert_desk_workflow(s, slug="wf-a", script='await step("x")\n')


def test_delete_blocks_seed():
    with _session() as s:
        with pytest.raises(WorkflowScriptError):
            delete_desk_workflow(s, "risk-manager-control-day")


def test_list_and_get():
    with _session() as s:
        upsert_desk_workflow(s, slug="wf-a", script=SCRIPT)
        s.commit()
        assert any(w.slug == "wf-a" for w in list_desk_workflows(s))
        assert get_desk_workflow(s, "wf-a") is not None
        assert get_desk_workflow(s, "nope") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflows_service.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement service**

```python
# backend/app/services/desk_workflows.py
"""CRUD for DeskWorkflow rows (Python-script workflows)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import DeskWorkflow
from .desk_workflows_script import WorkflowScriptError, validate_script


def upsert_desk_workflow(
    session: Session, *, slug: str, script: str, source: str = "user"
) -> DeskWorkflow:
    meta = validate_script(script, slug=slug)
    wf = session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()
    if wf is None:
        wf = DeskWorkflow(slug=slug, source=source)
        session.add(wf)
    wf.title = meta["title"]
    wf.persona = meta["persona"]
    wf.description = meta.get("description", "")
    wf.scope = meta["scope"]
    wf.default_mode = meta["mode"]
    wf.script = script
    session.flush()
    return wf


def list_desk_workflows(session: Session) -> list[DeskWorkflow]:
    return session.query(DeskWorkflow).order_by(DeskWorkflow.slug).all()


def get_desk_workflow(session: Session, slug: str) -> DeskWorkflow | None:
    return session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()


def delete_desk_workflow(session: Session, slug: str) -> None:
    wf = session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()
    if wf is None:
        return
    if wf.source == "seed":
        raise WorkflowScriptError(f"workflow {slug!r} is seeded and cannot be deleted")
    session.delete(wf)
    session.flush()
```

- [ ] **Step 4: Add schemas**

In `backend/app/schemas.py`, append:

```python
class DeskWorkflowSave(BaseModel):
    script: str


class DeskWorkflowSummaryOut(BaseModel):
    slug: str
    title: str
    persona: str
    description: str
    scope: str
    default_mode: str
    source: str

    model_config = {"from_attributes": True}


class DeskWorkflowOut(DeskWorkflowSummaryOut):
    script: str

    model_config = {"from_attributes": True}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_desk_workflows_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/desk_workflows.py backend/app/schemas.py backend/tests/test_desk_workflows_service.py
git commit -m "feat(desk-workflows): CRUD service + schemas"
```

---

### Task 4: CRUD router + mount

**Files:**
- Create: `backend/app/routers/workflows.py`
- Modify: `backend/app/main.py` (add `app.include_router(build_desk_workflows_router())` next to the other `include_router` calls, ~line 4137-4151; add the import)
- Test: `backend/tests/test_desk_workflows_api.py`

**Interfaces:**
- Consumes: service functions (Task 3), schemas (Task 3).
- Produces: `build_desk_workflows_router() -> APIRouter` with prefix `/api/workflows`. Routes: `GET ""`, `GET "/{slug}"`, `POST ""`, `PUT "/{slug}"`, `DELETE "/{slug}"`, `POST "/validate"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_desk_workflows_api.py
from fastapi.testclient import TestClient
from app.main import create_app

SCRIPT = (
    'meta = {"name": "api-wf", "title": "API WF", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("one")\n'
)


def _client():
    return TestClient(create_app())


def test_create_list_get_delete():
    c = _client()
    r = c.post("/api/workflows", json={"script": SCRIPT})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "api-wf"
    assert any(w["slug"] == "api-wf" for w in c.get("/api/workflows").json())
    assert c.get("/api/workflows/api-wf").json()["script"].startswith("meta =")
    assert c.delete("/api/workflows/api-wf").status_code == 200
    assert c.get("/api/workflows/api-wf").status_code == 404


def test_create_invalid_script_422():
    c = _client()
    r = c.post("/api/workflows", json={"script": 'await step("x")\n'})
    assert r.status_code == 422


def test_delete_seed_409():
    c = _client()
    assert c.delete("/api/workflows/risk-manager-control-day").status_code == 409


def test_validate_endpoint():
    c = _client()
    assert c.post("/api/workflows/validate", json={"script": SCRIPT}).json()["ok"] is True
    bad = c.post("/api/workflows/validate", json={"script": "import os\n"})
    assert bad.json()["ok"] is False and bad.json()["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflows_api.py -v`
Expected: FAIL (404s — router not mounted)

- [ ] **Step 3: Implement router**

```python
# backend/app/routers/workflows.py
"""CRUD API for DeskWorkflow rows."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import DeskWorkflowOut, DeskWorkflowSave, DeskWorkflowSummaryOut
from ..services.desk_workflows import (
    delete_desk_workflow,
    get_desk_workflow,
    list_desk_workflows,
    upsert_desk_workflow,
)
from ..services.desk_workflows_script import (
    WorkflowScriptError,
    extract_meta,
    validate_script,
)


def build_desk_workflows_router() -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflows"])

    @router.get("", response_model=list[DeskWorkflowSummaryOut])
    def list_workflows(session: Session = Depends(get_db)):
        return list_desk_workflows(session)

    @router.post("/validate")
    def validate(payload: DeskWorkflowSave):
        try:
            meta = extract_meta(payload.script)
            validate_script(payload.script, slug=meta.get("name", ""))
        except WorkflowScriptError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    @router.get("/{slug}", response_model=DeskWorkflowOut)
    def get_workflow(slug: str, session: Session = Depends(get_db)):
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return wf

    def _upsert(slug: str, payload: DeskWorkflowSave, session: Session):
        try:
            wf = upsert_desk_workflow(session, slug=slug, script=payload.script)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
        session.refresh(wf)
        return wf

    @router.post("", response_model=DeskWorkflowOut)
    def create_workflow(payload: DeskWorkflowSave, session: Session = Depends(get_db)):
        try:
            slug = extract_meta(payload.script)["name"]
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if get_desk_workflow(session, slug) is not None:
            raise HTTPException(status_code=409, detail=f"workflow {slug!r} already exists")
        return _upsert(slug, payload, session)

    @router.put("/{slug}", response_model=DeskWorkflowOut)
    def update_workflow(slug: str, payload: DeskWorkflowSave, session: Session = Depends(get_db)):
        return _upsert(slug, payload, session)

    @router.delete("/{slug}")
    def delete_workflow(slug: str, session: Session = Depends(get_db)):
        try:
            delete_desk_workflow(session, slug)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        return {"ok": True}

    return router
```

- [ ] **Step 4: Mount in main.py**

Near the other `include_router` calls (~line 4149) add:

```python
from .routers.workflows import build_desk_workflows_router  # at top with other router imports
...
app.include_router(build_desk_workflows_router())
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_desk_workflows_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/workflows.py backend/app/main.py backend/tests/test_desk_workflows_api.py
git commit -m "feat(desk-workflows): CRUD router + mount"
```

---

## Phase B — Execution runner

### Task 5: Restricted-exec runner (step/log helpers, SSE queue)

**Files:**
- Create: `backend/app/services/desk_workflow_runner.py`
- Test: `backend/tests/test_desk_workflow_runner.py`

**Interfaces:**
- Consumes: `DeskWorkflow`, `resolve_execution_mode`, `_sse`, arena `_wait_for_pending_tasks`.
- Produces:
  - `async def run_desk_workflow(*, thread_id, workflow, mode, drive, settle) -> AsyncIterator[str]` — yields SSE frames. `drive(thread_id, prompt, resolved_mode) -> AsyncIterator[str]` is the injectable per-step turn driver (default wraps `stream_and_persist` via the agent service); `settle()` is the injectable task-settler (default = arena waiter bound to a baseline). Both injectable for tests.
  - Emits `workflow.start`, `workflow.step.start`, `workflow.step.end`, `workflow.log`, `workflow.step.error`, `workflow.complete`.
  - `persona_to_character(persona: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_desk_workflow_runner.py
import json
import pytest
from app.models import DeskWorkflow
from app.services.desk_workflow_runner import persona_to_character, run_desk_workflow


def _wf(script: str) -> DeskWorkflow:
    return DeskWorkflow(
        slug="t", title="T", persona="risk_manager", description="",
        scope="local", default_mode="yolo", script=script, source="user",
    )


def _parse(frames: list[str]) -> list[tuple[str, dict]]:
    events = []
    for frame in frames:
        lines = frame.strip().split("\n")
        ev = next(l[6:].strip() for l in lines if l.startswith("event:"))
        data = next((l[5:].strip() for l in lines if l.startswith("data:")), "{}")
        events.append((ev, json.loads(data)))
    return events


@pytest.mark.asyncio
async def test_runner_drives_steps_in_order_and_settles():
    calls = []

    async def drive(thread_id, prompt, mode):
        calls.append(("drive", prompt, mode))
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    def settle():
        calls.append(("settle", None, None))

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo","scope":"local"}\n'
        'await step("a")\n'
        'log("mid")\n'
        'await step("b")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo", drive=drive, settle=settle,
    )]
    events = _parse(frames)
    names = [e[0] for e in events]
    assert names[0] == "workflow.start"
    assert names[-1] == "workflow.complete"
    assert names.count("workflow.step.start") == 2
    assert ("workflow.log", {"message": "mid"}) in events
    # drive called twice; settle after each drive
    drive_calls = [c for c in calls if c[0] == "drive"]
    assert [c[1] for c in drive_calls] == ["a", "b"]
    assert calls.count(("settle", None, None)) == 2


@pytest.mark.asyncio
async def test_runner_halts_on_step_error():
    async def drive(thread_id, prompt, mode):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo","scope":"local"}\n'
        'await step("a")\n'
        'await step("b")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo", drive=drive, settle=lambda: None,
    )]
    events = _parse(frames)
    names = [e[0] for e in events]
    assert "workflow.step.error" in names
    assert "workflow.complete" not in names
    assert names.count("workflow.step.start") == 1  # halted after first


def test_persona_to_character():
    assert persona_to_character("risk_manager") == "risk_manager"
    assert persona_to_character("quant") == "high_board"
    assert persona_to_character("sales") == "trader"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflow_runner.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement runner**

```python
# backend/app/services/desk_workflow_runner.py
"""Auto-pilot runner: exec a DeskWorkflow script, driving real desk turns."""
from __future__ import annotations

import ast
import asyncio
import json as _json
from typing import AsyncIterator, Awaitable, Callable

from ..models import DeskWorkflow
from .agents import _sse, resolve_execution_mode
from .desk_workflows_script import guard_script

_CHARACTER_BY_PERSONA = {
    "trader": "trader",
    "risk_manager": "risk_manager",
    "sales": "trader",
    "quant": "high_board",
}

# Type aliases for the injectable seams.
Drive = Callable[[int, str, str], AsyncIterator[str]]
Settle = Callable[[], None]


def persona_to_character(persona: str) -> str:
    return _CHARACTER_BY_PERSONA.get(persona, "trader")


def _wrap_async(script: str) -> ast.Module:
    """Lift the script body into `async def __workflow__():` so top-level await works."""
    tree = ast.parse(script)
    func = ast.AsyncFunctionDef(
        name="__workflow__",
        args=ast.arguments(
            posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
            kw_defaults=[], kwarg=None, defaults=[],
        ),
        body=tree.body or [ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    module = ast.Module(body=[func], type_ignores=[])
    return ast.fix_missing_locations(module)


_SAFE_BUILTINS = {
    "len": len, "range": range, "enumerate": enumerate, "str": str, "int": int,
    "float": float, "bool": bool, "list": list, "dict": dict, "tuple": tuple,
    "set": set, "min": min, "max": max, "sum": sum, "sorted": sorted, "abs": abs,
    "round": round, "any": any, "all": all, "zip": zip,
}


async def run_desk_workflow(
    *,
    thread_id: int,
    workflow: DeskWorkflow,
    mode: str,
    drive: Drive,
    settle: Settle,
) -> AsyncIterator[str]:
    guard_script(workflow.script)
    resolved_mode, _clear_hitl, _allow = resolve_execution_mode(mode, False)

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    state = {"index": 0, "error": None}

    async def step(prompt: str, *, mode: str | None = None):
        state["index"] += 1
        idx = state["index"]
        step_mode, _c, _a = resolve_execution_mode(mode or resolved_mode, False)
        await queue.put(_sse("workflow.step.start", {"index": idx, "prompt": prompt}))
        text_parts: list[str] = []
        async for frame in drive(thread_id, prompt, step_mode):
            await queue.put(frame)
            # accumulate streamed text for StepResult.text
            if frame.startswith("event: token"):
                data = frame.split("data:", 1)[1].strip() if "data:" in frame else "{}"
                try:
                    text_parts.append(str(_json.loads(data).get("text", "")))
                except Exception:
                    pass
        settle()
        await queue.put(_sse("workflow.step.end", {"index": idx}))
        return type("StepResult", (), {"text": "".join(text_parts), "ok": True})()

    def log(message: str) -> None:
        queue.put_nowait(_sse("workflow.log", {"message": str(message)}))

    async def _execute() -> None:
        module = _wrap_async(workflow.script)
        code = compile(module, filename=f"<desk-workflow:{workflow.slug}>", mode="exec")
        ns: dict = {
            "__builtins__": dict(_SAFE_BUILTINS),
            "step": step,
            "log": log,
        }
        try:
            exec(code, ns)
            await ns["__workflow__"]()
        except Exception as exc:  # halt on any step/script error
            state["error"] = str(exc)
            await queue.put(
                _sse("workflow.step.error", {"index": state["index"], "message": str(exc)})
            )
        finally:
            await queue.put(None)  # sentinel

    yield _sse("workflow.start", {"slug": workflow.slug, "mode": resolved_mode})
    task = asyncio.create_task(_execute())
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
    await task
    if state["error"] is None:
        yield _sse("workflow.complete", {"steps": state["index"]})
```

> NOTE: the token-text accumulation inside `step` is best-effort — used only to populate `StepResult.text` for script convenience, never for correctness/control of the run.

- [ ] **Step 4: Add pytest-asyncio marker support if needed**

If `pytest.mark.asyncio` is not already enabled, confirm `pytest-asyncio` is configured (other async tests in `backend/tests` use it). If absent, wrap tests with `asyncio.run(...)` instead.

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_desk_workflow_runner.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/desk_workflow_runner.py backend/tests/test_desk_workflow_runner.py
git commit -m "feat(desk-workflows): restricted-exec auto-pilot runner with SSE queue"
```

---

### Task 6: Run endpoint (SSE) in main.py

**Files:**
- Modify: `backend/app/main.py` (add endpoint beside `stream_chat_message`, ~line 834; add default `drive`/`settle` builders)
- Test: `backend/tests/test_desk_workflow_run_endpoint.py`

**Interfaces:**
- Consumes: `run_desk_workflow`, `persona_to_character`, `get_desk_workflow`, `ensure_thread_workflow_state`, arena `_wait_for_pending_tasks`, `active_agent_service.stream_and_persist`.
- Produces: `POST /api/chat/threads/{thread_id}/workflows/{slug}/run` body `{mode?: "auto"|"yolo"}` → SSE `StreamingResponse`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_desk_workflow_run_endpoint.py
from fastapi.testclient import TestClient
from app.main import create_app


def test_run_unknown_slug_404():
    c = TestClient(create_app())
    thread = c.post("/api/chat/threads", json={"title": "t", "character": "risk_manager"}).json()
    r = c.post(f"/api/chat/threads/{thread['id']}/workflows/nope/run", json={})
    assert r.status_code == 404


def test_run_streams_workflow_events(monkeypatch):
    # Stub the per-step driver so we don't invoke the real orchestrator.
    import app.main as main_mod

    async def fake_drive(thread_id, prompt, mode):
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    monkeypatch.setattr(main_mod, "_desk_workflow_drive_factory", lambda svc: fake_drive)
    monkeypatch.setattr(main_mod, "_desk_workflow_settle_factory", lambda: (lambda: None))

    c = TestClient(create_app())
    thread = c.post("/api/chat/threads", json={"title": "t", "character": "risk_manager"}).json()
    body = c.post(
        f"/api/chat/threads/{thread['id']}/workflows/risk-manager-control-day/run",
        json={"mode": "yolo"},
    )
    assert body.status_code == 200
    text = body.text
    assert "event: workflow.start" in text
    assert "event: workflow.complete" in text
    assert text.count("event: workflow.step.start") == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_desk_workflow_run_endpoint.py -v`
Expected: FAIL (404 for the run path / attribute missing)

- [ ] **Step 3: Implement endpoint + factories**

In `backend/app/main.py`, add module-level factories (so tests can monkeypatch) and the endpoint. Place near `stream_chat_message`:

```python
# --- desk-workflow run plumbing (module level) ---
from .services.desk_workflow_runner import run_desk_workflow, persona_to_character
from .services.desk_workflows import get_desk_workflow
from .services.arena.task import _wait_for_pending_tasks
from .models import TaskRun


def _desk_workflow_drive_factory(agent_service):
    """Return an injectable per-step driver that forwards SSE frames."""
    async def drive(thread_id: int, prompt: str, mode: str):
        # persist the step prompt as a user turn so history reads naturally
        with database.SessionLocal() as s:
            s.add(AgentMessage(thread_id=thread_id, role="user", content=prompt, meta={"mode": mode}))
            s.commit()
        async for frame in agent_service.stream_and_persist(
            thread_id=thread_id, content=prompt, mode=mode, confirmed_cost_preview=True,
        ):
            yield frame
    return drive


def _desk_workflow_settle_factory():
    with database.SessionLocal() as s:
        baseline = s.query(TaskRun.id).order_by(TaskRun.id.desc()).first()
    baseline_id = baseline[0] if baseline else 0
    def settle():
        _wait_for_pending_tasks(baseline_id)
    return settle
```

Then the route inside the app factory (where other `@app.post` chat routes live):

```python
    @app.post("/api/chat/threads/{thread_id}/workflows/{slug}/run")
    async def run_thread_workflow(
        thread_id: int,
        slug: str,
        payload: dict | None = None,
        session: Session = Depends(get_db),
    ):
        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        ensure_thread_workflow_state(session, thread.id)
        mode = (payload or {}).get("mode") or wf.default_mode
        drive = _desk_workflow_drive_factory(active_agent_service)
        settle = _desk_workflow_settle_factory()
        return StreamingResponse(
            run_desk_workflow(
                thread_id=thread.id, workflow=wf, mode=mode, drive=drive, settle=settle,
            ),
            media_type="text/event-stream",
        )
```

> NOTE: `AgentMessage` and `database` are already imported in `main.py`. The settle baseline is captured *before* the first step so it only waits on tasks this run queues. The monkeypatch test overrides the two module-level factories.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_desk_workflow_run_endpoint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_desk_workflow_run_endpoint.py
git commit -m "feat(desk-workflows): SSE run endpoint driving auto-pilot in a thread"
```

---

## Phase C — LLM builder backend

### Task 7: `save_desk_workflow` agent tool

**Files:**
- Create: `backend/app/tools/desk_workflows.py`
- Modify: `backend/app/tools/__init__.py` (add to `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py` (add `"save_desk_workflow"` to `DEEP_AGENT_TOOL_NAMES`)
- Test: `backend/tests/test_save_desk_workflow_tool.py`

**Interfaces:**
- Consumes: `upsert_desk_workflow`, `extract_meta`.
- Produces: tool `save_desk_workflow(script: str) -> dict` returning `{slug, ok}` or `{ok: False, error}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_save_desk_workflow_tool.py
from app import database
from app.services.agents import DEEP_AGENT_TOOL_NAMES
from app.tools import all_agent_tools
from app.tools.desk_workflows import save_desk_workflow_tool

SCRIPT = (
    'meta = {"name": "tool-wf", "title": "Tool WF", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("one")\n'
)


def test_tool_registered():
    assert "save_desk_workflow" in DEEP_AGENT_TOOL_NAMES
    assert "save_desk_workflow" in {t.name for t in all_agent_tools()}


def test_tool_saves():
    database.init_db()
    out = save_desk_workflow_tool.invoke({"script": SCRIPT})
    assert out["ok"] is True and out["slug"] == "tool-wf"
    with database.SessionLocal() as s:
        from app.services.desk_workflows import get_desk_workflow
        assert get_desk_workflow(s, "tool-wf") is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_save_desk_workflow_tool.py -v`
Expected: FAIL (module/registration missing)

- [ ] **Step 3: Implement the tool**

```python
# backend/app/tools/desk_workflows.py
"""Agent tool: persist a DeskWorkflow drafted by the build-workflow skill."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.desk_workflows import upsert_desk_workflow
from app.services.desk_workflows_script import WorkflowScriptError, extract_meta


class SaveDeskWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field(description="Full Python workflow script including a `meta = {...}` literal.")


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_desk_workflow", args_schema=SaveDeskWorkflowInput)
def save_desk_workflow_tool(script: str) -> dict[str, Any]:
    """Validate and persist a desk workflow script. Returns the slug on success."""
    database.init_db()
    try:
        slug = extract_meta(script)["name"]
    except WorkflowScriptError as exc:
        return {"ok": False, "error": str(exc)}
    with database.SessionLocal() as session:
        try:
            wf = upsert_desk_workflow(session, slug=slug, script=script)
        except WorkflowScriptError as exc:
            return {"ok": False, "error": str(exc)}
        session.commit()
        return {"ok": True, "slug": wf.slug, "title": wf.title}
```

- [ ] **Step 4: Register the tool**

In `backend/app/tools/__init__.py`: add `from .desk_workflows import save_desk_workflow_tool` and append `save_desk_workflow_tool` to `QUANT_AGENT_TOOLS`.

In `backend/app/services/agents.py`: add `"save_desk_workflow"` to the `DEEP_AGENT_TOOL_NAMES` frozenset.

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_save_desk_workflow_tool.py -v`
Expected: PASS

Also run the tool-count guard if present:
Run: `cd backend && python -m pytest tests/test_agent_tools.py -v` (fix any expected-count assertion: increment by 1)

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools/desk_workflows.py backend/app/tools/__init__.py backend/app/services/agents.py backend/tests/test_save_desk_workflow_tool.py
git commit -m "feat(desk-workflows): save_desk_workflow agent tool + registration"
```

---

### Task 8: `build-workflow` skill + routing + catalog test coupling

**Files:**
- Create: `backend/app/skills/workflows/desk-workflows/build-workflow/SKILL.md`
- Modify: `backend/app/services/deep_agent/persona_domains.py` (add `desk-workflows` domain to trader + risk_manager)
- Modify (test coupling, exact-set/count/routing): `backend/tests/test_skills_catalog.py`, `test_skills_catalog_v2.py`, `test_workflow_skills_phase3.py` OR `test_remaining_workflow_skills_phase3.py`, `test_reference_docs.py`, `test_routing_table.py`
- Test: relies on existing skill-lint test + the six coupled files.

**Interfaces:**
- Produces: a lint-clean workflow skill named `build-workflow` in domain `desk-workflows`, routed for trader + risk_manager.

- [ ] **Step 1: Run the catalog tests to capture the baseline failures you must satisfy**

Run: `cd backend && python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_routing_table.py -v`
Expected: PASS now (baseline green). After adding the skill they will FAIL until updated — that is the signal to update each pinned set/count/routing.

- [ ] **Step 2: Write the skill file**

```markdown
---
name: build-workflow
description: Draft and persist a reusable desk workflow as a Python script when a user wants to define, create, or edit an automated multi-step desk procedure. Use when the user asks to build a workflow, turn a sequence of desk actions into a reusable playbook, or author a slash-command workflow for the Agent Thread.
domain: desk-workflows
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - workflow_intent
optional_context:
  - persona
  - steps
write_actions: true
confirmation_required: true
success_criteria:
  - a valid Python workflow script with a meta literal is drafted
  - the script is persisted via save_desk_workflow and its slug is returned
routing:
  - request: "Create or edit a reusable desk workflow"
    persona: risk_manager
  - request: "Build a slash-command workflow playbook"
    persona: trader
---

## When to use

Use when a user wants a reusable, named workflow they can later launch with a
slash command in the Agent Thread. A workflow is a Python script that calls
`await step("<prompt>")` once per desk turn and may use `log("...")` and plain
Python control flow. The script must define a `meta` dict literal with keys
`name` (kebab slug), `title`, `persona` (trader|risk_manager|sales|quant),
`mode` (auto|yolo), `scope` (local|shared), and optional `description`.

Interview the user for the goal, persona, default mode, and the ordered steps.
Draft the script, then call `save_desk_workflow` with the full script text. Do
not invent tools inside the script — each `step` prompt is handled by the desk
orchestrator exactly like a user message.

## Example

User: Build me a morning risk workflow for the control portfolio.
Assistant: Draft a script with `meta = {"name": "morning-risk", ...}` and two
`await step(...)` calls (read latest risk, then refresh), then call
`save_desk_workflow` with the script and report the slug `morning-risk`.
```

- [ ] **Step 3: Lint the skill**

Run: `cd backend && python -m pytest tests/test_workflow_skills_phase3.py -v` (and the remaining-phase file). If lint fails (token count / missing field), trim the body under 500 tokens and ensure all required frontmatter keys present.

- [ ] **Step 4: Register the domain for personas**

In `backend/app/services/deep_agent/persona_domains.py`, add `"desk-workflows"` to the `PERSONA_WORKFLOW_DOMAINS` lists for `trader` and `risk_manager`.

- [ ] **Step 5: Update the six coupled test pins**

Run the catalog suite (Step 1 command). For each failure, update the pinned data:
- `test_skills_catalog.py`: add a `desk-workflows` domain expectation set `{"build-workflow"}` (mirror the `/workflows/risk/` assertion pattern).
- `test_skills_catalog_v2.py`: increment `trader_total_workflow_catalog` (24 → 25) and `risk_manager_total_workflow_catalog` (26 → 27) — confirm the exact deltas from the failure output.
- `test_workflow_skills_phase3.py` or `test_remaining_workflow_skills_phase3.py`: add `"desk-workflows/build-workflow/SKILL.md"` to the pinned file set (and its domain to the allowed-domains set if present).
- `test_reference_docs.py`: only touch if it asserts a global skill count; otherwise leave.
- `test_routing_table.py`: add the two routing triples to `OLD_TABLE_ROWS`:
  `("Create or edit a reusable desk workflow", "risk_manager", "build-workflow")` and
  `("Build a slash-command workflow playbook", "trader", "build-workflow")`. The row-count assertion updates automatically via `len(rows)`.

- [ ] **Step 6: Run the full catalog suite to verify green**

Run: `cd backend && python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_routing_table.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add backend/app/skills/workflows/desk-workflows backend/app/services/deep_agent/persona_domains.py backend/tests/test_skills_catalog.py backend/tests/test_skills_catalog_v2.py backend/tests/test_workflow_skills_phase3.py backend/tests/test_remaining_workflow_skills_phase3.py backend/tests/test_routing_table.py
git commit -m "feat(desk-workflows): build-workflow skill + routing + catalog test updates"
```

---

## Phase D — Frontend manager page

### Task 9: Types + API client functions

**Files:**
- Modify: `frontend/src/types.ts` (add `'workflows'` to `Route`; add `DeskWorkflow`, `DeskWorkflowSummary` types)
- Modify: `frontend/src/api/client.ts` (add CRUD + validate functions)
- Test: `frontend/src/api/desk-workflows.test.ts`

**Interfaces:**
- Produces: types `DeskWorkflowSummary` (`slug,title,persona,description,scope,default_mode,source`), `DeskWorkflow` (adds `script`). API fns `listWorkflows()`, `getWorkflow(slug)`, `createWorkflow(script)`, `updateWorkflow(slug, script)`, `deleteWorkflow(slug)`, `validateWorkflow(script)`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/api/desk-workflows.test.ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { listWorkflows, createWorkflow } from './client';

afterEach(() => vi.restoreAllMocks());

describe('desk-workflow api', () => {
  it('lists workflows', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([{ slug: 'a', title: 'A', persona: 'trader', description: '', scope: 'local', default_mode: 'auto', source: 'user' }]), { status: 200 }),
    );
    const rows = await listWorkflows();
    expect(rows[0].slug).toBe('a');
  });

  it('creates via POST', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ slug: 'a', script: 'meta = {}' }), { status: 200 }),
    );
    await createWorkflow('meta = {}');
    expect(spy).toHaveBeenCalledWith('/api/workflows', expect.objectContaining({ method: 'POST' }));
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/api/desk-workflows.test.ts`
Expected: FAIL (functions not exported)

- [ ] **Step 3: Add types**

In `frontend/src/types.ts`, add `'workflows'` to the `Route` union, and:

```typescript
export type DeskWorkflowSummary = {
  slug: string;
  title: string;
  persona: 'trader' | 'risk_manager' | 'sales' | 'quant';
  description: string;
  scope: 'local' | 'shared';
  default_mode: 'auto' | 'yolo';
  source: 'seed' | 'user';
};

export type DeskWorkflow = DeskWorkflowSummary & { script: string };
```

- [ ] **Step 4: Add API functions**

In `frontend/src/api/client.ts`:

```typescript
import type { DeskWorkflow, DeskWorkflowSummary } from '../types';

export const listWorkflows = () => api<DeskWorkflowSummary[]>('/api/workflows');
export const getWorkflow = (slug: string) => api<DeskWorkflow>(`/api/workflows/${slug}`);
export const createWorkflow = (script: string) =>
  api<DeskWorkflow>('/api/workflows', { method: 'POST', body: JSON.stringify({ script }) });
export const updateWorkflow = (slug: string, script: string) =>
  api<DeskWorkflow>(`/api/workflows/${slug}`, { method: 'PUT', body: JSON.stringify({ script }) });
export const deleteWorkflow = (slug: string) =>
  api<{ ok: boolean }>(`/api/workflows/${slug}`, { method: 'DELETE' });
export const validateWorkflow = (script: string) =>
  api<{ ok: boolean; error: string | null }>('/api/workflows/validate', {
    method: 'POST', body: JSON.stringify({ script }),
  });
```

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npx vitest run src/api/desk-workflows.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/api/desk-workflows.test.ts
git commit -m "feat(desk-workflows): frontend types + api client"
```

---

### Task 10: Workflows manager page + nav/routing

**Files:**
- Create: `frontend/src/routes/Workflows.tsx` (presentational), `frontend/src/routes/Workflows.live.tsx` (wiring), `frontend/src/routes/Workflows.css`
- Modify: `frontend/src/lib/routing.ts` (`ROUTE_PATHS.workflows = '/workflows'`), `frontend/src/main.tsx` (navItem + import + render switch + commandItem)
- Test: `frontend/src/routes/Workflows.test.tsx`

**Interfaces:**
- Consumes: api fns (Task 9).
- Produces: a `'workflows'` route page that lists workflows, shows a script editor with inline validate, save (create/update), delete (disabled for `source==='seed'`).

- [ ] **Step 1: Write the failing test (presentational)**

```tsx
// frontend/src/routes/Workflows.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Workflows } from './Workflows';

describe('Workflows page', () => {
  it('lists workflows and marks seed as non-deletable', () => {
    render(
      <Workflows
        items={[
          { slug: 'risk-manager-control-day', title: 'Risk Manager Control Day', persona: 'risk_manager', description: 'd', scope: 'shared', default_mode: 'yolo', source: 'seed' },
        ]}
        selected={null}
        draftScript=""
        validation={null}
        onSelect={() => {}}
        onChangeScript={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onNew={() => {}}
      />,
    );
    expect(screen.getByText('Risk Manager Control Day')).toBeInTheDocument();
    expect(screen.getByText(/seed/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/routes/Workflows.test.tsx`
Expected: FAIL (no `Workflows`)

- [ ] **Step 3: Implement the presentational page**

```tsx
// frontend/src/routes/Workflows.tsx
import type { DeskWorkflow, DeskWorkflowSummary } from '../types';
import { Button } from '../components/Button';
import './Workflows.css';

type Props = {
  items: DeskWorkflowSummary[];
  selected: DeskWorkflow | null;
  draftScript: string;
  validation: { ok: boolean; error: string | null } | null;
  onSelect: (slug: string) => void;
  onChangeScript: (script: string) => void;
  onSave: () => void;
  onDelete: (slug: string) => void;
  onNew: () => void;
};

export function Workflows({
  items, selected, draftScript, validation,
  onSelect, onChangeScript, onSave, onDelete, onNew,
}: Props) {
  return (
    <div className="workflows">
      <aside className="workflows__list">
        <Button onClick={onNew}>New workflow</Button>
        <ul>
          {items.map((w) => (
            <li key={w.slug}>
              <button className="workflows__item" onClick={() => onSelect(w.slug)}>
                <span>{w.title}</span>
                <small>{w.persona} · {w.scope} · {w.default_mode}</small>
                {w.source === 'seed' && <em className="workflows__badge">seed</em>}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="workflows__editor">
        <textarea
          aria-label="workflow script"
          value={draftScript}
          onChange={(e) => onChangeScript(e.target.value)}
          spellCheck={false}
        />
        {validation && !validation.ok && (
          <p className="workflows__error">{validation.error}</p>
        )}
        <div className="workflows__actions">
          <Button onClick={onSave} disabled={validation ? !validation.ok : false}>Save</Button>
          {selected && selected.source !== 'seed' && (
            <Button variant="ghost" onClick={() => onDelete(selected.slug)}>Delete</Button>
          )}
        </div>
      </section>
    </div>
  );
}
```

Add minimal `Workflows.css` (mirror `Skills.css` layout — two-column flex).

- [ ] **Step 4: Implement the live wiring**

```tsx
// frontend/src/routes/Workflows.live.tsx
import { useCallback, useEffect, useState } from 'react';
import { Workflows } from './Workflows';
import {
  createWorkflow, deleteWorkflow, getWorkflow, listWorkflows, updateWorkflow, validateWorkflow,
} from '../api/client';
import type { DeskWorkflow, DeskWorkflowSummary } from '../types';

const TEMPLATE = `meta = {
    "name": "new-workflow",
    "title": "New Workflow",
    "persona": "risk_manager",
    "mode": "auto",
    "scope": "local",
    "description": "",
}

await step("First step prompt")
`;

export function WorkflowsLive() {
  const [items, setItems] = useState<DeskWorkflowSummary[]>([]);
  const [selected, setSelected] = useState<DeskWorkflow | null>(null);
  const [draft, setDraft] = useState('');
  const [validation, setValidation] = useState<{ ok: boolean; error: string | null } | null>(null);

  const refresh = useCallback(async () => setItems(await listWorkflows()), []);
  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!draft) { setValidation(null); return; }
    const handle = setTimeout(() => { void validateWorkflow(draft).then(setValidation); }, 300);
    return () => clearTimeout(handle);
  }, [draft]);

  const onSelect = async (slug: string) => {
    const wf = await getWorkflow(slug);
    setSelected(wf); setDraft(wf.script);
  };
  const onNew = () => { setSelected(null); setDraft(TEMPLATE); };
  const onSave = async () => {
    if (selected) await updateWorkflow(selected.slug, draft);
    else await createWorkflow(draft);
    await refresh();
  };
  const onDelete = async (slug: string) => {
    await deleteWorkflow(slug); setSelected(null); setDraft(''); await refresh();
  };

  return (
    <Workflows
      items={items} selected={selected} draftScript={draft} validation={validation}
      onSelect={onSelect} onChangeScript={setDraft} onSave={onSave} onDelete={onDelete} onNew={onNew}
    />
  );
}
```

- [ ] **Step 5: Wire route + nav**

- `frontend/src/lib/routing.ts`: add `workflows: '/workflows',` to `ROUTE_PATHS`.
- `frontend/src/main.tsx`: import `WorkflowsLive`; add `{ route: 'workflows' as const, label: 'Workflows' }` to `navItems` (after `skills`); add render `{route === 'workflows' && <WorkflowsLive />}`; add a `commandItems` jump entry.

- [ ] **Step 6: Run to verify pass**

Run: `cd frontend && npx vitest run src/routes/Workflows.test.tsx`
Expected: PASS. Also run `npx tsc --noEmit` to confirm route-union types are consistent.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/Workflows.tsx frontend/src/routes/Workflows.live.tsx frontend/src/routes/Workflows.css frontend/src/lib/routing.ts frontend/src/main.tsx frontend/src/routes/Workflows.test.tsx
git commit -m "feat(desk-workflows): Workflows manager page + nav/routing"
```

---

## Phase E — Composer slash picker + run wiring

### Task 11: ChatComposer slash picker (with reserved-command guard)

**Files:**
- Modify: `frontend/src/components/ChatComposer.tsx` (add slash picker + `onLaunchWorkflow` + `workflows` props)
- Create: `frontend/src/lib/reservedCommands.ts` (`RESERVED_COMPOSER_COMMANDS = new Set(['goal'])`)
- Test: `frontend/src/components/ChatComposer.slash.test.tsx`

**Interfaces:**
- Consumes: `DeskWorkflowSummary[]` passed in, `RESERVED_COMPOSER_COMMANDS`.
- Produces: new optional props `workflows?: DeskWorkflowSummary[]`, `onLaunchWorkflow?: (slug: string, mode: 'auto'|'yolo') => void`. When text starts with `/` and first token is not reserved, show a filtered dropdown; selecting calls `onLaunchWorkflow(slug, default_mode)`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/ChatComposer.slash.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatComposer } from './ChatComposer';

const wf = [
  { slug: 'risk-manager-control-day', title: 'Risk Manager Control Day', persona: 'risk_manager', description: '', scope: 'shared', default_mode: 'yolo', source: 'seed' },
] as const;

describe('ChatComposer slash picker', () => {
  it('shows workflows on / and launches on select', () => {
    const onLaunch = vi.fn();
    render(
      <ChatComposer
        onSend={() => {}} sending={false}
        workflows={wf as never} onLaunchWorkflow={onLaunch}
      />,
    );
    const box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: '/risk' } });
    fireEvent.click(screen.getByText('Risk Manager Control Day'));
    expect(onLaunch).toHaveBeenCalledWith('risk-manager-control-day', 'yolo');
  });

  it('does not show picker for reserved /goal', () => {
    render(
      <ChatComposer onSend={() => {}} sending={false} workflows={wf as never} onLaunchWorkflow={() => {}} />,
    );
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '/goal reduce risk' } });
    expect(screen.queryByText('Risk Manager Control Day')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/ChatComposer.slash.test.tsx`
Expected: FAIL (props/picker missing)

- [ ] **Step 3: Implement**

Create `frontend/src/lib/reservedCommands.ts`:

```typescript
export const RESERVED_COMPOSER_COMMANDS = new Set<string>(['goal']);
```

In `ChatComposer.tsx`: add props `workflows?: DeskWorkflowSummary[]` and `onLaunchWorkflow?`. Compute picker visibility from `text`:

```typescript
import { RESERVED_COMPOSER_COMMANDS } from '../lib/reservedCommands';
import type { DeskWorkflowSummary } from '../types';

// inside component:
const firstToken = text.startsWith('/') ? text.slice(1).split(/\s+/)[0] ?? '' : null;
const showPicker =
  firstToken !== null &&
  !RESERVED_COMPOSER_COMMANDS.has(firstToken) &&
  !text.includes(' ') &&
  (workflows?.length ?? 0) > 0;
const matches = (workflows ?? []).filter((w) =>
  w.slug.includes(firstToken ?? '') || w.title.toLowerCase().includes((firstToken ?? '').toLowerCase()),
);

const launch = (w: DeskWorkflowSummary) => {
  onLaunchWorkflow?.(w.slug, w.default_mode);
  setText('');
};
```

Render the dropdown above the textarea when `showPicker && matches.length`:

```tsx
{showPicker && matches.length > 0 && (
  <ul className="composer__slash" role="listbox">
    {matches.map((w) => (
      <li key={w.slug}>
        <button type="button" onClick={() => launch(w)}>
          <strong>/{w.slug}</strong> <span>{w.title}</span>
        </button>
      </li>
    ))}
  </ul>
)}
```

Guard `handleSend` so a bare `/slug` matching a workflow does not send as chat (optional: if `showPicker`, Enter launches the top match instead of sending).

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/components/ChatComposer.slash.test.tsx`
Expected: PASS. Also run the existing `ChatComposer.test.tsx` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatComposer.tsx frontend/src/lib/reservedCommands.ts frontend/src/components/ChatComposer.slash.test.tsx
git commit -m "feat(desk-workflows): composer slash picker with reserved-command guard"
```

---

### Task 12: Workflow-run SSE consumption + AgentDesk wiring + step dividers

**Files:**
- Modify: `frontend/src/hooks/useAgentChatController.ts` (add `launchWorkflow(slug, mode)` that POSTs the run endpoint and reuses the SSE reader; handle `workflow.*` events)
- Modify: `frontend/src/routes/AgentDesk.live.tsx` + `AgentDesk.tsx` (pass `workflows`, `onLaunchWorkflow`)
- Modify: `frontend/src/components/MessageList.tsx` (render workflow step dividers from a new lightweight item kind)
- Test: `frontend/src/hooks/useAgentChatController.workflow.test.ts`

**Interfaces:**
- Consumes: run endpoint (Task 6), the SSE reader pattern in `useAgentChatController.ts`, `listWorkflows()`.
- Produces: `launchWorkflow(slug, mode)` on the controller; `workflow.step.start/end/log/complete/step.error` rendered as dividers/log lines.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/hooks/useAgentChatController.workflow.test.ts
import { describe, it, expect, vi } from 'vitest';
import { parseWorkflowSse } from './useAgentChatController';

describe('workflow SSE parsing', () => {
  it('extracts step events in order', () => {
    const frames = [
      'event: workflow.start\ndata: {"slug":"x","mode":"yolo"}\n\n',
      'event: workflow.step.start\ndata: {"index":1,"prompt":"a"}\n\n',
      'event: workflow.step.end\ndata: {"index":1}\n\n',
      'event: workflow.complete\ndata: {"steps":1}\n\n',
    ].join('');
    const events = parseWorkflowSse(frames);
    expect(events.map((e) => e.type)).toEqual([
      'workflow.start', 'workflow.step.start', 'workflow.step.end', 'workflow.complete',
    ]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useAgentChatController.workflow.test.ts`
Expected: FAIL (`parseWorkflowSse` not exported)

- [ ] **Step 3: Implement parsing helper + launch**

In `useAgentChatController.ts`, export a pure parser (reuse the existing line-splitter shape):

```typescript
export type WorkflowSseEvent = { type: string; data: Record<string, unknown> };

export function parseWorkflowSse(text: string): WorkflowSseEvent[] {
  const events: WorkflowSseEvent[] = [];
  let eventType = 'message';
  let dataLines: string[] = [];
  const flush = () => {
    if (dataLines.length) {
      try { events.push({ type: eventType, data: JSON.parse(dataLines.join('\n')) }); } catch { /* skip */ }
    }
    eventType = 'message'; dataLines = [];
  };
  for (const line of text.split('\n')) {
    if (line === '') { flush(); continue; }
    if (line.startsWith('event:')) eventType = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  flush();
  return events;
}
```

Add `launchWorkflow(slug, mode)` that `fetch`es `POST /api/chat/threads/${threadId}/workflows/${slug}/run` and feeds the response body through the SAME reader loop already used for `messages/stream` (extract that reader loop into a shared `consumeSse(response, onEvent)` helper if not already). Route `token`/`tool_start`/`tool_end` to the existing draft handler; route `workflow.step.start`/`end`/`log`/`complete`/`step.error` to append divider/log items to the thread message list.

- [ ] **Step 4: Wire AgentDesk**

In `AgentDesk.live.tsx`: load `listWorkflows()` once, pass `workflows` + `onLaunchWorkflow={controller.launchWorkflow}` down through `AgentDesk.tsx` to `ChatComposer`. In `MessageList.tsx`, render a `workflow-divider` message kind (`▶ Step {index}` / log line / error banner / "Workflow complete").

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npx vitest run src/hooks/useAgentChatController.workflow.test.ts`
Expected: PASS. Run `npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useAgentChatController.ts frontend/src/routes/AgentDesk.live.tsx frontend/src/routes/AgentDesk.tsx frontend/src/components/MessageList.tsx frontend/src/hooks/useAgentChatController.workflow.test.ts
git commit -m "feat(desk-workflows): launch workflow runs in-thread with step dividers"
```

---

## Phase F — Bespoke Workflow Builder UI

### Task 13: Builder split view (chat + live script preview + Save)

**Files:**
- Create: `frontend/src/routes/WorkflowBuilder.tsx`, `WorkflowBuilder.css`
- Modify: `frontend/src/routes/Workflows.live.tsx` (add a "Create with AI" tab that mounts the builder)
- Test: `frontend/src/routes/WorkflowBuilder.test.tsx`

**Interfaces:**
- Consumes: a builder agent thread (reuse the chat controller bound to a dedicated thread routed to `build-workflow`), the agent's `save_desk_workflow` tool-call args (surfaced via the existing `tool_start` event with `name === 'save_desk_workflow'` whose `args.script` is the draft), `createWorkflow`/`updateWorkflow`.
- Produces: split layout — left builder chat, right live script preview (from the latest `save_desk_workflow` tool-call args) + a Save button that persists via the CRUD API.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/WorkflowBuilder.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { WorkflowBuilder } from './WorkflowBuilder';

describe('WorkflowBuilder', () => {
  it('renders the drafted script preview and a Save button', () => {
    render(
      <WorkflowBuilder
        chat={<div>chat</div>}
        draftScript={'meta = {"name":"x"}'}
        onSave={() => {}}
        saving={false}
      />,
    );
    expect(screen.getByText(/meta = /)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/routes/WorkflowBuilder.test.tsx`
Expected: FAIL (no `WorkflowBuilder`)

- [ ] **Step 3: Implement presentational builder**

```tsx
// frontend/src/routes/WorkflowBuilder.tsx
import type { ReactNode } from 'react';
import { Button } from '../components/Button';
import './WorkflowBuilder.css';

type Props = {
  chat: ReactNode;
  draftScript: string;
  onSave: () => void;
  saving: boolean;
};

export function WorkflowBuilder({ chat, draftScript, onSave, saving }: Props) {
  return (
    <div className="wf-builder">
      <div className="wf-builder__chat">{chat}</div>
      <div className="wf-builder__preview">
        <pre>{draftScript || '// the assistant will draft your workflow here'}</pre>
        <Button onClick={onSave} disabled={!draftScript || saving}>
          {saving ? 'Saving…' : 'Save workflow'}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the builder live**

In `Workflows.live.tsx` add a "Create with AI" toggle that:
- Creates (or reuses) a dedicated builder `AgentThread` (POST `/api/chat/threads` with `character: 'risk_manager'`, title `"Workflow builder"`).
- Mounts the existing chat UI (MessageList + ChatComposer) bound to that thread via the chat controller, with an initial primed message hint to route to `build-workflow`.
- Watches streamed `tool_start` events; when `name === 'save_desk_workflow'`, set `draftScript = args.script`.
- On Save → `createWorkflow(draftScript)` (or `updateWorkflow` if slug exists) → refresh the list → switch back to the manager tab.

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npx vitest run src/routes/WorkflowBuilder.test.tsx`
Expected: PASS. Run `npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/WorkflowBuilder.tsx frontend/src/routes/WorkflowBuilder.css frontend/src/routes/Workflows.live.tsx frontend/src/routes/WorkflowBuilder.test.tsx
git commit -m "feat(desk-workflows): bespoke Workflow Builder (chat + live script preview)"
```

---

## Phase G — End-to-end validation

### Task 14: Full-suite regression + manual smoke

**Files:** none (validation only)

- [ ] **Step 1: Backend suite (no-.env worktree avoids the tracing false-failure)**

Run: `cd backend && python -m pytest -q`
Expected: all green; if `test_tracing_config` fails, confirm it is the known `.env` leak (this worktree has no `.env`), not a regression.

- [ ] **Step 2: Frontend suite + types**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: green.

- [ ] **Step 3: Manual smoke (optional, requires app running + a Zenmux key)**

Launch the app, open Agent Desk, type `/risk` → pick the flagship → confirm 7 step dividers stream and a report artifact appears. Then open Workflows → "Create with AI" → ask for a 2-step workflow → confirm the script preview fills and Save adds it to the list and the composer picker.

- [ ] **Step 4: Final commit (if any cleanups)**

```bash
git add -A && git commit -m "test(desk-workflows): full-suite green + cleanups"
```

---

## Notes & carry-forwards
- `market_data` is not modeled in workflow scripts; steps rely on the thread's normal pricing-profile pathway (same as arena).
- Restricted exec is not a security sandbox; a future multi-user `shared` scope needs real isolation.
- Builder thread lifecycle is ephemeral-per-session in v1; persisting/tagging builder threads (like arena's `source`) is a future enhancement.
- Coordinate `RESERVED_COMPOSER_COMMANDS` / `RESERVED_WORKFLOW_SLUGS` with the goal-mode spec when it lands so `/goal` stays reserved on both sides.
