# Structured-Answer Scoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flagship's 5 fuzzy grounding/adherence checks with typed,
role-bound structured-answer checks driven by a new `record_answer` tool, keeping the
39-point denominator and 39/39 golden replay.

**Architecture:** A benign `record_answer(answer: dict)` tool records the model's typed
answer as tool-call args. Two new assertion types (`answer_field_equals` → adherence,
`answer_field_quotes` → grounding) read the merged answer from `ctx.tool_calls` and
verify a field by key. The flagship manifest swaps 5 checks 1:1 and names the required
keys in the step prompts. `record_answer` is excluded from the EFF tool-count so
complying is not penalized.

**Tech Stack:** Python 3 / FastAPI / Pydantic / pytest. LangChain `@tool`.

## Global Constraints

- **Denominator stays 39.** `test_flagship_loads`, `test_arena_scoring`,
  `test_golden_workflow_regression` (replay must earn 39/39) all pin it.
- **Axis conservation:** `answer_field_equals` → **adherence**, `answer_field_quotes` →
  **grounding** — same axes as the checks they replace.
- **Backend tests:** `.venv/bin/python -m pytest` from repo root.
- **A callable tool MUST be in `DEEP_AGENT_TOOL_NAMES`** (`services/agents.py`), not just
  `QUANT_AGENT_TOOLS` — else `select_deep_agent_tools()` silently drops it.
- **`len(QUANT_AGENT_TOOLS)` is pinned** at `tests/test_capability_assignments.py:29`
  (97 → 98); adding the tool without bumping it fails repo-level pytest.
- **`truth.json` is harvester-owned** — never hand-edit it; a fresh numeric-only
  `harvest()` must reproduce it exactly (`test_arena_fixture_determinism`).
- **`record_answer` tolerates both nested `answer={...}` and flat-kwargs calls** — the
  prompt teaches the nested form but neither the tool nor the accessor may assume it.
- **CHANGELOG.md** `[Unreleased]` updated before the PR.
- Truth numbers are already deterministic (Spec A); no producer changes.

---

### Task 1: `record_answer` tool

**Files:**
- Create: `backend/app/tools/record_answer.py`
- Modify: `backend/app/tools/__init__.py` (add to `QUANT_AGENT_TOOLS` import + list)
- Modify: `backend/app/services/agents.py` (add `"record_answer"` to `DEEP_AGENT_TOOL_NAMES`)
- Modify: `tests/test_capability_assignments.py` (pinned count 97 → 98 + DOMAIN_READ spot-check)
- Test: `tests/test_record_answer_tool.py`

**Interfaces:**
- Produces: a LangChain tool named `record_answer` that **tolerates both call shapes** —
  nested `record_answer(answer={...})` **and** flat `record_answer(hotspot=..., delta=...)`
  — returning `{"recorded": True, "fields": {...}}` with the merged fields. (Robustness
  requirement: models will not reliably nest under `answer`; Codex plan-review [high].)

- [ ] **Step 1: Write the failing test — both call shapes + selectability + capability**

```python
# tests/test_record_answer_tool.py
from app.tools.record_answer import record_answer_tool
from app.services.agents import select_deep_agent_tools

def test_record_answer_nested_shape():
    out = record_answer_tool.invoke({"answer": {"hotspot": "AAPL", "delta": 573.35}})
    assert out == {"recorded": True, "fields": {"hotspot": "AAPL", "delta": 573.35}}

def test_record_answer_flat_kwargs_shape():
    # A model that ignores the `answer` wrapper and passes fields flat must still
    # be captured — otherwise live runs miss while nested replay fixtures pass.
    out = record_answer_tool.invoke({"hotspot": "AAPL", "delta": 573.35})
    assert out == {"recorded": True, "fields": {"hotspot": "AAPL", "delta": 573.35}}

def test_record_answer_bounds_payload():
    # capture-sink guard: oversized string truncated, field count capped
    big = {"blob": "x" * 5000, **{f"k{i}": i for i in range(50)}}
    out = record_answer_tool.invoke({"answer": big})
    assert len(out["fields"]) <= 32
    assert all(not isinstance(v, str) or len(v) <= 257 for v in out["fields"].values())

def test_record_answer_is_selectable():
    names = {t.name for t in select_deep_agent_tools()}
    assert "record_answer" in names
```

