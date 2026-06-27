# Parameterized Desk Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a desk workflow declare typed parameters in `meta['params']`, collect them through a launch form, and inject them as an `args` object the script interpolates with f-strings.

**Architecture:** A validated `args: dict[str, str]` threads from a new launch dialog → `launchWorkflow` → the run endpoint (validates → 422 on bad input) → `run_desk_workflow`, which injects a read-only `args` object into the script's exec namespace. Params are declared in the `meta` dict literal (validated at save time) and surfaced on the summary via a derived ORM property.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Pydantic v2 (backend); React + TypeScript + Vite + vitest (frontend). Spec: `docs/superpowers/specs/2026-06-27-parameterized-desk-workflows-design.md`.

## Global Constraints

- **Tests live at repo-root `tests/`** (NOT `backend/tests`). Run backend tests with `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/<file> -v` (pytest config sets `pythonpath=["backend"]`, `testpaths=["tests"]`).
- **Frontend tests:** `cd frontend && npx vitest run <path>` (or `npx tsc --noEmit` for types — CLI tsc is authoritative over inline LSP).
- **Param `name` must be a valid Python identifier AND not a Python keyword** — `^[a-z][a-z0-9_]*$` plus `keyword.iskeyword(name)` rejection. Reserved names: `{step, log, args}`.
- **`type ∈ {string, date, portfolio}`** exactly.
- **All declared params are required at launch** (no optionals in v1).
- **Token-only frontend styling** — `var(--token)` only, no hardcoded colors; reuse `Modal`/`Button` primitives. See `frontend/CLAUDE.md`.
- **Backward compatibility:** absent `meta['params']` ⇒ zero-param workflow, current behavior unchanged. `run_desk_workflow(args=None)` defaults to `{}`.
- **DRY / YAGNI / TDD / frequent commits.**

---

### Task 1: Param declaration & validation (`validate_params`)

**Files:**
- Modify: `backend/app/services/desk_workflows_script.py`
- Test: `tests/test_desk_workflows_script.py`

**Interfaces:**
- Consumes: existing `extract_meta(script) -> dict`, `WorkflowScriptError`, `validate_script(script, *, slug)`.
- Produces: `validate_params(meta: dict) -> list[dict]` returning normalized `[{"name","label","type"}]` (or `[]` when `params` absent). New module constants `VALID_PARAM_TYPES`, `_PARAM_NAME_RE`, `_RESERVED_PARAM_NAMES`. `validate_script` now also runs `validate_params(meta)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desk_workflows_script.py`:

```python
from app.services.desk_workflows_script import validate_params


def _meta(params):
    return {
        "name": "x", "title": "X", "persona": "trader",
        "mode": "auto", "scope": "local", "params": params,
    }


def test_validate_params_absent_returns_empty():
    assert validate_params({"name": "x"}) == []


def test_validate_params_happy_normalizes():
    out = validate_params(_meta([
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Start date", "type": "date"},
    ]))
    assert out == [
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Start date", "type": "date"},
    ]


@pytest.mark.parametrize("params", [
    "notalist",
    [["not", "a", "dict"]],
    [{"label": "L", "type": "string"}],                    # missing name
    [{"name": "p", "type": "string"}],                     # missing label
    [{"name": "p", "label": "L"}],                         # missing type
    [{"name": "p", "label": "L", "type": "color"}],        # bad type
    [{"name": "Portfolio", "label": "L", "type": "string"}],   # uppercase
    [{"name": "portfolio name", "label": "L", "type": "string"}],  # space
    [{"name": "for", "label": "L", "type": "string"}],     # python keyword
    [{"name": "args", "label": "L", "type": "string"}],    # reserved
    [{"name": "p", "label": "L", "type": "string"},
     {"name": "p", "label": "L2", "type": "date"}],        # duplicate
])
def test_validate_params_rejects(params):
    with pytest.raises(WorkflowScriptError):
        validate_params(_meta(params))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_script.py -k validate_params -v`
Expected: FAIL with `ImportError: cannot import name 'validate_params'`.

- [ ] **Step 3: Implement `validate_params` + wire into `validate_script`**

In `backend/app/services/desk_workflows_script.py`, add `import keyword` at the top (after `import re`), then add constants near the other module constants:

```python
VALID_PARAM_TYPES = {"string", "date", "portfolio"}
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_PARAM_NAMES = {"step", "log", "args"}
```

Add the function (place it after `extract_slug`):

```python
def validate_params(meta: dict) -> list[dict]:
    """Validate meta['params'] and return the normalized [{name,label,type}] list.

    Absent params -> []. Raises WorkflowScriptError on any malformed entry.
    """
    raw = meta.get("params")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkflowScriptError("meta['params'] must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise WorkflowScriptError("each param must be a dict")
        for key in ("name", "label", "type"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise WorkflowScriptError(f"param missing string {key!r}")
        name, label, ptype = entry["name"], entry["label"], entry["type"]
        if not _PARAM_NAME_RE.match(name) or keyword.iskeyword(name):
            raise WorkflowScriptError(
                f"param name {name!r} must be a valid Python identifier "
                f"(lowercase, no spaces, not a keyword)"
            )
        if name in _RESERVED_PARAM_NAMES:
            raise WorkflowScriptError(f"param name {name!r} is reserved")
        if name in seen:
            raise WorkflowScriptError(f"duplicate param name {name!r}")
        if ptype not in VALID_PARAM_TYPES:
            raise WorkflowScriptError(
                f"param {name!r} has invalid type {ptype!r}"
            )
        seen.add(name)
        out.append({"name": name, "label": label, "type": ptype})
    return out
```

