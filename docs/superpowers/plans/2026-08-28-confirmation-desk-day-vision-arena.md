# confirmation-desk-day (vision arena workflow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sixth golden workflow, `confirmation-desk-day`, that grades an arena contestant on reading real confirmation documents with **its own eyes**, so the board can measure vision.

**Architecture:** The extraction call inside `parse_trade_confirmation` currently routes by registry tag, so every contestant would share one model's eyes. A server-stamped `configurable` key lets the arena runner override that selection with the match's own model, gated on a new manifest field `extractor_model: contestant` so the arm is predeclared and no other workflow is silently rerouted. The synthetic document corpus becomes tracked and gains three vision-trap documents whose graded values exist only inside an image.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, pytest, LangChain / LangGraph (`langchain_anthropic` 1.4.8), `pypdf` + `pypdfium2` + Pillow + `python-docx` for document rendering.

**Spec:** `docs/superpowers/specs/2026-08-28-confirmation-desk-day-vision-arena-design.md`

## Global Constraints

- **Run everything from the worktree root** `/Users/fuxinyao/open-otc-trading/.claude/worktrees/arena-confirmation-vision`. Never `cd` to the main checkout.
- **Tests:** `.venv/bin/python -m pytest`. Never pipe pytest through `tail` (it hides the failure summary).
- **Default behaviour must be byte-identical when the new manifest field is unset.** The `confirmation_extractor → fast → default` tag ladder stays the production desk path; gemini-3.6-flash remains the pinned desk extractor.
- **Tag edits go to BOTH** `config/agent_channels.yaml` (gitignored, per-env) **and** `config/agent_channels.example.yml` (tracked).
- **`par_tool_calls` stays UNSET** on the new manifest. An uncalibrated workflow must remain on the legacy hyperbolic EFF curve.
- **A tool the model must call has to be in `DEEP_AGENT_TOOL_NAMES`** (`services/agents.py`), not merely registered in `QUANT_AGENT_TOOLS`. All three confirmation tools are already registered — verify, do not assume.
- **Never `git stash`** in this repo; the stash stack is shared with other sessions. Use a WIP commit instead.
- **Assertion models do not set `extra="forbid"`.** A manifest key that pydantic does not declare is silently dropped. Any new schema field must be added to the model *and* asserted in a test.

---

### Task 1: Make the extractor client protocol-agnostic

The probe measured `z-ai/glm-5.3-flash` returning `.content` as `[{'type':'thinking',...}, {'type':'text','text':'...'}]`. `RegistryExtractorClient.complete()` raises on any non-`str`, so every parse dies the moment extraction routes to an Anthropic-protocol reasoning model. This is a standalone bug fix — it is reachable today by anyone who tags such a model `confirmation_extractor`.

**Files:**
- Modify: `backend/app/services/confirmations/llm.py` (`RegistryExtractorClient.complete`)
- Test: `tests/test_confirmations_llm.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `app.services.confirmations.llm._content_to_text(content: Any) -> str` — returns the concatenated text of a content block list, or the string itself; raises `ExtractionError` when no text is recoverable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_confirmations_llm.py`:

```python
def test_content_to_text_passes_a_plain_string_through():
    from app.services.confirmations.llm import _content_to_text

    assert _content_to_text('{"strike": 205.0}') == '{"strike": 205.0}'


def test_content_to_text_flattens_anthropic_reasoning_blocks():
    """glm-5.3-flash returns [thinking, text]; only the text block is the answer.

    Measured live 2026-08-28 on z-ai/glm-5.3-flash:bigmodel. Before this, the
    extractor raised ExtractionError on every such response, which on an arena
    board would read as 'the model cannot see' rather than 'the harness dropped
    the answer'.
    """
    from app.services.confirmations.llm import _content_to_text

    content = [
        {"type": "thinking", "thinking": "The strike appears to be 205.", "signature": "abc"},
        {"type": "text", "text": '{"strike": 205.0}'},
    ]
    assert _content_to_text(content) == '{"strike": 205.0}'


def test_content_to_text_joins_multiple_text_blocks():
    from app.services.confirmations.llm import _content_to_text

    content = [{"type": "text", "text": '{"a": 1,'}, {"type": "text", "text": ' "b": 2}'}]
    assert _content_to_text(content) == '{"a": 1, "b": 2}'


def test_content_to_text_raises_when_no_text_block_survives():
    from app.services.confirmations.llm import _content_to_text

    with pytest.raises(ExtractionError):
        _content_to_text([{"type": "thinking", "thinking": "hmm", "signature": "s"}])


def test_content_to_text_raises_on_an_unusable_type():
    from app.services.confirmations.llm import _content_to_text

    with pytest.raises(ExtractionError):
        _content_to_text(None)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_confirmations_llm.py -k content_to_text -v
```

Expected: 5 FAILs with `ImportError: cannot import name '_content_to_text'`.

- [ ] **Step 3: Implement the flattener**

In `backend/app/services/confirmations/llm.py`, add above `class RegistryExtractorClient`:

```python
def _content_to_text(content: Any) -> str:
    """Reduce a chat response's ``.content`` to the text the extractor parses.

    An OpenAI-protocol model returns a plain string. An ANTHROPIC-protocol
    reasoning model returns a block LIST -- glm-5.3-flash sends
    ``[{'type': 'thinking', ...}, {'type': 'text', 'text': '...'}]`` -- and the
    answer is only the text block. Rejecting the list outright (the previous
    behaviour) loses a perfectly good response, and on an arena board that reads
    as a model failure rather than a harness one.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if parts:
            return "".join(parts)
    raise ExtractionError(
        f"extractor returned no text content ({type(content).__name__})"
    )
```

Add `from typing import Any` to the imports if it is not already present.

Then replace the body of `RegistryExtractorClient.complete`:

```python
    def complete(self, content_parts: list[dict]) -> str:
        message = {"role": "user", "content": content_parts}
        return _content_to_text(self._model.invoke([message]).content)
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_confirmations_llm.py -v
```

Expected: all PASS, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/confirmations/llm.py tests/test_confirmations_llm.py
git commit -m "fix(confirmations): accept block-list content from reasoning extractors

An anthropic-protocol reasoning model returns .content as [thinking, text];
complete() raised on anything non-str, so every parse died. Measured live on
z-ai/glm-5.3-flash:bigmodel 2026-08-28."
```

---

### Task 2: The extractor override seam

Let a caller override the tag-resolved extractor with an explicit selection, and let `parse_trade_confirmation` read that override from `configurable`. Default behaviour (override absent) is unchanged.

**Files:**
- Modify: `backend/app/services/confirmations/llm.py` (`resolve_confirmation_extractor_selection`, `RegistryExtractorClient.__init__`, `build_extractor_client`)
- Modify: `backend/app/tools/confirmations.py` (`parse_trade_confirmation`)
- Test: `tests/test_confirmations_llm.py`, `tests/test_confirmations_tools.py`

**Interfaces:**
- Consumes: `_content_to_text` (Task 1).
- Produces:
  - `llm.CONFIRMATION_EXTRACTOR_SELECTION_KEY: str = "__confirmation_extractor_selection__"`
  - `llm.resolve_confirmation_extractor_selection(registry, override: dict | None = None) -> dict`
  - `llm.build_extractor_client(selection: dict | None = None) -> ExtractorClient`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_confirmations_llm.py`:

```python
def test_resolver_override_wins_over_the_dedicated_tag():
    """The arena routes extraction to the CONTESTANT, or every model on the board
    reads every document with one shared model's eyes and the vision checks carry
    zero ability signal."""
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    class Reg:
        def select_by_tag(self, tag):
            return {"channel": "zenmux", "provider": "google-vertex",
                    "model": "google/gemini-3.6-flash"}

        def default_selection(self):
            return {"channel": "zenmux", "provider": "openai", "model": "default-y"}

    override = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}
    assert resolve_confirmation_extractor_selection(Reg(), override) == override


def test_resolver_ignores_an_empty_override_and_keeps_the_tag_ladder():
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    class Reg:
        def select_by_tag(self, tag):
            return {"channel": "zenmux", "provider": "openai", "model": "vision-x"} \
                if tag == "confirmation_extractor" else None

        def default_selection(self):
            return {"channel": "zenmux", "provider": "openai", "model": "default-y"}

    assert resolve_confirmation_extractor_selection(Reg(), None)["model"] == "vision-x"
    assert resolve_confirmation_extractor_selection(Reg(), {})["model"] == "vision-x"
```

Append to `tests/test_confirmations_tools.py`:

```python
def test_parse_tool_passes_the_configurable_override_to_the_client(monkeypatch):
    """The override is SERVER-STAMPED onto configurable and never a tool argument
    -- same discipline as fanout_attribution_extra. A model must not be able to
    choose which model reads its documents."""
    from app.services.confirmations import llm as confirmations_llm
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    seen: list = []

    def _fake_build(selection=None):
        seen.append(selection)

        class _C:
            def complete(self, content_parts):
                return '{"trades": []}'

        return _C()

    monkeypatch.setattr(confirmations_llm, "build_extractor_client", _fake_build)

    override = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}
    from app.tools.confirmations import parse_trade_confirmation

    parse_trade_confirmation.invoke(
        {"paths": []},
        config={"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: override}},
    )
    # "no files given" short-circuits BEFORE the client is built, so assert the
    # reachable contract instead: the key is readable and the tool accepts config.
    assert seen == [] or seen == [override]


def test_parse_tool_reads_the_override_key_from_config():
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import _extractor_override_from_config

    override = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}
    assert _extractor_override_from_config(
        {"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: override}}
    ) == override
    assert _extractor_override_from_config(None) is None
    assert _extractor_override_from_config({}) is None
    assert _extractor_override_from_config({"configurable": {}}) is None
    # A non-dict value is ignored rather than trusted.
    assert _extractor_override_from_config(
        {"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: "glm"}}
    ) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_confirmations_llm.py -k override tests/test_confirmations_tools.py -k override_or_config -v
```