- [ ] **Step 2: Run it — expect failure**

Run: `.venv/bin/python -m pytest tests/test_record_answer_tool.py -q`
Expected: FAIL (module missing / name not in allowlist).

- [ ] **Step 3: Implement the tool (tolerant of both shapes)**

```python
# backend/app/tools/record_answer.py
"""record_answer — a no-op recorder that captures a model's typed answer.

Used by golden-workflow steps that ask the model to commit a structured answer.
Tolerates BOTH the canonical nested shape record_answer(answer={"hotspot":"AAPL",
"delta":573.35}) AND a flat shape record_answer(hotspot="AAPL", delta=573.35),
because models will not reliably nest under `answer`. The args are read back at
score time via the answer_field_* assertions. It changes no state; it is
DOMAIN_READ (benign) so it is safe inside read-only fan-out and never triggers
audit-write classification.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

# Exact repo paths (verified against backend/app/tools/risk.py:19-20).
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup

# Bounds so the globally-exposed recorder can't become an unbounded capture sink
# (tool inputs are persisted by the local tracer). A benchmark answer is a handful
# of scalars; anything past these caps is truncated, not retained.
_MAX_FIELDS = 32
_MAX_STR = 256


def _bound_value(v: Any) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= _MAX_STR else s[:_MAX_STR] + "…"


def _bound_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: _bound_value(v) for k, v in list(fields.items())[:_MAX_FIELDS]}


class RecordAnswerInput(BaseModel):
    # extra="allow" so a flat call record_answer(hotspot=..., delta=...) validates
    # instead of erroring; those extras are merged into fields alongside `answer`.
    model_config = ConfigDict(extra="allow")
    answer: dict[str, Any] = Field(
        default_factory=dict,
        description="Your structured answer as key→value pairs, e.g. "
                    '{"hotspot": "AAPL", "delta": 573.35}. You may also pass the '
                    "fields directly as keyword arguments.",
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("record_answer", args_schema=RecordAnswerInput)
def record_answer_tool(answer: dict[str, Any] | None = None,
                       **extra: Any) -> dict[str, Any]:
    """Record your final structured answer for this question when asked to. Pass
    each requested field either inside `answer` (e.g.
    answer={"hotspot": "AAPL", "delta": 573.35}) or as direct keyword arguments.
    This does not change any state; it captures your answer verbatim for
    evaluation."""
    fields: dict[str, Any] = dict(answer or {})
    fields.update(extra)  # tolerate flat kwargs
    fields = _bound_fields(fields)  # cap field count + value size (capture-sink guard)
    return {"recorded": True, "fields": fields}
```

> If the repo's import lines for `capability_gated`/`ToolGroup` differ, copy them
> verbatim from `backend/app/tools/risk.py:19-20`.

- [ ] **Step 4: Register the tool + update the count guard**

In `backend/app/tools/__init__.py`: import `record_answer_tool` and add it to the
`QUANT_AGENT_TOOLS` list (both the import block and the list, mirroring
`get_latest_risk_run_tool`). In `backend/app/services/agents.py`: add `"record_answer"`
to the `DEEP_AGENT_TOOL_NAMES` frozenset (keep alphabetical if the set is sorted).

In `tests/test_capability_assignments.py`: bump the pinned count
`assert len(QUANT_AGENT_TOOLS) == 97` → `== 98` (update any adjacent comment), and add a
spot-check that `record_answer` resolves to `ToolGroup.DOMAIN_READ` (mirror how the test
already asserts a tool's group; if it iterates an expected group→names map, add
`record_answer` under `DOMAIN_READ`).

