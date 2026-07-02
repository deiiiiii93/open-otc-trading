# Audit Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an always-on, append-only audit trail of every dangerous (write-class) LLM-agent action across all execution modes, and surface it on a new read-only Audit page.

**Architecture:** A `wrap_tool_call` middleware (registered in all three agent stacks: orchestrator, personas, async agent) records a fail-closed `attempted` row before any classified write executes and updates it with the outcome; HITL proposals/decisions are captured append-only at the projection/resume seams with a mandatory server-minted `audit_ref`; a typed `agent_action_audits` table (migration 0042) backs a read-only `/api/audit` router and an Audit page.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), langchain `AgentMiddleware` (capture), React 19 / Vite / vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-02-audit-module-design.md` (all §-references below point there).

## Global Constraints

- Backend tests: `.venv/bin/python -m pytest` from **repo root**.
- Frontend: `cd frontend && npm test` (vitest), `npx tsc --noEmit`; **token-only styling**, BEM `wl-` prefixes, no hardcoded colors (frontend/CLAUDE.md).
- Migrations use **migration-local Core tables**, never ORM models (repo rule).
- Migration id: `0042_agent_action_audits`, `down_revision = "0041_morning_breach_assemble_prompt"`.
- Audit capture is **always on** — no feature flag.
- `/api/audit` is **read-only** — no mutating endpoint may ever be added (same doctrine as `tracing.py`).
- Append-only: the only row mutation is the phase-1→phase-2 outcome update by in-memory PK (§4).
- New workflow SKILL.md files: none (no skill-catalog test impact).

---

### Task 1: Shared write-action classifier

**Files:**
- Create: `backend/app/services/deep_agent/write_actions.py`
- Modify: `backend/app/services/deep_agent/fanout_readonly.py`
- Test: `tests/test_write_actions.py`

**Interfaces:**
- Produces: `FS_WRITE_TOOLS: frozenset[str]`, `write_names_by_class(tools: Sequence[BaseTool]) -> dict[str, str]`, `classify_write_action(name: str, args: dict | None, gated: dict[str, str], *, include_page_action: bool) -> str | None` (returns `'domain_write' | 'async_dispatch' | 'page_action' | 'fs_write' | 'artifact_write' | None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_write_actions.py
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.write_actions import (
    classify_write_action,
    write_names_by_class,
)


class _FakeTool:
    def __init__(self, name, group=None):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


def test_write_names_by_class_maps_gated_groups():
    tools = [
        _FakeTool("book_position", ToolGroup.DOMAIN_WRITE),
        _FakeTool("start_async_agent", ToolGroup.ASYNC_DISPATCH),
        _FakeTool("propose_reply_options", ToolGroup.PAGE_ACTION),
        _FakeTool("get_position_summaries"),  # ungated read
        _FakeTool("list_positions", ToolGroup.DOMAIN_READ),
    ]
    assert write_names_by_class(tools) == {
        "book_position": "domain_write",
        "start_async_agent": "async_dispatch",
        "propose_reply_options": "page_action",
    }


def test_classify_fs_and_artifact_writes():
    gated = {}
    assert classify_write_action("write_file", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action("edit_file", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action("execute", {}, gated, include_page_action=False) == "fs_write"
    assert classify_write_action("run_python", {"writes_artifacts": True}, gated, include_page_action=False) == "artifact_write"
    assert classify_write_action("run_python", {}, gated, include_page_action=False) is None
    assert classify_write_action("read_file", {}, gated, include_page_action=False) is None


def test_page_action_included_only_for_fanout_consumer():
    gated = {"propose_reply_options": "page_action", "book_position": "domain_write"}
    assert classify_write_action("propose_reply_options", {}, gated, include_page_action=False) is None
    assert classify_write_action("propose_reply_options", {}, gated, include_page_action=True) == "page_action"
    assert classify_write_action("book_position", {}, gated, include_page_action=False) == "domain_write"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_write_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: ... write_actions`

- [ ] **Step 3: Implement the classifier**

```python
# backend/app/services/deep_agent/write_actions.py
"""Shared write-action taxonomy (spec §5.1a).

One classification, two consumers with different group sets:
- FanoutReadOnlyMiddleware blocks PAGE_ACTION too (include_page_action=True);
- AuditTrailMiddleware audits persistent writes only (include_page_action=False).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool

from .envelopes import ToolGroup

# deepagents filesystem/shell built-ins are NOT capability-gated; classify by name.
FS_WRITE_TOOLS = frozenset({"write_file", "edit_file", "execute"})

_GROUP_TO_CLASS = {
    ToolGroup.DOMAIN_WRITE: "domain_write",
    ToolGroup.ASYNC_DISPATCH: "async_dispatch",
    ToolGroup.PAGE_ACTION: "page_action",
}


def write_names_by_class(tools: Sequence[BaseTool]) -> dict[str, str]:
    """Map tool name -> write class for capability-gated write tools."""
    out: dict[str, str] = {}
    for t in tools:
        cls = _GROUP_TO_CLASS.get(getattr(t, "__capability_group__", None))
        if cls is not None:
            out[t.name] = cls
    return out


def classify_write_action(
    name: str,
    args: dict[str, Any] | None,
    gated: dict[str, str],
    *,
    include_page_action: bool,
) -> str | None:
    """Return the write class for a tool call, or None for reads."""
    if name == "run_python":
        return "artifact_write" if (args or {}).get("writes_artifacts") else None
    if name in FS_WRITE_TOOLS:
        return "fs_write"
    cls = gated.get(name)
    if cls == "page_action" and not include_page_action:
        return None
    return cls
```

- [ ] **Step 4: Refactor `fanout_readonly.py` to consume it** (behavior unchanged)

Replace its `_WRITE_GROUPS` / `_FS_WRITE_TOOLS` constants and `_is_fanout_write` body:

```python
# in fanout_readonly.py — imports
from .write_actions import classify_write_action, write_names_by_class

# __init__ body becomes:
        super().__init__()
        self._gated = write_names_by_class(tools)

# _is_fanout_write becomes:
    def _is_fanout_write(self, name: str, args: dict[str, Any] | None) -> bool:
        return (
            classify_write_action(name, args, self._gated, include_page_action=True)
            is not None
        )
```

Delete the now-unused `_WRITE_GROUPS`, `_FS_WRITE_TOOLS`, and the `ToolGroup` import if unreferenced. Keep the module docstring.

- [ ] **Step 5: Run new tests + existing fan-out tests**

Run: `.venv/bin/python -m pytest tests/test_write_actions.py tests/ -k "fanout or write_actions" -v`
Expected: PASS (fan-out behavior regression-free)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/write_actions.py backend/app/services/deep_agent/fanout_readonly.py tests/test_write_actions.py
git commit -m "feat(audit): shared write-action classifier; fanout guard consumes it"
```

---

### Task 2: Redaction layer

**Files:**
- Create: `backend/app/services/deep_agent/audit_redaction.py`
- Test: `tests/test_audit_redaction.py`

**Interfaces:**
- Produces: `redact_args(tool_name: str, args: dict | None) -> tuple[dict, bool]` (returns `(payload, redacted_flag)`), `redact_text(text: str | None, cap: int = 2000) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_redaction.py
import hashlib

from app.services.deep_agent.audit_redaction import redact_args, redact_text


def test_key_pattern_redaction_recursive():
    payload, redacted = redact_args(
        "book_position",
        {"api_key": "sk-123", "nested": {"PASSWORD": "p", "qty": 5}, "note": "ok"},
    )
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["PASSWORD"] == "[REDACTED]"
    assert payload["nested"]["qty"] == 5
    assert payload["note"] == "ok"
    assert redacted is True


def test_content_body_elision_for_fs_tools():
    body = "secret file contents " * 100
    payload, redacted = redact_args("write_file", {"file_path": "/a.txt", "content": body})
    elided = payload["content"]
    assert elided["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert elided["byte_len"] == len(body.encode())
    assert elided["head"] == body[:256]
    assert "secret file contents" in elided["head"]  # head keeps 256 chars
    assert payload["file_path"] == "/a.txt"
    assert redacted is True


def test_code_body_elision_for_run_python_and_execute():
    payload, _ = redact_args("run_python", {"code": "print(1)" * 200, "writes_artifacts": True})
    assert set(payload["code"]) == {"sha256", "byte_len", "head"}
    payload, _ = redact_args("execute", {"command": "curl -H 'Authorization: Bearer x'"})
    assert set(payload["command"]) == {"sha256", "byte_len", "head"}


def test_clean_args_unchanged_and_size_cap():
    payload, redacted = redact_args("book_position", {"underlying": "AAPL", "qty": 1})
    assert payload == {"underlying": "AAPL", "qty": 1}
    assert redacted is False
    big, redacted = redact_args("book_position", {"blob": "x" * 20000})
    assert big["__truncated__"] is True
    assert redacted is True


def test_redact_text_caps_and_none():
    assert redact_text(None) is None
    assert len(redact_text("y" * 5000)) <= 2020  # cap + marker
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audit_redaction.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# backend/app/services/deep_agent/audit_redaction.py
"""Redaction before audit persistence (spec §5.1b).

The audit trail is append-only with no deletion UI, so nothing secret or bulky
may be persisted verbatim: secret-looking keys are masked, content/code bodies
are elided to {sha256, byte_len, head}, oversized payloads are truncated.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|api[_-]?key|credential|authorization)", re.IGNORECASE
)

# Tool -> argument names whose values are content/code bodies to elide.
_BODY_ARGS = {
    "write_file": ("content",),
    "edit_file": ("content", "new_string", "old_string"),
    "run_python": ("code",),
    "execute": ("command",),
}

_HEAD_CHARS = 256
_MAX_SERIALIZED_BYTES = 8 * 1024
_TEXT_CAP = 2000


def _elide(value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_len": len(raw),
        "head": value[:_HEAD_CHARS],
    }


def _redact_value(key: str, value: Any) -> tuple[Any, bool]:
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]", True
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            new, c = _redact_value(key, item)
            out.append(new)
            changed = changed or c
        return out, changed
    return value, False


