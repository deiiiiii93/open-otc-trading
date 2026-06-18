# Async Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a general-purpose async-agent dispatch capability to the desk orchestrator. One async agent type, three orchestrator tools (`start_async_agent`, `list_async_agents`, `cancel_async_agent`), backed by `task_runner`'s existing thread pool. Results auto-post into the parent chat thread; HITL interrupts bubble up for approval on the parent thread.

**Architecture:** Same-process asyncio background tasks. The async agent is a flat (non-persona) deep agent built by a new `build_async_agent()` that mirrors `build_orchestrator()`. Specialization is per-dispatch via the caller's `prompt` argument (Claude Code `Agent`-style) — no per-subagent registry. HITL bubble-up captures subagent interrupts and writes them as pending actions on the parent thread; the existing approve/reject UI works unchanged.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (Mapped[] declarative), LangGraph + LangChain (deepagents framework), Alembic, pytest. Frontend: React + TypeScript + Vite + vitest.

**Spec:** `docs/superpowers/specs/2026-05-16-async-subagents-design.md` (commit `073afa5`).

**Branch:** `feat/async-subagents` (single PR, 21 commits, behavior-preserving through Task 18; Task 19+ activates dispatch).

---

## File Structure

### New module: `backend/app/services/async_agents/`

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports. |
| `agent.py` | `build_async_agent()` — flat deep-agent builder paralleling `build_orchestrator`. |
| `policy.py` | Concurrency cap, scratch-dir template, policy fragment list. |
| `prompts/async_agent.md` | Identity prompt for the background analyst role. |
| `runner.py` | Lifecycle: `start_async_agent_task`, `_run`, brief composition. |
| `bubble_up.py` | Subagent-interrupt → parent-thread pending-action projection. |
| `autopost.py` | Subagent-completion → parent-thread result message + artifact materialization. |
| `resume.py` | `resume_async_agent_interrupt` — routes HITL approve/reject to subagent thread_id. |
| `tools.py` | Three LangChain `BaseTool` subclasses. |

### Modified files

| File | Change |
|---|---|
| `backend/app/models.py` | `TaskRun` gains 4 columns. |
| `backend/app/schemas.py` | New types; `AgentActionProposal.async_task_id`. |
| `backend/app/main.py` | Extend resume endpoint; new `GET /api/threads/{id}/async_agents`. |
| `backend/app/services/agents.py` | `DEEP_AGENT_TOOL_NAMES` adds 3; new `AgentService.resume_async_agent`. |
| `backend/app/services/langchain_tools.py` | Register 3 new tools in `QUANT_AGENT_TOOLS`. |
| `backend/app/services/task_runner.py` | `mark_stale_tasks_failed` posts async-restart message. |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | New "Async dispatch" section (Task 20 — activation). |
| `backend/app/services/deep_agent/skills/policy/cost-preview.md` | Async addendum (Task 19). |
| `backend/alembic/versions/0014_async_agent_columns.py` | New migration (head was `0013_underlying_pricing_defaults` at plan-write time). |
| `frontend/src/types.ts` | Async types + `AgentActionProposal.async_task_id`. |
| `frontend/src/components/ChatBubble.tsx` | Render `character="async_agent"` affordance. |
| `frontend/src/components/FloatingAgentMiniChat.tsx` | Pass `async_task_id` on approve/reject. |
| `frontend/src/routes/Tasks.tsx` and `Tasks.live.tsx` | New `async_agent` kind chip. |

### New test files

`tests/test_async_agents_unit.py`, `tests/test_async_agents_hitl.py`, `tests/test_async_agents_tools.py`, `tests/test_async_agents_integration.py`. Frontend: extend existing `*.test.tsx`.

---

## Pre-flight

- [ ] **Step 0.1: Create branch**

```bash
git checkout -b feat/async-subagents
git log -1 --oneline    # should be 073afa5 (spec commit) or a later HEAD
```

- [ ] **Step 0.2: Baseline green**

```bash
cd /Users/fuxinyao/open-otc-trading
uv run pytest -q --no-cov 2>&1 | tail -20
```

Expected: all tests pass. Do NOT proceed if baseline is red.

---

## Phase 1 — Schema migration (Tasks 1-3)

### Task 1: Extend `TaskRun` model with four new columns

**Files:**
- Modify: `backend/app/models.py` (around line 466-494, the `TaskRun` class)
- Test: `tests/test_async_agents_unit.py` (new)

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_async_agents_unit.py`:

```python
"""Unit tests for the async_agents module — Phase 1 schema only."""
from __future__ import annotations

from app.models import TaskRun


def test_task_run_has_async_agent_columns():
    """TaskRun gains parent_thread_id, description, result_payload, cancel_requested."""
    columns = {col.name for col in TaskRun.__table__.columns}
    assert "parent_thread_id" in columns
    assert "description" in columns
    assert "result_payload" in columns
    assert "cancel_requested" in columns


def test_task_run_async_columns_have_expected_types():
    """Types: parent_thread_id int FK nullable, description Text nullable,
    result_payload JSON nullable, cancel_requested Boolean default False."""
    cols = {c.name: c for c in TaskRun.__table__.columns}
    assert cols["parent_thread_id"].nullable is True
    assert cols["description"].nullable is True
    assert cols["result_payload"].nullable is True
    assert cols["cancel_requested"].nullable is False
    assert cols["cancel_requested"].default.arg is False
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_task_run_has_async_agent_columns -v
```

Expected: FAIL with `AssertionError: 'parent_thread_id' in columns` or similar.

- [ ] **Step 1.3: Edit `TaskRun` to add the four columns**

In `backend/app/models.py`, inside `class TaskRun(Base):`, immediately after the existing `report_job_id` mapped_column and before `progress_current`, insert:

```python
    parent_thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id"), index=True, nullable=True
    )
    description: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

If `Boolean` is not yet imported at the top of `models.py`, add it to the SQLAlchemy import line.