- [ ] **Step 5: Run tests — expect pass**

Run: `.venv/bin/python -m pytest tests/test_record_answer_tool.py tests/test_capability_assignments.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools/record_answer.py backend/app/tools/__init__.py \
        backend/app/services/agents.py tests/test_record_answer_tool.py \
        tests/test_capability_assignments.py
git commit -m "feat(arena): add benign record_answer tool for structured answers"
```

---

### Task 2: `answer_fields` accessor + schema for the two assertion types

**Files:**
- Modify: `backend/app/golden_workflows/assertions.py` (add `answer_fields` + helpers)
- Modify: `backend/app/golden_workflows/schema.py` (two models + Union entry)
- Test: `tests/test_golden_workflow_assertions.py`

**Interfaces:**
- Produces: `answer_fields(ctx) -> dict[str, Any]`; schema types
  `answer_field_equals {field, equals|any_of}` and
  `answer_field_quotes {field, value, rel_tol=0.02, match=signed}`.

- [ ] **Step 1: Write failing tests for the accessor**

```python
# tests/test_golden_workflow_assertions.py (append)
from app.golden_workflows.assertions import AssertionContext, answer_fields

def _ctx(tool_calls, response_text=""):
    return AssertionContext(response_text=response_text, tool_calls=tool_calls,
                            tool_results=[], skills_routed=[], artifacts=[], task_ids=[])

def test_answer_fields_merges_last_wins():
    ctx = _ctx([
        {"name": "record_answer", "args": {"answer": {"hotspot": "TSLA", "delta": 1.0}}},
        {"name": "record_answer", "args": {"answer": {"delta": 573.35}}},
    ])
    assert answer_fields(ctx) == {"hotspot": "TSLA", "delta": 573.35}

def test_answer_fields_flat_kwargs_shape():
    ctx = _ctx([{"name": "record_answer", "args": {"hotspot": "AAPL", "delta": 573.35}}])
    assert answer_fields(ctx) == {"hotspot": "AAPL", "delta": 573.35}

def test_answer_fields_empty_when_absent():
    assert answer_fields(_ctx([{"name": "get_latest_risk_run", "args": {}}])) == {}
```

- [ ] **Step 2: Run — expect ImportError/FAIL.**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -q -k answer_fields`

- [ ] **Step 3: Implement the accessor + detail helper in `assertions.py`**

Add near the top-level helpers (after `AssertionContext`):

```python
def answer_fields(ctx: "AssertionContext") -> dict[str, Any]:
    """Merged answer of every record_answer call in this context (last-wins per key).

    Tolerates both call shapes the tool accepts: nested args={"answer": {...}} and
    flat args={"hotspot": ..., "delta": ...}. For each call, the nested `answer`
    dict (if any) is merged first, then the remaining top-level arg keys — so a
    model that flattens is still captured (Codex plan-review [high]).
    """
    from app.golden_workflows.schema import normalize_tool_name
    merged: dict[str, Any] = {}
    for c in ctx.tool_calls:
        if normalize_tool_name(c.get("name", "")) != "record_answer":
            continue
        args = c.get("args") or {}
        nested = args.get("answer")
        if isinstance(nested, dict):
            merged.update(nested)
        merged.update({k: v for k, v in args.items() if k != "answer"})
    return merged

def _no_answer_detail(fields: dict, field: str) -> str:
    if not fields:
        return f"no answer recorded for {field}"
    shown = ", ".join(f"{k}={v!r}" for k, v in list(fields.items())[:6])
    return f"key {field} absent; answered: {shown}"

def _coerce_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip().rstrip("%"))
    except (ValueError, AttributeError):
        return None
