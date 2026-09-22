# Limit Incident Text Review (Jev site 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade every waiver rationale and comment thread on a limit incident with System One (Jev), store the read as a display-only row per `(event_id, kind)`, verify three cheap claims against the database, and surface the result on the Limits page Breaches tab.

**Architecture:** Two new modules under `backend/app/services/limits/` — `review.py` (questions, state builders, insert-or-select store, enqueue fast path, sweep) and `review_checks.py` (three deterministic checkers) — plus a shared `scopes.py` that both existing scope matchers adopt. Reviews are built from the immutable `limit_incident_events` row, never the incident's mutable columns; a `limit_incident_reviews` table (migration `0064`) keeps one row per event and kind. The REST routes and agent tools enqueue after commit; the limit-monitoring worker sweeps for anything missed. Reviews reach the UI through `LimitIncidentOut.reviews` and are pinned absent from the agent tools.

**Tech Stack:** FastAPI + pydantic v2, SQLAlchemy 2 ORM + Alembic (SQLite), `services/system_one/client.ask()`, `task_runner.submit_async_task`, React 19 / TypeScript / vitest with token-only CSS.

**Spec:** `docs/superpowers/specs/2026-09-21-limit-incident-review-design.md` (cited as **D*n***). Parent: `docs/superpowers/specs/2026-09-21-jev-system-one-design.md` (cited as **parent D*n***). Executors read both.

## Global Constraints

- **Branch base is `main` at or after `d3e2679`** (the System One merge `bc2d2a8` is in). The migration is `0064_limit_incident_reviews`, `down_revision = "0063_extracted_trade_family_check"`.
- **Live iff `OPEN_OTC_SYSTEM_ONE` AND `OPEN_OTC_LIMIT_REVIEW`** (default `true`). Either off ⇒ no call, no row (D15, parent D17).
- **Display-only (D5).** Nothing here changes an incident's status, blocks a waiver, reorders anything, or feeds the tool guard.
- **Built from the EVENT (D6).** State comes from `LimitIncidentEvent.payload` / `created_at`, never from `limit_incidents.waiver_rationale` or `last_evaluation_id`.
- **`client.ask()` is the only exit**; `questions` are module constants and are never rewritten (parent D16). Every question is tagged `untested`.
- **Module constants in `review.py`, exact values:** `review_rationale_chars = 4000`, `review_comment_chars = 600`, `review_thread_comments = 20`, `review_chip_min_p = 0.70`, `review_choice_min_p = 0.50`, `review_batch = 10`. Only the switch is a `Settings` field.
- **`claim_check ∈ {supported, no_evidence, unverified}`** — never "contradicted" (D11).
- **A `scored` row is never overwritten.** Checks are recomputed; Jev answers are not (D12).
- **Arena threads are never scored; "cannot tell" means skip** (D14).
- **Agent tools never carry reviews** (D13) — pinned by a test.
- **A waive, a comment, or a monitoring run can never fail because of a review.**
- **Migration rules:** idempotent (`_tables()` / `_indexes()` guards), migration-local Core only, no ORM import, downgrade drops the table.
- **Tests never reach the live POST** — conftest fails any that does; inject fakes from `tests/_system_one_fakes.py`. Run the backend suite with `.venv/bin/python -m pytest` from the worktree root; never `pytest | tail`.
- **Frontend:** token-only styling per `frontend/CLAUDE.md`; verify light + dark + compact; `cd frontend && npx tsc --noEmit && npm test`.
- **Before opening a PR:** `CHANGELOG.md` under `[Unreleased]`, `README.md` env row, `backend/app/services/system_one/CLAUDE.md` section.

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/config.py` | `limit_review_enabled` switch (both settings blocks) |
| `backend/app/services/thread_access.py` | `thread_is_arena(session, thread_id) -> bool \| None` — the shared, tri-state arena lookup |
| `backend/app/services/deep_agent/memory/queue.py` | `_is_arena_thread` delegates to the shared helper, keeps fail-open |
| `backend/app/services/limits/scopes.py` | **new** — `scope_key_for`, `scope_value`, `scope_matches`: the one definition of "in scope" |
| `backend/app/services/limits/monitoring.py` | `_resolve_scopes` uses `scopes.py`; `_finalize` calls `review.sweep` after commit |
| `backend/app/services/limits/sources.py` | `_risk_rows_for_scope` uses `scopes.scope_matches` |
| `backend/app/models.py` | `LimitIncidentReview` ORM model |
| `backend/alembic/versions/0064_limit_incident_reviews.py` | the table, idempotent |
| `backend/app/services/limits/review.py` | **new** — constants, `QUESTIONS`, state builders, store, `enqueue`, `score_event`, `sweep`, `latest_reviews` |
| `backend/app/services/limits/review_checks.py` | **new** — `ClaimCheck`, `CHECKERS`, three checkers, `run_check` |
| `backend/app/routers/limits.py` | enqueue after commit; `reviews` on every incident projection |
| `backend/app/schemas.py` | `LimitIncidentClaimOut`, `LimitIncidentReviewOut`, `LimitIncidentReviewsOut`, `LimitIncidentOut.reviews` |
| `backend/app/tools/limits.py` | enqueue after commit; projections unchanged |
| `frontend/src/types.ts`, `frontend/src/routes/Limits.tsx`, `frontend/src/routes/Limits.css` | the chips, the sortable `Rationale` column |
| `scripts/limit_review_probe.py`, `scripts/fixtures/limit_review_rationales.json` | the evidence probe (never on an app path) |
| `tests/_system_one_fakes.py` | `ReviewPost` — answers score + noul + choice in one request |
| `tests/test_limit_scopes.py`, `tests/test_thread_is_arena.py`, `tests/test_migration_0064_limit_incident_reviews.py`, `tests/test_limit_review_state.py`, `tests/test_limit_review_checks.py`, `tests/test_limit_review_store.py`, `tests/test_limit_review_wiring.py`, `tests/test_limit_review_api.py`, `tests/test_arena_purge_limit_reviews.py` | new tests |
| `tests/test_system_one_settings.py`, `tests/test_limits_tools.py`, `frontend/src/routes/Limits.live.test.tsx` | extended tests |

**Exact-set pins enumerated before starting** (`grep -rln "limit_incidents" tests/`): `test_arena_runner.py`, `test_arena_scoring.py`, `test_golden_workflow_fixtures.py`, `test_hitl.py`, `test_limit_incidents.py`, `test_limits_api.py`, `test_limits_models.py`, `test_limits_tools.py`, `test_migration_0046.py`, `test_migration_0047.py`, `test_migration_0048.py`, `test_risk_limit_breach_workflow.py`, `test_tool_guard_policy.py`. The only exact-dict pin on an HTTP body (`test_limits_api.py:547`) is on `LimitEvaluationOut`, not the incident, so adding `reviews` breaks none of them. The frontend `incident()` factory in `Limits.live.test.tsx:229` is untyped but is served as a `LimitIncident`; it gains `reviews` in Task 12.

---

### Task 1: Branch base and the feature switch

**Files:**
- Modify: `backend/app/config.py:268-271` (pydantic block) and `:425-428` (dataclass block) and `__post_init__` (`:476-484`)
- Test: `tests/test_system_one_settings.py`

**Interfaces:**
- Produces: `Settings.limit_review_enabled: bool` (env `OPEN_OTC_LIMIT_REVIEW`, default `True`).

- [ ] **Step 1: Bring the worktree to `main`**

```bash
git status --short            # only the untracked spec file is expected
git merge --ff-only main
git log --oneline -1          # expect d3e2679 (or newer)
ls backend/alembic/versions | tail -1   # expect 0063_extracted_trade_family_check.py
```

If `--ff-only` refuses, stop and report: this branch was expected to have no unique commits.

- [ ] **Step 2: Write the failing settings tests**

In `tests/test_system_one_settings.py`, add `"OPEN_OTC_LIMIT_REVIEW"` to `_VARS`, then extend the three existing tests and add one:

```python
def test_defaults_are_inert_and_match_the_spec():
    s = Settings()
    ...
    assert s.confirmation_family_check_enabled is True
    assert s.limit_review_enabled is True


def test_env_overrides(monkeypatch):
    ...
    monkeypatch.setenv("OPEN_OTC_LIMIT_REVIEW", "false")
    s = Settings()
    ...
    assert s.limit_review_enabled is False


def test_direct_construction_coerces_like_the_env_path():
    s = Settings(system_one_enabled="on", confirmation_family_check_enabled="0",
                 limit_review_enabled="0",
                 system_one_timeout_seconds="3", system_one_max_state_chars="99",
                 tool_guard_mode="bogus")
    ...
    assert s.limit_review_enabled is False


def test_limit_review_is_an_opt_out_under_the_master_switch():
    """Parent D15: default-enabled data class, opted out per feature."""
    assert Settings().limit_review_enabled is True
    assert Settings(limit_review_enabled="off").limit_review_enabled is False
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_system_one_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'limit_review_enabled'`.

- [ ] **Step 4: Add the field to both blocks and coerce it**

In `_EnvironmentSettings`, directly after `confirmation_family_check_enabled`:

```python
    # Limit incident text review (spec 2026-09-21-limit-incident-review D15):
    # display-only Jev reads of waiver rationales and comment threads.
    limit_review_enabled: bool = Field(
        True, validation_alias="OPEN_OTC_LIMIT_REVIEW"
    )
```

In `Settings`, directly after `confirmation_family_check_enabled`:

```python
    limit_review_enabled: bool = field(
        default_factory=lambda: _env_value("limit_review_enabled")
    )
```

In `Settings.__post_init__`, directly after the `confirmation_family_check_enabled` coercion:

```python
        object.__setattr__(
            self, "limit_review_enabled", _coerce_bool(self.limit_review_enabled)
        )
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_system_one_settings.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py tests/test_system_one_settings.py
git commit -m "feat(limits): OPEN_OTC_LIMIT_REVIEW switch (default on, under the master switch)"
```

---

### Task 2: Shared tri-state arena-thread lookup

**Files:**
- Modify: `backend/app/services/thread_access.py` (append)
- Modify: `backend/app/services/deep_agent/memory/queue.py:206-216`
- Test: `tests/test_thread_is_arena.py` (new); `tests/test_memory_queue_runjob.py` (existing, must keep passing)

**Interfaces:**
- Produces: `thread_is_arena(session: Session, thread_id: int | None) -> bool | None`. `False` for no thread id (a REST/desk action) or a non-arena thread; `True` for an arena thread; `None` when it cannot be told (no such row, or the lookup raised). Callers choose the fail direction.

- [ ] **Step 1: Write the failing tests**

`tests/test_thread_is_arena.py`:

```python
"""D14: one arena lookup, three answers; each caller picks its fail direction."""
from __future__ import annotations

import pytest

from app.models import AgentThread
from app.services.thread_access import thread_is_arena


@pytest.fixture
def threads(session):
    desk = AgentThread(title="desk", character="trader", source="desk")
    arena = AgentThread(title="[arena] risk-limit-breach-day · m", character="risk_manager",
                        source="arena", arena_run_id=1)
    session.add_all([desk, arena])
    session.commit()
    return desk.id, arena.id


def test_known_sources_answer_true_or_false(session, threads):
    desk_id, arena_id = threads
    assert thread_is_arena(session, arena_id) is True
    assert thread_is_arena(session, desk_id) is False


def test_no_thread_id_is_a_desk_action_not_unknown(session):
    assert thread_is_arena(session, None) is False


def test_a_missing_row_cannot_be_told(session):
    assert thread_is_arena(session, 999_999) is None


def test_a_failing_lookup_cannot_be_told():
    class Broken:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("db gone")

    assert thread_is_arena(Broken(), 5) is None


def test_memory_queue_stays_fail_open(session):
    """Extraction proceeds on None: the pre-existing behaviour of _is_arena_thread."""
    from app.services.deep_agent.memory.queue import MemoryQueue

    assert MemoryQueue._is_arena_thread(session, 999_999) is False
```

Check the class name before running: `grep -n "def _is_arena_thread" -B 30 backend/app/services/deep_agent/memory/queue.py | grep "^.*class "`. Use that class in the last test.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_thread_is_arena.py -v`
Expected: FAIL with `ImportError: cannot import name 'thread_is_arena'`.

- [ ] **Step 3: Implement the helper and delegate the queue to it**

Append to `backend/app/services/thread_access.py`:

```python
def thread_is_arena(session: Session, thread_id: int | None) -> bool | None:
    """Is `thread_id` an arena match's thread? True / False when known, None when
    it cannot be told (no such row, or the lookup itself failed).

    Tri-state on purpose (limit-review spec D14): memory extraction treats None
    as "proceed", the limit review treats None as "skip and retry next sweep".
    A thread id of None is a REST/desk action, so it is a known False.
    """
    if thread_id is None:
        return False
    try:
        thread = session.get(AgentThread, thread_id)
    except Exception:  # noqa: BLE001 — the caller decides what "unknown" means
        return None
    if thread is None:
        return None
    return thread.source == "arena"
```

Replace the body of `_is_arena_thread` in `backend/app/services/deep_agent/memory/queue.py`:

```python
    @staticmethod
    def _is_arena_thread(session, thread_id) -> bool:
        # Fail-open: "cannot tell" (None) means extraction proceeds.
        from app.services.thread_access import thread_is_arena

        return thread_is_arena(session, thread_id) is True
```

- [ ] **Step 4: Run to verify they pass, and that the queue's own tests still do**

Run: `.venv/bin/python -m pytest tests/test_thread_is_arena.py tests/test_memory_queue_runjob.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/thread_access.py backend/app/services/deep_agent/memory/queue.py tests/test_thread_is_arena.py
git commit -m "refactor(threads): shared tri-state thread_is_arena; memory queue keeps fail-open"
```

---

### Task 3: One definition of "in scope" (`limits/scopes.py`)

**Files:**
- Create: `backend/app/services/limits/scopes.py`
- Modify: `backend/app/services/limits/monitoring.py:185-260` (`_resolve_scopes`)
- Modify: `backend/app/services/limits/sources.py:231-266` (`_risk_rows_for_scope`)
- Test: `tests/test_limit_scopes.py` (new); `tests/test_limit_monitoring.py`, `tests/test_limit_sources.py`, `tests/test_limit_source_planner.py` (existing, must keep passing)

**Interfaces:**
- Produces:
  - `SCOPE_TYPES: tuple[str, ...] = ("portfolio", "underlying", "product_family", "position")`
  - `scope_key_for(scope_type: str, value: Any) -> str` — `"<type>:<value>"`, the format `_resolve_scopes` already mints.
  - `scope_value(scope_key: str) -> str | None` — the part after the first `:`; `None` for a bare legacy key such as `"portfolio"`.
  - `scope_matches(scope_type: str, value: Any, *, position_id: Any, underlying: Any, family: Any) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_limit_scopes.py`:

```python
"""The single scope-membership rule both monitoring and sources use (spec §Checkers)."""
from __future__ import annotations

import pytest

from app.services.limits.scopes import SCOPE_TYPES, scope_key_for, scope_matches, scope_value


def test_key_round_trip_matches_the_monitoring_format():
    assert scope_key_for("underlying", "000300") == "underlying:000300"
    assert scope_key_for("position", 7) == "position:7"
    assert scope_value("position:7") == "7"
    assert scope_value("underlying:A:B") == "A:B"      # only the FIRST colon splits
    assert scope_value("portfolio") is None             # legacy bare key


def test_unknown_scope_type_is_rejected():
    with pytest.raises(ValueError):
        scope_key_for("desk", 1)
    with pytest.raises(ValueError):
        scope_matches("desk", 1, position_id=1, underlying="x", family="y")


@pytest.mark.parametrize("scope_type, value, row, expected", [
    ("portfolio", "3", dict(position_id=1, underlying="000300", family="Snowball"), True),
    ("position", "7", dict(position_id=7, underlying="000300", family="Snowball"), True),
    ("position", "7", dict(position_id=8, underlying="000300", family="Snowball"), False),
    ("position", "7", dict(position_id=None, underlying="000300", family="Snowball"), False),
    ("underlying", "000300", dict(position_id=1, underlying="000300", family="x"), True),
    ("underlying", "000300", dict(position_id=1, underlying="510050", family="x"), False),
    ("underlying", "000300", dict(position_id=1, underlying=None, family="x"), False),
    ("product_family", "SnowballOption", dict(position_id=1, underlying="x", family="SnowballOption"), True),
    ("product_family", "SnowballOption", dict(position_id=1, underlying="x", family="PhoenixOption"), False),
])
def test_membership(scope_type, value, row, expected):
    assert scope_matches(scope_type, value, **row) is expected


def test_comparison_is_by_string_so_int_and_str_ids_agree():
    assert scope_matches("position", 7, position_id="7", underlying=None, family=None)
    assert SCOPE_TYPES == ("portfolio", "underlying", "product_family", "position")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_limit_scopes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.limits.scopes'`.

- [ ] **Step 3: Create the module**

`backend/app/services/limits/scopes.py`:

```python
"""The one definition of "this position is in that limit scope".

Two callers already matched scope membership independently — `monitoring._resolve_scopes`
over Position rows (it also mints `scope_key`) and `sources._risk_rows_for_scope` over
risk-run row dicts. The limit-review checkers need the same rule over Position rows at
check time, and a third copy would let a check disagree with the breach it is checking.
"""
from __future__ import annotations

from typing import Any

SCOPE_TYPES: tuple[str, ...] = ("portfolio", "underlying", "product_family", "position")


def _check_type(scope_type: str) -> None:
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"unsupported scope type {scope_type!r}")


def scope_key_for(scope_type: str, value: Any) -> str:
    """`<type>:<value>` — the key monitoring stores on evaluations and incidents."""
    _check_type(scope_type)
    return f"{scope_type}:{value}"


def scope_value(scope_key: str) -> str | None:
    """The value half of a scope key; None for a bare legacy key like `portfolio`."""
    _type, sep, value = str(scope_key).partition(":")
    return value if sep else None


def scope_matches(
    scope_type: str, value: Any, *, position_id: Any, underlying: Any, family: Any
) -> bool:
    """Does a row with these three attributes fall inside `scope_type:value`?

    Comparison is by `str` on both sides so an int id and its stored text agree.
    A missing attribute never matches (a row without an underlying is not "in"
    an underlying scope, whatever the value).
    """
    _check_type(scope_type)
    if scope_type == "portfolio":
        return True
    if scope_type == "position":
        return position_id is not None and value is not None and str(position_id) == str(value)
    if scope_type == "underlying":
        return underlying is not None and str(underlying) == str(value)
    return family is not None and str(family) == str(value)
```

- [ ] **Step 4: Run to verify the new tests pass**

Run: `.venv/bin/python -m pytest tests/test_limit_scopes.py -v`
Expected: PASS.

- [ ] **Step 5: Make `_resolve_scopes` mint keys and match through the helper**

In `backend/app/services/limits/monitoring.py` add the import near the other local imports (`from .scopes import scope_key_for, scope_matches`) and replace the body of `_resolve_scopes` so every f-string key and every membership comparison goes through the helper:

