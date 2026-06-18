# Booking Wizard Guidance (Term-Collection Card) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a direct booking has incomplete/invalid terms, the agent emits an interactive term-collection card (LLM-authored fields, chips, defaults); the user fills it and submits a string the agent re-validates via `build_product`, looping until legal, then books.

**Architecture:** Mirror the existing structured-reply-options pipeline end to end. A new `propose_term_form` tool declares a card payload; the stream collector captures its raw args at `on_tool_start` and normalizes them at `on_tool_end`; the orchestrator persists the normalized payload to `AgentMessage.meta["term_form"]`; the frontend renders a stacked card from that meta and, on submit, sends a `sentence + ```json{term_key: value}``` ` **string** user message via the existing reply-option send path. `build_product` stays the authoritative gate (a wrong/omitted field can never produce an illegal booking — the agent just re-emits the card).

**Tech Stack:** Python (LangChain `BaseTool`, Pydantic v2), FastAPI/SQLAlchemy meta persistence, React + TypeScript (Vite/Vitest/RTL), deepagents.

**Spec:** `docs/superpowers/specs/2026-05-31-booking-wizard-guidance-design.md`

**Conventions discovered during research (apply throughout):**
- `FieldSpec.key` is the **flat `build_product` terms key** the agent will merge (`initial_price`, `ko_barrier_pct`, `ki_barrier_pct`, `observation_frequency`, `trade_start_date`, `lockup_months`, `ko_rate`, `maturity_years`, …) — NOT the dotted `missing` identifier.
- Caps (backend tool + frontend mirror): `MAX_FIELDS=12`, `MIN_FIELDS=1`, `MAX_CHOICES=5`, `KEY_MAX=64`, `LABEL_MAX=56`, `HELP_MAX=160`, `CHOICE_LABEL_MAX=40`, `CHOICE_VALUE_MAX=64`. Field `type` ∈ `{"percent","number","date","enum","text"}`.
- Run backend tests with `python -m pytest`; frontend with `npm test --prefix frontend -- <file>` (vitest).

---

## File Structure

**Backend (new package mirrors `reply_options/`):**
- Create `backend/app/services/term_form/__init__.py`
- Create `backend/app/services/term_form/tool.py` — `ProposeTermFormTool`, `ProposeTermFormInput`, `FieldSpec`, `ChoiceSpec`, `_normalize_term_field`, caps.
- Modify `backend/app/tools/__init__.py` — register `ProposeTermFormTool` in `QUANT_AGENT_TOOLS` (capability-gated `PAGE_ACTION`).
- Modify `backend/app/services/deep_agent/stream_collector.py` — add `term_form` + `term_form_args` fields.
- Modify `backend/app/services/agents.py` — capture at tool-start, normalize at tool-end, persist to message meta, exclude from tool-name bookkeeping.

**Frontend:**
- Modify `frontend/src/types.ts` — `ChoiceMeta`, `TermFormField`, `TermFormMeta`, `meta.term_form?`.
- Create `frontend/src/components/termForm.ts` — `composeTermFormSubmission`, `validateTermFormValue` (pure, unit-testable).
- Create `frontend/src/components/TermForm.tsx` — the stacked card component.
- Modify `frontend/src/components/ChatBubble.tsx` — render `<TermForm>` from `meta.term_form`, wire submit to the existing send path.

**Skill:**
- Modify `backend/app/skills/workflows/products/build-product/SKILL.md` (full revised body in Task 9).
- Modify `backend/app/skills/workflows/positions/book-position/SKILL.md` (one-line reference).

**Tests:** `tests/test_term_form_tool.py`, `tests/test_term_form_capture.py`, `frontend/src/components/termForm.test.ts`, `frontend/src/components/TermForm.test.tsx`, plus a `ChatBubble.test.tsx` case.

---

## Task 1: `propose_term_form` backend tool