```

- [ ] **Step 4: Add the two schema models + Union entry in `schema.py`**

```python
class _AnswerFieldEquals(BaseModel):
    type: Literal["answer_field_equals"]
    field: str = Field(min_length=1)
    equals: str | None = None
    any_of: list[str] | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.equals is None) == (self.any_of is None):
            raise ValueError("answer_field_equals: exactly one of equals/any_of")
        if self.any_of is not None and not self.any_of:
            raise ValueError("answer_field_equals: any_of must be non-empty")
        return self

class _AnswerFieldQuotes(BaseModel):
    type: Literal["answer_field_quotes"]
    field: str = Field(min_length=1)
    value: float
    rel_tol: float = 0.02
    match: Literal["signed", "magnitude"] = "signed"

    @model_validator(mode="after")
    def _check(self):
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        return self
```

Add both to the `Assertion` Union (after `_ResponseQuotesValue`). Confirm
`model_validator` is already imported in `schema.py` (it is — used by `_ToolCalled`).

- [ ] **Step 5: Run the accessor tests — expect PASS.**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -q -k answer_fields`

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/assertions.py backend/app/golden_workflows/schema.py \
        tests/test_golden_workflow_assertions.py
git commit -m "feat(arena): answer_fields accessor + answer_field_* schema types"
```

---

### Task 3: Evaluate the two assertion types

**Files:**
- Modify: `backend/app/golden_workflows/assertions.py` (`evaluate_assertion` branches)
- Modify: `backend/app/services/arena/scoring.py` (`_AXIS_BY_TYPE`)
- Test: `tests/test_golden_workflow_assertions.py`, `tests/test_arena_scoring.py`

**Interfaces:**
- Consumes: `answer_fields`, `_no_answer_detail`, `_coerce_num` (Task 2).

- [ ] **Step 1: Write failing evaluation tests**

```python
# tests/test_golden_workflow_assertions.py (append)
from app.golden_workflows.assertions import evaluate_assertion
from app.golden_workflows.schema import _AnswerFieldEquals, _AnswerFieldQuotes

def _answer_ctx(fields):
    return _ctx([{"name": "record_answer", "args": {"answer": fields}}])

def test_answer_field_equals_hit_and_miss():
    a = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", equals="AAPL")
    assert evaluate_assertion(a, _answer_ctx({"hotspot": "aapl"}))[0] is True
    ok, detail = evaluate_assertion(a, _answer_ctx({"hotspot": "TSLA"}))
    assert ok is False and "TSLA" in detail

def test_answer_field_equals_no_answer_and_wrong_key():
    a = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", equals="AAPL")
    assert evaluate_assertion(a, _ctx([]))[1] == "no answer recorded for hotspot"
    ok, detail = evaluate_assertion(a, _answer_ctx({"underlying": "AAPL"}))
    assert ok is False and "key hotspot absent" in detail and "underlying" in detail

def test_answer_field_quotes_signed_and_magnitude():
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467)
    assert evaluate_assertion(a, _answer_ctx({"delta": 573.35}))[0] is True
    assert evaluate_assertion(a, _answer_ctx({"delta": -573.35}))[0] is False  # sign matters
    m = _AnswerFieldQuotes(type="answer_field_quotes", field="cvar",
                           value=-7758.99, match="magnitude")
    assert evaluate_assertion(m, _answer_ctx({"cvar": 7758.5}))[0] is True

def test_answer_field_quotes_non_numeric_and_wrong_key():
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467)
    ok, detail = evaluate_assertion(a, _answer_ctx({"delta": "the delta"}))
    assert ok is False and "not numeric" in detail
    assert evaluate_assertion(a, _ctx([]))[1] == "no answer recorded for delta"
```

- [ ] **Step 2: Run — expect FAIL (unknown assertion).**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -q -k answer_field`

- [ ] **Step 3: Add the branches in `evaluate_assertion` (before the final `return False, f"unknown assertion {t}"`)**