- [ ] **Step 1.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: both tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add backend/app/models.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): add 4 columns to TaskRun for async-agent kind"
```

### Task 2: Alembic migration

**Files:**
- Create: `backend/alembic/versions/0014_async_agent_columns.py`

- [ ] **Step 2.1: Write the migration**

Verify the current head first:

```bash
grep "^revision " /Users/fuxinyao/open-otc-trading/backend/alembic/versions/0013_underlying_pricing_defaults.py
```

Expected: `revision = "0013_underlying_pricing_defaults"` — use this as the `down_revision` below. If the actual head is different (other migrations may have landed), adapt accordingly.

Create `backend/alembic/versions/0014_async_agent_columns.py`:

```python
"""async_agent columns on task_runs

Revision ID: 0014_async_agent_columns
Revises: 0013_underlying_pricing_defaults
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_async_agent_columns"
down_revision = "0013_underlying_pricing_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_runs") as batch:
        batch.add_column(
            sa.Column(
                "parent_thread_id",
                sa.Integer(),
                sa.ForeignKey("agent_threads.id"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("description", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("result_payload", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.create_index(
        "ix_task_runs_parent_thread_id",
        "task_runs",
        ["parent_thread_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_runs_parent_thread_id", table_name="task_runs")
    with op.batch_alter_table("task_runs") as batch:
        batch.drop_column("cancel_requested")
        batch.drop_column("result_payload")
        batch.drop_column("description")
        batch.drop_column("parent_thread_id")
```

If new migrations have landed since this plan was written, set `down_revision` to the actual head's `revision` value (run `ls backend/alembic/versions/` and grep the latest file's `revision = "..."` line).

- [ ] **Step 2.2: Apply migration on a fresh DB**

```bash
cd /Users/fuxinyao/open-otc-trading
rm -f /tmp/check_migration.sqlite
DATABASE_URL=sqlite:///tmp/check_migration.sqlite uv run alembic upgrade head
DATABASE_URL=sqlite:///tmp/check_migration.sqlite uv run python -c "
import sqlite3
con = sqlite3.connect('/tmp/check_migration.sqlite')
cols = [row[1] for row in con.execute('PRAGMA table_info(task_runs)')]
print(sorted(cols))
assert 'parent_thread_id' in cols
assert 'description' in cols
assert 'result_payload' in cols
assert 'cancel_requested' in cols
print('OK')
"
```

Expected: prints column list, then `OK`.

- [ ] **Step 2.3: Round-trip downgrade**

```bash
DATABASE_URL=sqlite:///tmp/check_migration.sqlite uv run alembic downgrade -1
DATABASE_URL=sqlite:///tmp/check_migration.sqlite uv run python -c "
import sqlite3
con = sqlite3.connect('/tmp/check_migration.sqlite')
cols = [row[1] for row in con.execute('PRAGMA table_info(task_runs)')]
assert 'parent_thread_id' not in cols
assert 'cancel_requested' not in cols
print('OK')
"
DATABASE_URL=sqlite:///tmp/check_migration.sqlite uv run alembic upgrade head
rm -f /tmp/check_migration.sqlite
```

Expected: both runs print `OK`.

- [ ] **Step 2.4: Commit**

```bash
git add backend/alembic/versions/0014_async_agent_columns.py
git commit -m "feat(async-subagents): alembic migration for TaskRun columns"
```

### Task 3: Schema regression test against the live test DB

**Files:**
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 3.1: Add a live-DB schema check**

Append to `tests/test_async_agents_unit.py`:

```python
def test_task_run_async_columns_present_in_test_db(session):
    """The test fixture-built DB has the new columns (migration or metadata.create_all)."""
    from sqlalchemy import inspect

    inspector = inspect(session.bind)
    cols = {col["name"] for col in inspector.get_columns("task_runs")}
    assert "parent_thread_id" in cols
    assert "description" in cols
    assert "result_payload" in cols
    assert "cancel_requested" in cols
```

- [ ] **Step 3.2: Run the new test**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 3.3: Commit**

```bash
git add tests/test_async_agents_unit.py
git commit -m "test(async-subagents): assert TaskRun async columns exist in test DB"
```

---

## Phase 2 — Async agent runtime (Tasks 4-6)

### Task 4: Identity prompt file

**Files:**
- Create: `backend/app/services/async_agents/__init__.py`
- Create: `backend/app/services/async_agents/prompts/async_agent.md`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_async_agent_identity_prompt_loads_and_has_required_sections():
    """The identity prompt names each section the runtime depends on."""
    from pathlib import Path
    import app.services.async_agents as pkg

    prompt_path = Path(pkg.__file__).parent / "prompts" / "async_agent.md"
    text = prompt_path.read_text(encoding="utf-8")
    for needle in (
        "background analyst",
        "## Decision lens",
        "## Tools you use",
        "## Scratch and artifacts",
        "## Clarification policy",
        "## Skills",
        "## Output style",
        "## HITL bubble-up",
        "## Forbidden",
    ):
        assert needle in text, f"missing section/phrase: {needle!r}"
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_async_agent_identity_prompt_loads_and_has_required_sections -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.async_agents'`.

- [ ] **Step 4.3: Create the package and prompt**

Create `backend/app/services/async_agents/__init__.py` with one line:

```python
"""Async-agent dispatch module — general-purpose background analyst."""
```

Create `backend/app/services/async_agents/prompts/async_agent.md` with the full identity prompt body from spec §6.1 (verbatim). Copy starting at "You are the desk's background analyst." through the final "## Forbidden" section.

- [ ] **Step 4.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_async_agent_identity_prompt_loads_and_has_required_sections -v
```

Expected: PASS.

- [ ] **Step 4.5: Commit**

```bash
git add backend/app/services/async_agents/__init__.py \
        backend/app/services/async_agents/prompts/async_agent.md \
        tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): identity prompt for background analyst"
```

### Task 5: Policy constants module

**Files:**
- Create: `backend/app/services/async_agents/policy.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_async_policy_constants():
    """Policy constants are as specified in spec §2.1."""
    from app.services.async_agents import policy

    assert policy.MAX_CONCURRENT_PER_THREAD == 4
    assert policy.SCRATCH_DIR_TEMPLATE == "/trading_desk/async/{task_id}/"
    # Mirrors trader/risk minus clarification-protocol
    assert policy.ASYNC_POLICY_FRAGMENTS == (
        "read-before-compute",
        "cost-preview",
        "hitl-batch-size-1",
        "run-python-rfsw",
    )


def test_scratch_dir_for_task():
    """Helper builds the scratch dir for a task id."""
    from app.services.async_agents import policy

    assert policy.scratch_dir_for_task(42) == "/trading_desk/async/42/"
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_async_policy_constants -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5.3: Create `policy.py`**

Create `backend/app/services/async_agents/policy.py`:

```python
"""Policy constants and helpers for async-agent dispatch."""
from __future__ import annotations

MAX_CONCURRENT_PER_THREAD: int = 4
SCRATCH_DIR_TEMPLATE: str = "/trading_desk/async/{task_id}/"

# Same fragments as trader/risk personas, minus clarification-protocol
# (async agents cannot clarify mid-flight; the identity prompt teaches
# best-guess + surface-assumption instead).
ASYNC_POLICY_FRAGMENTS: tuple[str, ...] = (
    "read-before-compute",
    "cost-preview",
    "hitl-batch-size-1",
    "run-python-rfsw",
)


def scratch_dir_for_task(task_id: int | str) -> str:
    """Return the virtual scratch-dir path for an async-agent task."""
    return SCRATCH_DIR_TEMPLATE.format(task_id=task_id)
```

- [ ] **Step 5.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_async_policy_constants tests/test_async_agents_unit.py::test_scratch_dir_for_task -v
```

Expected: PASS.

- [ ] **Step 5.5: Commit**

```bash
git add backend/app/services/async_agents/policy.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): policy constants and scratch-dir helper"
```

### Task 6: `build_async_agent()` — flat deep-agent builder

**Files:**
- Create: `backend/app/services/async_agents/agent.py`
- Modify: `backend/app/services/async_agents/__init__.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 6.1: Write the failing tests**

Append to `tests/test_async_agents_unit.py`:

```python
def test_build_async_agent_returns_compiled_graph():
    """build_async_agent returns a compiled state graph (duck-typed: has .ainvoke)."""
    from app.services.async_agents.agent import build_async_agent
    from app.services.deep_agent.checkpointer import build_checkpointer
    from app.services.deep_agent.model_factory import build_agent_model
    from app.services.deep_agent.channel_registry import get_registry
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    registry = get_registry()
    model = build_agent_model(registry)
    if model is None:
        import pytest

        pytest.skip("no LLM channel configured; cannot build agent")
    from app.config import get_settings

    checkpointer = build_checkpointer(get_settings())
    agent = build_async_agent(model=model, tools=QUANT_AGENT_TOOLS, checkpointer=checkpointer, task_id=999)
    assert hasattr(agent, "ainvoke")
    assert hasattr(agent, "aget_state")


def test_build_async_agent_writes_only_to_task_scratch():
    """FilesystemPermissions allow write to /trading_desk/async/<task_id>/** only."""
    from app.services.async_agents.agent import _filesystem_permissions

    perms = _filesystem_permissions(task_id=42)
    # At least one allow-read on /, allow-read+write on the per-task scratch,
    # and a deny-write fallback. Inspect by attributes that
    # deepagents.middleware.permissions.FilesystemPermission exposes.
    paths_with_write_allow: set[str] = set()
    for perm in perms:
        ops = set(getattr(perm, "operations", []) or [])
        mode = getattr(perm, "mode", None)
        path_list = getattr(perm, "paths", []) or []
        if mode == "allow" and "write" in ops:
            paths_with_write_allow.update(path_list)
    assert "/trading_desk/async/42" in paths_with_write_allow
    assert "/trading_desk/async/42/**" in paths_with_write_allow


def test_build_async_agent_uses_same_interrupt_config():
    """Async agent's interrupt_on mirrors hitl.interrupt_on_config exactly."""
    from app.services.async_agents.agent import _interrupt_on_for_async
    from app.services.deep_agent.hitl import interrupt_on_config

    assert _interrupt_on_for_async(yolo_mode=False) == interrupt_on_config(yolo_mode=False)
    assert _interrupt_on_for_async(yolo_mode=True) == interrupt_on_config(yolo_mode=True)
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_unit.py::test_build_async_agent_writes_only_to_task_scratch tests/test_async_agents_unit.py::test_build_async_agent_uses_same_interrupt_config -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 6.3: Create `agent.py`**

Create `backend/app/services/async_agents/agent.py`:

```python
"""build_async_agent — flat deep-agent builder for async-agent dispatch.

Mirrors backend/app/services/deep_agent/orchestrator.py:build_orchestrator,
but without persona subagents (flat) and with a broader skills allowlist
plus per-task scratch write permission.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ..deep_agent.hitl import interrupt_on_config
from ..deep_agent.skills_loader import compose_persona_prompt
from .policy import ASYNC_POLICY_FRAGMENTS, scratch_dir_for_task

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SKILLS_FS_ROOT = Path(__file__).parent.parent / "deep_agent" / "skills"


def _artifacts_root() -> Path:
    try:
        from ... import database

        return Path(database.settings.artifact_dir)
    except Exception:  # pragma: no cover
        return Path(__file__).parent.parent.parent.parent.parent / "artifacts"


def _identity_prompt() -> str:
    return (_PROMPTS_DIR / "async_agent.md").read_text(encoding="utf-8")


def _build_backend() -> Any:
    """Same CompositeBackend shape as build_orchestrator: routes /skills/
    and /artifacts/ to FilesystemBackends, StateBackend for the rest."""
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend

    skills_fs = FilesystemBackend(root_dir=str(_SKILLS_FS_ROOT), virtual_mode=True)
    artifacts_fs = FilesystemBackend(
        root_dir=str(_artifacts_root()), virtual_mode=True
    )
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": skills_fs, "/artifacts/": artifacts_fs},
    )


def _filesystem_permissions(*, task_id: int | str) -> list[Any]:
    """Read-everywhere, write-only-to-per-task-scratch."""
    from deepagents.middleware.permissions import FilesystemPermission

    scratch = scratch_dir_for_task(task_id).rstrip("/")
    return [
        FilesystemPermission(operations=["read"], paths=["/"], mode="allow"),
        FilesystemPermission(
            operations=["read", "write"],
            paths=[scratch, f"{scratch}/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/large_tool_results", "/large_tool_results/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"], paths=["/skills", "/skills/**"], mode="allow"
        ),
        FilesystemPermission(
            operations=["read"], paths=["/artifacts", "/artifacts/**"], mode="allow"
        ),
        FilesystemPermission(
            operations=["read", "write"], paths=["/", "/**"], mode="deny"
        ),
    ]


def _interrupt_on_for_async(*, yolo_mode: bool = False) -> dict[str, Any]:
    """Async agent uses the same interrupt_on map as personas."""
    return interrupt_on_config(yolo_mode=yolo_mode)


def build_async_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    task_id: int | str,
    yolo_mode: bool = False,
) -> Any:
    """Build a flat deep-agent for one async-agent task.

    Same tool list as personas. Broad skills allowlist (domains, procedures,
    products — NOT routing). Filesystem permission grants write to the
    per-task scratch under /trading_desk/async/<task_id>/.
    """
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=compose_persona_prompt(
            identity_prompt=_identity_prompt(),
            policy_fragment_names=ASYNC_POLICY_FRAGMENTS,
        ),
        subagents=[],  # flat — no personas
        interrupt_on=_interrupt_on_for_async(yolo_mode=yolo_mode),
        checkpointer=checkpointer,
        backend=_build_backend(),
        permissions=_filesystem_permissions(task_id=task_id),
        skills=["/skills/domains/", "/skills/procedures/", "/skills/products/"],
        name=f"async_agent_{task_id}",
    )
```

Update `backend/app/services/async_agents/__init__.py`:

```python
"""Async-agent dispatch module — general-purpose background analyst."""
from .agent import build_async_agent
from .policy import (
    ASYNC_POLICY_FRAGMENTS,
    MAX_CONCURRENT_PER_THREAD,
    SCRATCH_DIR_TEMPLATE,
    scratch_dir_for_task,
)

__all__ = [
    "build_async_agent",
    "ASYNC_POLICY_FRAGMENTS",
    "MAX_CONCURRENT_PER_THREAD",
    "SCRATCH_DIR_TEMPLATE",
    "scratch_dir_for_task",
]
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: all PASS (the live-model test may be skipped if no LLM channel is configured — that's fine).

- [ ] **Step 6.5: Commit**

```bash
git add backend/app/services/async_agents/agent.py \
        backend/app/services/async_agents/__init__.py \
        tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): build_async_agent flat deep-agent builder"
```

---

## Phase 3 — Runner + lifecycle (Tasks 7-11)

### Task 7: Runner skeleton — task brief composition

**Files:**
- Create: `backend/app/services/async_agents/runner.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 7.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_compose_task_brief_includes_envelope():
    """Task brief HumanMessage contains prompt, structured inputs, envelope fields."""
    from app.services.async_agents.runner import compose_task_brief

    msg = compose_task_brief(
        task_id=42,
        parent_thread_id=7,
        prompt="Draft a narrative for report 99.",
        inputs={"report_id": 99, "portfolio_id": 3},
        accounting_date="2026-05-16",
    )
    text = msg.content
    assert "Draft a narrative for report 99." in text
    assert "report_id" in text and "99" in text
    assert "portfolio_id" in text and "3" in text
    assert "/trading_desk/async/42/" in text
    assert "Accounting anchor" in text
    assert "2026-05-16" in text
    # task_id surfaced explicitly so the agent can self-reference its scratch
    assert "task_id" in text.lower()
    assert "42" in text
    # parent_thread_id present for audit only
    assert "parent_thread_id" in text.lower()
    assert "7" in text


def test_compose_task_brief_handles_missing_inputs():
    """inputs=None or empty dict still produces a valid brief."""
    from app.services.async_agents.runner import compose_task_brief

    msg = compose_task_brief(
        task_id=1,
        parent_thread_id=2,
        prompt="Hello.",
        inputs=None,
        accounting_date="2026-05-16",
    )
    assert "Hello." in msg.content
    assert "(no structured inputs)" in msg.content or "Inputs: none" in msg.content
```

- [ ] **Step 7.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_compose_task_brief_includes_envelope -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 7.3: Create `runner.py` with `compose_task_brief`**

Create `backend/app/services/async_agents/runner.py`:

```python
"""Async-agent runner: lifecycle, dispatch, brief composition.

Other module-level functions (start_async_agent_task, _run, etc.) are added
in later tasks. This file currently exposes only compose_task_brief.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage

from .policy import scratch_dir_for_task


def compose_task_brief(
    *,
    task_id: int,
    parent_thread_id: int,
    prompt: str,
    inputs: dict[str, Any] | None,
    accounting_date: str,
) -> HumanMessage:
    """Compose the first HumanMessage for the async agent.

    Combines orchestrator's prompt (free-text briefing), structured inputs,
    and the framework envelope (task_id, scratch dir, accounting anchor,
    parent_thread_id for traceability).
    """
    scratch = scratch_dir_for_task(task_id)

    if inputs:
        inputs_block = "Inputs (structured context):\n" + json.dumps(
            inputs, indent=2, default=str
        )
    else:
        inputs_block = "Inputs: none. (no structured inputs)"

    envelope = (
        "=== Envelope (framework-injected) ===\n"
        f"task_id: {task_id}\n"
        f"parent_thread_id: {parent_thread_id}\n"
        f"scratch_dir: {scratch}\n"
        f"Accounting anchor: {accounting_date}"
    )

    body = (
        "=== Orchestrator brief ===\n"
        f"{prompt}\n\n"
        f"=== {inputs_block} ===\n\n"
        f"{envelope}"
    )
    return HumanMessage(content=body)
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_unit.py::test_compose_task_brief_includes_envelope tests/test_async_agents_unit.py::test_compose_task_brief_handles_missing_inputs -v
```

Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add backend/app/services/async_agents/runner.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): compose_task_brief HumanMessage builder"
```

### Task 8: `start_async_agent_task` and concurrency cap

**Files:**
- Modify: `backend/app/services/async_agents/runner.py`
- Modify: `backend/app/services/async_agents/__init__.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 8.1: Write the failing tests**

Append to `tests/test_async_agents_unit.py`:

```python
def test_start_async_agent_task_creates_taskrun_row(session, agent_thread_factory):
    """start_async_agent_task inserts a QUEUED TaskRun with kind=async_agent."""
    from app.models import TaskRun, TaskStatus
    from app.services.async_agents import runner

    thread = agent_thread_factory()
    task_id = runner.start_async_agent_task(
        session,
        parent_thread_id=thread.id,
        description="test draft",
        prompt="Test brief.",
        inputs={"x": 1},
        _submit=lambda *a, **k: None,  # no-op so we don't kick off the executor
    )
    session.commit()
    row = session.get(TaskRun, task_id)
    assert row is not None
    assert row.kind == "async_agent"
    assert row.status == TaskStatus.QUEUED.value
    assert row.parent_thread_id == thread.id
    assert row.description == "test draft"
    assert row.cancel_requested is False
    # prompt + inputs stored on TaskRun.message as JSON for runner._run pickup
    import json
    payload = json.loads(row.message)
    assert payload["prompt"] == "Test brief."
    assert payload["inputs"] == {"x": 1}


def test_start_async_agent_task_concurrency_cap_rejects(session, agent_thread_factory):
    """5th active dispatch on the same parent thread returns too_many_running."""
    from app.models import TaskRun, TaskStatus
    from app.services.async_agents import policy, runner

    thread = agent_thread_factory()
    for _ in range(policy.MAX_CONCURRENT_PER_THREAD):
        session.add(
            TaskRun(
                kind="async_agent",
                status=TaskStatus.RUNNING.value,
                parent_thread_id=thread.id,
                description="prior",
            )
        )
    session.commit()
    with pytest.raises(runner.TooManyRunningError):
        runner.start_async_agent_task(
            session,
            parent_thread_id=thread.id,
            description="overflow",
            prompt="...",
            inputs=None,
            _submit=lambda *a, **k: None,
        )
```

Add an `import pytest` to the top of `tests/test_async_agents_unit.py` if not present.

If `agent_thread_factory` is not a pre-existing fixture in `tests/conftest.py`, add one:

```python
# In tests/conftest.py (append if not present)
@pytest.fixture
def agent_thread_factory(session):
    from app.models import AgentThread

    counter = {"n": 0}

    def make(title: str | None = None, character: str = "auto"):
        counter["n"] += 1
        thread = AgentThread(title=title or f"thread-{counter['n']}", character=character)
        session.add(thread)
        session.flush()
        return thread

    return make
```

- [ ] **Step 8.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_unit.py::test_start_async_agent_task_creates_taskrun_row -v
```

Expected: FAIL with `AttributeError: module 'app.services.async_agents.runner' has no attribute 'start_async_agent_task'`.

- [ ] **Step 8.3: Implement `start_async_agent_task` and `TooManyRunningError`**

Append to `backend/app/services/async_agents/runner.py`:

```python
from collections.abc import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import TaskRun, TaskStatus
from .policy import MAX_CONCURRENT_PER_THREAD


_ACTIVE_STATUSES = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)


class TooManyRunningError(Exception):
    """Raised when a thread's per-thread async-agent cap is exceeded."""

    def __init__(self, cap: int):
        super().__init__(
            f"This thread already has {cap} background agents in flight. "
            f"Cancel one or wait."
        )
        self.cap = cap


def _count_active_for_thread(session: Session, parent_thread_id: int) -> int:
    return (
        session.query(func.count(TaskRun.id))
        .filter(
            TaskRun.kind == "async_agent",
            TaskRun.parent_thread_id == parent_thread_id,
            TaskRun.status.in_(_ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )


def start_async_agent_task(
    session: Session,
    *,
    parent_thread_id: int,
    description: str,
    prompt: str,
    inputs: dict[str, Any] | None,
    _submit: Callable[..., Any] | None = None,
) -> int:
    """Insert a QUEUED TaskRun row and submit the runner to the thread pool.

    Returns the new task_id. Raises TooManyRunningError if the per-thread cap
    is exceeded. The `_submit` parameter is for test injection — defaults to
    task_runner.submit_async_task at runtime.
    """
    active = _count_active_for_thread(session, parent_thread_id)
    if active >= MAX_CONCURRENT_PER_THREAD:
        raise TooManyRunningError(MAX_CONCURRENT_PER_THREAD)

    payload = {"prompt": prompt, "inputs": inputs or {}}
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.QUEUED.value,
        parent_thread_id=parent_thread_id,
        description=description[:120],
        message=json.dumps(payload, default=str),
    )
    session.add(row)
    session.flush()

    submit = _submit
    if submit is None:
        from ..task_runner import submit_async_task

        submit = submit_async_task
    submit(_run, row.id)
    return row.id


def _run(task_id: int) -> None:
    """Stub — replaced in Task 11."""
    raise NotImplementedError("_run is defined in Task 11")
```

Update `backend/app/services/async_agents/__init__.py` exports:

```python
from .runner import TooManyRunningError, compose_task_brief, start_async_agent_task

__all__ += ["TooManyRunningError", "compose_task_brief", "start_async_agent_task"]
```

- [ ] **Step 8.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: all PASS.

- [ ] **Step 8.5: Commit**

```bash
git add backend/app/services/async_agents/runner.py \
        backend/app/services/async_agents/__init__.py \
        tests/test_async_agents_unit.py \
        tests/conftest.py
git commit -m "feat(async-subagents): start_async_agent_task with concurrency cap"
```

### Task 9: `bubble_up.handle` — interrupt projection

**Files:**
- Create: `backend/app/services/async_agents/bubble_up.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 9.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_bubble_up_writes_pending_action_message(session, agent_thread_factory):
    """A subagent Interrupt projects to a parent-thread AgentMessage."""
    from app.models import AgentMessage, TaskRun
    from app.services.async_agents import bubble_up
    from langgraph.types import Interrupt

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="bubble test",
    )
    session.add(row)
    session.flush()

    intr = Interrupt(
        value={
            "action_requests": [
                {
                    "name": "create_report",
                    "args": {"portfolio_id": 1},
                    "description": "Create a report",
                }
            ]
        },
        id="intr-1",
        resumable=True,
        when="during",
    )
    bubble_up.handle(session, task_id=row.id, interrupts=[intr])
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.role == "assistant"
    assert msg.character == "async_agent"
    assert msg.meta["agent_phase"] == "awaiting_confirmation"
    assert msg.meta["async_task_id"] == row.id
    pending = msg.meta["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "create_report"
    assert pending[0]["async_task_id"] == row.id
    assert pending[0]["persona"] == f"async:{row.id}"
```

- [ ] **Step 9.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_bubble_up_writes_pending_action_message -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 9.3: Implement `bubble_up.py`**

Create `backend/app/services/async_agents/bubble_up.py`:

```python
"""Project subagent Interrupts into parent-thread AgentMessages."""
from __future__ import annotations

from typing import Any

from langgraph.types import Interrupt
from sqlalchemy.orm import Session

from ...models import AgentMessage, TaskRun
from ...schemas import AgentActionProposal
from ..audit import record_audit
from ..deep_agent.hitl import pending_actions_from_interrupts


def handle(
    session: Session,
    *,
    task_id: int,
    interrupts: list[Interrupt],
) -> AgentMessage:
    """Write an awaiting_confirmation AgentMessage on the parent thread."""
    task = session.get(TaskRun, task_id)
    if task is None or task.parent_thread_id is None:
        raise ValueError(f"async_agent task {task_id} not found or has no parent thread")

    proposals: list[AgentActionProposal] = pending_actions_from_interrupts(
        interrupts, persona=f"async:{task_id}"
    )
    # Attach async_task_id to each proposal.
    pending_dicts: list[dict[str, Any]] = []
    for proposal in proposals:
        d = proposal.model_dump(mode="json")
        d["async_task_id"] = task_id
        pending_dicts.append(d)

    description = task.description or f"task #{task_id}"
    first_tool = pending_dicts[0]["tool_name"] if pending_dicts else "unknown"
    msg = AgentMessage(
        thread_id=task.parent_thread_id,
        role="assistant",
        character="async_agent",
        content=(
            f"Background task '{description}' wants approval for "
            f"{first_tool}."
        ),
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "awaiting_confirmation",
            "async_task_id": task_id,
            "pending_actions": pending_dicts,
            "interrupt_ids": [intr.id for intr in interrupts],
        },
    )
    session.add(msg)
    # Keep task running; mark sub-status via task.message.
    task.message = "awaiting approval"
    session.flush()

    record_audit(
        session,
        event_type="async_agent.awaiting_approval",
        actor="system",
        subject_type="thread",
        subject_id=task.parent_thread_id,
        payload={
            "task_id": task_id,
            "tool_name": first_tool,
            "interrupt_id": interrupts[0].id if interrupts else None,
        },
    )
    return msg
```

- [ ] **Step 9.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_bubble_up_writes_pending_action_message -v
```

Expected: PASS.

- [ ] **Step 9.5: Commit**

```bash
git add backend/app/services/async_agents/bubble_up.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): bubble_up.handle projects interrupts to parent thread"
```

### Task 10: `autopost.handle` — completion auto-post + artifact materialization

**Files:**
- Create: `backend/app/services/async_agents/autopost.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 10.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_autopost_writes_completion_message_and_materializes_artifacts(
    session, agent_thread_factory, tmp_path, monkeypatch
):
    """autopost.handle writes the final message + materializes scratch files."""
    from app.models import AgentMessage, TaskRun
    from app.services.async_agents import autopost
    from app.config import get_settings

    # Redirect artifact_dir to tmp_path
    settings = get_settings()
    monkeypatch.setattr(settings, "artifact_dir", tmp_path, raising=False)

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="autopost test",
    )
    session.add(row)
    session.flush()

    # Simulate the subagent's state at completion
    from langchain_core.messages import AIMessage

    state_values = {
        "messages": [AIMessage(content="Headline.\n\n- finding 1\n- finding 2")],
        "files": {
            f"/trading_desk/async/{row.id}/note.md": "# Note\nhello",
        },
    }
    autopost.handle(session, task_id=row.id, state_values=state_values)
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.role == "assistant"
    assert msg.character == "async_agent"
    assert msg.meta["agent_phase"] == "completed"
    assert msg.meta["async_task_id"] == row.id
    assert "Headline." in msg.content

    # Materialized
    materialized = tmp_path / "agent" / f"thread-{thread.id}" / f"async-{row.id}" / "note.md"
    assert materialized.exists()
    assert "hello" in materialized.read_text()

    # Asset link in meta
    assets = msg.meta.get("assets", [])
    assert any(
        a.get("url", "").endswith(f"async-{row.id}/note.md") for a in assets
    )
```

- [ ] **Step 10.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_autopost_writes_completion_message_and_materializes_artifacts -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 10.3: Implement `autopost.py`**

Create `backend/app/services/async_agents/autopost.py`:

```python
"""Auto-post completion: read subagent state, write parent-thread message,
materialize scratch artifacts to disk."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import AgentMessage, TaskRun, TaskStatus
from ..audit import record_audit
from ..agents import _extract_final_ai_text


def _materialize_assets(
    files: dict[str, Any] | None,
    *,
    artifact_dir: Path,
    thread_id: int,
    task_id: int,
) -> list[dict[str, Any]]:
    """Materialize /trading_desk/async/<task_id>/** files to disk."""
    if not isinstance(files, dict):
        return []
    prefix = f"/trading_desk/async/{task_id}/"
    assets: list[dict[str, Any]] = []
    for virtual_path, file_data in sorted(files.items()):
        if not isinstance(virtual_path, str) or not virtual_path.startswith(prefix):
            continue
        content = file_data if isinstance(file_data, str) else (
            (file_data or {}).get("content") if isinstance(file_data, dict) else None
        )
        if not isinstance(content, str):
            continue
        relative = virtual_path[len(prefix):]
        target = (
            artifact_dir / "agent" / f"thread-{thread_id}" / f"async-{task_id}" / relative
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        url = f"/api/artifacts/agent/thread-{thread_id}/async-{task_id}/{relative}"
        assets.append(
            {
                "id": f"agent-async-{task_id}-" + relative.replace("/", "-"),
                "kind": _kind(relative),
                "title": PurePosixPath(relative).name,
                "mime_type": _mime(relative),
                "url": url,
                "path": virtual_path,
                "metadata": {"virtual_path": virtual_path, "artifact_path": str(target)},
            }
        )
    return assets


def _kind(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix == ".json":
        return "json"
    return "file"


def _mime(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    return {".html": "text/html", ".htm": "text/html", ".md": "text/markdown", ".json": "application/json"}.get(suffix)


def handle(session: Session, *, task_id: int, state_values: dict[str, Any]) -> AgentMessage:
    """Write a completed AgentMessage on the parent thread + materialize assets."""
    task = session.get(TaskRun, task_id)
    if task is None or task.parent_thread_id is None:
        raise ValueError(f"async_agent task {task_id} not found")

    settings = get_settings()
    final_text = _extract_final_ai_text(state_values) or "(no response)"
    assets = _materialize_assets(
        state_values.get("files"),
        artifact_dir=Path(settings.artifact_dir),
        thread_id=task.parent_thread_id,
        task_id=task_id,
    )
    msg = AgentMessage(
        thread_id=task.parent_thread_id,
        role="assistant",
        character="async_agent",
        content=final_text,
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "completed",
            "async_task_id": task_id,
            "description": task.description,
            "assets": assets,
        },
    )
    session.add(msg)

    # Update TaskRun
    task.status = TaskStatus.COMPLETED.value
    task.result_payload = {"final_text": final_text, "asset_count": len(assets)}

    record_audit(
        session,
        event_type="async_agent.completed",
        actor="system",
        subject_type="thread",
        subject_id=task.parent_thread_id,
        payload={"task_id": task_id, "asset_count": len(assets)},
    )
    session.flush()
    return msg
```

- [ ] **Step 10.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_autopost_writes_completion_message_and_materializes_artifacts -v
```

Expected: PASS.

- [ ] **Step 10.5: Commit**

```bash
git add backend/app/services/async_agents/autopost.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): autopost.handle writes completion + materializes assets"
```

### Task 11: `_run` body + `resume.py` + stale-recovery extension

**Files:**
- Modify: `backend/app/services/async_agents/runner.py`
- Create: `backend/app/services/async_agents/resume.py`
- Modify: `backend/app/services/task_runner.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 11.1: Write the failing tests**

Append to `tests/test_async_agents_unit.py`:

```python
def test_stale_recovery_marks_async_agent_failed_and_posts_message(
    session, agent_thread_factory
):
    from app.models import AgentMessage, TaskRun, TaskStatus
    from app.services.task_runner import mark_stale_tasks_failed

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="stale test",
    )
    session.add(row)
    session.flush()

    count = mark_stale_tasks_failed(session)
    session.commit()
    assert count >= 1
    refreshed = session.get(TaskRun, row.id)
    assert refreshed.status == TaskStatus.FAILED.value

    # The recovery message exists on the parent thread.
    posted = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .all()
    )
    matched = [
        m
        for m in posted
        if (m.meta or {}).get("async_task_id") == row.id
        and (m.meta or {}).get("agent_phase") == "error"
    ]
    assert len(matched) == 1
    assert "interrupted by server restart" in matched[0].content.lower()


def test_resume_async_agent_interrupt_uses_subagent_thread_id(
    session, agent_thread_factory, monkeypatch
):
    """The resume Command targets thread_id 'async:<parent>:<task>'."""
    from app.models import TaskRun
    from app.services.async_agents import resume as resume_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="resume test",
    )
    session.add(row)
    session.flush()
    session.commit()

    captured: dict = {}

    def fake_submit(fn, *args):
        captured["fn"] = fn
        captured["args"] = args
        return None

    monkeypatch.setattr(resume_mod, "_submit", fake_submit)

    resume_mod.resume_async_agent_interrupt(
        task_id=row.id, decision="approve", message=None, _session_factory=lambda: session
    )
    assert "fn" in captured
    # _submit was given (_resume_run, task_id, "approve", None)
    assert captured["args"][0] == row.id
    assert captured["args"][1] == "approve"
```

- [ ] **Step 11.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_unit.py::test_stale_recovery_marks_async_agent_failed_and_posts_message -v
```

Expected: FAIL.

- [ ] **Step 11.3: Extend `task_runner.mark_stale_tasks_failed`**

In `backend/app/services/task_runner.py`, locate the existing `mark_stale_tasks_failed(session: Session) -> int` (around line 59 per the spec). After the existing loop that sets task.status/error/finished_at, append:

```python
    # Post async-agent stale-recovery messages
    from ..models import AgentMessage  # local import to avoid cycle

    for task in tasks:
        if task.kind != "async_agent" or task.parent_thread_id is None:
            continue
        msg = AgentMessage(
            thread_id=task.parent_thread_id,
            role="assistant",
            character="async_agent",
            content=(
                f"Background task '{task.description or 'unnamed'}' was "
                f"interrupted by server restart. Re-dispatch if still needed."
            ),
            meta={
                "agent_graph": "async_agent",
                "agent_phase": "error",
                "async_task_id": task.id,
            },
        )
        session.add(msg)
```

Make sure the existing function signature and final `session.flush()` / `return len(tasks)` remain intact.

- [ ] **Step 11.4: Create `resume.py`**

Create `backend/app/services/async_agents/resume.py`:

```python
"""Resume an async-agent run after a HITL approve/reject decision."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from ... import database as _database
from ...models import TaskRun
from ..deep_agent.hitl import build_resume_command
from ..task_runner import submit_async_task

# Indirection points so tests can monkeypatch
_submit = submit_async_task


def _default_session_factory() -> Session:
    return _database.SessionLocal()


def resume_async_agent_interrupt(
    *,
    task_id: int,
    decision: str,
    message: str | None,
    _session_factory: Callable[[], Session] = _default_session_factory,
) -> None:
    """Build a resume Command and submit the resume_run to the thread pool."""
    with _scope(_session_factory()) as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            raise ValueError(f"async_agent task {task_id} not found")
        if task.kind != "async_agent":
            raise ValueError(f"task {task_id} is not an async_agent")
    _submit(_resume_run, task_id, decision, message)


@contextmanager
def _scope(session: Session):
    try:
        yield session
    finally:
        session.close()


def _resume_run(task_id: int, decision: str, message: str | None) -> None:
    """Re-build the async agent and ainvoke with Command(resume=...).

    Stub for now — replaced when end-to-end runner integration is wired in
    Task 15. For Phase 3 we only need the submission to be reachable and
    parameterized correctly.
    """
    # Body completed in Task 15.
    raise NotImplementedError("_resume_run is wired up in Task 15")
```

- [ ] **Step 11.5: Update `_run` placeholder in `runner.py`**

Replace the existing `_run` stub at the bottom of `backend/app/services/async_agents/runner.py` with the full body:

```python
def _run(task_id: int) -> None:
    """Worker entry point: build agent, invoke, dispatch to bubble_up/autopost."""
    import asyncio

    asyncio.run(_run_async(task_id))


async def _run_async(task_id: int) -> None:
    from ...config import get_settings
    from ...database import SessionLocal
    from ...models import TaskRun, TaskStatus
    from ..deep_agent.channel_registry import get_registry
    from ..deep_agent.checkpointer import build_async_checkpointer
    from ..deep_agent.model_factory import build_agent_model
    from ..langchain_tools import QUANT_AGENT_TOOLS
    from ..task_runner import mark_task_finished, mark_task_running
    from .agent import build_async_agent
    from .autopost import handle as autopost_handle
    from .bubble_up import handle as bubble_up_handle

    # Phase 1: load and mark running
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            return
        if task.cancel_requested:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="cancelled before start",
            )
            session.commit()
            return
        payload = json.loads(task.message or "{}")
        parent_thread_id = task.parent_thread_id
        description = task.description or "async task"
        mark_task_running(session, task_id, message=f"running: {description}")
        session.commit()

    settings = get_settings()
    registry = get_registry()
    model = build_agent_model(registry)
    if model is None:
        with SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="no LLM channel configured",
            )
            session.commit()
        return

    # Phase 2: build agent and invoke
    from datetime import datetime

    async with build_async_checkpointer(settings) as checkpointer:
        agent = build_async_agent(
            model=model,
            tools=QUANT_AGENT_TOOLS,
            checkpointer=checkpointer,
            task_id=task_id,
        )
        brief = compose_task_brief(
            task_id=task_id,
            parent_thread_id=parent_thread_id,
            prompt=payload.get("prompt", ""),
            inputs=payload.get("inputs"),
            accounting_date=datetime.utcnow().date().isoformat(),
        )
        config = {
            "configurable": {"thread_id": f"async:{parent_thread_id}:{task_id}"}
        }
        try:
            await agent.ainvoke({"messages": [brief]}, config=config)
        except Exception as exc:
            with SessionLocal() as session:
                mark_task_finished(
                    session,
                    task_id,
                    status=TaskStatus.FAILED.value,
                    message=str(exc)[:500],
                )
                session.commit()
            return

        # Phase 3: inspect state for interrupts or completion
        state = await agent.aget_state(config)

    interrupts: list[Any] = []
    if state and getattr(state, "tasks", None):
        for t in state.tasks:
            interrupts.extend(getattr(t, "interrupts", []) or [])

    with SessionLocal() as session:
        if interrupts:
            bubble_up_handle(session, task_id=task_id, interrupts=interrupts)
            session.commit()
            return
        autopost_handle(
            session, task_id=task_id, state_values=getattr(state, "values", {}) or {}
        )
        session.commit()
```

- [ ] **Step 11.6: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: all PASS. The new `_run` body is not directly tested here — it's covered by the integration test in Task 21.

- [ ] **Step 11.7: Commit**

```bash
git add backend/app/services/async_agents/runner.py \
        backend/app/services/async_agents/resume.py \
        backend/app/services/async_agents/__init__.py \
        backend/app/services/task_runner.py \
        tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): _run, resume, and stale-recovery message"
```

Also update `backend/app/services/async_agents/__init__.py` to export `resume_async_agent_interrupt`:

```python
from .resume import resume_async_agent_interrupt

__all__ += ["resume_async_agent_interrupt"]
```

(Include this in the same commit if missed.)

---

## Phase 4 — Schemas and endpoints (Tasks 12-13)

### Task 12: Schemas — `AsyncAgentTaskOut`, `AsyncAgentStartIn`, `AgentActionProposal.async_task_id`

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 12.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_agent_action_proposal_has_async_task_id_field():
    """AgentActionProposal accepts and serializes async_task_id."""
    from app.schemas import AgentActionProposal

    p = AgentActionProposal(
        id="x:0",
        tool_name="create_report",
        label="Create report",
        summary="...",
        payload={},
        requires_confirmation=True,
        status="pending",
        async_task_id=42,
    )
    dumped = p.model_dump(mode="json")
    assert dumped["async_task_id"] == 42


def test_async_agent_schemas_exist():
    from app.schemas import AsyncAgentStartIn, AsyncAgentTaskOut

    out = AsyncAgentTaskOut(
        task_id=1,
        description="d",
        status="running",
        awaiting_approval=False,
        started_at=None,
        finished_at=None,
        last_message_preview=None,
    )
    assert out.task_id == 1

    incoming = AsyncAgentStartIn(
        description="d",
        prompt="p",
        inputs={"x": 1},
    )
    assert incoming.inputs == {"x": 1}
```

- [ ] **Step 12.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_unit.py::test_agent_action_proposal_has_async_task_id_field tests/test_async_agents_unit.py::test_async_agent_schemas_exist -v
```

Expected: FAIL.

- [ ] **Step 12.3: Edit `backend/app/schemas.py`**

Locate `class AgentActionProposal(BaseModel):` and add the optional field (place it near the other optional fields like `persona`):

```python
    async_task_id: int | None = None
```

Append two new models at the end of the file (or beside other Agent* models):

```python
class AsyncAgentStartIn(BaseModel):
    """Input payload for start_async_agent tool (also usable as API DTO)."""

    description: str = Field(..., max_length=120)
    prompt: str = Field(..., max_length=8000)
    inputs: dict[str, Any] | None = None


class AsyncAgentTaskOut(BaseModel):
    """Single async-agent task summary for list/list-API."""

    task_id: int
    description: str
    status: str
    awaiting_approval: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_message_preview: str | None = None
```

Ensure `datetime`, `Any`, `BaseModel`, `Field` are imported at the top of the file.

- [ ] **Step 12.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_unit.py -v
```

Expected: PASS.

- [ ] **Step 12.5: Commit**

```bash
git add backend/app/schemas.py tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): AsyncAgent schemas and async_task_id field"
```

### Task 13: Resume endpoint routing + `GET /async_agents`

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/agents.py` (add `AgentService.resume_async_agent`)
- Test: `tests/test_async_agents_hitl.py` (new)

- [ ] **Step 13.1: Write the failing test**

Create `tests/test_async_agents_hitl.py`:

```python
"""HITL bubble-up and endpoint routing tests."""
from __future__ import annotations

import pytest

from app.models import AgentMessage, TaskRun, TaskStatus


def test_resume_endpoint_routes_to_async_when_task_id_present(
    client, session, agent_thread_factory, monkeypatch
):
    """POST /api/threads/.../resume with async_task_id calls AgentService.resume_async_agent."""
    from app.services import agents as agents_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="route test",
    )
    session.add(row)
    # Create an awaiting message so the action proposal id resolves
    msg = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        character="async_agent",
        content="approve please",
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "awaiting_confirmation",
            "async_task_id": row.id,
            "pending_actions": [
                {
                    "id": "intr-x:0",
                    "tool_name": "create_report",
                    "label": "Create report",
                    "summary": "",
                    "payload": {},
                    "requires_confirmation": True,
                    "status": "pending",
                    "async_task_id": row.id,
                }
            ],
        },
    )
    session.add(msg)
    session.commit()

    called = {}

    def fake_resume(self, task_id, decision, message):
        called["task_id"] = task_id
        called["decision"] = decision
        called["message"] = message
        return None

    monkeypatch.setattr(
        agents_mod.AgentService, "resume_async_agent", fake_resume, raising=False
    )

    resp = client.post(
        f"/api/threads/{thread.id}/resume",
        json={
            "decision": "approve",
            "pending_action_id": "intr-x:0",
            "async_task_id": row.id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "async_agent"
    assert body["task_id"] == row.id
    assert called["task_id"] == row.id
    assert called["decision"] == "approve"


def test_list_async_agents_endpoint_returns_active_tasks(
    client, session, agent_thread_factory
):
    thread = agent_thread_factory()
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.RUNNING.value,
            parent_thread_id=thread.id,
            description="active",
        )
    )
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.COMPLETED.value,
            parent_thread_id=thread.id,
            description="done",
        )
    )
    session.commit()

    resp = client.get(f"/api/threads/{thread.id}/async_agents")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "active"

    resp_all = client.get(
        f"/api/threads/{thread.id}/async_agents?include_terminal=true"
    )
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 2
```

- [ ] **Step 13.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_hitl.py::test_resume_endpoint_routes_to_async_when_task_id_present -v
```

Expected: FAIL — endpoint doesn't yet inspect `async_task_id`.

- [ ] **Step 13.3: Add `AgentService.resume_async_agent`**

In `backend/app/services/agents.py`, add a method on `AgentService` (near `invoke_resume`):

```python
    def resume_async_agent(
        self,
        task_id: int,
        decision: str,
        message: str | None,
    ) -> None:
        from .async_agents.resume import resume_async_agent_interrupt

        resume_async_agent_interrupt(
            task_id=task_id, decision=decision, message=message
        )
```

- [ ] **Step 13.4: Update the resume endpoint in `backend/app/main.py`**

Find the existing resume endpoint (search for `def resume` or `pending_action_id`). Extend the request model to accept `async_task_id: int | None = None`. In the handler, branch:

```python
@app.post("/api/threads/{thread_id}/resume")
def resume_thread(thread_id: int, body: ResumeRequest, ...):
    if body.async_task_id is not None:
        agent_service.resume_async_agent(
            body.async_task_id, body.decision, body.message
        )
        return {
            "message_id": None,
            "routed_to": "async_agent",
            "task_id": body.async_task_id,
        }
    # ... existing legacy path unchanged
```

Add the new endpoint after it:

```python
@app.get("/api/threads/{thread_id}/async_agents")
def list_thread_async_agents(
    thread_id: int,
    include_terminal: bool = False,
    limit: int = 20,
    session: Session = Depends(get_db),
) -> list[AsyncAgentTaskOut]:
    from app.services.async_agents.tools import build_task_summaries

    return build_task_summaries(
        session,
        parent_thread_id=thread_id,
        include_terminal=include_terminal,
        limit=limit,
    )
```

(`build_task_summaries` is introduced in Task 14; the endpoint can be wired now, and the test for `test_list_async_agents_endpoint_returns_active_tasks` will fail until then. Mark that test `@pytest.mark.skip(reason="wired in Task 14")` for now, or temporarily inline a minimal query — see Step 13.5.)

For Phase 4 self-containedness, inline a minimal query in `main.py` instead of depending on `tools.build_task_summaries`:

```python
@app.get("/api/threads/{thread_id}/async_agents")
def list_thread_async_agents(
    thread_id: int,
    include_terminal: bool = False,
    limit: int = 20,
    session: Session = Depends(get_db),
):
    from app.models import TaskRun, TaskStatus

    active_statuses = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
    q = session.query(TaskRun).filter(
        TaskRun.kind == "async_agent",
        TaskRun.parent_thread_id == thread_id,
    )
    if not include_terminal:
        q = q.filter(TaskRun.status.in_(active_statuses))
    rows = q.order_by(TaskRun.started_at.desc().nulls_last()).limit(limit).all()
    return [
        {
            "task_id": row.id,
            "description": row.description or "",
            "status": row.status,
            "awaiting_approval": (row.message or "") == "awaiting approval",
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "last_message_preview": None,
        }
        for row in rows
    ]
```

(Task 14 will refactor to delegate to `tools.build_task_summaries`.)

Also ensure the `ResumeRequest` model has `pending_action_id: str` and `async_task_id: int | None = None`. If the existing model doesn't have `pending_action_id`, the resume route may already use positional `decision` only — keep compatibility by adding the new optional field while preserving existing required ones.

- [ ] **Step 13.5: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_hitl.py -v
```

Expected: PASS.

- [ ] **Step 13.6: Commit**

```bash
git add backend/app/main.py backend/app/services/agents.py tests/test_async_agents_hitl.py
git commit -m "feat(async-subagents): resume endpoint routing + GET /async_agents"
```

---

## Phase 5 — Tools registration (Tasks 14-15)

### Task 14: Three `BaseTool` classes — `start_async_agent`, `list_async_agents`, `cancel_async_agent`

**Files:**
- Create: `backend/app/services/async_agents/tools.py`
- Test: `tests/test_async_agents_tools.py` (new)

- [ ] **Step 14.1: Write the failing tests**

Create `tests/test_async_agents_tools.py`:

```python
"""Tool-surface tests for the three orchestrator tools."""
from __future__ import annotations

import pytest

from app.models import TaskRun, TaskStatus


@pytest.fixture
def tool_config_for_thread(agent_thread_factory):
    def make(**overrides):
        thread = agent_thread_factory()
        return {
            "configurable": {"thread_id": str(thread.id)},
            "_thread": thread,
            **overrides,
        }

    return make


def test_start_async_agent_returns_task_id(session, tool_config_for_thread, monkeypatch):
    from app.services.async_agents import runner, tools

    cfg = tool_config_for_thread()
    # Replace _submit so the executor doesn't actually run
    monkeypatch.setattr(runner, "_run", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task",
        lambda *a, **k: None,
        raising=False,
    )
    tool = tools.StartAsyncAgentTool()
    result = tool.invoke(
        {"description": "test", "prompt": "do a thing", "inputs": {"a": 1}}, config=cfg
    )
    assert result["ok"] is True
    assert isinstance(result["task_id"], int)


def test_start_async_agent_rejects_at_concurrency_cap(
    session, tool_config_for_thread, monkeypatch
):
    from app.services.async_agents import policy, tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    for _ in range(policy.MAX_CONCURRENT_PER_THREAD):
        session.add(
            TaskRun(
                kind="async_agent",
                status=TaskStatus.RUNNING.value,
                parent_thread_id=thread.id,
                description="prior",
            )
        )
    session.commit()
    tool = tools.StartAsyncAgentTool()
    result = tool.invoke(
        {"description": "overflow", "prompt": "..."}, config=cfg
    )
    assert result["ok"] is False
    assert result["error"] == "too_many_running"


def test_list_async_agents_excludes_terminal_by_default(
    session, tool_config_for_thread
):
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.RUNNING.value,
            parent_thread_id=thread.id,
            description="active",
        )
    )
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.COMPLETED.value,
            parent_thread_id=thread.id,
            description="done",
        )
    )
    session.commit()
    tool = tools.ListAsyncAgentsTool()
    result = tool.invoke({}, config=cfg)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["description"] == "active"


def test_cancel_async_agent_flags_running(
    session, tool_config_for_thread
):
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="cancel me",
    )
    session.add(row)
    session.commit()
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is True
    assert result["previous_status"] == TaskStatus.RUNNING.value
    assert result["new_status"] == TaskStatus.RUNNING.value
    session.refresh(row)
    assert row.cancel_requested is True


def test_cancel_async_agent_refuses_other_threads_task(
    session, tool_config_for_thread, agent_thread_factory
):
    from app.services.async_agents import tools

    other_thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=other_thread.id,
        description="not yours",
    )
    session.add(row)
    session.commit()

    cfg = tool_config_for_thread()  # different parent thread
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is False
    assert result["error"] == "not_owned"
```

- [ ] **Step 14.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_tools.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 14.3: Create `tools.py`**

Create `backend/app/services/async_agents/tools.py`:

```python
"""Three LangChain BaseTool subclasses for orchestrator dispatch.

Each tool resolves parent_thread_id from RunnableConfig.configurable.thread_id.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ... import database as _database
from ...models import TaskRun, TaskStatus
from ..audit import record_audit
from .runner import TooManyRunningError, start_async_agent_task


_ACTIVE = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
_TERMINAL = (
    TaskStatus.COMPLETED.value,
    TaskStatus.COMPLETED_WITH_ERRORS.value,
    TaskStatus.FAILED.value,
)


def _resolve_parent_thread_id(config: dict[str, Any] | None) -> int | None:
    if not config:
        return None
    raw = (config.get("configurable") or {}).get("thread_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_task_summaries(
    session,
    *,
    parent_thread_id: int,
    include_terminal: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Shared query used by both the tool and the API endpoint."""
    q = session.query(TaskRun).filter(
        TaskRun.kind == "async_agent",
        TaskRun.parent_thread_id == parent_thread_id,
    )
    if not include_terminal:
        q = q.filter(TaskRun.status.in_(_ACTIVE))
    rows = q.order_by(TaskRun.started_at.desc().nulls_last()).limit(limit).all()
    return [
        {
            "task_id": row.id,
            "description": row.description or "",
            "status": row.status,
            "awaiting_approval": (row.message or "") == "awaiting approval",
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "last_message_preview": _preview(row),
        }
        for row in rows
    ]


def _preview(row: TaskRun) -> str | None:
    payload = row.result_payload or {}
    text = payload.get("final_text")
    if isinstance(text, str):
        return text[:120]
    return None


class StartAsyncAgentInput(BaseModel):
    description: str = Field(..., max_length=80)
    prompt: str = Field(..., max_length=8000)
    inputs: dict[str, Any] | None = None


class StartAsyncAgentTool(BaseTool):
    name: str = "start_async_agent"
    description: str = (
        "Spawn a background general-purpose agent with a self-contained brief. "
        "Returns a task_id. The agent runs in parallel with the chat; its "
        "result auto-posts as a new assistant message in this thread."
    )
    args_schema: type[BaseModel] = StartAsyncAgentInput

    def _run(
        self,
        description: str,
        prompt: str,
        inputs: dict[str, Any] | None = None,
        *,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {"ok": False, "error": "no_parent_thread", "message": "thread_id missing from config"}

        with _database.SessionLocal() as session:
            try:
                task_id = start_async_agent_task(
                    session,
                    parent_thread_id=parent_thread_id,
                    description=description,
                    prompt=prompt,
                    inputs=inputs,
                )
            except TooManyRunningError as exc:
                session.rollback()
                return {
                    "ok": False,
                    "error": "too_many_running",
                    "message": str(exc),
                }
            record_audit(
                session,
                event_type="async_agent.started",
                actor="desk_user",
                subject_type="thread",
                subject_id=parent_thread_id,
                payload={
                    "task_id": task_id,
                    "description": description,
                    "proxy_fired": _infer_proxy(prompt, inputs),
                },
            )
            session.commit()
        return {"ok": True, "task_id": task_id, "status": "queued"}

    async def _arun(self, *args, **kwargs):
        return self._run(*args, **kwargs)


def _infer_proxy(prompt: str, inputs: dict[str, Any] | None) -> str:
    """Heuristic tagging for audit observability."""
    lower = prompt.lower()
    written = any(
        kw in lower
        for kw in ("narrative", "summary", "audit", "comparison", "draft", "write")
    )
    parallel = any(
        kw in lower
        for kw in ("while", "and also", "in parallel", "in the background", "let me know when", "come back")
    )
    if written and parallel:
        return "multiple"
    if written:
        return "written_artifact"
    if parallel:
        return "user_signal"
    return "5plus_calls"


class ListAsyncAgentsInput(BaseModel):
    include_terminal: bool = False
    limit: int = Field(20, ge=1, le=100)


class ListAsyncAgentsTool(BaseTool):
    name: str = "list_async_agents"
    description: str = (
        "List background agents on this thread. By default only active "
        "(queued/running) tasks; set include_terminal=true to see completed/failed."
    )
    args_schema: type[BaseModel] = ListAsyncAgentsInput

    def _run(
        self,
        include_terminal: bool = False,
        limit: int = 20,
        *,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {"tasks": []}
        with _database.SessionLocal() as session:
            tasks = build_task_summaries(
                session,
                parent_thread_id=parent_thread_id,
                include_terminal=include_terminal,
                limit=limit,
            )
        return {"tasks": tasks}

    async def _arun(self, *args, **kwargs):
        return self._run(*args, **kwargs)


class CancelAsyncAgentInput(BaseModel):
    task_id: int
    reason: str | None = Field(None, max_length=200)


class CancelAsyncAgentTool(BaseTool):
    name: str = "cancel_async_agent"
    description: str = "Stop a background agent. Best-effort; running tasks finish their current step."
    args_schema: type[BaseModel] = CancelAsyncAgentInput

    def _run(
        self,
        task_id: int,
        reason: str | None = None,
        *,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {"ok": False, "task_id": task_id, "error": "no_parent_thread"}

        with _database.SessionLocal() as session:
            row = session.get(TaskRun, task_id)
            if row is None or row.kind != "async_agent":
                return {"ok": False, "task_id": task_id, "error": "not_found"}
            if row.parent_thread_id != parent_thread_id:
                return {"ok": False, "task_id": task_id, "error": "not_owned"}
            prev = row.status
            if prev in _TERMINAL:
                return {
                    "ok": False,
                    "task_id": task_id,
                    "previous_status": prev,
                    "new_status": prev,
                    "note": "already terminal",
                }
            if prev == TaskStatus.QUEUED.value:
                row.status = TaskStatus.FAILED.value
                row.message = "cancelled before start"
                new_status = row.status
            else:
                row.cancel_requested = True
                new_status = prev
            record_audit(
                session,
                event_type="async_agent.cancelled",
                actor="desk_user",
                subject_type="thread",
                subject_id=parent_thread_id,
                payload={"task_id": task_id, "reason": reason},
            )
            session.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "previous_status": prev,
            "new_status": new_status,
        }

    async def _arun(self, *args, **kwargs):
        return self._run(*args, **kwargs)
```

- [ ] **Step 14.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_tools.py -v
```

Expected: PASS.

- [ ] **Step 14.5: Commit**

```bash
git add backend/app/services/async_agents/tools.py tests/test_async_agents_tools.py
git commit -m "feat(async-subagents): three BaseTool subclasses + build_task_summaries helper"
```

### Task 15: Register tools in `QUANT_AGENT_TOOLS` and `DEEP_AGENT_TOOL_NAMES`

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Modify: `backend/app/services/agents.py`
- Modify: `tests/test_async_agents_tools.py`

- [ ] **Step 15.1: Write the failing tests**

Append to `tests/test_async_agents_tools.py`:

```python
def test_quant_agent_tools_includes_three_async_tools():
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "start_async_agent" in names
    assert "list_async_agents" in names
    assert "cancel_async_agent" in names


def test_deep_agent_tool_names_includes_three_async_tools():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert "start_async_agent" in DEEP_AGENT_TOOL_NAMES
    assert "list_async_agents" in DEEP_AGENT_TOOL_NAMES
    assert "cancel_async_agent" in DEEP_AGENT_TOOL_NAMES


def test_async_tools_not_in_interrupt_names():
    """Dispatching is free; only subagent's tool calls bubble."""
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert "start_async_agent" not in INTERRUPT_TOOL_NAMES
    assert "list_async_agents" not in INTERRUPT_TOOL_NAMES
    assert "cancel_async_agent" not in INTERRUPT_TOOL_NAMES
```

- [ ] **Step 15.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_tools.py::test_quant_agent_tools_includes_three_async_tools -v
```

Expected: FAIL.

- [ ] **Step 15.3: Register the tools**

In `backend/app/services/langchain_tools.py`, find the `QUANT_AGENT_TOOLS = [` list (around line 1509). Just before the closing `]`, add three entries:

```python
    StartAsyncAgentTool(),
    ListAsyncAgentsTool(),
    CancelAsyncAgentTool(),
```

Add the matching import at the top of the file (with the other tool imports):

```python
from .async_agents.tools import (
    CancelAsyncAgentTool,
    ListAsyncAgentsTool,
    StartAsyncAgentTool,
)
```

In `backend/app/services/agents.py`, find `DEEP_AGENT_TOOL_NAMES` (around line 60). Add three entries:

```python
        "start_async_agent",
        "list_async_agents",
        "cancel_async_agent",
```

- [ ] **Step 15.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_tools.py -v
```

Expected: PASS.

- [ ] **Step 15.5: Commit**

```bash
git add backend/app/services/langchain_tools.py backend/app/services/agents.py tests/test_async_agents_tools.py
git commit -m "feat(async-subagents): register 3 tools in QUANT_AGENT_TOOLS and DEEP_AGENT_TOOL_NAMES"
```

---

## Phase 6 — Bubble-up integration (Task 16)

### Task 16: Wire `AgentService.resume_async_agent` end-to-end + add bubble-up integration test

**Files:**
- Modify: `backend/app/services/async_agents/resume.py` (replace the `_resume_run` stub)
- Modify: `tests/test_async_agents_hitl.py`

- [ ] **Step 16.1: Write the failing test**

Append to `tests/test_async_agents_hitl.py`:

```python
def test_multiple_bubble_ups_in_one_task_each_route_correctly(
    session, agent_thread_factory, monkeypatch
):
    """Two consecutive bubble-ups produce two awaiting messages, each tagged."""
    from app.models import AgentMessage
    from app.services.async_agents import bubble_up
    from langgraph.types import Interrupt

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="multi-bubble",
    )
    session.add(row)
    session.flush()

    def mk_interrupt(name, idx):
        return Interrupt(
            value={
                "action_requests": [
                    {"name": name, "args": {}, "description": f"{name} call"}
                ]
            },
            id=f"intr-{idx}",
            resumable=True,
            when="during",
        )

    bubble_up.handle(session, task_id=row.id, interrupts=[mk_interrupt("create_report", 1)])
    bubble_up.handle(session, task_id=row.id, interrupts=[mk_interrupt("price_positions", 2)])
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 2
    for msg in msgs:
        assert msg.meta["async_task_id"] == row.id
        assert msg.meta["agent_phase"] == "awaiting_confirmation"
    assert msgs[0].meta["pending_actions"][0]["tool_name"] == "create_report"
    assert msgs[1].meta["pending_actions"][0]["tool_name"] == "price_positions"


def test_resume_async_agent_routes_command_to_subagent_thread_id(
    session, agent_thread_factory, monkeypatch
):
    """When resume_async_agent_interrupt fires _resume_run, the
    LangGraph config uses 'async:<parent>:<task>' as thread_id."""
    from app.services.async_agents import resume as resume_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="route command",
    )
    session.add(row)
    session.commit()

    captured = {}

    async def fake_ainvoke(self, payload, config=None):
        captured["thread_id"] = (config or {}).get("configurable", {}).get("thread_id")
        return None

    async def fake_aget_state(self, config):
        class S:
            tasks = []
            values = {}

        return S()

    import app.services.async_agents.resume as r

    # We patch the agent factory used inside _resume_run to return a stub
    class StubAgent:
        ainvoke = fake_ainvoke
        aget_state = fake_aget_state

    monkeypatch.setattr(
        r, "_build_agent_for_resume", lambda **kw: StubAgent(), raising=False
    )
    # Also patch model + checkpointer factories to no-ops
    monkeypatch.setattr(
        "app.services.deep_agent.model_factory.build_agent_model",
        lambda *a, **k: object(),
    )

    import asyncio

    asyncio.run(r._resume_run_async(row.id, "approve", None))

    assert captured["thread_id"] == f"async:{thread.id}:{row.id}"
```

- [ ] **Step 16.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_async_agents_hitl.py::test_resume_async_agent_routes_command_to_subagent_thread_id -v
```

Expected: FAIL — `_resume_run_async` not implemented.

- [ ] **Step 16.3: Replace `_resume_run` body in `resume.py`**

In `backend/app/services/async_agents/resume.py`, replace the `_resume_run` stub:

```python
def _resume_run(task_id: int, decision: str, message: str | None) -> None:
    import asyncio

    asyncio.run(_resume_run_async(task_id, decision, message))


def _build_agent_for_resume(*, model: Any, tools: Any, checkpointer: Any, task_id: int) -> Any:
    """Factory hook isolated for test monkeypatch."""
    from .agent import build_async_agent

    return build_async_agent(
        model=model, tools=tools, checkpointer=checkpointer, task_id=task_id
    )


async def _resume_run_async(task_id: int, decision: str, message: str | None) -> None:
    from ...config import get_settings
    from ...database import SessionLocal
    from ...models import TaskRun
    from ..deep_agent.channel_registry import get_registry
    from ..deep_agent.checkpointer import build_async_checkpointer
    from ..deep_agent.hitl import build_resume_command
    from ..deep_agent.model_factory import build_agent_model
    from ..langchain_tools import QUANT_AGENT_TOOLS
    from .autopost import handle as autopost_handle
    from .bubble_up import handle as bubble_up_handle

    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            return
        parent_thread_id = task.parent_thread_id

    settings = get_settings()
    registry = get_registry()
    model = build_agent_model(registry)
    if model is None:
        return

    command = build_resume_command(decision, message=message)
    async with build_async_checkpointer(settings) as checkpointer:
        agent = _build_agent_for_resume(
            model=model, tools=QUANT_AGENT_TOOLS, checkpointer=checkpointer, task_id=task_id
        )
        config = {"configurable": {"thread_id": f"async:{parent_thread_id}:{task_id}"}}
        await agent.ainvoke(command, config=config)
        state = await agent.aget_state(config)

    interrupts: list[Any] = []
    if state and getattr(state, "tasks", None):
        for t in state.tasks:
            interrupts.extend(getattr(t, "interrupts", []) or [])

    with SessionLocal() as session:
        if interrupts:
            bubble_up_handle(session, task_id=task_id, interrupts=interrupts)
        else:
            autopost_handle(
                session,
                task_id=task_id,
                state_values=getattr(state, "values", {}) or {},
            )
        from ..audit import record_audit

        record_audit(
            session,
            event_type="async_agent.resumed",
            actor="desk_user",
            subject_type="thread",
            subject_id=parent_thread_id,
            payload={"task_id": task_id, "decision": decision},
        )
        session.commit()
```

- [ ] **Step 16.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_async_agents_hitl.py -v
```

Expected: PASS.

- [ ] **Step 16.5: Commit**

```bash
git add backend/app/services/async_agents/resume.py tests/test_async_agents_hitl.py
git commit -m "feat(async-subagents): _resume_run wires Command(resume=...) to subagent thread_id"
```

---

## Phase 7 — Frontend integration (Tasks 17-18)

### Task 17: `types.ts` — async types and `async_task_id`

**Files:**
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/components/FloatingAgentMiniChat.test.tsx` (extend)

- [ ] **Step 17.1: Add types**

In `frontend/src/types.ts`, locate the existing `AgentActionProposal` interface and add the optional field:

```ts
export interface AgentActionProposal {
  // ... existing fields ...
  async_task_id?: number | null;
}
```

Append two new types at the end:

```ts
export type AsyncAgentStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface AsyncAgentTaskOut {
  task_id: number;
  description: string;
  status: AsyncAgentStatus;
  awaiting_approval: boolean;
  started_at: string | null;
  finished_at: string | null;
  last_message_preview: string | null;
}
```

If `AgentMessageMeta.agent_phase` doesn't already accept `'awaiting_confirmation'`, leave it as already-existing in the file (grep confirmed it does).

- [ ] **Step 17.2: Write a frontend assertion test**

Append to `frontend/src/components/FloatingAgentMiniChat.test.tsx`:

```ts
import { describe, it, expect } from 'vitest';
import type { AgentActionProposal, AsyncAgentTaskOut } from '../types';

describe('async types', () => {
  it('AgentActionProposal accepts async_task_id', () => {
    const p: AgentActionProposal = {
      id: 'x:0',
      tool_name: 'create_report',
      label: 'Create',
      summary: '',
      payload: {},
      requires_confirmation: true,
      status: 'pending',
      async_task_id: 42,
    } as AgentActionProposal;
    expect(p.async_task_id).toBe(42);
  });

  it('AsyncAgentTaskOut shape is satisfied by an active task', () => {
    const t: AsyncAgentTaskOut = {
      task_id: 1,
      description: 'd',
      status: 'running',
      awaiting_approval: false,
      started_at: '2026-05-16T00:00:00Z',
      finished_at: null,
      last_message_preview: null,
    };
    expect(t.task_id).toBe(1);
  });
});
```

(Adjust the import of `AgentActionProposal` if existing required fields differ; the test compiles only if the types resolve.)

- [ ] **Step 17.3: Run frontend tests**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend
npx vitest run src/components/FloatingAgentMiniChat.test.tsx
```

Expected: PASS (including the new assertions).

- [ ] **Step 17.4: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add frontend/src/types.ts frontend/src/components/FloatingAgentMiniChat.test.tsx
git commit -m "feat(async-subagents): frontend types for async agent task + async_task_id"
```

### Task 18: Frontend rendering — `character="async_agent"` + bubble-up approve flow

**Files:**
- Modify: `frontend/src/components/FloatingAgentMiniChat.tsx`
- Modify: `frontend/src/hooks/useAgentChatController.ts` (only if needed)
- Modify: `frontend/src/components/ChatBubble.tsx` (only if needed)
- Modify: `frontend/src/routes/Tasks.tsx` and/or `Tasks.live.tsx`
- Test: `frontend/src/components/FloatingAgentMiniChat.test.tsx`

- [ ] **Step 18.1: Write the failing test**

Append to `frontend/src/components/FloatingAgentMiniChat.test.tsx`:

```ts
it('renders an awaiting_confirmation message from async_agent with approve/reject buttons', async () => {
  // Mount the component with a fake thread containing one awaiting message
  // whose pending action carries async_task_id, and assert:
  // - the approve button POSTs to /api/threads/<id>/resume with async_task_id in body
  // - the chat bubble shows a "Background" affordance for character="async_agent"
  //
  // Use the existing test harness in this file as a template for mounting +
  // intercepting fetch. The shape of the assertion is:
  //   expect(lastFetchCall.body).toMatchObject({ async_task_id: 42 });
  //   expect(screen.getByText(/Background/i)).toBeInTheDocument();
});
```

Fill in the test body using the patterns already established at the top of `FloatingAgentMiniChat.test.tsx` (look at the existing approve/reject test for shape — should be 30-50 lines).

- [ ] **Step 18.2: Run test to verify it fails**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend
npx vitest run src/components/FloatingAgentMiniChat.test.tsx
```

Expected: FAIL (button doesn't include `async_task_id` in the resume POST body).

- [ ] **Step 18.3: Edit `FloatingAgentMiniChat.tsx`**

Locate the approve/reject handler (search for `/resume` in the file). When constructing the POST body, include `async_task_id` from the pending action:

```ts
const asyncTaskId = pendingAction.async_task_id ?? null;
const body = {
  decision,
  pending_action_id: pendingAction.id,
  message: rejectionMessage ?? null,
  async_task_id: asyncTaskId,
};
fetch(`/api/threads/${threadId}/resume`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
```

In the bubble renderer (search for `character` usage), add a small affordance when `character === 'async_agent'`:

```tsx
{message.character === 'async_agent' && (
  <span className="message-badge message-badge--async">Background</span>
)}
```

Add a `.message-badge--async` style in the matching `.css` file (or a Tailwind class — match whatever convention the existing `.message-badge` uses).

- [ ] **Step 18.4: Edit `routes/Tasks.tsx` and `Tasks.live.tsx`**

In the existing task-list rendering, add a branch for `kind === "async_agent"`:

```tsx
if (task.kind === 'async_agent') {
  return (
    <TaskChip
      key={task.id}
      kind="async_agent"
      title={task.description ?? 'Background analyst'}
      status={task.status}
      onCancel={() => cancelTask(task.id)}
      viewInChatHref={`/agent?thread=${task.parent_thread_id}#async-${task.id}`}
    />
  );
}
```

(Adapt to the actual component names used in `Tasks.tsx` — `TaskChip` may be inline.)

- [ ] **Step 18.5: Run tests to verify they pass**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend
npx vitest run
```

Expected: PASS for the new test; existing tests still PASS.

- [ ] **Step 18.6: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add frontend/src/components/FloatingAgentMiniChat.tsx \
        frontend/src/components/FloatingAgentMiniChat.test.tsx \
        frontend/src/components/FloatingAgentMiniChat.css \
        frontend/src/routes/Tasks.tsx \
        frontend/src/routes/Tasks.live.tsx
git commit -m "feat(async-subagents): UI for async_agent bubble approve flow and task panel"
```

---

## Phase 8 — Activation (Tasks 19-21)

### Task 19: Cost-preview policy addendum

**Files:**
- Modify: `backend/app/services/deep_agent/skills/policy/cost-preview.md`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 19.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_cost_preview_fragment_includes_async_clause():
    from pathlib import Path
    import app.services.deep_agent as deep_pkg

    text = (
        Path(deep_pkg.__file__).parent / "skills" / "policy" / "cost-preview.md"
    ).read_text(encoding="utf-8")
    assert "When you have no user in your conversation" in text
    assert "embed the cost preview into the HITL action" in text.lower()
```

- [ ] **Step 19.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_cost_preview_fragment_includes_async_clause -v
```

Expected: FAIL.

- [ ] **Step 19.3: Append the section**

Append to `backend/app/services/deep_agent/skills/policy/cost-preview.md`:

```markdown

### When you have no user in your conversation

If you are an async agent (no user in this conversation), you cannot
preview-then-wait. Instead, embed the cost preview into the HITL action's
`description` argument — the user will see the estimate on the approval
card before approving the actual tool call. Do not omit the estimate; the
bubble-up message is your only channel to surface it.
```

- [ ] **Step 19.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_cost_preview_fragment_includes_async_clause -v
```

Expected: PASS.

- [ ] **Step 19.5: Commit**

```bash
git add backend/app/services/deep_agent/skills/policy/cost-preview.md tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): cost-preview policy adapts for async agents"
```

### Task 20: Orchestrator prompt — "Async dispatch" section (ACTIVATION)

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 20.1: Write the failing test**

Append to `tests/test_async_agents_unit.py`:

```python
def test_orchestrator_prompt_has_async_dispatch_section():
    from pathlib import Path
    import app.services.deep_agent as deep_pkg

    text = (
        Path(deep_pkg.__file__).parent / "prompts" / "orchestrator.md"
    ).read_text(encoding="utf-8")
    assert "## Async dispatch" in text
    assert "start_async_agent" in text
    assert "list_async_agents" in text
    assert "cancel_async_agent" in text
    # Proxies
    assert "Proxy 1" in text and "Tool-call budget" in text
    assert "Proxy 2" in text and "Deliverable shape" in text
    assert "Proxy 3" in text and "User intent signals" in text
    # Canonical examples header
    assert "Canonical examples" in text
    # Concurrency note
    assert "Per-thread cap: 4" in text
```

- [ ] **Step 20.2: Run test to verify it fails**

```bash
uv run pytest tests/test_async_agents_unit.py::test_orchestrator_prompt_has_async_dispatch_section -v
```

Expected: FAIL.

- [ ] **Step 20.3: Insert the section**

In `backend/app/services/deep_agent/prompts/orchestrator.md`, insert the full "Async dispatch" section from spec §6.2 immediately AFTER the existing `## Compound queries` section and BEFORE `## Batch-size-1 rule for HITL`. Copy the entire section starting at `## Async dispatch (for slow / parallel / analysis-heavy work)` through the final "Their write tools still go through HITL bubble-up; the user approves in this thread." line.

- [ ] **Step 20.4: Run test to verify it passes**

```bash
uv run pytest tests/test_async_agents_unit.py::test_orchestrator_prompt_has_async_dispatch_section -v
```

Expected: PASS.

- [ ] **Step 20.5: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md tests/test_async_agents_unit.py
git commit -m "feat(async-subagents): activate dispatch via orchestrator prompt update"
```

### Task 21: End-to-end integration test — orchestrator dispatches a stubbed async agent

**Files:**
- Test: `tests/test_async_agents_integration.py` (new)

- [ ] **Step 21.1: Write the integration test**

Create `tests/test_async_agents_integration.py`:

```python
"""End-to-end integration: orchestrator dispatches → runner runs → autopost lands."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.models import AgentMessage, TaskRun, TaskStatus


def test_runner_runs_stubbed_agent_then_autoposts(
    session, agent_thread_factory, monkeypatch
):
    """Full _run path with a stubbed agent + scripted final AI message."""
    from langchain_core.messages import AIMessage

    from app.services.async_agents import runner

    thread = agent_thread_factory()

    # Create the TaskRun row up-front (skipping start_async_agent_task's
    # executor submission so we drive _run synchronously).
    task = TaskRun(
        kind="async_agent",
        status=TaskStatus.QUEUED.value,
        parent_thread_id=thread.id,
        description="integration test",
        message=json.dumps({"prompt": "do it", "inputs": {"x": 1}}),
    )
    session.add(task)
    session.commit()

    # Stub the agent factory — return an object with ainvoke + aget_state.
    class StubAgent:
        async def ainvoke(self, payload, config=None):
            return None

        async def aget_state(self, config):
            class S:
                tasks = []
                values = {
                    "messages": [AIMessage(content="Headline.\n\n- finding")],
                    "files": {},
                }

            return S()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_checkpointer(*a, **k):
        yield object()

    monkeypatch.setattr(
        "app.services.async_agents.agent.build_async_agent",
        lambda **kw: StubAgent(),
    )
    monkeypatch.setattr(
        "app.services.async_agents.runner.build_async_checkpointer",
        fake_checkpointer,
        raising=False,
    )
    # Patch where _run_async actually imports build_async_checkpointer
    import app.services.async_agents.runner as runner_mod

    monkeypatch.setattr(
        "app.services.deep_agent.checkpointer.build_async_checkpointer",
        fake_checkpointer,
    )
    # Provide a fake model
    monkeypatch.setattr(
        "app.services.deep_agent.model_factory.build_agent_model",
        lambda *a, **k: object(),
    )

    # Run the body synchronously
    asyncio.run(runner_mod._run_async(task.id))

    # Re-open a session to pick up commits
    session.expire_all()
    refreshed = session.get(TaskRun, task.id)
    assert refreshed.status == TaskStatus.COMPLETED.value

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert any(
        m.character == "async_agent" and m.meta.get("agent_phase") == "completed"
        for m in msgs
    )
```

- [ ] **Step 21.2: Run the test**

```bash
uv run pytest tests/test_async_agents_integration.py -v
```

Expected: PASS.

- [ ] **Step 21.3: Run the full suite to confirm no regressions**

```bash
uv run pytest -q --no-cov 2>&1 | tail -30
```

Expected: all green. Same baseline count + new tests.

- [ ] **Step 21.4: Commit**

```bash
git add tests/test_async_agents_integration.py
git commit -m "test(async-subagents): end-to-end runner → autopost integration"
```

---

## Final verification

- [ ] **Step F.1: Migration round-trip on the real dev DB**

```bash
cd /Users/fuxinyao/open-otc-trading
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: no errors. The `task_runs` table has the four new columns.

- [ ] **Step F.2: Manual smoke (acceptance criterion from spec §8.5)**

Start the dev server. From the chat UI, ask the orchestrator: *"Draft a narrative companion for any recent report."* Confirm:
- The orchestrator's reply includes "I've started a … task #N."
- A new chip appears in the task panel for the async_agent kind.
- After the agent finishes (or fails on missing report), a new assistant message with `character="async_agent"` appears in the chat thread.

- [ ] **Step F.3: Frontend lint + type-check**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step F.4: Push and open PR**

```bash
cd /Users/fuxinyao/open-otc-trading
git push -u origin feat/async-subagents
gh pr create --title "feat(async-subagents): general-purpose background agent dispatch" --body "$(cat <<'EOF'
## Summary
- Adds general-purpose async-agent dispatch to the desk orchestrator.
- Three new orchestrator tools (start/list/cancel) backed by the existing task_runner thread pool.
- HITL interrupts bubble up to the parent thread; existing approve/reject UI works.
- Auto-post completion as a new assistant message on the parent thread.
- Behavior-preserving through Task 18; Tasks 19-20 activate dispatch via prompt update.

## Spec
docs/superpowers/specs/2026-05-16-async-subagents-design.md

## Test plan
- [ ] uv run pytest tests/test_async_agents_*.py
- [ ] uv run alembic upgrade head; downgrade -1; upgrade head
- [ ] cd frontend && npx vitest run
- [ ] cd frontend && npx tsc --noEmit
- [ ] Manual: dispatch a narrative-writer task; observe auto-post
- [ ] Manual: trigger a bubble-up (subagent calls create_report); approve via UI; observe resume + auto-post
- [ ] Manual: cancel a running task; observe cancellation message

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (post-write)

**Spec coverage:**
- §1 architecture → Tasks 4-11 (runtime + lifecycle)
- §2 components → file structure section + all tasks
- §3 data flow → Tasks 8, 9, 10, 11, 16 (start, bubble, autopost, run, resume)
- §4.1 schema → Tasks 1-3
- §4.3 concurrency cap → Task 8
- §4.4 audit events → Tasks 9 (awaiting), 10 (completed), 14 (started), 16 (resumed)
- §5 tool schemas → Task 14
- §5.5 resume endpoint → Task 13
- §6.1 identity prompt → Task 4
- §6.2 orchestrator section → Task 20
- §6.3 cost-preview addendum → Task 19
- §7 tests → distributed across all tasks; integration → Task 21
- §8 phased rollout → tasks numbered to match phases

**Placeholder scan:** No "TBD" / "TODO" / "Add appropriate error handling" / "Similar to Task N". Each step shows the actual code or the actual file edit.

**Type consistency:** `task_id` is `int` throughout. `parent_thread_id` is `int` throughout. `async_task_id` on AgentActionProposal is `int | None`. Tool return shapes match between Python types and TS types. `character="async_agent"` consistently. Checkpointer thread_id format `f"async:{parent_thread_id}:{task_id}"` consistent across Tasks 11 and 16.

**Known soft spots (acceptable for v1):**
- Task 18's frontend test body is a sketch; the implementer fills in the mounting boilerplate by reading the existing test as a template. This is the only test in the plan that doesn't give a fully-runnable code block — frontend test scaffolding varies enough between repos that a stub is more honest than a guess.
- `routes/Tasks.tsx` edits are scoped by example; exact component naming may differ — implementer adapts.