```python
def _resolve_scopes(
    version: RiskLimitVersion,
    *,
    portfolio: Portfolio,
    positions: list[Position],
) -> tuple[_ResolvedScope, ...]:
    config = dict(version.scope_config or {})
    by_id = {position.id: position for position in positions}

    def _members(scope_type: str, value: Any) -> tuple[int, ...]:
        return tuple(
            position.id
            for position in positions
            if scope_matches(
                scope_type, value,
                position_id=position.id,
                underlying=position.underlying,
                family=str(position.product_type),
            )
        )

    if version.scope_type == "portfolio":
        return (
            _ResolvedScope(
                "portfolio",
                scope_key_for("portfolio", portfolio.id),
                portfolio.name,
                tuple(sorted(by_id)),
                portfolio.id,
            ),
        )
    if version.scope_type == "position":
        ids = tuple(sorted(int(value) for value in config.get("position_ids") or []))
        ids = tuple(position_id for position_id in ids if position_id in by_id)
        return tuple(
            _ResolvedScope(
                "position",
                scope_key_for("position", position_id),
                f"Position {position_id}",
                (position_id,),
                position_id,
            )
            for position_id in ids
        )
    if version.scope_type == "underlying":
        symbols = config.get("symbols")
        values = (
            sorted({str(position.underlying) for position in positions})
            if config.get("all_in_portfolio") is True
            else sorted(str(value) for value in (symbols or []))
        )
        return tuple(
            _ResolvedScope("underlying", scope_key_for("underlying", value), value,
                           _members("underlying", value), value)
            for value in values
            if _members("underlying", value)
        )
    if version.scope_type == "product_family":
        families = config.get("families")
        values = (
            sorted({str(position.product_type) for position in positions})
            if config.get("all_in_portfolio") is True
            else sorted(str(value) for value in (families or []))
        )
        return tuple(
            _ResolvedScope("product_family", scope_key_for("product_family", value), value,
                           _members("product_family", value), value)
            for value in values
            if _members("product_family", value)
        )
    raise ValueError(f"unsupported scope type {version.scope_type!r}")
```

(`Any` is already imported in monitoring.py; confirm with `grep -n "^from typing" backend/app/services/limits/monitoring.py`.)

- [ ] **Step 6: Make `_risk_rows_for_scope` match through the helper**

In `backend/app/services/limits/sources.py` add `from .scopes import scope_matches` and replace `_risk_rows_for_scope`:

```python
def _risk_rows_for_scope(
    rows: list[dict[str, Any]],
    scope: ObservationScope,
) -> tuple[list[dict[str, Any]], tuple[int, ...] | None]:
    selected = list(rows)
    requested_ids = scope.position_ids
    if requested_ids is not None:
        wanted = set(requested_ids)
        selected = [row for row in selected if row.get("position_id") in wanted]
    value: Any = scope.value
    if scope.scope_type == "position":
        value = (
            int(scope.value)
            if scope.value is not None
            else requested_ids[0]
            if requested_ids
            else None
        )
        requested_ids = (value,) if value is not None else ()
    selected = [
        row
        for row in selected
        if scope_matches(
            scope.scope_type, value,
            position_id=row.get("position_id"),
            underlying=row.get("underlying"),
            family=row.get("product_family") or row.get("product_type"),
        )
    ]
    return selected, requested_ids
```

- [ ] **Step 7: Run the existing limits suites to prove no behaviour moved**

Run: `.venv/bin/python -m pytest tests/test_limit_monitoring.py tests/test_limit_sources.py tests/test_limit_source_planner.py tests/test_limit_monitoring_tasks.py tests/test_limits_api.py tests/test_limit_scopes.py -v`
Expected: all PASS. If a sources test fails on a row whose `underlying` is `None`, that row was previously matched by the `str(None) == "None"` accident; keep the new (correct) behaviour and adjust the test's fixture row to carry its underlying.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/limits/scopes.py backend/app/services/limits/monitoring.py backend/app/services/limits/sources.py tests/test_limit_scopes.py
git commit -m "refactor(limits): one scope-membership rule shared by monitoring and sources"
```

---

### Task 4: `LimitIncidentReview` model and migration 0064

**Files:**
- Modify: `backend/app/models.py` (insert after `LimitIncidentEvent`, ~line 2362)
- Create: `backend/alembic/versions/0064_limit_incident_reviews.py`
- Test: `tests/test_migration_0064_limit_incident_reviews.py` (new); `tests/test_migration_fresh_chain.py`, `tests/test_limits_models.py` (existing)

**Interfaces:**
- Produces: `app.models.LimitIncidentReview` with the columns of the spec's table and `UniqueConstraint("event_id", "kind", name="uq_limit_incident_reviews_event_kind")`.

- [ ] **Step 1: Write the failing migration test**

`tests/test_migration_0064_limit_incident_reviews.py`:

```python
"""Migration 0064 + the ORM model agree on the (event_id, kind) key (spec §Data model).

Drives the migration module directly against temp SQLite — never `head`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

_TABLE = "limit_incident_reviews"
_INDEXES = {"ix_limit_incident_reviews_incident_id", "ix_limit_incident_reviews_created_at"}
_COLUMNS = {
    "id", "incident_id", "event_id", "kind", "status", "unscored_reason",
    "rationale_grade", "rationale_confidence", "authority_only_p",
    "thread_state", "thread_state_p", "claims_json", "answers_json",
    "model", "latency_ms", "attempted_at", "created_at",
}


def _run(method: str, engine: sa.Engine) -> None:
    module = importlib.import_module("backend.alembic.versions.0064_limit_incident_reviews")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _engine(tmp_path: Path, name: str) -> sa.Engine:
    return sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def _insert(conn, event_id: int, kind: str) -> None:
    conn.execute(sa.text(
        "INSERT INTO limit_incident_reviews (incident_id, event_id, kind, status, "
        "claims_json, answers_json, created_at) VALUES (1, :e, :k, 'unscored', '[]', '{}', "
        "'2026-09-22 00:00:00')"), {"e": event_id, "k": kind})


def _assert_key_behaviour(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        _insert(conn, 10, "waiver")
        _insert(conn, 10, "thread")   # same event, other kind: a different review
        _insert(conn, 11, "waiver")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert(conn, 10, "waiver")


def test_upgrade_creates_table_indexes_and_the_unique_key(tmp_path):
    engine = _engine(tmp_path, "empty.sqlite3")
    _run("upgrade", engine)
    insp = inspect(engine)
    assert _TABLE in insp.get_table_names()
    assert {c["name"] for c in insp.get_columns(_TABLE)} == _COLUMNS
    assert _INDEXES <= {i["name"] for i in insp.get_indexes(_TABLE)}
    _assert_key_behaviour(engine)


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    """0001 materialises today's ORM, so the table already exists on a fresh chain."""
    from app.models import LimitIncidentReview

    engine = _engine(tmp_path, "fresh.sqlite3")
    LimitIncidentReview.__table__.create(bind=engine)
    before = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
    _run("upgrade", engine)
    assert {i["name"] for i in inspect(engine).get_indexes(_TABLE)} == before
    assert _INDEXES <= before


def test_the_orm_table_carries_the_same_key(tmp_path):
    from app.models import LimitIncidentReview

    engine = _engine(tmp_path, "orm.sqlite3")
    LimitIncidentReview.__table__.create(bind=engine)
    assert {c["name"] for c in inspect(engine).get_columns(_TABLE)} == _COLUMNS
    _assert_key_behaviour(engine)


def test_downgrade_drops_the_table(tmp_path):
    engine = _engine(tmp_path, "down.sqlite3")
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert _TABLE not in inspect(engine).get_table_names()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migration_0064_limit_incident_reviews.py -v`
Expected: FAIL with `ModuleNotFoundError` on the migration module.

- [ ] **Step 3: Add the ORM model**

In `backend/app/models.py`, immediately after the `LimitIncidentEvent` class:

```python
class LimitIncidentReview(Base):
    """System One's display-only read of ONE incident text event.

    Spec: docs/superpowers/specs/2026-09-21-limit-incident-review-design.md.
    One row per (event_id, kind): an incident can be waived, expire, reopen and
    be waived again, and each waiver's rationale keeps its own grade (D3). Built
    from the event, never from the incident's mutable columns (D6). A `scored`
    row is never overwritten; only its `claims_json` checks are recomputed (D12).
    """

    __tablename__ = "limit_incident_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("limit_incidents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("limit_incident_events.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)      # waiver | thread
    status: Mapped[str] = mapped_column(String(10), nullable=False)    # scored | unscored
    unscored_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rationale_grade: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    authority_only_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    thread_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_state_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    claims_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    answers_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("event_id", "kind", name="uq_limit_incident_reviews_event_kind"),
    )
```

- [ ] **Step 4: Write the migration**

`backend/alembic/versions/0064_limit_incident_reviews.py`:

```python
"""limit_incident_reviews — System One's display-only read of incident text

Revision ID: 0064_limit_incident_reviews
Revises: 0063_extracted_trade_family_check

One row per (event_id, kind) (spec 2026-09-21-limit-incident-review D3). event_id
is NOT NULL, so the NULL-distinct UNIQUE trap 0061 documents does not arise.

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already
has this table and its indexes. HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0064_limit_incident_reviews"
down_revision = "0063_extracted_trade_family_check"
branch_labels = None
depends_on = None

_TABLE = "limit_incident_reviews"
_IX_INCIDENT = "ix_limit_incident_reviews_incident_id"
_IX_CREATED = "ix_limit_incident_reviews_created_at"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("incident_id", sa.Integer(),
                      sa.ForeignKey("limit_incidents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_id", sa.Integer(),
                      sa.ForeignKey("limit_incident_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("unscored_reason", sa.String(40), nullable=True),
            sa.Column("rationale_grade", sa.Float(), nullable=True),
            sa.Column("rationale_confidence", sa.Float(), nullable=True),
            sa.Column("authority_only_p", sa.Float(), nullable=True),
            sa.Column("thread_state", sa.String(32), nullable=True),
            sa.Column("thread_state_p", sa.Float(), nullable=True),
            sa.Column("claims_json", sa.JSON(), nullable=False),
            sa.Column("answers_json", sa.JSON(), nullable=False),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("attempted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("event_id", "kind", name="uq_limit_incident_reviews_event_kind"),
        )
    existing = _indexes(_TABLE)
    if _IX_INCIDENT not in existing:
        op.create_index(_IX_INCIDENT, _TABLE, ["incident_id"])
    if _IX_CREATED not in existing:
        op.create_index(_IX_CREATED, _TABLE, ["created_at"])


def downgrade() -> None:
    if _TABLE in _tables():
        op.drop_table(_TABLE)
```

- [ ] **Step 5: Run the migration tests, the fresh-chain guard and the models pins**

Run: `.venv/bin/python -m pytest tests/test_migration_0064_limit_incident_reviews.py tests/test_migration_fresh_chain.py tests/test_limits_models.py -v`
Expected: all PASS. If `test_limits_models.py` pins a table list, add `limit_incident_reviews` to it.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0064_limit_incident_reviews.py tests/test_migration_0064_limit_incident_reviews.py tests/test_limits_models.py
git commit -m "feat(limits): limit_incident_reviews table (migration 0064, idempotent)"
```

---

### Task 5: `review.py` — constants, questions and state builders

**Files:**
- Create: `backend/app/services/limits/review.py` (this task writes the top half; Task 7 appends the store, enqueue, score and sweep)
- Test: `tests/test_limit_review_state.py`

**Interfaces:**
- Produces (all in `app.services.limits.review`):
  - constants `KIND_WAIVER = "waiver"`, `KIND_THREAD = "thread"`, `KINDS`, `EVENT_TYPE_FOR_KIND = {"waiver": "waived", "thread": "commented"}`, the six `review_*` values, `OUTAGE_REASONS`.
  - `RATIONALE_LEVELS: tuple[str, ...]` (5), `CLAIM_QUESTIONS: dict[str, Noul]` (6, in spec order), `AUTHORITY_ONLY_QUESTION: Noul`, `WAIVER_QUESTIONS: dict[str, Question]` (8 keys: `rationale_grade`, the six claims, `authority_only`), `THREAD_STATE_OPTIONS: dict[str, str]` (6), `THREAD_QUESTIONS: dict[str, Question]` (`thread_state`), `QUESTION_EVIDENCE: dict[str, str]`.
  - `is_live(settings: Settings | None = None) -> bool`
  - `floor_days(later: datetime, earlier: datetime) -> int`, `ceil_days(later, earlier) -> int`
  - `parse_iso(value: Any) -> datetime | None` (naive UTC)
  - `evaluation_as_of(session, incident, at: datetime) -> LimitEvaluation | None`
  - `status_as_of(events: Sequence[LimitIncidentEvent]) -> str`
  - `class ReviewStateTooLarge(ValueError)`
  - `build_waiver_state(session, incident, event) -> dict`, `build_thread_state(session, incident, event) -> dict`
  - `latest_event_id(incident: LimitIncident, kind: str) -> int | None`

- [ ] **Step 1: Write the failing tests**

`tests/test_limit_review_state.py`:

```python
"""State is built from the EVENT (D6), numbers are computed by code (spec §Predicates)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import LimitIncidentEvent
from app.services.deep_agent.tool_guard_policy import EVIDENCE_LEVELS
from app.services.limits import incidents, review
from app.services.limits.contracts import LimitActionContext
from app.services.system_one import validate_questions
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture, _next_run


def _open_incident(session, *, utilization=1.18):
    limit, version, run = _fixture(session)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW,
                             utilization=utilization)
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT,
        occurred_at=NOW)
    session.commit()
    return result.incidents[0], limit, version, run


def _waive(session, incident, *, rationale, at, days):
    incidents.waive(session, incident_id=incident.id, rationale=rationale,
                    expires_at=at + timedelta(days=days),
                    expected_row_version=incident.row_version, context=CONTEXT,
                    occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "waived"][-1]


def test_every_question_validates_and_is_tagged_untested():
    validate_questions(review.WAIVER_QUESTIONS)
    validate_questions(review.THREAD_QUESTIONS)
    assert list(review.WAIVER_QUESTIONS) == [
        "rationale_grade", "position_rolling_off", "data_error", "limit_under_review",
        "hedge_in_progress", "client_flow_expected", "market_reversion", "authority_only"]
    assert list(review.THREAD_QUESTIONS) == ["thread_state"]
    assert len(review.RATIONALE_LEVELS) == 5
    assert list(review.THREAD_STATE_OPTIONS) == [
        "disputes_number", "remediating", "requests_limit_change", "requests_more_time",
        "root_cause_only", "no_position"]
    assert set(review.QUESTION_EVIDENCE) == set(review.WAIVER_QUESTIONS) | set(review.THREAD_QUESTIONS)
    assert set(review.QUESTION_EVIDENCE.values()) == {"untested"} <= EVIDENCE_LEVELS


def test_thresholds_are_the_spec_values():
    assert (review.review_rationale_chars, review.review_comment_chars,
            review.review_thread_comments, review.review_chip_min_p,
            review.review_choice_min_p, review.review_batch) == (4000, 600, 20, 0.70, 0.50, 10)
    assert review.OUTAGE_REASONS == frozenset({"no_key", "timeout", "http_error"})


def test_day_arithmetic_floors_open_days_and_ceils_duration():
    t0 = datetime(2026, 9, 1, 9, 0)
    assert review.floor_days(t0 + timedelta(days=3, hours=23), t0) == 3
    assert review.ceil_days(t0 + timedelta(days=11, hours=1), t0) == 12
    assert review.ceil_days(t0 + timedelta(days=12), t0) == 12
    assert review.floor_days(t0 - timedelta(hours=1), t0) == 0   # clamps, never negative


def test_waiver_state_is_read_from_the_event(session):
    incident, limit, version, run = _open_incident(session)
    first = _waive(session, incident, rationale="stale mark", at=NOW + timedelta(days=3), days=12)
    # Expire, re-open and re-waive: the incident's columns now hold the SECOND rationale.
    context = LimitActionContext(actor="monitor", persona=None, mode="auto")
    later_run = _next_run(session, run, at=NOW + timedelta(days=20))
    later = _evaluation(session, version, later_run, status="breach",
                        at=NOW + timedelta(days=20), utilization=1.30)
    incidents.reconcile_monitoring_incidents(session, monitoring_run=later_run,
                                             evaluations=[later], context=context,
                                             occurred_at=NOW + timedelta(days=20))
    session.commit()
    session.refresh(incident)
    _waive(session, incident, rationale="second reason", at=NOW + timedelta(days=21), days=5)
    session.refresh(incident)
    assert incident.waiver_rationale == "second reason"

    state = review.build_waiver_state(session, incident, first)
    assert state["waiver"] == {"rationale": "stale mark", "duration_days": 12}
    assert state["breach"] == {"severity": "breach", "utilization": 1.18, "days_open": 3}
    assert state["limit"] == {"name": limit.name, "metric_kind": "delta",
                              "unit": "underlying_units", "scope_type": "position",
                              "scope_label": "Position 7"}
    # No author identity anywhere in the state (D16).
    assert "actor" not in str(state) and "persona" not in str(state)


def test_waiver_state_severity_is_as_of_the_event_not_of_scoring(session):
    """The evaluation stamped on the latest event at or before the waive (D6)."""
    incident, _limit, version, run = _open_incident(session, utilization=1.10)
    waived = _waive(session, incident, rationale="r", at=NOW + timedelta(hours=1), days=3)
    later_run = _next_run(session, run, at=NOW + timedelta(days=1))
    later = _evaluation(session, version, later_run, status="breach",
                        at=NOW + timedelta(days=1), utilization=1.50)
    incidents.reconcile_monitoring_incidents(
        session, monitoring_run=later_run, evaluations=[later],
        context=LimitActionContext(actor="monitor", persona=None, mode="auto"),
        occurred_at=NOW + timedelta(days=1))
    session.commit()
    assert review.build_waiver_state(session, incident, waived)["breach"]["utilization"] == 1.10


def test_rationale_over_the_cap_is_rejected_never_truncated(session):
    incident, *_ = _open_incident(session)
    waived = _waive(session, incident, rationale="x" * (review.review_rationale_chars + 1),
                    at=NOW + timedelta(hours=1), days=3)
    with pytest.raises(review.ReviewStateTooLarge):
        review.build_waiver_state(session, incident, waived)


def test_thread_state_takes_the_last_twenty_comments_cut_to_600(session):
    incident, limit, *_ = _open_incident(session)
    at = NOW
    for i in range(25):
        at = at + timedelta(minutes=1)
        session.refresh(incident)
        incidents.comment(session, incident_id=incident.id, comment=f"c{i} " + "y" * 700,
                          expected_row_version=incident.row_version, context=CONTEXT,
                          occurred_at=at)
        session.commit()
    session.refresh(incident)
    comments = [e for e in incident.events if e.event_type == "commented"]
    state = review.build_thread_state(session, incident, comments[-1])
    assert len(state["comments"]) == 20
    assert state["comments"][0]["text"].startswith("c5 ")
    assert all(len(c["text"]) == 600 and c["text"].endswith("…") for c in state["comments"])
    assert state["comments"][0] == {"at": comments[5].created_at.isoformat(),
                                    "actor": "alice", "text": state["comments"][0]["text"]}
    assert state["incident"] == {"severity": "breach", "status": "open", "days_open": 0}
    assert state["limit"] == {"name": limit.name, "scope_label": "Position 7"}


def test_thread_state_for_an_older_comment_stops_at_that_comment(session):
    incident, *_ = _open_incident(session)
    incidents.comment(session, incident_id=incident.id, comment="first",
                      expected_row_version=incident.row_version, context=CONTEXT,
                      occurred_at=NOW + timedelta(minutes=1))
    session.commit()
    session.refresh(incident)
    incidents.acknowledge(session, incident_id=incident.id,
                          expected_row_version=incident.row_version, context=CONTEXT,
                          occurred_at=NOW + timedelta(minutes=2))
    session.commit()
    session.refresh(incident)
    incidents.comment(session, incident_id=incident.id, comment="second",
                      expected_row_version=incident.row_version, context=CONTEXT,
                      occurred_at=NOW + timedelta(minutes=3))
    session.commit()
    session.refresh(incident)
    first, second = [e for e in incident.events if e.event_type == "commented"]
    assert [c["text"] for c in review.build_thread_state(session, incident, first)["comments"]] == ["first"]
    assert review.build_thread_state(session, incident, first)["incident"]["status"] == "open"
    assert review.build_thread_state(session, incident, second)["incident"]["status"] == "acknowledged"


def test_status_as_of_replays_the_timeline():
    def ev(event_type):
        return LimitIncidentEvent(event_type=event_type, actor="a", payload={})
    assert review.status_as_of([ev("opened")]) == "open"
    assert review.status_as_of([ev("opened"), ev("waived"), ev("assigned")]) == "waived"
    assert review.status_as_of([ev("opened"), ev("waived"), ev("waiver_expired")]) == "open"
    assert review.status_as_of([ev("opened"), ev("resolved"), ev("reopened")]) == "open"
    assert review.status_as_of([ev("opened"), ev("recovered")]) == "recovered"