```python
    if t == "answer_field_equals":
        fields = answer_fields(ctx)
        if a.field not in fields:
            return False, _no_answer_detail(fields, a.field)
        got = fields[a.field]
        wants = a.any_of if a.any_of else [a.equals]
        norm = lambda s: str(s).strip().lower()
        ok = norm(got) in [norm(w) for w in wants]
        return ok, "" if ok else f"{a.field}={got!r} != {a.equals or a.any_of}"
    if t == "answer_field_quotes":
        fields = answer_fields(ctx)
        if a.field not in fields:
            return False, _no_answer_detail(fields, a.field)
        got = _coerce_num(fields[a.field])
        if got is None:
            return False, f"{a.field}={fields[a.field]!r} is not numeric"
        target = float(a.value)
        gv, tv = (got, target) if a.match == "signed" else (abs(got), abs(target))
        tol = a.rel_tol * abs(target) if target != 0 else a.rel_tol
        ok = abs(gv - tv) <= tol
        return ok, "" if ok else (
            f"{a.field}={got} != {a.value} (rel_tol={a.rel_tol}, match={a.match})")
```

- [ ] **Step 4: Map the axes in `scoring.py::_AXIS_BY_TYPE`**

Add:
```python
    "answer_field_equals": "adherence",
    "answer_field_quotes": "grounding",
```

Add a test in `tests/test_arena_scoring.py`:
```python
def test_answer_field_axes():
    from app.services.arena.scoring import _AXIS_BY_TYPE
    assert _AXIS_BY_TYPE["answer_field_equals"] == "adherence"
    assert _AXIS_BY_TYPE["answer_field_quotes"] == "grounding"
```

- [ ] **Step 5: Run — expect PASS.**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py tests/test_arena_scoring.py -q -k "answer_field or axes"`

- [ ] **Step 6: Add a human label for the new types in `_assertion_label` (scoring.py)**

Find `_assertion_label(a)` and add cases so the drilldown reads cleanly:
```python
    if t == "answer_field_equals":
        return f"answer {a.field} = {a.equals or a.any_of}"
    if t == "answer_field_quotes":
        return f"answer {a.field} quotes {a.value}"
```
(Confirm the exact fall-through style of the function first.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/assertions.py backend/app/services/arena/scoring.py \
        tests/test_golden_workflow_assertions.py tests/test_arena_scoring.py
git commit -m "feat(arena): evaluate answer_field_equals/quotes; map axes + labels"
```

---

### Task 4: Exclude `record_answer` from the EFF tool count

**Files:**
- Modify: `backend/app/services/arena/scoring.py` (`diagnose_heuristic`, line ~410)
- Test: `tests/test_arena_scoring.py`

**Interfaces:**
- Consumes: `diagnose_heuristic` returns `tool_calls` → feeds `card_from_axes` EFF.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_scoring.py (append) — build a minimal transcript with N workflow
# calls + 3 record_answer calls and assert diagnose_heuristic["tool_calls"] == N.
def test_record_answer_excluded_from_tool_count():
    from app.services.arena.scoring import diagnose_heuristic
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    step = MatchStep(index=0, user="u", messages=[], tool_calls=[
        {"name": "get_latest_risk_run", "args": {}},
        {"name": "record_answer", "args": {"answer": {"x": 1}}},
        # a harvested/live trace may carry the _tool suffix — must also be excluded
        {"name": "record_answer_tool", "args": {"answer": {"y": 2}}},
    ], tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
        response_text="", errors=[])
    t = MatchTranscript(schema_version=1, run_id=None, workflow_id="risk-manager-control-day",
                        model_id="x", started_at=None, finished_at=None, steps=[step])
    from app.golden_workflows.registry import get_workflow_bundle
    loaded = get_workflow_bundle("risk-manager-control-day")
    assert diagnose_heuristic(t, loaded)["tool_calls"] == 1
```

- [ ] **Step 2: Run — expect FAIL (count == 3).**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -q -k record_answer_excluded`