**Files:**
- Create: `backend/app/services/term_form/__init__.py`
- Create: `backend/app/services/term_form/tool.py`
- Test: `tests/test_term_form_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_term_form_tool.py
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.services.term_form.tool import (
    _CHOICE_LABEL_MAX,
    _HELP_MAX,
    _KEY_MAX,
    _LABEL_MAX,
    _MAX_CHOICES,
    _MAX_FIELDS,
    ChoiceSpec,
    FieldSpec,
    ProposeTermFormInput,
    ProposeTermFormTool,
    _normalize_term_field,
)

_OK_FIELD = {
    "key": "ko_barrier_pct",
    "label": "KO barrier",
    "help": "early-redemption level",
    "type": "percent",
    "choices": [{"label": "103%", "value": 103}],
    "default": {"label": "103%", "value": 103},
    "required": True,
}


def test_tool_metadata():
    tool = ProposeTermFormTool()
    assert tool.name == "propose_term_form"
    assert "term-collection card" in tool.description
    assert tool.args_schema is ProposeTermFormInput


def test_run_returns_count_ack():
    tool = ProposeTermFormTool()
    out = tool._run(title="Finish booking", fields=[_OK_FIELD])
    assert out == {"ok": True, "count": 1}


def test_arun_mirrors_run():
    tool = ProposeTermFormTool()
    out = asyncio.run(tool._arun(title="t", fields=[_OK_FIELD]))
    assert out == {"ok": True, "count": 1}


def test_input_schema_rejects_zero_fields():
    with pytest.raises(ValidationError):
        ProposeTermFormInput(title="t", fields=[])


def test_input_schema_rejects_too_many_fields():
    with pytest.raises(ValidationError):
        ProposeTermFormInput(
            title="t",
            fields=[{**_OK_FIELD, "key": f"k{i}"} for i in range(_MAX_FIELDS + 1)],
        )


def test_input_schema_rejects_bad_type():
    with pytest.raises(ValidationError):
        FieldSpec(key="k", label="L", type="dropdown")


def test_input_schema_rejects_too_many_choices():
    with pytest.raises(ValidationError):
        FieldSpec(
            key="k",
            label="L",
            type="enum",
            choices=[{"label": f"c{i}", "value": i} for i in range(_MAX_CHOICES + 1)],
        )


def test_input_schema_rejects_oversized_label():
    with pytest.raises(ValidationError):
        FieldSpec(key="k", label="x" * (_LABEL_MAX + 1), type="text")


def test_normalize_drops_non_dict():
    assert _normalize_term_field("nope") is None


def test_normalize_requires_key_and_label():
    assert _normalize_term_field({"label": "no key", "type": "text"}) is None
    assert _normalize_term_field({"key": "k", "type": "text"}) is None


def test_normalize_caps_and_coerces():
    norm = _normalize_term_field(
        {
            "key": "k" * (_KEY_MAX + 5),
            "label": "L" * (_LABEL_MAX + 5),
            "help": "h" * (_HELP_MAX + 5),
            "type": "percent",
            "choices": [{"label": "c" * (_CHOICE_LABEL_MAX + 5), "value": 1}] * 9,
            "default": {"label": "d", "value": 1},
        }
    )
    assert norm is not None
    assert len(norm["key"]) == _KEY_MAX
    assert len(norm["label"]) == _LABEL_MAX
    assert len(norm["help"]) == _HELP_MAX
    assert len(norm["choices"]) == _MAX_CHOICES
    assert len(norm["choices"][0]["label"]) == _CHOICE_LABEL_MAX
    assert norm["required"] is True  # default


def test_normalize_defaults_unknown_type_to_text():
    norm = _normalize_term_field({"key": "k", "label": "L", "type": "weird"})
    assert norm is not None
    assert norm["type"] == "text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_term_form_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.term_form'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/term_form/__init__.py
```

(empty file — package marker)

```python
# backend/app/services/term_form/tool.py
"""Backend tool: declare an interactive term-collection card for the next turn.

The LLM calls ``propose_term_form`` when a direct booking has missing/invalid
economics. The orchestrator captures the tool's input args (after Pydantic
validation) and writes them onto the persisted assistant message as
``meta["term_form"]``; the frontend renders a card. The tool is a pure
declaration — it does not mutate state. ``build_product`` remains the
authoritative gate, so card content is advisory only.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

_KEY_MAX = 64
_LABEL_MAX = 56
_HELP_MAX = 160
_CHOICE_LABEL_MAX = 40
_CHOICE_VALUE_MAX = 64
_MAX_CHOICES = 5
_MIN_FIELDS = 1
_MAX_FIELDS = 12
_FIELD_TYPES = ("percent", "number", "date", "enum", "text")


class ChoiceSpec(BaseModel):
    label: str = Field(..., min_length=1, max_length=_CHOICE_LABEL_MAX)
    value: str | float | int = Field(...)


class FieldSpec(BaseModel):
    key: str = Field(..., min_length=1, max_length=_KEY_MAX)
    label: str = Field(..., min_length=1, max_length=_LABEL_MAX)
    help: str | None = Field(None, max_length=_HELP_MAX)
    type: Literal["percent", "number", "date", "enum", "text"]
    choices: list[ChoiceSpec] | None = Field(None, max_length=_MAX_CHOICES)
    default: ChoiceSpec | None = None
    required: bool = True


class ProposeTermFormInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    subtitle: str | None = Field(None, max_length=200)
    fields: list[FieldSpec] = Field(..., min_length=_MIN_FIELDS, max_length=_MAX_FIELDS)
    submit_label: str = Field("Review & book", max_length=40)


class ProposeTermFormTool(BaseTool):
    name: str = "propose_term_form"
    description: str = (
        "Attach an interactive term-collection card to your NEXT assistant "
        "message to gather missing booking economics. Use immediately before "
        "your reply when build_product reports missing/invalid fields for a "
        "direct booking. Each field has: key (the flat build_product terms key "
        "you will merge, e.g. 'ko_barrier_pct'), label, optional help, a type "
        "('percent'|'number'|'date'|'enum'|'text'), optional choices (<=5 "
        "chips), an optional default (the suggested chip), and required. "
        "Suggest defaults (latest spot for initial_price, today for "
        "trade_start_date) but never assume them. After calling this, phrase a "
        "short prompt in your reply; do NOT list the fields as bullets - the "
        "card renders them."
    )
    args_schema: type[BaseModel] = ProposeTermFormInput

    def _run(
        self,
        title: str,
        fields: list[dict[str, Any]],
        subtitle: str | None = None,
        submit_label: str = "Review & book",
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return {"ok": True, "count": len(fields)}

    async def _arun(
        self,
        title: str,
        fields: list[dict[str, Any]],
        subtitle: str | None = None,
        submit_label: str = "Review & book",
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(
            title, fields, subtitle=subtitle, submit_label=submit_label, config=config
        )


def _normalize_choice(choice: Any) -> dict[str, Any] | None:
    if not isinstance(choice, dict):
        return None
    raw_label = choice.get("label")
    if not isinstance(raw_label, str) or not raw_label.strip():
        return None
    value = choice.get("value")
    if isinstance(value, str):
        value = value.strip()[:_CHOICE_VALUE_MAX]
    elif not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return {"label": raw_label.strip()[:_CHOICE_LABEL_MAX], "value": value}


def _normalize_term_field(field: Any) -> dict[str, Any] | None:
    """Defensive normalizer for raw field dicts read out of tool args.

    Mirrors reply_options._normalize_reply_option: re-checks shape and enforces
    caps because the orchestrator reads raw args recovered from event payloads.
    """
    if not isinstance(field, dict):
        return None
    raw_key = field.get("key")
    raw_label = field.get("label")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    if not isinstance(raw_label, str) or not raw_label.strip():
        return None
    field_type = field.get("type")
    if field_type not in _FIELD_TYPES:
        field_type = "text"
    out: dict[str, Any] = {
        "key": raw_key.strip()[:_KEY_MAX],
        "label": raw_label.strip()[:_LABEL_MAX],
        "type": field_type,
        "required": bool(field.get("required", True)),
    }
    raw_help = field.get("help")
    if isinstance(raw_help, str) and raw_help.strip():
        out["help"] = raw_help.strip()[:_HELP_MAX]
    raw_choices = field.get("choices")
    if isinstance(raw_choices, list):
        choices: list[dict[str, Any]] = []
        for choice in raw_choices:
            norm = _normalize_choice(choice)
            if norm is not None:
                choices.append(norm)
            if len(choices) >= _MAX_CHOICES:
                break
        if choices:
            out["choices"] = choices
    default = _normalize_choice(field.get("default"))
    if default is not None:
        out["default"] = default
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_term_form_tool.py -q`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/term_form/ tests/test_term_form_tool.py
git commit -m "feat(term-form): propose_term_form tool + defensive normalizer"
```

---

## Task 2: Register the tool for personas

**Files:**
- Modify: `backend/app/tools/__init__.py:22` (import) and `:166` (registration, next to `ProposeReplyOptionsTool`)
- Test: `tests/test_term_form_tool.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_term_form_tool.py
def test_propose_term_form_registered_in_quant_agent_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "propose_term_form" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_term_form_tool.py::test_propose_term_form_registered_in_quant_agent_tools -q`
Expected: FAIL — `propose_term_form` not in the set.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/tools/__init__.py`, add the import next to the existing reply-options import (line ~22):