def test_latest_event_id_picks_the_newest_of_the_kind(session):
    incident, *_ = _open_incident(session)
    assert review.latest_event_id(incident, review.KIND_WAIVER) is None
    first = _waive(session, incident, rationale="a", at=NOW + timedelta(hours=1), days=1)
    assert review.latest_event_id(incident, review.KIND_WAIVER) == first.id
    assert review.latest_event_id(incident, review.KIND_THREAD) is None
```

`test_limit_incidents.py` already defines `CONTEXT`, `NOW`, `_fixture`, `_evaluation`, `_next_run` (the `tests/` directory is on `sys.path`, which is how `from _system_one_fakes import …` works). Confirm `_fixture` returns `(limit, version, run)` and that `_evaluation` accepts `utilization=` before running.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_limit_review_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'review'`.

- [ ] **Step 3: Write the top half of `review.py`**

`backend/app/services/limits/review.py`:

```python
"""Limit incident text review — Jev site 4 (spec 2026-09-21-limit-incident-review).

Three reads of the text on a limit incident: a waiver rationale's completeness
grade, the claims it makes (three of which are checked against the database),
and the state a comment thread leaves the incident in. DISPLAY-ONLY (D5): a
review changes no status, blocks no waiver, and is invisible to the agent (D13).

Everything is built from the immutable `limit_incident_events` row (D6) and
stored once per (event_id, kind) in `limit_incident_reviews` (D3). Jev is the
only source of probabilities; every number in `state` is computed here.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import database
from ...config import Settings, get_settings
from ...models import (
    AgentThread,
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitIncidentReview,
    RiskLimit,
    RiskLimitVersion,
    utcnow,
)
from ..system_one import (
    Answer,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    SystemOneUnavailable,
    ask,
    cap,
    is_enabled,
)
from ..task_runner import submit_async_task
from ..thread_access import thread_is_arena
from .review_checks import CLAIMS_WITH_CHECKERS, ClaimCheck, run_check

logger = logging.getLogger(__name__)

KIND_WAIVER = "waiver"
KIND_THREAD = "thread"
KINDS: tuple[str, ...] = (KIND_WAIVER, KIND_THREAD)
EVENT_TYPE_FOR_KIND: dict[str, str] = {KIND_WAIVER: "waived", KIND_THREAD: "commented"}

#: Module constants (the parent's MemoryConfig precedent); only the switch is a Setting.
review_rationale_chars = 4000
review_comment_chars = 600
review_thread_comments = 20
review_chip_min_p = 0.70     # provisional: between the parent's HIGH and low bands
review_choice_min_p = 0.50   # the parent's confirmations site
review_batch = 10

#: The service is unreachable for EVERY row: the first ends the batch (keep-alive rule).
OUTAGE_REASONS = frozenset({"no_key", "timeout", "http_error"})

# --- questions (developer-authored constants; never sanitized, never rewritten) ----

RATIONALE_LEVELS: tuple[str, ...] = (
    "No reason is given: the text is filler, restates that the limit is breached, or "
    "only says the waiver was requested or approved",
    "A cause is asserted with nothing checkable — \"market move\", \"temporary\", "
    "\"known issue\" — and no position, trade, event or date is named",
    "A specific cause is named (a position, trade, market event or data problem), but "
    "nothing says what will bring the exposure back inside the limit",
    "A specific cause and a specific remediation are named, but with no owner or date "
    "— or the remediation lands after the waiver expires (compare any date in the text "
    "with `waiver.duration_days`)",
    "A specific cause, a specific remediation, who will do it, and a date on or before "
    "the waiver's expiry",
)
RATIONALE_QUESTION = Score(
    instructions=(
        "Which situation best describes the waiver rationale in `waiver.rationale`, "
        "read against the breach in `breach` and the limit in `limit`? The waiver "
        "lasts `waiver.duration_days` days from the day it was written."
    ),
    criteria=RATIONALE_LEVELS,
)

_CLAIM = "The rationale in `waiver.rationale` claims that "
CLAIM_QUESTIONS: dict[str, Noul] = {
    "position_rolling_off": Noul(_CLAIM + "the exposure will fall on its own because a "
                                 "position expires, matures, knocks out or settles"),
    "data_error": Noul(_CLAIM + "the breach comes from a wrong, stale or missing market "
                       "input or a failed risk run, not from real exposure"),
    "limit_under_review": Noul(_CLAIM + "the limit itself is mis-sized or is being changed"),
    "hedge_in_progress": Noul(_CLAIM + "a hedge or unwind trade has been ordered or is "
                              "being executed"),
    "client_flow_expected": Noul(_CLAIM + "an expected client trade or unwind will reduce "
                                 "the exposure"),
    "market_reversion": Noul(_CLAIM + "the exposure will return inside the limit because "
                             "the market will move back"),
}
AUTHORITY_ONLY_QUESTION = Noul(
    "The only justification given in `waiver.rationale` is that a trader, a manager or "
    "another person asked for or approved the waiver"
)
WAIVER_QUESTIONS: dict[str, Question] = {
    "rationale_grade": RATIONALE_QUESTION,
    **CLAIM_QUESTIONS,
    "authority_only": AUTHORITY_ONLY_QUESTION,
}

THREAD_STATE_OPTIONS: dict[str, str] = {
    "disputes_number": "The desk says the breach figure is wrong. The figure is contested; "
                       "no error has been confirmed",
    "remediating": "The desk accepts the breach and describes action taken or under way",
    "requests_limit_change": "The desk argues the limit should change rather than the exposure",
    "requests_more_time": "The desk asks for a waiver or more time without describing remediation",
    "root_cause_only": "The comments explain why the breach happened and say nothing about "
                       "what happens next",
    "no_position": "The comments are administrative (assignment, FYI); none takes a position",
}
THREAD_QUESTIONS: dict[str, Question] = {
    "thread_state": Choice(
        instructions=(
            "Which option best describes where the comment thread in `comments` (oldest "
            "first) leaves the incident described in `incident` and `limit`?"
        ),
        criteria=THREAD_STATE_OPTIONS,
    ),
}

#: Honesty marker per question (parent's EVIDENCE_LEVELS). Every one starts untested;
#: a key moves to "tested-posthoc" only by an edit that cites a probe run.
QUESTION_EVIDENCE: dict[str, str] = {
    key: "untested" for key in (*WAIVER_QUESTIONS, *THREAD_QUESTIONS)
}

#: Short labels the UI and the probe share, keyed as the questions are.
RATIONALE_LEVEL_LABELS: tuple[str, ...] = (
    "no reason", "cause not checkable", "cause, no remediation",
    "remediation, no owner/date", "complete",
)


def is_live(settings: Settings | None = None) -> bool:
    """Live iff the master switch AND OPEN_OTC_LIMIT_REVIEW (D15)."""
    cfg = settings or get_settings()
    return bool(cfg.limit_review_enabled) and is_enabled(cfg)


# --- arithmetic (code computes, Jev reads) -----------------------------------------

def floor_days(later: datetime, earlier: datetime) -> int:
    return max(0, int((later - earlier).total_seconds() // 86400))


def ceil_days(later: datetime, earlier: datetime) -> int:
    return max(0, math.ceil((later - earlier).total_seconds() / 86400))


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 text -> naive UTC datetime (the event payload stores isoformat())."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


# --- as-of lookups (D6) --------------------------------------------------------------

def evaluation_as_of(session: Session, incident: LimitIncident, at: datetime) -> LimitEvaluation | None:
    """The evaluation on the incident's latest evaluation-stamped event at or before `at`.

    NOT `incident.last_evaluation_id`: that keeps moving after the waiver, and a late
    retry would grade the rationale against a breach its author never saw.
    """
    event = session.scalar(
        select(LimitIncidentEvent)
        .where(
            LimitIncidentEvent.incident_id == incident.id,
            LimitIncidentEvent.evaluation_id.is_not(None),
            LimitIncidentEvent.created_at <= at,
        )
        .order_by(LimitIncidentEvent.created_at.desc(), LimitIncidentEvent.id.desc())
    )
    if event is None:
        return None
    return session.get(LimitEvaluation, event.evaluation_id)


_STATUS_AFTER: dict[str, str] = {
    "opened": "open", "acknowledged": "acknowledged", "assigned": "assigned",
    "waived": "waived", "waiver_expired": "open", "recovered": "recovered",
    "resolved": "resolved", "reopened": "open",
}


def status_as_of(events: Sequence[LimitIncidentEvent]) -> str:
    """Replay the timeline: the incident's status right after the last event given.
    Mirrors incidents.py, including that assigning a waived incident keeps it waived."""
    status = "open"
    for event in events:
        if event.event_type == "assigned" and status == "waived":
            continue
        status = _STATUS_AFTER.get(event.event_type, status)
    return status


def _ordered_events(incident: LimitIncident) -> list[LimitIncidentEvent]:
    return sorted(incident.events, key=lambda e: (e.created_at, e.id))


def _events_up_to(incident: LimitIncident, event: LimitIncidentEvent) -> list[LimitIncidentEvent]:
    return [e for e in _ordered_events(incident) if (e.created_at, e.id) <= (event.created_at, event.id)]


def latest_event_id(incident: LimitIncident, kind: str) -> int | None:
    wanted = EVENT_TYPE_FOR_KIND[kind]
    ids = [e.id for e in incident.events if e.event_type == wanted and e.id is not None]
    return max(ids) if ids else None


# --- state builders ------------------------------------------------------------------

class ReviewStateTooLarge(ValueError):
    """The text is over its cap; it is rejected, never clipped (spec §Waiver request)."""


def _limit_block(session: Session, incident: LimitIncident, evaluation: LimitEvaluation | None) -> dict[str, Any]:
    limit = session.get(RiskLimit, incident.risk_limit_id)
    version: RiskLimitVersion | None = None
    if evaluation is not None:
        version = session.get(RiskLimitVersion, evaluation.limit_version_id)
    if version is None and limit is not None and limit.active_version_id is not None:
        version = session.get(RiskLimitVersion, limit.active_version_id)
    return {
        "name": limit.name if limit is not None else None,
        "metric_kind": version.metric_kind if version is not None else None,
        "unit": version.unit if version is not None else None,
        "scope_type": incident.scope_type,
        "scope_label": incident.scope_label,
    }


def build_waiver_state(session: Session, incident: LimitIncident, event: LimitIncidentEvent) -> dict[str, Any]:
    payload = dict(event.payload or {})
    rationale = str(payload.get("rationale") or "")
    if len(rationale) > review_rationale_chars:
        raise ReviewStateTooLarge(f"rationale {len(rationale)} > {review_rationale_chars} chars")
    expires = parse_iso(payload.get("waiver_expires_at"))
    evaluation = evaluation_as_of(session, incident, event.created_at)
    return {
        "limit": _limit_block(session, incident, evaluation),
        "breach": {
            "severity": evaluation.status if evaluation is not None else None,
            "utilization": evaluation.utilization if evaluation is not None else None,
            "days_open": floor_days(event.created_at, incident.first_seen_at),
        },
        "waiver": {
            "rationale": rationale,
            "duration_days": ceil_days(expires, event.created_at) if expires is not None else None,
        },
    }


def build_thread_state(session: Session, incident: LimitIncident, event: LimitIncidentEvent) -> dict[str, Any]:
    up_to = _events_up_to(incident, event)
    comments = [e for e in up_to if e.event_type == "commented"][-review_thread_comments:]
    evaluation = evaluation_as_of(session, incident, event.created_at)
    limit = session.get(RiskLimit, incident.risk_limit_id)
    return {
        "limit": {"name": limit.name if limit is not None else None,
                  "scope_label": incident.scope_label},
        "incident": {
            "severity": evaluation.status if evaluation is not None else None,
            "status": status_as_of(up_to),
            "days_open": floor_days(event.created_at, incident.first_seen_at),
        },
        "comments": [
            {
                "at": e.created_at.isoformat(),
                "actor": e.actor,
                "text": cap(str((e.payload or {}).get("comment") or ""), review_comment_chars),
            }
            for e in comments
        ],
    }
```

Task 7 appends the rest of the module. Until then, the `from .review_checks import …` line will fail at import — so **Task 6 (review_checks.py) must land before this file is importable**. Order the work: write this file, then Task 6, then run this task's tests.

- [ ] **Step 4: Do Task 6 now, then run these tests**

Run: `.venv/bin/python -m pytest tests/test_limit_review_state.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit (together with Task 6)**

```bash
git add backend/app/services/limits/review.py backend/app/services/limits/review_checks.py tests/test_limit_review_state.py tests/test_limit_review_checks.py
git commit -m "feat(limits): review questions, event-built state and the three claim checkers"
```

---

### Task 6: `review_checks.py` — the three deterministic checkers

**Files:**
- Create: `backend/app/services/limits/review_checks.py`
- Test: `tests/test_limit_review_checks.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ClaimCheck(check: str, detail: str, checked_at: datetime)` with `as_json() -> dict`.
  - `CheckFn = Callable[[Session, LimitIncident, LimitIncidentEvent], ClaimCheck]`
  - `CHECKERS: dict[str, CheckFn]` with keys `position_rolling_off`, `data_error`, `limit_under_review`; `CLAIMS_WITH_CHECKERS: frozenset[str]`.
  - `run_check(session, claim: str, incident, event, *, now: datetime) -> ClaimCheck` — `unverified` for a claim without a checker or a checker that raises; stamps `checked_at = now`.
- Consumes: `scopes.scope_matches`, `scopes.scope_value`; `review.parse_iso` is NOT used here (avoid the circular import) — this module has its own `_parse_iso`.

- [ ] **Step 1: Write the failing tests**

`tests/test_limit_review_checks.py` — rows are booked through the real services (`positions`/`position_terms` for expiry, `definitions` for limit versions, `reconcile_monitoring_incidents` for incidents):

```python
"""Checkers say supported / no_evidence / unverified — never contradicted (D11)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models import LimitSourceReference, Position
from app.services.domains import position_terms
from app.services.limits import definitions, incidents, review_checks
from app.services.limits.contracts import LimitActionContext, LimitVersionSpec
from app.services.limits.review_checks import CHECKERS, ClaimCheck, run_check
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture


def _incident(session, *, evaluation_kwargs=None):
    limit, version, run = _fixture(session)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW, utilization=1.2)
    for key, value in (evaluation_kwargs or {}).items():
        setattr(evaluation, key, value)
    session.flush()
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT, occurred_at=NOW)
    session.commit()
    incident = result.incidents[0]
    incidents.waive(session, incident_id=incident.id, rationale="r",
                    expires_at=NOW + timedelta(days=10), expected_row_version=incident.row_version,
                    context=CONTEXT, occurred_at=NOW + timedelta(hours=1))
    session.commit()
    session.refresh(incident)
    waived = [e for e in incident.events if e.event_type == "waived"][-1]
    return incident, waived, limit, version, run, evaluation


def _book_option(session, portfolio_id, *, position_id=None, underlying="000300", expiry: date):
    position = Position(id=position_id, portfolio_id=portfolio_id, underlying=underlying,
                        product_type="EuropeanVanillaOption",
                        product_kwargs={"strike": 100.0, "option_type": "call",
                                        "expiry_date": expiry.isoformat()},
                        quantity=1.0, status="open")
    session.add(position)
    session.flush()
    position_terms.upsert_position_term_rows(session, position)   # the real expiry writer
    session.commit()
    return position


def test_position_rolling_off_supported_when_a_scoped_position_expires_in_window(session):
    incident, waived, *_ = _incident(session)          # scope position:7
    _book_option(session, incident.portfolio_id, position_id=7, expiry=NOW.date() + timedelta(days=5))
    result = CHECKERS["position_rolling_off"](session, incident, waived)
    assert result.check == "supported"
    assert result.detail == f"1 position in scope expires on or before {(NOW + timedelta(days=10)).date().isoformat()}"


def test_position_rolling_off_ignores_positions_outside_scope_or_window(session):
    incident, waived, *_ = _incident(session)
    _book_option(session, incident.portfolio_id, position_id=8, expiry=NOW.date() + timedelta(days=5))   # not position 7
    _book_option(session, incident.portfolio_id, position_id=7, expiry=NOW.date() + timedelta(days=40))  # after expiry
    result = CHECKERS["position_rolling_off"](session, incident, waived)
    assert result.check == "no_evidence"
    assert "no open position in scope expires between" in result.detail


def test_data_error_supported_on_a_reason_code_or_coverage_gap_or_stale_source(session):
    incident, waived, *_ = _incident(session, evaluation_kwargs={"coverage_ratio": 0.8})
    result = CHECKERS["data_error"](session, incident, waived)
    assert result.check == "supported" and "coverage 0.80" in result.detail

    incident2, waived2, _l, _v, run2, evaluation2 = _incident(session)
    ref = LimitSourceReference(monitoring_run_id=run2.id, source_kind="risk_run",
                               source_status="completed", is_fresh=False,
                               completeness_diagnostics={})
    session.add(ref)
    session.flush()
    evaluation2.evidence = {**(evaluation2.evidence or {}), "source_reference_id": ref.id}
    session.commit()
    result2 = CHECKERS["data_error"](session, incident2, waived2)
    assert result2.check == "supported" and "outside the freshness policy" in result2.detail


def test_data_error_no_evidence_on_a_clean_evaluation(session):
    incident, waived, *_ = _incident(session)
    result = CHECKERS["data_error"](session, incident, waived)
    assert result.check == "no_evidence"
    assert result.detail == "1 evaluation behind this incident carries no reason code, coverage gap or stale source"


def test_limit_under_review_supported_by_a_version_created_after_the_incident_opened(session):
    incident, waived, limit, version, *_ = _incident(session)
    assert CHECKERS["limit_under_review"](session, incident, waived).check == "no_evidence"
    definitions.create_version(
        session, limit_id=limit.id, spec=LimitVersionSpec(
            metric_kind="delta", source_kind="risk_run", scope_type="position",
            scope_config={"position_ids": [7]}, aggregation="net", transform="absolute",
            comparator="upper", warning_upper=120.0, hard_upper=150.0, unit="underlying_units"),
        expected_row_version=limit.row_version, context=CONTEXT)
    session.commit()
    result = CHECKERS["limit_under_review"](session, incident, waived)
    assert result.check == "supported"
    assert result.detail.startswith("limit version 2 (draft) was created on ")


def test_run_check_degrades_a_raising_checker_and_an_unknown_claim_to_unverified(session, monkeypatch):
    incident, waived, *_ = _incident(session)
    now = datetime(2026, 9, 22, 10, 0)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setitem(CHECKERS, "position_rolling_off", boom)
    assert run_check(session, "position_rolling_off", incident, waived, now=now) == ClaimCheck(
        "unverified", "check failed", now)
    assert run_check(session, "hedge_in_progress", incident, waived, now=now) == ClaimCheck(
        "unverified", "no checker for this claim", now)
    assert run_check(session, "limit_under_review", incident, waived, now=now).checked_at == now


def test_as_json_shape():
    now = datetime(2026, 9, 22, 10, 0)
    assert ClaimCheck("supported", "d", now).as_json() == {
        "check": "supported", "detail": "d", "checked_at": "2026-09-22T10:00:00"}
```

Before running, confirm the `definitions.create_version` signature (`grep -n "def create_version" -A 8 backend/app/services/limits/definitions.py`) and adjust the keyword names in the test to match it exactly; the version number it mints for the second version must be `2`. Confirm `Position` requires no other non-null column by checking `test_limits_tools.py`'s or `test_limit_monitoring.py`'s position seeding and copy any extra required fields.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_limit_review_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.limits.review_checks'`.

- [ ] **Step 3: Write the module**

`backend/app/services/limits/review_checks.py`:

```python
"""Deterministic checks of the claims a waiver rationale makes (spec §Checkers, D2).