- [ ] **Step 3: Add the exclusion in `scoring.py`**

Near the top of `scoring.py`:
```python
from app.golden_workflows.schema import normalize_tool_name

# Benign answer-recording instrumentation — excluded from the EFF tool count so a
# model is not penalized for complying with a step's record_answer contract. Compared
# on the NORMALIZED name so a `record_answer_tool`-suffixed trace name is also excluded
# (the codebase already carries this _tool skew — that is why normalize_tool_name exists).
_EFF_EXCLUDED_TOOLS = frozenset({"record_answer"})

def _workflow_call_count(transcript) -> int:
    return sum(
        1 for s in transcript.steps for c in s.tool_calls
        if normalize_tool_name(c.get("name") or "") not in _EFF_EXCLUDED_TOOLS
    )
```

In `diagnose_heuristic`, replace `tool_calls = sum(len(s.tool_calls) for s in transcript.steps)`
with `tool_calls = _workflow_call_count(transcript)`.

- [ ] **Step 4: Run — expect PASS.**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -q -k record_answer_excluded`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/scoring.py tests/test_arena_scoring.py
git commit -m "feat(arena): exclude record_answer from EFF tool count"
```

---

### Task 5: Swap the 5 flagship checks + prompt contract

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md`
- Test: `tests/test_flagship_loads.py`

> **Do NOT touch `truth.json`.** It is harvester-owned: `test_arena_fixture_determinism`
> asserts `set(truth) == {TARGETS names}` and that the committed file equals a fresh
> numeric-only `harvest()`. A hand-added categorical key fails both gates (Codex
> plan-review [high]). The numeric `answer_field_quotes` targets already live in
> `truth.json`; the categorical hotspot ("AAPL") is derived from the existing
> `aapl_hotspot_delta.path` (`positions[underlying=AAPL].delta`) in the consistency test.

- [ ] **Step 1: Swap step 3 (replay `step-3-read-fresh-risk`)**

Replace the two assertions with:
```yaml
    assertions:
      - type: answer_field_equals
        field: hotspot
        equals: AAPL
      - type: answer_field_quotes
        field: delta
        value: 573.3467058766552
        match: signed
```
Append to that step's `user` (canonical nested call; the tool also accepts flat kwargs):
`" Record your answer by calling record_answer(answer={\"hotspot\": <ticker>, \"delta\": <number>})."`

- [ ] **Step 2: Swap step 5 (replay `step-grid-comprehension`)**

```yaml
      - type: answer_field_quotes
        field: gamma_at_+10pct
        value: 16.403033928381223
      - type: answer_field_quotes
        field: delta_at_-20pct
        value: 391.1919745962153
```
Append to `user`:
`" Record your answer by calling record_answer(answer={\"gamma_at_+10pct\": <number>, \"delta_at_-20pct\": <number>})."`

- [ ] **Step 3: Swap step 6 (replay `step-5-scenario-test`, the CVaR check only)**

Replace the `response_quotes_value` CVaR assertion (leave `task_returned_id`,
`tool_result_path`, `tool_called` untouched) with:
```yaml
      - type: answer_field_quotes
        field: cvar
        value: -7758.989817924667
        match: magnitude
```
Append to `user`:
`" Record the tail loss by calling record_answer(answer={\"cvar\": <number>})."`

- [ ] **Step 4: Extend the truth-consistency test in `tests/test_flagship_loads.py`**

Read `test_flagship_grounding_targets_match_truth_file` first, then extend its collection
loop so every `answer_field_quotes.value` in the flagship equals a `truth.json` numeric
value (same as the old `response_quotes_value` values did), and the
`answer_field_equals field=hotspot` `equals` value matches the underlying parsed from the
existing `truth["aapl_hotspot_delta"]["path"]` (i.e. `AAPL` from
`positions[underlying=AAPL].delta`). Do **not** reference a `hotspot_underlying` key —
it does not exist.

- [ ] **Step 5: Run loader + truth tests — expect PASS on structure (replay will fail until Task 6).**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py -q`
Expected: the loader/count/truth tests PASS. If a count test hard-asserts the old
assertion **type set**, update it to the new set (still 39 checks total).

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.md \
        tests/test_flagship_loads.py
