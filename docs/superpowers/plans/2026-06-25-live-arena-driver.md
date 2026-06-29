# Live Arena Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the arena's `NotImplementedError` driver stub so a match drives the real desk orchestrator against a golden workflow and scores it from the trace DB.

**Architecture:** Each match seeds fresh fixtures into the main DB, creates an arena-tagged `AgentThread`, drives each workflow step through the production `AgentService.stream_and_persist` bound to a Zenmux model, then reconstructs the `MatchTranscript` from the persisted trace spans (`data/agent_traces.sqlite3`). `skills_routed` is read as ground truth from `read_file` SKILL.md loads. Scoring/judge/store are untouched.

**Tech Stack:** Python 3.11, SQLAlchemy, Alembic, FastAPI, pytest (backend); React + TypeScript + Vitest (frontend); LangChain/LangGraph deep-agent; SQLite.

## Global Constraints

- Table name is `agent_threads` (plural). Migration revision `0033_agent_thread_source`, `down_revision = "0032_arena_runs"`.
- Repo import root is `app.` (never `backend.app.` in source). Migration tests import modules as `backend.alembic.versions.<rev>` (matches `test_arena_migration.py`).
- Migrations use migration-local Core tables / `op` calls only — never ORM models or services.
- `skills_routed` source = `read_file` tool spans whose `inputs.file_path` matches `^/skills/workflows/.+/([a-z0-9-]+)/SKILL\.md$`; skill name = capture group 1; ordered by span `start_time`.
- Meta tools excluded from `tool_calls`/`tool_results`: `{"task", "read_file", "write_todos"}`.
- Persona→character map: `trader→trader`, `risk_manager→risk_manager`, `sales→trader`, `quant→trader`.
- Arena turns run `stream_and_persist(..., yolo_mode=True, confirmed_cost_preview=True)` → exactly one orchestrator root trace per turn.
- Arena threads tagged `source="arena"`, `arena_run_id=<run id or None>`. `source` defaults to `'desk'` for all other threads.
- Frontend: token-only styling, `wl-` prefixed BEM, reuse primitives (see `frontend/CLAUDE.md`). Arena threads hidden by default.
- The `MatchTranscript` / `MatchStep` shapes in `app/golden_workflows/transcript.py` are fixed; the harvester must emit the `turn_events` dict that `extract_step_from_events` consumes.
- Keep `run_match` injectable for tests via `drive=` and `harvest=` parameters.

---

### Task 1: Data model — `agent_threads.source` + `arena_run_id`

**Files:**
- Create: `backend/alembic/versions/0033_agent_thread_source.py`
- Modify: `backend/app/models.py:124-146` (AgentThread)
- Modify: `backend/app/schemas.py:213-221` (AgentThreadOut)
- Test: `tests/test_migration_0033.py`, `tests/test_agent_thread_source_schema.py`

**Interfaces:**
- Produces: `AgentThread.source: str` (default `"desk"`), `AgentThread.arena_run_id: int | None`; `AgentThreadOut.source: str`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migration_0033.py`. The migration adds columns to `agent_threads`, so the test must first create a minimal `agent_threads` table, then drive the migration directly (same style as `tests/test_arena_migration.py`).

```python
"""Round-trip test for migration 0033_agent_thread_source."""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _run_migration(module, method: str, engine: sa.Engine) -> None:
    connection = engine.connect()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original_op
        connection.close()


def _engine_with_agent_threads(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE agent_threads ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title VARCHAR(200),"
            " character VARCHAR(40))"
        ))
    return engine


def test_upgrade_adds_source_and_arena_run_id(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "up.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("agent_threads")}
    assert "source" in cols
    assert "arena_run_id" in cols
    assert cols["arena_run_id"]["nullable"] is True
    idx = {i["name"] for i in insp.get_indexes("agent_threads")}
    assert "ix_agent_threads_arena_run_id" in idx


def test_source_defaults_to_desk(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "def.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO agent_threads (title, character) VALUES ('t', 'trader')"))
        row = conn.execute(sa.text("SELECT source, arena_run_id FROM agent_threads")).fetchone()
    assert row[0] == "desk"
    assert row[1] is None


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "idem.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise


def test_downgrade_removes_columns(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "down.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "downgrade", engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("agent_threads")}
    assert "source" not in cols
    assert "arena_run_id" not in cols
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_migration_0033.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.alembic.versions.0033_agent_thread_source`.

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/0033_agent_thread_source.py`:

```python
"""agent_threads.source and arena_run_id

Revision ID: 0033_agent_thread_source
Revises: 0032_arena_runs
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0033_agent_thread_source"
down_revision = "0032_arena_runs"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    cols = _columns("agent_threads")
    if "source" not in cols:
        op.add_column(
            "agent_threads",
            sa.Column("source", sa.String(length=20), nullable=False, server_default="desk"),
        )
    if "arena_run_id" not in cols:
        op.add_column(
            "agent_threads",
            sa.Column("arena_run_id", sa.Integer(), nullable=True),
        )
    if "ix_agent_threads_arena_run_id" not in _indexes("agent_threads"):
        op.create_index("ix_agent_threads_arena_run_id", "agent_threads", ["arena_run_id"])


def downgrade() -> None:
    if "ix_agent_threads_arena_run_id" in _indexes("agent_threads"):
        op.drop_index("ix_agent_threads_arena_run_id", table_name="agent_threads")
    cols = _columns("agent_threads")
    with op.batch_alter_table("agent_threads") as batch:
        if "arena_run_id" in cols:
            batch.drop_column("arena_run_id")
        if "source" in cols:
            batch.drop_column("source")
```

- [ ] **Step 4: Run the migration test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_migration_0033.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the ORM columns**

In `backend/app/models.py`, inside `class AgentThread`, after the `report_currency` column (line 133), add:

```python
    source: Mapped[str] = mapped_column(
        String(20), default="desk", server_default="desk", nullable=False
    )
    arena_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
```

- [ ] **Step 6: Write the failing schema test**

Create `tests/test_agent_thread_source_schema.py`:

```python
from app.models import AgentThread
from app.schemas import AgentThreadOut


def test_agent_thread_out_serialises_source():
    thread = AgentThread(id=7, title="t", character="trader", source="arena")
    out = AgentThreadOut.model_validate(thread)
    assert out.source == "arena"


def test_agent_thread_out_source_defaults_to_desk():
    # A freshly-constructed ORM object without source set serialises 'desk'.
    thread = AgentThread(id=8, title="t", character="trader")
    out = AgentThreadOut.model_validate(thread)
    assert out.source == "desk"
```

- [ ] **Step 7: Run the schema test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_agent_thread_source_schema.py -v`
Expected: FAIL — `AgentThreadOut` has no `source` (validation error or AttributeError).

- [ ] **Step 8: Add the schema field**

In `backend/app/schemas.py`, inside `class AgentThreadOut` (after `character: str`, line ~216) add:

```python
    source: str = "desk"
```

- [ ] **Step 9: Run both test files to verify they pass**

Run: `cd backend && python -m pytest ../tests/test_migration_0033.py ../tests/test_agent_thread_source_schema.py -v`
Expected: PASS (6 tests).

- [ ] **Step 10: Commit**

```bash
git add backend/alembic/versions/0033_agent_thread_source.py backend/app/models.py backend/app/schemas.py tests/test_migration_0033.py tests/test_agent_thread_source_schema.py
git commit -m "feat(arena): agent_threads.source + arena_run_id (migration 0033)"
```

---

### Task 2: `arena_model_to_selection` helper

**Files:**
- Modify: `backend/app/services/arena/models.py` (append helper)
- Test: `tests/test_arena_model_selection.py`

**Interfaces:**
- Consumes: `ArenaModel.zenmux_name` (e.g. `"openai/gpt-5.5"`).
- Produces: `arena_model_to_selection(model: ArenaModel) -> dict[str, str]` returning `{"channel": "zenmux", "provider": <vendor>, "model": <name>}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_model_selection.py`:

```python
import pytest

from app.services.arena.models import ArenaModel, arena_model_to_selection


def _m(zenmux_name: str) -> ArenaModel:
    return ArenaModel(slug="x", zenmux_name=zenmux_name, display_name="X", default_config={})


def test_splits_vendor_and_model():
    sel = arena_model_to_selection(_m("openai/gpt-5.5"))
    assert sel == {"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}


def test_anthropic_slug():
    sel = arena_model_to_selection(_m("anthropic/claude-opus-4.8"))
    assert sel == {"channel": "zenmux", "provider": "anthropic", "model": "claude-opus-4.8"}


def test_missing_slash_raises():
    with pytest.raises(ValueError):
        arena_model_to_selection(_m("gpt-5.5"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_arena_model_selection.py -v`
Expected: FAIL — `ImportError: cannot import name 'arena_model_to_selection'`.

- [ ] **Step 3: Implement the helper**

Append to `backend/app/services/arena/models.py`:

```python
def arena_model_to_selection(model: ArenaModel) -> dict[str, str]:
    """Map an ArenaModel's zenmux_name to a desk model_selection dict.

    "openai/gpt-5.5" -> {"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}

    Raises:
        ValueError: if zenmux_name does not contain a '<vendor>/<model>' slash.
    """
    name = model.zenmux_name
    if "/" not in name:
        raise ValueError(
            f"zenmux_name '{name}' must be '<vendor>/<model>' (e.g. 'openai/gpt-5.5')."
        )
    provider, _, model_name = name.partition("/")
    return {"channel": "zenmux", "provider": provider, "model": model_name}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_arena_model_selection.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/models.py tests/test_arena_model_selection.py
git commit -m "feat(arena): arena_model_to_selection zenmux mapping"
```

---

### Task 3: Trace-harvest module — `transcript_from_trace`

**Files:**
- Create: `backend/app/services/arena/trace_harvest.py`
- Test: `tests/test_arena_trace_harvest.py`

**Interfaces:**
- Consumes: `MatchTranscript`, `extract_step_from_events` from `app.golden_workflows.transcript`; a `store` object with `list_thread_traces(thread_id, *, limit)` and `get_trace(trace_id)` (same shape as `app.services.tracing.store.TraceStore`); a `GoldenWorkflow` (`.id`, `.steps[].user`); an `ArenaModel` (`.slug`).
- Produces: `transcript_from_trace(thread_id: int, workflow, model, *, store=None) -> MatchTranscript`; helpers `_spans_to_turn_events`, `_parse_tool_output`, `_llm_text`.

**Background — exact trace formats (verified against `data/agent_traces.sqlite3`):**
- A tool span's `outputs` column is JSON: `{"output": "content='<inner-json>' name='<tool>' tool_call_id='<id>'"}`. The inner content is a JSON object; for `run_batch_pricing` it includes `"task_id": 12`. Artifact-producing tools embed `"artifacts": [{"path":..., "kind":"text", ...}]` in that content.
- An LLM span's `outputs` is `{"generations": [[{"text": "<assistant text>", ...}]]}`.
- A `read_file` span's `inputs` is `{"file_path": "/skills/workflows/risk/run-risk/SKILL.md", "limit": 1000}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_trace_harvest.py`:

```python
import json

from app.services.arena.models import ArenaModel
from app.services.arena.trace_harvest import (
    _parse_tool_output,
    _spans_to_turn_events,
    transcript_from_trace,
)


def _tool_output(content: dict, name: str, tcid: str = "tc1") -> str:
    return json.dumps({"output": f"content='{json.dumps(content)}' name='{name}' tool_call_id='{tcid}'"})


def _llm_output(text: str) -> str:
    return json.dumps({"generations": [[{"text": text}]]})


def test_parse_tool_output_extracts_content_dict():
    raw = _tool_output({"task_id": 12, "status": "queued"}, "run_batch_pricing")
    content, name, tcid = _parse_tool_output(raw)
    assert content == {"task_id": 12, "status": "queued"}
    assert name == "run_batch_pricing"
    assert tcid == "tc1"


def test_parse_tool_output_non_json_falls_back_to_raw():
    raw = json.dumps({"output": "content='plain text' name='x' tool_call_id='tc2'"})
    content, _, _ = _parse_tool_output(raw)
    assert content == {"raw": "plain text"}


def test_skills_routed_from_read_file_ordered():
    spans = [
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:02",
         "inputs": json.dumps({"file_path": "/skills/workflows/reporting/generate-report/SKILL.md"})},
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:01",
         "inputs": json.dumps({"file_path": "/skills/workflows/risk/run-risk/SKILL.md"})},
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:03",
         "inputs": json.dumps({"file_path": "/skills/references/pricing/engines.md"})},  # not a SKILL.md
    ]
    turn = _spans_to_turn_events(0, "do it", spans)
    assert turn["skills_routed"] == ["run-risk", "generate-report"]  # ordered by start_time, reference skipped


def test_tool_calls_exclude_meta_and_capture_args_results():
    spans = [
        {"run_type": "tool", "name": "task", "start_time": "1",
         "inputs": json.dumps({"subagent_type": "trader"}), "outputs": None},
        {"run_type": "tool", "name": "get_positions", "start_time": "2", "id": "sp1",
         "inputs": json.dumps({"portfolio_id": 4}),
         "outputs": _tool_output({"total_count": 2}, "get_positions", "tc9")},
        {"run_type": "llm", "name": "ChatAnthropic", "start_time": "3",
         "outputs": _llm_output("Here is your answer.")},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert [c["name"] for c in turn["tool_calls"]] == ["get_positions"]
    assert turn["tool_calls"][0]["args"] == {"portfolio_id": 4}
    assert turn["tool_results"][0]["content"] == {"total_count": 2}
    assert turn["tool_results"][0]["tool_call_id"] == "tc9"
    assert turn["response_text"] == "Here is your answer."


def test_artifacts_harvested_from_tool_content():
    spans = [
        {"run_type": "tool", "name": "write_report_artifact", "start_time": "1", "id": "sp2",
         "inputs": json.dumps({}),
         "outputs": _tool_output(
             {"file_path": "/r.md", "artifacts": [{"path": "/r.md", "kind": "text"}]},
             "write_report_artifact")},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert any(a.get("kind") == "text" for a in turn["artifacts"])


def test_error_tool_span_sets_error():
    spans = [
        {"run_type": "tool", "name": "get_positions", "start_time": "1", "id": "sp3",
         "status": "error", "error": "boom",
         "inputs": json.dumps({"portfolio_id": 4}), "outputs": None},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert turn["tool_results"][0]["error"] == "boom"


class _FakeStore:
    def __init__(self, roots, traces):
        self._roots = roots
        self._traces = traces

    def list_thread_traces(self, thread_id, *, limit=50, offset=0):
        return list(self._roots)

    def get_trace(self, trace_id):
        return self._traces.get(trace_id, [])


class _WF:
    id = "wf-x"
    class _S:
        def __init__(self, user): self.user = user
    steps = [_S("step one"), _S("step two")]


def test_transcript_from_trace_maps_roots_to_steps_in_order():
    roots = [
        {"trace_id": "T2", "start_time": "2026-01-01T00:00:05", "end_time": "2026-01-01T00:00:06"},
        {"trace_id": "T1", "start_time": "2026-01-01T00:00:01", "end_time": "2026-01-01T00:00:02"},
    ]
    traces = {
        "T1": [{"run_type": "tool", "name": "get_positions", "start_time": "1", "id": "a",
                "inputs": json.dumps({"portfolio_id": 4}),
                "outputs": _tool_output({"total_count": 2}, "get_positions", "tc1")}],
        "T2": [{"run_type": "llm", "name": "ChatAnthropic", "start_time": "2",
                "outputs": _llm_output("done")}],
    }
    model = ArenaModel(slug="m", zenmux_name="openai/x", display_name="M", default_config={})
    transcript = transcript_from_trace(99, _WF(), model, store=_FakeStore(roots, traces))
    assert transcript.model_id == "m"
    assert transcript.workflow_id == "wf-x"
    assert len(transcript.steps) == 2
    # chronological: T1 (earlier) is step 0
    assert transcript.steps[0].user == "step one"
    assert [c["name"] for c in transcript.steps[0].tool_calls] == ["get_positions"]
    assert transcript.steps[1].response_text == "done"


def test_transcript_from_trace_missing_root_records_error():
    roots = [{"trace_id": "T1", "start_time": "1", "end_time": "2"}]  # only 1 root, 2 steps
    traces = {"T1": [{"run_type": "llm", "name": "ChatAnthropic", "start_time": "1",
                      "outputs": _llm_output("hi")}]}
    model = ArenaModel(slug="m", zenmux_name="openai/x", display_name="M", default_config={})
    transcript = transcript_from_trace(1, _WF(), model, store=_FakeStore(roots, traces))
    assert len(transcript.steps) == 2
    assert transcript.steps[1].errors and transcript.steps[1].errors[0]["type"] == "missing_trace"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_arena_trace_harvest.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.arena.trace_harvest`.

- [ ] **Step 3: Implement the harvester**

Create `backend/app/services/arena/trace_harvest.py`:

```python
"""Reconstruct a MatchTranscript from persisted trace spans.

The arena drives the real desk orchestrator; every turn's tool calls, LLM
output, and skill loads are persisted by the LocalTracer. This module reads
those spans back and emits the turn_events dicts that
``extract_step_from_events`` consumes.

skills_routed is GROUND TRUTH: the deep-agent loads each skill it follows via
``read_file`` on ``/skills/workflows/<domain>/<name>/SKILL.md``. A skill the
model acts on from its injected description alone (without read_file) is NOT
captured — an accepted edge case (multi-step golden workflows read the file).
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.golden_workflows.transcript import MatchTranscript, extract_step_from_events

SKILL_PATH_RE = re.compile(r"^/skills/workflows/.+/([a-z0-9-]+)/SKILL\.md$")
TOOL_OUTPUT_RE = re.compile(
    r"^content='(?P<content>.*)' name='(?P<name>[^']*)' tool_call_id='(?P<tcid>[^']*)'\s*$",
    re.DOTALL,
)
META_TOOLS = {"task", "read_file", "write_todos"}


def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_tool_output(outputs_raw: Any) -> tuple[dict, str | None, str | None]:
    """Parse a tool span's serialized ToolMessage output.

    Returns (content_dict, tool_name, tool_call_id). Falls back to
    ``{"raw": <str>}`` content when the inner payload is not a JSON object.
    """
    parsed = _loads(outputs_raw)
    output_str = parsed.get("output") if isinstance(parsed, dict) else None
    if not isinstance(output_str, str):
        return {}, None, None
    m = TOOL_OUTPUT_RE.match(output_str)
    if not m:
        return {"raw": output_str}, None, None
    content = _loads(m.group("content"))
    if not isinstance(content, dict):
        content = {"raw": m.group("content")}
    return content, m.group("name"), m.group("tcid")


def _llm_text(outputs_raw: Any) -> str:
    parsed = _loads(outputs_raw) or {}
    try:
        return parsed["generations"][0][0]["text"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _spans_to_turn_events(index: int, user: str, spans: list[dict]) -> dict:
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    artifacts: list[dict] = []
    skill_spans: list[tuple[str, str]] = []
    response_text = ""

    for sp in spans:
        run_type = sp.get("run_type")
        name = sp.get("name", "")
        if run_type == "tool" and name == "read_file":
            inp = _loads(sp.get("inputs"))
            fp = inp.get("file_path", "") if isinstance(inp, dict) else ""
            mm = SKILL_PATH_RE.match(fp or "")
            if mm:
                skill_spans.append((sp.get("start_time") or "", mm.group(1)))
        elif run_type == "tool" and name not in META_TOOLS:
            inp = _loads(sp.get("inputs"))
            args = inp if isinstance(inp, dict) else {}
            content, _tname, tcid = _parse_tool_output(sp.get("outputs"))
            call_id = tcid or sp.get("id")
            tool_calls.append({"id": call_id, "name": name, "args": args})
            result = {"name": name, "tool_call_id": call_id, "content": content}
            if sp.get("status") == "error":
                result["error"] = sp.get("error") or "tool error"
            tool_results.append(result)
            embedded = content.get("artifacts") if isinstance(content, dict) else None
            if isinstance(embedded, list):
                artifacts.extend(a for a in embedded if isinstance(a, dict))
        elif run_type == "llm":
            txt = _llm_text(sp.get("outputs"))
            if txt:
                response_text = txt

    skills_routed = [s for _, s in sorted(skill_spans, key=lambda x: x[0])]
    return {
        "index": index,
        "user": user,
        "messages": [],
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "skills_routed": skills_routed,
        "artifacts": artifacts,
        "response_text": response_text,
        "errors": [],
    }


def transcript_from_trace(thread_id, workflow, model, *, store=None) -> MatchTranscript:
    """Build a MatchTranscript for *thread_id* by reading its trace spans.

    Root traces (one orchestrator run per turn) map 1:1 to workflow steps in
    chronological order. A step with no matching root records a ``missing_trace``
    error rather than silently dropping.
    """
    if store is None:
        from app.config import get_settings
        from app.services.tracing.store import get_trace_store
        store = get_trace_store(get_settings())

    roots = sorted(
        store.list_thread_traces(thread_id, limit=1000),
        key=lambda r: r.get("start_time") or "",
    )

    steps = []
    for i, wf_step in enumerate(workflow.steps):
        if i < len(roots):
            spans = store.get_trace(roots[i]["trace_id"])
            turn = _spans_to_turn_events(i, wf_step.user, spans)
        else:
            turn = {
                "index": i, "user": wf_step.user, "messages": [],
                "tool_calls": [], "tool_results": [], "skills_routed": [],
                "artifacts": [], "response_text": "",
                "errors": [{"type": "missing_trace", "step": i}],
            }
        steps.append(extract_step_from_events(turn))

    started_at = roots[0].get("start_time") if roots else None
    finished_at = roots[-1].get("end_time") if roots else None
    return MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=workflow.id,
        model_id=model.slug,
        started_at=started_at,
        finished_at=finished_at,
        steps=steps,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_arena_trace_harvest.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/trace_harvest.py tests/test_arena_trace_harvest.py
git commit -m "feat(arena): transcript_from_trace harvests transcript from trace spans"
```

---

### Task 4: Rewrite `run_match` to drive the real orchestrator

**Files:**
- Modify: `backend/app/services/arena/runner.py` (rewrite; delete dead code)
- Modify: `backend/app/services/arena/task.py:197` (pass `run_id=run_id` to `run_match`)
- Test: `tests/test_arena_runner.py` (rewrite to inject `drive`+`harvest`)

**Interfaces:**
- Consumes: `arena_model_to_selection` (Task 2), `transcript_from_trace` (Task 3), `AgentThread.source`/`arena_run_id` (Task 1), `apply_seed` from `app.golden_workflows.fixtures`, `database.SessionLocal`.
- Produces: `run_match(loaded, model, *, artifact_root, run_id=None, drive=None, harvest=None) -> MatchTranscript`. `drive(thread_id: int, content: str, selection: dict) -> None`. The old `agent=`/`chat=` params, `isolated_match_db`, `build_arena_agent`, `arena_tools`, `_wrap_run_tools`, `_default_status_checker`, `_drive_step`, `_make_langchain_agent_driver` are REMOVED.

**Note for the implementer:** The existing `tests/test_arena_runner.py` exercises the deleted machinery (injected `agent=`, `isolated_match_db`, `arena_tools`, run-tool blocking). Replace the whole test file with the version below — those code paths no longer exist by design (spec D1, D4).

- [ ] **Step 1: Write the new failing test file**

Replace `tests/test_arena_runner.py` entirely with:

```python
"""run_match drives the real orchestrator via injected drive+harvest seams."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.arena.models import get_model
from app.services.arena.runner import run_match, _persona_to_character


class _Step:
    def __init__(self, user): self.user = user


class _WF:
    id = "wf-test"
    persona = "risk_manager"
    steps = [_Step("first ask"), _Step("second ask")]


class _Loaded:
    workflow = _WF()
    fixtures = object()  # apply_seed is monkeypatched, so contents don't matter


def test_persona_to_character_maps_known_and_unknown():
    assert _persona_to_character("trader") == "trader"
    assert _persona_to_character("risk_manager") == "risk_manager"
    assert _persona_to_character("sales") == "trader"
    assert _persona_to_character("quant") == "trader"


def test_run_match_seeds_creates_arena_thread_and_drives_each_step(tmp_path, monkeypatch):
    created = {}
    seeded = {"called": False}

    # Stub apply_seed (no real DB write of fixtures)
    monkeypatch.setattr("app.services.arena.runner.apply_seed", lambda b, s: seeded.__setitem__("called", True))

    # Stub the DB session + thread creation
    class _Thread:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 4242
    monkeypatch.setattr("app.services.arena.runner.AgentThread", _Thread)

    class _Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, obj): created["thread"] = obj
        def commit(self): pass
    monkeypatch.setattr("app.services.arena.runner.database",
                        type("D", (), {"SessionLocal": lambda *a, **k: _Sess()})())

    drive_calls = []
    def fake_drive(thread_id, content, selection):
        drive_calls.append((thread_id, content, selection))

    # Fake harvest returns a minimal valid MatchTranscript
    from app.golden_workflows.transcript import MatchTranscript
    def fake_harvest(thread_id, workflow, model, **kw):
        assert thread_id == 4242
        return MatchTranscript(schema_version=1, run_id=None, workflow_id=workflow.id,
                               model_id=model.slug, started_at=None, finished_at=None, steps=[])

    model = get_model("gpt-5-5")
    transcript = run_match(_Loaded(), model, artifact_root=tmp_path, run_id=7,
                           drive=fake_drive, harvest=fake_harvest)

    assert seeded["called"] is True
    assert created["thread"].source == "arena"
    assert created["thread"].arena_run_id == 7
    assert created["thread"].character == "risk_manager"
    # one drive call per workflow step, in order, with the zenmux selection
    assert [c[1] for c in drive_calls] == ["first ask", "second ask"]
    assert all(c[0] == 4242 for c in drive_calls)
    assert drive_calls[0][2] == {"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}
    assert transcript.workflow_id == "wf-test"


def test_run_match_requires_no_agent_param(tmp_path):
    # The old agent=/chat= params are gone; calling with them must error.
    with pytest.raises(TypeError):
        run_match(_Loaded(), get_model("gpt-5-5"), artifact_root=tmp_path, agent=object())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_arena_runner.py -v`
Expected: FAIL — `ImportError: cannot import name '_persona_to_character'` (and the old runner still has `isolated_match_db` etc.).

- [ ] **Step 3: Rewrite `runner.py`**

Replace the entire contents of `backend/app/services/arena/runner.py` with:

```python
"""Arena match runner.

Drives a single arena match against the REAL desk orchestrator:
  1. seed the workflow's fixtures into the main DB (fresh IDs per match),
  2. create an arena-tagged AgentThread,
  3. drive each workflow step through AgentService.stream_and_persist bound to
     the candidate Zenmux model,
  4. reconstruct the MatchTranscript from the persisted trace spans.

Matches run sequentially (the async checkpointer SQLite serialises writes).
The `drive` and `harvest` seams are injectable for unit tests.
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import database
from app.golden_workflows.fixtures import apply_seed
from app.models import AgentThread
from app.services.arena.models import arena_model_to_selection
from app.services.arena.trace_harvest import transcript_from_trace

_PERSONA_TO_CHARACTER = {
    "trader": "trader",
    "risk_manager": "risk_manager",
    "sales": "trader",
    "quant": "trader",
}

_ARENA_SERVICE = None


def _persona_to_character(persona: str) -> str:
    return _PERSONA_TO_CHARACTER.get(persona, "trader")


def _get_arena_service():
    """Lazily build one AgentService for the process (model is rebound per turn)."""
    global _ARENA_SERVICE
    if _ARENA_SERVICE is None:
        from app.services.agents import AgentService
        _ARENA_SERVICE = AgentService()
    return _ARENA_SERVICE


def _default_drive(thread_id: int, content: str, selection: dict) -> None:
    """Drive one desk turn to completion via stream_and_persist (HITL auto-cleared).

    The transcript is harvested from the trace afterwards, so the streamed SSE
    events are consumed and discarded here.
    """
    svc = _get_arena_service()

    async def _run() -> None:
        async for _chunk in svc.stream_and_persist(
            thread_id=thread_id,
            content=content,
            model_selection=selection,
            yolo_mode=True,
            confirmed_cost_preview=True,
        ):
            pass

    asyncio.run(_run())


def _copy_artifacts(artifacts: list[dict], artifact_root: Path, workflow_id: str) -> list[dict]:
    """Copy artifact files under artifact_root/workflow_id/ and rewrite paths.

    Silently passes through artifacts whose ``path`` is missing or non-existent.
    """
    dest_dir = artifact_root / workflow_id
    copied = []
    for art in artifacts:
        path_str = art.get("path")
        if not path_str:
            copied.append(art)
            continue
        src = Path(path_str)
        if not src.exists():
            copied.append(art)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        copied.append({**art, "path": str(dest)})
    return copied


def run_match(
    loaded,
    model,
    *,
    artifact_root: Path,
    run_id: int | None = None,
    drive: Callable[[int, str, dict], None] | None = None,
    harvest: Callable[..., Any] | None = None,
) -> Any:
    """Run a single arena match and return a MatchTranscript.

    Args:
        loaded: LoadedWorkflow (registry.get_workflow_bundle).
        model: ArenaModel descriptor.
        artifact_root: Root directory for copied artifacts.
        run_id: ArenaRun id used to tag the created thread (None in unit tests).
        drive: Injectable turn driver ``(thread_id, content, selection) -> None``.
            Defaults to the stream_and_persist-based ``_default_drive``.
        harvest: Injectable transcript harvester ``(thread_id, workflow, model)``.
            Defaults to ``transcript_from_trace``.
    """
    drive = drive or _default_drive
    harvest = harvest or transcript_from_trace

    workflow = loaded.workflow
    artifact_root = Path(artifact_root)
    selection = arena_model_to_selection(model)

    # Seed fixtures (fresh IDs) + create the arena-tagged thread.
    with database.SessionLocal() as session:
        apply_seed(loaded.fixtures, session)
        thread = AgentThread(
            title=f"[arena] {workflow.id} · {model.slug}",
            character=_persona_to_character(workflow.persona),
            source="arena",
            arena_run_id=run_id,
        )
        session.add(thread)
        session.commit()
        thread_id = thread.id

    # Drive every workflow step as a turn on the same thread.
    for wf_step in workflow.steps:
        drive(thread_id, wf_step.user, selection)

    transcript = harvest(thread_id, workflow, model)

    # Copy any harvested artifacts under the run's artifact root.
    copied_steps = []
    for step in transcript.steps:
        if step.artifacts:
            step.artifacts = _copy_artifacts(step.artifacts, artifact_root, workflow.id)
        copied_steps.append(step)
    transcript.steps = copied_steps
    return transcript
```

- [ ] **Step 4: Wire `run_id` through `task.py`**

In `backend/app/services/arena/task.py`, line 197, change:

```python
                transcript = _run_match_fn(loaded, model, artifact_root=artifact_root)
```

to:

```python
                transcript = _run_match_fn(loaded, model, artifact_root=artifact_root, run_id=run_id)
```

- [ ] **Step 5: Run the runner test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_arena_runner.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the arena task + harvest + model + transcript tests together (no regressions)**

Run: `cd backend && python -m pytest ../tests/test_arena_runner.py ../tests/test_arena_trace_harvest.py ../tests/test_arena_model_selection.py ../tests/test_arena_task.py -v`
Expected: PASS. If `tests/test_arena_task.py` references the removed `agent=` path, update only those call sites to the injected `run_match_fn` pattern (it already injects `run_match_fn`, so it should be unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/arena/runner.py backend/app/services/arena/task.py tests/test_arena_runner.py
git commit -m "feat(arena): drive real orchestrator + trace harvest; drop isolated-db clone"
```

---

### Task 5: Agent Desk arena-thread toggle (frontend)

**Files:**
- Modify: `frontend/src/types.ts:25-32` (Thread type)
- Modify: `frontend/src/routes/AgentDesk.tsx:230-289` (toggle + filter)
- Test: `frontend/src/routes/AgentDesk.arenaToggle.test.tsx`

**Interfaces:**
- Consumes: `Thread.source` (from backend `AgentThreadOut.source`).
- Produces: a "Show arena threads" checkbox; arena threads (`source === 'arena'`) are hidden unless checked.

- [ ] **Step 1: Add `source` to the Thread type**

In `frontend/src/types.ts`, add to the `Thread` type (after `character: string;`):

```typescript
  source?: string;
```

- [ ] **Step 2: Write the failing component test**

Create `frontend/src/routes/AgentDesk.arenaToggle.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentDesk } from './AgentDesk';
import type { Thread } from '../types';

function thread(id: number, title: string, source?: string): Thread {
  return { id, title, character: 'trader', source, messages: [] };
}

const baseProps = {
  activeThreadId: null,
  sending: false,
  viewMode: 'detailed' as const,
  onChangeViewMode: vi.fn(),
  onSelectThread: vi.fn(),
  onNewThread: vi.fn(),
  onRenameThread: vi.fn(),
  onExportThread: vi.fn(),
  onDeleteThread: vi.fn(),
  onForkThread: vi.fn(),
  onSend: vi.fn(),
  onConfirmAction: vi.fn(),
  onDismissAction: vi.fn(),
};

describe('AgentDesk arena thread toggle', () => {
  it('hides arena threads by default and reveals them when toggled', () => {
    const threads = [thread(1, 'Desk one'), thread(2, 'Arena run', 'arena')];
    render(<AgentDesk {...baseProps} threads={threads} />);

    expect(screen.getByText('Desk one')).toBeInTheDocument();
    expect(screen.queryByText('Arena run')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/show arena threads/i));
    expect(screen.getByText('Arena run')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/AgentDesk.arenaToggle.test.tsx`
Expected: FAIL — "Arena run" is present (no filtering yet) or the checkbox label is missing.

- [ ] **Step 4: Add the toggle state + filter**

In `frontend/src/routes/AgentDesk.tsx`, add state next to the existing `searchQuery` state (line 233):

```tsx
  const [showArena, setShowArena] = useState(false);
```

Change the `filteredThreads` memo (lines 235-245) to also drop arena threads unless `showArena`:

```tsx
  const filteredThreads = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return threads.filter((thread) => {
      if (!showArena && thread.source === 'arena') return false;
      if (!query) return true;
      const haystack = [
        thread.title,
        ...thread.messages.map((message) => message.content),
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [threads, searchQuery, showArena]);
```

Add the checkbox directly after the search `</label>` (line 289), before the thread list `<div>`:

```tsx
      <label className="wl-agent-desk__arena-toggle">
        <input
          type="checkbox"
          checked={showArena}
          onChange={(event) => setShowArena(event.target.checked)}
        />
        Show arena threads
      </label>
```

- [ ] **Step 5: Add the toggle style**

In `frontend/src/routes/AgentDesk.css`, add (token-only, per `frontend/CLAUDE.md`):

```css
.wl-agent-desk__arena-toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  cursor: pointer;
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/routes/AgentDesk.arenaToggle.test.tsx`
Expected: PASS.

- [ ] **Step 7: Type-check + run the existing AgentDesk tests (no regressions)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/routes/AgentDesk`
Expected: PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/AgentDesk.tsx frontend/src/routes/AgentDesk.css frontend/src/routes/AgentDesk.arenaToggle.test.tsx
git commit -m "feat(arena): Agent Desk toggle to show/hide arena threads"
```

---

## Manual verification (post-merge, not a unit test)

These require live credentials and are run by hand after the tasks land
(the unit suite injects fakes and never calls a real LLM):

1. **Live trace flush check** — drive one real turn (`ARENA_LIVE`/`ZENMUX_API_KEY` set) and immediately assert `get_trace_store(get_settings()).list_thread_traces(thread_id)` returns ≥1 root. If empty, the `LocalTracer` has an async write buffer and `transcript_from_trace` needs a settle/flush before reading (spec Failure-handling risk).
2. **End-to-end match** — `queue_arena_run` for `risk-manager-control-day` × `gpt-5-5`, run the task, confirm the match scores (not `status="failed"`) and the arena-tagged thread appears in Agent Desk only when the toggle is on.

## Notes / known limitations (documented, not bugs)

- Background `TaskRun`s queued by `run_batch_pricing` during a match are not auto-executed by a worker in the arena process; workflow steps designed around "queue + monitor" (as the golden flagship is) assert the queued action and `task_id`, not completed background results. Synchronous task execution is out of scope (spec D-OOS1-adjacent).
- Seeded fixture portfolios persist in the main DB as inspectable demo data (no auto-cleanup; spec D-OOS2).
- Live runs require the `zenmux` channel + candidate models present in `config/agent_channels.yaml` and `ZENMUX_API_KEY`; absence makes the match fail cleanly via the existing per-match try/except.