Read-only. `detail` is one sentence built from database facts, never from the
rationale. A check can say a claim is `supported` or that this database holds
`no_evidence` for it — never that it is false (D11): the review may be happening
by email. A checker that raises degrades that one claim to `unverified`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitSourceReference,
    OptionCoreTerm,
    Position,
    RiskLimitVersion,
)
from .scopes import scope_matches, scope_value

logger = logging.getLogger(__name__)

SUPPORTED = "supported"
NO_EVIDENCE = "no_evidence"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ClaimCheck:
    check: str
    detail: str
    checked_at: datetime

    def as_json(self) -> dict[str, Any]:
        return {"check": self.check, "detail": self.detail,
                "checked_at": self.checked_at.isoformat()}


CheckFn = Callable[[Session, LimitIncident, LimitIncidentEvent], ClaimCheck]


def _now() -> datetime:
    return datetime.utcnow()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def check_position_rolling_off(session: Session, incident: LimitIncident, event: LimitIncidentEvent) -> ClaimCheck:
    """Open positions in the incident's scope whose materialised expiry
    (`option_core_terms.expiry_date`, written by position_terms) falls inside
    [event.created_at, waiver_expires_at]. Non-option products carry no term row
    and are therefore never counted."""
    expires = _parse_iso((event.payload or {}).get("waiver_expires_at"))
    if expires is None:
        return ClaimCheck(UNVERIFIED, "the waived event carries no waiver_expires_at", _now())
    start, end = event.created_at.date(), expires.date()
    value = scope_value(incident.scope_key)
    rows = session.execute(
        select(Position.id, Position.underlying, Position.product_type)
        .join(OptionCoreTerm, OptionCoreTerm.position_id == Position.id)
        .where(
            Position.portfolio_id == incident.portfolio_id,
            Position.status == "open",
            OptionCoreTerm.expiry_date.is_not(None),
            OptionCoreTerm.expiry_date >= start,
            OptionCoreTerm.expiry_date <= end,
        )
    ).all()
    hits = [
        row for row in rows
        if scope_matches(incident.scope_type, value, position_id=row.id,
                         underlying=row.underlying, family=str(row.product_type))
    ]
    if hits:
        return ClaimCheck(
            SUPPORTED,
            f"{_plural(len(hits), 'position')} in scope expire on or before {end.isoformat()}",
            _now(),
        )
    return ClaimCheck(
        NO_EVIDENCE,
        f"no open position in scope expires between {start.isoformat()} and {end.isoformat()}",
        _now(),
    )


def check_data_error(session: Session, incident: LimitIncident, event: LimitIncidentEvent) -> ClaimCheck:
    """The evaluations this incident's events reference up to the scored event,
    plus first_evaluation_id. Evidence keys read from evaluator.py / monitoring.py:
    `reason_code` (evaluator._preflight_reason), `coverage_ratio`, and in `evidence`
    the `source_reference_id` monitoring stamps, plus `is_fresh`,
    `missing_market_evidence` and `missing_fx` from sources.py."""
    ids = set(session.scalars(
        select(LimitIncidentEvent.evaluation_id).where(
            LimitIncidentEvent.incident_id == incident.id,
            LimitIncidentEvent.evaluation_id.is_not(None),
            LimitIncidentEvent.created_at <= event.created_at,
        )
    ))
    if incident.first_evaluation_id is not None:
        ids.add(incident.first_evaluation_id)
    evaluations = list(session.scalars(
        select(LimitEvaluation).where(LimitEvaluation.id.in_(ids)).order_by(LimitEvaluation.id)
    )) if ids else []
    problems: list[str] = []
    for ev in evaluations:
        evidence = dict(ev.evidence or {})
        if ev.reason_code:
            problems.append(f"evaluation #{ev.id} reason {ev.reason_code}")
        elif ev.coverage_ratio is not None and ev.coverage_ratio < 1.0:
            problems.append(f"evaluation #{ev.id} coverage {ev.coverage_ratio:.2f}")
        elif evidence.get("is_fresh") is False:
            problems.append(f"evaluation #{ev.id} used stale source evidence")
        elif evidence.get("missing_market_evidence") or evidence.get("missing_fx"):
            problems.append(f"evaluation #{ev.id} is missing market inputs")
        else:
            ref_id = evidence.get("source_reference_id")
            ref = session.get(LimitSourceReference, int(ref_id)) if isinstance(ref_id, int) else None
            if ref is not None and ref.is_fresh is False:
                problems.append(f"evaluation #{ev.id} used source reference #{ref.id}, "
                                "outside the freshness policy")
            elif ref is not None and (ref.completeness_diagnostics or {}).get("missing_market_evidence"):
                problems.append(f"evaluation #{ev.id} used source reference #{ref.id} "
                                "with missing market evidence")
    if problems:
        return ClaimCheck(SUPPORTED, "; ".join(problems[:3]), _now())
    return ClaimCheck(
        NO_EVIDENCE,
        f"{_plural(len(evaluations), 'evaluation')} behind this incident carries no reason "
        "code, coverage gap or stale source",
        _now(),
    )


def check_limit_under_review(session: Session, incident: LimitIncident, event: LimitIncidentEvent) -> ClaimCheck:
    newer = list(session.scalars(
        select(RiskLimitVersion)
        .where(
            RiskLimitVersion.risk_limit_id == incident.risk_limit_id,
            RiskLimitVersion.created_at > incident.first_seen_at,
        )
        .order_by(RiskLimitVersion.version)
    ))
    if newer:
        latest = newer[-1]
        return ClaimCheck(
            SUPPORTED,
            f"limit version {latest.version} ({latest.state}) was created on "
            f"{latest.created_at.date().isoformat()}, after the incident opened",
            _now(),
        )
    return ClaimCheck(NO_EVIDENCE, "no limit version has been created since the incident opened", _now())


CHECKERS: dict[str, CheckFn] = {
    "position_rolling_off": check_position_rolling_off,
    "data_error": check_data_error,
    "limit_under_review": check_limit_under_review,
}
CLAIMS_WITH_CHECKERS: frozenset[str] = frozenset(CHECKERS)


def run_check(session: Session, claim: str, incident: LimitIncident, event: LimitIncidentEvent,
              *, now: datetime) -> ClaimCheck:
    """Never raises; a failing checker yields `unverified` and one log line."""
    checker = CHECKERS.get(claim)
    if checker is None:
        return ClaimCheck(UNVERIFIED, "no checker for this claim", now)
    try:
        result = checker(session, incident, event)
    except Exception:  # noqa: BLE001 — the review is still written
        logger.warning("limit review: %s check failed for incident %s event %s",
                       claim, incident.id, event.id, exc_info=True)
        return ClaimCheck(UNVERIFIED, "check failed", now)
    return replace(result, checked_at=now)
```

Note the detail for the `no_evidence` data-error case: with the `_plural` helper `1 evaluation` and `2 evaluations` both read correctly, and the test pins the singular.

- [ ] **Step 4: Run both new test files**

Run: `.venv/bin/python -m pytest tests/test_limit_review_checks.py tests/test_limit_review_state.py -v`
Expected: all PASS (review.py imports review_checks, which now exists). The `test_data_error_supported…` test's stale-source branch relies on `_incident` leaving `evaluation2.reason_code` and `coverage_ratio` unset — `_evaluation` in `test_limit_incidents.py` does not set them.

- [ ] **Step 5: Commit** — see Task 5 Step 5 (one commit for both modules).

---

### Task 7: `review.py` — store, `score_event`, `enqueue`, `sweep`, `latest_reviews`

**Files:**
- Modify: `backend/app/services/limits/review.py` (append)
- Modify: `tests/_system_one_fakes.py` (append `ReviewPost`)
- Test: `tests/test_limit_review_store.py`

**Interfaces:**
- Produces:
  - `claims_from_answers(answers_json: Mapping[str, Any], checks: Mapping[str, ClaimCheck]) -> list[dict]` — all six claims in `CLAIM_QUESTIONS` order, `{claim, p, check, detail, checked_at}`; check fields `None` below `review_chip_min_p`.
  - `answers_to_json(answers: Mapping[str, Answer]) -> dict` — noul `{"p"}`, score `{"score","confidence","probabilities"}`, choice `{"choice","confidence","probabilities"}`.
  - `score_event(event_id: int, kind: str, *, session_factory=None, post=None, settings=None, now=None) -> str` — returns `"scored"`, `"skipped"`, `"exists"`, or the `unscored_reason` written (`no_key`, `timeout`, `http_error`, `bad_response`, `state_too_large`, `low_confidence`, `internal_error`). Never raises.
  - `enqueue(event_id: int, kind: str, *, settings=None, submit=None) -> bool` and `enqueue_latest(incident, kind, *, settings=None, submit=None) -> bool`. `submit` defaults to the module's `_submit` (= `task_runner.submit_async_task`); tests replace it with an inline runner.
  - `due_events(session, limit: int) -> list[tuple[int, str]]` — `(event_id, kind)`, never-attempted first, then `attempted_at ASC, event_id ASC`; arena-thread events excluded in SQL.
  - `recompute_checks(session, *, now: datetime) -> int`
  - `sweep(session_factory=None, *, post=None, settings=None, now=None) -> dict[str, int]` — counters `scored`, `unscored`, `skipped`, `rechecked`; never raises.
  - `latest_reviews(session, incident_ids: Iterable[int]) -> dict[int, dict[str, LimitIncidentReview | None]]` — the row with the highest `event_id` per kind.
- Consumes: Task 5's builders and constants; Task 6's `run_check`, `CLAIMS_WITH_CHECKERS`; Task 2's `thread_is_arena`.

- [ ] **Step 1: Add the `ReviewPost` fake**

Append to `tests/_system_one_fakes.py`:

```python
class ReviewPost:
    """Answers the limit-review request: the `score` at `score`/`confidence`,
    every `noul` at `nouls.get(key, 0.05)`, and every `choice` as `choice` at
    `choice_p` (the rest of the mass on the first other option).

    `.exc` raises instead; `.bad_for_rationale` holds rationale texts that get a
    malformed body (a per-row bad_response). `.calls` keeps each payload.
    """

    def __init__(self, *, score: float = 2.0, confidence: float = 0.9,
                 nouls: dict[str, float] | None = None,
                 choice: str = "remediating", choice_p: float = 0.8) -> None:
        self.score = score
        self.confidence = confidence
        self.nouls = dict(nouls or {})
        self.choice = choice
        self.choice_p = choice_p
        self.exc: BaseException | None = None
        self.bad_for_rationale: set[str] = set()
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        if self.exc is not None:
            raise self.exc
        rationale = ((payload["state"].get("waiver") or {}).get("rationale"))
        if rationale in self.bad_for_rationale:
            return {"answers": {}}
        answers: dict[str, Any] = {}
        for key, question in payload["questions"].items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": self.nouls.get(key, 0.05)}
            elif question["type"] == "score":
                levels = len(question["criteria"])
                probabilities = {str(i): 0.0 for i in range(levels)}
                probabilities[str(min(levels - 1, round(self.score)))] = 1.0
                answers[key] = {"type": "score", "score": self.score,
                                "confidence": self.confidence, "probabilities": probabilities}
            else:
                options = list(question["criteria"])
                probabilities = {option: 0.0 for option in options}
                probabilities[self.choice] = self.choice_p
                others = [o for o in options if o != self.choice]
                if others:
                    probabilities[others[0]] = round(1 - self.choice_p, 2)
                answers[key] = {"type": "choice", "choice": self.choice,
                                "confidence": self.choice_p, "probabilities": probabilities}
        return {"model": "typesafe/jev-1.13", "answers": answers}
```

- [ ] **Step 2: Write the failing store tests**

`tests/test_limit_review_store.py`:

```python
"""Insert-or-select store, due/rotation, the outage breaker, D10, D14, inert-when-off."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from _system_one_fakes import ReviewPost
from app import database
from app.models import AgentThread, LimitIncidentReview
from app.services.limits import incidents, review
from app.services.limits.contracts import LimitActionContext
from app.services.system_one import SystemOneUnavailable
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture

SCORE_AT = NOW + timedelta(days=2)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _factory():
    return database.SessionLocal()


def _open_incident(session):
    limit, version, run = _fixture(session)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW, utilization=1.2)
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT, occurred_at=NOW)
    session.commit()
    return result.incidents[0]


def _waive(session, incident, *, rationale="stale mark on 000300", context=CONTEXT, at=None):
    at = at or NOW + timedelta(hours=1)
    session.refresh(incident)
    incidents.waive(session, incident_id=incident.id, rationale=rationale,
                    expires_at=at + timedelta(days=10), expected_row_version=incident.row_version,
                    context=context, occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "waived"][-1].id


def _comment(session, incident, text, *, context=CONTEXT, at=None):
    at = at or NOW + timedelta(hours=2)
    session.refresh(incident)
    incidents.comment(session, incident_id=incident.id, comment=text,
                      expected_row_version=incident.row_version, context=context, occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "commented"][-1].id


def _rows(session, incident_id=None):
    stmt = select(LimitIncidentReview).order_by(LimitIncidentReview.id)
    if incident_id is not None:
        stmt = stmt.where(LimitIncidentReview.incident_id == incident_id)
    return list(session.scalars(stmt))


def test_a_waiver_scores_grade_claims_checks_and_raw_answers(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    post = ReviewPost(score=2.0, confidence=0.9,
                      nouls={"data_error": 0.83, "authority_only": 0.04})
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post,
                              now=SCORE_AT) == "scored"
    [row] = _rows(session)
    assert (row.kind, row.status, row.unscored_reason) == ("waiver", "scored", None)
    assert row.rationale_grade == pytest.approx(0.5) and row.rationale_confidence == 0.9
    assert row.authority_only_p == 0.04
    assert row.model == "typesafe/jev-1.13" and row.latency_ms is not None
    assert row.attempted_at == SCORE_AT
    claims = {c["claim"]: c for c in row.claims_json}
    assert list(claims) == list(review.CLAIM_QUESTIONS)
    assert claims["data_error"]["p"] == 0.83
    assert claims["data_error"]["check"] == "no_evidence"            # checked: p >= 0.70
    assert claims["data_error"]["checked_at"] == SCORE_AT.isoformat()
    assert claims["position_rolling_off"] == {"claim": "position_rolling_off", "p": 0.05,
                                              "check": None, "detail": None, "checked_at": None}
    assert row.answers_json["rationale_grade"]["score"] == 2.0
    assert row.answers_json["data_error"] == {"p": 0.83}
    assert len(post.calls) == 1 and set(post.calls[0]["questions"]) == set(review.WAIVER_QUESTIONS)


def test_a_thread_scores_argmax_and_low_confidence_is_unscored(live, session):
    incident = _open_incident(session)
    event_id = _comment(session, incident, "we are unwinding half the book today")
    assert review.score_event(event_id, "thread", session_factory=_factory,
                              post=ReviewPost(choice="remediating", choice_p=0.8), now=SCORE_AT) == "scored"
    [row] = _rows(session)
    assert (row.thread_state, row.thread_state_p, row.claims_json) == ("remediating", 0.8, [])

    low_id = _comment(session, incident, "FYI", at=NOW + timedelta(hours=3))
    assert review.score_event(low_id, "thread", session_factory=_factory,
                              post=ReviewPost(choice="no_position", choice_p=0.4), now=SCORE_AT) == "low_confidence"
    low = [r for r in _rows(session) if r.event_id == low_id][0]
    assert (low.status, low.unscored_reason, low.thread_state) == ("unscored", "low_confidence", None)
    assert low.answers_json["thread_state"]["choice"] == "no_position"   # raw kept for re-thresholding


def test_state_too_large_is_a_row_fact_and_no_call_is_made(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident, rationale="x" * 4001)
    post = ReviewPost()
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post) == "state_too_large"
    [row] = _rows(session)
    assert (row.status, row.unscored_reason) == ("unscored", "state_too_large")
    assert post.calls == []


@pytest.mark.parametrize("exc, reason", [
    (TimeoutError("slow"), "timeout"), (RuntimeError("HTTP 500"), "http_error"),
])
def test_transport_failures_write_the_reason(live, session, exc, reason):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    post = ReviewPost()
    post.exc = exc
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post) == reason
    [row] = _rows(session)
    assert (row.status, row.unscored_reason, row.model) == ("unscored", reason, "typesafe/jev-1.13")


def test_no_key_is_visible_not_silent(live, session, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=ReviewPost()) == "no_key"
    assert _rows(session)[0].unscored_reason == "no_key"


def test_a_scored_row_is_never_overwritten_but_an_unscored_one_is_retried(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    failing = ReviewPost()
    failing.exc = TimeoutError("slow")
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=failing) == "timeout"
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=4.0)) == "scored"
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=0.0)) == "exists"
    [row] = _rows(session)
    assert row.status == "scored" and row.rationale_grade == pytest.approx(1.0)


def test_two_workers_on_one_event_leave_one_row(live, session, monkeypatch):
    """Simulated race: the loser's INSERT hits the unique key and loads the winner."""
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    original = review._write

    def racing_write(session_, **kwargs):
        # Someone else inserts the same (event_id, kind) just before we do.
        with _factory() as other:
            original(other, **{**kwargs, "values": {**kwargs["values"], "rationale_grade": 0.25}})
            other.commit()
        return original(session_, **kwargs)

    monkeypatch.setattr(review, "_write", racing_write)
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=4.0)) == "scored"
    [row] = _rows(session)
    assert row.rationale_grade == 0.25       # the winner's row stands; ours was not applied


def test_arena_events_are_skipped_and_unknown_threads_are_skipped_too(live, session):
    arena = AgentThread(title="[arena] x", character="risk_manager", source="arena", arena_run_id=1)
    session.add(arena)
    session.commit()
    incident = _open_incident(session)
    arena_ctx = LimitActionContext(actor="agent", persona=None, mode="auto", thread_id=arena.id)
    arena_event = _waive(session, incident, context=arena_ctx)
    post = ReviewPost()
    assert review.score_event(arena_event, "waiver", session_factory=_factory, post=post) == "skipped"
    unknown_ctx = LimitActionContext(actor="agent", persona=None, mode="auto", thread_id=999_999)
    unknown_event = _comment(session, incident, "c", context=unknown_ctx)
    assert review.score_event(unknown_event, "thread", session_factory=_factory, post=post) == "skipped"
    assert _rows(session) == [] and post.calls == []
    with _factory() as s:
        assert review.due_events(s, 10) == [(unknown_event, "thread")]   # arena excluded in SQL; unknown stays due


def test_due_is_a_database_fact_and_only_the_latest_comment_is_due(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    c1 = _comment(session, incident, "first", at=NOW + timedelta(hours=2))
    c2 = _comment(session, incident, "second", at=NOW + timedelta(hours=3))
    with _factory() as s:
        assert review.due_events(s, 10) == [(w1, "waiver"), (c2, "thread")]
    assert review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost()) == "scored"
    with _factory() as s:
        assert review.due_events(s, 10) == [(c2, "thread")]
    assert c1 not in [e for e, _k in review.due_events(session, 10)]


def test_rotation_never_attempted_first_then_oldest_attempt(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident, at=NOW + timedelta(hours=1))
    failing = ReviewPost()
    failing.exc = TimeoutError("slow")
    assert review.score_event(w1, "waiver", session_factory=_factory, post=failing,
                              now=SCORE_AT) == "timeout"
    c1 = _comment(session, incident, "later", at=NOW + timedelta(hours=2))
    with _factory() as s:
        assert review.due_events(s, 10) == [(c1, "thread"), (w1, "waiver")]


def test_sweep_ends_the_batch_on_an_outage_and_continues_on_a_row_reason(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident, rationale="bad body", at=NOW + timedelta(hours=1))
    c1 = _comment(session, incident, "c", at=NOW + timedelta(hours=2))
    post = ReviewPost()
    post.bad_for_rationale = {"bad body"}
    counters = review.sweep(_factory, post=post, now=SCORE_AT)
    assert counters == {"scored": 1, "unscored": 1, "skipped": 0, "rechecked": 0}
    assert {r.unscored_reason for r in _rows(session)} == {"bad_response", None}

    incident2 = _open_incident(session)
    _waive(session, incident2, at=NOW + timedelta(hours=1))
    _comment(session, incident2, "c", at=NOW + timedelta(hours=2))
    outage = ReviewPost()
    outage.exc = TimeoutError("slow")
    counters = review.sweep(_factory, post=outage, now=SCORE_AT)
    assert counters["unscored"] == 1 and counters["scored"] == 0
    assert len(outage.calls) == 1                     # one failed request per sweep