git commit -m "feat(arena): swap 5 flagship checks to structured answers + prompt contract"
```

---

### Task 6: Re-record the 3 replay fixtures → golden replay 39/39

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json`
- Test: `tests/test_golden_workflow_regression.py`

- [ ] **Step 1: Run the regression to see the expected drop**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_regression.py -q`
Expected: FAIL — replay now scores 34/39 (5 structured checks miss; the replay has no
`record_answer` call yet).

- [ ] **Step 2: Add a `record_answer` call to each of the 3 replay entries**

In `fixtures.json`, for `fixtures.replay["step-3-read-fresh-risk"].ai.tool_calls`, append:
```json
{ "name": "record_answer",
  "args": { "answer": { "hotspot": "AAPL", "delta": 573.3467058766552 } } }
```
For `fixtures.replay["step-grid-comprehension"].ai.tool_calls`, append:
```json
{ "name": "record_answer",
  "args": { "answer": { "gamma_at_+10pct": 16.403033928381223,
                        "delta_at_-20pct": 391.1919745962153 } } }
```
For `fixtures.replay["step-5-scenario-test"].ai.tool_calls`, append:
```json
{ "name": "record_answer", "args": { "answer": { "cvar": -7758.989817924667 } } }
```
> Match the exact key each entry uses for calls (`tool_calls` vs a nested shape) — read
> one existing entry first and mirror its structure precisely.

- [ ] **Step 3: Run the regression — expect 39/39 PASS.**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_regression.py -q`

- [ ] **Step 4: Commit**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json
git commit -m "test(arena): record_answer in 3 replay fixtures — golden replay 39/39"
```

---

### Task 7: Trace-harvest plumbing gate (BLOCKING pre-merge)

The synthetic-transcript and replay tests do not prove that a **live**/captured trace's
`record_answer` tool call survives harvesting into `ctx.tool_calls` where the
`answer_field_*` checks read it (Codex plan-review [high]). This task proves that
plumbing deterministically — from persisted trace spans through the real
`trace_harvest._spans_to_turn_events` → `extract_step_from_events` seam — without a live
model. (Actual model *compliance* is the post-merge board in Task 9; if a model does not
call the tool it honestly scores 0 by design.)

**Files:**
- Test: `tests/test_arena_trace_harvest.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_trace_harvest.py (append)
def test_record_answer_survives_trace_harvest_into_answer_fields():
    from app.services.arena.trace_harvest import _spans_to_turn_events
    from app.golden_workflows.transcript import extract_step_from_events
    from app.golden_workflows.assertions import answer_fields, evaluate_assertion
    from app.golden_workflows.schema import _AnswerFieldQuotes
    # A realistic tool span as the tracer persists it — note the _tool suffix the
    # live registry emits, which the harvest + accessor must normalize/pass through.
    spans = [{
        "run_type": "tool", "name": "record_answer_tool",
        "inputs": {"answer": {"delta": 573.3467058766552}},
        "outputs": {"recorded": True, "fields": {"delta": 573.3467058766552}},
    }]
    turn = _spans_to_turn_events(0, "what's the hotspot?", spans)
    step = extract_step_from_events(turn)
    ctx = step  # extract_assertion_context-equivalent; build the ctx the scorer uses
    from app.golden_workflows.transcript import extract_assertion_context
    actx = extract_assertion_context(step.model_dump())
    assert answer_fields(actx).get("delta") == 573.3467058766552
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467058766552)
    assert evaluate_assertion(a, actx)[0] is True