Then in `validate_script`, add a call just before `return meta`:

```python
    validate_params(meta)
    return meta
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_script.py -v`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/desk_workflows_script.py tests/test_desk_workflows_script.py
git commit -m "feat(workflows): validate typed meta['params'] declarations"
```

---

### Task 2: Validate launch args (`validate_workflow_args`)

**Files:**
- Modify: `backend/app/services/desk_workflows_script.py`
- Test: `tests/test_desk_workflows_script.py`

**Interfaces:**
- Consumes: `validate_params(meta)`, `WorkflowScriptError`.
- Produces: `validate_workflow_args(meta: dict, args) -> dict[str, str]` — returns a clean `{name: stripped_value}` containing exactly the declared names; raises `WorkflowScriptError` on any violation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desk_workflows_script.py`:

```python
from app.services.desk_workflows_script import validate_workflow_args

_PARAMS = [
    {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
    {"name": "start", "label": "Start", "type": "date"},
]
_PMETA = {"name": "x", "params": _PARAMS}


def test_validate_args_happy_strips():
    out = validate_workflow_args(_PMETA, {"portfolio": " Default ", "start": "2026-06-25"})
    assert out == {"portfolio": "Default", "start": "2026-06-25"}


def test_validate_args_no_params_ignores_input():
    assert validate_workflow_args({"name": "x"}, {}) == {}


@pytest.mark.parametrize("args", ["foo", [1, 2], 7])
def test_validate_args_rejects_non_dict(args):
    with pytest.raises(WorkflowScriptError):
        validate_workflow_args(_PMETA, args)


@pytest.mark.parametrize("args", [
    {"portfolio": "Default"},                              # missing start
    {"portfolio": "  ", "start": "2026-06-25"},            # blank value
    {"portfolio": "Default", "start": "2026-13-01"},       # bad month
    {"portfolio": "Default", "start": "06/25/2026"},       # wrong format
    {"portfolio": "Default", "start": "2026-06-25", "x": "y"},  # unknown key
])
def test_validate_args_rejects(args):
    with pytest.raises(WorkflowScriptError):
        validate_workflow_args(_PMETA, args)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_script.py -k validate_args -v`
Expected: FAIL with `ImportError: cannot import name 'validate_workflow_args'`.

- [ ] **Step 3: Implement `validate_workflow_args`**

Add `import datetime` at the top of `backend/app/services/desk_workflows_script.py` (after `import ast`). Add the function after `validate_params`:

```python
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_workflow_args(meta: dict, args) -> dict[str, str]:
    """Validate launch-time args against meta['params']; return a clean dict.

    Every declared param is required and must be a non-empty string; date-typed
    params must be ISO YYYY-MM-DD. Unknown keys and non-dict args are rejected.
    """
    if not isinstance(args, dict):
        raise WorkflowScriptError("args must be an object")
    params = validate_params(meta)
    declared = {p["name"]: p for p in params}
    unknown = set(args) - set(declared)
    if unknown:
        raise WorkflowScriptError(f"unknown parameter {sorted(unknown)[0]!r}")
    clean: dict[str, str] = {}
    for name, spec in declared.items():
        raw = args.get(name)
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            raise WorkflowScriptError(f"missing required parameter {name!r}")
        if spec["type"] == "date":
            if not _DATE_RE.match(value):
                raise WorkflowScriptError(
                    f"parameter {name!r} must be an ISO date (YYYY-MM-DD)"
                )
            try:
                datetime.date.fromisoformat(value)
            except ValueError as exc:
                raise WorkflowScriptError(
                    f"parameter {name!r} must be an ISO date (YYYY-MM-DD)"
                ) from exc
        clean[name] = value
    return clean
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_script.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/desk_workflows_script.py tests/test_desk_workflows_script.py
git commit -m "feat(workflows): validate launch args against declared params"
```

---

### Task 3: Inject `args` into the runner namespace

**Files:**
- Modify: `backend/app/services/desk_workflow_runner.py`
- Test: `tests/test_desk_workflow_runner.py`

**Interfaces:**
- Consumes: `WorkflowScriptError` (from `desk_workflows_script`), existing `run_desk_workflow(*, thread_id, workflow, mode, drive, settle)`.
- Produces: `run_desk_workflow(..., args: dict[str, str] | None = None)`. New `_Args` class injected as `ns["args"]`, supporting `args.portfolio` and `args["portfolio"]`; undeclared access raises `WorkflowScriptError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desk_workflow_runner.py`:

```python
@pytest.mark.asyncio
async def test_runner_substitutes_args_into_prompt():
    seen = []

    async def drive(thread_id, prompt, mode):
        seen.append(prompt)
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo",'
        '"scope":"local","params":[{"name":"portfolio","label":"P","type":"portfolio"}]}\n'
        'await step(f"latest risk for {args.portfolio}?")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo",
        drive=drive, settle=lambda: None, args={"portfolio": "Default"},
    )]
    assert seen == ["latest risk for Default?"]
    # the substituted prompt is also echoed in the step.start frame
    assert any('"prompt": "latest risk for Default?"' in f for f in frames)


@pytest.mark.asyncio
async def test_runner_undeclared_arg_errors():
    async def drive(thread_id, prompt, mode):
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo","scope":"local"}\n'
        'await step(f"{args.nope}")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo",
        drive=drive, settle=lambda: None, args={},
    )]
    names = [e[0] for e in _parse(frames)]
    assert "workflow.step.error" in names
    assert "workflow.complete" not in names


def test_args_is_read_only():
    from app.services.desk_workflow_runner import _Args

    a = _Args({"portfolio": "Default"})
    assert a.portfolio == "Default" and a["portfolio"] == "Default"
    with pytest.raises(Exception):
        a.portfolio = "Other"          # __setattr__ raises
    with pytest.raises(Exception):
        del a._values                  # __delattr__ raises
    with pytest.raises(Exception):
        a._values["portfolio"] = "X"   # MappingProxyType is immutable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_runner.py -k "args" -v`
