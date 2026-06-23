# Asian Fixing Agent Tools + Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose `generate_asian_fixing_schedule` and `capture_due_asian_fixings` as HITL-gated agent tools, add a dedicated `asian-fixings` routing skill, and update the catalog tests the new skill trips.

**Architecture:** Two thin `@tool` wrappers in `backend/app/tools/positions.py` delegating to the existing (already-reviewed) services. One backward-compatible service tweak makes `capture_due_asian_fixings` self-scope its own transaction when no session is injected. New skill under `positions/asian-fixings/`.

**Tech Stack:** Python, LangChain `@tool`, SQLAlchemy, pytest. Run tests from worktree with `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest`.

## Global Constraints

- Tests live at repo-root `tests/` (NOT `backend/tests/`).
- Session fixture is `session` (not `db_session`).
- Do NOT change service correctness (row locks, close-only capture, idempotency) — only the capture transaction boundary.
- Both tools are persisted writes → `@capability_gated(group=ToolGroup.DOMAIN_WRITE)`.
- `DEEP_AGENT_TOOL_NAMES` is a strict allowlist — a tool absent there is dropped.

---

### Task 1: Make `capture_due_asian_fixings` self-scoping

**Files:**
- Modify: `backend/app/services/domains/positions.py` (`capture_due_asian_fixings`, ~762)
- Test: `tests/test_asian_fixing_tools.py`

**Interfaces:**
- Produces: `capture_due_asian_fixings(session: Session | None, position_id: int, *, portfolio_id=None, as_of=None) -> int` — when `session is None`, opens+commits its own unit of work; when injected, unchanged (no commit).

- [ ] **Step 1: Write the failing test** (`tests/test_asian_fixing_tools.py`)

```python
from datetime import date, datetime, timedelta

from app.models import Instrument, MarketQuote, Position
from app.services.domains import positions as positions_svc


def _asian_position(session, *, captured=False):
    inst = Instrument(symbol="ASN1", name="Asian Underlying 1")
    session.add(inst)
    session.flush()
    past = date(2026, 1, 15)
    rec = {"observation_date": past.isoformat(), "weight": 1.0, "observed_price": (100.0 if captured else None)}
    pos = Position(
        portfolio_id=1,
        product_type="AsianOption",
        underlying_id=inst.id,
        product_kwargs={
            "averaging_frequency": "MONTHLY",
            "maturity_years": 1.0,
            "trade_start_date": "2025-12-15",
            "observation_records": [rec],
        },
    )
    session.add(pos)
    session.flush()
    session.add(MarketQuote(instrument_id=inst.id, as_of=datetime(2026, 1, 15, 15, 0), price=123.0, price_type="close"))
    session.commit()
    return pos.id, inst.id


def test_capture_self_scopes_and_commits_when_session_is_none(session):
    pos_id, _ = _asian_position(session)
    # Tool path: pass session=None so the service owns the transaction.
    captured = positions_svc.capture_due_asian_fixings(None, pos_id)
    assert captured == 1
    # Re-read through the SAME session after expiring — value must be persisted.
    session.expire_all()
    pos = session.query(Position).get(pos_id)
    rec = pos.product_kwargs["observation_records"][0]
    assert rec["observed_price"] == 123.0
```

- [ ] **Step 2: Run it, verify it fails**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py::test_capture_self_scopes_and_commits_when_session_is_none -v`
Expected: FAIL — `capture_due_asian_fixings` currently requires a real `session` (AttributeError on `None.query`) / no commit.

- [ ] **Step 3: Implement the tweak**

Change the signature first line and wrap the body. Current:
```python
def capture_due_asian_fixings(
    session: Session,
    position_id: int,
    *,
    portfolio_id: int | None = None,
    as_of: date | None = None,
) -> int:
```
to:
```python
def capture_due_asian_fixings(
    session: Session | None,
    position_id: int,
    *,
    portfolio_id: int | None = None,
    as_of: date | None = None,
) -> int:
```
Then wrap the existing body in `with _session_scope(session) as sess:` and replace the in-body `session` references with `sess`. At the end, after the `if captured:` flush block, add a self-scoped commit:
```python
        if captured:
            kwargs["observation_records"] = new_records
            position.product_kwargs = kwargs
            flag_modified(position, "product_kwargs")
            sess.flush()
        if session is None:
            sess.commit()
        return captured