```python
from app.services.term_form.tool import ProposeTermFormTool
```

In the `QUANT_AGENT_TOOLS` list, add directly after the `ProposeReplyOptionsTool` entry (line ~166):

```python
    capability_gated(group=ToolGroup.PAGE_ACTION)(ProposeTermFormTool()),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_term_form_tool.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/__init__.py tests/test_term_form_tool.py
git commit -m "feat(term-form): register propose_term_form in QUANT_AGENT_TOOLS"
```

---

## Task 3: Stream collector fields

**Files:**
- Modify: `backend/app/services/deep_agent/stream_collector.py:34` and `:51` (next to `reply_options` / `reply_options_args`)
- Test: covered by Task 4's capture test (collector is a dataclass; no standalone test needed)

- [ ] **Step 1: Add the fields**

In `StreamCollector`, next to the `reply_options` field (~line 34) add:

```python
    term_form: dict | None = None
```

Next to `reply_options_args` (~line 51) add:

```python
    # Untruncated args for the propose_term_form tool call, keyed by run_id.
    term_form_args: dict[str, dict] = field(default_factory=dict)
```

- [ ] **Step 2: Verify import/collector still constructs**

Run: `python -c "from app.services.deep_agent.stream_collector import StreamCollector; c=StreamCollector(); assert c.term_form is None and c.term_form_args == {}"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/deep_agent/stream_collector.py
git commit -m "feat(term-form): stream collector term_form + term_form_args fields"
```

---

## Task 4: agents.py capture, normalize, persist

**Files:**
- Modify: `backend/app/services/agents.py` — add constants + `_capture_term_form_from_tool_end` + `_term_form_from_result`; capture raw args at `:2689` and `:2768`; call capture beside `_capture_reply_options_from_tool_end`; persist beside `reply_options` (`:1441`, `:1742`); exclude `propose_term_form` from `_completed_tool_names` (`:204`) and the skip set (`:301`).
- Test: `tests/test_term_form_capture.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_term_form_capture.py
from __future__ import annotations

from app.services.agents import (
    _TERM_FORM_MAX_FIELDS,
    _capture_term_form_from_tool_end,
)
from app.services.deep_agent.stream_collector import StreamCollector

_FIELDS = [
    {"key": "initial_price", "label": "Initial fixing S0", "type": "number",
     "default": {"label": "spot 8359.56", "value": 8359.56}},
    {"key": "ko_barrier_pct", "label": "KO barrier", "type": "percent",
     "choices": [{"label": "103%", "value": 103}]},
]


def _payload():
    return {"title": "Finish booking", "subtitle": "pf 6", "fields": _FIELDS,
            "submit_label": "Review & book"}


def test_capture_writes_normalized_term_form():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()

    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )

    assert c.term_form is not None
    assert c.term_form["title"] == "Finish booking"
    assert [f["key"] for f in c.term_form["fields"]] == ["initial_price", "ko_barrier_pct"]
    assert c.term_form["fields"][0]["default"]["value"] == 8359.56


def test_capture_ignores_other_tools():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_reply_options", error_text=None
    )
    assert c.term_form is None


def test_capture_skips_on_tool_error():
    c = StreamCollector()
    c.term_form_args["run-1"] = _payload()
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text="boom"
    )
    assert c.term_form is None


def test_capture_drops_malformed_fields_and_blanks_when_empty():
    c = StreamCollector()
    c.term_form_args["run-1"] = {"title": "t", "fields": ["nope", {"no": "key"}]}
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    assert c.term_form is None  # no valid fields -> not set


def test_capture_caps_fields():
    c = StreamCollector()
    many = [{"key": f"k{i}", "label": f"L{i}", "type": "text"} for i in range(_TERM_FORM_MAX_FIELDS + 3)]
    c.term_form_args["run-1"] = {"title": "t", "fields": many}
    _capture_term_form_from_tool_end(
        c, run_id="run-1", name="propose_term_form", error_text=None
    )
    assert c.term_form is not None
    assert len(c.term_form["fields"]) == _TERM_FORM_MAX_FIELDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_term_form_capture.py -q`