Expected: FAIL — `run_desk_workflow() got an unexpected keyword argument 'args'`.

- [ ] **Step 3: Implement `_Args` + the `args` parameter**

In `backend/app/services/desk_workflow_runner.py`, add `from types import MappingProxyType` near the top imports, and change the desk-workflows-script import line to also bring in `WorkflowScriptError`:

```python
from .desk_workflows_script import WorkflowScriptError, guard_script
```

Add the `_Args` class after `_SAFE_BUILTINS`. It must be **genuinely read-only**: the
backing map is a `MappingProxyType` (so a script that reaches `args._values` — single
underscore, which the dunder-only AST guard does not block — still cannot mutate it),
and `__setattr__` is overridden so `args.x = ...` raises instead of silently succeeding.
Missing-key access raises `WorkflowScriptError` (the endpoint already guarantees the
supplied keys equal the declared params, so a missing key means the script referenced an
undeclared parameter):

```python
class _Args:
    """Read-only view of validated launch params: supports args.x and args["x"]."""

    def __init__(self, values: dict) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __getattr__(self, name: str) -> str:
        try:
            return object.__getattribute__(self, "_values")[name]
        except KeyError:
            raise WorkflowScriptError(
                f"workflow referenced undeclared parameter {name!r}"
            ) from None

    def __setattr__(self, name: str, value: object) -> None:
        raise WorkflowScriptError("workflow args are read-only")

    def __delattr__(self, name: str) -> None:
        raise WorkflowScriptError("workflow args are read-only")

    def __getitem__(self, name: str) -> str:
        return self.__getattr__(name)
```

> Scope note: per spec §5.4/§9 the runtime is a footgun-reducer, not a security boundary
> (single-user MVP). `_Args` enforces read-only access and missing-key errors; enforcing
> that the *supplied* keys match the *declared* params is the endpoint validator's job
> (`validate_workflow_args`, Task 4), run before the stream starts so it can return a
> clean 422.

Change the `run_desk_workflow` signature to add `args`:

```python
async def run_desk_workflow(
    *,
    thread_id: int,
    workflow: DeskWorkflow,
    mode: str,
    drive: Drive,
    settle: Settle,
    args: dict | None = None,
) -> AsyncIterator[str]:
```

In `_execute`, change the namespace line to include `args`:

```python
        ns: dict = {
            "__builtins__": dict(_SAFE_BUILTINS),
            "step": step, "log": log, "args": _Args(args or {}),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_runner.py -v`