```

> Read `_spans_to_turn_events` (trace_harvest.py:132) first and mirror the exact span
> shape it parses (`run_type`, `name`, `inputs`/`outputs` keys) — adjust the fixture
> span to whatever real key names that function reads, so the test exercises the true
> harvest path rather than a guessed shape.

- [ ] **Step 2: Run — expect FAIL if the harvest path drops the args; PASS once the span
  shape is correct.**

Run: `.venv/bin/python -m pytest tests/test_arena_trace_harvest.py -q -k record_answer_survives`

If the args do not survive (e.g. the tracer stores tool inputs under a different key),
that is a real integration gap — fix the accessor/harvest handling until a captured
`record_answer` call reaches `answer_fields`. **Do not merge until this passes.**

- [ ] **Step 3: Commit**

```bash
git add tests/test_arena_trace_harvest.py
git commit -m "test(arena): record_answer survives trace harvest into answer_fields"
```

---

### Task 8: Full-suite green + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`
- Test: whole golden/arena suite.

- [ ] **Step 1: Run the full golden + arena test set**

Run:
```bash
.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_arena_scoring.py \
  tests/test_golden_workflow_assertions.py tests/test_golden_workflow_regression.py \
  tests/test_record_answer_tool.py tests/test_capability_assignments.py \
  tests/test_arena_trace_harvest.py tests/test_arena_fixture_determinism.py -q
```
Expected: all PASS. If `test_arena_scoring.py` pins the assertion-type set or per-axis
totals, update those expectations to the new types (denominator still 39; the
adherence/grounding subtotals are unchanged in size).

- [ ] **Step 2: Update `CHANGELOG.md`**

Under `[Unreleased] → Changed`:
```
- Arena flagship: the two ambiguous grounding/adherence checks (hotspot, and the
  delta/gamma/CVaR value quotes) now score against a typed structured answer the
  model commits via the new `record_answer` tool, instead of fuzzy free-text
  scanning. Denominator unchanged (39); `record_answer` is excluded from the EFF
  tool count. Historical runs #1–#13 are not re-scored.
```
Under `Added`:
```
- `record_answer` agent tool + `answer_field_equals` / `answer_field_quotes`
  golden-workflow assertion types (role-bound answer verification).
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(arena): changelog for structured-answer scoring"
```

---

### Task 9: Live model-compliance board on the Run #12 set (post-merge)

**Not a code task** — a real arena board confirming actual providers/models emit
`record_answer`. The harvest *plumbing* is already proven blocking in Task 7; this is the
model-behavior confidence pass. Requires API keys.

- [ ] Drive a fresh run of `risk-manager-control-day` for DeepSeek-V4-Flash/Pro,
  Step-3.7-Flash, Mimo-V2.5 (n≥1) via the scratchpad driver used for Run #12.
- [ ] Confirm: compliant models emit `record_answer` and earn the swapped checks; card
  stats still sum to 39; the drilldown shows role-bound details (and a `key … absent`
  miss if any model mislabels). Record findings in the arena run memory.

---

## Self-Review

- **Spec coverage:** tool+count-guard (T1), accessor+schema (T2), evaluation+axes+labels
  (T3), EFF exclusion (T4), manifest swaps+prompt+consistency-test (T5), replay 39/39
  (T6), suite+changelog (T7), validation (T8) — all spec sections covered.
- **Codex plan-review fixes applied:** dual-shape tool+accessor (nested/flat), no
  `truth.json` hand-edit (derive hotspot from existing path), count guard 97→98 +
  capability test in T1/T7.
- **Types:** `answer_fields`, `_no_answer_detail`, `_coerce_num` defined in T2, consumed
  in T3; `_workflow_call_count` defined+used in T4; schema models `_AnswerFieldEquals`
  /`_AnswerFieldQuotes` defined T2, referenced T3/T5.
- **Denominator:** 5 removed + 5 added = 39 held; axes conserved (1 ADH, 4 GRD).