Expected: FAIL — `ImportError: cannot import name '_TERM_FORM_MAX_FIELDS'` / `_capture_term_form_from_tool_end`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/agents.py`, add near the reply-options imports (top, where `_normalize_reply_option` is imported ~line 70):

```python
from .term_form.tool import _MAX_FIELDS as _TERM_FORM_MAX_FIELDS
from .term_form.tool import _normalize_term_field
```

Add this function directly below `_capture_reply_options_from_tool_end` (after ~line 159):

```python
def _capture_term_form_from_tool_end(
    collector: StreamCollector,
    *,
    run_id: str,
    name: str,
    error_text: str | None,
) -> None:
    """If a ``propose_term_form`` tool just ended cleanly, write its normalized
    payload into ``collector.term_form``. Last call wins; validation errors
    leave any prior payload in place. Mirrors the reply-options capture: reads
    ``collector.term_form_args[run_id]`` (populated at on_tool_start before
    truncation)."""
    if name != "propose_term_form" or error_text:
        return
    raw = collector.term_form_args.get(run_id)
    if not isinstance(raw, dict):
        return
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list):
        return
    fields: list[dict] = []
    for field in raw_fields:
        norm = _normalize_term_field(field)
        if norm is not None:
            fields.append(norm)
        if len(fields) >= _TERM_FORM_MAX_FIELDS:
            break
    if not fields:
        return
    title = raw.get("title")
    payload: dict = {
        "title": title.strip()[:120] if isinstance(title, str) and title.strip() else "Complete booking",
        "fields": fields,
    }
    subtitle = raw.get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip():
        payload["subtitle"] = subtitle.strip()[:200]
    submit_label = raw.get("submit_label")
    payload["submit_label"] = (
        submit_label.strip()[:40]
        if isinstance(submit_label, str) and submit_label.strip()
        else "Review & book"
    )
    collector.term_form = payload
```

Add a result-path extractor below `_reply_options_from_result` (after ~line 600). First read the existing `_reply_options_from_result` to match its tool-call-scanning shape, then mirror it:

```python
def _term_form_from_result(result: Any) -> dict | None:
    """Extract a normalized term_form payload from a non-streaming agent result
    by scanning AIMessage tool_calls for the last propose_term_form call.
    Mirrors _reply_options_from_result."""
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return None
    payload: dict | None = None
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") != "propose_term_form":
                continue
            args = tool_call.get("args") or {}
            raw_fields = args.get("fields")
            if not isinstance(raw_fields, list):
                continue
            fields: list[dict] = []
            for field in raw_fields:
                norm = _normalize_term_field(field)
                if norm is not None:
                    fields.append(norm)
                if len(fields) >= _TERM_FORM_MAX_FIELDS:
                    break
            if not fields:
                continue
            title = args.get("title")
            payload = {
                "title": title.strip()[:120] if isinstance(title, str) and title.strip() else "Complete booking",
                "fields": fields,
                "submit_label": (args.get("submit_label") or "Review & book"),
            }
            if isinstance(args.get("subtitle"), str) and args["subtitle"].strip():
                payload["subtitle"] = args["subtitle"].strip()[:200]
    return payload
```

Capture raw args at `on_tool_start` — at BOTH `:2689` and `:2768`, directly below the existing `collector.reply_options_args[run_id] = raw_options` line, add a sibling block. (At those sites the raw tool args are available; reply-options reads `args.get("options")`. Read the surrounding lines and mirror the variable in scope — the raw args dict is the same object reply-options pulls `options` from.)

```python
                    if name == "propose_term_form":
                        collector.term_form_args[run_id] = raw_args
