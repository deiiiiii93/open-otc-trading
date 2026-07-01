# Dynamic Subagents (Pilot Vertical Slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a governed, self-contained pilot of QuickJS dynamic subagents: the orchestrator may fan out `task()` work to a read-only `risk_manager` **only** inside one seeded, allowlisted Case-3 workflow (morning risk-breach commentary); every other `js_eval` is hard-blocked.

**Architecture:** Enable the already-wired `CodeInterpreterMiddleware` behind a config flag, then bolt on four guards: (1) a pre-eval **attribution gate** middleware that rejects every `eval` tool call lacking server-set `fanout_attribution`; (2) server-side stamping of that attribution only for an allowlisted seed workflow; (3) a **fan-out read-only** middleware that blocks write/irreversible tools inside fanned-out subagents (deny-by-default); (4) meta-validation that refuses the `dynamic_subagents` flag from user/model-authored saves. Backend routes the existing `subagent` custom-stream events to the web SSE. Coverage is guaranteed by a deterministic scope tool + chunked fan-out with per-item success/failed records.

**Tech Stack:** Python 3.11, FastAPI, LangGraph, `langchain.agents.middleware` (`AgentMiddleware`, `ToolCallRequest`), `deepagents`, `langchain_quickjs` v0.3.2 (`quickjs_rs` 0.2.4), SQLAlchemy, Alembic, pytest.

## Global Constraints

- Tests run via `.venv/bin/python -m pytest` from repo root.
- Middleware base: `from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest`.
- Read run config in middleware via `from langgraph.config import get_config` → `get_config().get("configurable") or {}` (wrap in try/except → default deny).
- The eval tool the interpreter exposes is named **`"eval"`** (`langchain_quickjs` default `tool_name`).
- Short-circuit a tool call by **returning a `ToolMessage(status="error", tool_call_id=<id>, content=...)` without calling `handler`** (see `tool_error_boundary.py`).
- **Deny-by-default:** any tool not positively classified read-safe is treated as write-capable.
- Attribution values live under `configurable["fanout_attribution"]`; only server code writes them. The model/JS/tool args must never be trusted to set them.
- The pilot workflow slug is **`morning-risk-breach-commentary`**; the server allowlist is `DYNAMIC_SUBAGENTS_ALLOWLIST = frozenset({"morning-risk-breach-commentary"})`, honored only when the persisted row also has `source == "seed"`.
- `max_ptc_calls` is lowered 64 → **24** (per-eval backstop).
- Do NOT flip `OPEN_OTC_AGENT_CODE_INTERPRETER` in the tracked `.env`; enablement is per-run/test config only.

---

### Task 1: Enable interpreter + lower the per-eval cap

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py:166-172`
- Modify: `backend/app/services/deep_agent/capability_gate.py` (add allowlist constant) — or a new `backend/app/services/deep_agent/dynamic_subagents.py`
- Test: `tests/test_dynamic_subagents_orchestrator.py`

**Interfaces:**
- Produces: constant `DYNAMIC_SUBAGENTS_ALLOWLIST: frozenset[str]`; `MAX_PTC_CALLS = 24`; module `dynamic_subagents.py` that later tasks import (`eval_gate` middleware, attribution helpers).

- [ ] **Step 1: Create the shared module with the allowlist + constants**

Create `backend/app/services/deep_agent/dynamic_subagents.py`:
```python
"""Shared constants + helpers for the dynamic-subagents pilot slice."""
from __future__ import annotations

# Server-owned allowlist. A workflow may carry `dynamic_subagents` ONLY if its
# slug is here AND its persisted row has source == "seed" (checked at save time).
DYNAMIC_SUBAGENTS_ALLOWLIST: frozenset[str] = frozenset({"morning-risk-breach-commentary"})

# Per-eval PTC backstop for the QuickJS interpreter (lowered from the lib default 64).
MAX_PTC_CALLS: int = 24

# configurable keys (server-set only).
FANOUT_ATTRIBUTION_KEY = "fanout_attribution"
FANOUT_ATTRIBUTION_CASE3 = "case3_workflow"
FANOUT_WORKFLOW_ID_KEY = "fanout_workflow_slug"


def is_allowlisted(slug: str | None) -> bool:
    return bool(slug) and slug in DYNAMIC_SUBAGENTS_ALLOWLIST
```

- [ ] **Step 2: Write the failing test for the cap**

Create `tests/test_dynamic_subagents_orchestrator.py`:
```python
from app.services.deep_agent.dynamic_subagents import MAX_PTC_CALLS, is_allowlisted


def test_max_ptc_calls_is_24():
    assert MAX_PTC_CALLS == 24


def test_allowlist_only_pilot_slug():
    assert is_allowlisted("morning-risk-breach-commentary")
    assert not is_allowlisted("some-user-workflow")
    assert not is_allowlisted(None)
```

- [ ] **Step 3: Run test to verify it passes (constants) / fails (import)**

Run: `.venv/bin/python -m pytest tests/test_dynamic_subagents_orchestrator.py -v`
Expected: PASS once `dynamic_subagents.py` exists.

- [ ] **Step 4: Fix the interpreter wiring + lower the cap**

`ptc=["task"]` is **INVALID** — `langchain_quickjs` raises `ValueError` from `filter_tools_for_ptc` because `task()` is exposed as a top-level subagent global via `subagents=True` (the default), NOT through `ptc`. The current code is wired-but-broken (never hit because the flag is off). Remove `ptc=["task"]`. Change `orchestrator.py:166-172`:
```python
    from .dynamic_subagents import MAX_PTC_CALLS
    from langchain_quickjs import (  # pyright: ignore[reportMissingImports]
        CodeInterpreterMiddleware,
    )

    middleware.append(
        CodeInterpreterMiddleware(
            max_ptc_calls=MAX_PTC_CALLS,   # per-eval backstop
            timeout=5.0,
        )
    )  # subagents defaults True → exposes the top-level task() global
```

- [ ] **Step 5: Smoke test — an interpreter-enabled orchestrator builds without the PTC error**

Add to `tests/test_dynamic_subagents_orchestrator.py` (regression against `ptc=["task"]`), using the repo's existing fake-chat-model test helper (see other `build_orchestrator`/agent tests for the exact import):
```python
def test_code_interpreter_orchestrator_builds():
    from app.services.deep_agent.orchestrator import build_orchestrator
    model = _fake_chat_model()  # reuse the helper other orchestrator tests use
    agent = build_orchestrator(model=model, tools=[], checkpointer=None,
                               enable_code_interpreter=True)
    assert agent is not None  # old ptc=["task"] wiring raised ValueError here
```
Run: `.venv/bin/python -m pytest tests/test_dynamic_subagents_orchestrator.py::test_code_interpreter_orchestrator_builds -v` → Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/dynamic_subagents.py backend/app/services/deep_agent/orchestrator.py tests/test_dynamic_subagents_orchestrator.py
git commit -m "feat(dynamic-subagents): pilot constants, drop invalid ptc=[task], lower max_ptc_calls to 24"
```