Expected: PASS (including the 4 pre-existing tests — `args` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/desk_workflow_runner.py tests/test_desk_workflow_runner.py
git commit -m "feat(workflows): inject read-only args object into runner namespace"
```

---

### Task 4: Run endpoint validates + forwards args

**Files:**
- Modify: `backend/app/main.py:951-979` (the `run_thread_workflow` endpoint)
- Test: `tests/test_desk_workflow_run_endpoint.py`

**Interfaces:**
- Consumes: `validate_workflow_args`, `extract_meta`, `WorkflowScriptError` (from `desk_workflows_script`); `run_desk_workflow(..., args=...)`.
- Produces: endpoint accepts `{"mode": ..., "args": {...}}`; returns 422 on invalid args, 200 + SSE stream on valid.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_desk_workflow_run_endpoint.py` (this file's flagship now declares params after Task 6 — but write these tests against a small param workflow created inline so they pass independently of seed ordering):

```python
def _create_param_wf(client) -> str:
    script = (
        'meta = {"name":"need-portfolio","title":"NP","persona":"risk_manager",'
        '"mode":"yolo","scope":"local",'
        '"params":[{"name":"portfolio","label":"P","type":"portfolio"}]}\n'
        'await step(f"risk for {args.portfolio}?")\n'
    )
    r = client.post("/api/workflows", json={"script": script})
    assert r.status_code == 200, r.text
    return "need-portfolio"


def test_run_missing_args_422(client):
    slug = _create_param_wf(client)
    tid = _make_thread(client)
    r = client.post(f"/api/chat/threads/{tid}/workflows/{slug}/run", json={"mode": "yolo"})
    assert r.status_code == 422


def test_run_with_valid_args_streams(client, monkeypatch):
    import app.main as main_mod

    captured = {}

    async def fake_drive(thread_id, prompt, mode):
        captured["prompt"] = prompt
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    monkeypatch.setattr(
        main_mod, "_desk_workflow_drive_factory", lambda svc, character="auto": fake_drive
    )
    monkeypatch.setattr(main_mod, "_desk_workflow_settle_factory", lambda: (lambda: None))

    slug = _create_param_wf(client)
    tid = _make_thread(client)
    r = client.post(
        f"/api/chat/threads/{tid}/workflows/{slug}/run",
        json={"mode": "yolo", "args": {"portfolio": "Default"}},
    )
    assert r.status_code == 200
    assert "event: workflow.complete" in r.text
    assert captured["prompt"] == "risk for Default?"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_run_endpoint.py -k "args" -v`
Expected: FAIL — missing-args returns 200 (no validation yet), and the valid-args prompt is unsubstituted.

- [ ] **Step 3: Wire validation + args into the endpoint**

In `backend/app/main.py`, inside `run_thread_workflow`, update the imports and body. Replace the existing block:

```python
        from .services.desk_workflow_runner import persona_to_character, run_desk_workflow
        from .services.desk_workflows import get_desk_workflow

        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        ensure_thread_workflow_state(session, thread.id)
        session.commit()
        mode = (payload or {}).get("mode") or wf.default_mode
```

with:

```python
        from .services.desk_workflow_runner import persona_to_character, run_desk_workflow
        from .services.desk_workflows import get_desk_workflow
        from .services.desk_workflows_script import (
            WorkflowScriptError,
            extract_meta,
            validate_workflow_args,
        )

        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        raw_args = (payload or {}).get("args")
        if raw_args is None:
            raw_args = {}
        try:
            validated_args = validate_workflow_args(extract_meta(wf.script), raw_args)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ensure_thread_workflow_state(session, thread.id)
        session.commit()
        mode = (payload or {}).get("mode") or wf.default_mode
```

Then add `args=validated_args` to the `run_desk_workflow(...)` call:

```python
            run_desk_workflow(
                thread_id=thread.id, workflow=wf, mode=mode,
                drive=drive, settle=settle, args=validated_args,
            ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_run_endpoint.py -k "args or unknown_slug or factories" -v`
Expected: PASS for the new arg tests + `test_run_unknown_slug_404` + `test_real_factories_resolve`. (`test_run_streams_workflow_events` is updated in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_desk_workflow_run_endpoint.py
git commit -m "feat(workflows): validate + forward launch args in run endpoint"
```

---

### Task 5: Derived `params` property + summary schema field

**Files:**
- Modify: `backend/app/models.py` (class `DeskWorkflow`, around line 191)
- Modify: `backend/app/schemas.py:1662` (`DeskWorkflowSummaryOut`)
- Test: `tests/test_desk_workflows_model.py`, `tests/test_desk_workflows_api.py`

**Interfaces:**
- Consumes: `extract_meta`, `validate_params` (local import inside the property).
- Produces: `DeskWorkflow.params -> list[dict]` (derived, not a column); `DeskWorkflowSummaryOut.params: list[dict] = []`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desk_workflows_model.py`:

```python
def test_desk_workflow_params_property(session):
    script = (
        'meta = {"name":"pw","title":"PW","persona":"trader","mode":"auto",'
        '"scope":"local","params":[{"name":"p","label":"P","type":"portfolio"}]}\n'
        'await step(f"{args.p}")\n'
    )
    wf = DeskWorkflow(
        slug="pw", title="PW", persona="trader", description="",
        scope="local", default_mode="auto", script=script, source="user",
    )
    assert wf.params == [{"name": "p", "label": "P", "type": "portfolio"}]


def test_desk_workflow_params_empty_when_absent(session):
    wf = DeskWorkflow(
        slug="np", title="NP", persona="trader", description="",
        scope="local", default_mode="auto", script="meta = {}\n", source="user",
    )
    assert wf.params == []
```

Append to `tests/test_desk_workflows_api.py` a check that the summary carries params. This file uses a router-only `_make_app(session)` helper and a `session` fixture (NOT a `client` fixture) — mirror it:

```python
def test_list_includes_params(session):
    client = _make_app(session)
    script = (
        'meta = {"name":"with-params","title":"WP","persona":"trader","mode":"auto",'
        '"scope":"local","params":[{"name":"p","label":"P","type":"date"}]}\n'
        'await step(f"{args.p}")\n'
    )
    assert client.post("/api/workflows", json={"script": script}).status_code == 200
    rows = client.get("/api/workflows").json()
    row = next(r for r in rows if r["slug"] == "with-params")
    assert row["params"] == [{"name": "p", "label": "P", "type": "date"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_model.py -k params tests/test_desk_workflows_api.py -k params -v`
Expected: FAIL — `DeskWorkflow` has no `params`; summary has no `params` key.

- [ ] **Step 3: Add the property + schema field**

In `backend/app/models.py`, inside `class DeskWorkflow`, after the `updated_at` column, add:

```python
    @property
    def params(self) -> list[dict]:
        """Declared launch params, derived from the stored script's meta."""
        from .services.desk_workflows_script import extract_meta, validate_params
        try:
            return validate_params(extract_meta(self.script))
        except Exception:
            return []  # stored scripts are validated at save; be defensive
```

In `backend/app/schemas.py`, add `params` to `DeskWorkflowSummaryOut`:

```python
class DeskWorkflowSummaryOut(BaseModel):
    slug: str
    title: str
    persona: str
    description: str
    scope: str
    default_mode: str
    source: str
    params: list[dict] = []

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_model.py tests/test_desk_workflows_api.py -v`
Expected: PASS (existing tests unaffected — `params` is additive with a default).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/schemas.py tests/test_desk_workflows_model.py tests/test_desk_workflows_api.py
git commit -m "feat(workflows): expose derived params on summary schema"
```

---

### Task 6: Parameterize the flagship seed

**Files:**
- Modify: `backend/app/desk_workflow_seed.py`
- Modify: `tests/test_desk_workflow_run_endpoint.py` (`test_run_streams_workflow_events`)
- Test: `tests/test_desk_workflows_model.py` (existing `test_seed_flagship_present`)

**Interfaces:**
- Consumes: nothing new — edits constant strings only (safe for the migration import).
- Produces: flagship `risk-manager-control-day` declares 3 params (`portfolio`/portfolio, `start`/date, `end`/date) and interpolates `args` in its prompts. Still exactly 7 `await step(` calls.

- [ ] **Step 1: Update the existing endpoint test to pass args (it will fail first)**

In `tests/test_desk_workflow_run_endpoint.py`, change `test_run_streams_workflow_events` to send args, since the flagship now requires them:

```python
    body = client.post(
        f"/api/chat/threads/{tid}/workflows/risk-manager-control-day/run",
        json={"mode": "yolo", "args": {
            "portfolio": "Default", "start": "2026-03-24", "end": "2026-06-24",
        }},
    )
```

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_run_endpoint.py::test_run_streams_workflow_events -v`
Expected: FAIL — the current seed has no params, so `validate_workflow_args` rejects the unknown keys (422).

- [ ] **Step 2: Rewrite the flagship script with params + f-strings**

In `backend/app/desk_workflow_seed.py`, replace `FLAGSHIP_SCRIPT` with:

```python
FLAGSHIP_SCRIPT = '''meta = {
    "name": "risk-manager-control-day",
    "title": "Risk Manager Control Day",
    "persona": "risk_manager",
    "mode": "yolo",
    "scope": "shared",
    "description": "Full desk-control loop: stale-check, refresh, hotspot, Greeks landscape, stress test, backtest, governance report.",
    "params": [
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Backtest start", "type": "date"},
        {"name": "end", "label": "Backtest end", "type": "date"},
    ],
}

await step(f"What does the latest risk say for the portfolio: {args.portfolio}?")
await step(f"Run a fresh risk calculation for portfolio {args.portfolio} using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step(f"Run a Greeks landscape across spot shifts for portfolio {args.portfolio}.")
await step(f"Stress-test portfolio {args.portfolio} using the market-crash scenario set with the Control Profile.")
await step(f"Run a historical backtest of the delta-hedge strategy from {args.start} to {args.end}.")
await step(f"Generate a governance risk report for today's control session on portfolio {args.portfolio}.")
'''
```

- [ ] **Step 3: Verify the seed script validates and keeps 7 steps**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -c "from app.desk_workflow_seed import FLAGSHIP_SCRIPT; from app.services.desk_workflows_script import validate_script, extract_slug, validate_params, extract_meta; validate_script(FLAGSHIP_SCRIPT, slug=extract_slug(FLAGSHIP_SCRIPT)); print('params:', [p['name'] for p in validate_params(extract_meta(FLAGSHIP_SCRIPT))]); print('steps:', FLAGSHIP_SCRIPT.count('await step('))"`
Expected: `params: ['portfolio', 'start', 'end']` and `steps: 7`.

- [ ] **Step 4: Run the affected tests**

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflow_run_endpoint.py tests/test_desk_workflows_model.py -v`
Expected: PASS — `test_seed_flagship_present` still sees 7 steps; `test_run_streams_workflow_events` now passes args. If any other seed-content test fails (e.g. `test_save_desk_workflow_tool.py`, golden registry), run it and reconcile to the new prompts.

- [ ] **Step 5: Commit**

```bash
git add backend/app/desk_workflow_seed.py tests/test_desk_workflow_run_endpoint.py
git commit -m "feat(workflows): parameterize flagship seed (portfolio + date range)"
```

---

### Task 7: Frontend types + `launchWorkflow` carries args

**Files:**
- Modify: `frontend/src/types.ts:26` (`DeskWorkflowSummary`)
- Modify: `frontend/src/hooks/useAgentChatController.ts:81,466-493,703`
- Test: `frontend/src/hooks/useAgentChatController.test.tsx` (create if absent — see Step 1)

**Interfaces:**
- Produces: `DeskWorkflowSummary.params?: WorkflowParam[]`; `launchWorkflow(slug, mode, args?: Record<string, string>)` posts `{ mode, args }`.

- [ ] **Step 1: Write the failing test**

The only existing controller test (`useAgentChatController.workflow.test.ts`) covers the pure `parseWorkflowSse` — there's no hook-render harness to reuse. Create `frontend/src/hooks/useAgentChatController.launchArgs.test.tsx` with a **URL-routing** fetch mock that tolerates the hook's mount fetches (`/api/agent/models` returns an `AgentModelConfig`-shaped object; `/api/chat/threads` GET returns a list):

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useAgentChatController } from './useAgentChatController';

describe('launchWorkflow args', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.restoreAllMocks();
    fetchMock = vi.fn((url: string, init?: { method?: string; body?: string }) => {
      const u = String(url);
      if (u.endsWith('/api/agent/models')) {
        return Promise.resolve({ ok: true, json: async () => ({ active: null, channels: [] }) });
      }
      if (u.includes('/workflows/') && u.includes('/run')) {
        return Promise.resolve({ ok: true, body: null });
      }
      if (u.includes('/api/chat/threads') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ id: 7, title: 't', character: 'trader', source: 'desk', messages: [] }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => [] }); // GET threads list, polling, etc.
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  it('posts args in the run body', async () => {
    const { result } = renderHook(() => useAgentChatController());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled()); // let mount settle
    await act(async () => {
      await result.current.launchWorkflow('need-portfolio', 'yolo', { portfolio: 'Default' });
    });
    const runCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/workflows/need-portfolio/run'));
    expect(runCall).toBeTruthy();
    expect(JSON.parse((runCall![1] as { body: string }).body)).toMatchObject({
      mode: 'yolo', args: { portfolio: 'Default' },
    });
  });
});
```

> The `api()` helper checks `res.ok` and calls `res.json()`; `launchWorkflow` uses raw `fetch` and only reads `response.body` (null here → it skips the stream reader and finishes). The mount `fetchCatalog` reads `cfg.active` (null is fine since `selectedModel` starts null, so `selectionExists` is never called).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useAgentChatController.launchArgs.test.tsx`
Expected: FAIL — `launchWorkflow` ignores the third arg; body has no `args`.

- [ ] **Step 3: Add the type and thread args through**

In `frontend/src/types.ts`, above `DeskWorkflowSummary`, add the param type and a field:

```typescript
export type WorkflowParam = {
  name: string;
  label: string;
  type: 'string' | 'date' | 'portfolio';
};

export type DeskWorkflowSummary = {
  slug: string;
  title: string;
  persona: 'trader' | 'risk_manager' | 'sales' | 'quant';
  description: string;
  scope: 'local' | 'shared';
  default_mode: 'auto' | 'yolo';
  source: 'seed' | 'user';
  params?: WorkflowParam[];
};
```

In `frontend/src/hooks/useAgentChatController.ts`:
- Line ~81 (the `AgentChatController` type): change the `launchWorkflow` signature to
  `launchWorkflow: (slug: string, mode: 'auto' | 'yolo', args?: Record<string, string>) => Promise<void>;`
- Line ~466 (`const launchWorkflow = useCallback(async (slug, mode) => {`): change to
  `const launchWorkflow = useCallback(async (slug: string, mode: 'auto' | 'yolo', args?: Record<string, string>) => {`
- Line ~492 (the POST body): change to
  `body: JSON.stringify({ mode, args: args ?? {} }),`

- [ ] **Step 4: Run the test + typecheck**

Run: `cd frontend && npx vitest run src/hooks/useAgentChatController.launchArgs.test.tsx && npx tsc --noEmit`
Expected: PASS, tsc exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/hooks/useAgentChatController.ts frontend/src/hooks/useAgentChatController.launchArgs.test.tsx
git commit -m "feat(workflows): launchWorkflow forwards launch args"
```

---

### Task 8: `WorkflowParamsDialog` component

**Files:**
- Create: `frontend/src/components/WorkflowParamsDialog.tsx`
- Create: `frontend/src/components/WorkflowParamsDialog.css`
- Test: `frontend/src/components/WorkflowParamsDialog.test.tsx`

**Interfaces:**
- Consumes: `Modal`, `Button`, `DatePicker`, `WorkflowParam`, `DeskWorkflowSummary`.
- Produces: `WorkflowParamsDialog({ open, workflow, portfolios, onCancel, onRun })` where `workflow: DeskWorkflowSummary`, `portfolios: string[]`, `onRun: (args: Record<string, string>) => void`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/WorkflowParamsDialog.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkflowParamsDialog } from './WorkflowParamsDialog';
import type { DeskWorkflowSummary } from '../types';

const WF: DeskWorkflowSummary = {
  slug: 'rmcd', title: 'RMCD', persona: 'risk_manager',
  description: '', scope: 'shared', default_mode: 'yolo', source: 'seed',
  params: [
    { name: 'portfolio', label: 'Portfolio', type: 'portfolio' },
    { name: 'start', label: 'Start date', type: 'date' },
  ],
};

it('renders one field per param', () => {
  render(<WorkflowParamsDialog open workflow={WF} portfolios={['Default']} onCancel={() => {}} onRun={() => {}} />);
  expect(screen.getByText('Portfolio')).toBeInTheDocument();
  expect(screen.getByText('Start date')).toBeInTheDocument();
});

it('disables Run until all fields are filled, then emits args', () => {
  const onRun = vi.fn();
  render(<WorkflowParamsDialog open workflow={WF} portfolios={['Default']} onCancel={() => {}} onRun={onRun} />);
  const run = screen.getByRole('button', { name: /run/i });
  expect(run).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Portfolio'), { target: { value: 'Default' } });
  fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-06-25' } });
  expect(run).not.toBeDisabled();
  fireEvent.click(run);
  expect(onRun).toHaveBeenCalledWith({ portfolio: 'Default', start: '2026-06-25' });
});
```

> Note: the test drives the date field through a raw `<input aria-label>` so it does not depend on `DatePicker`'s popover internals. Implement the `date` control as a `DatePicker` for production but ensure each field's control is reachable by its label text (`aria-label={param.label}`). If `DatePicker` does not forward an accessible name, wrap a native `<input type="text">` fallback labelled by `param.label` — keep it token-styled. Confirm `DatePicker`'s accessible name during Step 3; adjust the test selector only if `DatePicker` already exposes `param.label` as its accessible name.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/WorkflowParamsDialog.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the dialog**

Create `frontend/src/components/WorkflowParamsDialog.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { Button } from './Button';
import type { DeskWorkflowSummary } from '../types';
import './WorkflowParamsDialog.css';

type Props = {
  open: boolean;
  workflow: DeskWorkflowSummary;
  portfolios: string[];
  onCancel: () => void;
  onRun: (args: Record<string, string>) => void;
};

export function WorkflowParamsDialog({ open, workflow, portfolios, onCancel, onRun }: Props) {
  const params = workflow.params ?? [];
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) setValues({});
  }, [open]);

  const set = (name: string, value: string) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  const allFilled = params.every((p) => (values[p.name] ?? '').trim().length > 0);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!allFilled) return;
    const clean: Record<string, string> = {};
    for (const p of params) clean[p.name] = values[p.name].trim();
    onRun(clean);
  };

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onCancel(); }}
      title={`Run · ${workflow.title}`}
      layoutKey="workflow-params"
      defaultHeight={360}
    >
      <form className="wl-wf-params" onSubmit={submit}>
        {params.map((p) => (
          <label key={p.name} className="wl-wf-params__field">
            <span>{p.label}</span>
            {p.type === 'portfolio' ? (
              <select
                className="wl-input"
                aria-label={p.label}
                value={values[p.name] ?? ''}
                onChange={(e) => set(p.name, e.target.value)}
              >
                <option value="" disabled>Select a portfolio…</option>
                {portfolios.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            ) : (
              <input
                className="wl-input"
                type={p.type === 'date' ? 'date' : 'text'}
                aria-label={p.label}
                value={values[p.name] ?? ''}
                onChange={(e) => set(p.name, e.target.value)}
              />
            )}
          </label>
        ))}
        <div className="wl-wf-params__actions">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!allFilled}>Run</Button>
        </div>
      </form>
    </Modal>
  );
}
```

> Implementation note on the date control: a native `<input type="date">` is used here for a token-clean, accessible-by-label control that satisfies the spec (ISO `YYYY-MM-DD` value) without depending on `DatePicker`'s popover. This keeps the component self-contained and the test deterministic. If desk-wide consistency later wants the custom `DatePicker`, swap it in behind the same `aria-label={p.label}` accessible name.

Create `frontend/src/components/WorkflowParamsDialog.css` (token-only):

```css
.wl-wf-params {
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
  padding: var(--gap-3);
}
.wl-wf-params__field {
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-wf-params__field > span {
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-2);
}
.wl-wf-params__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--gap-2);
  margin-top: var(--gap-2);
}
```

> Before committing, verify each token exists in `frontend/src/tokens/` (the builder-chat refinement caught a phantom `--paper-1`). Confirm `--gap-2`, `--gap-3`, `--type-caps-size`, `--ink-2`, and the `wl-input` primitive class are real; substitute the nearest real token if any is missing.

- [ ] **Step 4: Run the tests + typecheck**

Run: `cd frontend && npx vitest run src/components/WorkflowParamsDialog.test.tsx && npx tsc --noEmit`
Expected: PASS, tsc exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/WorkflowParamsDialog.tsx frontend/src/components/WorkflowParamsDialog.css frontend/src/components/WorkflowParamsDialog.test.tsx
git commit -m "feat(workflows): launch-params dialog component"
```

---

### Task 9: Composer opens the dialog; live wrapper supplies portfolios

**Files:**
- Modify: `frontend/src/components/ChatComposer.tsx:56-91`
- Modify: `frontend/src/routes/AgentDesk.tsx:51-52,98-99,171-183`
- Modify: `frontend/src/routes/AgentDesk.live.tsx`
- Modify: `frontend/src/api/client.ts` (add `listPortfolios`)
- Test: `frontend/src/components/ChatComposer.test.tsx` (create if absent)

**Interfaces:**
- Consumes: `WorkflowParamsDialog`, `controller.launchWorkflow(slug, mode, args)`, `DeskWorkflowSummary.params`.
- Produces: `ChatComposer` prop `onRequestParams?: (workflow: DeskWorkflowSummary) => void`; a parameterized `/slug` selection calls `onRequestParams` instead of `onLaunchWorkflow`. `listPortfolios(): Promise<string[]>`.

- [ ] **Step 1: Write the failing composer test**

Create `frontend/src/components/ChatComposer.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatComposer } from './ChatComposer';
import type { DeskWorkflowSummary } from '../types';