```

(Where `raw_args` is the same raw args dict the reply-options branch reads `options` from. If that dict is bound to a different local name at the site, use that name.)

Call the capture beside the reply-options call. Find every call to `_capture_reply_options_from_tool_end(` and add immediately after it:

```python
        _capture_term_form_from_tool_end(
            collector, run_id=run_id, name=name, error_text=error_text
        )
```

Persist to message meta beside reply_options. At `:1441` and `:1742`, where you see:

```python
                **({"reply_options": reply_options} if reply_options else {}),
```
or
```python
                        {"reply_options": collector.reply_options}
                        if collector.reply_options
                        else {}
```

add the parallel `term_form` entry. For the result path (`:1441`), compute `term_form = _term_form_from_result(result)` next to `reply_options = _reply_options_from_result(result)` (line ~1372) and add `**({"term_form": term_form} if term_form else {})`. For the streaming path (`:1742`), add `**({"term_form": collector.term_form} if collector.term_form else {})`.

Exclude `propose_term_form` from tool-name bookkeeping: at `:204` change `name in {"write_todos", "propose_reply_options"}` to include `"propose_term_form"`; same for the set at `:301`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_term_form_capture.py -q`
Expected: PASS.

- [ ] **Step 5: Regression-check the reply-options suites (shared code paths)**

Run: `python -m pytest tests/test_reply_options_capture.py tests/test_reply_options_collector.py tests/test_reply_options_persistence.py -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agents.py tests/test_term_form_capture.py
git commit -m "feat(term-form): capture + persist term_form to assistant message meta"
```

---

## Task 5: Frontend types

**Files:**
- Modify: `frontend/src/types.ts` (after `ReplyOptionMeta`, ~line 40; and inside `ChatMessage.meta`, ~line 65)

- [ ] **Step 1: Add types**

After `ReplyOptionMeta` (line ~40):

```typescript
export type ChoiceMeta = {
  label: string;
  value: string | number;
};

export type TermFormField = {
  key: string;
  label: string;
  help?: string;
  type: 'percent' | 'number' | 'date' | 'enum' | 'text';
  choices?: ChoiceMeta[];
  default?: ChoiceMeta;
  required?: boolean;
};

export type TermFormMeta = {
  title: string;
  subtitle?: string;
  fields: TermFormField[];
  submit_label?: string;
};
```

Inside `ChatMessage.meta` (next to `reply_options?`, line ~65):

```typescript
    term_form?: TermFormMeta;
```

- [ ] **Step 2: Verify the frontend type-checks**

Run: `npm run --prefix frontend build` (or `npm run --prefix frontend typecheck` if defined)
Expected: no type errors from `types.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(term-form): frontend TermFormMeta types + meta.term_form"
```

---

## Task 6: `termForm.ts` pure helpers (compose + validate)

**Files:**
- Create: `frontend/src/components/termForm.ts`
- Test: `frontend/src/components/termForm.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/termForm.test.ts
import { describe, expect, it } from 'vitest';
import { composeTermFormSubmission, validateTermFormValue } from './termForm';
import type { TermFormField } from '../types';

const fields: TermFormField[] = [
  { key: 'initial_price', label: 'Initial fixing S0', type: 'number' },
  { key: 'ko_barrier_pct', label: 'KO barrier', type: 'percent' },
  { key: 'observation_frequency', label: 'Frequency', type: 'enum',
    choices: [{ label: 'Monthly', value: 'MONTHLY' }] },
  { key: 'trade_start_date', label: 'Start', type: 'date' },
];

describe('validateTermFormValue', () => {
  it('flags required-empty', () => {
    expect(validateTermFormValue(fields[0], '')).toBe('Required');
  });
  it('flags non-numeric number/percent', () => {
    expect(validateTermFormValue(fields[1], 'abc')).toBe('Must be a number');
  });
  it('accepts percent with trailing %', () => {
    expect(validateTermFormValue(fields[1], '103%')).toBeNull();
  });
  it('flags bad date', () => {
    expect(validateTermFormValue(fields[3], '2026/05/31')).toBe('Use YYYY-MM-DD');
  });
  it('accepts ISO date', () => {
    expect(validateTermFormValue(fields[3], '2026-05-31')).toBeNull();
  });
  it('flags enum value not in choices', () => {
    expect(validateTermFormValue(fields[2], 'WEEKLY')).toBe('Pick a listed option');
  });
});

describe('composeTermFormSubmission', () => {
  it('emits a sentence + json block keyed by field key, coercing numbers', () => {
    const msg = composeTermFormSubmission(fields, {
      initial_price: '8359.56',
      ko_barrier_pct: '103%',
      observation_frequency: 'MONTHLY',
      trade_start_date: '2026-05-31',
    });
    expect(msg).toContain('booking terms');
    expect(msg).toContain('```json');
    const json = JSON.parse(msg.split('```json')[1].split('```')[0].trim());
    expect(json).toEqual({
      initial_price: 8359.56,
      ko_barrier_pct: 103,
      observation_frequency: 'MONTHLY',
      trade_start_date: '2026-05-31',
    });
  });
  it('omits blank optional values', () => {
    const msg = composeTermFormSubmission(
      [{ key: 'lockup_months', label: 'Lockup', type: 'number', required: false }],
      { lockup_months: '' },
    );
    const json = JSON.parse(msg.split('```json')[1].split('```')[0].trim());
    expect(json).toEqual({});
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix frontend -- src/components/termForm.test.ts`
Expected: FAIL — cannot resolve `./termForm`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/components/termForm.ts
import type { TermFormField } from '../types';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function toNumber(raw: string): number | null {
  const cleaned = raw.trim().replace(/%$/, '').trim();
  if (cleaned === '') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** Returns an error string, or null when the value is acceptable. */
export function validateTermFormValue(field: TermFormField, raw: string): string | null {
  const value = raw.trim();
  const required = field.required !== false;
  if (!value) return required ? 'Required' : null;
  if (field.type === 'number' || field.type === 'percent') {
    return toNumber(value) === null ? 'Must be a number' : null;
  }
  if (field.type === 'date') {
    return ISO_DATE.test(value) ? null : 'Use YYYY-MM-DD';
  }
  if (field.type === 'enum') {
    const allowed = (field.choices ?? []).map((c) => String(c.value));
    return allowed.includes(value) ? null : 'Pick a listed option';
  }
  return null;
}

/** Composes the user message sent on submit: a readable sentence plus a
 * json block keyed by each field's build_product terms key. The agent parses
 * the json and merges it into terms before re-validating via build_product. */
export function composeTermFormSubmission(
  fields: TermFormField[],
  values: Record<string, string>,
): string {
  const out: Record<string, string | number> = {};
  for (const field of fields) {
    const raw = (values[field.key] ?? '').trim();
    if (!raw) continue;
    if (field.type === 'number' || field.type === 'percent') {
      const n = toNumber(raw);
      if (n !== null) out[field.key] = n;
    } else {
      out[field.key] = raw;
    }
  }
  const json = JSON.stringify(out, null, 2);
  return `Here are the booking terms:\n\n\`\`\`json\n${json}\n\`\`\``;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix frontend -- src/components/termForm.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/termForm.ts frontend/src/components/termForm.test.ts
git commit -m "feat(term-form): compose + validate helpers"
```

---

## Task 7: `TermForm.tsx` component (layout A · stacked)

**Files:**
- Create: `frontend/src/components/TermForm.tsx`
- Test: `frontend/src/components/TermForm.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/TermForm.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TermForm } from './TermForm';
import type { TermFormMeta } from '../types';

const form: TermFormMeta = {
  title: 'Finish booking',
  subtitle: 'Portfolio 6',
  submit_label: 'Review & book',
  fields: [
    { key: 'initial_price', label: 'Initial fixing S0', type: 'number',
      default: { label: 'spot 8359.56', value: 8359.56 } },
    { key: 'observation_frequency', label: 'Frequency', type: 'enum',
      choices: [{ label: 'Monthly', value: 'MONTHLY' }, { label: 'Quarterly', value: 'QUARTERLY' }] },
  ],
};

describe('TermForm', () => {
  it('renders title, fields, and a default chip preselected', () => {
    render(<TermForm form={form} onSubmit={vi.fn()} />);
    expect(screen.getByText('Finish booking')).toBeInTheDocument();
    expect(screen.getByText('Initial fixing S0')).toBeInTheDocument();
    // default chip applied -> the S0 input shows the default value
    expect(screen.getByLabelText('Initial fixing S0')).toHaveValue(8359.56);
  });

  it('blocks submit and shows an error when a required field is empty', () => {
    const onSubmit = vi.fn();
    render(<TermForm form={form} onSubmit={onSubmit} />);
    // frequency has no default -> required empty
    fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Required')).toBeInTheDocument();
  });

  it('submits a composed string once all fields are valid', () => {
    const onSubmit = vi.fn();
    render(<TermForm form={form} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: /monthly/i }));
    fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const msg = onSubmit.mock.calls[0][0] as string;
    expect(msg).toContain('```json');
    expect(msg).toContain('"observation_frequency": "MONTHLY"');
    expect(msg).toContain('"initial_price": 8359.56');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix frontend -- src/components/TermForm.test.tsx`
Expected: FAIL — cannot resolve `./TermForm`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/components/TermForm.tsx
import { useMemo, useState } from 'react';
import type { ChoiceMeta, TermFormField, TermFormMeta } from '../types';
import { composeTermFormSubmission, validateTermFormValue } from './termForm';
import './TermForm.css';

type Props = {
  form: TermFormMeta;
  onSubmit: (message: string) => void;
};

function initialValues(fields: TermFormField[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of fields) {
    out[field.key] = field.default ? String(field.default.value) : '';
  }
  return out;
}

export function TermForm({ form, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(form.fields));
  const [showErrors, setShowErrors] = useState(false);

  const errors = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const field of form.fields) out[field.key] = validateTermFormValue(field, values[field.key] ?? '');
    return out;
  }, [form.fields, values]);

  const setValue = (key: string, value: string) =>
    setValues((prev) => ({ ...prev, [key]: value }));

  const filledCount = form.fields.filter((f) => !errors[f.key]).length;
  const chipValue = (choice: ChoiceMeta) => String(choice.value);

  const handleSubmit = () => {
    const hasError = form.fields.some((f) => errors[f.key]);
    if (hasError) {
      setShowErrors(true);
      return;
    }
    onSubmit(composeTermFormSubmission(form.fields, values));
  };

  return (
    <section className="wl-term-form" aria-label={form.title}>
      <header className="wl-term-form__head">
        <p className="wl-term-form__title">{form.title}</p>
        {form.subtitle && <p className="wl-term-form__sub">{form.subtitle}</p>}
        <span className="wl-term-form__progress">{filledCount} of {form.fields.length} terms set</span>
      </header>

      {form.fields.map((field) => {
        const inputId = `tf-${field.key}`;
        const error = showErrors ? errors[field.key] : null;
        return (
          <div className="wl-term-form__row" key={field.key}>
            <label className="wl-term-form__label" htmlFor={inputId}>
              {field.label}
              {field.help && <span className="wl-term-form__help"> — {field.help}</span>}
            </label>
            <div className="wl-term-form__controls">
              {(field.choices ?? []).map((choice) => {
                const selected = values[field.key] === chipValue(choice);
                const isDefault = field.default && chipValue(field.default) === chipValue(choice);
                return (
                  <button
                    type="button"
                    key={choice.label}
                    className={
                      'wl-term-form__chip'
                      + (selected ? ' is-selected' : '')
                      + (isDefault ? ' is-default' : '')
                    }
                    onClick={() => setValue(field.key, chipValue(choice))}
                  >
                    {choice.label}
                  </button>
                );
              })}
              {field.type !== 'enum' && (
                <input
                  id={inputId}
                  className="wl-term-form__input"
                  type={field.type === 'date' ? 'date' : field.type === 'number' || field.type === 'percent' ? 'number' : 'text'}
                  value={values[field.key] ?? ''}
                  onChange={(e) => setValue(field.key, e.target.value)}
                  placeholder={field.type === 'percent' ? '%' : undefined}
                  aria-label={field.label}
                />
              )}
            </div>
            {error && <p className="wl-term-form__error">{error}</p>}
          </div>
        );
      })}

      <button type="button" className="wl-term-form__submit" onClick={handleSubmit}>
        {form.submit_label ?? 'Review & book'}
      </button>
    </section>
  );
}
```

```css
/* frontend/src/components/TermForm.css */
.wl-term-form { border: 1px solid var(--wl-border, #2a2f3a); border-radius: 12px; padding: 14px 16px; margin-top: 10px; max-width: 560px; }
.wl-term-form__title { font-weight: 600; margin: 0; }
.wl-term-form__sub { font-size: 12px; opacity: 0.75; margin: 2px 0 0; }
.wl-term-form__progress { display: inline-block; font-size: 11px; opacity: 0.8; border: 1px solid var(--wl-border, #2a2f3a); border-radius: 999px; padding: 2px 9px; margin: 8px 0; }
.wl-term-form__row { padding: 9px 0; border-top: 1px solid var(--wl-border-faint, #1d2330); }
.wl-term-form__label { display: block; font-weight: 600; margin-bottom: 5px; font-size: 13px; }
.wl-term-form__help { font-weight: 400; opacity: 0.65; }
.wl-term-form__controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.wl-term-form__chip { font-size: 12px; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--wl-border, #39404e); background: transparent; cursor: pointer; }
.wl-term-form__chip.is-default { border-style: dashed; }
.wl-term-form__chip.is-selected { background: #2563eb; border-color: #2563eb; color: #fff; }
.wl-term-form__input { font-size: 12px; padding: 4px 8px; border-radius: 8px; border: 1px solid var(--wl-border, #39404e); background: transparent; color: inherit; }
.wl-term-form__error { color: #f87171; font-size: 11px; margin: 4px 0 0; }
.wl-term-form__submit { margin-top: 10px; background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix frontend -- src/components/TermForm.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TermForm.tsx frontend/src/components/TermForm.css frontend/src/components/TermForm.test.tsx
git commit -m "feat(term-form): stacked-list TermForm card component"
```

---

## Task 8: Render `TermForm` in `ChatBubble`

**Files:**
- Modify: `frontend/src/components/ChatBubble.tsx` (import; render block after the `ReplyOptionButtons` block ~line 161)
- Test: `frontend/src/components/ChatBubble.test.tsx` (append a case)

- [ ] **Step 1: Write the failing test**

```tsx
// append to frontend/src/components/ChatBubble.test.tsx
it('renders a term-collection card from meta.term_form and submits a composed string', () => {
  const onSelectReplyOption = vi.fn();
  render(
    <ChatBubble
      message={{
        id: 77,
        role: 'assistant',
        character: 'trader',
        content: 'Fill in the missing terms to book.',
        meta: {
          term_form: {
            title: 'Finish booking',
            submit_label: 'Review & book',
            fields: [
              { key: 'observation_frequency', label: 'Frequency', type: 'enum',
                choices: [{ label: 'Monthly', value: 'MONTHLY' }] },
            ],
          },
        },
      }}
      viewMode="chat"
      onConfirmAction={vi.fn()}
      onDismissAction={vi.fn()}
      onSelectReplyOption={onSelectReplyOption}
      replyOptionsEnabled
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /monthly/i }));
  fireEvent.click(screen.getByRole('button', { name: /review & book/i }));
  expect(onSelectReplyOption).toHaveBeenCalledTimes(1);
  expect(onSelectReplyOption.mock.calls[0][0]).toBe(77);
  expect(onSelectReplyOption.mock.calls[0][1]).toContain('"observation_frequency": "MONTHLY"');
});
```

(Ensure `fireEvent` is imported in this test file; add it to the existing `@testing-library/react` import if missing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix frontend -- src/components/ChatBubble.test.tsx`
Expected: FAIL — no term-form rendered; `onSelectReplyOption` not called.

- [ ] **Step 3: Write minimal implementation**

Add the import (next to the `replyOptions` import, ~line 26):

```tsx
import { TermForm } from './TermForm';
import type { TermFormMeta } from '../types';
```

Compute the payload near the other meta reads (after `assets`, ~line 73):

```tsx
  const termForm: TermFormMeta | null =
    variant === 'assistant'
    && !isStreaming
    && pendingActions.length === 0
    && meta.term_form
    && Array.isArray((meta.term_form as TermFormMeta).fields)
    && (meta.term_form as TermFormMeta).fields.length > 0
      ? (meta.term_form as TermFormMeta)
      : null;
```

Render it directly after the `showReplyOptions && ...` `<ReplyOptionButtons>` block (~line 161), reusing the existing string-send prop:

```tsx
        {termForm && onSelectReplyOption && (
          <TermForm
            form={termForm}
            onSubmit={(message) => onSelectReplyOption(message.id, message)}
          />
        )}
```

Wait — `message.id` shadowing: inside the bubble, the message prop is `message`. Use `message.id`:

```tsx
        {termForm && onSelectReplyOption && (
          <TermForm
            form={termForm}
            onSubmit={(composed) => onSelectReplyOption(message.id, composed)}
          />
        )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix frontend -- src/components/ChatBubble.test.tsx`
Expected: PASS (new case + existing cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatBubble.tsx frontend/src/components/ChatBubble.test.tsx
git commit -m "feat(term-form): render TermForm card in ChatBubble"
```

---

## Task 9: Skill guidance (build-product wizard procedure)

**Files:**
- Modify: `backend/app/skills/workflows/products/build-product/SKILL.md`
- Modify: `backend/app/skills/workflows/positions/book-position/SKILL.md`
- Test: `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py`

- [ ] **Step 1: Run the catalog tests first (capture the green baseline)**

Run: `python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py -q`
Expected: PASS. Note any test that asserts on `build-product`'s exact `description` string — if one exists, it must be updated in lockstep with Step 2.

- [ ] **Step 2: Replace `build-product/SKILL.md` with the revised body**

Overwrite `backend/app/skills/workflows/products/build-product/SKILL.md` with the full body in spec §9 (the fenced ```markdown block titled "full `build-product` SKILL.md sketch"). It keeps the same `name: build-product` and `domain: products`, broadens the `description` to mention the term-collection card, and rewrites the Procedure to call `propose_term_form` on missing/invalid and loop on `build_product`.

- [ ] **Step 3: Add the book-position reference**

In `backend/app/skills/workflows/positions/book-position/SKILL.md`, under `## Procedure` step 2 ("Validate the product family ... required terms are present."), append one sentence:

```
   When terms are incomplete, complete them via the term-collection card in
   `build-product` (it calls `propose_term_form` and loops on `build_product`)
   before composing the confirmation summary.
```

- [ ] **Step 4: Re-run the catalog tests**

Run: `python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py -q`
Expected: PASS. If a description-equality assertion broke, update that expected string to match the new `build-product` description, then re-run.

- [ ] **Step 5: Commit**

```bash
git add backend/app/skills/workflows/products/build-product/SKILL.md backend/app/skills/workflows/positions/book-position/SKILL.md tests/
git commit -m "feat(term-form): build-product wizard procedure via propose_term_form"
```

---

## Task 10: Full regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Backend agent + tools + skills suites**

Run:
```bash
python -m pytest tests/test_term_form_tool.py tests/test_term_form_capture.py \
  tests/test_reply_options_capture.py tests/test_reply_options_collector.py \
  tests/test_reply_options_persistence.py tests/test_reply_options_tool.py \
  tests/test_personas.py tests/test_product_booking.py \
  tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py \
  -q --deselect "tests/test_personas.py::test_orchestrator_can_enable_quickjs_code_interpreter_middleware"
```
Expected: PASS. (The deselected test requires the optional `langchain_quickjs` package, which is absent in this environment — pre-existing, unrelated.)

- [ ] **Step 2: Frontend suites**

Run: `npm test --prefix frontend -- src/components/termForm.test.ts src/components/TermForm.test.tsx src/components/ChatBubble.test.tsx src/components/MessageList.test.tsx`
Expected: PASS.

- [ ] **Step 3: Final commit (if any test-fixture tweaks were needed)**

```bash
git add -A
git commit -m "test(term-form): regression sweep green" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage (§ → task):**
- §3.1 guide-then-book / no persistence → no schema task exists by design (✓ nothing persists pre-completion).
- §3.2 rich single-round → Task 7 renders all missing fields in one card.
- §3.3 mini-form affordance → Tasks 5–8 (types, helpers, component, ChatBubble).
- §3.4 client checks + agent validates → Task 6 (`validateTermFormValue`), Task 9 (skill loops on `build_product`); submit sends a string (Task 6/8), no new endpoint (✓).
- §3.5 LLM-authored card → Task 1 tool validates shape only; Task 9 skill authors fields.
- §3.6 new `propose_term_form` tool → Task 1–2.
- §3.7 layout A stacked → Task 7.
- §4 safety invariant → Task 9 procedure re-validates via `build_product`; no booking from this path.
- §5 plumbing mirror → Tasks 3–4 mirror reply-options capture/persist; Task 8 reuses send path.
- §6 tool contract → Task 1 schema + caps.
- §8 families scope → Task 9 ships snowball conventions; framework family-agnostic (keys off agent-authored fields).
- §11 testing → Tasks 1,4,6,7,8 tests; Task 10 sweep.

**Deviation from spec (intentional, discovered in research):** §7 proposed `meta.term_form_response` (structured) on the *user* message. The actual reply-options send path carries only a **string** (`onSelectReplyOption(id, string)`), so Task 6/8 send a `sentence + ```json{...}``` ` string instead. This resolves the spec's §12 "verify submit payload transport" risk and is simpler. `FieldSpec.key` is the flat `build_product` terms key so the json round-trips cleanly.

**Placeholder scan:** none — every code step has complete code. The only "find the call site" instruction (Task 4 `_capture_reply_options_from_tool_end` callers, on_tool_start `raw_args` binding) is a parallel-to-existing-code instruction with exact anchors (`:2689`, `:2768`, `:204`, `:301`, `:1372`, `:1441`, `:1742`); the implementer must read those lines and mirror the in-scope variable name.

**Type consistency:** `FieldSpec`/`TermFormField` keys (`key`,`label`,`help`,`type`,`choices`,`default`,`required`) match across backend tool, normalizer, frontend types, helpers, and component. `ChoiceSpec`/`ChoiceMeta` = `{label, value}` throughout. The persisted payload shape (`{title, subtitle?, fields, submit_label}`) is identical in `_capture_term_form_from_tool_end`, `_term_form_from_result`, `TermFormMeta`, and `TermForm`.
```