def test_sweep_recomputes_checks_for_the_current_waiver_only(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    review.sweep(_factory, post=ReviewPost(nouls={"limit_under_review": 0.9}), now=SCORE_AT)
    [row] = _rows(session)
    assert [c for c in row.claims_json if c["claim"] == "limit_under_review"][0]["check"] == "no_evidence"
    # A new limit version appears; the next sweep flips the check without re-asking Jev.
    from app.models import RiskLimitVersion
    session.add(RiskLimitVersion(risk_limit_id=incident.risk_limit_id, version=2, state="draft",
                                 metric_kind="delta", source_kind="risk_run", methodology={},
                                 scope_type="position", scope_config={"position_ids": [7]},
                                 aggregation="net", transform="absolute", comparator="upper",
                                 hard_upper=150.0, unit="underlying_units",
                                 created_at=NOW + timedelta(days=1)))
    session.commit()
    post = ReviewPost()
    counters = review.sweep(_factory, post=post, now=SCORE_AT + timedelta(days=1))
    assert counters["rechecked"] == 1 and post.calls == []
    session.expire_all()
    [row] = _rows(session)
    check = [c for c in row.claims_json if c["claim"] == "limit_under_review"][0]
    assert check["check"] == "supported"
    assert check["checked_at"] == (SCORE_AT + timedelta(days=1)).isoformat()
    assert row.answers_json["limit_under_review"] == {"p": 0.9}   # Jev answer untouched


@pytest.mark.parametrize("setup", [
    lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"),
    lambda mp: mp.setenv("OPEN_OTC_LIMIT_REVIEW", "false"),
])
def test_inert_when_either_switch_is_off(live, session, monkeypatch, setup):
    setup(monkeypatch)
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    c1 = _comment(session, incident, "c")
    post = ReviewPost()
    assert review.score_event(w1, "waiver", session_factory=_factory, post=post) == "skipped"
    assert review.enqueue(w1, "waiver", submit=lambda fn, *a, **k: fn(*a)) is False
    assert review.sweep(_factory, post=post) == {"scored": 0, "unscored": 0, "skipped": 0, "rechecked": 0}
    assert _rows(session) == [] and post.calls == []


def test_enqueue_runs_score_event_through_submit(live, session, monkeypatch):
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    ran = []
    post = ReviewPost()
    monkeypatch.setattr("app.services.system_one.client._default_post", post)
    assert review.enqueue(w1, "waiver", submit=lambda fn, *a, **k: ran.append(fn(*a))) is True
    assert ran == ["scored"]
    assert review.enqueue_latest(incident, "thread", submit=lambda fn, *a, **k: fn(*a)) is False  # no comment yet


def test_an_internal_error_is_recorded_and_never_raised(live, session, monkeypatch):
    incident = _open_incident(session)
    w1 = _waive(session, incident)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(review, "build_waiver_state", boom)
    assert review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost()) == "internal_error"
    [row] = _rows(session)
    assert (row.status, row.unscored_reason) == ("unscored", "internal_error")


def test_latest_reviews_returns_the_highest_event_per_kind(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident, at=NOW + timedelta(hours=1))
    review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost(score=1.0))
    # expire the waiver and re-waive
    from app.models import LimitIncident
    with _factory() as s:
        row = s.get(LimitIncident, incident.id)
        incidents._update(s, incident=row, expected_row_version=None, values={"status": "open"})
        s.commit()
    w2 = _waive(session, incident, at=NOW + timedelta(days=1))
    review.score_event(w2, "waiver", session_factory=_factory, post=ReviewPost(score=4.0))
    with _factory() as s:
        lookup = review.latest_reviews(s, [incident.id, 999])
        assert lookup[incident.id]["waiver"].event_id == w2
        assert lookup[incident.id]["thread"] is None
        assert lookup[999] == {"waiver": None, "thread": None}
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_limit_review_store.py -v`
Expected: FAIL with `AttributeError: module … review has no attribute 'score_event'`.

- [ ] **Step 4: Append the store, scoring, enqueue and sweep to `review.py`**

```python
# --- answers <-> json ------------------------------------------------------------------