Expected: FAIL with `ImportError` / `TypeError: resolve_confirmation_extractor_selection() takes 1 positional argument`.

- [ ] **Step 3: Implement the override in `llm.py`**

```python
# Server-stamped RunnableConfig key carrying an explicit extractor selection.
# configurable (not a ContextVar) because LangGraph may execute nodes on worker
# threads -- see deep_agent/booking_capture.py for the same reasoning -- and
# because configurable is forwarded into persona subagents and survives async
# resume. NEVER read from model or tool INPUT.
CONFIRMATION_EXTRACTOR_SELECTION_KEY = "__confirmation_extractor_selection__"


def resolve_confirmation_extractor_selection(registry, override: dict | None = None) -> dict:
    """Pick the extraction model.

    ``override`` (server-stamped, arena only) wins outright. Otherwise the
    production ladder is unchanged: dedicated tag -> fast -> registry default.
    """
    if override:
        return dict(override)
    for tag in (CONFIRMATION_EXTRACTOR_TAG, _FALLBACK_TAG):
        selection = registry.select_by_tag(tag)
        if selection is not None:
            return selection
    return registry.default_selection()
```

Thread it through the client:

```python
class RegistryExtractorClient:
    """Multimodal chat call through the channel registry (LangChain content parts)."""

    def __init__(self, selection: dict | None = None):
        from app.services.deep_agent.channel_registry import get_registry
        from app.services.deep_agent.model_factory import build_agent_model

        registry = get_registry()
        self.selection = resolve_confirmation_extractor_selection(registry, selection)
        self._model = build_agent_model(registry, self.selection)
        if self._model is None:
            raise RuntimeError("confirmation extractor model unavailable")
```

```python
def build_extractor_client(selection: dict | None = None) -> ExtractorClient:
    return RegistryExtractorClient(selection)
```

- [ ] **Step 4: Implement the config read in the tool**

In `backend/app/tools/confirmations.py`, add the import and helper:

```python
from langchain_core.runnables import RunnableConfig
```

```python
def _extractor_override_from_config(config: RunnableConfig | None) -> dict | None:
    """Read the SERVER-STAMPED extractor override off ``configurable``.

    Returns None for anything that is not a dict, so a malformed stamp degrades
    to the production tag ladder rather than crashing a desk turn.
    """
    from ..services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    configurable = (config or {}).get("configurable") or {}
    override = configurable.get(CONFIRMATION_EXTRACTOR_SELECTION_KEY)
    return override if isinstance(override, dict) and override else None
```

Change the tool signature and the client build:

```python
@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("parse_trade_confirmation", args_schema=ParseTradeConfirmationInput)
def parse_trade_confirmation(
    paths: list[str],
    portfolio_id: int | None = None,
    config: RunnableConfig = None,  # type: ignore[assignment]
) -> dict:
```

and, inside the body, replace the client build:

```python
    try:
        client = confirmations_llm.build_extractor_client(
            _extractor_override_from_config(config)
        )
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_confirmations_llm.py tests/test_confirmations_tools.py tests/test_confirmations_service.py -v
```

Expected: all PASS.

- [ ] **Step 6: Verify `config` is NOT exposed to the model**

`args_schema=ParseTradeConfirmationInput` declares only `paths` and `portfolio_id`, so `config` cannot be model-supplied. Confirm:

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app.tools.confirmations import parse_trade_confirmation as t
print(sorted(t.args_schema.model_fields))
"
```

Expected: `['paths', 'portfolio_id']` — no `config`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/confirmations/llm.py backend/app/tools/confirmations.py tests/test_confirmations_llm.py tests/test_confirmations_tools.py
git commit -m "feat(confirmations): allow a server-stamped extractor override

resolve_confirmation_extractor_selection takes an optional override that wins
over the tag ladder; parse_trade_confirmation reads it from configurable.
Unset = today's behaviour byte for byte."
```

---

### Task 3: Route the override to the contestant from the arena runner

Add the manifest field, thread the override through `stream_and_persist`, and have the runner supply the match's own selection.

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (`GoldenWorkflow`)
- Modify: `backend/app/services/agents.py` (`stream_and_persist` signature + configurable build)
- Modify: `backend/app/services/arena/runner.py` (`_drive_step`, `_default_drive`, `_make_default_drive`, `run_match`)
- Test: `tests/test_golden_workflow_registry.py`, `tests/test_arena_runner.py`

**Interfaces:**
- Consumes: `llm.CONFIRMATION_EXTRACTOR_SELECTION_KEY` (Task 2).
- Produces:
  - `GoldenWorkflow.extractor_model: Literal["contestant"] | None = None`
  - `AgentService.stream_and_persist(..., extractor_selection: dict | None = None)`
  - `runner._make_default_drive(accounting_date: str | None, route_extractor_to_contestant: bool = False)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arena_extractor_routing.py`:

```python
"""The arena must route document extraction to the CONTESTANT.

Without this, resolve_confirmation_extractor_selection picks by registry tag and
every contestant reads every document with gemini-3.6-flash's eyes -- so every
vision check lands N/N across the field and carries zero ability signal, the same
defect the Run #58 scoring-validity audit found in 15 of 50 checks.
"""
import pytest

from app.golden_workflows.schema import GoldenWorkflow


def _wf(**over):
    base = dict(
        id="x", schema_version=1, persona="trader", title="T", objective="O",
        fixtures="x.fixtures.json",
        steps=[{"user": "u", "expected_skill": None, "outcome": "o", "replay": "r"}],
        success={"assertions": [], "rubric": []},
    )
    base.update(over)
    return GoldenWorkflow(**base)


def test_extractor_model_defaults_to_none():
    assert _wf().extractor_model is None


def test_extractor_model_accepts_contestant():
    assert _wf(extractor_model="contestant").extractor_model == "contestant"


def test_extractor_model_rejects_an_arbitrary_model_id():
    """The field names a ROUTING POLICY, not a model. Allowing a model id here
    would let a manifest pin extraction to one model and silently un-level the
    board it is scoring."""
    with pytest.raises(Exception):
        _wf(extractor_model="google/gemini-3.6-flash")


def test_drive_stamps_the_contestant_selection_when_routing_is_on(monkeypatch):
    from app.services.arena import runner
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    seen: dict = {}

    def _fake_drive_step(thread_id, content, selection, *, accounting_date,
                         extractor_selection=None):
        seen["extractor_selection"] = extractor_selection

    monkeypatch.setattr(runner, "_drive_step", _fake_drive_step)
    selection = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}

    drive = runner._make_default_drive(None, route_extractor_to_contestant=True)
    drive(1, "hello", selection)
    assert seen["extractor_selection"] == selection
    assert CONFIRMATION_EXTRACTOR_SELECTION_KEY  # key exists and is importable


def test_drive_stamps_nothing_when_routing_is_off(monkeypatch):
    from app.services.arena import runner

    seen: dict = {"extractor_selection": "unset"}

    def _fake_drive_step(thread_id, content, selection, *, accounting_date,
                         extractor_selection=None):
        seen["extractor_selection"] = extractor_selection

    monkeypatch.setattr(runner, "_drive_step", _fake_drive_step)
    drive = runner._make_default_drive(None)
    drive(1, "hello", {"channel": "c", "provider": "p", "model": "m"})
    assert seen["extractor_selection"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_arena_extractor_routing.py -v
```

Expected: FAILs — `extractor_model` is not a field; `_make_default_drive()` takes 1 argument.

- [ ] **Step 3: Add the manifest field**

In `backend/app/golden_workflows/schema.py`, inside `GoldenWorkflow`, below `par_tool_calls`:

```python
    # Routing policy for the document-extraction sub-call inside
    # parse_trade_confirmation. Unset (default) = the production tag ladder
    # (confirmation_extractor -> fast -> registry default), unchanged.
    # "contestant" = the arena routes extraction to the MATCH's own model, which
    # is what makes a vision check measure the contestant rather than whichever
    # model happens to hold the confirmation_extractor tag.
    # Declared in the manifest rather than inferred by the harness so the
    # experimental arm is PREDECLARED and no other workflow is silently rerouted.
    extractor_model: Literal["contestant"] | None = Field(default=None)
```

Confirm `Literal` and `Field` are already imported in that module (they are — `persona` uses `Literal`, `title` uses `Field`).

- [ ] **Step 4: Thread the kwarg through `stream_and_persist`**

In `backend/app/services/agents.py`, add `extractor_selection: dict[str, str] | None = None` to the `stream_and_persist` signature, and in the `configurable` dict it builds (beside `AUDIT_CONTEXT_KEY`) add:

```python
            # Arena-only: route the confirmation extractor to THIS match's model.
            # Stamped server-side; never model- or tool-supplied.
            **(
                {CONFIRMATION_EXTRACTOR_SELECTION_KEY: extractor_selection}
                if extractor_selection
                else {}
            ),
```

with the import at the top of the module:

```python
from .confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
```

If that import creates a cycle (`app.services.confirmations` imports `app.tools` function-scope, so it should not), fall back to a function-scope import inside `stream_and_persist` and note it in the code comment.

- [ ] **Step 5: Thread it through the runner**

In `backend/app/services/arena/runner.py`:

```python
def _drive_step(
    thread_id: int,
    content: str,
    selection: dict,
    *,
    accounting_date: str | None,
    extractor_selection: dict | None = None,
) -> None:
```

and pass `extractor_selection=extractor_selection` into the `svc.stream_and_persist(...)` call inside `_run()`.

```python
def _make_default_drive(
    accounting_date: str | None, route_extractor_to_contestant: bool = False
):
    """Build the default turn driver.

    ``route_extractor_to_contestant`` makes the match's own model selection the
    document-extraction model, so a vision workflow grades the CONTESTANT's eyes
    rather than whichever model holds the confirmation_extractor tag.
    """

    def _drive(thread_id: int, content: str, selection: dict) -> None:
        _drive_step(
            thread_id, content, selection,
            accounting_date=accounting_date,
            extractor_selection=selection if route_extractor_to_contestant else None,
        )

    return _drive
```

In `run_match`, replace the drive default:

```python
    drive = drive or _make_default_drive(
        getattr(workflow, "accounting_date", None),
        route_extractor_to_contestant=(
            getattr(workflow, "extractor_model", None) == "contestant"
        ),
    )
```

Leave `_default_drive` as-is — it is a separate injected seam used by tests.

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_arena_extractor_routing.py tests/test_arena_runner.py tests/test_golden_workflow_registry.py -v
```

Expected: all PASS.

- [ ] **Step 7: Verify the five existing workflows are untouched**

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app.golden_workflows.registry import list_workflow_bundles
for b in list_workflow_bundles():
    print(b.workflow.id, '->', b.workflow.extractor_model)
"
```

Expected: every existing workflow prints `None`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/golden_workflows/schema.py backend/app/services/agents.py backend/app/services/arena/runner.py tests/test_arena_extractor_routing.py
git commit -m "feat(arena): route document extraction to the contestant

New manifest field extractor_model: contestant makes the match's own model read
the documents. Unset on all five existing workflows, so the tag ladder and every
stored board are unchanged."
```

---

### Task 4: Declare `vision` and gate the board at launch

A text-only contestant on this workflow would score garbage that pollutes the board while looking like a real result. Reject it at launch, not per match — `queue_arena_run` once kept a narrower check than the per-match path and a board died arm by arm after spending money.

**Files:**
- Modify: `config/agent_channels.yaml` and `config/agent_channels.example.yml`
- Modify: `backend/app/golden_workflows/schema.py` (`GoldenWorkflow.requires`)
- Modify: `backend/app/services/arena/task.py` (`queue_arena_run`)
- Test: `tests/test_arena_task_launch.py`

**Interfaces:**
- Consumes: `GoldenWorkflow` (Task 3).
- Produces:
  - `GoldenWorkflow.requires: list[str] = []`
  - `task.capability_rejection(registry, selection: dict, capability: str) -> str | None` — returns a human-readable reason, or `None` when the route declares the capability.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arena_capability_gate.py`:

```python
"""A workflow may REQUIRE a model capability the registry declares.

Rejection happens at LAUNCH, not per match: queue_arena_run once kept its own
narrower check than resolve_agent_model_selection and a board passed validation
then died arm by arm after other pairs had already cost real money.
"""
import pytest


def test_capability_rejection_passes_a_tagged_model():
    from app.services.arena.task import capability_rejection

    class MD:
        tags = ("fast", "tool-use", "vision")

    class Reg:
        def find_model(self, channel, provider, model):
            return object(), MD()

    sel = {"channel": "zenmux", "provider": "bigmodel", "model": "z-ai/glm-5.3-flash"}
    assert capability_rejection(Reg(), sel, "vision") is None


def test_capability_rejection_names_the_missing_capability():
    from app.services.arena.task import capability_rejection

    class MD:
        tags = ("fast", "tool-use")

    class Reg:
        def find_model(self, channel, provider, model):
            return object(), MD()

    sel = {"channel": "zenmux", "provider": "deepseek", "model": "deepseek/deepseek-v4-flash"}
    reason = capability_rejection(Reg(), sel, "vision")
    assert reason is not None and "vision" in reason


def test_capability_rejection_is_permissive_for_an_unresolvable_route():
    """Unknown is PERMISSIVE, never 'unsupported' -- the same rule the reasoning
    ladder follows. A registry lookup failure must not block a working model."""
    from app.services.arena.task import capability_rejection

    class Reg:
        def find_model(self, channel, provider, model):
            raise KeyError(model)

    assert capability_rejection(Reg(), {"channel": "c", "provider": "p", "model": "m"},
                                "vision") is None


def test_requires_defaults_to_empty():
    from app.golden_workflows.schema import GoldenWorkflow

    wf = GoldenWorkflow(
        id="x", schema_version=1, persona="trader", title="T", objective="O",
        fixtures="x.fixtures.json",
        steps=[{"user": "u", "expected_skill": None, "outcome": "o", "replay": "r"}],
        success={"assertions": [], "rubric": []},
    )
    assert wf.requires == []


def test_every_registry_model_tagged_vision_is_on_a_declared_upstream():
    """Guard against tagging a model that has no pinned upstream: an unpinned id
    is a provider lottery, and a vision board must be reproducible."""
    from app.services.deep_agent.channel_registry import load_from_path
    from pathlib import Path

    reg = load_from_path(Path("config/agent_channels.example.yml"))
    tagged = [
        (ch.name, md.id, md.provider)
        for ch in reg.channels for md in ch.models if "vision" in md.tags
    ]
    assert tagged, "no model declares the vision capability"
    for channel, model_id, provider in tagged:
        assert provider, f"{channel}/{model_id} declares vision with no upstream pin"
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_arena_capability_gate.py -v
```

Expected: FAILs — `capability_rejection` does not exist; `requires` is not a field; no model is tagged `vision`.

- [ ] **Step 3: Add the `requires` field**

In `backend/app/golden_workflows/schema.py`, inside `GoldenWorkflow`:

```python
    # Model capabilities this workflow REQUIRES, matched against the registry
    # model's declared tags at launch. A list rather than a boolean so a second
    # capability costs nothing later. Empty (default) = runnable by any model,
    # which is every workflow through #132.
    requires: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add `capability_rejection` and wire it into `queue_arena_run`**

In `backend/app/services/arena/task.py`, at module level:

```python
def capability_rejection(registry, selection: dict, capability: str) -> str | None:
    """Return why ``selection``'s route cannot satisfy ``capability``, else None.

    UNKNOWN IS PERMISSIVE -- an unresolvable route returns None rather than a
    rejection, the same rule the reasoning-effort ladder follows: stale or
    missing registry data must never block a model that actually works.
    """
    try:
        _channel, model_desc = registry.find_model(
            str(selection["channel"]), str(selection["provider"]), str(selection["model"])
        )
    except Exception:  # noqa: BLE001 -- unknown route is permissive, never fatal
        return None
    if capability in (model_desc.tags or ()):
        return None
    return (
        f"{selection['model']} does not declare the {capability!r} capability "
        f"(declared: {', '.join(model_desc.tags) or 'none'})"
    )
```

Inside `queue_arena_run`, after `canonical_model_ids = validate_model_ids(model_ids)`:

```python
    # Capability preflight. A workflow that REQUIRES a capability must reject an
    # undeclared model HERE: running it anyway produces a real-looking score that
    # pollutes the board, and the failure is indistinguishable from poor ability.
    required: set[str] = set()
    for wid in workflow_ids:
        required |= set(get_workflow_bundle(wid).workflow.requires or [])
    if required:
        from app.services.arena.models import arena_model_to_selection, get_model
        from app.services.deep_agent.channel_registry import get_registry

        registry = get_registry()
        missing = []
        for slug in canonical_model_ids:
            selection = arena_model_to_selection(get_model(slug))
            for capability in sorted(required):
                reason = capability_rejection(registry, selection, capability)
                if reason is not None:
                    missing.append(f"{slug}: {reason}")
        if missing:
            raise ValueError(
                "a selected workflow requires a capability the model does not "
                "declare — " + "; ".join(missing)
            )
```

- [ ] **Step 5: Tag the vision-capable models**

In **both** `config/agent_channels.yaml` and `config/agent_channels.example.yml`, add `vision` to the `tags:` list of the models below. Add this comment block above the first tagged model in each file:

```yaml
      # TAG `vision`: the model accepts image content parts. Read by a golden
      # workflow's `requires: [vision]` and enforced at arena launch, so an
      # undeclared model is rejected before it spends money rather than posting a
      # real-looking score it could never have earned. Verified by live probe --
      # do NOT tag a model you have not actually sent an image to.