---

### Task 2: Eval attribution gate middleware

Rejects **every** `eval` tool call unless the run carries server-set Case-3 attribution for an allowlisted workflow. Blocks emergent fan-out AND arbitrary non-`task()` QuickJS.

**Files:**
- Create: `backend/app/services/deep_agent/eval_gate.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py` (`_agent_middleware`, append the gate when interpreter enabled)
- Test: `tests/test_eval_gate.py`

**Interfaces:**
- Consumes: `dynamic_subagents.{FANOUT_ATTRIBUTION_KEY, FANOUT_ATTRIBUTION_CASE3, FANOUT_WORKFLOW_ID_KEY, is_allowlisted}`.
- Produces: `class EvalAttributionGateMiddleware(AgentMiddleware)` with `wrap_tool_call` / `awrap_tool_call`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_gate.py`:
```python
import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from app.services.deep_agent.eval_gate import EvalAttributionGateMiddleware
from app.services.deep_agent import dynamic_subagents as ds


def _req(name: str, call_id: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(tool_call={"name": name, "args": {}, "id": call_id},
                           tool=None, state={}, runtime=None)


def _handler_ok(_req):
    return ToolMessage(content="ran", tool_call_id=_req.tool_call["id"], name=_req.tool_call["name"])


def _configurable(monkeypatch, cfg: dict | None):
    import app.services.deep_agent.eval_gate as gate
    monkeypatch.setattr(gate, "_read_configurable", lambda: cfg or {})


def test_eval_blocked_without_attribution(monkeypatch):
    _configurable(monkeypatch, {})
    mw = EvalAttributionGateMiddleware()
    result = mw.wrap_tool_call(_req("eval"), _handler_ok)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not authorized" in result.content.lower()


def test_eval_allowed_with_allowlisted_case3(monkeypatch):
    _configurable(monkeypatch, {
        ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3,
        ds.FANOUT_WORKFLOW_ID_KEY: "morning-risk-breach-commentary",
    })
    mw = EvalAttributionGateMiddleware()
    result = mw.wrap_tool_call(_req("eval"), _handler_ok)
    assert isinstance(result, ToolMessage) and result.content == "ran"


def test_eval_blocked_for_non_allowlisted_workflow(monkeypatch):
    _configurable(monkeypatch, {
        ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3,
        ds.FANOUT_WORKFLOW_ID_KEY: "attacker-workflow",
    })
    mw = EvalAttributionGateMiddleware()
    result = mw.wrap_tool_call(_req("eval"), _handler_ok)
    assert result.status == "error"


def test_non_eval_tool_passes_through(monkeypatch):
    _configurable(monkeypatch, {})
    mw = EvalAttributionGateMiddleware()
    result = mw.wrap_tool_call(_req("run_batch_pricing"), _handler_ok)
    assert result.content == "ran"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: eval_gate`.

- [ ] **Step 3: Implement the gate**

Create `backend/app/services/deep_agent/eval_gate.py`:
```python
"""Pre-eval attribution gate: block every `eval` unless server-authorized."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from . import dynamic_subagents as ds

_EVAL_TOOL_NAME = "eval"
_DENY = ("This run is not authorized to execute `eval`. QuickJS fan-out is "
         "restricted to allowlisted Case-3 workflows.")


def _read_configurable() -> dict[str, Any]:
    try:
        from langgraph.config import get_config
        return get_config().get("configurable") or {}
    except Exception:
        return {}


def _authorized(cfg: dict[str, Any]) -> bool:
    if cfg.get(ds.FANOUT_ATTRIBUTION_KEY) != ds.FANOUT_ATTRIBUTION_CASE3:
        return False
    return ds.is_allowlisted(cfg.get(ds.FANOUT_WORKFLOW_ID_KEY))


def _deny(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(content=_DENY, tool_call_id=request.tool_call["id"],
                       name=request.tool_call["name"], status="error")


class EvalAttributionGateMiddleware(AgentMiddleware):
    """Reject any `eval` tool call lacking server-set Case-3 attribution."""

    def wrap_tool_call(self, request: ToolCallRequest,
                       handler: Callable[[ToolCallRequest], Any]) -> Any:
        if request.tool_call.get("name") == _EVAL_TOOL_NAME and not _authorized(_read_configurable()):
            return _deny(request)
        return handler(request)

    async def awrap_tool_call(self, request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], Awaitable[Any]]) -> Any:
        if request.tool_call.get("name") == _EVAL_TOOL_NAME and not _authorized(_read_configurable()):
            return _deny(request)
        return await handler(request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_eval_gate.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Wire the gate into the orchestrator (outermost, before CodeInterpreterMiddleware)**

In `orchestrator.py` `_agent_middleware`, when `enable_code_interpreter` is true, append the gate BEFORE the `CodeInterpreterMiddleware` append:
```python
    from .eval_gate import EvalAttributionGateMiddleware
    middleware.append(EvalAttributionGateMiddleware())
    # ... then the CodeInterpreterMiddleware append from Task 1 ...
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/eval_gate.py backend/app/services/deep_agent/orchestrator.py tests/test_eval_gate.py
git commit -m "feat(dynamic-subagents): whole-eval attribution gate (deny-by-default)"
```

---

### Task 3: Server-side attribution stamping via the desk-workflow drive seam

Only the server sets `fanout_attribution`, derived from the **DeskWorkflow row** (`slug` + `source`) that the runner already holds. The runtime router's `WorkspaceRouteDecision.workflow_id` is an **integer `Workflow.id`, NOT a slug** — do not use it. The desk-workflow path is: `run_desk_workflow(workflow=wf, drive=drive, ...)` → `step(prompt)` → `drive(thread_id, prompt, mode)` → `agent_service.stream_and_persist(...)`. Today `drive` drops `wf.slug`/`wf.source`, so we thread them through.

**Files:**
- Modify: `backend/app/services/deep_agent/dynamic_subagents.py` (add `fanout_attribution_extra`)
- Modify: `backend/app/main.py` (`_desk_workflow_drive_factory` — capture the workflow, pass slug/source)
- Modify: `backend/app/services/agents.py:2410-2425` (`stream_and_persist` — new kwargs) and BOTH `configurable_extra` sites (routed `~2130-2139` and non-routed `~2636-2646`)
- Test: `tests/test_fanout_attribution_stamping.py`

**Interfaces:**
- Consumes: `dynamic_subagents.{is_allowlisted, FANOUT_*}`; `run_desk_workflow(workflow=DeskWorkflow, ...)` where `workflow.slug` / `workflow.source` are available; `stream_and_persist(*, thread_id, content, requested_character, mode, ...)`.
- Produces: `fanout_attribution_extra(*, slug, source) -> dict`; `stream_and_persist` gains `desk_workflow_slug: str | None = None`, `desk_workflow_source: str | None = None`; `configurable["fanout_attribution"]` + `["fanout_workflow_slug"]` present iff the run is the allowlisted seed workflow.

- [ ] **Step 1: Write the failing test for the pure helper**

Create `tests/test_fanout_attribution_stamping.py`:
```python
from app.services.deep_agent.dynamic_subagents import (
    FANOUT_ATTRIBUTION_CASE3, FANOUT_ATTRIBUTION_KEY, FANOUT_WORKFLOW_ID_KEY,
    fanout_attribution_extra,
)


def test_stamps_for_allowlisted_seed_workflow():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="seed") == {
        FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
        FANOUT_WORKFLOW_ID_KEY: "morning-risk-breach-commentary",
    }


def test_no_stamp_for_user_source_even_if_allowlisted_slug():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="user") == {}


def test_no_stamp_for_non_allowlisted():
    assert fanout_attribution_extra(slug="whatever", source="seed") == {}


def test_no_stamp_for_plain_chat():
    assert fanout_attribution_extra(slug=None, source=None) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fanout_attribution_stamping.py -v`
Expected: FAIL (`ImportError: fanout_attribution_extra`).

- [ ] **Step 3: Add the helper to `dynamic_subagents.py`**

Append to `backend/app/services/deep_agent/dynamic_subagents.py`:
```python
def fanout_attribution_extra(*, slug: str | None, source: str | None) -> dict[str, str]:
    """Server-derived attribution. Stamps ONLY when the run is an allowlisted slug
    persisted with source == 'seed'. Never trusts the model/route runtime id."""
    if source == "seed" and is_allowlisted(slug):
        return {FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
                FANOUT_WORKFLOW_ID_KEY: slug}
    return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fanout_attribution_stamping.py -v`
Expected: PASS.

- [ ] **Step 5: Add the kwargs to `stream_and_persist` and inject in BOTH configurable branches**

In `agents.py`, add to the `stream_and_persist` signature (line ~2410):
```python
        desk_workflow_slug: str | None = None,
        desk_workflow_source: str | None = None,
```
Then in BOTH `configurable_extra` assembly sites — the routed branch (`~2130-2139`) and the non-routed branch (`~2636-2646`) — add immediately before the `graph_run_config(...)` call:
```python
        from .deep_agent.dynamic_subagents import fanout_attribution_extra
        configurable_extra.update(
            fanout_attribution_extra(slug=desk_workflow_slug, source=desk_workflow_source)
        )
```

- [ ] **Step 6: Thread slug/source through the drive factory (write the failing test first)**

Add to `tests/test_fanout_attribution_stamping.py`:
```python
import pytest

@pytest.mark.asyncio
async def test_drive_factory_forwards_slug_and_source():
    from types import SimpleNamespace
    import app.main as main
    captured = {}
    class _FakeService:
        async def stream_and_persist(self, **kw):
            captured.update(kw)
            if False:
                yield ""  # make it an async generator
    wf = SimpleNamespace(slug="morning-risk-breach-commentary", source="seed")
    drive = main._desk_workflow_drive_factory(_FakeService(), "auto", desk_workflow=wf)
    async for _ in drive(1, "hi", "yolo"):
        pass
    assert captured["desk_workflow_slug"] == "morning-risk-breach-commentary"
    assert captured["desk_workflow_source"] == "seed"
```
Run: `.venv/bin/python -m pytest tests/test_fanout_attribution_stamping.py::test_drive_factory_forwards_slug_and_source -v` → Expected: FAIL (factory takes no `desk_workflow`).

- [ ] **Step 7: Implement the drive-factory change**

In `main.py` `_desk_workflow_drive_factory`, add `*, desk_workflow=None` and forward its fields:
```python
def _desk_workflow_drive_factory(agent_service, character: str = "auto", *, desk_workflow=None):
    slug = getattr(desk_workflow, "slug", None)
    source = getattr(desk_workflow, "source", None)
    async def drive(thread_id: int, prompt: str, mode: str):
        # ... existing AgentMessage insert ...
        async for frame in agent_service.stream_and_persist(
            thread_id=thread_id, content=prompt, requested_character=character, mode=mode,
            desk_workflow_slug=slug, desk_workflow_source=source,
        ):
            yield frame
    return drive
```
At the call site that builds `drive` for `run_desk_workflow`, pass `desk_workflow=wf`.

- [ ] **Step 8: Run tests**

Run: `.venv/bin/python -m pytest tests/test_fanout_attribution_stamping.py tests/test_eval_gate.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/deep_agent/dynamic_subagents.py backend/app/main.py backend/app/services/agents.py tests/test_fanout_attribution_stamping.py
git commit -m "feat(dynamic-subagents): stamp attribution via desk-workflow drive seam (slug+source)"
```

---

### Task 4: Meta-validation — reject `dynamic_subagents` from user saves

**Files:**
- Modify: `backend/app/services/desk_workflows_script.py:151-179` (`validate_script`)
- Test: `tests/test_desk_workflows_dynamic_flag.py`

**Interfaces:**
- Consumes: `dynamic_subagents.is_allowlisted`; existing `WorkflowScriptError`, `validate_script(script, *, slug)`.
- Produces: `validate_script` accepts an optional `source` kwarg; permits `dynamic_subagents: true` only for allowlisted + seed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_desk_workflows_dynamic_flag.py`:
```python
import pytest
from app.services.desk_workflows_script import validate_script, WorkflowScriptError

_BODY = "\n\nawait step('hi')\n"  # runner lifts the body into async def __workflow__() (top-level await)

def _script(slug, dyn):
    flag = "\n    'dynamic_subagents': True," if dyn else ""
    return (f"meta = {{\n 'name': '{slug}', 'title': 'T', 'persona': 'risk_manager',"
            f"\n 'mode': 'auto', 'scope': 'shared',{flag}\n}}" + _BODY)

def test_user_save_with_dynamic_flag_rejected():
    with pytest.raises(WorkflowScriptError):
        validate_script(_script("morning-risk-breach-commentary", True),
                        slug="morning-risk-breach-commentary", source="user")

def test_non_allowlisted_seed_with_flag_rejected():
    with pytest.raises(WorkflowScriptError):
        validate_script(_script("other", True), slug="other", source="seed")

def test_allowlisted_seed_with_flag_ok():
    meta = validate_script(_script("morning-risk-breach-commentary", True),
                           slug="morning-risk-breach-commentary", source="seed")
    assert meta.get("dynamic_subagents") is True

def test_no_flag_still_ok_for_user():
    meta = validate_script(_script("u", False), slug="u", source="user")
    assert "dynamic_subagents" not in meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_desk_workflows_dynamic_flag.py -v`
Expected: FAIL (`dynamic_subagents` is an unknown key → current code raises for ALL, or `source` kwarg unknown).

- [ ] **Step 3: Implement — allow the key conditionally**

In `desk_workflows_script.py`: add `dynamic_subagents` to the set of allowed optional keys, add a `source: str | None = None` param to `validate_script`, and after the base validation:
```python
    if meta.get("dynamic_subagents"):
        from .deep_agent.dynamic_subagents import is_allowlisted
        if source != "seed" or not is_allowlisted(slug):
            raise WorkflowScriptError(
                "`dynamic_subagents` is server-owned: only allowlisted seed "
                "workflows may set it.")
```
Keep `dynamic_subagents` out of the *required* set; it stays optional and defaults absent.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_desk_workflows_dynamic_flag.py -v`
Expected: PASS.

- [ ] **Step 5: Thread `source` through the save path**

Find the workflow upsert in `desk_workflows.py` (`upsert_desk_workflow(...)`) that calls `validate_script(...)`; pass `source=<incoming row source>` (user-facing saves are `"user"`; the boot-seed path is `"seed"`). Verify the seed path in `database.py` supplies `source="seed"`.

- [ ] **Step 6: Make seed workflows immutable via the user/API save path (write the failing test first)**

`PUT /api/workflows/{slug}` → `upsert_desk_workflow(..., source="user")` currently updates an existing row WITHOUT changing its `source`, so a user could overwrite the allowlisted seed slug's script while it keeps `source='seed'` — then receive Case-3 attribution for arbitrary eval. Close the hole: reject a `source="user"` upsert that targets an existing `source="seed"` row.

Add to `tests/test_desk_workflows_dynamic_flag.py`:
```python
def test_user_cannot_overwrite_seed_workflow(db_session):  # reuse the repo's session fixture
    from app.services import desk_workflows as dw
    dw.upsert_desk_workflow(db_session, slug="pilot", title="T", persona="risk_manager",
                            description="", scope="shared", default_mode="yolo",
                            script="meta={'name':'pilot','title':'T','persona':'risk_manager',"
                                   "'mode':'yolo','scope':'shared'}\n\nawait step('x')",
                            source="seed")
    with pytest.raises(Exception):  # match the module's conflict error type
        dw.upsert_desk_workflow(db_session, slug="pilot", title="T2", persona="risk_manager",
                                description="", scope="shared", default_mode="yolo",
                                script="meta={'name':'pilot','title':'T2','persona':'risk_manager',"
                                       "'mode':'yolo','scope':'shared'}\n\nawait step('y')",
                                source="user")
```
Then implement the guard in `upsert_desk_workflow`: before writing, if the existing row has `source == "seed"` and the incoming `source == "user"`, raise the module's conflict/validation error (map to HTTP 409 like the archived-conflict pattern). Confirm the test passes and that the boot-seed path (`source="seed"`) can still refresh its own rows.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/desk_workflows_script.py backend/app/services/desk_workflows.py tests/test_desk_workflows_dynamic_flag.py
git commit -m "feat(dynamic-subagents): server-owned dynamic_subagents flag + immutable seed workflows"
```

---

### Task 5: Fan-out read-only enforcement middleware

Inside a fanned-out subagent, block write/irreversible/unclassified tools (deny-by-default). Top-level scope/assemble steps are unaffected.

**Files:**
- Create: `backend/app/services/deep_agent/fanout_readonly.py`
- Modify: `backend/app/services/deep_agent/personas.py:186-201` (append middleware to each persona spec)
- Test: `tests/test_fanout_readonly.py`

**Interfaces:**
- Consumes: `dynamic_subagents.FANOUT_ATTRIBUTION_KEY`; `hitl._RISK_LEVEL_BY_TOOL` (name→`"read"|"write"|"irreversible"`); the subagent marker `configurable["ls_agent_type"] == "subagent"` (set by deepagents `task()`).
- Produces: `class FanoutReadOnlyMiddleware(AgentMiddleware)`.

- [ ] **Step 1: Write the failing test (argument-aware)**

`run_python` is classified `"read"` by name but writes artifacts when `writes_artifacts=True` — so the check MUST inspect args, not just the name. Unlisted tools (e.g. `propose_reply_options`, `propose_term_form`) are denied by default.

Create `tests/test_fanout_readonly.py`:
```python
import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from app.services.deep_agent.fanout_readonly import FanoutReadOnlyMiddleware
from app.services.deep_agent import dynamic_subagents as ds


def _req(name, args=None, call_id="c1"):
    return ToolCallRequest(tool_call={"name": name, "args": args or {}, "id": call_id},
                           tool=None, state={}, runtime=None)

def _ok(r): return ToolMessage(content="ran", tool_call_id=r.tool_call["id"], name=r.tool_call["name"])

def _cfg(monkeypatch, cfg):
    import app.services.deep_agent.fanout_readonly as m
    monkeypatch.setattr(m, "_read_configurable", lambda: cfg)

_FANOUT = {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3, "ls_agent_type": "subagent"}

def test_irreversible_tool_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("book_position"), _ok)
    assert isinstance(r, ToolMessage) and r.status == "error"

def test_write_tool_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("create_report"), _ok).status == "error"

def test_run_python_pure_analysis_allowed(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_python", {"writes_artifacts": False}), _ok)
    assert r.content == "ran"

def test_run_python_writes_artifacts_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_python", {"writes_artifacts": True}), _ok)
    assert r.status == "error"

def test_unlisted_card_tool_denied_by_default(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("propose_reply_options"), _ok).status == "error"

def test_unclassified_tool_denied_by_default(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("brand_new_tool"), _ok).status == "error"

def test_top_level_scope_step_unaffected(monkeypatch):
    # fanout attribution present but NOT a subagent → this is the scope/assemble step
    _cfg(monkeypatch, {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3})
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_batch_pricing"), _ok).content == "ran"

def test_normal_chat_unaffected(monkeypatch):
    _cfg(monkeypatch, {})
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("book_position"), _ok).content == "ran"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fanout_readonly.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement (argument-aware, deny-by-default)**

Create `backend/app/services/deep_agent/fanout_readonly.py`:
```python
"""Deny write/irreversible/unclassified tools inside fanned-out subagents.

Argument-aware: `run_python` is read-like for pure analysis but writes when
`writes_artifacts=True`, so we inspect args, not just the tool name.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from . import dynamic_subagents as ds
from .hitl import _RISK_LEVEL_BY_TOOL

_DENY = ("Fanned-out subagents are read-only: `{name}` may write or is "
         "unclassified, so it is blocked in a dynamic-subagents fan-out.")


def _read_configurable() -> dict[str, Any]:
    try:
        from langgraph.config import get_config
        return get_config().get("configurable") or {}
    except Exception:
        return {}


def _in_fanout_subagent(cfg: dict[str, Any]) -> bool:
    return (cfg.get(ds.FANOUT_ATTRIBUTION_KEY) == ds.FANOUT_ATTRIBUTION_CASE3
            and cfg.get("ls_agent_type") == "subagent")


def _is_fanout_write(name: str, args: dict[str, Any] | None) -> bool:
    # Argument-aware escalation: run_python writes when writes_artifacts=True.
    if name == "run_python":
        return bool((args or {}).get("writes_artifacts"))
    # Deny-by-default: anything not positively classified "read" is a write.
    return _RISK_LEVEL_BY_TOOL.get(name, "write") != "read"


class FanoutReadOnlyMiddleware(AgentMiddleware):
    def _maybe_deny(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        if _in_fanout_subagent(_read_configurable()) and _is_fanout_write(name, args):
            return ToolMessage(content=_DENY.format(name=name),
                               tool_call_id=request.tool_call["id"],
                               name=name, status="error")
        return None

    def wrap_tool_call(self, request, handler):
        return self._maybe_deny(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._maybe_deny(request) or await handler(request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fanout_readonly.py -v`
Expected: PASS (all 8).

- [ ] **Step 5: Wire into every persona spec**

In `personas.py` `all_personas` loop (around line 197, next to `ToolErrorBoundaryMiddleware`), insert the middleware so it runs on persona tool calls:
```python
        from .fanout_readonly import FanoutReadOnlyMiddleware
        middleware.insert(0, FanoutReadOnlyMiddleware())  # after ToolErrorBoundary insert(0)
```
(Order: `ToolErrorBoundaryMiddleware` stays outermost; `FanoutReadOnlyMiddleware` just inside it so a denial still becomes a clean ToolMessage.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/fanout_readonly.py backend/app/services/deep_agent/personas.py tests/test_fanout_readonly.py
git commit -m "feat(dynamic-subagents): read-only enforcement inside fanned-out subagents (deny-by-default)"
```

---

### Task 6: Seed the pilot workflow (real INSERT…SELECT mechanism)

The seed module today exposes only `FLAGSHIP_*` string constants, and `database.py:279-326` boot-seeds the flagship via a single idempotent `INSERT … SELECT … WHERE NOT EXISTS` (+ `UPDATE` when the script changed). Refactor to a `SEED_WORKFLOWS` list looped by both the boot-seed and a new migration, then add the pilot.

**Files:**
- Modify: `backend/app/desk_workflow_seed.py` (add `MORNING_COMMENTARY_*` constants; add `SEED_WORKFLOWS: list[dict]`)
- Modify: `backend/app/database.py:279-326` (loop `SEED_WORKFLOWS` instead of the single flagship block)
- Create: `backend/alembic/versions/0040_seed_morning_risk_breach.py` (Core INSERT…SELECT, inlined constants, `down_revision='0039_memory_extraction_runs'` — the head; migrations use Core SQL, never ORM/services)
- Test: `tests/test_pilot_workflow_seed.py`

**Interfaces:**
- Consumes: `validate_script(script, *, slug, source)` from Task 4; existing `FLAGSHIP_*` constants.
- Produces: `SEED_WORKFLOWS = [ {slug,title,persona,description,scope,default_mode,script}, ... ]`; seeded row slug `morning-risk-breach-commentary`, `source='seed'`, meta `dynamic_subagents: True`.

- [ ] **Step 1: Write the failing test (constants + validation)**

Create `tests/test_pilot_workflow_seed.py`:
```python
from app.desk_workflow_seed import SEED_WORKFLOWS
from app.services.desk_workflows_script import validate_script


def test_pilot_workflow_present_and_validates_as_seed():
    wf = next(w for w in SEED_WORKFLOWS if w["slug"] == "morning-risk-breach-commentary")
    meta = validate_script(wf["script"], slug=wf["slug"], source="seed")
    assert meta["persona"] == "risk_manager"
    assert meta["dynamic_subagents"] is True


def test_flagship_still_in_seed_list():
    assert any(w["slug"] == "risk-manager-control-day" for w in SEED_WORKFLOWS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pilot_workflow_seed.py -v`
Expected: FAIL (`ImportError: SEED_WORKFLOWS`).

- [ ] **Step 3: Add constants + the `SEED_WORKFLOWS` list**

In `backend/app/desk_workflow_seed.py`, add the pilot constants and a list that also includes the existing flagship (built from the existing `FLAGSHIP_*` constants):
```python
MORNING_COMMENTARY_SLUG = "morning-risk-breach-commentary"
MORNING_COMMENTARY_TITLE = "Morning Risk-Breach Commentary"
MORNING_COMMENTARY_PERSONA = "risk_manager"
MORNING_COMMENTARY_SCOPE = "shared"
MORNING_COMMENTARY_MODE = "yolo"
MORNING_COMMENTARY_DESCRIPTION = (
    "Scope today's limit breaches, fan out a read-only risk_manager to "
    "investigate each, and assemble a morning report."
)
MORNING_COMMENTARY_SCRIPT = '''meta = {
    "name": "morning-risk-breach-commentary",
    "title": "Morning Risk-Breach Commentary",
    "persona": "risk_manager",
    "mode": "yolo",
    "scope": "shared",
    "dynamic_subagents": True,
    "description": "Scope breaches, fan out a read-only risk_manager per breach, assemble a report.",
    "params": [{"name": "portfolio_id", "label": "Portfolio", "type": "portfolio"}],
}

# TOP-LEVEL await — the runner (`_wrap_async`) lifts this whole body into a generated
# `async def __workflow__()`. Do NOT define your own async def here (it would be a no-op).
# 1. SCOPE (deterministic tool) — price the book so breaches are computable server-side.
await step("Run batch risk pricing for portfolio " + args.portfolio_id +
           " and list EVERY position that breaches a risk limit today.")
# 2. FAN-OUT (judgment) — one read-only risk_manager subagent per breach via the code
#    interpreter (batches <=10). Each returns {position_id, severity, commentary};
#    on error return {position_id, status: 'failed'}. No booking/write tools.
await step("For EVERY breached position, use the code interpreter to fan out one "
           "read-only risk_manager subagent per breach and collect "
           "{position_id, severity, commentary} for each.")
# 3. ASSEMBLE (deterministic reconciliation) — assemble_breach_report re-derives the
#    AUTHORITATIVE breach list server-side from the portfolio (NOT from model text) and
#    reconciles the collected records; uncovered ids are marked failed, not dropped.
await step("Call assemble_breach_report with portfolio_id=" + args.portfolio_id +
           " and the collected records to produce the morning report; surface any "
           "position marked 'failed' in a 'needs manual review' section.")
'''

SEED_WORKFLOWS: list[dict] = [
    {"slug": FLAGSHIP_SLUG, "title": FLAGSHIP_TITLE, "persona": FLAGSHIP_PERSONA,
     "description": FLAGSHIP_DESCRIPTION, "scope": FLAGSHIP_SCOPE,
     "default_mode": FLAGSHIP_MODE, "script": FLAGSHIP_SCRIPT},
    {"slug": MORNING_COMMENTARY_SLUG, "title": MORNING_COMMENTARY_TITLE,
     "persona": MORNING_COMMENTARY_PERSONA, "description": MORNING_COMMENTARY_DESCRIPTION,
     "scope": MORNING_COMMENTARY_SCOPE, "default_mode": MORNING_COMMENTARY_MODE,
     "script": MORNING_COMMENTARY_SCRIPT},
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pilot_workflow_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor `database.py` boot-seed to loop `SEED_WORKFLOWS`**

Replace the single-flagship INSERT/UPDATE block (lines ~283-326) with a loop that runs the same idempotent `INSERT … SELECT … WHERE NOT EXISTS` + `UPDATE … WHERE slug=:slug AND source='seed' AND script != :script` for each entry, always with `source='seed'`:
```python
    if "desk_workflows" in tables:
        from .desk_workflow_seed import SEED_WORKFLOWS
        with active_engine.begin() as connection:
            for wf in SEED_WORKFLOWS:
                connection.execute(text(
                    "INSERT INTO desk_workflows (slug, title, persona, description, scope, "
                    "default_mode, script, source, created_at, updated_at) "
                    "SELECT :slug, :title, :persona, :description, :scope, :default_mode, "
                    ":script, 'seed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                    "WHERE NOT EXISTS (SELECT 1 FROM desk_workflows WHERE slug = :slug)"), wf)
                connection.execute(text(
                    "UPDATE desk_workflows SET title=:title, persona=:persona, "
                    "description=:description, scope=:scope, default_mode=:default_mode, "
                    "script=:script, updated_at=CURRENT_TIMESTAMP "
                    "WHERE slug=:slug AND source='seed' AND script != :script"), wf)
```

- [ ] **Step 6: Write a DB-level boot-seed test**

Add to `tests/test_pilot_workflow_seed.py`:
```python
def test_boot_seed_creates_pilot_row(tmp_path, monkeypatch):
    import sqlalchemy as sa
    from app import database
    engine = sa.create_engine(f"sqlite:///{tmp_path/'t.db'}")
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "active_engine", engine, raising=False)
    database.seed_desk_workflows()  # extract the boot-seed block into this callable in Step 5
    with engine.connect() as c:
        row = c.execute(sa.text(
            "SELECT source FROM desk_workflows WHERE slug='morning-risk-breach-commentary'"
        )).fetchone()
    assert row is not None and row[0] == "seed"
```
(In Step 5, wrap the loop in a `def seed_desk_workflows():` so it is callable from the test; call it from the existing boot path.)

- [ ] **Step 6b: Integration test — the seeded script is NOT a no-op**

The runner lifts top-level `await step(...)` into `__workflow__`; a nested `async def` would silently do nothing. Prove three steps actually run. Add to `tests/test_pilot_workflow_seed.py` (`import pytest` at top):
```python
@pytest.mark.asyncio
async def test_seeded_pilot_emits_three_steps():
    from types import SimpleNamespace
    from app.services.desk_workflow_runner import run_desk_workflow
    from app.desk_workflow_seed import SEED_WORKFLOWS

    entry = next(w for w in SEED_WORKFLOWS if w["slug"] == "morning-risk-breach-commentary")
    wf = SimpleNamespace(slug=entry["slug"], script=entry["script"], persona=entry["persona"],
                         default_mode=entry["default_mode"], source="seed")

    async def fake_drive(thread_id, prompt, mode):
        if False:
            yield ""  # async generator that drives no frames

    starts = 0
    async for frame in run_desk_workflow(thread_id=1, workflow=wf, mode="yolo",
                                         drive=fake_drive, settle=lambda: None,
                                         args={"portfolio_id": "1"}):
        if "workflow.step.start" in frame:
            starts += 1
    assert starts == 3
```
Run: `.venv/bin/python -m pytest tests/test_pilot_workflow_seed.py::test_seeded_pilot_emits_three_steps -v` → Expected: PASS (fails loudly if the script is a no-op).

- [ ] **Step 7: Add migration 0040 (Core SQL, inlined) at the current head**

Create `backend/alembic/versions/0040_seed_morning_risk_breach.py` mirroring `0035`'s inlined-constant + `op.execute(sa.text("INSERT … SELECT … WHERE NOT EXISTS …"))` pattern, with `revision = "0040_seed_morning_risk_breach"` and `down_revision = "0039_memory_extraction_runs"` (the current head — `0036` is already `0036_agent_thread_goal_run`). Do NOT import ORM models/services — inline the constants. Verify a single head after: `.venv/bin/python -m alembic heads` shows exactly one.

- [ ] **Step 8: Run tests**

Run: `.venv/bin/python -m pytest tests/test_pilot_workflow_seed.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/desk_workflow_seed.py backend/app/database.py backend/alembic/versions/0040_seed_morning_risk_breach.py tests/test_pilot_workflow_seed.py
git commit -m "feat(dynamic-subagents): seed morning-risk-breach-commentary via SEED_WORKFLOWS loop + migration 0040"
```

---

### Task 7: Route `subagent` custom events to the web SSE

**Files:**
- Modify: `backend/app/services/agents.py` (`_handle_v3_event`, and `_handle_v2_event` if the configured stream version is v2)
- Modify: `backend/app/services/deep_agent/stream_collector.py` (store subagent events)
- Test: `tests/test_subagent_sse.py`

**Interfaces:**
- Consumes: `langchain_quickjs._subagent` event shape — `{"type": "subagent", "phase": "start"|"complete"|"error", "id", "eval_id"?, "subagent_type"?, "label"?, "description"?, "duration_ms"?, "error"?}` emitted on the LangGraph `custom` stream.
- Produces: SSE lines `event: subagent\ndata: {...}` and `StreamCollector.subagent_events: list[dict]`.

- [ ] **Step 1: Discover the exact custom-event envelope**

The `custom` stream surfaces in `astream_events` as an event dict. Run a focused probe to capture its exact top-level shape (v3 `method`, key holding the payload):
```bash
.venv/bin/python - <<'PY'
# minimal: assert the _subagent event TypedDicts and how custom data is wrapped
from langchain_quickjs._subagent import SUBAGENT_STREAM_EVENT_TYPE
print("event type discriminator:", SUBAGENT_STREAM_EVENT_TYPE)
PY
```
Then grep `_handle_v3_event` for how it reads `ev["method"]` / `ev.get("data")` and confirm where a `method == "custom"` (or `on_custom`) event lands. Pin the exact accessor before writing the handler.

- [ ] **Step 2: Write the failing test**

Create `tests/test_subagent_sse.py`:
```python
import json
from app.services.deep_agent.stream_collector import StreamCollector
from app.services.agents import _subagent_sse_line  # helper added in Step 3


def test_subagent_event_becomes_sse_line():
    collector = StreamCollector()
    event = {"type": "subagent", "phase": "start", "id": "ptc_task_ab12",
             "eval_id": "call_9", "subagent_type": "risk_manager",
             "label": "breach 42", "description": "investigate"}
    line = _subagent_sse_line(event, collector)
    assert line.startswith("event: subagent\n")
    payload = json.loads(line.split("data: ", 1)[1])
    assert payload["phase"] == "start" and payload["subagent_type"] == "risk_manager"
    assert collector.subagent_events[-1]["id"] == "ptc_task_ab12"
```

- [ ] **Step 3: Implement the helper + collector field**

In `stream_collector.py`, add `self.subagent_events: list[dict] = []` in `__init__` and:
```python
    def on_subagent(self, event: dict) -> None:
        self.subagent_events.append(event)
```
In `agents.py`, add near `_sse` (line ~80):
```python
def _subagent_sse_line(event: dict, collector) -> str:
    collector.on_subagent(event)
    return _sse("subagent", event)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_subagent_sse.py -v`
Expected: PASS.

- [ ] **Step 5: Dispatch custom events in the stream handler**

In `_handle_v3_event` (and `_handle_v2_event` per configured version), detect the custom-stream payload discovered in Step 1 where `payload.get("type") == "subagent"` and return `_subagent_sse_line(payload, collector)`. Place the check before the generic fallthrough so it isn't swallowed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agents.py backend/app/services/deep_agent/stream_collector.py tests/test_subagent_sse.py
git commit -m "feat(dynamic-subagents): route subagent custom-stream events to web SSE"
```

---

### Task 8: Deterministic coverage reconciliation + acceptance

The coverage/overflow/failure guarantee must be **real code, not a prompt**. A pure
function reconciles the deterministic scope-list against the fan-out's returned records:
every scoped id gets **exactly one** terminal record, any uncovered id becomes `failed`
(so a `max_ptc_calls`-truncated or errored run can never silently drop a breach). It is
exposed as a deterministic `assemble_breach_report` tool the workflow's assemble step calls.

**Files:**
- Modify: `backend/app/services/deep_agent/dynamic_subagents.py` (add `reconcile_fanout_coverage`)
- Create/reuse: deterministic breach enumeration `enumerate_limit_breaches(session, portfolio_id) -> list[str]`
- Create: `backend/app/tools/assemble_breach_report.py` (tool keyed on `portfolio_id`, re-derives scope server-side) + register in `all_agent_tools()`
- Test: `tests/test_fanout_reconcile.py`

**Interfaces:**
- Consumes: `MAX_PTC_CALLS`; a deterministic server-side breach source.
- Produces: `reconcile_fanout_coverage(scoped_ids: list[str], records: list[dict]) -> {"records","failed_ids","covered","total"}`; `enumerate_limit_breaches(session, portfolio_id) -> list[str]`; tool `assemble_breach_report(portfolio_id, records)` that re-derives scope server-side (never trusts model-passed ids).

- [ ] **Step 1: Write the failing test (deterministic — the real guarantee)**

Create `tests/test_fanout_reconcile.py`:
```python
from app.services.deep_agent.dynamic_subagents import (
    reconcile_fanout_coverage, MAX_PTC_CALLS,
)


def _rec(pid, **kw): return {"position_id": pid, **kw}


def test_every_scoped_id_gets_exactly_one_record():
    out = reconcile_fanout_coverage(["p1", "p2"],
                                    [_rec("p1", severity="high", commentary="x"),
                                     _rec("p2", severity="low", commentary="y")])
    assert {r["position_id"] for r in out["records"]} == {"p1", "p2"}
    assert len(out["records"]) == 2 and out["covered"] == 2 and out["total"] == 2
    assert out["failed_ids"] == []


def test_missing_id_becomes_failed_not_dropped():
    out = reconcile_fanout_coverage(["p1", "p2", "p3"], [_rec("p1", severity="high", commentary="x")])
    by_id = {r["position_id"]: r for r in out["records"]}
    assert set(by_id) == {"p1", "p2", "p3"}          # coverage preserved
    assert by_id["p2"]["status"] == "failed" and by_id["p3"]["status"] == "failed"
    assert set(out["failed_ids"]) == {"p2", "p3"}


def test_explicit_failed_record_is_kept():
    out = reconcile_fanout_coverage(["p1"], [_rec("p1", status="failed")])
    assert out["records"][0]["status"] == "failed" and out["failed_ids"] == ["p1"]


def test_overflow_all_covered_even_beyond_ptc_budget():
    scoped = [f"p{i}" for i in range(MAX_PTC_CALLS + 40)]      # 64 > 24
    records = [_rec(f"p{i}", severity="low", commentary="c") for i in range(MAX_PTC_CALLS)]
    out = reconcile_fanout_coverage(scoped, records)
    assert len(out["records"]) == len(scoped)                 # ALL covered
    assert out["covered"] == MAX_PTC_CALLS
    assert len(out["failed_ids"]) == 40                       # truncated tail -> failed


def test_duplicate_records_collapse_to_one():
    out = reconcile_fanout_coverage(["p1"], [_rec("p1", commentary="a"), _rec("p1", commentary="b")])
    assert len([r for r in out["records"] if r["position_id"] == "p1"]) == 1


def test_records_for_unscoped_ids_ignored():
    out = reconcile_fanout_coverage(["p1"], [_rec("p1", commentary="a"), _rec("ghost", commentary="z")])
    assert {r["position_id"] for r in out["records"]} == {"p1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fanout_reconcile.py -v`
Expected: FAIL (`ImportError: reconcile_fanout_coverage`).

- [ ] **Step 3: Implement the pure reconciler**

Append to `backend/app/services/deep_agent/dynamic_subagents.py`:
```python
def reconcile_fanout_coverage(scoped_ids, records):
    """Guarantee exactly one terminal record per scoped id.

    - first record per id wins (duplicates collapse);
    - records for ids not in scope are ignored;
    - any scoped id with no record becomes {"position_id": id, "status": "failed"}.
    Coverage is thus independent of how many dispatches actually returned.
    """
    seen: dict[str, dict] = {}
    scoped = list(dict.fromkeys(scoped_ids))  # de-dupe, preserve order
    scoped_set = set(scoped)
    for rec in records:
        pid = rec.get("position_id")
        if pid in scoped_set and pid not in seen:
            seen[pid] = rec
    out_records, failed = [], []
    for pid in scoped:
        rec = seen.get(pid) or {"position_id": pid, "status": "failed"}
        if rec.get("status") == "failed":
            failed.append(pid)
        out_records.append(rec)
    return {"records": out_records, "failed_ids": failed,
            "covered": len(seen), "total": len(scoped)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fanout_reconcile.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Establish a deterministic server-side breach source**

Scope must NOT come from model text (the runner passes only text between steps). Discover how breaches are computed today: `grep -rn "breach\|limit\|set_portfolio_rule\|set_hedge_bands" backend/app/services | head`. If a deterministic breach query exists, reuse it; otherwise add `enumerate_limit_breaches(session, portfolio_id) -> list[str]` (returns breached position ids by comparing the latest `RiskRun` exposures to the portfolio's rules). Unit-test it against a seeded portfolio with 2 breaching / 1 clean position → returns exactly the 2 breached ids.

- [ ] **Step 6: Wrap it in a deterministic `assemble_breach_report` tool (scope re-derived server-side)**

Create `backend/app/tools/assemble_breach_report.py` — a `@capability_gated(group=ToolGroup.DOMAIN_READ)` StructuredTool taking `{portfolio_id: str, records: list[dict]}` that (1) re-derives `scoped_ids = enumerate_limit_breaches(session, portfolio_id)` **server-side, ignoring any model-passed id list**, then (2) returns `reconcile_fanout_coverage(scoped_ids, records)` as JSON. Register in `all_agent_tools()`. Because scope is server-authoritative, a model that omits breaches cannot shrink coverage — omitted ids surface as `failed`.

- [ ] **Step 7: End-to-end coverage test — model omissions cannot drop breaches**

Add to `tests/test_fanout_reconcile.py` a test that invokes the tool with a stubbed `enumerate_limit_breaches` returning `MAX_PTC_CALLS + 40` ids while `records` covers only `MAX_PTC_CALLS` of them; assert the tool's output covers ALL scoped ids and marks the 40 uncovered as `failed` (regardless of what ids the caller passed):
```python
def test_tool_uses_server_scope_not_model_records(monkeypatch):
    import app.tools.assemble_breach_report as t
    from app.services.deep_agent.dynamic_subagents import MAX_PTC_CALLS
    scoped = [f"p{i}" for i in range(MAX_PTC_CALLS + 40)]
    monkeypatch.setattr(t, "enumerate_limit_breaches", lambda *a, **k: scoped)
    records = [{"position_id": f"p{i}", "severity": "low", "commentary": "c"} for i in range(MAX_PTC_CALLS)]
    out = t._assemble("ptf-1", records)  # pure inner fn under the StructuredTool
    assert len(out["records"]) == len(scoped) and len(out["failed_ids"]) == 40
```

- [ ] **Step 8: Run the whole suite; fix any tool-catalog/count guards**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If a tool-catalog exact-set/count test breaks from the new tool, or a workflow-catalog test breaks from the new seed workflow (known coupling across ~6 files), update those fixtures.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/deep_agent/dynamic_subagents.py backend/app/services/risk_limits.py backend/app/tools/assemble_breach_report.py backend/app/tools/__init__.py tests/test_fanout_reconcile.py
git commit -m "feat(dynamic-subagents): server-authoritative scope reconciliation + assemble_breach_report tool"
```

---

## Self-Review

**Spec coverage:**
- §2a in-scope items → Tasks: enable+cap (1), whole-eval gate (2), attribution stamping (3), server-owned flag (4), read-only exclusion (5), seeded pilot workflow (6), SSE plumbing (7), acceptance tests (8). ✓
- §5a whole-eval gate + immutable server attribution + allowlist + no self-labeling → Tasks 2, 3, 4 (+ tests a–d distributed across them). ✓
- §6 capability read-only (deny-by-default, **argument-aware** for `run_python.writes_artifacts`), per-eval overflow, per-item terminal state → Task 5 (argument-aware read-only), Task 8 (`reconcile_fanout_coverage`: every scoped id → exactly one terminal record, uncovered→`failed`, overflow-safe regardless of `max_ptc_calls` truncation). ✓
- §10 vertical-slice acceptance set → Tasks 2/3/4 (gate + attribution + allowlist), 5 (arg-aware exclusion), 6 (seeded workflow + DB seed test), 7 (observability data path), 8 (deterministic coverage/overflow/failure). ✓
- Deferred surfaces (frontend panel, IM carriage, Case-1/2, auto-mode) → NOT tasked (correctly out of scope; the whole-eval gate hard-blocks them at runtime). ✓

**Placeholder scan:** Task 7 includes one bounded **discovery step** (the exact custom-event envelope shape emitted on the LangGraph `custom` stream) before its handler, because that shape comes from third-party (`langchain_quickjs`) code and must be pinned against the real API rather than guessed — a concrete probe, not an open-ended TODO. All other steps carry real code.

**Type consistency:** `FANOUT_ATTRIBUTION_KEY` / `FANOUT_ATTRIBUTION_CASE3` / `FANOUT_WORKFLOW_ID_KEY` / `is_allowlisted` / `fanout_attribution_extra(*, slug, source)` / `MAX_PTC_CALLS` / `reconcile_fanout_coverage(scoped_ids, records)` are defined once in `dynamic_subagents.py` (Tasks 1, 3, 8) and consumed by identical names in Tasks 2, 4, 5, 6, 8. `stream_and_persist` gains `desk_workflow_slug` / `desk_workflow_source` (Task 3), set by `_desk_workflow_drive_factory(..., desk_workflow=wf)`. `_read_configurable` is a per-module private (eval_gate, fanout_readonly) monkeypatched in tests. Eval tool name `"eval"` is consistent across Task 2 and the gate wiring.

## Notes for the implementer
- **Attribution seam (Task 3):** the runtime router's `WorkspaceRouteDecision.workflow_id` is an **integer `Workflow.id`, not a DeskWorkflow slug** — never use it for attribution. Stamp from the `DeskWorkflow` row (`slug`+`source`) the runner holds, threaded through `_desk_workflow_drive_factory` → `stream_and_persist` → both `configurable_extra` sites.
- Personas today all receive the same toolset and are differentiated by prompt only. This slice enforces read-only at **runtime** (Task 5 middleware) rather than by rebuilding toolsets, because the orchestrator/personas are built once and the fan-out context is only known per-run via `configurable`.
- **`run_python` is argument-aware** (Task 5): classified `"read"` by name but writes when `writes_artifacts=True` (see `run_python_requires_hitl`). The read-only check inspects args, not just the name. Message/card tools (`propose_reply_options`, `propose_term_form`) are unlisted in `_RISK_LEVEL_BY_TOOL` → denied by default in fan-out.
- **Coverage is deterministic, not prompt-trusted** (Task 8): `reconcile_fanout_coverage` is the guarantee — even if the model's fan-out hits `max_ptc_calls=24` and returns fewer records than scoped breaches, uncovered ids become explicit `failed` records, never silently dropped.
- `ToolGroup` lives in `envelopes.py`; `_RISK_LEVEL_BY_TOOL` (read/write/irreversible) lives in `hitl.py`. The seed mechanism is a `SEED_WORKFLOWS` loop over idempotent `INSERT…SELECT` in `database.py` + migration 0036 (migrations use Core SQL, never ORM).
- Do not add new personas (persona-count/skill-catalog tests are brittle — see the skill-catalog coupling across ~6 files).