def answers_to_json(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """Every raw answer, so a threshold change is a re-render, never a re-score."""
    out: dict[str, Any] = {}
    for key, answer in answers.items():
        if isinstance(answer, NoulAnswer):
            out[key] = {"p": round(answer.probability, 4)}
        elif isinstance(answer, ScoreAnswer):
            out[key] = {"score": answer.score, "confidence": answer.confidence,
                        "probabilities": dict(answer.probabilities)}
        elif isinstance(answer, ChoiceAnswer):
            out[key] = {"choice": answer.choice, "confidence": answer.confidence,
                        "probabilities": dict(answer.probabilities)}
    return out


def claims_from_answers(answers_json: Mapping[str, Any], checks: Mapping[str, ClaimCheck]) -> list[dict[str, Any]]:
    """All six claims in question order; check fields are null below the chip threshold."""
    out = []
    for claim in CLAIM_QUESTIONS:
        p = float((answers_json.get(claim) or {}).get("p", 0.0))
        check = checks.get(claim)
        out.append({
            "claim": claim, "p": p,
            "check": check.check if check is not None else None,
            "detail": check.detail if check is not None else None,
            "checked_at": check.checked_at.isoformat() if check is not None else None,
        })
    return out


def _claims_to_check(answers_json: Mapping[str, Any]) -> list[str]:
    return [claim for claim in CLAIM_QUESTIONS
            if float((answers_json.get(claim) or {}).get("p", 0.0)) >= review_chip_min_p]


# --- store: insert-or-select on (event_id, kind) --------------------------------------

def _existing(session: Session, event_id: int, kind: str) -> LimitIncidentReview | None:
    return session.scalar(select(LimitIncidentReview).where(
        LimitIncidentReview.event_id == event_id, LimitIncidentReview.kind == kind))


def _write(session: Session, *, incident_id: int, event_id: int, kind: str,
           values: dict[str, Any]) -> LimitIncidentReview:
    """Insert in a savepoint; on the unique key, load the winner and update it only
    if it is still `unscored`. A `scored` row is never overwritten."""
    row = LimitIncidentReview(incident_id=incident_id, event_id=event_id, kind=kind, **values)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        session.expunge(row)
    winner = _existing(session, event_id, kind)
    if winner is None:  # pragma: no cover — the key fired, so the row exists
        raise RuntimeError(f"limit review ({event_id}, {kind}) vanished after an insert race")
    if winner.status == "unscored":
        for key, value in values.items():
            setattr(winner, key, value)
        session.flush()
    return winner


def _unscored_values(reason: str, *, now: datetime, model: str | None = None,
                     latency_ms: int | None = None, answers: dict | None = None) -> dict[str, Any]:
    return {
        "status": "unscored", "unscored_reason": reason, "attempted_at": now,
        "rationale_grade": None, "rationale_confidence": None, "authority_only_p": None,
        "thread_state": None, "thread_state_p": None,
        "claims_json": [], "answers_json": answers or {},
        "model": model, "latency_ms": latency_ms, "created_at": now,
    }


# --- scoring one event ---------------------------------------------------------------

def _scored_values(kind: str, session: Session, incident: LimitIncident, event: LimitIncidentEvent,
                   answers: Mapping[str, Answer], model: str, latency_ms: int, now: datetime) -> dict[str, Any]:
    answers_json = answers_to_json(answers)
    base = {"attempted_at": now, "model": model, "latency_ms": latency_ms,
            "answers_json": answers_json, "created_at": now}
    if kind == KIND_WAIVER:
        grade = answers["rationale_grade"]
        checks = {claim: run_check(session, claim, incident, event, now=now)
                  for claim in _claims_to_check(answers_json)}
        return {
            **base, "status": "scored", "unscored_reason": None,
            "rationale_grade": grade.normalized, "rationale_confidence": grade.confidence,
            "authority_only_p": answers["authority_only"].probability,
            "thread_state": None, "thread_state_p": None,
            "claims_json": claims_from_answers(answers_json, checks),
        }
    choice = answers["thread_state"]
    p = float(choice.probabilities.get(choice.choice, 0.0))
    if p < review_choice_min_p:
        return _unscored_values("low_confidence", now=now, model=model, latency_ms=latency_ms,
                                answers=answers_json)
    return {
        **base, "status": "scored", "unscored_reason": None,
        "rationale_grade": None, "rationale_confidence": None, "authority_only_p": None,
        "thread_state": choice.choice, "thread_state_p": p, "claims_json": [],
    }


def score_event(event_id: int, kind: str, *, session_factory: Callable[[], Session] | None = None,
                post: Any = None, settings: Settings | None = None, now: datetime | None = None) -> str:
    """Score one event in its own session. Returns "scored", "skipped", "exists" or the
    unscored reason written. Never raises; never touches a caller's Session."""
    if kind not in KINDS:
        return "skipped"
    cfg = settings or get_settings()
    if not is_live(cfg):
        return "skipped"
    factory = session_factory or database.SessionLocal
    when = now or datetime.utcnow()
    with factory() as session:
        try:
            event = session.get(LimitIncidentEvent, event_id)
            if event is None or event.event_type != EVENT_TYPE_FOR_KIND[kind]:
                return "skipped"
            if thread_is_arena(session, event.thread_id) is not False:   # None ⇒ skip (D14)
                return "skipped"
            existing = _existing(session, event_id, kind)
            if existing is not None and existing.status == "scored":
                return "exists"
            incident = session.get(LimitIncident, event.incident_id)
            if incident is None:
                return "skipped"
            try:
                state = (build_waiver_state if kind == KIND_WAIVER else build_thread_state)(
                    session, incident, event)
            except ReviewStateTooLarge:
                _write(session, incident_id=incident.id, event_id=event_id, kind=kind,
                       values=_unscored_values("state_too_large", now=when))
                session.commit()
                return "state_too_large"
            questions = WAIVER_QUESTIONS if kind == KIND_WAIVER else THREAD_QUESTIONS
            try:
                result = ask(state, questions, post=post, settings=cfg)
            except SystemOneUnavailable as exc:
                _write(session, incident_id=incident.id, event_id=event_id, kind=kind,
                       values=_unscored_values(exc.reason, now=when, model=cfg.system_one_model,
                                               latency_ms=exc.latency_ms))
                session.commit()
                return exc.reason
            values = _scored_values(kind, session, incident, event, result.answers,
                                    result.model, result.latency_ms, when)
            _write(session, incident_id=incident.id, event_id=event_id, kind=kind, values=values)
            session.commit()
            return "scored" if values["status"] == "scored" else str(values["unscored_reason"])
        except Exception:  # noqa: BLE001 — a review can never fail anything else
            session.rollback()
            logger.warning("limit review: scoring event %s (%s) failed", event_id, kind, exc_info=True)
            try:
                event = session.get(LimitIncidentEvent, event_id)
                if event is not None:
                    _write(session, incident_id=event.incident_id, event_id=event_id, kind=kind,
                           values=_unscored_values("internal_error", now=when))
                    session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
            return "internal_error"


# --- fast path (D9: only the fast path; the sweep is the guarantee) --------------------

_submit = submit_async_task


def enqueue(event_id: int, kind: str, *, settings: Settings | None = None,
            submit: Callable[..., Any] | None = None) -> bool:
    """Hand `score_event` to the async pool after the caller has COMMITTED. False when
    not live, the event is missing, or its thread is (or may be) an arena thread.
    Never raises: forgetting or failing to enqueue only delays a review (D9)."""
    try:
        cfg = settings or get_settings()
        if kind not in KINDS or not is_live(cfg):
            return False
        with database.SessionLocal() as session:
            event = session.get(LimitIncidentEvent, event_id)
            if event is None or thread_is_arena(session, event.thread_id) is not False:
                return False
        (submit or _submit)(score_event, event_id, kind)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("limit review: enqueue of event %s (%s) failed", event_id, kind, exc_info=True)
        return False


def enqueue_latest(incident: LimitIncident, kind: str, *, settings: Settings | None = None,
                   submit: Callable[..., Any] | None = None) -> bool:
    try:
        event_id = latest_event_id(incident, kind)
    except Exception:  # noqa: BLE001
        return False
    return enqueue(event_id, kind, settings=settings, submit=submit) if event_id is not None else False


# --- sweep (D9 / D10 / D12) -------------------------------------------------------------

def due_events(session: Session, limit: int) -> list[tuple[int, str]]:
    """Scoreable events with no row or an `unscored` row: every `waived` event, and
    each incident's LATEST `commented` event (D10). Arena-thread events are excluded
    here; a thread that cannot be resolved stays due and is skipped per attempt."""
    review = LimitIncidentReview
    not_arena = or_(LimitIncidentEvent.thread_id.is_(None), AgentThread.source != "arena",
                    AgentThread.id.is_(None))
    waived = (
        select(LimitIncidentEvent.id, literal(KIND_WAIVER).label("kind"), review.attempted_at)
        .outerjoin(AgentThread, AgentThread.id == LimitIncidentEvent.thread_id)
        .outerjoin(review, and_(review.event_id == LimitIncidentEvent.id, review.kind == KIND_WAIVER))
        .where(LimitIncidentEvent.event_type == "waived", not_arena,
               or_(review.id.is_(None), review.status == "unscored"))
    )
    latest_comment = (
        select(func.max(LimitIncidentEvent.id).label("event_id"))
        .where(LimitIncidentEvent.event_type == "commented")
        .group_by(LimitIncidentEvent.incident_id)
        .subquery()
    )
    commented = (
        select(LimitIncidentEvent.id, literal(KIND_THREAD).label("kind"), review.attempted_at)
        .join(latest_comment, latest_comment.c.event_id == LimitIncidentEvent.id)
        .outerjoin(AgentThread, AgentThread.id == LimitIncidentEvent.thread_id)
        .outerjoin(review, and_(review.event_id == LimitIncidentEvent.id, review.kind == KIND_THREAD))
        .where(not_arena, or_(review.id.is_(None), review.status == "unscored"))
    )
    rows = list(session.execute(waived)) + list(session.execute(commented))
    rows.sort(key=lambda r: (r[2] is not None, r[2] or datetime.min, r[0]))
    return [(int(r[0]), str(r[1])) for r in rows[:limit]]


def recompute_checks(session: Session, *, now: datetime) -> int:
    """Re-run the checkers on every scored waiver review whose incident is still
    `waived` and whose event is the incident's current waiver. Jev answers untouched."""
    rows = list(session.scalars(
        select(LimitIncidentReview)
        .join(LimitIncident, LimitIncident.id == LimitIncidentReview.incident_id)
        .where(LimitIncidentReview.kind == KIND_WAIVER, LimitIncidentReview.status == "scored",
               LimitIncident.status == "waived")
        .order_by(LimitIncidentReview.id)
    ))
    count = 0
    for row in rows:
        incident = session.get(LimitIncident, row.incident_id)
        event = session.get(LimitIncidentEvent, row.event_id)
        if incident is None or event is None or latest_event_id(incident, KIND_WAIVER) != row.event_id:
            continue
        answers_json = dict(row.answers_json or {})
        checks = {claim: run_check(session, claim, incident, event, now=now)
                  for claim in _claims_to_check(answers_json)}
        row.claims_json = claims_from_answers(answers_json, checks)
        count += 1
    session.commit()
    return count


def sweep(session_factory: Callable[[], Session] | None = None, *, post: Any = None,
          settings: Settings | None = None, now: datetime | None = None) -> dict[str, int]:
    """Score up to `review_batch` due events, then recompute checks. Never raises."""
    counters = {"scored": 0, "unscored": 0, "skipped": 0, "rechecked": 0}
    try:
        cfg = settings or get_settings()
        if not is_live(cfg):
            return counters
        factory = session_factory or database.SessionLocal
        when = now or datetime.utcnow()
        with factory() as session:
            due = due_events(session, review_batch)
        for event_id, kind in due:
            outcome = score_event(event_id, kind, session_factory=factory, post=post,
                                  settings=cfg, now=when)
            if outcome == "scored":
                counters["scored"] += 1
            elif outcome in ("skipped", "exists"):
                counters["skipped"] += 1
            else:
                counters["unscored"] += 1
                if outcome in OUTAGE_REASONS:
                    logger.info("limit review: System One unreachable (%s); ending this batch", outcome)
                    break
        with factory() as session:
            counters["rechecked"] = recompute_checks(session, now=when)
    except Exception:  # noqa: BLE001 — a monitoring run must never fail because of this
        logger.warning("limit review: sweep failed", exc_info=True)
    return counters


# --- read model --------------------------------------------------------------------------

def latest_reviews(session: Session, incident_ids: Iterable[int]) -> dict[int, dict[str, LimitIncidentReview | None]]:
    """The row with the highest event_id per kind (the latest scoreable event, not the
    latest write — a late retry of an old waiver must not outrank the current one)."""
    ids = [int(i) for i in incident_ids]
    out: dict[int, dict[str, LimitIncidentReview | None]] = {
        i: {KIND_WAIVER: None, KIND_THREAD: None} for i in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(LimitIncidentReview)
        .where(LimitIncidentReview.incident_id.in_(ids))
        .order_by(LimitIncidentReview.event_id.asc(), LimitIncidentReview.id.asc())
    )
    for row in rows:
        out[row.incident_id][row.kind] = row
    return out
```

`session.expunge(row)` after the failed savepoint matters: the pending object would otherwise be re-flushed on the next `flush()` and hit the key again.

- [ ] **Step 5: Run the store tests**

Run: `.venv/bin/python -m pytest tests/test_limit_review_store.py tests/test_limit_review_state.py tests/test_limit_review_checks.py -v`
Expected: all PASS. If `test_two_workers_on_one_event_leave_one_row` fails on SQLite locking, the racing write must commit its own session before returning — it does (`other.commit()`); if it still deadlocks, open the "other" session BEFORE the scoring session by moving the pre-insert into the test body ahead of `score_event` (insert a row with `status="unscored"` first, then assert the scored call reports `"scored"` and updated it — this still proves the loser loads the winner).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/limits/review.py tests/_system_one_fakes.py tests/test_limit_review_store.py
git commit -m "feat(limits): review store (insert-or-select), score_event, enqueue fast path, sweep"
```

---

### Task 8: Wire the fast path (routes, tools) and the sweep (monitoring)

**Files:**
- Modify: `backend/app/routers/limits.py:611-704` (`_apply_incident_action`, comment/waive routes)
- Modify: `backend/app/tools/limits.py` (`_mutate_incident`, comment/waive tools)
- Modify: `backend/app/services/limits/monitoring.py:933-994` (`_finalize`)
- Test: `tests/test_limit_review_wiring.py` (new); `tests/test_limits_tools.py` (D13 pin appended)

**Interfaces:**
- Consumes: `review.enqueue_latest(incident, kind, *, settings=None, submit=None)`, `review.sweep(session_factory)`, `review._submit`.
- Produces: `_apply_incident_action(..., review_kind: str | None = None)`; `_mutate_incident(action, *, incident_id, review_kind: str | None = None, **kwargs)`.

- [ ] **Step 1: Write the failing wiring tests**

`tests/test_limit_review_wiring.py`:

```python
"""Call sites enqueue AFTER commit; a review can never fail a waive, a comment or a run."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from _system_one_fakes import ReviewPost
from app import database
from app.models import LimitIncident, LimitIncidentReview, LimitMonitoringRun, TaskRun
from app.routers.limits import build_limits_router
from app.services.limits import review
from test_limits_api import NOW, _seed_monitoring_episode


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def inline_submit(monkeypatch):
    """Run the fast path synchronously so the test can read its row."""
    ran: list[str] = []
    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: ran.append(fn(*a)))
    return ran


@pytest.fixture
def jev(monkeypatch):
    post = ReviewPost(score=3.0, nouls={"hedge_in_progress": 0.9})
    monkeypatch.setattr("app.services.system_one.client._default_post", post)
    return post


@pytest.fixture
def api(session):
    app = FastAPI()

    def get_db():
        with database.SessionLocal() as db:
            yield db

    app.include_router(build_limits_router(get_db=get_db,
                                           dispatch_limit_monitoring_fn=lambda *_: None))
    with TestClient(app) as client:
        yield client


def _episode(session):
    from app.models import Portfolio, RiskLimit, RiskLimitVersion
    portfolio = Portfolio(name="Wiring desk", base_currency="USD")
    limit = RiskLimit(key="wiring-delta", name="Wiring delta", description="", category="greek",
                      owner="market-risk", tags=[])
    session.add_all([portfolio, limit])
    session.flush()
    version = RiskLimitVersion(risk_limit_id=limit.id, version=1, state="active", metric_kind="delta",
                               source_kind="risk_run", methodology={}, scope_type="portfolio",
                               scope_config={"portfolio_ids": [portfolio.id]}, aggregation="net",
                               transform="absolute", comparator="upper", warning_upper=80.0,
                               hard_upper=100.0, unit="underlying_units", freshness_policy={},
                               effective_from=NOW - timedelta(days=1))
    session.add(version)
    session.flush()
    limit.active_version_id = version.id
    _run, _evaluation, incident = _seed_monitoring_episode(session, portfolio=portfolio,
                                                          limit=limit, version=version)
    return portfolio, incident


def _reviews(session):
    return list(session.scalars(select(LimitIncidentReview).order_by(LimitIncidentReview.id)))


def test_rest_waive_and_comment_enqueue_after_commit(live, inline_submit, jev, api, session):
    portfolio, incident = _episode(session)
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive",
                      params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "unwinding half by Friday — LW",
                            "expires_at": (NOW + timedelta(days=5)).isoformat()})
    assert waived.status_code == 200, waived.text
    assert inline_submit == ["scored"]
    commented = api.post(f"/api/limit-incidents/{incident.id}/comments",
                         params={"portfolio_id": portfolio.id},
                         json={"expected_row_version": 2, "comment": "hedge booked"})
    assert commented.status_code == 200, commented.text
    assert inline_submit == ["scored", "scored"]
    kinds = [(r.kind, r.status) for r in _reviews(session)]
    assert kinds == [("waiver", "scored"), ("thread", "scored")]
    # acknowledge/assign/resolve enqueue nothing
    acked = api.post(f"/api/limit-incidents/{incident.id}/acknowledge",
                     params={"portfolio_id": portfolio.id}, json={"expected_row_version": 3})
    assert acked.status_code == 200 and len(inline_submit) == 2


def test_tool_waive_and_comment_enqueue_after_commit(live, inline_submit, jev, session):
    from app.tools.limits import comment_limit_incident_tool, waive_limit_incident_tool
    _portfolio, incident = _episode(session)
    out = waive_limit_incident_tool.func(incident_id=incident.id, rationale="stale mark",
                                         expires_at=(NOW + timedelta(days=5)).isoformat(),
                                         expected_row_version=1)
    assert out["status"] == "waived" and inline_submit == ["scored"]
    out = comment_limit_incident_tool.func(incident_id=incident.id, comment="disputing the print",
                                           expected_row_version=2)
    assert out["id"] == incident.id and inline_submit == ["scored", "scored"]


def test_a_client_that_raises_inside_the_worker_leaves_the_waive_committed(live, api, session, monkeypatch):
    portfolio, incident = _episode(session)

    def boom(*_a, **_k):
        raise RuntimeError("worker bug")

    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: boom())
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive",
                      params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "r",
                            "expires_at": (NOW + timedelta(days=5)).isoformat()})
    assert waived.status_code == 200, waived.text
    session.expire_all()
    assert session.get(LimitIncident, incident.id).status == "waived"
    assert _reviews(session) == []          # no row yet — still due for the next sweep (D9)


def test_a_sweep_that_raises_leaves_the_monitoring_run_completed(live, session, monkeypatch):
    """The sweep rides on _finalize; nothing it does may flip the run to failed."""
    from app.services.limits import monitoring

    calls = []

    def exploding_due(*_a, **_k):
        calls.append(1)
        raise RuntimeError("sweep bug")

    monkeypatch.setattr(review, "due_events", exploding_due)
    _portfolio, incident = _episode(session)
    run = session.scalar(select(LimitMonitoringRun).order_by(LimitMonitoringRun.id.desc()))
    # Drive _finalize directly with an empty snapshot: no evaluations, so the reconciler is a no-op.
    task = TaskRun(kind="limit_monitoring", status="running", limit_monitoring_run_id=run.id)
    session.add(task)
    session.commit()
    monitoring._finalize(
        database.SessionLocal, task_id=task.id, monitoring_run_id=run.id,
        snapshot={"inputs": {"valuation_as_of": NOW.isoformat()}, "versions": [],
                  "context": {"actor": "monitor", "persona": None, "mode": "auto"}},
        groups={}, incident_reconciler=lambda *a, **k: None,
    )
    session.expire_all()
    assert session.get(LimitMonitoringRun, run.id).status == "completed"
    assert session.get(TaskRun, task.id).status == "completed"
    assert calls == [1]                      # the sweep RAN and failed, and nothing noticed
```

Before running, read `monitoring._context_from_snapshot` (`grep -n "def _context_from_snapshot" -A 12 backend/app/services/limits/monitoring.py`) and shape the `snapshot["context"]` (or whatever key it reads) so `_finalize` can build a `LimitActionContext`; also confirm `TaskKind.LIMIT_MONITORING.value == "limit_monitoring"` and replace the string if it differs.

- [ ] **Step 2: Add the D13 pin to `tests/test_limits_tools.py`**

Append:

```python
def test_agent_tool_payloads_never_carry_reviews(tmp_path, monkeypatch):
    """D13: a model that can see its rationale's grade will write to the grader."""
    from _system_one_fakes import ReviewPost
    from app.services.limits import review
    from app.tools.limits import get_limit_incident_tool, list_limit_incidents_tool, waive_limit_incident_tool

    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    monkeypatch.setattr("app.services.system_one.client._default_post", ReviewPost())
    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: fn(*a))
    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    waived = waive_limit_incident_tool.func(
        incident_id=ids["incident"], rationale="stale mark", expires_at="2026-07-01T00:00:00",
        expected_row_version=1)
    assert waived["status"] == "waived"
    with database.SessionLocal() as session:
        assert session.query(models.LimitIncidentReview).count() == 1   # a row EXISTS…

    got = get_limit_incident_tool.func(incident_id=ids["incident"])
    listed = list_limit_incidents_tool.func(portfolio_id=ids["portfolio"])["incidents"][0]
    for payload in (waived, got, listed):
        flat = str(payload)
        assert "reviews" not in payload
        for key in ("rationale_grade", "thread_state", "authority_only", "claims", "unscored"):
            assert key not in flat                                        # …and is invisible
```

Confirm `_seed_limit_world` returns a `"portfolio"` key (`grep -n "return {" -A 6 tests/test_limits_tools.py`); if it is named differently, use that name.

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_limit_review_wiring.py tests/test_limits_tools.py::test_agent_tool_payloads_never_carry_reviews -v`
Expected: the enqueue tests FAIL with `inline_submit == []` (nothing enqueues yet); the pin FAILS on `count() == 1`.

- [ ] **Step 4: Wire the REST routes**

In `backend/app/routers/limits.py`, import `review` alongside the other services (`from app.services.limits import definitions, incidents, monitoring, review`) and change `_apply_incident_action`:

```python
    def _apply_incident_action(
        session: Session,
        *,
        incident_id: int,
        portfolio_id: int,
        payload: LimitActionIn,
        action: Callable[..., LimitIncident],
        review_kind: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        _incident_with_portfolio(session, incident_id, portfolio_id)
        try:
            row = action(
                session,
                incident_id=incident_id,
                expected_row_version=payload.expected_row_version,
                context=_context(),
                **extra,
            )
            session.commit()
            # The incident was loaded for the portfolio guard before the
            # service appended its immutable event; expire eager collections so
            # the response reflects the just-committed ledger row.
            session.expire_all()
            fresh = _incident_with_portfolio(session, row.id, portfolio_id)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)
        # After the commit, never inside it: a review can delay, never fail, an action.
        if review_kind is not None:
            review.enqueue_latest(fresh, review_kind)
        return _incident_out(fresh, portfolio_id=portfolio_id)
```

`_raise_domain_error` always raises, so `fresh` is bound on the non-error path; add `# type: ignore[possibly-undefined]` only if the type checker complains. Pass `review_kind=review.KIND_THREAD` from `comment_incident` and `review_kind=review.KIND_WAIVER` from `waive_incident`. (Task 9 changes `_incident_out`'s signature again; the call here becomes `_incident_out(fresh, portfolio_id=portfolio_id, reviews=review.latest_reviews(session, [fresh.id])[fresh.id])`.)

- [ ] **Step 5: Wire the agent tools**

In `backend/app/tools/limits.py`, add `from ..services.limits import review as review_service  # noqa: E402` next to the other late imports, and change `_mutate_incident`:

```python
def _mutate_incident(action, *, incident_id: int, review_kind: str | None = None, **kwargs) -> dict[str, Any]:
    database.init_db()
    with database.SessionLocal() as session:
        try:
            incident = action(session, incident_id=incident_id, **kwargs)
            session.commit()
        except LimitConflictError as exc:
            ...  # unchanged
        except (LimitNotFoundError, LimitValidationError) as exc:
            ...  # unchanged
        if review_kind is not None:
            # After the commit (D9 fast path). Payloads stay review-free (D13).
            review_service.enqueue_latest(incident, review_kind)
        return _incident_out(session, incident)
```

Pass `review_kind=review_service.KIND_THREAD` from `comment_limit_incident_tool` and `review_kind=review_service.KIND_WAIVER` from `waive_limit_incident_tool`. `_incident_out` is untouched.

- [ ] **Step 6: Wire the sweep into `_finalize`**

In `backend/app/services/limits/monitoring.py`, add `from . import review` with the local imports, and at the end of `_finalize`, AFTER the `with session_factory() as session:` block closes (so the run's commit is durable first):

```python
    # After that run's work has committed: the review sweep (spec §Flow 4). It opens
    # its own sessions, and it never raises — a review cannot fail a monitoring run.
    review.sweep(session_factory)
```

- [ ] **Step 7: Run the wiring tests and the neighbours**

Run: `.venv/bin/python -m pytest tests/test_limit_review_wiring.py tests/test_limits_tools.py tests/test_limits_api.py tests/test_limit_monitoring_tasks.py tests/test_limit_monitoring.py -v`
Expected: all PASS. `test_limit_monitoring_tasks.py` exercises `_finalize` with the master switch off (conftest), so the sweep is inert there.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/limits.py backend/app/tools/limits.py backend/app/services/limits/monitoring.py tests/test_limit_review_wiring.py tests/test_limits_tools.py
git commit -m "feat(limits): enqueue reviews after commit from REST and tools; sweep after each monitoring run"
```

---

### Task 9: The HTTP read model — `reviews` on every incident projection

**Files:**
- Modify: `backend/app/schemas.py:1428-1452` (`LimitIncidentOut` and new models before it)
- Modify: `backend/app/routers/limits.py:145-176` (`_incident_out`) and its four call sites (`list_incidents`, `get_incident`, `_apply_incident_action`, `dashboard` ~line 837)
- Test: `tests/test_limit_review_api.py`

**Interfaces:**
- Produces on the wire: `LimitIncidentOut.reviews: {waiver: LimitIncidentReviewOut | null, thread: LimitIncidentReviewOut | null}`, where

```python
class LimitIncidentClaimOut(BaseModel):
    claim: str
    p: float
    check: str | None
    detail: str | None
    checked_at: datetime | None


class LimitIncidentReviewOut(BaseModel):
    id: int
    incident_id: int
    event_id: int
    kind: str
    status: str
    unscored_reason: str | None
    rationale_grade: float | None
    rationale_confidence: float | None
    authority_only_p: float | None
    thread_state: str | None
    thread_state_p: float | None
    claims: list[LimitIncidentClaimOut] = Field(default_factory=list)
    chip_min_p: float
    model: str | None
    latency_ms: int | None
    attempted_at: datetime | None
    created_at: datetime


class LimitIncidentReviewsOut(BaseModel):
    waiver: LimitIncidentReviewOut | None = None
    thread: LimitIncidentReviewOut | None = None
```

  `chip_min_p` is `review.review_chip_min_p`, served so the UI filters chips with the server's threshold and a config edit is a re-render (spec §Thresholds).
- Produces in the router: `_incident_out(row, *, portfolio_id, reviews: Mapping[str, LimitIncidentReview | None]) -> dict`.

- [ ] **Step 1: Write the failing HTTP test**

`tests/test_limit_review_api.py`:

```python
"""Every served review field is asserted at the HTTP layer (three swallowing layers)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _system_one_fakes import ReviewPost
from app import database
from app.routers.limits import build_limits_router
from app.services.limits import review
from test_limit_review_wiring import _episode
from test_limits_api import NOW

EMPTY = {"waiver": None, "thread": None}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: fn(*a))


@pytest.fixture
def api(session):
    app = FastAPI()

    def get_db():
        with database.SessionLocal() as db:
            yield db

    app.include_router(build_limits_router(get_db=get_db,
                                           dispatch_limit_monitoring_fn=lambda *_: None))
    with TestClient(app) as client:
        yield client


def test_an_unreviewed_incident_serves_explicit_nulls(api, session):
    portfolio, incident = _episode(session)
    body = api.get(f"/api/limit-incidents/{incident.id}", params={"portfolio_id": portfolio.id}).json()
    assert body["reviews"] == EMPTY
    listed = api.get("/api/limit-incidents", params={"portfolio_id": portfolio.id}).json()
    assert listed["items"][0]["reviews"] == EMPTY
    dashboard = api.get("/api/limit-monitoring/dashboard", params={"portfolio_id": portfolio.id}).json()
    assert dashboard["active_incidents"][0]["reviews"] == EMPTY


def test_get_serves_every_review_field(live, api, session, monkeypatch):
    monkeypatch.setattr("app.services.system_one.client._default_post",
                        ReviewPost(score=2.0, confidence=0.9,
                                   nouls={"data_error": 0.83, "authority_only": 0.04},
                                   choice="disputes_number", choice_p=0.7))
    portfolio, incident = _episode(session)
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive", params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "stale mark on 000300",
                            "expires_at": (NOW + timedelta(days=5)).isoformat()})
    assert waived.status_code == 200, waived.text
    # The action response itself already carries the review (the fast path ran inline).
    waiver = waived.json()["reviews"]["waiver"]
    assert waived.json()["reviews"]["thread"] is None
    assert set(waiver) == {
        "id", "incident_id", "event_id", "kind", "status", "unscored_reason",
        "rationale_grade", "rationale_confidence", "authority_only_p", "thread_state",
        "thread_state_p", "claims", "chip_min_p", "model", "latency_ms", "attempted_at", "created_at"}
    assert (waiver["kind"], waiver["status"], waiver["unscored_reason"]) == ("waiver", "scored", None)
    assert waiver["incident_id"] == incident.id
    assert waiver["rationale_grade"] == pytest.approx(0.5)
    assert waiver["rationale_confidence"] == 0.9 and waiver["authority_only_p"] == 0.04
    assert waiver["chip_min_p"] == 0.70 and waiver["model"] == "typesafe/jev-1.13"
    assert [c["claim"] for c in waiver["claims"]] == list(review.CLAIM_QUESTIONS)
    data_error = [c for c in waiver["claims"] if c["claim"] == "data_error"][0]
    assert data_error["p"] == 0.83 and data_error["check"] == "no_evidence"
    assert data_error["detail"] and data_error["checked_at"]
    assert waiver["thread_state"] is None and waiver["thread_state_p"] is None

    commented = api.post(f"/api/limit-incidents/{incident.id}/comments", params={"portfolio_id": portfolio.id},
                         json={"expected_row_version": 2, "comment": "the print is wrong"})
    thread = commented.json()["reviews"]["thread"]
    assert (thread["kind"], thread["thread_state"], thread["thread_state_p"]) == ("thread", "disputes_number", 0.7)
    assert thread["claims"] == [] and thread["rationale_grade"] is None

    got = api.get(f"/api/limit-incidents/{incident.id}", params={"portfolio_id": portfolio.id}).json()
    assert got["reviews"]["waiver"]["event_id"] == waiver["event_id"]
    assert got["reviews"]["thread"]["event_id"] == thread["event_id"]


def test_an_unscored_review_serves_its_reason(live, api, session, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    portfolio, incident = _episode(session)
    body = api.post(f"/api/limit-incidents/{incident.id}/waive", params={"portfolio_id": portfolio.id},
                    json={"expected_row_version": 1, "rationale": "r",
                          "expires_at": (NOW + timedelta(days=5)).isoformat()}).json()
    waiver = body["reviews"]["waiver"]
    assert (waiver["status"], waiver["unscored_reason"], waiver["rationale_grade"]) == ("unscored", "no_key", None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_limit_review_api.py -v`
Expected: FAIL with `KeyError: 'reviews'`.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, insert the three models from the Interfaces block directly above `class LimitIncidentOut`, and add to `LimitIncidentOut` after `events`:

```python
    reviews: LimitIncidentReviewsOut = Field(default_factory=LimitIncidentReviewsOut)
```

- [ ] **Step 4: Project reviews in the router**

In `backend/app/routers/limits.py` import `LimitIncidentClaimOut, LimitIncidentReviewOut, LimitIncidentReviewsOut` from `app.schemas` and `LimitIncidentReview` from `app.models`, then:

```python
def _review_out(row: LimitIncidentReview | None) -> LimitIncidentReviewOut | None:
    if row is None:
        return None
    return LimitIncidentReviewOut(
        id=row.id,
        incident_id=row.incident_id,
        event_id=row.event_id,
        kind=row.kind,
        status=row.status,
        unscored_reason=row.unscored_reason,
        rationale_grade=row.rationale_grade,
        rationale_confidence=row.rationale_confidence,
        authority_only_p=row.authority_only_p,
        thread_state=row.thread_state,
        thread_state_p=row.thread_state_p,
        claims=[LimitIncidentClaimOut(**claim) for claim in (row.claims_json or [])],
        chip_min_p=review.review_chip_min_p,
        model=row.model,
        latency_ms=row.latency_ms,
        attempted_at=row.attempted_at,
        created_at=row.created_at,
    )


def _incident_out(
    row: LimitIncident,
    *,
    portfolio_id: int,
    reviews: Mapping[str, LimitIncidentReview | None],
) -> dict[str, Any]:
    if row.portfolio_id != portfolio_id:
        raise ValueError("incident portfolio scope mismatch")
    return LimitIncidentOut(
        ...  # every existing field unchanged
        events=sorted(row.events, key=lambda event: event.id),
        reviews=LimitIncidentReviewsOut(
            waiver=_review_out(reviews.get(review.KIND_WAIVER)),
            thread=_review_out(reviews.get(review.KIND_THREAD)),
        ),
    ).model_dump()
```

(`from collections.abc import Callable, Generator, Mapping`.) Then at each call site build the lookup once per response:

```python
        # list_incidents (page only) and dashboard (its active_incidents list):
        page = rows[offset : offset + limit]
        lookup = review.latest_reviews(session, [row.id for row in page])
        return {"items": [_incident_out(row, portfolio_id=portfolio_id, reviews=lookup[row.id]) for row in page],
                "total": len(rows)}

        # get_incident and _apply_incident_action:
        row = _incident_with_portfolio(session, incident_id, portfolio_id)
        return _incident_out(row, portfolio_id=portfolio_id,
                             reviews=review.latest_reviews(session, [row.id])[row.id])
```

Grep for every remaining `_incident_out(` in the router (`grep -n "_incident_out(" backend/app/routers/limits.py`) — there must be none without `reviews=`.

- [ ] **Step 5: Run the HTTP tests and the neighbours**

Run: `.venv/bin/python -m pytest tests/test_limit_review_api.py tests/test_limits_api.py tests/test_limit_review_wiring.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/limits.py tests/test_limit_review_api.py
git commit -m "feat(limits): serve reviews on every incident projection, every field named"
```

---

### Task 10: Arena purge keeps the FK chain clean

**Files:**
- Test: `tests/test_arena_purge_limit_reviews.py` (new). No production change is expected: `_delete_portfolios_with_dependents` walks FK children recursively, so `limit_incident_reviews` rows (FK to `limit_incidents` and to `limit_incident_events`) go before the events and the incidents. This test pins that a review row cannot wedge a purge.

- [ ] **Step 1: Write the test**

```python
"""A review row on an arena fixture incident must never wedge the post-board purge."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models import LimitIncident, LimitIncidentEvent, LimitIncidentReview, Portfolio
from app.services.arena.runner import _delete_portfolios_with_dependents
from test_limit_review_wiring import _episode


def test_purge_deletes_reviews_before_events_and_incidents(session):
    portfolio, incident = _episode(session)
    event = session.scalar(select(LimitIncidentEvent).where(LimitIncidentEvent.incident_id == incident.id))
    session.add(LimitIncidentReview(incident_id=incident.id, event_id=event.id, kind="waiver",
                                    status="unscored", unscored_reason="no_key", claims_json=[],
                                    answers_json={}, created_at=datetime(2026, 9, 22)))
    session.commit()
    _delete_portfolios_with_dependents(session, [portfolio.id])
    session.commit()
    assert session.scalar(select(LimitIncidentReview.id)) is None
    assert session.scalar(select(LimitIncidentEvent.id)) is None
    assert session.scalar(select(LimitIncident.id)) is None
    assert session.get(Portfolio, portfolio.id) is None
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_arena_purge_limit_reviews.py -v`
Expected: PASS. If it fails with `FOREIGN KEY constraint failed`, the traversal missed the table: add an explicit `session.execute(delete(models.LimitIncidentReview).where(models.LimitIncidentReview.incident_id.in_(incident_ids)))` in `_delete_portfolios_with_dependents` immediately after the `ARENA_FIXTURE_PURGE_INFO_KEY` is set, computing `incident_ids` from `LimitIncident.portfolio_id.in_(pids)`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_arena_purge_limit_reviews.py backend/app/services/arena/runner.py
git commit -m "test(arena): purge deletes limit_incident_reviews ahead of their incidents"
```

---

### Task 11: Limits page — grade, claim, authority and thread chips; sortable `Rationale` column

**Files:**
- Modify: `frontend/src/types.ts:1259-1284` (`LimitIncident`), plus new types above it
- Modify: `frontend/src/routes/Limits.tsx:993-1069` (`BreachesTab` columns), `:1154-1260` (`IncidentDetail`), helpers near `:2549`
- Modify: `frontend/src/routes/Limits.css` (`.limits-incident-table .wl-table__row` min-width, new classes)
- Test: `frontend/src/routes/Limits.live.test.tsx` (`incident()` factory `:229`, new `describe`)

**Interfaces:**
- Consumes the wire shape from Task 9 verbatim.
- Produces TS types `LimitIncidentClaim`, `LimitIncidentReview`, `LimitIncidentReviews`, and `LimitIncident.reviews`.

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, above `export type LimitIncident = {`:

```ts
export type LimitIncidentClaimKey =
  | 'position_rolling_off'
  | 'data_error'
  | 'limit_under_review'
  | 'hedge_in_progress'
  | 'client_flow_expected'
  | 'market_reversion';

export type LimitIncidentClaim = {
  claim: LimitIncidentClaimKey;
  p: number;
  check: 'supported' | 'no_evidence' | 'unverified' | null;
  detail: string | null;
  checked_at: string | null;
};

export type LimitIncidentThreadState =
  | 'disputes_number'
  | 'remediating'
  | 'requests_limit_change'
  | 'requests_more_time'
  | 'root_cause_only'
  | 'no_position';

/** System One's display-only read of one incident text event. null = never scored. */
export type LimitIncidentReview = {
  id: number;
  incident_id: number;
  event_id: number;
  kind: 'waiver' | 'thread';
  status: 'scored' | 'unscored';
  unscored_reason: string | null;
  rationale_grade: number | null;
  rationale_confidence: number | null;
  authority_only_p: number | null;
  thread_state: LimitIncidentThreadState | null;
  thread_state_p: number | null;
  claims: LimitIncidentClaim[];
  chip_min_p: number;
  model: string | null;
  latency_ms: number | null;
  attempted_at: string | null;
  created_at: string;
};

export type LimitIncidentReviews = {
  waiver: LimitIncidentReview | null;
  thread: LimitIncidentReview | null;
};
```

and add to `LimitIncident` after `events: LimitIncidentEvent[];`:

```ts
  reviews: LimitIncidentReviews;
```

- [ ] **Step 2: Fix the fixture and write the failing UI tests**

In `frontend/src/routes/Limits.live.test.tsx`, add `reviews: { waiver: null, thread: null },` to the object returned by `incident()` (after `events: [OPEN_EVENT],`). Then add, near the other incident tests:

```tsx
const CLAIM = (claim: string, p: number, check: 'supported' | 'no_evidence' | 'unverified' | null, detail: string | null) => ({
  claim, p, check, detail, checked_at: check ? '2026-07-18T10:00:00' : null,
});

const WAIVER_REVIEW = {
  id: 1, incident_id: 81, event_id: 402, kind: 'waiver', status: 'scored', unscored_reason: null,
  rationale_grade: 0.5, rationale_confidence: 0.9, authority_only_p: 0.81,
  thread_state: null, thread_state_p: null,
  claims: [
    CLAIM('position_rolling_off', 0.05, null, null),
    CLAIM('data_error', 0.83, 'no_evidence', '1 evaluation behind this incident carries no reason code, coverage gap or stale source'),
    CLAIM('limit_under_review', 0.12, null, null),
    CLAIM('hedge_in_progress', 0.91, 'unverified', 'no checker for this claim'),
    CLAIM('client_flow_expected', 0.02, null, null),
    CLAIM('market_reversion', 0.69, null, null),
  ],
  chip_min_p: 0.7, model: 'typesafe/jev-1.13', latency_ms: 1300,
  attempted_at: '2026-07-18T10:00:00', created_at: '2026-07-18T10:00:00',
};

const THREAD_REVIEW = {
  ...WAIVER_REVIEW, id: 2, event_id: 403, kind: 'thread', rationale_grade: null,
  rationale_confidence: null, authority_only_p: null, claims: [],
  thread_state: 'disputes_number', thread_state_p: 0.74,
};

describe('limit incident reviews', () => {
  it('renders the grade, the claims at or above the threshold, the authority warning and the thread state', async () => {
    const reviewed = incident({
      status: 'waived', waiver_rationale: 'stale mark, and the Dec position rolls off',
      reviews: { waiver: WAIVER_REVIEW, thread: THREAD_REVIEW },
    });
    installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents') {
        return json({ items: [reviewed], total: 1 });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents/81') {
        return json(reviewed);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    expect(await screen.findByText('2/4 · cause, no remediation')).toBeInTheDocument();
    expect(screen.getByText('data error ?').closest('span[title]')).toHaveAttribute(
      'title', '1 evaluation behind this incident carries no reason code, coverage gap or stale source');
    expect(screen.getByText('hedge in progress ·')).toBeInTheDocument();
    expect(screen.queryByText(/market reversion/)).not.toBeInTheDocument();   // 0.69 < 0.70
    expect(screen.queryByText(/rolling off/)).not.toBeInTheDocument();
    expect(screen.getByText('authority only')).toBeInTheDocument();
    expect(screen.getByText('disputes number').closest('span[title]')).toHaveAttribute('title', 'thread state at 0.74');
  });

  it('renders an unscored reason muted and a missing review as a dash', async () => {
    const unscored = incident({
      reviews: { waiver: { ...WAIVER_REVIEW, status: 'unscored', unscored_reason: 'no_key',
                           rationale_grade: null, authority_only_p: null, claims: [] },
                 thread: null },
    });
    installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents') {
        return json({ items: [unscored], total: 1 });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents/81') {
        return json(unscored);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);
    expect(await screen.findByText('unscored · no_key')).toHaveClass('limits-review--muted');
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('sorts the Rationale column weakest first, unreviewed last', async () => {
    const weak = incident({ id: 81, reviews: { waiver: { ...WAIVER_REVIEW, rationale_grade: 0.0 }, thread: null } });
    const strong = incident({ id: 82, reviews: { waiver: { ...WAIVER_REVIEW, id: 9, incident_id: 82, rationale_grade: 1.0 }, thread: null } });
    const none = incident({ id: 83 });
    installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents') {
        return json({ items: [strong, none, weak], total: 3 });
      }
      if (request.method === 'GET' && request.url.pathname.startsWith('/api/limit-incidents/')) {
        return json(strong);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);
    await screen.findByText('#83');
    const order = () => screen.getAllByRole('row').slice(1).map((row) => row.textContent?.match(/#8\d/)?.[0]);
    expect(order()).toEqual(['#82', '#83', '#81']);           // server order (last_seen desc)
    await userEvent.click(screen.getByRole('button', { name: /sort by rationale grade/i }));
    expect(order()).toEqual(['#81', '#82', '#83']);           // 0/4, 4/4, —
    await userEvent.click(screen.getByRole('button', { name: /sort by rationale grade/i }));
    expect(order()).toEqual(['#82', '#81', '#83']);           // 4/4, 0/4, — (unreviewed stay last)
  });
});
```

If the harness shows the incident detail only after selecting a row (check how the existing "sends the current concurrency token" test reaches the Waive button — it renders and immediately finds the button, so the first incident is auto-selected), keep the tests as written; otherwise add `await userEvent.click(await screen.findByText('#81'))` before the first assertion.

- [ ] **Step 3: Run to verify they fail**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/routes/Limits.live.test.tsx`
Expected: `tsc` passes (the factory is untyped; the type only gains a field), vitest FAILS on `findByText('2/4 · cause, no remediation')`.

- [ ] **Step 4: Add the helpers and chips to `Limits.tsx`**

Import `LimitIncidentClaim`, `LimitIncidentReview` from `../types`. Near `incidentStatusCode`:

```tsx
const RATIONALE_LEVEL_LABELS = [
  'no reason', 'cause not checkable', 'cause, no remediation', 'remediation, no owner/date', 'complete',
] as const;

const CLAIM_LABELS: Record<LimitIncidentClaim['claim'], string> = {
  position_rolling_off: 'rolling off',
  data_error: 'data error',
  limit_under_review: 'limit under review',
  hedge_in_progress: 'hedge in progress',
  client_flow_expected: 'client flow',
  market_reversion: 'market reversion',
};

const THREAD_STATE_LABELS: Record<NonNullable<LimitIncidentReview['thread_state']>, string> = {
  disputes_number: 'disputes number',
  remediating: 'remediating',
  requests_limit_change: 'asks limit change',
  requests_more_time: 'asks more time',
  root_cause_only: 'root cause only',
  no_position: 'no position',
};

const CHECK_MARK: Record<NonNullable<LimitIncidentClaim['check']>, string> = {
  supported: '✓', no_evidence: '?', unverified: '·',
};
const CHECK_VARIANT: Record<NonNullable<LimitIncidentClaim['check']>, BadgeVariant> = {
  supported: 'pos', no_evidence: 'warn', unverified: 'ink',
};

/** Nearest of the five levels; null = never scored (not 0). */
function rationaleLevel(review: LimitIncidentReview | null): number | null {
  if (!review || review.rationale_grade == null) return null;
  return Math.round(review.rationale_grade * 4);
}

function gradeVariant(level: number): BadgeVariant {
  return level <= 1 ? 'neg' : level === 2 ? 'warn' : 'pos';
}

function ReviewChips({ review }: { review: LimitIncidentReview | null }) {
  if (!review) return <span className="limits-review--muted">—</span>;
  if (review.status === 'unscored') {
    return <span className="limits-review--muted">{`unscored · ${review.unscored_reason ?? 'unknown'}`}</span>;
  }
  const level = rationaleLevel(review);
  return (
    <div className="limits-review-chips">
      {level != null ? (
        <Badge variant={gradeVariant(level)}>{`${level}/4 · ${RATIONALE_LEVEL_LABELS[level]}`}</Badge>
      ) : null}
      {review.claims
        .filter((claim) => claim.p >= review.chip_min_p)
        .map((claim) => (
          <span key={claim.claim} title={claim.detail ?? `p ${claim.p.toFixed(2)}`}>
            <Badge variant={claim.check ? CHECK_VARIANT[claim.check] : 'ink'}>
              {`${CLAIM_LABELS[claim.claim]} ${claim.check ? CHECK_MARK[claim.check] : '·'}`}
            </Badge>
          </span>
        ))}
      {review.authority_only_p != null && review.authority_only_p >= review.chip_min_p ? (
        <span title={`p ${review.authority_only_p.toFixed(2)}`}>
          <Badge variant="warn">authority only</Badge>
        </span>
      ) : null}
    </div>
  );
}

function ThreadStateChip({ review }: { review: LimitIncidentReview | null }) {
  if (!review) return <span className="limits-review--muted">—</span>;
  if (review.status === 'unscored' || !review.thread_state) {
    return <span className="limits-review--muted">{`unscored · ${review.unscored_reason ?? 'unknown'}`}</span>;
  }
  return (
    <span title={`thread state at ${(review.thread_state_p ?? 0).toFixed(2)}`}>
      <Badge variant="info">{THREAD_STATE_LABELS[review.thread_state]}</Badge>
    </span>
  );
}
```

Note the `title` sits on a wrapping `<span>`, the same pattern `Confirmations.tsx:familyCheckBadge` uses, because `Badge` does not forward `title`.

In `IncidentDetail`, replace the `Waiver rationale` `Fact` with:

```tsx
        <div className="limits-incident-facts__wide">
          <dt>Waiver rationale</dt>
          <dd>
            {incident.waiver_rationale ?? '—'}
            <ReviewChips review={incident.reviews.waiver} />
          </dd>
        </div>
```

and add a ledger header directly inside `<div className="limits-ledger">`, before the map:

```tsx
        <div className="limits-ledger__head">
          <strong>Timeline</strong>
          <ThreadStateChip review={incident.reviews.thread} />
        </div>
```

- [ ] **Step 5: Add the sortable `Rationale` column to `BreachesTab`**

```tsx
  const [rationaleSort, setRationaleSort] = useState<'none' | 'asc' | 'desc'>('none');
  const sorted = useMemo(() => {
    if (rationaleSort === 'none') return visible;
    const key = (row: LimitIncident) => rationaleLevel(row.reviews.waiver);
    return [...visible].sort((a, b) => {
      const ga = key(a);
      const gb = key(b);
      if (ga == null && gb == null) return 0;
      if (ga == null) return 1;                       // unreviewed always last
      if (gb == null) return -1;
      return rationaleSort === 'asc' ? ga - gb : gb - ga;
    });
  }, [visible, rationaleSort]);
```

Add to `columns` (after `status`, before `owner`) and add `rationaleSort` to the `useMemo` dependency list:

```tsx
    {
      key: 'rationale',
      header: (
        <button
          type="button"
          className="limits-sort-header"
          aria-label="Sort by rationale grade"
          onClick={() => setRationaleSort((s) => (s === 'asc' ? 'desc' : 'asc'))}
        >
          Rationale{rationaleSort === 'asc' ? ' ↑' : rationaleSort === 'desc' ? ' ↓' : ''}
        </button>
      ),
      width: '6rem',
      render: (row) => {
        const level = rationaleLevel(row.reviews.waiver);
        return level == null
          ? <span className="limits-review--muted">—</span>
          : <span title={RATIONALE_LEVEL_LABELS[level]}>{`${level}/4`}</span>;
      },
    },
```

Pass `rows={sorted}` to the `Table`. The header button lives inside a `role="columnheader"` cell; the Table's row `onClick` is on body rows only, so the click does not select a row.

- [ ] **Step 6: Styles (tokens only)**

Append to `frontend/src/routes/Limits.css`, and change the existing `.limits-incident-table .wl-table__row { min-width: 48rem; }` to `min-width: 54rem;` (one fixed 6rem column was added):

```css
.limits-incident-facts__wide {
  grid-column: 1 / -1;
  min-width: 0;
}

.limits-review-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--gap-1);
  margin-top: var(--gap-1);
}