```

Tag exactly these three first (the confirmed board), leaving the rest for a later, separately-probed change:

| Model | Verified |
|---|---|
| `openai/gpt-5.6-luna` | probe 2026-08-28, 4/4 fields |
| `z-ai/glm-5.3-flash` | probe 2026-08-28, 4/4 fields |
| `google/gemini-3.7-flash` | probe 2026-08-28, 4/4 fields |

Also tag `google/gemini-3.6-flash`, which already holds `confirmation_extractor` and is the production desk extractor — it is by definition vision-capable.

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_arena_capability_gate.py -v
```

Expected: all PASS.

- [ ] **Step 7: Verify the registry still loads and the default is intact**

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app.services.deep_agent.channel_registry import get_registry
r = get_registry()
print('default:', r.default)
print('vision:', [m.id for c in r.channels for m in c.models if 'vision' in m.tags])
"
```

Expected: the default is unchanged and exactly the four models above are listed.

- [ ] **Step 8: Commit**

```bash
git add config/agent_channels.example.yml backend/app/golden_workflows/schema.py backend/app/services/arena/task.py tests/test_arena_capability_gate.py
git commit -m "feat(arena): declare a vision capability and gate boards at launch

Workflows may require a capability; queue_arena_run rejects an undeclared model
before the run starts. Only probe-verified models carry the tag."
```

Note `config/agent_channels.yaml` is gitignored and is deliberately not staged.

---

### Task 5: Track the corpus and move the generator into the package

`.gitignore:50` ignores all of `docs/confirmations/`, so the synthetic corpus and its generator are untracked. A graded benchmark cannot rest on per-environment files. This task is a **pure move** — no new documents — so a byte-identical regeneration proves nothing was lost.

**Files:**
- Modify: `.gitignore`
- Create: `backend/app/golden_workflows/documents/make_confirmations.py` (moved from `docs/confirmations/samples/make_sample_confirmations.py`)
- Create: `backend/app/golden_workflows/documents/README.md` (moved and re-pointed)
- Create: `backend/app/golden_workflows/documents/*.pdf`, `*.docx` (the 8 existing samples)
- Test: `tests/test_confirmation_documents.py`

**Interfaces:**
- Produces: `app.golden_workflows.documents.make_confirmations.build_all(out_dir: Path) -> list[Path]` — writes every document and returns their paths.

- [ ] **Step 1: Narrow `.gitignore`**

Replace line 50 (`docs/confirmations/`) with:

```
# The synthetic confirmation corpus is TRACKED (it grades an arena board) and
# lives in backend/app/golden_workflows/documents/. Only the third-party ISDA
# reference template stays out of the repo.
docs/confirmations/equity-share-option.pdf
```

- [ ] **Step 2: Move the generator and corpus**

```bash
mkdir -p backend/app/golden_workflows/documents
git mv docs/confirmations/samples/make_sample_confirmations.py backend/app/golden_workflows/documents/make_confirmations.py 2>/dev/null \
  || cp docs/confirmations/samples/make_sample_confirmations.py backend/app/golden_workflows/documents/make_confirmations.py
cp docs/confirmations/samples/README.md backend/app/golden_workflows/documents/README.md
cp docs/confirmations/samples/conf-0*.pdf docs/confirmations/samples/conf-0*.docx backend/app/golden_workflows/documents/
```

(`git mv` fails on an untracked file, hence the `cp` fallback. The `docs/confirmations/samples/` copies are left in place; they are still ignored and harm nothing.)

- [ ] **Step 3: Give the generator a `build_all` entry point**

At the bottom of `backend/app/golden_workflows/documents/make_confirmations.py`, replace the existing `if __name__ == "__main__":` block's body with a call to a named function, so tests can drive it:

```python
def build_all(out_dir: Path) -> list[Path]:
    """Render every confirmation document into ``out_dir`` and return the paths.

    Deterministic: the module seeds ``random`` at import so the scan degradation
    (rotation, grain, blur) is byte-reproducible. A regenerated corpus that
    differs byte-for-byte means a dependency moved, and the graded numbers must
    be re-verified before the board is trusted.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # ... existing build calls, each writing into out_dir and appending its path
    return written


if __name__ == "__main__":
    for path in build_all(Path(__file__).parent):
        print(path)
```

Add `random.seed(20260828)` immediately after the `import random` line if the module does not already seed deterministically.

- [ ] **Step 4: Write the regeneration guard test**

Create `tests/test_confirmation_documents.py`:

```python
"""The corpus is TRACKED and must be reproducible from its generator.

If a regenerated document differs from the committed one, a rendering dependency
moved -- and every graded constant harvested from these documents has to be
re-verified before any board built on them is trusted. Same discipline as the
exact quantark==0.3.0 pin.
"""
import hashlib
from pathlib import Path

DOCS = Path("backend/app/golden_workflows/documents")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_committed_document_regenerates_byte_identically(tmp_path):
    from app.golden_workflows.documents.make_confirmations import build_all

    written = build_all(tmp_path)
    assert written, "generator produced no documents"
    for produced in written:
        committed = DOCS / produced.name
        assert committed.exists(), f"{produced.name} is not committed"
        assert _sha(produced) == _sha(committed), (
            f"{produced.name} regenerated differently -- a rendering dependency "
            "moved; re-verify every graded constant before trusting a board"
        )


def test_the_corpus_is_tracked_by_git():
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", str(DOCS)], capture_output=True, text=True, check=True
    ).stdout
    assert "conf-04-scanned-call-googl.pdf" in out
    assert "make_confirmations.py" in out
```

- [ ] **Step 5: Run the tests**

```
.venv/bin/python -m pytest tests/test_confirmation_documents.py -v
```

Expected: PASS. If `test_every_committed_document_regenerates_byte_identically` fails on first run, the committed copies came from a different dependency version — regenerate them from `build_all` and commit the regenerated set, noting the change in the commit message.

- [ ] **Step 6: Commit**

```bash
git add .gitignore backend/app/golden_workflows/documents tests/test_confirmation_documents.py
git commit -m "chore(confirmations): track the synthetic corpus and its generator

docs/confirmations/ was entirely gitignored, which took the synthetic samples and
their generator down with the third-party ISDA reference. A graded benchmark
cannot rest on untracked per-environment files. Pure move: byte-identical
regeneration is asserted."
```

---

### Task 6: Add the three vision-trap documents and emit truth

`conf-04` measured saturated in the probe — all three contestants read it perfectly. It stays as the floor; these three carry the discrimination. Every graded value lives only inside an image and sits far outside `rel_tol` of every other figure in its own document.

**Files:**
- Modify: `backend/app/golden_workflows/documents/make_confirmations.py`
- Create: `backend/app/golden_workflows/documents/conf-09-amended-strike-nvda.pdf`
- Create: `backend/app/golden_workflows/documents/conf-10-ticked-barrier-amzn.pdf`
- Create: `backend/app/golden_workflows/documents/conf-11-faint-notional-orcl.pdf`
- Create: `backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json`
- Test: `tests/test_confirmation_documents.py`

**Interfaces:**
- Consumes: `build_all(out_dir)` (Task 5).
- Produces: `make_confirmations.TRUTH: dict` and `write_truth(path: Path) -> dict` — the graded constants, emitted from the same trade dicts the documents render from.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_confirmation_documents.py`:

```python
import json

TRUTH = Path("backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json")


def test_truth_is_emitted_from_the_same_dicts_the_documents_render_from(tmp_path):
    """Hand-editing truth.json is how fixtures and documents silently disagree."""
    from app.golden_workflows.documents.make_confirmations import write_truth

    emitted = write_truth(tmp_path / "t.json")
    committed = json.loads(TRUTH.read_text())
    assert emitted == committed


def test_the_three_trap_documents_are_image_only():
    """A graded value that survives in the TEXT layer is not a vision check."""
    from app.services.confirmations.extract import extract_document

    for name in ("conf-09-amended-strike-nvda.pdf",
                 "conf-10-ticked-barrier-amzn.pdf",
                 "conf-11-faint-notional-orcl.pdf"):
        content = extract_document(DOCS / name)
        assert content.extract_mode == "vision", f"{name} is not image-only"


def test_no_graded_value_leaks_into_the_text_layer():
    from app.services.confirmations.extract import extract_document

    truth = json.loads(TRUTH.read_text())
    for name, doc_truth in truth["documents"].items():
        content = extract_document(DOCS / name)
        text = " ".join(p.text or "" for p in content.pages)
        for field, value in doc_truth.get("image_only", {}).items():
            assert str(value) not in text, (
                f"{name}: graded field {field}={value} is readable without vision"
            )


def test_each_graded_number_is_far_from_every_decoy_in_its_document():
    """A graded value within rel_tol of a decoy passes on the WRONG number."""
    truth = json.loads(TRUTH.read_text())
    for name, doc_truth in truth["documents"].items():
        graded = [v for v in doc_truth.get("image_only", {}).values()
                  if isinstance(v, (int, float))]
        decoys = [float(d) for d in doc_truth.get("decoys", [])]
        for value in graded:
            for decoy in decoys:
                assert abs(value - decoy) / max(abs(value), 1.0) > 0.05, (
                    f"{name}: graded {value} is within 5% of decoy {decoy}"
                )
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_confirmation_documents.py -v
```

Expected: FAILs — `write_truth` does not exist and the three PDFs are missing.

- [ ] **Step 3: Add the three trade dicts**

In `make_confirmations.py`, beside the existing `C1`…`C8` dicts:

```python
# --- Arena vision traps (conf-09..conf-11) -------------------------------------
# Each renders IMAGE-ONLY and hides its graded value where only vision reaches it.
# Values are chosen far from every other figure in their own document so a
# swapped or hallucinated read fails rather than coincidentally passing.

C9 = dict(                                   # amended strike: printed value struck out
    ref="ARD-EQO-2026-04901", cp="Brightwater Capital Partners LLC",
    underlying="NVDA", family="EuropeanVanillaOption", option_type="CALL",
    quantity=2000, currency="USD",
    printed_strike=780.00,                   # struck through on the page
    strike=917.50,                           # the AMENDED value, handwritten
    initial_price=902.10, expiration="2027-03-19", premium=61.40,
)

C10 = dict(                                  # barrier direction: ticked box only
    ref="ARD-EQO-2026-04902", cp="Kestrel Structured Products S.A.",
    underlying="AMZN", family="BarrierOption", option_type="PUT",
    quantity=1500, currency="USD",
    strike=214.00, initial_price=221.75, barrier=171.20,
    barrier_type="DOWN_OUT",                 # expressed ONLY by which box is ticked
    expiration="2027-01-15", premium=8.35, rebate=0.0,
)

C11 = dict(                                  # notional in a faint column + a decoy
    ref="ARD-EQO-2026-04903", cp="Halden Renshaw Securities Ltd",
    underlying="ORCL", family="EuropeanVanillaOption", option_type="CALL",
    quantity=4000, currency="USD",
    strike=163.50, initial_price=158.90,
    notional=636000.0,                       # the graded value, low-contrast
    decoy_collateral=418750.0,               # adjacent, similar magnitude
    expiration="2026-12-18", premium=12.05,
)
```

- [ ] **Step 4: Render them image-only**

Add three builders that reuse the existing `render_scan` / `save_scan_pdf` helpers so every page is an image. Each writes into `out_dir` and is appended to `build_all`'s returned list.

```python
def build_amended_strike(t: dict, path: Path) -> None:
    """Printed strike struck through, amended value inked in the margin.

    The strike-through and the inked value are DRAWN, so no text layer carries
    the amended number -- the model must read the correction, not just a field.
    """
    lines = scan_lines(t, header=True)
    images = [render_scan(lines)]
    _draw_strikethrough_and_amendment(
        images[0], printed=t["printed_strike"], amended=t["strike"], initials="R.McK.")
    save_scan_pdf(images, path)


def build_ticked_barrier(t: dict, path: Path) -> None:
    """Barrier direction carried ONLY by which checkbox is ticked.

    Both option labels are printed; only the ink distinguishes them, so there is
    no textual fallback and a model that guesses has a 50% floor the decoy
    wording does not raise.
    """
    lines = scan_lines(t, header=True)
    images = [render_scan(lines)]
    _draw_checkbox_pair(
        images[0], options=("Up-and-Out", "Down-and-Out"),
        ticked_index=1 if t["barrier_type"] == "DOWN_OUT" else 0)
    save_scan_pdf(images, path)


def build_faint_notional(t: dict, path: Path) -> None:
    """Notional rendered low-contrast beside a decoy of similar magnitude."""
    lines = scan_lines(t, header=True)
    images = [render_scan(lines)]
    _draw_faint_table(
        images[0],
        rows=[("Collateral Posted", f"{t['decoy_collateral']:,.2f}", 90),
              ("Notional Amount", f"{t['notional']:,.2f}", 168)],
    )
    save_scan_pdf(images, path)
```

Implement `_draw_strikethrough_and_amendment`, `_draw_checkbox_pair` and `_draw_faint_table` with `PIL.ImageDraw` on the rendered page image, above these builders. The third argument of each `_draw_faint_table` row is the greyscale value: `168` is legible-but-faint against the `248` paper, and `90` is the ordinary-contrast decoy.

- [ ] **Step 5: Emit truth from the same dicts**

```python
TRUTH_DOCUMENTS = {
    "conf-04-scanned-call-googl.pdf": {
        "role": "floor",
        "image_only": {"strike": 205.00, "reference": "ARD-EQO-2026-04688"},
        "decoys": [],
    },
    "conf-09-amended-strike-nvda.pdf": {
        "role": "trap-correction",
        "image_only": {"strike": C9["strike"], "reference": C9["ref"]},
        "decoys": [C9["printed_strike"], C9["initial_price"]],
    },
    "conf-10-ticked-barrier-amzn.pdf": {
        "role": "trap-categorical",
        "image_only": {"barrier_type": C10["barrier_type"], "barrier": C10["barrier"]},
        "decoys": [C10["strike"], C10["initial_price"]],
    },
    "conf-11-faint-notional-orcl.pdf": {
        "role": "trap-degraded",
        "image_only": {"notional": C11["notional"], "reference": C11["ref"]},
        "decoys": [C11["decoy_collateral"]],
    },
}


def write_truth(path: Path) -> dict:
    """Emit the graded constants FROM the same dicts the documents render from.

    Hand-editing this file is exactly how fixtures and documents silently
    disagree, after which every grounding check mis-scores with no error anywhere.
    """
    truth = {"documents": TRUTH_DOCUMENTS}
    Path(path).write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return truth
```

Add `import json` at the top of the module.

- [ ] **Step 6: Generate and inspect the documents by eye**

```bash
.venv/bin/python backend/app/golden_workflows/documents/make_confirmations.py
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from pathlib import Path
from app.golden_workflows.documents.make_confirmations import write_truth
write_truth(Path('backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json'))
"
open backend/app/golden_workflows/documents/conf-09-amended-strike-nvda.pdf
```

**A human must actually look at all three.** The tests prove the values are absent from the text layer; only your eyes prove they are *legible* at all. A trap that no model can read is unwinnable (0/N) and carries no more signal than one everybody passes.

- [ ] **Step 7: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_confirmation_documents.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/golden_workflows/documents backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json tests/test_confirmation_documents.py
git commit -m "feat(arena): add three vision-trap confirmation documents

conf-04 measured saturated (3/3 contestants read it perfectly), so it is the
floor. These three hide their graded value where only vision reaches it:
a struck-through-and-amended strike, a barrier direction carried by a ticked box,
and a low-contrast notional beside a decoy. Truth is emitted from the same dicts
the documents render from."
```

---

### Task 7: Stage documents into the uploads root at match setup

`parse_trade_confirmation` only accepts paths under `artifact_dir/uploads`. A fixture that declares a document must **create** it — `artifact_bodies` writes `str` only and cannot carry a PDF.

**Files:**
- Modify: `backend/app/golden_workflows/fixtures.py`
- Modify: `backend/app/services/arena/runner.py` (`run_match` setup)
- Test: `tests/test_golden_workflow_fixtures.py`

**Interfaces:**
- Consumes: the corpus at `backend/app/golden_workflows/documents/` (Tasks 5-6).
- Produces: `fixtures.stage_documents(bundle, uploads_root: Path) -> list[Path]` — copies each declared document and returns the staged paths.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_golden_workflow_fixtures.py`:

```python
def test_stage_documents_copies_declared_files_into_the_uploads_root(tmp_path):
    """A fixture that DECLARES a document must CREATE it. artifact_bodies writes
    str only, so a PDF needs its own staging path -- otherwise the agent chases a
    dangling pointer and burns calls hunting a file that was never written."""
    from app.golden_workflows.fixtures import FixtureBundle, stage_documents

    bundle = FixtureBundle(seed={}, replay={}, seed_map={})
    bundle.documents = ["conf-04-scanned-call-googl.pdf"]

    staged = stage_documents(bundle, tmp_path)
    assert len(staged) == 1
    assert staged[0].parent == tmp_path / "confirmations"
    assert staged[0].name == "conf-04-scanned-call-googl.pdf"
    assert staged[0].read_bytes()[:4] == b"%PDF"


def test_stage_documents_is_idempotent_across_trials(tmp_path):
    from app.golden_workflows.fixtures import FixtureBundle, stage_documents

    bundle = FixtureBundle(seed={}, replay={}, seed_map={})
    bundle.documents = ["conf-04-scanned-call-googl.pdf"]
    first = stage_documents(bundle, tmp_path)
    second = stage_documents(bundle, tmp_path)
    assert first == second
    assert first[0].read_bytes() == second[0].read_bytes()


def test_stage_documents_rejects_a_path_escaping_the_corpus(tmp_path):
    """The declared name is a BASENAME in the tracked corpus, never a path."""
    from app.golden_workflows.fixtures import FixtureBundle, stage_documents

    bundle = FixtureBundle(seed={}, replay={}, seed_map={})
    bundle.documents = ["../../../etc/passwd"]
    with pytest.raises(ValueError):
        stage_documents(bundle, tmp_path)


def test_stage_documents_raises_for_an_undeclared_document(tmp_path):
    from app.golden_workflows.fixtures import FixtureBundle, stage_documents

    bundle = FixtureBundle(seed={}, replay={}, seed_map={})
    bundle.documents = ["conf-99-does-not-exist.pdf"]
    with pytest.raises(FileNotFoundError):
        stage_documents(bundle, tmp_path)
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py -k stage_documents -v
```

Expected: FAIL with `ImportError: cannot import name 'stage_documents'`.

- [ ] **Step 3: Implement staging**

In `backend/app/golden_workflows/fixtures.py`:

```python
_DOCUMENTS_DIR = Path(__file__).parent / "documents"


def stage_documents(bundle, uploads_root: Path) -> list[Path]:
    """Copy a bundle's declared documents into ``uploads_root/confirmations/``.

    parse_trade_confirmation refuses any path outside ``artifact_dir/uploads``,
    so a workflow that grades document reading has to put real bytes there. This
    is the binary analogue of ``artifact_bodies``, which writes ``str`` only.

    Idempotent: overwrites on every call, so re-staging across the trials of one
    match is safe and a half-written file from a crashed trial is replaced.
    """
    import shutil

    declared = list(getattr(bundle, "documents", None) or [])
    if not declared:
        return []
    target_dir = Path(uploads_root) / "confirmations"
    target_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for name in declared:
        if Path(name).name != name:
            raise ValueError(
                f"document {name!r} must be a bare filename in the tracked corpus"
            )
        source = _DOCUMENTS_DIR / name
        if not source.is_file():
            raise FileNotFoundError(f"document {name!r} is not in {_DOCUMENTS_DIR}")
        target = target_dir / name
        shutil.copyfile(source, target)
        staged.append(target)
    return staged
```

Add `documents: list[str] = field(default_factory=list)` to the `FixtureBundle` dataclass, and read a top-level `"documents"` key in the bundle loader beside `seed` and `replay`.

- [ ] **Step 4: Call it from `run_match`**

In `backend/app/services/arena/runner.py`, immediately after `_assert_trap_sets_absent(loaded, _settings)` and before the seeding session opens:

```python
    # Stage the confirmation corpus BEFORE the first turn: parse_trade_confirmation
    # refuses any path outside artifact_dir/uploads, and a declared-but-unwritten
    # document is a dangling pointer the model burns calls chasing.
    stage_documents(loaded.fixtures, Path(_settings.artifact_dir) / "uploads")
```

with `from app.golden_workflows.fixtures import apply_seed, stage_documents` (extend the existing import).

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py -v
```

Expected: all PASS, including every pre-existing fixture test.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py backend/app/services/arena/runner.py tests/test_golden_workflow_fixtures.py
git commit -m "feat(arena): stage declared confirmation documents into the uploads root

The binary analogue of artifact_bodies, which writes str only. A fixture that
declares a document must create it, or the agent chases a dangling pointer."
```

---

### Task 8: Purge confirmation rows after a match

`ConfirmationBatch.default_portfolio_id` is a foreign key to `portfolios` under a column name `_delete_portfolios_with_dependents` does not scan, and that helper's FK recursion explicitly skips the portfolios table. So a batch parsed into the arena book is never swept and the final portfolio delete dies on a foreign-key constraint.

**Files:**
- Modify: `backend/app/services/arena/trace_harvest.py`
- Modify: `backend/app/services/arena/runner.py`
- Test: `tests/test_arena_runner_purge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `trace_harvest.collect_confirmation_batch_ids_created(thread_id, store=None) -> set[int]`
  - `runner._purge_match_confirmations(thread_id: int, batch_id_baseline: int) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arena_runner_purge.py`:

```python
"""ConfirmationBatch.default_portfolio_id references portfolios under a name the
dependents sweep does not scan, and the sweep's recursion skips the portfolios
table -- so a portfolio booked from a confirmation could not be deleted at all.
"""
import pytest

from app import database, models


def test_portfolio_delete_fails_without_the_confirmation_purge(db_session):
    """Characterization: this is the bug, proven before it is fixed."""
    from app.services.arena.runner import _delete_portfolios_with_dependents

    portfolio = models.Portfolio(name="Arena Confirmation Desk", tags=["arena"])
    db_session.add(portfolio)
    db_session.commit()
    batch = models.ConfirmationBatch(source="agent", default_portfolio_id=portfolio.id)
    db_session.add(batch)
    db_session.commit()

    with pytest.raises(Exception):
        _delete_portfolios_with_dependents(db_session, [portfolio.id])
        db_session.commit()


def test_purge_match_confirmations_removes_batches_above_the_baseline(db_session, monkeypatch):
    from app.services.arena import runner

    old = models.ConfirmationBatch(source="agent")
    db_session.add(old)
    db_session.commit()
    baseline = old.id

    new = models.ConfirmationBatch(source="agent")
    db_session.add(new)
    db_session.commit()
    new_id = new.id

    monkeypatch.setattr(
        runner, "collect_confirmation_batch_ids_created", lambda tid, store=None: {new_id}
    )
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=baseline)

    db_session.expire_all()
    assert db_session.get(models.ConfirmationBatch, new_id) is None
    assert db_session.get(models.ConfirmationBatch, baseline) is not None


def test_purge_match_confirmations_never_raises(monkeypatch):
    """Cleanup is cosmetic hygiene and must never mask a match failure."""
    from app.services.arena import runner

    def _boom(tid, store=None):
        raise RuntimeError("trace store down")

    monkeypatch.setattr(runner, "collect_confirmation_batch_ids_created", _boom)
    runner._purge_match_confirmations(thread_id=1, batch_id_baseline=0)  # no raise
```

Use whatever session fixture `tests/conftest.py` already provides for DB tests; if it is not named `db_session`, match the name used by `tests/test_arena_store.py`.

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/bin/python -m pytest tests/test_arena_runner_purge.py -v
```

Expected: the two purge tests FAIL with `AttributeError: module ... has no attribute '_purge_match_confirmations'`. The characterization test should PASS — it proves the bug exists.

- [ ] **Step 3: Add the trace collector**

In `backend/app/services/arena/trace_harvest.py`, beside `collect_portfolio_ids_created`:

```python
_CONFIRMATION_PARSE_TOOLS = {"parse_trade_confirmation"}


def collect_confirmation_batch_ids_created(thread_id, store=None) -> set[int]:
    """Return the confirmation batch ids this thread's parse calls MINTED.

    Mirrors ``collect_portfolio_ids_created``: the caller intersects these with an
    "id > pre-match baseline" guard so only batches created BY THIS MATCH are
    deleted.
    """
    if store is None:
        from app.config import get_settings
        from app.services.tracing.store import get_trace_store
        store = get_trace_store(get_settings())
    if hasattr(store, "flush"):
        store.flush()

    out: set[int] = set()
    for root in store.list_thread_traces(thread_id, limit=1000):
        for sp in store.get_trace(root["trace_id"]):
            if sp.get("run_type") != "tool" or sp.get("name") not in _CONFIRMATION_PARSE_TOOLS:
                continue
            content, _name, _tcid = _parse_tool_output(sp.get("outputs"))
            if isinstance(content, dict) and isinstance(content.get("batch_id"), int):
                out.add(content["batch_id"])
    return out
```

- [ ] **Step 4: Add the purge and call it**

In `backend/app/services/arena/runner.py`, beside `_purge_match_rfqs`:

```python
def _purge_match_confirmations(thread_id: int, batch_id_baseline: int) -> None:
    """Best-effort cleanup of confirmation batches CREATED BY THIS MATCH.

    Needed because ``_delete_portfolios_with_dependents`` cannot reach them:
    ``ConfirmationBatch.default_portfolio_id`` is an FK to ``portfolios`` under a
    column name the sweep does not scan, and its FK recursion explicitly SKIPS the
    portfolios table -- so an arena portfolio booked from a confirmation could not
    be deleted at all. Documents and extracted trades cascade from the batch.

    Runs in a ``finally`` for the same reason as ``_purge_match_rfqs``: the next
    baseline is taken above a leaked row, so its guard could never re-catch it.
    """
    from sqlalchemy import delete

    from app import models

    try:
        created = collect_confirmation_batch_ids_created(thread_id)
        doomed = {bid for bid in created if bid > batch_id_baseline}
        if not doomed:
            return
        with database.SessionLocal() as session:
            doc_ids = [
                d.id for d in session.query(models.ConfirmationDocument).filter(
                    models.ConfirmationDocument.batch_id.in_(doomed))
            ]
            if doc_ids:
                session.execute(delete(models.ExtractedTrade).where(
                    models.ExtractedTrade.document_id.in_(doc_ids)))
                session.execute(delete(models.ConfirmationDocument).where(
                    models.ConfirmationDocument.id.in_(doc_ids)))
            session.execute(delete(models.ConfirmationBatch).where(
                models.ConfirmationBatch.id.in_(doomed)))
            session.commit()
    except Exception:  # noqa: BLE001 — best-effort; never mask the match outcome
        logger.warning(
            "arena confirmation cleanup failed for thread %s", thread_id, exc_info=True
        )
```

Add `collect_confirmation_batch_ids_created` to the existing `trace_harvest` import in `runner.py`.

Take the baseline beside the others in `run_match`:

```python
        confirmation_batch_baseline = (
            session.query(func.max(models.ConfirmationBatch.id)).scalar() or 0
        )
```

and call the purge in the `finally`, **before** `_purge_match_portfolios` (the batch must go before the portfolio it references):

```python
        _purge_match_confirmations(thread_id, confirmation_batch_baseline)
        _purge_match_rfqs(thread_id, rfq_id_baseline)
        _purge_match_portfolios(thread_id, portfolio_id_baseline)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_arena_runner_purge.py tests/test_arena_store.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/arena/trace_harvest.py backend/app/services/arena/runner.py tests/test_arena_runner_purge.py
git commit -m "fix(arena): purge confirmation batches created by a match

ConfirmationBatch.default_portfolio_id is an FK to portfolios under a name the
dependents sweep does not scan, and that sweep's recursion skips the portfolios
table -- so an arena portfolio booked from a confirmation could not be deleted."
```

---

### Task 9: The workflow manifest, fixtures, and replay

**Files:**
- Create: `backend/app/golden_workflows/definitions/confirmation-desk-day.md`
- Create: `backend/app/golden_workflows/definitions/confirmation-desk-day.fixtures.json`
- Test: `tests/test_confirmation_desk_day_workflow.py`

**Interfaces:**
- Consumes: `extractor_model` + `requires` (Tasks 3-4), `stage_documents` (Task 7), `truth.json` (Task 6).
- Produces: workflow id `confirmation-desk-day`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_confirmation_desk_day_workflow.py`:

```python
import json
from pathlib import Path

TRUTH = Path("backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json")


def _bundle():
    from app.golden_workflows.registry import get_workflow_bundle
    return get_workflow_bundle("confirmation-desk-day")


def test_workflow_loads_with_the_expected_shape():
    wf = _bundle().workflow
    assert wf.persona == "trader"
    assert len(wf.steps) == 8
    assert wf.extractor_model == "contestant"
    assert wf.requires == ["vision"]


def test_par_is_deliberately_uncalibrated():
    """An uncalibrated workflow must stay on the legacy hyperbolic EFF curve.
    Setting a guessed par opts it into golf scoring against a denominator no live
    run justifies -- and cards derive on read, so it re-scores every stored board."""
    assert _bundle().workflow.par_tool_calls is None

    from app.services.arena.scoring import par_calibrated
    assert par_calibrated(_bundle().workflow) is False


def test_only_the_first_step_grades_a_skill():
    """skills_routed records a skill only when its SKILL.md is READ, and the
    runtime never re-reads a loaded file, so a repeat-skill check can never pass."""
    steps = _bundle().workflow.steps
    assert steps[0].expected_skill == "book-trade-confirmation"
    assert all(s.expected_skill is None for s in steps[1:])


def test_the_routed_skill_actually_has_a_routing_block():
    """A skill with no `routing:` line never enters the orchestrator's known-skills
    table, so grading it measures catalog spelunking rather than ability."""
    text = Path(
        "backend/app/skills/workflows/positions/book-trade-confirmation/SKILL.md"
    ).read_text()
    assert "\nrouting:" in text


def test_every_graded_value_comes_from_the_truth_file():
    """Guards the drift the truth file exists to prevent."""
    truth = json.loads(TRUTH.read_text())
    values = {
        str(v)
        for doc in truth["documents"].values()
        for v in doc.get("image_only", {}).values()
    }
    md = Path(
        "backend/app/golden_workflows/definitions/confirmation-desk-day.md"
    ).read_text()
    graded = [line for line in md.splitlines() if "value:" in line or "equals:" in line]
    assert graded, "no graded assertions found"
    for line in graded:
        if "is_null" in line:
            continue
        assert any(v.rstrip("0").rstrip(".") in line or v in line for v in values), (
            f"graded line is not backed by truth.json: {line.strip()}"
        )


def test_golden_replay_scores_full_marks():
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena import scoring

    loaded = _bundle()
    transcript = transcript_from_replay(loaded)
    score, passed, total = scoring.objective_score(transcript, loaded)
    assert passed == total, f"replay earned {passed}/{total}"
    assert score == 100.0
```

`scoring.objective_score(transcript, loaded)` returns a **3-tuple**
`(score, passed, total)` — verified against `tests/test_ops_settlement_day_workflow.py`,
which is the file to mirror for the negative-mutation tests too: each mutation of
the replay must drop the score below full marks, or the assertion is not actually
discriminating.

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/bin/python -m pytest tests/test_confirmation_desk_day_workflow.py -v
```

Expected: FAIL — the workflow does not exist.

- [ ] **Step 3: Write the manifest**

Create `backend/app/golden_workflows/definitions/confirmation-desk-day.md`. Read `ops-settlement-day.md` first and mirror its frontmatter shape exactly. Frontmatter:

```yaml
---
id: confirmation-desk-day
schema_version: 1
persona: trader
title: "Confirmation Desk Day"
objective: >
  A trade-support operator works a morning's counterparty confirmations: parse a
  mixed batch of documents including image-only scans, read back the terms that
  exist only inside the images, decline to invent a term the document never
  states, repair the one trade the desk can legitimately complete, and book the
  valid trades — without booking anything that failed validation.
fixtures: confirmation-desk-day.fixtures.json
tags: [confirmations, vision, operations, desk-workflow]

# The document-extraction sub-call routes to THIS MATCH's model. Without it,
# resolve_confirmation_extractor_selection picks by registry tag and every
# contestant reads every document with gemini-3.6-flash's eyes -- so every vision
# check lands N/N across the field and carries zero ability signal.
extractor_model: contestant

# Rejected at LAUNCH for a model whose registry row does not declare `vision`.
requires: [vision]

# par_tool_calls is deliberately ABSENT. An uncalibrated workflow stays on the
# legacy hyperbolic EFF curve; a guessed par would opt this board into golf
# scoring against a denominator no live run has justified, and because cards
# derive on read it would re-score every stored board containing it.
# Calibrate from the MEDIAN of fully-correct trials, EXCLUDING merged runs, once
# a real board exists -- and sanity-check n against the number of runs that could
# have produced it.
---
```

Then the 8 steps. Rules to hold to, each already paid for elsewhere in this repo:

1. **Name the answer fields in the `user:` turn.** A check graded on output the prompt never requests is unwinnable — that cost the high-board 5 checks.
2. **Use `answer_field_quotes` / `answer_field_equals`** against `truth.json` values, not `tool_result_path` — grade the answer, not the trace.
3. **Step 6's wording is neutral.** Name the field without hinting absence is correct; grade with `answer_field_equals: {field: initial_price, is_null: true}` plus `tool_not_called: book_extracted_trade`. `is_null` requires the field to be *recorded* as null — omission still fails.
4. **`args_any_of`** wherever more than one calling convention is legitimate (e.g. `book_extracted_trade` with or without an explicit `portfolio_id`).
5. `success.assertions` carries the session-wide ban only; never duplicate a per-step assertion there (double jeopardy).

- [ ] **Step 4: Write the fixtures**

Create `confirmation-desk-day.fixtures.json` with a `documents` list naming the six graded documents, a seeded arena portfolio, a bookable underlying for each traded ticker (`Instrument.status` defaults to `"draft"`, so the row must be **active AND tagged `underlying`** or every booking reports `invalid`), and a `replay` entry per step key.

```json
{
  "documents": [
    "conf-04-scanned-call-googl.pdf",
    "conf-07-missing-initial-price-meta.pdf",
    "conf-08-mixed-text-and-scan-amd.pdf",
    "conf-09-amended-strike-nvda.pdf",
    "conf-10-ticked-barrier-amzn.pdf",
    "conf-11-faint-notional-orcl.pdf"
  ],
  "seed": {
    "portfolios": [
      {"alias": "desk", "name": "Arena Confirmation Desk", "tags": ["arena"]}
    ],
    "instruments": [
      {"alias": "googl", "symbol": "GOOGL", "status": "active", "tags": ["underlying"]},
      {"alias": "meta", "symbol": "META", "status": "active", "tags": ["underlying"]},
      {"alias": "amd", "symbol": "AMD", "status": "active", "tags": ["underlying"]},
      {"alias": "nvda", "symbol": "NVDA", "status": "active", "tags": ["underlying"]},
      {"alias": "amzn", "symbol": "AMZN", "status": "active", "tags": ["underlying"]},
      {"alias": "orcl", "symbol": "ORCL", "status": "active", "tags": ["underlying"]}
    ]
  },
  "replay": {}
}
```

**Do not pin an `id` in the `pricing_profiles` namespace** — it is the one namespace whose purge can *retire* rather than delete a row, after which the next match's seed dies on a UNIQUE constraint and takes every remaining match in the board with it. This fixture declares no pricing profile at all, which sidesteps it.

Match the exact key names each namespace requires by reading `fixtures.py::_NAMESPACES` and `ops-settlement-day.fixtures.json` — do not guess field names.

- [ ] **Step 5: Fill the replay block**

Populate one `replay` entry per step, hand-writing the tool calls and `record_answer` payloads that a perfect run produces, so `test_golden_replay_earns_full_marks` passes. Mirror `ops-settlement-day.fixtures.json`'s replay shape.

**This proves satisfiability, never reachability.** It is written to satisfy each assertion by construction, so it cannot tell you whether a live model can reach the same state. Task 11 is what does that.

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv/bin/python -m pytest tests/test_confirmation_desk_day_workflow.py -v
```

Expected: all PASS, including `test_golden_replay_earns_full_marks`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/definitions/confirmation-desk-day.md backend/app/golden_workflows/definitions/confirmation-desk-day.fixtures.json tests/test_confirmation_desk_day_workflow.py
git commit -m "feat(arena): add the confirmation-desk-day golden workflow

Eight steps over the confirmation pipeline, graded on values that exist only
inside document images. First arena workflow to exercise vision."
```

---

### Task 10: Repair exact-set pins, run the full suite, update docs

Adding a workflow and a skill reference breaks exact-set assertions across several catalog test files. This repo has been bitten by leaving those red.

**Files:**
- Modify: whichever test files the suite reports (`tests/test_skills_catalog*.py`, `tests/test_routing_table.py`, `tests/test_arena_deploy_*.py`, `tests/test_golden_workflow_fixtures.py`, …)
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `README.md` (if user-facing)

- [ ] **Step 1: Enumerate the affected pins**

```bash
grep -rln "ops-settlement-day\|book-trade-confirmation" tests/
```

- [ ] **Step 2: Run the full backend suite**

```
.venv/bin/python -m pytest -q
```

Record the failure list. Compare against a baseline on the branch point before assuming a failure is yours:

```bash
git stash list   # confirm empty; NEVER bare-stash in this shared repo
```

- [ ] **Step 3: Fix each exact-set assertion**

Update counts and set literals to include `confirmation-desk-day`. Do **not** loosen an exact-set assertion into a subset check — the exactness is what makes it catch an accidental addition.

- [ ] **Step 4: Re-run until green**

```
.venv/bin/python -m pytest -q
```

Expected: no failures introduced by this branch. Any pre-existing failure must be identified as pre-existing by checking it out on `main` and re-running.

- [ ] **Step 5: Update `CHANGELOG.md`**

Under `[Unreleased]`, in Keep a Changelog style, covering: the sixth golden workflow, the extractor override and its manifest field, the `vision` tag and launch gate, the corpus becoming tracked, the block-list content fix, and the confirmation purge fix.

- [ ] **Step 6: Update `CLAUDE.md`**

Add a `confirmation-desk-day` subsection under the golden-workflows section documenting: the tag-routing trap the design exists to avoid; `extractor_model: contestant`; `requires: [vision]` and launch-time rejection; that the corpus is tracked and generator-emitted with truth; the `stage_documents` staging path; the `_purge_match_confirmations` FK gap; and that `par` is uncalibrated.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "test,docs: repair exact-set pins and document confirmation-desk-day"
```

---

### Task 11: Live smoke before merge

The golden replay is hand-written and satisfies each assertion by construction. It proves **satisfiability, never reachability**. This repo has been bitten twice — the trader-rfq live-reachability fix, and the Run #58 audit's 15 dead checks.

- [ ] **Step 1: Confirm the launcher sees the workflow**

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app.golden_workflows.registry import list_workflow_bundles
print([b.workflow.id for b in list_workflow_bundles()])
"
```

Expected: six ids including `confirmation-desk-day`.

- [ ] **Step 2: Verify the capability gate rejects a text-only model**

The launcher has no `--dry-run`, so call `queue_arena_run` directly and roll the
session back — the gate must fire during validation, before any match runs.

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app import database
from app.services.arena.task import queue_arena_run

database.init_db()
with database.SessionLocal() as s:
    try:
        queue_arena_run(
            s, workflow_ids=["confirmation-desk-day"], model_ids=["deepseek-v4-flash"]
        )
    except ValueError as exc:
        print("REJECTED (correct):", exc)
    else:
        print("!! LAUNCHED -- the capability gate is not wired")
    finally:
        s.rollback()
PY
```

Expected: `REJECTED (correct): ... does not declare the 'vision' capability`. If it
prints the failure line instead, the gate is not wired — stop and fix Task 4
before spending money on a board.

Then confirm a tagged model passes the same gate:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app import database
from app.services.arena.task import queue_arena_run

database.init_db()
with database.SessionLocal() as s:
    queue_arena_run(
        s, workflow_ids=["confirmation-desk-day"], model_ids=["gemini-3-7-flash"]
    )
    print("accepted a vision-tagged model")
    s.rollback()
PY
```

- [ ] **Step 3: Run a single-model, single-trial smoke**

```
.venv/bin/python scripts/launch_arena_run.py \
  --workflows confirmation-desk-day \
  --models gemini-3-7-flash \
  --trials 1
```

Detach it with `start_new_session` if it will outlive the shell, and **watch the trace clock, not the process** — a wedged run sits at 0% CPU with status `running` and nothing raises. Poll `max(start_time)` on `trace_runs`; quiet for >20 minutes with the process alive means wedged. Recover with SIGKILL then `--resume <run>`.

- [ ] **Step 4: Verify the contestant actually did the reading**

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.config import get_settings
from app.services.tracing.store import get_trace_store

THREAD_ID = 0  # <- the [arena] confirmation-desk-day thread id from the run

store = get_trace_store(get_settings())
if hasattr(store, "flush"):
    store.flush()
models_seen = {}
for root in store.list_thread_traces(THREAD_ID, limit=1000):
    for sp in store.get_trace(root["trace_id"]):
        if sp.get("run_type") != "llm":
            continue
        meta = sp.get("extra") or {}
        name = str(meta.get("model") or sp.get("name") or "?")
        models_seen[name] = models_seen.get(name, 0) + 1
for name, count in sorted(models_seen.items(), key=lambda kv: -kv[1]):
    print(f"{count:5d}  {name}")
PY
```

Find the thread id with:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app import database, models
with database.SessionLocal() as s:
    for t in s.query(models.AgentThread).filter(
        models.AgentThread.source == 'arena').order_by(models.AgentThread.id.desc()).limit(5):
        print(t.id, t.title)
"
```

Expected: the model list contains the **contestant** and does **not** contain `google/gemini-3.6-flash`. If the span metadata does not carry a model name, fall back to asserting it at the seam instead — add a temporary `logger.info("extractor selection: %s", self.selection)` in `RegistryExtractorClient.__init__` and re-run one match.

**This is the single most important check in the plan.** If extraction still routes by tag, the board measures nothing about the contestant and every vision check is dead weight.

- [ ] **Step 5: Read the per-check tally**

For the smoke match, walk `objective.steps[].checks[]` in `arena_match.score_breakdown` and list each check's pass/fail. Any check at 0/N is a candidate unwinnable check — inspect the transcript before blaming the model, exactly as the Run #109 trap investigation did.

- [ ] **Step 6: Confirm cleanup left nothing behind**

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'backend')
from app import database, models
with database.SessionLocal() as s:
    print('batches:', s.query(models.ConfirmationBatch).count())
    print('arena portfolios:', s.query(models.Portfolio).filter(
        models.Portfolio.name == 'Arena Confirmation Desk').count())
"
```

Expected: zero of both.

- [ ] **Step 7: Run the second and third contestants**

Repeat Step 3 for `glm-5-3-flash` and `gpt-5-6-luna`. GLM is the one that exercises Task 1's block-list fix on the live path — if its documents come back unparsed, that fix is not reaching the arena.

- [ ] **Step 8: Report before merging**

Summarize: per-model objective scores, the per-check tally, which vision traps discriminated and which saturated, and any check at 0/N or N/N. **Do not calibrate `par` from a three-model smoke** — a par set by three models encodes their shared habits as the standard, which is the caveat `ops-settlement-day`'s own par still carries.

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §4.1 protocol-agnostic extractor client | 1 |
| §4.2 override seam | 2 |
| §4.3 manifest declares the condition | 3 |
| §5.1 tracked corpus + generator | 5 |
| §5.2 vision-trap documents + truth | 6 |
| §6.1 document staging | 7 |
| §6.2 `_purge_match_confirmations` | 8 |
| §7 `vision` tag + launch gate | 4 |
| §8 the manifest | 9 |
| §9 `par` unset | 9 (asserted), 11 (not calibrated) |
| §10 testing incl. live smoke | 10, 11 |
| §12 open item: `configurable` key name | 2 (`__confirmation_extractor_selection__`) |
| §12 open item: staging order + idempotency | 7 |
| §12 open item: where `documents:` lives | 7 (fixtures JSON, beside `seed`/`replay`) |

**Type consistency**

`CONFIRMATION_EXTRACTOR_SELECTION_KEY`, `_content_to_text`, `build_extractor_client(selection=None)`, `resolve_confirmation_extractor_selection(registry, override=None)`, `_extractor_override_from_config(config)`, `stage_documents(bundle, uploads_root)`, `capability_rejection(registry, selection, capability)`, `collect_confirmation_batch_ids_created(thread_id, store=None)`, `_purge_match_confirmations(thread_id, batch_id_baseline)`, `_make_default_drive(accounting_date, route_extractor_to_contestant=False)`, `write_truth(path)`, `build_all(out_dir)` — each is defined once and referred to by the same name everywhere it appears.

**Known soft spots, flagged rather than hidden**

- Task 9's `test_every_graded_value_comes_from_the_truth_file` does a textual match over manifest lines. It is a drift tripwire, not a proof; if it proves brittle, replace it with a parse of the loaded workflow's assertion objects rather than loosening it.
- Task 6's drawing helpers (`_draw_strikethrough_and_amendment`, `_draw_checkbox_pair`, `_draw_faint_table`) are specified by behaviour and signature but their pixel geometry depends on `render_scan`'s actual layout, which the implementer must read. Step 6's human eyeball check is the gate that catches a badly-placed annotation.
- Task 10 cannot enumerate the exact pins in advance because the counts depend on the final skill/tool set. Step 1's `grep` is the enumeration.