const base: Omit<DeskWorkflowSummary, 'slug' | 'params'> = {
  title: 'T', persona: 'trader', description: '', scope: 'local',
  default_mode: 'auto', source: 'user',
};
const plain: DeskWorkflowSummary = { ...base, slug: 'plain' };
const param: DeskWorkflowSummary = {
  ...base, slug: 'needs', params: [{ name: 'p', label: 'P', type: 'string' }],
};

it('parameterized workflow requests params instead of launching', () => {
  const onLaunch = vi.fn();
  const onRequestParams = vi.fn();
  render(
    <ChatComposer
      onSend={() => {}} sending={false}
      workflows={[param]} onLaunchWorkflow={onLaunch} onRequestParams={onRequestParams}
    />,
  );
  fireEvent.change(screen.getByLabelText('Ask anything'), { target: { value: '/needs' } });
  fireEvent.click(screen.getByRole('option', { name: /\/needs/ }));
  expect(onRequestParams).toHaveBeenCalledWith(param);
  expect(onLaunch).not.toHaveBeenCalled();
});

it('zero-param workflow launches directly', () => {
  const onLaunch = vi.fn();
  const onRequestParams = vi.fn();
  render(
    <ChatComposer
      onSend={() => {}} sending={false}
      workflows={[plain]} onLaunchWorkflow={onLaunch} onRequestParams={onRequestParams}
    />,
  );
  fireEvent.change(screen.getByLabelText('Ask anything'), { target: { value: '/plain' } });
  fireEvent.click(screen.getByRole('option', { name: /\/plain/ }));
  expect(onLaunch).toHaveBeenCalledWith('plain', 'auto');
  expect(onRequestParams).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ChatComposer.test.tsx`
Expected: FAIL — `onRequestParams` is not a prop; both selections call `onLaunchWorkflow`.

- [ ] **Step 3: Wire the composer, AgentDesk, live wrapper, and api**

In `frontend/src/components/ChatComposer.tsx`:
- Add to `Props`: `onRequestParams?: (workflow: DeskWorkflowSummary) => void;`
- Add `onRequestParams` to the destructured params (next to `onLaunchWorkflow`).
- Change `launch`:

```tsx
  const launch = (w: DeskWorkflowSummary) => {
    if ((w.params?.length ?? 0) > 0 && onRequestParams) {
      onRequestParams(w);
      setText('');
      return;
    }
    onLaunchWorkflow?.(w.slug, w.default_mode);
    setText('');
  };
```

In `frontend/src/routes/AgentDesk.tsx`:
- Add to its `Props` (next to `onLaunchWorkflow`): `onRequestParams?: (workflow: DeskWorkflowSummary) => void;`
- Destructure `onRequestParams` and pass it to `<ChatComposer ... onRequestParams={onRequestParams} />`.

In `frontend/src/api/client.ts`, add:

```typescript
export const listPortfolios = () =>
  api<Array<{ name: string }>>('/api/portfolios').then((rows) => rows.map((r) => r.name));
```

In `frontend/src/routes/AgentDesk.live.tsx`:
- Import the dialog, the type, and the api: add `WorkflowParamsDialog` from `'../components/WorkflowParamsDialog'`, `listPortfolios` from `'../api/client'`, and ensure `DeskWorkflowSummary` is imported.
- Add state near the existing `workflows` state:

```tsx
  const [paramsWorkflow, setParamsWorkflow] = useState<DeskWorkflowSummary | null>(null);
  const [portfolios, setPortfolios] = useState<string[]>([]);

  const handleRequestParams = (wf: DeskWorkflowSummary) => {
    if ((wf.params ?? []).some((p) => p.type === 'portfolio') && portfolios.length === 0) {
      void listPortfolios().then(setPortfolios).catch(() => setPortfolios([]));
    }
    setParamsWorkflow(wf);
  };
```

- Pass `onRequestParams={handleRequestParams}` to the rendered `AgentDesk`.
- Render the dialog (e.g. just before the closing fragment/element):

```tsx
      {paramsWorkflow && (
        <WorkflowParamsDialog
          open
          workflow={paramsWorkflow}
          portfolios={portfolios}
          onCancel={() => setParamsWorkflow(null)}
          onRun={(args) => {
            const wf = paramsWorkflow;
            setParamsWorkflow(null);
            void controller.launchWorkflow(wf.slug, wf.default_mode, args);
          }}
        />
      )}
```

> If `AgentDesk.live.tsx` returns `<AgentDesk .../>` directly (no wrapping element), wrap it in a fragment `<>…</>` so the dialog can be a sibling. Verify `useState` is imported.

- [ ] **Step 4: Run the tests + typecheck + the surrounding suites**

Run: `cd frontend && npx vitest run src/components/ChatComposer.test.tsx src/routes/AgentDesk.arenaToggle.test.tsx src/routes/AgentDesk.live.test.tsx && npx tsc --noEmit`
Expected: PASS for the new composer tests; the existing AgentDesk tests stay green; tsc exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatComposer.tsx frontend/src/components/ChatComposer.test.tsx frontend/src/routes/AgentDesk.tsx frontend/src/routes/AgentDesk.live.tsx frontend/src/api/client.ts
git commit -m "feat(workflows): open launch-params dialog from the slash picker"
```

---

## Final Verification (after all tasks)

- [ ] Backend desk-workflow suite green:

Run: `cd /Users/fuxinyao/open-otc-trading && .venv/bin/python -m pytest tests/test_desk_workflows_script.py tests/test_desk_workflow_runner.py tests/test_desk_workflow_run_endpoint.py tests/test_desk_workflows_model.py tests/test_desk_workflows_api.py tests/test_desk_workflows_service.py tests/test_save_desk_workflow_tool.py -v`
Expected: all PASS. Reconcile any seed-content assertion to the new flagship prompts.

- [ ] Frontend: `cd frontend && npx vitest run src/components/WorkflowParamsDialog.test.tsx src/components/ChatComposer.test.tsx src/hooks/useAgentChatController.launchArgs.test.tsx && npx tsc --noEmit` — all green, tsc exit 0.

- [ ] Manual smoke (optional): start the app, type `/risk-manager-control-day`, confirm the dialog opens with Portfolio dropdown + two date fields, fill them, Run, and confirm the first streamed step prompt reads "…portfolio: Default?".

- [ ] Confirm no new regressions beyond the documented 12 pre-existing frontend failures (seedless-market-data Booking/Positions/TermForm).

## Notes on Coupling / Risk

- `test_desk_workflow_run_endpoint.py::test_run_streams_workflow_events` **must** be updated in Task 6 (the flagship now requires args) — this is the one pre-existing test the seed change breaks.
- `DeskWorkflowSummaryOut.params` is additive (default `[]`); existing API-shape assertions keep passing.
- The migration 0035 imports `FLAGSHIP_SCRIPT` from `desk_workflow_seed.py`, so the seed edit covers both the migration and the boot-seed with one change — verify the migration still imports the constant rather than duplicating the string (`grep -n FLAGSHIP_SCRIPT backend/alembic/versions/0035_desk_workflows.py`).
- No new skill, route, or tool — so the skill-catalog/routing-table coupling ([[skill_catalog_test_coupling]]) is **not** touched.
- `keyword.iskeyword` only rejects hard keywords; soft keywords (`match`, `case`, `type`, `_`) are valid identifiers and remain allowed — intentional.