.limits-review--muted {
  color: var(--ink-2);
  font-size: var(--type-small-size);
}

.limits-ledger__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--gap-3);
  margin-bottom: var(--gap-2);
  font-family: var(--font-numeric);
}

.limits-sort-header {
  background: none;
  border: 0;
  padding: 0;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
```

`.limits-incident-facts__wide dt` / `dd` inherit the existing `.limits-incident-facts dt/dd` rules only if the element is a direct child `div`; the selector list at `Limits.css:170-185` is `.limits-incident-facts dt`, so a `div` with a different class still matches. Confirm visually.

- [ ] **Step 7: Type-check, test, and verify both themes + compact density**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/routes/Limits.live.test.tsx src/routes/Limits.test.tsx`
Expected: PASS. Then start the app (`run` skill or `npm run dev`), open Limits → Breaches with a waived incident, toggle the theme and density switches, and confirm the chips read in dark mode (they use `Badge`, so the tokens are already theme-aware) and the `Rationale` column does not wrap at compact density.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/Limits.tsx frontend/src/routes/Limits.css frontend/src/routes/Limits.live.test.tsx
git commit -m "feat(limits-ui): rationale grade, claim, authority and thread-state chips; sortable Rationale column"
```

---

### Task 12: The evidence probe (not on any app path)

**Files:**
- Create: `scripts/limit_review_probe.py`
- Create: `scripts/fixtures/limit_review_rationales.json`

**Interfaces:**
- Consumes: `review.WAIVER_QUESTIONS`, `review.THREAD_QUESTIONS`, `review.RATIONALE_LEVEL_LABELS`, `system_one.ask`; the app DB (`AgentThread`, `ArenaMatch`); the trace DB at `Settings.trace_db_path` (`trace_runs` with `run_type='tool'`, `name`, `inputs` JSON, `thread_id`).
- Run by hand with a real key: `ZENMUX_API_KEY=… .venv/bin/python scripts/limit_review_probe.py [--fixture-only] [--limit 50]`. `ask()` does not read the master switch, so the probe needs no feature flag — it is never on an app path.

- [ ] **Step 1: Write the fixture**

`scripts/fixtures/limit_review_rationales.json` — 15 cases spanning the five levels and six claims, two-claim and authority-only cases, and terse human-style text (spec §Open risks):

```json
{
  "limit": {"name": "Desk net delta", "metric_kind": "delta", "unit": "underlying_units",
            "scope_type": "underlying", "scope_label": "000300"},
  "breach": {"severity": "breach", "utilization": 1.18, "days_open": 3},
  "cases": [
    {"id": "L0-approved", "rationale": "Waiver approved.", "duration_days": 12,
     "expected_level": 0, "expected_claims": [], "expected_authority_only": true},
    {"id": "L0-restates", "rationale": "Limit is breached, waiving until further notice.", "duration_days": 12,
     "expected_level": 0, "expected_claims": [], "expected_authority_only": false},
    {"id": "L1-market-move", "rationale": "Market move, temporary.", "duration_days": 12,
     "expected_level": 1, "expected_claims": [], "expected_authority_only": false},
    {"id": "L1-known-issue", "rationale": "Known issue, will normalise.", "duration_days": 12,
     "expected_level": 1, "expected_claims": ["market_reversion"], "expected_authority_only": false},
    {"id": "L2-cause-only", "rationale": "The Dec 000300 snowball book is the driver; delta ran up after Friday's rally.", "duration_days": 12,
     "expected_level": 2, "expected_claims": [], "expected_authority_only": false},
    {"id": "L2-data-error", "rationale": "Breach is a stale vol mark on 000300 from Thursday's failed overnight run.", "duration_days": 12,
     "expected_level": 2, "expected_claims": ["data_error"], "expected_authority_only": false},
    {"id": "L3-no-owner-date", "rationale": "Driver is the Dec 3000-strike calls; we will unwind half the book next week.", "duration_days": 12,
     "expected_level": 3, "expected_claims": ["hedge_in_progress"], "expected_authority_only": false},
    {"id": "L3-remediation-after-expiry", "rationale": "The Dec position expires on 2026-12-18 and the exposure drops out then.", "duration_days": 12,
     "expected_level": 3, "expected_claims": ["position_rolling_off"], "expected_authority_only": false,
     "note": "remediation lands after the 12-day waiver: level 3 by the second clause"},
    {"id": "L4-complete", "rationale": "Cause: the 000300 Dec 6000 call block bought Monday. LW will sell 40% of it by 2026-10-02, inside the waiver window.", "duration_days": 12,
     "expected_level": 4, "expected_claims": ["hedge_in_progress"], "expected_authority_only": false},
    {"id": "L4-terse-human", "rationale": "rolling Dec CSI500, done Fri — LW", "duration_days": 12,
     "expected_level": 4, "expected_claims": ["hedge_in_progress"], "expected_authority_only": false,
     "note": "terse desk style; the ladder must not read it as level 1"},
    {"id": "two-claims", "rationale": "Stale mark on 510050 from the failed overnight run, and the Dec position rolls off on 2026-10-03 anyway.", "duration_days": 12,
     "expected_level": 3, "expected_claims": ["data_error", "position_rolling_off"], "expected_authority_only": false},
    {"id": "authority-only", "rationale": "Trader asked for the waiver and the head of desk approved it.", "duration_days": 12,
     "expected_level": 0, "expected_claims": [], "expected_authority_only": true},
    {"id": "limit-under-review", "rationale": "The 500 delta cap is mis-sized for the new book; risk is drafting a 750 version this week.", "duration_days": 12,
     "expected_level": 3, "expected_claims": ["limit_under_review"], "expected_authority_only": false},
    {"id": "client-flow", "rationale": "Client unwind of the 300-lot Phoenix expected Thursday will bring delta back inside.", "duration_days": 12,
     "expected_level": 3, "expected_claims": ["client_flow_expected"], "expected_authority_only": false},
    {"id": "market-reversion", "rationale": "Vol spike will fade; exposure returns inside the limit as the market calms.", "duration_days": 12,
     "expected_level": 1, "expected_claims": ["market_reversion"], "expected_authority_only": false}
  ]
}
```

- [ ] **Step 2: Write the probe**

`scripts/limit_review_probe.py`:

```python
#!/usr/bin/env python
"""Evidence probe for the limit-incident review predicates. NOT an app path.

1. Counts the model-written corpus: waive_limit_incident / comment_limit_incident
   calls in risk-limit-breach-day arena matches (trace DB, read-only), split by the
   step-5 outcome the scorer already recorded ("held" = tool NOT called passed).
2. Scores each through the SAME questions the feature uses.
3. Scores the committed hand-written fixture, so a wording change is re-run
   against the same cases.

Output is direction, never a rate: model-written text, one workflow's policy,
tiny N. A predicate moves from `untested` only by an edit that cites a run.

Usage:  ZENMUX_API_KEY=... .venv/bin/python scripts/limit_review_probe.py [--fixture-only] [--limit 50]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app import database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import AgentThread, ArenaMatch  # noqa: E402
from app.services.limits import review  # noqa: E402
from app.services.system_one import SystemOneUnavailable, ask  # noqa: E402

WORKFLOW = "risk-limit-breach-day"
TITLE_PREFIX = f"[arena] {WORKFLOW} · "
STEP5_LABEL = "tool NOT called: waive_limit_incident"
FIXTURE = ROOT / "scripts" / "fixtures" / "limit_review_rationales.json"


def _find_check(node: Any, label: str) -> bool | None:
    """Depth-first search of a score_breakdown for a check with `label`."""
    if isinstance(node, dict):
        if node.get("label") == label and "passed" in node:
            return bool(node["passed"])
        for value in node.values():
            found = _find_check(value, label)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_check(item, label)
            if found is not None:
                return found
    return None


def _corpus(limit: int) -> list[dict[str, Any]]:
    """Every waive/comment tool call in the workflow's arena threads, with its label."""
    trace_path = Path(get_settings().trace_db_path)
    if not trace_path.exists():
        print(f"trace DB not found at {trace_path}; corpus = 0")
        return []
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(f"file:{trace_path}?mode=ro", uri=True)
    try:
        with database.SessionLocal() as session:
            threads = session.scalars(select(AgentThread).where(
                AgentThread.source == "arena", AgentThread.title.like(TITLE_PREFIX + "%")
            )).all()
            for thread in threads:
                model = thread.title[len(TITLE_PREFIX):]
                match = session.scalar(select(ArenaMatch).where(
                    ArenaMatch.run_id == thread.arena_run_id, ArenaMatch.workflow_id == WORKFLOW,
                    ArenaMatch.model_id == model))
                held = _find_check(match.score_breakdown, STEP5_LABEL) if match is not None else None
                outcome = "unknown" if held is None else ("held" if held else "waived")
                spans = conn.execute(
                    "SELECT name, inputs, start_time FROM trace_runs WHERE thread_id=? AND run_type='tool' "
                    "AND name IN ('waive_limit_incident','comment_limit_incident') ORDER BY start_time",
                    (thread.id,)).fetchall()
                for name, inputs, start_time in spans:
                    try:
                        args = json.loads(inputs) if inputs else {}
                    except ValueError:
                        args = {}
                    text = args.get("rationale") if name == "waive_limit_incident" else args.get("comment")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    rows.append({"thread_id": thread.id, "run_id": thread.arena_run_id, "model": model,
                                 "tool": name, "text": text, "expires_at": args.get("expires_at"),
                                 "at": start_time, "step5": outcome})
    finally:
        conn.close()
    return rows[:limit]


def _waiver_state(limit: dict, breach: dict, rationale: str, duration_days: int | None) -> dict:
    return {"limit": limit, "breach": breach,
            "waiver": {"rationale": rationale, "duration_days": duration_days}}


def _level(answer) -> int:
    return round(answer.normalized * 4)


def _score_waiver(state: dict) -> dict[str, Any] | str:
    try:
        result = ask(state, review.WAIVER_QUESTIONS)
    except SystemOneUnavailable as exc:
        return exc.reason
    answers = result.answers
    return {
        "level": _level(answers["rationale_grade"]),
        "confidence": round(answers["rationale_grade"].confidence, 2),
        "claims": {key: round(answers[key].probability, 2) for key in review.CLAIM_QUESTIONS},
        "authority_only": round(answers["authority_only"].probability, 2),
        "latency_ms": result.latency_ms,
    }


def _print_corpus(rows: list[dict[str, Any]]) -> None:
    print(f"\n== arena corpus: {len(rows)} text rows ==")
    by = Counter((r["tool"], r["step5"]) for r in rows)
    for (tool, outcome), n in sorted(by.items()):
        print(f"  {tool:24s} step5={outcome:8s} n={n}")
    limit = {"name": "Tool Net Delta Cap", "metric_kind": "delta", "unit": "underlying_units",
             "scope_type": "portfolio", "scope_label": "arena book"}
    breach = {"severity": "breach", "utilization": None, "days_open": 0}
    levels: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row["tool"] != "waive_limit_incident":
            continue
        scored = _score_waiver(_waiver_state(limit, breach, row["text"], None))
        if isinstance(scored, str):
            print(f"  ! {scored} on thread {row['thread_id']}")
            continue
        levels[row["step5"]][scored["level"]] += 1
        fired = [k for k, p in scored["claims"].items() if p >= review.review_chip_min_p]
        print(f"  [{row['step5']}] {row['model']} L{scored['level']} auth={scored['authority_only']} "
              f"claims={fired} :: {row['text'][:90]!r}")
    for outcome, counter in levels.items():
        print(f"  level distribution step5={outcome}: {dict(sorted(counter.items()))}")


def _print_fixture() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"\n== fixture: {len(data['cases'])} cases ==")
    misses = 0
    for case in data["cases"]:
        scored = _score_waiver(_waiver_state(data["limit"], data["breach"], case["rationale"],
                                             case["duration_days"]))
        if isinstance(scored, str):
            print(f"  ! {scored}: {case['id']}")
            misses += 1
            continue
        fired = sorted(k for k, p in scored["claims"].items() if p >= review.review_chip_min_p)
        ok_level = scored["level"] == case["expected_level"]
        ok_claims = fired == sorted(case["expected_claims"])
        ok_auth = (scored["authority_only"] >= review.review_chip_min_p) == case["expected_authority_only"]
        flag = "" if (ok_level and ok_claims and ok_auth) else "  <-- MISS"
        misses += 0 if not flag else 1
        print(f"  {case['id']:28s} L{scored['level']} (exp {case['expected_level']}) "
              f"claims={fired} (exp {sorted(case['expected_claims'])}) auth={scored['authority_only']}"
              f"{flag}")
    print(f"  misses: {misses}/{len(data['cases'])} — direction, not a rate")
    return misses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    database.init_db()
    if not args.fixture_only:
        _print_corpus(_corpus(args.limit))
    _print_fixture()
    print("\nEvery predicate stays `untested` until an edit cites this run (spec §Evidence probe).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Dry-run the script without a key, then with one**

Run: `.venv/bin/python scripts/limit_review_probe.py --fixture-only`
Expected (no key): 15 lines of `! no_key: <case id>` and `misses: 15/15` — the plumbing works and nothing was sent. Then, with `ZENMUX_API_KEY` exported (the developer runs this by hand — it is the spec's rollout step 3), run again and record the output in the PR description. If a fixture case misses by two or more levels, adjust the LEVEL WORDING (not the fixture) before Task 11's UI is merged, and re-run.

Also run the corpus count once: `.venv/bin/python scripts/limit_review_probe.py --limit 50` and paste the `== arena corpus: N text rows ==` block into the PR — the spec's first task is to count it.

- [ ] **Step 4: Commit**

```bash
git add scripts/limit_review_probe.py scripts/fixtures/limit_review_rationales.json
git commit -m "chore(limits): evidence probe over the arena corpus and a committed rationale fixture"
```

---

### Task 13: Docs, changelog, guide — and the full-suite gate

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)
- Modify: `README.md:302` (env table row after `OPEN_OTC_CONFIRMATION_FAMILY_CHECK`)
- Modify: `backend/app/services/system_one/CLAUDE.md` (new section)
- Modify: `CLAUDE.md` (root; the `system_one` row of the subsystem table)

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]` → `### Added`, after the *Confirmation family cross-check* bullet:

```markdown
- **Limit incident text review** — with System One on, every waiver rationale and
  the latest comment on a limit incident get a display-only Jev read: a 5-level
  completeness grade, six claim probabilities (three of them — `position_rolling_off`,
  `data_error`, `limit_under_review` — checked against the book as
  `supported` / `no_evidence` / `unverified`, never "contradicted"), an
  `authority only` flag, and a comment-thread state. Built from the immutable
  event, stored once per `(event_id, kind)` in `limit_incident_reviews`
  (migration `0064`), served as `reviews` on every incident payload and shown on
  the Limits → Breaches tab with a sortable `Rationale` column. Enqueued after each
  waive/comment commit; a sweep after every limit-monitoring run catches anything
  missed. Invisible to the agent tools. Every predicate ships `untested`;
  `scripts/limit_review_probe.py` scores the arena corpus and a committed fixture.
  Opt out with `OPEN_OTC_LIMIT_REVIEW=false`.
- Shared `thread_is_arena()` (tri-state) and `limits/scopes.py` (the one
  scope-membership rule monitoring, sources and the review checkers all use).
```

- [ ] **Step 2: README env row**

Insert after the `OPEN_OTC_CONFIRMATION_FAMILY_CHECK` row:

```markdown
| `OPEN_OTC_LIMIT_REVIEW` | `true` (default) \| `false`. With System One on, sends each limit-incident waiver rationale and comment text (sanitized) to Jev, together with the limit's name / metric / unit / scope label, the breach severity, utilization and days open, and the waiver's duration in days. Scope labels and free text can carry underlying or client names — names are not secrets and are not masked. Display-only; never shown to the agent | No |
```

- [ ] **Step 3: System One guide section**

Append to `backend/app/services/system_one/CLAUDE.md`:

```markdown
## Limit incident review (`limits/review.py`, `limits/review_checks.py`)

- Spec: `docs/superpowers/specs/2026-09-21-limit-incident-review-design.md`. Three reads
  of incident TEXT: a waiver rationale's 5-level grade (`score`), six claims (`noul`, one
  each — a rationale often makes two), `authority_only` (`noul`), and a comment
  thread's state (`choice`). DISPLAY-ONLY; invisible to the agent tools (pinned).
- **Built from the EVENT, never the incident's columns.** A re-waive overwrites
  `waiver_rationale`; the review scores the `waived` event's payload, and severity /
  utilization come from the latest evaluation-stamped event at or before it — not
  `last_evaluation_id`, which keeps moving after the waiver.
- **Due is a database fact**: a `waived` event, or an incident's LATEST `commented`
  event, with no row or an `unscored` row. The post-commit `enqueue` is only the fast
  path; `sweep()` after every monitoring run is the guarantee. There is no `pending`
  state to go stale.
- **Insert-or-select on UNIQUE (event_id, kind).** A `scored` row is never overwritten.
  Checks (`claims_json`) are recomputed each sweep for the incident's current waiver;
  Jev answers (`answers_json`) never are. `review_chip_min_p` is therefore a re-render,
  not a re-score.
- **Checks never say "contradicted."** `supported` / `no_evidence` / `unverified` only.
  Scope membership goes through `limits/scopes.py::scope_matches` — the same rule
  monitoring and sources use — and expiry through `option_core_terms.expiry_date`.
- **Arena threads are never scored; "cannot tell" means skip.** `thread_access.
  thread_is_arena()` is tri-state; this caller treats `None` as skip (the memory queue
  treats it as proceed). A skipped event is still due, so it is retried.
- Outage reasons (`no_key`, `timeout`, `http_error`) end a sweep batch; `bad_response`,
  `state_too_large`, `low_confidence` are row facts and the batch continues. Any other
  exception ⇒ `unscored:internal_error`; a waive, a comment or a monitoring run can
  never fail because of a review.
- Every question is `untested`. `scripts/limit_review_probe.py` scores the arena
  corpus (split by the step-5 outcome) and `scripts/fixtures/limit_review_rationales.json`;
  its output is direction, never a rate.
```

- [ ] **Step 4: Root `CLAUDE.md` subsystem row**

Change the `system_one` row's "Read before" cell to:
`System One (TypeSafe Jev): the AUTO tool guard, memory keep-alive, the confirmation family cross-check, the limit incident review`.

- [ ] **Step 5: Run everything**

```bash
.venv/bin/python -m pytest
cd frontend && npx tsc --noEmit && npm test && cd ..
git config core.hooksPath .githooks     # so pre-push checks CHANGELOG.md
```

Expected: backend and frontend suites fully green (do not pipe through `tail`; read the summary line). Fix any exact-set pin that moved (Task 4 Step 5 and the enumeration in *File Structure* list the candidates).

- [ ] **Step 6: Commit and hand off**

```bash
git add CHANGELOG.md README.md CLAUDE.md backend/app/services/system_one/CLAUDE.md
git commit -m "docs(limits): limit incident review — changelog, env row, System One guide"
```

Then use `superpowers:finishing-a-development-branch`. Before merging, run the probe once with a real key (Task 12 Step 3) and paste its two summary blocks into the PR body; if any fixture case misses by ≥ 2 levels, fix the wording first (spec Rollout step 3).

---

## Self-review

**Spec coverage** (spec section → task):

| Spec section / decision | Task |
|---|---|
| Evidence base — count the corpus first | 12 (probe `--limit`, corpus block pasted into the PR) |
| D1 scope: grade, claims + checks, thread state | 5, 6, 7 |
| D2 three checkers; others `unverified` | 6 (`CHECKERS`, `run_check` → "no checker for this claim") |
| D3 `limit_incident_reviews`, one row per `(event_id, kind)` | 4 |
| D4 ladder + six claims as drafted | 5 (`RATIONALE_LEVELS`, `CLAIM_QUESTIONS`) |
| D5 display-only | 7 (no status writes), 8 (enqueue after commit only), 11 (chips only) |
| D6 built from the event; severity as of the event | 5 (`build_waiver_state`, `evaluation_as_of`) + tests |
| D7 claims are `noul`s | 5 |
| D8 one request per event | 7 (`ask(state, WAIVER_QUESTIONS)` once; `post.calls == 1` asserted) |
| D9 due = DB fact; fast path only | 7 (`due_events`, `enqueue`), 8 (worker-raises test leaves no row, still due) |
| D10 latest comment only; older never backfilled | 7 (`due_events` subquery + test) |
| D11 never "contradicted" | 6 |
| D12 checks recomputed, answers not | 7 (`recompute_checks` + test) |
| D13 invisible to the agent | 8 (`test_agent_tool_payloads_never_carry_reviews`) |
| D14 arena skip, tri-state, SQL exclusion in the sweep | 2, 7 |
| D15 switch under the master switch | 1, 7 (`is_live`), inert test |
| D16 author identity out of `state` (waiver) | 5 (asserted `"actor" not in str(state)`) |
| Predicates: state shapes, day arithmetic, caps | 5 |
| Thresholds as module constants; `chip_min_p` served for re-render | 5, 9 |
| Architecture flow 1–5 (unchanged `incidents.py`, fast path, `score_event`, sweep, breaker) | 7, 8 |
| Checkers: shared scope predicate; evidence keys read from the code | 3, 6 |
| Data model & migration; arena purge | 4, 10 |
| Failure handling table | 7 (every row has a test: switch off, no_key, timeout/http, bad_response, state_too_large, low_confidence, arena, forgotten enqueue, race, checker raises, internal_error) |
| Surfaces: API (every field named, asserted at HTTP), UI, tools unchanged, README | 9, 11, 8, 13 |
| Testing list | 5–11 |
| Evidence probe + committed fixture incl. terse human cases | 12 |
| Rollout: merge first, own worktree, probe before UI, docs | 1, 12, 13 |

**Type consistency checks made while writing:** `ClaimCheck.as_json()` keys `{check, detail, checked_at}` match the `claims_json` element shape produced by `claims_from_answers` and consumed by `LimitIncidentClaimOut` and the TS `LimitIncidentClaim`; `EVENT_TYPE_FOR_KIND` is the only place kind → event type is spelled; `review._submit` is the seam every test replaces; `thread_is_arena` is `bool | None` at both call sites; `scope_matches(scope_type, value, *, position_id, underlying, family)` has the same keyword names in monitoring, sources and the checker; `latest_reviews()[id]` is keyed by `KIND_WAIVER` / `KIND_THREAD`, which `_incident_out` reads.

**Known deliberate divergences from the spec text:** the shared scope helper is a NEW module (`scopes.py`) that both `_resolve_scopes` and `_risk_rows_for_scope` adopt, rather than a lift out of `sources.py` alone — because `monitoring._resolve_scopes` is the function that mints `scope_key`, and leaving it out would keep two definitions. The expiry source is `option_core_terms.expiry_date` (already materialised by `position_terms`), not a new read of `product_kwargs`. `claims_json` stores all six claims (check fields `null` below the threshold) and the API serves `chip_min_p`, so the spec's "changing the threshold is a config edit and a re-render" holds without the UI hardcoding `0.70`.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-22-limit-incident-review.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks (`superpowers:subagent-driven-development`).
2. **Inline Execution** — execute tasks in this session with `superpowers:executing-plans`, batch execution with checkpoints.