def _redact_dict(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    out: dict[str, Any] = {}
    for key, value in data.items():
        new, c = _redact_value(key, value)
        out[key] = new
        changed = changed or c
    return out, changed


def redact_args(tool_name: str, args: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return (persistable payload, redacted flag) for a tool call's args."""
    payload, redacted = _redact_dict(dict(args or {}))
    for body_arg in _BODY_ARGS.get(tool_name, ()):
        value = payload.get(body_arg)
        if isinstance(value, str):
            payload[body_arg] = _elide(value)
            redacted = True
    try:
        serialized = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        serialized = repr(payload)
        payload = {"__repr__": serialized[:_MAX_SERIALIZED_BYTES]}
        redacted = True
    if len(serialized.encode("utf-8", errors="replace")) > _MAX_SERIALIZED_BYTES:
        payload = {
            "__truncated__": True,
            "head": serialized[:_MAX_SERIALIZED_BYTES // 4],
        }
        redacted = True
    return payload, redacted


def redact_text(text: str | None, cap: int = _TEXT_CAP) -> str | None:
    """Cap free-text previews (results/errors) before persistence."""
    if text is None:
        return None
    return text if len(text) <= cap else text[:cap] + "…[truncated]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audit_redaction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/audit_redaction.py tests/test_audit_redaction.py
git commit -m "feat(audit): redaction layer (key-pattern masking, body elision, size caps)"
```

---

### Task 3: `AgentActionAudit` model + migration 0042

**Files:**
- Modify: `backend/app/models.py` (add class after `DomainEvent`, ~line 481)
- Create: `backend/alembic/versions/0042_agent_action_audits.py`
- Test: `tests/test_migration_0042.py`

**Interfaces:**
- Produces: `AgentActionAudit` ORM model, table `agent_action_audits` (columns per spec §4: `id, kind, status, deny_reason, tool_name, tool_class, tool_call_id, audit_ref, mode, envelope, actor, model, persona, thread_id, workflow_id, session_id, task_id, message_id, desk_workflow_slug, args_json, redacted, result_preview, error, occurred_at, completed_at`).

- [ ] **Step 1: Add the model to `models.py`** (uses the file's existing imports: `Mapped, mapped_column, Integer, String, Text, Boolean, JSON, DateTime, ForeignKey, Index, utcnow`)

```python
class AgentActionAudit(Base):
    """Append-only dangerous-action audit trail (audit spec §4).

    kind: execution | hitl_proposal | hitl_decision.
    The only permitted mutation is the phase-1 -> phase-2 outcome update on an
    execution row, addressed by in-memory PK; everything else is append-only.
    """

    __tablename__ = "agent_action_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True, default="execution")
    status: Mapped[str] = mapped_column(String(20), index=True)
    deny_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    tool_class: Mapped[str] = mapped_column(String(30), index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    audit_ref: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    envelope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str] = mapped_column(String(80), default="agent")
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    persona: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=True, index=True
    )
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id"), nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desk_workflow_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_agent_action_audits_tool_occurred", "tool_name", "occurred_at"),
        Index("ix_agent_action_audits_thread_occurred", "thread_id", "occurred_at"),
    )
```

- [ ] **Step 2: Write the migration** (migration-local Core tables — repo rule; pattern from `0039_memory_extraction_runs.py` including the `_has_table` idempotency guard)

```python
# backend/alembic/versions/0042_agent_action_audits.py
"""agent_action_audits — dangerous-action audit trail (audit spec §4)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_agent_action_audits"
down_revision = "0041_morning_breach_assemble_prompt"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("agent_action_audits"):
        return
    op.create_table(
        "agent_action_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="execution"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("deny_reason", sa.String(length=30), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_class", sa.String(length=30), nullable=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True),
        sa.Column("audit_ref", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("envelope", sa.String(length=40), nullable=True),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="agent"),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("persona", sa.String(length=40), nullable=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("agent_threads.id"), nullable=True),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id"), nullable=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("agent_tasks.id"), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("desk_workflow_slug", sa.String(length=120), nullable=True),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_preview", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    for name, cols in [
        ("ix_agent_action_audits_kind", ["kind"]),
        ("ix_agent_action_audits_status", ["status"]),
        ("ix_agent_action_audits_tool_name", ["tool_name"]),
        ("ix_agent_action_audits_tool_class", ["tool_class"]),
        ("ix_agent_action_audits_tool_call_id", ["tool_call_id"]),
        ("ix_agent_action_audits_audit_ref", ["audit_ref"]),
        ("ix_agent_action_audits_thread_id", ["thread_id"]),
        ("ix_agent_action_audits_occurred_at", ["occurred_at"]),
        ("ix_agent_action_audits_tool_occurred", ["tool_name", "occurred_at"]),
        ("ix_agent_action_audits_thread_occurred", ["thread_id", "occurred_at"]),
    ]:
        op.create_index(name, "agent_action_audits", cols)


def downgrade() -> None:
    if _has_table("agent_action_audits"):
        op.drop_table("agent_action_audits")
```

- [ ] **Step 3: Write the migration test** — an **isolated Alembic upgrade** against a temp SQLite DB (the ORM `Base.metadata` fixture would not exercise the revision at all), plus an ORM round-trip:

```python
# tests/test_migration_0042.py
"""Real Alembic upgrade/downgrade of 0042 against an isolated temp DB.

Follow the invocation pattern of the existing migration tests (e.g.
tests/test_arena_migration.py) for building the alembic Config; the assertions
below are the contract."""
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _alembic_config(db_url: str, monkeypatch) -> Config:
    # The Alembic config lives at the REPO ROOT, and backend/alembic/env.py
    # overwrites sqlalchemy.url from app.config.get_settings() — so the
    # settings, not the ini option, must be patched or the migration runs
    # against the configured app DB. Mirror the existing migration tests
    # (tests/test_arena_migration.py) exactly.
    import app.config as app_config

    monkeypatch.setattr(
        app_config, "get_settings",
        lambda: app_config.Settings(database_url=db_url),
    )
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_creates_table_columns_and_indexes(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mig.sqlite3'}"
    command.upgrade(_alembic_config(url, monkeypatch), "0042_agent_action_audits")
    insp = sa.inspect(sa.create_engine(url))
    cols = {c["name"] for c in insp.get_columns("agent_action_audits")}
    assert {
        "id", "kind", "status", "deny_reason", "tool_name", "tool_class",
        "tool_call_id", "audit_ref", "mode", "envelope", "actor", "model",
        "persona", "thread_id", "workflow_id", "session_id", "task_id",
        "message_id", "desk_workflow_slug", "args_json", "redacted",
        "result_preview", "error", "occurred_at", "completed_at",
    } <= cols
    index_names = {i["name"] for i in insp.get_indexes("agent_action_audits")}
    assert "ix_agent_action_audits_tool_occurred" in index_names
    assert "ix_agent_action_audits_thread_occurred" in index_names
    assert "ix_agent_action_audits_audit_ref" in index_names


def test_downgrade_drops_table(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mig.sqlite3'}"
    cfg = _alembic_config(url, monkeypatch)
    command.upgrade(cfg, "0042_agent_action_audits")
    command.downgrade(cfg, "0041_morning_breach_assemble_prompt")
    insp = sa.inspect(sa.create_engine(url))
    assert "agent_action_audits" not in insp.get_table_names()


def test_orm_row_roundtrip_with_defaults(session):
    from app.models import AgentActionAudit

    row = AgentActionAudit(
        status="attempted", tool_name="book_position", tool_class="domain_write",
        args_json={"underlying": "AAPL"},
    )
    session.add(row)
    session.commit()
    assert row.id is not None
    assert row.kind == "execution"
    assert row.redacted is False
    assert row.occurred_at is not None
    assert row.completed_at is None
```

- [ ] **Step 4: Run tests; then upgrade the live DB**

Run: `.venv/bin/python -m pytest tests/test_migration_0042.py -v`
Expected: PASS (the Alembic revision itself is exercised in CI)
Then apply to the live DB (a real upgrade, not a dry run — the live DB may lag head per CLAUDE.md):
Run: `.venv/bin/python -m alembic upgrade head`
Expected: `Running upgrade 0041_... -> 0042_agent_action_audits`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0042_agent_action_audits.py tests/test_migration_0042.py
git commit -m "feat(audit): AgentActionAudit model + migration 0042"
```

---

### Task 4: Audit recorder service (fail-closed, retry, counter)

**Files:**
- Create: `backend/app/services/audit_trail.py`
- Test: `tests/test_audit_trail_recorder.py`

**Interfaces:**
- Produces:
  - `AUDIT_CONTEXT_KEY = "__audit_context__"` (configurable key)
  - `class AuditUnavailableError(RuntimeError)`
  - `record_attempt(*, tool_name, tool_class, tool_call_id, args, context) -> int` (PK; raises `AuditUnavailableError` after bounded retry; §5.2 phase 1)
  - `record_outcome(row_id, *, status, deny_reason=None, result_preview=None, error=None) -> None` (best-effort; §5.2 phase 2)
  - `record_refusal(*, tool_name, tool_class, tool_call_id, context) -> None` (best-effort `status='refused'` row; in-memory fallback counter)
  - `record_hitl_proposal(session, *, proposal: dict, tool_class, context) -> None` (joins the caller's session — atomic with message persist; §5.4)
  - `record_hitl_decision(session, *, action: dict, decision: str, actor: str) -> None`
  - `unpersisted_refusals() -> int`
- Consumes: `redact_args`/`redact_text` (Task 2), `AgentActionAudit` (Task 3).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_trail_recorder.py
import pytest
from sqlalchemy.exc import OperationalError

from app import database
from app.models import AgentActionAudit
from app.services import audit_trail
from app.services.audit_trail import (
    AuditUnavailableError,
    record_attempt,
    record_hitl_decision,
    record_hitl_proposal,
    record_outcome,
    record_refusal,
)

CTX = {
    "actor": "desk_user", "mode": "yolo", "model": "deepseek/deepseek-v4-flash",
    "thread_id": None, "workflow_id": None, "session_id": None, "task_id": None,
    "message_id": None, "desk_workflow_slug": None, "envelope": "DESK_WORKFLOW",
}


def test_attempt_then_outcome(session):
    row_id = record_attempt(
        tool_name="book_position", tool_class="domain_write",
        tool_call_id="call_1", args={"qty": 1, "api_key": "sk"}, context=CTX,
    )
    row = session.get(AgentActionAudit, row_id)
    assert row.status == "attempted"
    assert row.args_json["api_key"] == "[REDACTED]"
    assert row.redacted is True
    assert row.mode == "yolo"
    record_outcome(row_id, status="ok", result_preview="booked #7")
    session.expire_all()
    row = session.get(AgentActionAudit, row_id)
    assert row.status == "ok"
    assert row.completed_at is not None


def test_attempt_fail_closed_after_bounded_retry(monkeypatch):
    calls = []

    class _BoomSession:
        def __enter__(self): raise OperationalError("db locked", None, Exception("locked"))
        def __exit__(self, *a): return False

    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(database, "SessionLocal", lambda: calls.append(1) or _BoomSession())
    with pytest.raises(AuditUnavailableError):
        record_attempt(tool_name="book_position", tool_class="domain_write",
                       tool_call_id="c", args={}, context=CTX)
    assert len(calls) == 4  # 1 try + 3 retries


def test_refusal_row_and_unpersisted_counter(session, monkeypatch):
    record_refusal(tool_name="book_position", tool_class="domain_write",
                   tool_call_id="c9", context=CTX)
    row = session.query(AgentActionAudit).filter_by(status="refused").one()
    assert row.deny_reason == "audit_unavailable"
    # When even the refusal row cannot persist, the in-memory counter grows.
    before = audit_trail.unpersisted_refusals()
    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.0,))
    monkeypatch.setattr(database, "SessionLocal", lambda: (_ for _ in ()).throw(OperationalError("x", None, Exception())))
    record_refusal(tool_name="t", tool_class="domain_write", tool_call_id=None, context=CTX)
    assert audit_trail.unpersisted_refusals() == before + 1


def test_hitl_proposal_joins_caller_session(session):
    proposal = {
        "id": "int1:0", "tool_name": "book_position",
        "payload": {"qty": 2},
        "source_meta": {"audit": {"audit_ref": "ref-1", "tool_call_id": "call_2"}},
    }
    record_hitl_proposal(session, proposal=proposal, tool_class="domain_write", context=CTX)
    # NOT committed yet — atomicity is the caller's transaction.
    session.rollback()
    assert session.query(AgentActionAudit).filter_by(kind="hitl_proposal").count() == 0
    record_hitl_proposal(session, proposal=proposal, tool_class="domain_write", context=CTX)
    session.commit()
    row = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").one()
    assert row.status == "proposed"
    assert row.audit_ref == "ref-1"
    assert row.tool_call_id == "call_2"


def test_hitl_decision_row(session):
    action = {"id": "int1:0", "tool_name": "book_position",
              "source_meta": {"audit": {"audit_ref": "ref-2", "tool_call_id": "c3"}}}
    record_hitl_decision(session, action=action, decision="approved", actor="desk_user")
    session.commit()
    row = session.query(AgentActionAudit).filter_by(kind="hitl_decision").one()
    assert row.status == "approved"
    assert row.audit_ref == "ref-2"
    assert row.actor == "desk_user"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audit_trail_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: ... audit_trail`

- [ ] **Step 3: Implement the recorder**

```python
# backend/app/services/audit_trail.py
"""Durable recorder for the dangerous-action audit trail (audit spec §5).