```
(The early `return 0` paths inside the `with` block are fine — `_session_scope` does not commit on a non-owned session, and the no-capture owned path has nothing to commit.)

- [ ] **Step 4: Run the test + the existing fixing-capture suite**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py tests/test_asian_fixing_capture.py tests/test_asian_fixing_lifecycle.py -v`
Expected: new test PASS; all pre-existing capture/lifecycle tests still PASS (injected callers unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/positions.py tests/test_asian_fixing_tools.py
git commit -m "feat(asian): self-scope capture_due_asian_fixings when session omitted"
```

---

### Task 2: The two agent tools + registration

**Files:**
- Modify: `backend/app/tools/positions.py` (new input schemas + two `@tool` fns)
- Modify: `backend/app/tools/__init__.py` (import + `QUANT_AGENT_TOOLS` list)
- Modify: `backend/app/services/agents.py` (`DEEP_AGENT_TOOL_NAMES`)
- Test: `tests/test_asian_fixing_tools.py`

**Interfaces:**
- Consumes: `positions_svc.generate_asian_fixing_schedule(...)`, `positions_svc.capture_due_asian_fixings(None, position_id, portfolio_id=…, ...)` from Task 1.
- Produces: tools `generate_asian_fixing_schedule` → `{position_id, events_created}`; `capture_asian_fixings` → `{position_id, captured}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_asian_fixing_tools.py`)

```python
from app.tools import capture_asian_fixings_tool, generate_asian_fixing_schedule_tool
from app.services.agents import DEEP_AGENT_TOOL_NAMES


def test_generate_tool_creates_one_event_per_average_date(session):
    pos_id, _ = _asian_position(session)
    out = generate_asian_fixing_schedule_tool.invoke({"position_id": pos_id})
    assert out["position_id"] == pos_id
    assert out["events_created"] >= 1


def test_capture_tool_snapshots_due_close(session):
    pos_id, _ = _asian_position(session)
    out = capture_asian_fixings_tool.invoke({"position_id": pos_id})
    assert out == {"position_id": pos_id, "captured": 1}
    # idempotent
    again = capture_asian_fixings_tool.invoke({"position_id": pos_id})
    assert again["captured"] == 0


def test_both_tools_registered_in_allowlist():
    assert "generate_asian_fixing_schedule" in DEEP_AGENT_TOOL_NAMES
    assert "capture_asian_fixings" in DEEP_AGENT_TOOL_NAMES
```

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py -v`
Expected: FAIL — ImportError (`capture_asian_fixings_tool` not defined).

- [ ] **Step 3: Add input schemas + tools** (`backend/app/tools/positions.py`, near the lifecycle tools)

```python
class GenerateAsianFixingScheduleInput(BaseModel):
    position_id: int | None = Field(default=None, description="Position.id of the Asian option.")
    source_trade_id: str | None = Field(default=None, description="Optional source trade id guard.")
    portfolio_id: int | None = Field(default=None, description="Optional portfolio guard.")


class CaptureAsianFixingsInput(BaseModel):
    position_id: int = Field(description="Position.id of the Asian option.")
    portfolio_id: int | None = Field(default=None, description="Optional portfolio guard; 404 on mismatch.")
    as_of: date | str | None = Field(default=None, description="Capture fixings on/before this date (default today).")
```

```python
@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_asian_fixing_schedule", args_schema=GenerateAsianFixingScheduleInput)
def generate_asian_fixing_schedule_tool(
    position_id: int | None = None,
    source_trade_id: str | None = None,
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    """Plant informational `fixing` lifecycle events from an Asian option's averaging schedule. Idempotent: re-running cancels prior active fixing events first."""
    count = positions_svc.generate_asian_fixing_schedule(
        position_id=position_id,
        source_trade_id=source_trade_id,
        portfolio_id=portfolio_id,
        actor="agent",
    )
    return {"position_id": position_id, "events_created": count}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("capture_asian_fixings", args_schema=CaptureAsianFixingsInput)
def capture_asian_fixings_tool(
    position_id: int,
    portfolio_id: int | None = None,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    """Snapshot the close price for every due (past, uncaptured) Asian fixing into the position's observation records. Idempotent; never overwrites an existing fixing."""
    captured = positions_svc.capture_due_asian_fixings(
        None,
        position_id,
        portfolio_id=portfolio_id,
        as_of=_parse_date(as_of),
    )
    return {"position_id": position_id, "captured": captured}
```
(Reuse the existing module-level `_parse_date` helper for `as_of`.)

- [ ] **Step 4: Export from `backend/app/tools/__init__.py`**

Add to the `from .positions import (` block:
```python
    capture_asian_fixings_tool,
    generate_asian_fixing_schedule_tool,
```
And add both **tool objects** (bare identifiers, not strings — this is the `QUANT_AGENT_TOOLS` list literal at the top of the file, NOT `__all__`) under the persisted-action / HITL-gated section (next to `settle_position_tool`):
```python
    generate_asian_fixing_schedule_tool,
    capture_asian_fixings_tool,
```

- [ ] **Step 5: Add to `DEEP_AGENT_TOOL_NAMES`** (`backend/app/services/agents.py`)

In the frozenset, next to `"settle_position"` / `"mark_knockout"`:
```python
        "generate_asian_fixing_schedule",
        "capture_asian_fixings",
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tools/positions.py backend/app/tools/__init__.py backend/app/services/agents.py tests/test_asian_fixing_tools.py
git commit -m "feat(asian): generate + capture fixing agent tools"
```

---

### Task 3: The `asian-fixings` skill + catalog coupling

**Files:**
- Create: `backend/app/skills/workflows/positions/asian-fixings/SKILL.md`
- Modify: `tests/test_skills_catalog_v2.py`, `tests/test_routing_table.py` (and any other count the new skill trips)
- Test: `tests/test_asian_fixing_tools.py` (skill well-formedness)

**Interfaces:**
- Consumes: tool names `get_asian_schedule`, `generate_asian_fixing_schedule`, `capture_asian_fixings`.

- [ ] **Step 1: Write the failing well-formedness test** (append to `tests/test_asian_fixing_tools.py`)

```python
def test_asian_fixings_skill_is_wellformed():
    import yaml
    from app.services.deep_agent.skills_paths import WORKFLOWS_DIR

    path = WORKFLOWS_DIR / "positions/asian-fixings/SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["name"] == "asian-fixings"
    assert fm["domain"] == "positions"
    assert fm["write_actions"] is True
    assert len(text) < 4000  # ~500-token body budget
```

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py::test_asian_fixings_skill_is_wellformed -v`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Create the skill** (`backend/app/skills/workflows/positions/asian-fixings/SKILL.md`)

```markdown
---
name: asian-fixings
description: Set up an Asian option's fixing calendar and lock in due fixings. Use when a user wants to generate the averaging-date fixing schedule for an Asian position, or to capture (snapshot) the close price for observation dates that have already passed so pricing uses the realized average.
domain: positions
workflow_type: write
allowed_envelopes:
  - desk_workflow
required_context:
  - portfolio_id
  - position_id
optional_context:
  - as_of
write_actions: true
confirmation_required: true
success_criteria:
  - the number of fixing events created is reported
  - the number of fixings captured is reported
  - any still-uncaptured past observation dates are surfaced
routing:
  - request: "Set up or refresh the Asian fixing calendar, or capture a due fixing for an Asian position"
    persona: trader
---

## When to use

- A user wants to generate the fixing-date calendar for an Asian (averaging) option.
- A user wants to lock in (capture) the realized close for observation dates that have already passed.
- Pricing of an Asian position looks coarse because past fixings were never captured.

## Required inputs

`position_id` and `portfolio_id` from page context or user text. Optional `as_of` limits capture to fixings on or before that date (default today).

## Procedure

1. Call `get_asian_schedule(position_id=<position_id>)` to read the averaging schedule and which observations already have a captured price.
2. Call `generate_asian_fixing_schedule(position_id=<position_id>, portfolio_id=<portfolio_id>)` to plant one informational `fixing` lifecycle event per averaging date. This is idempotent — re-running cancels prior active fixing events before re-creating them, so it is safe to refresh after a reschedule.
3. Call `capture_asian_fixings(position_id=<position_id>, portfolio_id=<portfolio_id>)` to snapshot the official close for every observation whose date has passed and is not yet captured. This is idempotent and never overwrites an existing fixing.
4. Report `events_created` and `captured`. Capture needs a `close` market quote on the underlying for each past date; if some remain uncaptured, tell the user — pricing falls back to a coarse uniform average until they are captured.

## Guardrails

- Both generate and capture are persisted writes; confirm before running on a live position.
- Never overwrite an already-captured fixing — captured prices are immutable realized observations.
```

- [ ] **Step 4: Run the well-formedness test**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_asian_fixing_tools.py::test_asian_fixings_skill_is_wellformed -v`
Expected: PASS.

- [ ] **Step 5: Run the catalog/routing suite to enumerate red counts**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_routing_table.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py -v`
Expected: failures on the exact-count assertions in `test_skills_catalog_v2.py` (`== 23`, `== 25`) and `test_routing_table.py` (`OLD_TABLE_ROWS` length). Read each failure message for the exact expected→actual.

- [ ] **Step 6: Fix each red count**

In `tests/test_skills_catalog_v2.py`: bump `== 23` → `== 24` and `== 25` → `== 26`; add `"asian-fixings"` to whichever exhaustive membership set the test asserts (only if it lists every skill — a subset/`>=` does not need it).
In `tests/test_routing_table.py`: append the new routing triple to `OLD_TABLE_ROWS` so `len(rows)` / `len(lines)` match. Match the exact tuple shape the existing rows use (read them first).
Do NOT pre-edit subset (`<=`) assertions — only edit what is actually red.

- [ ] **Step 7: Re-run the catalog/routing suite**

Run: same command as Step 5.
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/skills/workflows/positions/asian-fixings/SKILL.md tests/test_skills_catalog_v2.py tests/test_routing_table.py tests/test_asian_fixing_tools.py
git commit -m "feat(asian): asian-fixings routing skill + catalog count updates"
```

---

### Task 4: Full-suite regression + review gate

- [ ] **Step 1: Run the full backend suite**

Run: `PYTHONPATH=/Users/fuxinyao/oot-asian-tools/backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest -q`
Expected: exit 0 (note any pre-existing failures from memory and confirm they are unrelated).

- [ ] **Step 2: zenmux review the whole branch**

Run the zenmux-codex-review-loop with `--base main` (≤3 loops); fix every finding; re-review until clean.

- [ ] **Step 3: Fast-forward `main`**

After clean review + green suite, fast-forward only (never force-drop `main` history). `main` is not checked out anywhere (primary HEAD is the concurrent session's branch), so verify ancestry then move the ref:
```bash
git merge-base --is-ancestor main <tip> && git branch -f main <tip>   # ff-only: aborts if <tip> is not a descendant of main
```
Then remove the worktree and update memory.