Phase-1 (`record_attempt`) is FAIL-CLOSED: bounded busy-retry, then
AuditUnavailableError — the caller must refuse the tool call. Phase-2
(`record_outcome`) and refusal rows are best-effort (log on failure): the
durable attempt row already exists, only the outcome may be unknown.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .. import database
from ..models import AgentActionAudit
from .deep_agent.audit_redaction import redact_args, redact_text

logger = logging.getLogger(__name__)

AUDIT_CONTEXT_KEY = "__audit_context__"

# Bounded backoff before a fail-closed refusal (spec §5.2 lock handling).
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.3, 0.9)

_unpersisted_refusals = 0
_refusal_lock = threading.Lock()


class AuditUnavailableError(RuntimeError):
    """Raised when the phase-1 attempt record cannot be committed."""


_CTX_COLUMNS = (
    "mode", "envelope", "actor", "model", "persona", "thread_id",
    "workflow_id", "session_id", "task_id", "message_id", "desk_workflow_slug",
)


def _context_columns(context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = context or {}
    out = {k: ctx.get(k) for k in _CTX_COLUMNS}
    if out.get("actor") is None:
        out["actor"] = "agent"
    return out


def _insert_with_retry(row: AgentActionAudit) -> int:
    last_exc: Exception | None = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            with database.SessionLocal() as session:
                session.add(row)
                session.commit()
                return row.id
        except OperationalError as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
    raise AuditUnavailableError(
        f"audit attempt row could not be committed after {len(_RETRY_DELAYS) + 1} tries"
    ) from last_exc


def record_attempt(
    *,
    tool_name: str,
    tool_class: str,
    tool_call_id: str | None,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> int:
    payload, redacted = redact_args(tool_name, args)
    ctx = _context_columns(context)
    audit_ref = (context or {}).get("audit_ref")
    row = AgentActionAudit(
        kind="execution", status="attempted", tool_name=tool_name,
        tool_class=tool_class, tool_call_id=tool_call_id, audit_ref=audit_ref,
        args_json=payload, redacted=redacted, **ctx,
    )
    return _insert_with_retry(row)


def record_outcome(
    row_id: int,
    *,
    status: str,
    deny_reason: str | None = None,
    result_preview: str | None = None,
    error: str | None = None,
) -> None:
    from datetime import datetime, timezone

    try:
        with database.SessionLocal() as session:
            row = session.get(AgentActionAudit, row_id)
            if row is None:  # pragma: no cover — PK came from phase 1
                return
            row.status = status
            row.deny_reason = deny_reason
            row.result_preview = redact_text(result_preview)
            row.error = redact_text(error)
            row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    except SQLAlchemyError:
        logger.exception(
            "audit outcome update failed for row %s (status=%s) — row stays 'attempted'",
            row_id, status,
        )


def record_refusal(
    *,
    tool_name: str,
    tool_class: str,
    tool_call_id: str | None,
    context: dict[str, Any] | None,
) -> None:
    """Best-effort durable record of a fail-closed refusal (spec §5.2)."""
    global _unpersisted_refusals
    row = AgentActionAudit(
        kind="execution", status="refused", deny_reason="audit_unavailable",
        tool_name=tool_name, tool_class=tool_class, tool_call_id=tool_call_id,
        args_json={}, **_context_columns(context),
    )
    try:
        _insert_with_retry(row)
    except (AuditUnavailableError, SQLAlchemyError):
        with _refusal_lock:
            _unpersisted_refusals += 1
        logger.exception("audit refusal row could not be persisted (counted in memory)")


def unpersisted_refusals() -> int:
    with _refusal_lock:
        return _unpersisted_refusals


def _audit_block(entry: dict[str, Any]) -> dict[str, Any]:
    meta = entry.get("source_meta") or {}
    return meta.get("audit") or {}


def record_hitl_proposal(
    session: Any,
    *,
    proposal: dict[str, Any],
    tool_class: str,
    context: dict[str, Any] | None,
) -> None:
    """Insert a proposal row into the CALLER's session (atomic with the
    pending-action card persistence — spec §5.4). No commit here."""
    audit = _audit_block(proposal)
    payload, redacted = redact_args(proposal.get("tool_name") or "", proposal.get("payload"))
    session.add(AgentActionAudit(
        kind="hitl_proposal", status="proposed",
        tool_name=str(proposal.get("tool_name") or ""), tool_class=tool_class,
        tool_call_id=audit.get("tool_call_id"), audit_ref=audit.get("audit_ref"),
        args_json=payload, redacted=redacted, **_context_columns(context),
    ))


def record_hitl_decision(
    session: Any,
    *,
    action: dict[str, Any],
    decision: str,
    actor: str,
    context: dict[str, Any] | None = None,
    tool_class: str = "domain_write",
) -> None:
    """Insert an approved/rejected decision row into the caller's session.

    Join keys (thread/workflow/session/task) come from `context` when the call
    site has them, falling back to the action's own source_meta — decision rows
    must be reachable from thread-scoped audit views (plan-review finding #3).
    """
    audit = _audit_block(action)
    meta = action.get("source_meta") or {}
    merged = {
        "thread_id": meta.get("thread_id"),
        "workflow_id": meta.get("workflow_id"),
        "session_id": meta.get("session_id"),
        "task_id": meta.get("task_id"),
        **{k: v for k, v in (context or {}).items() if v is not None},
    }
    ctx = _context_columns(merged)
    ctx["actor"] = actor
    session.add(AgentActionAudit(
        kind="hitl_decision", status=decision,
        tool_name=str(action.get("tool_name") or ""), tool_class=tool_class,
        tool_call_id=audit.get("tool_call_id"), audit_ref=audit.get("audit_ref"),
        args_json={}, **ctx,
    ))
```

Note: `record_hitl_decision` sets `tool_class="domain_write"` as a safe default — every tool in `INTERRUPT_TOOL_NAMES` is state-mutating; if the classifier map is available at the call site, pass through instead (Task 7 threads it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audit_trail_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Add the real lock-contention test** (spec §8)

```python
# append to tests/test_audit_trail_recorder.py
def test_contention_fail_closed_with_real_lock(tmp_path, monkeypatch):
    """Hold a real SQLite write lock; record_attempt must retry then refuse."""
    import sqlite3
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    db_file = tmp_path / "locked.sqlite3"
    engine = sa.create_engine(f"sqlite:///{db_file}", connect_args={"timeout": 0.05})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.01, 0.01))

    locker = sqlite3.connect(db_file)
    locker.execute("BEGIN IMMEDIATE")  # hold RESERVED lock
    try:
        with pytest.raises(AuditUnavailableError):
            record_attempt(tool_name="book_position", tool_class="domain_write",
                           tool_call_id="c", args={}, context=None)
    finally:
        locker.rollback(); locker.close()
```

Run: `.venv/bin/python -m pytest tests/test_audit_trail_recorder.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/audit_trail.py tests/test_audit_trail_recorder.py
git commit -m "feat(audit): fail-closed recorder with bounded retry, refusal counter, HITL row helpers"
```

---

### Task 5: `AuditTrailMiddleware`

**Files:**
- Create: `backend/app/services/deep_agent/audit_trail_middleware.py`
- Test: `tests/test_audit_trail_middleware.py`

**Interfaces:**
- Produces: `AuditTrailMiddleware(tools: Sequence[BaseTool])` with `wrap_tool_call` / `awrap_tool_call`.
- Consumes: `classify_write_action`/`write_names_by_class` (Task 1), recorder (Task 4), `CapabilityDeniedError`/`CostPreviewRequiredError`/`ToolScopeDeniedError` from `capability_gate`, `GraphBubbleUp` from `langgraph.errors`, `_DENY` template match from `fanout_readonly`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_trail_middleware.py
import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

from app.models import AgentActionAudit
from app.services import audit_trail
from app.services.deep_agent.audit_trail_middleware import AuditTrailMiddleware
from app.services.deep_agent.capability_gate import CapabilityDeniedError
from app.services.deep_agent.envelopes import Envelope, ToolGroup


class _FakeTool:
    def __init__(self, name, group=None):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


class _Req:
    def __init__(self, name, args=None, call_id="tc1"):
        self.tool_call = {"name": name, "args": args or {}, "id": call_id}


TOOLS = [_FakeTool("book_position", ToolGroup.DOMAIN_WRITE), _FakeTool("list_positions")]


def _mw():
    return AuditTrailMiddleware(tools=TOOLS)


def test_read_tool_passes_through_no_rows(session):
    called = []
    result = _mw().wrap_tool_call(_Req("list_positions"), lambda r: called.append(r) or "ok")
    assert result == "ok" and called
    assert session.query(AgentActionAudit).count() == 0


def test_write_tool_attempt_before_handler_then_ok(session):
    order = []

    def handler(request):
        order.append(session.query(AgentActionAudit).filter_by(status="attempted").count())
        return ToolMessage(content="booked", tool_call_id="tc1", name="book_position")

    _mw().wrap_tool_call(_Req("book_position", {"qty": 1}), handler)
    assert order == [1]  # attempted row visible BEFORE the handler ran
    row = session.query(AgentActionAudit).one()
    assert row.status == "ok" and row.tool_class == "domain_write"


def test_error_toolmessage_recorded_as_error(session):
    msg = ToolMessage(content="Error: boom", tool_call_id="tc1", name="book_position", status="error")
    _mw().wrap_tool_call(_Req("book_position"), lambda r: msg)
    assert session.query(AgentActionAudit).one().status == "error"


def test_capability_denied_recorded_and_reraised(session):
    def handler(request):
        raise CapabilityDeniedError(envelope=Envelope.PET_PAGE, group=ToolGroup.DOMAIN_WRITE, tool_name="book_position")

    with pytest.raises(CapabilityDeniedError):
        _mw().wrap_tool_call(_Req("book_position"), handler)
    row = session.query(AgentActionAudit).one()
    assert row.status == "denied" and row.deny_reason == "capability"


def test_interrupt_recorded_and_reraised(session):
    def handler(request):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        _mw().wrap_tool_call(_Req("book_position"), handler)
    assert session.query(AgentActionAudit).one().status == "interrupted"


def test_raw_exception_recorded_and_reraised(session):
    with pytest.raises(ValueError):
        _mw().wrap_tool_call(_Req("book_position"), lambda r: (_ for _ in ()).throw(ValueError("bad terms")))
    row = session.query(AgentActionAudit).one()
    assert row.status == "error" and "bad terms" in row.error


def test_fail_closed_refusal_blocks_handler(session, monkeypatch):
    monkeypatch.setattr(
        audit_trail, "record_attempt",
        lambda **kw: (_ for _ in ()).throw(audit_trail.AuditUnavailableError("down")),
    )
    called = []
    result = _mw().wrap_tool_call(_Req("book_position"), lambda r: called.append(1) or "x")
    assert not called  # business tool NEVER executed
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "audit trail unavailable" in result.content.lower()


@pytest.mark.anyio
async def test_awrap_tool_call_ok(session):
    async def handler(request):
        return ToolMessage(content="ok", tool_call_id="tc1", name="book_position")

    await _mw().awrap_tool_call(_Req("book_position"), handler)
    assert session.query(AgentActionAudit).one().status == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audit_trail_middleware.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# backend/app/services/deep_agent/audit_trail_middleware.py
"""Always-on capture of dangerous tool executions (audit spec §5.2).

Sits just inside ToolErrorBoundaryMiddleware in every stack. Phase 1 is
fail-closed: no classified write may execute without a committed attempt row.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeAlias

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from ..audit_trail import (
    AUDIT_CONTEXT_KEY,
    AuditUnavailableError,
    record_attempt,
    record_outcome,
    record_refusal,
)
from .capability_gate import (
    CapabilityDeniedError,
    CostPreviewRequiredError,
    ToolScopeDeniedError,
)
from .write_actions import classify_write_action, write_names_by_class

logger = logging.getLogger(__name__)

_ToolResult: TypeAlias = ToolMessage | Command

_REFUSAL = (
    "Audit trail unavailable; write action '{name}' blocked (fail-closed). "
    "Read-only tools still work; retry the write shortly."
)

_DENY_REASON_BY_EXC = {
    CapabilityDeniedError: "capability",
    CostPreviewRequiredError: "cost_preview",
    ToolScopeDeniedError: "tool_scope",
}

# Fan-out read-only denials return an error ToolMessage with this prefix
# (fanout_readonly._DENY) rather than raising.
_FANOUT_DENY_MARKER = "Fanned-out subagents are read-only:"


def _read_audit_context() -> dict[str, Any] | None:
    try:
        from langgraph.config import get_config

        configurable = get_config().get("configurable") or {}
        ctx = configurable.get(AUDIT_CONTEXT_KEY)
        return dict(ctx) if isinstance(ctx, dict) else None
    except Exception:  # pragma: no cover — outside a runnable context
        return None


class AuditTrailMiddleware(AgentMiddleware):
    def __init__(self, tools: Sequence[BaseTool] = ()) -> None:
        super().__init__()
        self._gated = write_names_by_class(tools)

    def _classify(self, request: ToolCallRequest) -> str | None:
        call = request.tool_call
        return classify_write_action(
            call.get("name", ""), call.get("args") or {}, self._gated,
            include_page_action=False,
        )

    def _refuse(self, request: ToolCallRequest, tool_class: str) -> ToolMessage:
        call = request.tool_call
        record_refusal(
            tool_name=call.get("name", ""), tool_class=tool_class,
            tool_call_id=call.get("id"), context=_read_audit_context(),
        )
        return ToolMessage(
            content=_REFUSAL.format(name=call.get("name", "")),
            tool_call_id=call["id"], name=call.get("name"), status="error",
        )

    def _attempt(self, request: ToolCallRequest, tool_class: str) -> int:
        call = request.tool_call
        return record_attempt(
            tool_name=call.get("name", ""), tool_class=tool_class,
            tool_call_id=call.get("id"), args=call.get("args") or {},
            context=_read_audit_context(),
        )

    @staticmethod
    def _record_result(row_id: int, result: _ToolResult) -> None:
        if isinstance(result, ToolMessage) and result.status == "error":
            content = str(result.content)
            if content.startswith(_FANOUT_DENY_MARKER):
                record_outcome(row_id, status="denied", deny_reason="fanout_readonly")
            else:
                record_outcome(row_id, status="error", error=content)
        else:
            preview = str(result.content) if isinstance(result, ToolMessage) else repr(result)
            record_outcome(row_id, status="ok", result_preview=preview)

    @staticmethod
    def _record_exception(row_id: int, exc: Exception) -> None:
        for exc_type, reason in _DENY_REASON_BY_EXC.items():
            if isinstance(exc, exc_type):
                record_outcome(row_id, status="denied", deny_reason=reason)
                return
        if isinstance(exc, GraphBubbleUp):
            record_outcome(row_id, status="interrupted")
        else:
            record_outcome(row_id, status="error", error=repr(exc))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        tool_class = self._classify(request)
        if tool_class is None:
            return handler(request)
        try:
            row_id = self._attempt(request, tool_class)
        except AuditUnavailableError:
            return self._refuse(request, tool_class)
        try:
            result = handler(request)
        except Exception as exc:
            self._record_exception(row_id, exc)
            raise
        self._record_result(row_id, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        tool_class = self._classify(request)
        if tool_class is None:
            return await handler(request)
        try:
            row_id = self._attempt(request, tool_class)
        except AuditUnavailableError:
            return self._refuse(request, tool_class)
        try:
            result = await handler(request)
        except Exception as exc:
            self._record_exception(row_id, exc)
            raise
        self._record_result(row_id, result)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audit_trail_middleware.py -v`
Expected: PASS. If `CapabilityDeniedError` behavior is masked by conftest's `_bypass_capability_gate`, these tests don't invoke the gate (they raise the exception directly from the fake handler), so no `_GATE_TEST_FILES` registration is needed — verify this holds.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/audit_trail_middleware.py tests/test_audit_trail_middleware.py
git commit -m "feat(audit): AuditTrailMiddleware — fail-closed capture at wrap_tool_call"
```

---

### Task 6: Register middleware in all three stacks + coverage assertion

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py:145` (`_agent_middleware`)
- Modify: `backend/app/services/deep_agent/personas.py:193-206` (`all_personas`)
- Modify: `backend/app/services/async_agents/agent.py:120-132` (`build_async_agent`)
- Test: `tests/test_audit_registration.py`

**Interfaces:**
- Consumes: `AuditTrailMiddleware` (Task 5).

- [ ] **Step 1: Write the failing coverage-assertion test** (spec §5.2a: a factory missing the middleware must fail CI)

```python
# tests/test_audit_registration.py
"""Every agent middleware stack must carry AuditTrailMiddleware (spec §5.2a)."""
from app.services.deep_agent.audit_trail_middleware import AuditTrailMiddleware


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def test_orchestrator_stack_has_audit_inside_error_boundary():
    from unittest.mock import MagicMock
    from app.services.deep_agent.orchestrator import _agent_middleware

    mw = _agent_middleware(False, model=MagicMock(), backend=MagicMock(), tools=[])
    names = _names(mw)
    assert names[0] == "ToolErrorBoundaryMiddleware"
    assert names[1] == "AuditTrailMiddleware"


def test_persona_stacks_have_audit_inside_error_boundary():
    from unittest.mock import MagicMock
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(MagicMock(), [], skills_backend=MagicMock())
    for spec in specs:
        names = _names(spec["middleware"])
        assert names[0] == "ToolErrorBoundaryMiddleware"
        assert names[1] == "AuditTrailMiddleware"
        assert "FanoutReadOnlyMiddleware" in names


def test_async_agent_stack_has_audit(monkeypatch):
    import app.services.async_agents.agent as agent_mod

    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured["middleware"] = kwargs["middleware"]
        return object()

    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create_deep_agent)
    from unittest.mock import MagicMock

    agent_mod.build_async_agent(
        model=MagicMock(), tools=[], checkpointer=None, task_id=1,
    )
    assert "AuditTrailMiddleware" in _names(captured["middleware"])
```

(Adjust the `create_deep_agent` monkeypatch target if `build_async_agent` imports it locally — patch `agent_mod` namespace accordingly; check the import style first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_audit_registration.py -v`
Expected: FAIL (middleware not registered yet)

- [ ] **Step 3: Register in `orchestrator.py::_agent_middleware`** — after line 145:

```python
    # Just inside the boundary: always-on dangerous-action audit (audit spec §5.2a).
    middleware: list[Any] = [ToolErrorBoundaryMiddleware()]
    from .audit_trail_middleware import AuditTrailMiddleware
    middleware.append(AuditTrailMiddleware(tools=tools))
```

- [ ] **Step 4: Register in `personas.py::all_personas`** — after the `ToolErrorBoundaryMiddleware` insert at index 0:

```python
        middleware.insert(0, ToolErrorBoundaryMiddleware())
        # Just inside the boundary: always-on dangerous-action audit (audit spec §5.2a).
        middleware.insert(1, AuditTrailMiddleware(tools=tools))
        # Just inside the audit trail: block writes when this persona runs as a
        # fanned-out subagent ... (existing comment)
        middleware.insert(2, FanoutReadOnlyMiddleware(tools=tools))
```

with the import added next to the existing middleware imports:
```python
    from .audit_trail_middleware import AuditTrailMiddleware
```

- [ ] **Step 5: Register in `async_agents/agent.py::build_async_agent`** — head of the middleware list:

```python
    from ..deep_agent.audit_trail_middleware import AuditTrailMiddleware

    middleware = [AuditTrailMiddleware(tools=tools)]
```

- [ ] **Step 6: Run the registration test + existing orchestrator/persona tests**

Run: `.venv/bin/python -m pytest tests/test_audit_registration.py tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/deep_agent/orchestrator.py backend/app/services/deep_agent/personas.py backend/app/services/async_agents/agent.py tests/test_audit_registration.py
git commit -m "feat(audit): register AuditTrailMiddleware in orchestrator, personas, async agent + CI coverage assertion"
```

---

### Task 7: Context enrichment + HITL `audit_ref` minting + proposal/decision rows

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py:296-326` (`_source_meta_for_action`)
- Modify: `backend/app/services/agents.py` — audit-context injection at the `graph_run_config` call sites (`configurable_extra`), proposal-row insertion at the five `pending_actions` persistence sites (~2033, ~2358, ~3075, ~4046, ~4230), decision rows in `_mark_pending_action_resolved` (~930)
- Modify: `backend/app/services/async_agents/runner.py:242` and `backend/app/services/async_agents/resume.py:141` (audit context in `configurable_extra`)
- Test: `tests/test_audit_hitl_capture.py`

**Interfaces:**
- Consumes: `record_hitl_proposal` / `record_hitl_decision` / `AUDIT_CONTEXT_KEY` (Task 4).
- Produces: `source_meta.audit` always contains `{audit_ref, tool_call_id, tool_name, persona, emitted_at, interrupt_id}` for every projected action.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_hitl_capture.py
"""audit_ref minting is UNCONDITIONAL — the async projection path passes
persona=None and source_meta=None and must still produce the audit block
(spec §5.4; this was review finding #1 of iteration 2)."""
import uuid

from langgraph.types import Interrupt

from app.services.deep_agent.hitl import pending_actions_from_interrupts


def _interrupt():
    return Interrupt(
        value={"action_requests": [{"name": "book_position", "args": {"qty": 1}, "id": "call_9"}]},
        id="int_1",
    )


def test_audit_ref_minted_without_source_meta():
    [proposal] = pending_actions_from_interrupts([_interrupt()], persona=None, source_meta=None)
    audit = proposal.source_meta["audit"]
    assert uuid.UUID(audit["audit_ref"])  # valid uuid
    assert audit["tool_call_id"] == "call_9"
    assert audit["tool_name"] == "book_position"
    assert audit["interrupt_id"] == "int_1"


def test_audit_ref_preserved_when_source_meta_present():
    [proposal] = pending_actions_from_interrupts(
        [_interrupt()], persona="trader",
        source_meta={"workflow_id": 3, "audit": {"audit_ref": "keep-me"}},
    )
    audit = proposal.source_meta["audit"]
    assert audit["audit_ref"] == "keep-me"
    assert proposal.source_meta["workflow_id"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audit_hitl_capture.py -v`
Expected: FAIL (`source_meta` is `{}` on the no-meta path)

- [ ] **Step 3: Rewrite `_source_meta_for_action`** (drop the `if not source_meta: return {}` early exit):

```python
def _source_meta_for_action(
    *,
    source_meta: dict[str, Any] | None,
    interrupt_id: str,
    action_request: dict[str, Any],
    persona: str | None,
    tool_name: str,
) -> dict[str, Any]:
    """Always returns an audit block — audit_ref minting is unconditional
    (audit spec §5.4): async projections pass source_meta=None and previously
    got {} here, which broke proposal/decision/execution correlation."""
    from uuid import uuid4

    emitted_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    tool_call_id = (
        action_request.get("id")
        or action_request.get("tool_call_id")
        or interrupt_id
    )
    base = dict(source_meta or {})
    audit = dict(base.get("audit") or {})
    audit.setdefault("audit_ref", str(uuid4()))
    audit.update(
        {
            "tool_call_id": str(tool_call_id),
            "tool_name": tool_name,
            "persona": persona,
            "emitted_at": emitted_at,
            "interrupt_id": interrupt_id,
        }
    )
    base["audit"] = audit
    return base
```

- [ ] **Step 4: Inject `__audit_context__` into `configurable_extra`**

In `agents.py`, add one helper near the top of `AgentService`:

```python
    def _audit_context_extra(
        self,
        *,
        actor: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        thread_id: int | None = None,
        workflow_id: int | None = None,
        session_id: int | None = None,
        task_id: int | None = None,
        message_id: int | None = None,
        desk_workflow_slug: str | None = None,
        envelope: str | None = None,
        audit_ref: str | None = None,
    ) -> dict[str, Any]:
        from .audit_trail import AUDIT_CONTEXT_KEY

        return {
            AUDIT_CONTEXT_KEY: {
                "actor": actor, "mode": mode, "model": model,
                "thread_id": thread_id, "workflow_id": workflow_id,
                "session_id": session_id, "task_id": task_id,
                "message_id": message_id,
                "desk_workflow_slug": desk_workflow_slug,
                "envelope": envelope, "audit_ref": audit_ref,
            }
        }
```

Then merge `**self._audit_context_extra(...)` into the `configurable_extra` dict at each `graph_run_config` call site that runs agent turns, passing whatever identifiers are in scope at that site (all fields nullable — partial enrichment degrades gracefully). Known sites (verify each during implementation; some are read-only paths that never run write tools and may be skipped): `agents.py:1762, 1903, 2163, 2677, 3171, 3322, 3407, 3498`; the three resume paths must pass `audit_ref` from the action's `source_meta["audit"]["audit_ref"]`; `async_agents/runner.py:242` passes `actor="async_agent"`, `task_id`, `mode`; `async_agents/resume.py:141` additionally passes the `audit_ref`.

- [ ] **Step 5: Insert proposal rows at the five persistence sites**

At each site where `"pending_actions": [a.model_dump(mode="json") for a in pending]` (or the re-projected list) is written into a message `meta` inside an open session — the `agents.py` sites (~2033, ~2358, ~3075, ~4046, ~4230) **plus the async projection path in `backend/app/services/async_agents/bubble_up.py`**, which calls `pending_actions_from_interrupts(...)` and persists `meta['pending_actions']` directly (insert the proposal rows in that same transaction, before its flush/commit) — add immediately before the message-meta assignment/commit:

```python
            from .audit_trail import record_hitl_proposal
            from .deep_agent.write_actions import classify_write_action, write_names_by_class

            gated = write_names_by_class(self.tools)
            for entry in (a.model_dump(mode="json") for a in pending):
                tool_class = classify_write_action(
                    entry.get("tool_name") or "",
                    entry.get("payload") or {},
                    gated,
                    include_page_action=False,
                ) or "domain_write"
                record_hitl_proposal(
                    session,
                    proposal=entry,
                    tool_class=tool_class,
                    context={"thread_id": thread_id, "actor": actor},
                )
```

using the session/thread/actor variables in scope at each site (names differ per site — match locals). The insert joins the same transaction as the message persist (atomicity, spec §5.4). If `self.tools` is not the attribute name, use the AgentService tool list attribute found at implementation time. **Classification goes through `classify_write_action` with the proposal's `payload` as the args so a `run_python(writes_artifacts=True)` HITL proposal lands as `artifact_write`, matching its later execution row** (the `RunPythonArtifactHITLMiddleware` interrupt path); the `"domain_write"` fallback covers every remaining `INTERRUPT_TOOL_NAMES` member. Add to the Task 7 tests: a `run_python` proposal with `payload={"writes_artifacts": True}` produces `tool_class == "artifact_write"` on proposal, decision, and execution rows.

- [ ] **Step 6: Insert decision rows AT THE RESUME BOUNDARY, before the agent resumes**

Do **not** rely solely on `_mark_pending_action_resolved` — several resume branches
invoke the agent first and roll back on failure, so an approved write that fails
during resume would leave no decision row (plan-review-3 finding #2). Record the
decision in `resume_pending_action` **immediately after the action is validated as
`pending` and before any graph invocation**, in its own committed transaction that
survives a subsequent resume failure. The status flip in
`_mark_pending_action_resolved` remains the card-state mechanism; the audit row is
written earlier:

```python
    # in resume_pending_action, right after the ResumeConflictError pending-status
    # guard passes and BEFORE routing to any resume path / graph invocation —
    # committed in its own short transaction (SessionLocal) so a later resume
    # failure cannot roll it back:
    from .audit_trail import record_hitl_decision
    from .deep_agent.write_actions import classify_write_action, write_names_by_class

    tool_class = classify_write_action(
        entry.get("tool_name") or "",
        entry.get("payload") or {},
        write_names_by_class(self.tools),
        include_page_action=False,
    ) or "domain_write"
    record_hitl_decision(
        session,
        action=entry,
        decision="approved" if status == "confirmed" else "rejected",
        actor=actor,
        tool_class=tool_class,
        context={
            "thread_id": thread_id,
            "workflow_id": workflow_id,
            "session_id": agent_session_id,
            "message_id": source_message_id,
        },
    )
```

(The decision row must carry the same class as its proposal/execution — a
`run_python(writes_artifacts=True)` approval is `artifact_write`, not the
`domain_write` default; asserted by the three-row chain test below.)

`resume_pending_action` has the source message, thread/workflow identifiers, and the resolved action dict in scope at that point — use a dedicated `with database.SessionLocal() as s: ...; s.commit()` block. Add to the Task 7 tests: (a) after a decision row is recorded with a `thread_id`, `GET /api/audit/actions?thread_id=N` (or the equivalent query) returns proposal, decision, and execution rows sharing one `audit_ref`; (b) **failure-path test** — a resume whose graph invocation raises still leaves the `hitl_decision` row persisted (`approved`), with the execution row `attempted`/`error` telling the rest of the story.

- [ ] **Step 7: Write integration-style tests for proposal + decision rows**

```python
# append to tests/test_audit_hitl_capture.py
def test_proposal_and_decision_rows_roundtrip(session):
    from app.models import AgentActionAudit
    from app.services.audit_trail import record_hitl_decision, record_hitl_proposal

    [proposal] = pending_actions_from_interrupts([_interrupt()], persona=None, source_meta=None)
    entry = proposal.model_dump(mode="json")
    record_hitl_proposal(session, proposal=entry, tool_class="domain_write",
                         context={"actor": "desk_user"})
    session.commit()
    prop_row = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").one()

    record_hitl_decision(session, action=entry, decision="approved", actor="desk_user")
    session.commit()
    dec_row = session.query(AgentActionAudit).filter_by(kind="hitl_decision").one()
    assert dec_row.audit_ref == prop_row.audit_ref  # the chain correlates
    assert dec_row.tool_call_id == prop_row.tool_call_id == "call_9"
```

- [ ] **Step 7b: Async bubble-up proposal test**

```python
# append to tests/test_audit_hitl_capture.py — exact fixture shape depends on
# bubble_up.py's entry function; drive it with a stub interrupt the way its own
# existing tests do (see tests/ for the bubble_up test file) and assert:
def test_async_bubble_up_records_proposal_rows(session):
    """The async projection path must insert hitl_proposal rows in the same
    transaction that persists meta['pending_actions'] (plan-review-2 finding #1)."""
    from app.models import AgentActionAudit
    # ... drive bubble_up projection with one write-tool interrupt ...
    rows = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").all()
    assert len(rows) == 1
    assert rows[0].audit_ref  # minted despite persona=None / no source_meta
```

- [ ] **Step 8: Run the new tests + the full agents/hitl test files**

Run: `.venv/bin/python -m pytest tests/test_audit_hitl_capture.py tests/ -k "hitl or pending" -v`
Expected: PASS, including all pre-existing HITL tests (the `_source_meta_for_action` change now returns a non-empty dict where `{}` was returned — if an existing test asserts `source_meta == {}`, update that assertion to check the new unconditional audit block instead; that behavior change is the point of the spec).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py backend/app/services/agents.py backend/app/services/async_agents/runner.py backend/app/services/async_agents/resume.py tests/test_audit_hitl_capture.py
git commit -m "feat(audit): unconditional audit_ref minting, audit context threading, proposal/decision rows"
```

---

### Task 8: `/api/audit` router

**Files:**
- Create: `backend/app/routers/audit.py`
- Modify: `backend/app/main.py` (import ~line 252-257, `include_router` ~line 4045-4061)
- Test: `tests/test_audit_router.py`

**Interfaces:**
- Produces: `build_audit_router() -> APIRouter` with `GET /api/audit/actions`, `GET /api/audit/actions/{id}`, `GET /api/audit/summary`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit_router.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentActionAudit
from app.routers.audit import build_audit_router


@pytest.fixture()
def client(session):
    app = FastAPI()
    app.include_router(build_audit_router())
    return TestClient(app)


def _seed(session):
    rows = [
        AgentActionAudit(kind="execution", status="ok", tool_name="book_position",
                         tool_class="domain_write", mode="yolo", tool_call_id="c1"),
        AgentActionAudit(kind="execution", status="denied", deny_reason="capability",
                         tool_name="delete_position", tool_class="domain_write", mode="interactive"),
        AgentActionAudit(kind="hitl_decision", status="rejected", tool_name="book_position",
                         tool_class="domain_write", actor="desk_user", audit_ref="r1"),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def test_list_filters_and_pagination(client, session):
    _seed(session)
    body = client.get("/api/audit/actions").json()
    assert body["total"] == 3
    assert body["items"][0]["id"] > body["items"][-1]["id"]  # newest first
    assert client.get("/api/audit/actions?status=denied").json()["total"] == 1
    assert client.get("/api/audit/actions?mode=yolo").json()["total"] == 1
    assert client.get("/api/audit/actions?kind=hitl_decision").json()["total"] == 1
    assert client.get("/api/audit/actions?tool_name=book_position").json()["total"] == 2
    assert len(client.get("/api/audit/actions?limit=2").json()["items"]) == 2
    assert client.get("/api/audit/actions?limit=500").status_code == 422  # cap 200


def test_detail_and_404(client, session):
    rows = _seed(session)
    detail = client.get(f"/api/audit/actions/{rows[0].id}").json()
    assert detail["tool_name"] == "book_position"
    assert client.get("/api/audit/actions/99999").status_code == 404


def test_summary_counts(client, session):
    _seed(session)
    body = client.get("/api/audit/summary").json()
    assert body["by_status"]["ok"] == 1
    assert body["by_status"]["denied"] == 1
    assert body["by_mode"]["yolo"] == 1
    assert body["by_class"]["domain_write"] == 3
    assert body["fail_closed_refusals"]["unpersisted"] >= 0


def test_read_only_surface(client):
    assert client.post("/api/audit/actions", json={}).status_code == 405
    assert client.delete("/api/audit/actions/1").status_code == 405
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audit_router.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the router**

```python
# backend/app/routers/audit.py
"""Read-only API over agent_action_audits (audit spec §6).

READ-ONLY BY DOCTRINE (same rule as tracing.py): the audit trail is
append-only evidence; no mutating endpoint may ever be added here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app import database
from app.models import AgentActionAudit
from app.services.audit_trail import unpersisted_refusals

_FILTERABLE = ("status", "kind", "tool_name", "tool_class", "mode")


class AuditActionOut(BaseModel):
    id: int
    kind: str
    status: str
    deny_reason: str | None
    tool_name: str
    tool_class: str
    tool_call_id: str | None
    audit_ref: str | None
    mode: str | None
    envelope: str | None
    actor: str
    model: str | None
    persona: str | None
    thread_id: int | None
    workflow_id: int | None
    session_id: int | None
    task_id: int | None
    message_id: int | None
    desk_workflow_slug: str | None
    args_json: Any
    redacted: bool
    result_preview: str | None
    error: str | None
    occurred_at: Any
    completed_at: Any


def _out(row: AgentActionAudit) -> dict:
    return AuditActionOut(
        **{f: getattr(row, f) for f in AuditActionOut.model_fields}
    ).model_dump()


def build_audit_router() -> APIRouter:
    router = APIRouter(prefix="/api/audit", tags=["audit"])

    @router.get("/actions")
    def list_actions(
        status: str | None = None,
        kind: str | None = None,
        tool_name: str | None = None,
        tool_class: str | None = None,
        mode: str | None = None,
        thread_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = Query(50, le=200, ge=1),
        offset: int = Query(0, ge=0),
    ):
        with database.SessionLocal() as session:
            q = session.query(AgentActionAudit)
            for field, value in zip(
                _FILTERABLE, (status, kind, tool_name, tool_class, mode)
            ):
                if value is not None:
                    q = q.filter(getattr(AgentActionAudit, field) == value)
            if thread_id is not None:
                q = q.filter(AgentActionAudit.thread_id == thread_id)
            if since is not None:
                q = q.filter(AgentActionAudit.occurred_at >= since)
            if until is not None:
                q = q.filter(AgentActionAudit.occurred_at <= until)
            total = q.count()
            rows = (
                q.order_by(AgentActionAudit.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {"items": [_out(r) for r in rows], "total": total}

    @router.get("/actions/{action_id}")
    def get_action(action_id: int):
        with database.SessionLocal() as session:
            row = session.get(AgentActionAudit, action_id)
            if row is None:
                raise HTTPException(404, "audit action not found")
            related = []
            if row.audit_ref:
                related = (
                    session.query(AgentActionAudit)
                    .filter(
                        AgentActionAudit.audit_ref == row.audit_ref,
                        AgentActionAudit.id != row.id,
                    )
                    .order_by(AgentActionAudit.id)
                    .all()
                )
            elif row.tool_call_id and row.thread_id is not None:
                related = (
                    session.query(AgentActionAudit)
                    .filter(
                        AgentActionAudit.thread_id == row.thread_id,
                        AgentActionAudit.tool_call_id == row.tool_call_id,
                        AgentActionAudit.id != row.id,
                    )
                    .order_by(AgentActionAudit.id)
                    .all()
                )
            return {**_out(row), "related": [_out(r) for r in related]}

    @router.get("/summary")
    def summary(since: datetime | None = None):
        with database.SessionLocal() as session:
            q = session.query(AgentActionAudit)
            if since is not None:
                q = q.filter(AgentActionAudit.occurred_at >= since)

            def _counts(col):
                rows = (
                    q.with_entities(col, func.count())
                    .group_by(col)
                    .all()
                )
                return {str(k): v for k, v in rows if k is not None}

            refused = (
                q.filter(AgentActionAudit.status == "refused").count()
            )
            return {
                "by_status": _counts(AgentActionAudit.status),
                "by_class": _counts(AgentActionAudit.tool_class),
                "by_mode": _counts(AgentActionAudit.mode),
                "fail_closed_refusals": {
                    "persisted": refused,
                    "unpersisted": unpersisted_refusals(),
                },
            }

    return router
```

- [ ] **Step 4: Register in `main.py`** — add alongside the other router imports and includes:

```python
from .routers.audit import build_audit_router
# ... in create_app():
    app.include_router(build_audit_router())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audit_router.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/audit.py backend/app/main.py tests/test_audit_router.py
git commit -m "feat(audit): read-only /api/audit router (actions, detail+related, summary)"
```

---

### Task 9: Frontend types, API client, routing, nav

**Files:**
- Modify: `frontend/src/types.ts` (Route union line 2-25 + new interfaces at end)
- Modify: `frontend/src/lib/routing.ts:8` (`ROUTE_PATHS`)
- Modify: `frontend/src/api/client.ts` (new fetchers)
- Modify: `frontend/src/main.tsx` (navItems ~line 45, import ~37-40, route switch ~274-325)

**Interfaces:**
- Produces: `Route` gains `'audit'`; `AuditAction`, `AuditActionDetail`, `AuditSummary` types; `listAuditActions(params)`, `getAuditAction(id)`, `fetchAuditSummary()` client fns; `/audit` path + nav item.

- [ ] **Step 1: Add types** (`frontend/src/types.ts`)

```typescript
// append to types.ts
export interface AuditAction {
  id: number;
  kind: 'execution' | 'hitl_proposal' | 'hitl_decision';
  status:
    | 'attempted' | 'ok' | 'error' | 'denied' | 'interrupted' | 'refused'
    | 'proposed' | 'approved' | 'rejected';
  deny_reason: string | null;
  tool_name: string;
  tool_class: 'domain_write' | 'async_dispatch' | 'fs_write' | 'artifact_write';
  tool_call_id: string | null;
  audit_ref: string | null;
  mode: string | null;
  envelope: string | null;
  actor: string;
  model: string | null;
  persona: string | null;
  thread_id: number | null;
  workflow_id: number | null;
  session_id: number | null;
  task_id: number | null;
  message_id: number | null;
  desk_workflow_slug: string | null;
  args_json: Record<string, unknown>;
  redacted: boolean;
  result_preview: string | null;
  error: string | null;
  occurred_at: string;
  completed_at: string | null;
}

export interface AuditActionDetail extends AuditAction {
  related: AuditAction[];
}

export interface AuditSummary {
  by_status: Record<string, number>;
  by_class: Record<string, number>;
  by_mode: Record<string, number>;
  fail_closed_refusals: { persisted: number; unpersisted: number };
}
```

Add `| 'audit'` to the `Route` union.

- [ ] **Step 2: Add client fns** (`frontend/src/api/client.ts`, near the tracing fetchers)

```typescript
export interface AuditListParams {
  status?: string;
  kind?: string;
  tool_name?: string;
  tool_class?: string;
  mode?: string;
  limit?: number;
  offset?: number;
}

export function listAuditActions(
  params: AuditListParams = {},
): Promise<{ items: AuditAction[]; total: number }> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const qs = search.toString();
  return api(`/api/audit/actions${qs ? `?${qs}` : ''}`);
}

export function getAuditAction(id: number): Promise<AuditActionDetail> {
  return api(`/api/audit/actions/${id}`);
}

export function fetchAuditSummary(): Promise<AuditSummary> {
  return api('/api/audit/summary');
}
```

(with `AuditAction, AuditActionDetail, AuditSummary` added to the existing type-import block).

- [ ] **Step 3: Register the route on EVERY navigation surface** —
  - `routing.ts` `ROUTE_PATHS`: add `audit: '/audit',`.
  - `main.tsx`: add `{ route: 'audit' as const, label: 'Audit' }` to `navItems` (after `tracing` for adjacency), `import { AuditLive } from './routes/Audit.live';`, and `{route === 'audit' && <AuditLive />}` in the route switch.
  - **Command palette**: `main.tsx` (or wherever the hardcoded jump items live — grep `jump-`) gains a `jump-audit` item mirroring `jump-tracing`.
  - **Routing tests**: `frontend/src/lib/routing.test.ts` asserts the exact navigable-route count/path set — update it for `audit: '/audit'` (otherwise the existing suite fails even though `tsc` passes).

- [ ] **Step 4: Type-check** (Audit.live doesn't exist yet — create a placeholder in Task 10 order, or do Tasks 9+10 in one working session and type-check at the end; the commit lands with Task 10)

Run after Task 10: `cd frontend && npx tsc --noEmit`
Expected: clean

---

### Task 10: Audit page (pure + live + css + tests)

**Files:**
- Create: `frontend/src/routes/Audit.tsx`, `frontend/src/routes/Audit.live.tsx`, `frontend/src/routes/Audit.css`
- Test: `frontend/src/routes/Audit.test.tsx`

**Interfaces:**
- Consumes: Task 9 types/fns, shared `PageScaffold`, `PageToolbar`/`PageToolbarSpacer`/`PageToolbarSearch`, `Table`, `Badge`, `Select`, `Modal`, `Empty`, `Button` primitives.

- [ ] **Step 1: Write the pure component** (`Audit.tsx`)

```tsx
import { useMemo } from 'react';
import type { AuditAction, AuditActionDetail, AuditSummary } from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { PageToolbar, PageToolbarSearch, PageToolbarSpacer } from '../components/PageToolbar';
import { Table, type Column } from '../components/Table';
import { Badge } from '../components/Badge';
import { Select } from '../components/Select';
import { Modal } from '../components/Modal';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import './Audit.css';

export interface AuditProps {
  items: AuditAction[];
  total: number;
  summary: AuditSummary | null;
  loading: boolean;
  error: string | null;
  search: string;
  statusFilter: string;
  classFilter: string;
  modeFilter: string;
  detail: AuditActionDetail | null;
  onSearch: (value: string) => void;
  onStatusFilter: (value: string) => void;
  onClassFilter: (value: string) => void;
  onModeFilter: (value: string) => void;
  onRowClick: (row: AuditAction) => void;
  onCloseDetail: () => void;
  onLoadMore: () => void;
  onRefresh: () => void;
}

const STATUS_TONE: Record<string, 'ok' | 'danger' | 'warn' | 'muted'> = {
  ok: 'ok', approved: 'ok',
  denied: 'danger', error: 'danger', refused: 'danger',
  attempted: 'warn', interrupted: 'warn', proposed: 'warn',
  rejected: 'muted',
};

function statusBadge(status: string) {
  return (
    <Badge tone={STATUS_TONE[status] ?? 'muted'} data-testid="audit-status">
      {status}
    </Badge>
  );
}

function argsSummary(row: AuditAction): string {
  const keys = Object.keys(row.args_json ?? {});
  if (!keys.length) return '—';
  const first = keys
    .slice(0, 3)
    .map((k) => `${k}=${JSON.stringify(row.args_json[k])}`)
    .join(', ');
  return keys.length > 3 ? `${first}, +${keys.length - 3}` : first;
}

export function Audit(props: AuditProps) {
  const {
    items, total, summary, loading, error, search,
    statusFilter, classFilter, modeFilter, detail,
  } = props;

  const columns = useMemo<Column<AuditAction>[]>(
    () => [
      {
        key: 'occurred_at', header: 'Time', width: '11rem',
        render: (r) => new Date(r.occurred_at + 'Z').toLocaleString(),
      },
      { key: 'tool_name', header: 'Tool', width: 'minmax(0, 1.2fr)' },
      {
        key: 'tool_class', header: 'Class', width: '8.5rem',
        render: (r) => <Badge tone="muted">{r.tool_class}</Badge>,
      },
      { key: 'status', header: 'Status', width: '7.5rem', render: (r) => statusBadge(r.status) },
      {
        key: 'mode', header: 'Mode', width: '6.5rem',
        render: (r) =>
          r.mode ? (
            <Badge tone={r.mode === 'yolo' ? 'danger' : 'muted'}>{r.mode}</Badge>
          ) : ('—'),
      },
      { key: 'actor', header: 'Actor', width: '7rem' },
      {
        key: 'args', header: 'Args', width: 'minmax(0, 2fr)',
        render: (r) => <span className="audit__args">{argsSummary(r)}</span>,
      },
    ],
    [],
  );

  const chips = summary
    ? [
        `${total} records`,
        `denied ${summary.by_status.denied ?? 0}`,
        `yolo ${summary.by_mode.yolo ?? 0}`,
        `refused ${summary.fail_closed_refusals.persisted + summary.fail_closed_refusals.unpersisted}`,
      ]
    : [`${total} records`];

  return (
    <PageScaffold title="Audit" chips={chips} feedback={error}>
      <PageToolbar>
        <PageToolbarSearch value={search} onChange={props.onSearch} placeholder="Filter by tool name…" />
        <Select value={statusFilter} onChange={props.onStatusFilter}
          options={['', 'ok', 'error', 'denied', 'refused', 'attempted', 'interrupted', 'proposed', 'approved', 'rejected']}
          placeholder="Status" />
        <Select value={classFilter} onChange={props.onClassFilter}
          options={['', 'domain_write', 'async_dispatch', 'fs_write', 'artifact_write']}
          placeholder="Class" />
        <Select value={modeFilter} onChange={props.onModeFilter}
          options={['', 'interactive', 'auto', 'yolo']} placeholder="Mode" />
        <PageToolbarSpacer />
        <Button onClick={props.onRefresh} disabled={loading}>Refresh</Button>
      </PageToolbar>
      {items.length === 0 && !loading ? (
        <Empty title="No audit records" hint="Dangerous agent actions will appear here." />
      ) : (
        <>
          <Table columns={columns} rows={items} rowKey={(r) => r.id} onRowClick={props.onRowClick} />
          {items.length < total && (
            <div className="audit__more">
              <Button onClick={props.onLoadMore} disabled={loading}>Load more</Button>
            </div>
          )}
        </>
      )}
      {detail && (
        <Modal title={`${detail.tool_name} — ${detail.status}`} onClose={props.onCloseDetail}>
          <div className="audit__detail">
            <dl className="audit__meta">
              <dt>Kind</dt><dd>{detail.kind}</dd>
              <dt>Actor</dt><dd>{detail.actor}</dd>
              <dt>Mode</dt><dd>{detail.mode ?? '—'}</dd>
              <dt>Model</dt><dd>{detail.model ?? '—'}</dd>
              <dt>Persona</dt><dd>{detail.persona ?? '—'}</dd>
              <dt>Thread</dt>
              <dd>
                {detail.thread_id != null ? (
                  <a href={`/tracing?thread=${detail.thread_id}`}>#{detail.thread_id}</a>
                ) : ('—')}
              </dd>
              <dt>Deny reason</dt><dd>{detail.deny_reason ?? '—'}</dd>
              <dt>Redacted</dt><dd>{detail.redacted ? 'yes' : 'no'}</dd>
            </dl>
            <h4>Args</h4>
            <pre className="audit__json">{JSON.stringify(detail.args_json, null, 2)}</pre>
            {detail.result_preview && (
              <>
                <h4>Result</h4>
                <pre className="audit__json">{detail.result_preview}</pre>
              </>
            )}
            {detail.error && (
              <>
                <h4>Error</h4>
                <pre className="audit__json audit__json--error">{detail.error}</pre>
              </>
            )}
            {detail.related.length > 0 && (
              <>
                <h4>Related (same action chain)</h4>
                <ul className="audit__related">
                  {detail.related.map((r) => (
                    <li key={r.id}>
                      {r.kind} — {statusBadge(r.status)} · {new Date(r.occurred_at + 'Z').toLocaleString()}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </Modal>
      )}
    </PageScaffold>
  );
}
```

**Adapt to the real primitive APIs at implementation time** — check `Select`/`Badge`/`Modal`/`Empty`/`Table` prop names against their source before writing (e.g. `Badge` may use `variant` not `tone`; `Table` may use `getKey` not `rowKey`; `PageScaffold` chips may take objects). The structure and class names above are the contract; prop-name mismatches are expected to be fixed inline.

- [ ] **Step 2: Write the live container** (`Audit.live.tsx`)

```tsx
import { useCallback, useEffect, useState } from 'react';
import type { AuditAction, AuditActionDetail, AuditSummary } from '../types';
import { fetchAuditSummary, getAuditAction, listAuditActions } from '../api/client';
import { Audit } from './Audit';

const PAGE = 50;

export function AuditLive() {
  const [items, setItems] = useState<AuditAction[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [detail, setDetail] = useState<AuditActionDetail | null>(null);

  const load = useCallback(
    async (offset = 0) => {
      setLoading(true);
      setError(null);
      try {
        const [page, sum] = await Promise.all([
          listAuditActions({
            tool_name: search || undefined,
            status: statusFilter || undefined,
            tool_class: classFilter || undefined,
            mode: modeFilter || undefined,
            limit: PAGE,
            offset,
          }),
          fetchAuditSummary(),
        ]);
        setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
        setTotal(page.total);
        setSummary(sum);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [search, statusFilter, classFilter, modeFilter],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const onRowClick = useCallback(async (row: AuditAction) => {
    try {
      setDetail(await getAuditAction(row.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  return (
    <Audit
      items={items} total={total} summary={summary} loading={loading} error={error}
      search={search} statusFilter={statusFilter} classFilter={classFilter}
      modeFilter={modeFilter} detail={detail}
      onSearch={setSearch} onStatusFilter={setStatusFilter}
      onClassFilter={setClassFilter} onModeFilter={setModeFilter}
      onRowClick={onRowClick} onCloseDetail={() => setDetail(null)}
      onLoadMore={() => void load(items.length)} onRefresh={() => void load(0)}
    />
  );
}
```

- [ ] **Step 3: Write the CSS** (`Audit.css` — token-only, per frontend/CLAUDE.md)

```css
.audit__args {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
}

.audit__more {
  display: flex;
  justify-content: center;
  padding: var(--space-3) 0;
}

.audit__detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.audit__meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-1) var(--space-3);
  margin: 0;
}

.audit__meta dt {
  color: var(--text-muted);
}

.audit__meta dd {
  margin: 0;
}

.audit__json {
  background: var(--surface-sunken);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-2);
  overflow: auto;
  max-height: 16rem;
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
}

.audit__json--error {
  color: var(--status-danger-text);
}

.audit__related {
  margin: 0;
  padding-left: var(--space-4);
}
```

**Verify every token above exists in `frontend/src/tokens/`** before using; substitute the project's actual token names (e.g. the danger-text token may be `--tone-danger-text`). Never invent tokens.

- [ ] **Step 4: Write component tests**

```tsx
// frontend/src/routes/Audit.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Audit, type AuditProps } from './Audit';
import type { AuditAction } from '../types';

const ROW: AuditAction = {
  id: 1, kind: 'execution', status: 'ok', deny_reason: null,
  tool_name: 'book_position', tool_class: 'domain_write', tool_call_id: 'c1',
  audit_ref: null, mode: 'yolo', envelope: null, actor: 'desk_user',
  model: null, persona: 'trader', thread_id: 7, workflow_id: null,
  session_id: null, task_id: null, message_id: null, desk_workflow_slug: null,
  args_json: { qty: 1 }, redacted: false, result_preview: null, error: null,
  occurred_at: '2026-07-02T10:00:00', completed_at: '2026-07-02T10:00:01',
};

function props(overrides: Partial<AuditProps> = {}): AuditProps {
  return {
    items: [ROW], total: 1, summary: null, loading: false, error: null,
    search: '', statusFilter: '', classFilter: '', modeFilter: '', detail: null,
    onSearch: vi.fn(), onStatusFilter: vi.fn(), onClassFilter: vi.fn(),
    onModeFilter: vi.fn(), onRowClick: vi.fn(), onCloseDetail: vi.fn(),
    onLoadMore: vi.fn(), onRefresh: vi.fn(),
    ...overrides,
  };
}

describe('Audit', () => {
  it('renders rows with tool, status and yolo mode badge', () => {
    render(<Audit {...props()} />);
    expect(screen.getByText('book_position')).toBeInTheDocument();
    expect(screen.getByText('ok')).toBeInTheDocument();
    expect(screen.getByText('yolo')).toBeInTheDocument();
  });

  it('renders empty state', () => {
    render(<Audit {...props({ items: [], total: 0 })} />);
    expect(screen.getByText('No audit records')).toBeInTheDocument();
  });

  it('renders detail modal with args and related chain', () => {
    render(
      <Audit
        {...props({
          detail: {
            ...ROW, related: [{ ...ROW, id: 2, kind: 'hitl_decision', status: 'approved' }],
          },
        })}
      />,
    );
    expect(screen.getByText(/"qty": 1/)).toBeInTheDocument();
    expect(screen.getByText('hitl_decision', { exact: false })).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run the FULL frontend suite + typecheck** (not just the Audit tests — routing.test.ts and any nav-surface tests must pass with the new route)

Run: `cd frontend && npm test -- --run && npx tsc --noEmit`
Expected: PASS / clean

- [ ] **Step 6: Commit** (includes Task 9 files)

```bash
git add frontend/src/types.ts frontend/src/lib/routing.ts frontend/src/api/client.ts frontend/src/main.tsx frontend/src/routes/Audit.tsx frontend/src/routes/Audit.live.tsx frontend/src/routes/Audit.css frontend/src/routes/Audit.test.tsx
git commit -m "feat(audit): Audit page — filterable dangerous-action trail with detail modal"
```

---

### Task 11: Full-suite verification

**Files:** none new.

- [ ] **Step 1: Backend full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS (watch the known traps: `.env` tracing leak affects `test_tracing_config` only in dirty environments; skill-catalog counts unaffected — no skill files added)

- [ ] **Step 2: Frontend full suite + typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS / clean

- [ ] **Step 3: Live smoke of the API** (backend running on :8001)

Run: `curl -s localhost:8001/api/audit/summary | python3 -m json.tool`
Expected: JSON with `by_status` / `fail_closed_refusals` keys

- [ ] **Step 4: Commit any straggler fixes**

```bash
git add -A && git commit -m "test(audit): full-suite fixes"
```

---

## Coverage note — async integration test

Spec §8 asks for an end-to-end async write test. A live background async run needs a
real/mocked LLM loop; instead coverage is **compositional** and equivalent: Task 6
proves `build_async_agent`'s stack carries the middleware, Task 5 proves the
middleware records every classified write it wraps, Task 7 proves the async
projection path (persona=None, no source_meta) mints `audit_ref` and that
proposal/decision rows correlate. If a future async harness with a scripted model
lands, add the end-to-end variant on top.

## Execution notes

- **Order matters:** Tasks 1→8 are backend-sequential (each consumes the previous), 9→10 frontend, 11 last.
- **Line numbers drift** in `agents.py` (shared checkout) — locate the sites by the grep patterns given in each task, not by absolute line.
- **Existing-test fallout to expect:** HITL tests asserting `source_meta == {}` (Task 7 changes that contract deliberately); persona middleware index assertions if any test pins `FanoutReadOnlyMiddleware` at index 1.
