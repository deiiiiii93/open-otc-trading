# Jev (System One) Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TypeSafe's Jev ("System One") as a calibrated classifier at three desk decision points — a per-call guard for AUTO-mode destructive tools (shadow first, enforce behind a flag), a display-only keep-alive score on memory facts, and a family cross-check on parsed confirmations — all inert unless `OPEN_OTC_SYSTEM_ONE=true`.

**Architecture:** One HTTP client (`services/system_one/`) is the only exit to Jev: it sanitizes `state`, enforces a size budget, maps every failure to one of five `unavailable` reasons, and validates responses. Three features sit on it and share nothing else: `ToolGuardMiddleware` (a `HumanInTheLoopMiddleware` subclass at `after_model`, verdicts committed under a UNIQUE key so a LangGraph resume never re-asks a non-deterministic model), `memory/keep_alive.py` (runs on the memory-writer daemon after commits), and `confirmations/family_check.py` (runs inside `parse_document`, never fails a document).

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0.49, Alembic 1.18.4, LangGraph/langchain v1 middleware, `requests`; React 19 + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-09-21-jev-system-one-design.md` (commit `ee266e9`). Read it before starting. Decisions are cited as D1–D17.

## Global Constraints

- Master switch `OPEN_OTC_SYSTEM_ONE` defaults **`false`**. With it off: no HTTP call, no new rows, every new field stays `NULL` (D6, D17).
- Feature switches, each subordinate to the master: `OPEN_OTC_TOOL_GUARD` = `off` | `shadow` | `enforce`, default `shadow`, unknown value → `shadow` + warning (never `off`, never `enforce`); `OPEN_OTC_MEMORY_KEEP_ALIVE` `on`/`off` default `on` (also needs `OPEN_OTC_MEMORY` on); `OPEN_OTC_CONFIRMATION_FAMILY_CHECK` default `true`.
- Other settings: `OPEN_OTC_SYSTEM_ONE_MODEL` = `typesafe/jev-1.13`; `OPEN_OTC_SYSTEM_ONE_BASE_URL` = `https://zenmux.ai/api/v1`; `OPEN_OTC_SYSTEM_ONE_TIMEOUT_S` = `5.0`; `OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS` = `60000`.
- Route: `POST {base_url}/systemone`, `Authorization: Bearer $ZENMUX_API_KEY` read from `os.environ` at call time.
- `UNAVAILABLE_REASONS = ("no_key", "state_too_large", "timeout", "http_error", "bad_response")`, stored verbatim.
- Jev is **not** a channel-registry model: no `agent_channels.yaml` / `.example.yml` edit (D7).
- Guard scope is exactly 9 tools (D1): `void_settlement_cashflow`, `close_position`, `settle_position`, `mark_knockout`, `waive_limit_incident`, `resolve_limit_incident`, `delete_pricing_parameter_rows`, `remove_portfolio_sources`, `import_otc_positions`. Threshold `0.5`. `allowed_decisions` is `["approve", "reject"]` — never `edit`.
- Guard registration: `yolo_mode and allow_reply_options` (AUTO only), in all four stacks, plus the runtime belt `AUDIT_CONTEXT_KEY['mode'] == "auto"`.
- Verdict table `agent_tool_guard_verdicts`; `thread_id INTEGER NOT NULL DEFAULT 0`; partial unique index on `(thread_id, tool_call_id) WHERE tool_call_id != ''`.
- Keep-alive: `keep_alive_batch=10`, `keep_alive_sibling_limit=50`, `keep_alive_sibling_chars=240`, `keep_alive_refresh_days=30`; outage reasons (`no_key`, `timeout`, `http_error`) end the batch, row reasons (`state_too_large`, `bad_response`) do not. Display-only (D3).
- Family check: `DISAGREE_MIN = 0.5`; `"unknown"` is a reserved option; disagreement never touches `validation_status`, `validation_errors` or bookability.
- Migrations `0061`–`0063`, each **idempotent** (`_tables()`/`_columns()`/`_indexes()` guards), migration-local Core only, downgrade drops via `op.batch_alter_table` (never a direct `op.drop_column`). Per-migration tests target the migration under test, never `head`.
- **No test makes a live call.** Tests inject `post=` or patch `client._default_post`; conftest fails any test that reaches the real POST.
- Frontend: token-only styling, reuse `Badge`; no new CSS.
- Rollout: **five commits** in dependency order — client, guard-shadow, guard-enforce, memory, confirmations. Each is an acceptance gate (full backend suite + `tsc` + the touched page's vitest green at that commit). Each phase's commit carries its own `CHANGELOG.md` / `README.md` lines.
- Every commit message ends with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Spec clarifications and deviations (read before Task 1)

Found while mapping the spec onto the code. Each one is either a latent defect in the spec as written or a fact the spec did not have.

| # | Spec says | Plan does | Why |
|---|---|---|---|
| 1 | Guard reads the turn's user message via `AUDIT_CONTEXT_KEY['message_id']` | Stamps and reads a **new key `user_message_id`** | Both HITL resume paths already stamp `message_id` = the **assistant** message that carried the pending action, and `message_id` is persisted into `agent_action_audits.message_id`. Reusing it would make every guarded call after a resume `no_user_request`, and would change that column's meaning for stream rows. The verdict column `user_request_source` keeps the spec's vocabulary (`message_id` / `latest`). |
| 2 | (silent) | Every HITL resume path stamps `mode` (and `invoke_workflow_resume` gets an audit context at all, with `thread_id`) | The resume sites at `agents.py:3466` and `:3636` stamp no `mode`, and `invoke_workflow_resume` stamps no audit context. The guard's runtime belt would then return `None` on the resume pass, **skip its `interrupt()`**, never consume the human's decision, and run a call the human rejected. Missing `thread_id` would also miss the committed-verdict lookup and re-ask Jev. |
| 3 | Refusal: call "dropped from the AIMessage and answered with an error ToolMessage" | The call **stays** on the `AIMessage` and is answered by an error `ToolMessage` | That is exactly what `_process_decision` does for a rejection (`langchain/agents/middleware/human_in_the_loop.py:300`), and `_make_model_to_tools_edge` skips calls that already have a `ToolMessage`. A dropped call would orphan its `ToolMessage`, which providers reject with a 400. |
| 4 | D13: synchronous | `aafter_model` resolves verdicts in `asyncio.to_thread`; `interrupt()` stays on the loop | Still synchronous for the agent (the call waits for its verdict); keeps a ~1.3 s blocking HTTP + DB round-trip off the server event loop, where it would stall every other SSE stream. |
| 5 | `run_job` calls `score_pending` after `apply_diff` committed | Hook runs in `process_one` / `_run_sweep` **after `session.commit()`**; skipped while draining at shutdown | `run_job`'s `apply_diff` is only committed by `process_one` after `run_job` returns. Scoring during `flush()` would add ~13 s to a 5 s shutdown grace. |
| 6 | (silent) | Every keep-alive write sets `updated_at = updated_at` | `MemoryEntry.updated_at` has `onupdate=utcnow`, and `load_injectable` orders by it. A naive write would reorder injection and break D3 (display-only). Verified: an ORM `update().values(updated_at=MemoryEntry.updated_at)` suppresses `onupdate`. |
| 7 | `earlier_in_this_turn` caps only the result head (300) | Also caps each rendered args string at 300 | 8 uncapped arg payloads (`redact_args` allows 8 KB each) reach 64 KB, over the 60 000 budget, which would make every long turn `state_too_large`. |
| 8 | Card description states which predicate fired | `_summary_for` appends the guard note even when a `_SUMMARY_BUILDERS` entry wins | For `close_position`, `settle_position` and `mark_knockout` the builder beats `description` (`hitl.py:567`), so the "why" would never reach the card for 3 of the 9 tools. |
| 9 | `tool_guard.py` holds middleware, state builder and verdict store | Split into `tool_guard.py`, `tool_guard_state.py`, `tool_guard_store.py` | One responsibility per file; each gets its own test task. |
| 10 | Five "independently revertable" commits | Code is revertable per commit; **migrations are not** | The alembic chain is linear (0061 → 0062 → 0063). To back out a middle phase, revert its code and keep its migration (all three are additive and nullable). |
| 11 | `keep_alive_sibling_limit = 50` is a constant | `MemoryStore.load_existing` gains `limit: int = 50` and keep-alive passes the constant | Otherwise the constant is decorative (the store hardcodes `.limit(50)`). |
| 12 | Scan page ⇒ `no_text_layer` | Also: a segment whose text-layer pages carry no text at all ⇒ `no_text_layer` | Nothing to classify; within the spec's reason vocabulary. |

**Known limits this plan does not fix (surface them before rollout step 4, "enable enforce"):**

- **Multi-call cards cannot be resumed today.** `hitl.build_resume_command` always sends ONE decision ("v1 design constraint: at most one HITL action per assistant turn"). If an enforce pass cards ≥ 2 guarded calls from one `AIMessage`, the resume pass raises the same count-mismatch `ValueError` that `LongRunningCostHITLMiddleware` and the native HITL middleware raise. It is pre-existing and shared; shadow is unaffected.
- The sync `AgentService.respond()` path stamps no audit context (and has no production caller), so the guard is inert there — the safe direction.
- 6 of the 9 guarded tools have no `_SUMMARY_BUILDERS` entry; in `enforce` their card shows ids plus the guard note, exactly as interactive mode shows them today.

## File map

**Create**

| File | Responsibility |
|---|---|
| `backend/app/services/system_one/__init__.py` | Re-exports |
| `backend/app/services/system_one/client.py` | Question/answer types, validation, `ask()`, `cap()`, `serialize_state()`, `SystemOneUnavailable`, `requests_post` |
| `backend/app/services/system_one/CLAUDE.md` | Subsystem guide |
| `backend/app/services/deep_agent/tool_guard_policy.py` | `GuardPredicate`, `GUARD_POLICY`, `validate_policy()` |
| `backend/app/services/deep_agent/tool_guard_store.py` | `args_fingerprint`, `find_verdict`, `commit_verdict`, `record_structural`, `mark_interrupted` |
| `backend/app/services/deep_agent/tool_guard_state.py` | `load_user_request`, `build_guard_state` |
| `backend/app/services/deep_agent/tool_guard.py` | `ToolGuardMiddleware` |
| `backend/app/services/deep_agent/memory/keep_alive.py` | Keep-alive question, state builder, `score_pending` |
| `backend/app/services/confirmations/family_check.py` | `FAMILY_DESCRIPTIONS`, `FamilyCheck`, `check_family` |
| `backend/alembic/versions/0061_tool_guard_verdicts.py` | Verdict table |
| `backend/alembic/versions/0062_memory_keep_alive.py` | Five keep-alive columns |
| `backend/alembic/versions/0063_extracted_trade_family_check.py` | `family_check` JSON column |
| `tests/_system_one_fakes.py` | `FakePost`, `JevPost`, `ScorePost`, `ChoicePost` test doubles |
| `tests/test_system_one_settings.py`, `tests/test_system_one_redaction.py`, `tests/test_system_one_client.py` | Phase A |
| `tests/test_tool_guard_policy.py`, `tests/test_migration_0061_tool_guard_verdicts.py`, `tests/test_tool_guard_store.py`, `tests/test_tool_guard_state.py`, `tests/test_tool_guard_middleware.py`, `tests/test_tool_guard_registration.py`, `tests/test_audit_context_stamping.py`, `tests/test_audit_guard_verdicts_api.py` | Phase B |
| `tests/test_tool_guard_enforce.py`, `tests/test_tool_guard_card.py` | Phase C |
| `tests/test_memory_keep_alive_config.py`, `tests/test_migration_0062_memory_keep_alive.py`, `tests/test_memory_keep_alive_invalidation.py`, `tests/test_memory_keep_alive.py`, `tests/test_memory_keep_alive_queue.py`, `tests/test_memory_keep_alive_api.py` | Phase D |
| `tests/test_migration_0063_family_check.py`, `tests/test_confirmation_family_check.py`, `tests/test_confirmations_family_check_service.py`, `tests/test_confirmations_family_check_surfaces.py` | Phase E |

**Modify**

`backend/app/config.py`, `tests/conftest.py`, `backend/app/services/deep_agent/audit_redaction.py`, `backend/app/models.py`, `backend/app/services/deep_agent/hitl.py`, `backend/app/services/deep_agent/orchestrator.py`, `backend/app/services/deep_agent/personas.py`, `backend/app/services/async_agents/agent.py`, `backend/app/services/agents.py`, `backend/app/routers/audit.py`, `backend/app/services/deep_agent/memory/{config,store,queue}.py`, `backend/app/routers/memory.py`, `backend/app/services/confirmations/{llm,service}.py`, `backend/app/tools/confirmations.py`, `backend/app/schemas.py`, `frontend/src/types.ts`, `frontend/src/routes/{Audit,Memory,Confirmations}.tsx` + their `.test.tsx` / `.live.test.tsx`, `CHANGELOG.md`, `README.md`, `.env.example`, `CLAUDE.md`, `backend/app/services/deep_agent/CLAUDE.md`.

---

## Phase 0 — Workspace

### Task 0: Worktree setup and baseline

**Files:** none tracked.

- [ ] **Step 1: Confirm you are on the spec branch, at the spec commit**

```bash
cd /Users/fuxinyao/open-otc-trading/.claude/worktrees/jev-system-one
git branch --show-current          # expect: worktree-jev-system-one
git log --oneline -2               # expect: ee266e9 docs(spec): Jev System One ... / f1fd32d Merge ...
git merge-base --is-ancestor f1fd32d HEAD && echo "based on local main: ok"
```

If the branch is based on a stale `origin/main` instead, stop and rebase onto local `main` first (the harness has done that before in this repo).

- [ ] **Step 2: Link the shared venv and node_modules; confirm the gitignored channel file**

```bash
ln -s /Users/fuxinyao/open-otc-trading/.venv .venv
ln -s /Users/fuxinyao/open-otc-trading/frontend/node_modules frontend/node_modules
ls config/agent_channels.yaml      # must exist, or every app.main import dies at collection
```

Trust `pytest` (its `pythonpath=["backend"]` resolves `app` to THIS worktree). A bare `.venv/bin/python -c "import app"` resolves to MAIN and will report worktree-new symbols as missing — ignore those.

- [ ] **Step 3: Enumerate exact-set pins (root CLAUDE.md rule)**

```bash
grep -rln "guard-verdicts\|agent_action_audits\|memory_entries\|extracted_trades" tests/
```

Expected: only migration tests (`test_migration_0043.py`, `test_migration_0046.py`, `test_memory_migration.py`, confirmation migration/model tests). None pin a column set or route set that this plan changes. No agent tool is added, so the four-registration rule and the tool-count pins are not triggered.

- [ ] **Step 4: Baseline suites (record pre-existing failures; do not fix them)**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-baseline.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-baseline.log"
(cd frontend && npx tsc --noEmit && echo "tsc ok")
```

Never pipe `pytest` into `tail` directly (it hides the exit code). Record any baseline failures in your notes; every later gate is "no NEW failures".

---

## Phase A — commit 1: the System One client

Phase A work is committed as `wip(jev-A): …` commits and squashed into one commit in Task 5.

### Task 1: System One settings

**Files:**
- Modify: `backend/app/config.py`
- Test: `tests/test_system_one_settings.py`

**Interfaces:**
- Produces: `Settings.system_one_enabled: bool`, `.system_one_model: str`, `.system_one_base_url: str`, `.system_one_timeout_seconds: float`, `.system_one_max_state_chars: int`, `.tool_guard_mode: str` (`"off"|"shadow"|"enforce"`), `.confirmation_family_check_enabled: bool`; module constant `TOOL_GUARD_MODES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_system_one_settings.py
"""System One settings (spec 2026-09-21 §0 Settings). Inert by default (D6)."""
from __future__ import annotations

import logging

import pytest

from app.config import TOOL_GUARD_MODES, Settings

_VARS = (
    "OPEN_OTC_SYSTEM_ONE", "OPEN_OTC_SYSTEM_ONE_MODEL", "OPEN_OTC_SYSTEM_ONE_BASE_URL",
    "OPEN_OTC_SYSTEM_ONE_TIMEOUT_S", "OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS",
    "OPEN_OTC_TOOL_GUARD", "OPEN_OTC_CONFIRMATION_FAMILY_CHECK",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_inert_and_match_the_spec():
    s = Settings()
    assert s.system_one_enabled is False
    assert s.system_one_model == "typesafe/jev-1.13"
    assert s.system_one_base_url == "https://zenmux.ai/api/v1"
    assert s.system_one_timeout_seconds == 5.0
    assert s.system_one_max_state_chars == 60000
    assert s.tool_guard_mode == "shadow"
    assert s.confirmation_family_check_enabled is True
    assert TOOL_GUARD_MODES == ("off", "shadow", "enforce")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MODEL", "typesafe/jev-latest")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_BASE_URL", "http://localhost:9/api/v1")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_TIMEOUT_S", "2.5")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "1234")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "enforce")
    monkeypatch.setenv("OPEN_OTC_CONFIRMATION_FAMILY_CHECK", "false")
    s = Settings()
    assert s.system_one_enabled is True
    assert s.system_one_model == "typesafe/jev-latest"
    assert s.system_one_base_url == "http://localhost:9/api/v1"
    assert s.system_one_timeout_seconds == 2.5
    assert s.system_one_max_state_chars == 1234
    assert s.tool_guard_mode == "enforce"
    assert s.confirmation_family_check_enabled is False


@pytest.mark.parametrize("raw, expected", [
    ("OFF", "off"), (" Shadow ", "shadow"), ("enforce", "enforce"),
])
def test_guard_mode_is_normalised(monkeypatch, raw, expected):
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", raw)
    assert Settings().tool_guard_mode == expected


@pytest.mark.parametrize("raw", ["enforced", "on", "", "block"])
def test_unknown_guard_mode_fails_closed_to_shadow(monkeypatch, caplog, raw):
    """Never to `off` (a typo must not stop measurement), never to `enforce`."""
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", raw)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert Settings().tool_guard_mode == "shadow"
    assert "OPEN_OTC_TOOL_GUARD" in caplog.text


def test_direct_construction_coerces_like_the_env_path():
    s = Settings(system_one_enabled="on", confirmation_family_check_enabled="0",
                 system_one_timeout_seconds="3", system_one_max_state_chars="99",
                 tool_guard_mode="bogus")
    assert s.system_one_enabled is True
    assert s.confirmation_family_check_enabled is False
    assert s.system_one_timeout_seconds == 3.0
    assert s.system_one_max_state_chars == 99
    assert s.tool_guard_mode == "shadow"


def test_non_positive_timeout_is_rejected():
    with pytest.raises(ValueError, match="system_one_timeout_seconds"):
        Settings(system_one_timeout_seconds=0)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_system_one_settings.py -q`
Expected: FAIL — `ImportError: cannot import name 'TOOL_GUARD_MODES'`.

- [ ] **Step 3: Implement**

In `backend/app/config.py`:

(a) Add `import logging` below `import os`, and after the imports:

```python
logger = logging.getLogger(__name__)
```

(b) After `_coerce_bool`, add:

```python
#: System One tool-guard modes (spec 2026-09-21 §0).
TOOL_GUARD_MODES: tuple[str, ...] = ("off", "shadow", "enforce")


def _normalize_tool_guard_mode(value: Any) -> str:
    """`off` | `shadow` | `enforce`; anything else FAILS CLOSED to `shadow`.

    Never to `off` — a typo must not silently stop measurement — and never to
    `enforce` — a typo must not start blocking.
    """
    mode = str(value if value is not None else "").strip().lower()
    if mode in TOOL_GUARD_MODES:
        return mode
    logger.warning(
        "OPEN_OTC_TOOL_GUARD=%r is not one of %s; using 'shadow'",
        value, "/".join(TOOL_GUARD_MODES),
    )
    return "shadow"
```

(c) In `_EnvironmentSettings`, after `desk_region`:

```python
    # System One (TypeSafe Jev), spec 2026-09-21. The master switch is the
    # data-policy opt-in (D15): while it is false no request is ever made.
    system_one_enabled: bool = Field(False, validation_alias="OPEN_OTC_SYSTEM_ONE")
    system_one_model: str = Field(
        "typesafe/jev-1.13", validation_alias="OPEN_OTC_SYSTEM_ONE_MODEL"
    )
    system_one_base_url: str = Field(
        "https://zenmux.ai/api/v1", validation_alias="OPEN_OTC_SYSTEM_ONE_BASE_URL"
    )
    system_one_timeout_seconds: float = Field(
        5.0, validation_alias="OPEN_OTC_SYSTEM_ONE_TIMEOUT_S"
    )
    system_one_max_state_chars: int = Field(
        60000, validation_alias="OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS"
    )
    # A plain str, normalised in Settings.__post_init__, so an unknown value
    # degrades to "shadow" with a warning instead of failing app start-up.
    tool_guard_mode: str = Field("shadow", validation_alias="OPEN_OTC_TOOL_GUARD")
    confirmation_family_check_enabled: bool = Field(
        True, validation_alias="OPEN_OTC_CONFIRMATION_FAMILY_CHECK"
    )
```

(d) In the `Settings` dataclass, after `desk_region`:

```python
    system_one_enabled: bool = field(
        default_factory=lambda: _env_value("system_one_enabled")
    )
    system_one_model: str = field(
        default_factory=lambda: _env_value("system_one_model")
    )
    system_one_base_url: str = field(
        default_factory=lambda: _env_value("system_one_base_url")
    )
    system_one_timeout_seconds: float = field(
        default_factory=lambda: _env_value("system_one_timeout_seconds")
    )
    system_one_max_state_chars: int = field(
        default_factory=lambda: _env_value("system_one_max_state_chars")
    )
    tool_guard_mode: str = field(default_factory=lambda: _env_value("tool_guard_mode"))
    confirmation_family_check_enabled: bool = field(
        default_factory=lambda: _env_value("confirmation_family_check_enabled")
    )
```

(e) At the end of `Settings.__post_init__`:

```python
        object.__setattr__(
            self, "system_one_enabled", _coerce_bool(self.system_one_enabled)
        )
        object.__setattr__(
            self,
            "confirmation_family_check_enabled",
            _coerce_bool(self.confirmation_family_check_enabled),
        )
        timeout = float(self.system_one_timeout_seconds)
        if timeout <= 0:
            raise ValueError("system_one_timeout_seconds must be positive")
        object.__setattr__(self, "system_one_timeout_seconds", timeout)
        object.__setattr__(
            self, "system_one_max_state_chars", max(1, int(self.system_one_max_state_chars))
        )
        object.__setattr__(
            self, "tool_guard_mode", _normalize_tool_guard_mode(self.tool_guard_mode)
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_system_one_settings.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py tests/test_system_one_settings.py
git commit -m "wip(jev-A): System One settings"
```

### Task 2: Outbound secret masking

**Files:**
- Modify: `backend/app/services/deep_agent/audit_redaction.py`
- Test: `tests/test_system_one_redaction.py`

**Interfaces:**
- Produces: `mask_text(text: str) -> str`, `mask_secrets(value: Any) -> Any` (new structure; never mutates input). Uses the existing `_SECRET_KEY_RE` for dict keys.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_system_one_redaction.py
"""The single outbound sanitizer (D16): the audit trail's key regex for dict
keys, plus token patterns inside free text."""
from __future__ import annotations

import copy

from app.services.deep_agent.audit_redaction import mask_secrets, mask_text

TOKEN = "sk-" + "A1b2C3d4E5f6G7h8I9j0"


def test_masks_openai_style_token_in_free_text():
    assert TOKEN not in mask_text(f"use key {TOKEN} for this")
    assert "[REDACTED]" in mask_text(f"use key {TOKEN} for this")


def test_masks_bearer_and_key_value_pairs_but_keeps_the_label():
    out = mask_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz password=hunter2")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "hunter2" not in out
    assert "password=[REDACTED]" in out


def test_leaves_ordinary_desk_text_alone():
    text = "Void cashflow 9300 and close position 27 (KO at 105%)."
    assert mask_text(text) == text


def test_masks_nested_strings_and_secret_keys_without_mutating_input():
    state = {
        "user_request": f"here is {TOKEN}",
        "pending_tool_call": {"name": "x", "args": {"api_key": "zzz", "note": ["ok", TOKEN]}},
        "count": 3,
    }
    before = copy.deepcopy(state)
    out = mask_secrets(state)
    assert TOKEN not in str(out)
    assert out["pending_tool_call"]["args"]["api_key"] == "[REDACTED]"
    assert out["pending_tool_call"]["args"]["note"][0] == "ok"
    assert out["count"] == 3
    assert state == before
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_system_one_redaction.py -q`
Expected: FAIL — `ImportError: cannot import name 'mask_secrets'`.

- [ ] **Step 3: Implement** — append to `backend/app/services/deep_agent/audit_redaction.py`:

```python
# Free-text token patterns. The key regex above catches `{"api_key": ...}`;
# these catch the same secrets pasted into prose, which is how they arrive in a
# user request or a tool result that is about to leave the process.
_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b(\s*[:=]\s*)[^\s,;]+"),
        r"\1\2[REDACTED]",
    ),
)


def mask_text(text: str) -> str:
    """Mask secret-looking tokens inside free text; the label stays readable."""
    for pattern, replacement in _SECRET_TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask_secrets(value: Any) -> Any:
    """Recursively mask secrets in a JSON-able value before it leaves the process.

    The System One client runs this over `state` at its single exit (D16), so no
    feature can forget it. Returns a new structure; never mutates the input.
    """
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY_RE.search(str(key)) else mask_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_secrets(item) for item in value]
    return value
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_system_one_redaction.py tests/test_audit_redaction.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/audit_redaction.py tests/test_system_one_redaction.py
git commit -m "wip(jev-A): outbound secret masking"
```

### Task 3: Client types, question validation, `cap`, `serialize_state`

**Files:**
- Create: `backend/app/services/system_one/__init__.py`, `backend/app/services/system_one/client.py`
- Test: `tests/test_system_one_client.py` (first half)

**Interfaces:**
- Produces: `Noul(instructions)`, `Choice(instructions, criteria: Mapping[str,str])`, `Score(instructions, criteria: Sequence[str])`, `Question = Noul|Choice|Score`; `NoulAnswer(probability)`, `ChoiceAnswer(choice, confidence, probabilities)`, `ScoreAnswer(score, confidence, probabilities, levels)` with `.normalized`; `SystemOneResult(answers, model, latency_ms)`; `SystemOneUnavailable(reason, *, latency_ms=None, detail=None)` with `.reason/.latency_ms/.detail`; `UNAVAILABLE_REASONS`; `validate_questions(questions) -> None`; `question_payload(q) -> dict`; `cap(text, n) -> str`; `serialize_state(state) -> str`; `is_enabled(settings=None) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_system_one_client.py
"""System One client (spec 2026-09-21 §0). No test makes a live call."""
from __future__ import annotations

import datetime as dt
import decimal
import json

import pytest

from app.config import Settings
from app.services.system_one import client as s1
from app.services.system_one.client import (
    UNAVAILABLE_REASONS, Choice, Noul, Score, ScoreAnswer, SystemOneUnavailable,
    cap, is_enabled, question_payload, serialize_state, validate_questions,
)


# --- types, validation, helpers -------------------------------------------

def test_unavailable_reasons_are_the_spec_five():
    assert UNAVAILABLE_REASONS == (
        "no_key", "state_too_large", "timeout", "http_error", "bad_response")


def test_unavailable_rejects_an_unknown_reason():
    with pytest.raises(ValueError):
        SystemOneUnavailable("flaky")


def test_score_normalized_is_score_over_levels_minus_one():
    assert ScoreAnswer(score=1.98, confidence=0.98, probabilities={}, levels=3).normalized == pytest.approx(0.99)
    assert ScoreAnswer(score=1.5, confidence=0.5, probabilities={}, levels=4).normalized == pytest.approx(0.5)


def test_question_payload_shapes():
    assert question_payload(Noul("x is true")) == {"type": "noul", "instructions": "x is true"}
    assert question_payload(Choice("which", {"a": "A", "b": "B"})) == {
        "type": "choice", "instructions": "which", "criteria": {"a": "A", "b": "B"}}
    assert question_payload(Score("how", ("lo", "hi"))) == {
        "type": "score", "instructions": "how", "criteria": ["lo", "hi"]}


@pytest.mark.parametrize("questions", [
    {},
    {"q": Noul("")},
    {"q": Noul("   ")},
    {"": Noul("x")},
    {"q": Score("s", ["only one"])},
    {"q": Score("s", [str(i) for i in range(11)])},
    {"q": Score("s", ["lo", ""])},
    {"q": Choice("c", {"a": "A"})},
    {"q": Choice("c", {str(i): "d" for i in range(256)})},
    {"q": Choice("c", {"a": "A", "": "B"})},
    {"q": Choice("c", {"a": "A", "b": ""})},
    {"q": "not a question"},
])
def test_programmer_errors_raise_value_error(questions):
    """The server answers a malformed body with an opaque 400, so catch it here."""
    with pytest.raises(ValueError):
        validate_questions(questions)


def test_valid_bounds_pass():
    validate_questions({
        "a": Noul("x"),
        "b": Score("s", ["lo", "hi"]),
        "c": Score("s", [str(i) for i in range(10)]),
        "d": Choice("c", {"a": "A", "b": "B"}),
        "e": Choice("c", {str(i): "d" for i in range(255)}),
    })


def test_cap_is_exact_length_and_marked():
    assert cap("abc", 3) == "abc"
    assert cap("abcdef", 4) == "abc…"
    assert len(cap("x" * 5000, 4000)) == 4000


def test_serialize_state_is_compact_sorted_non_ascii_and_total():
    out = serialize_state({"b": "é", "a": [1, dt.date(2026, 9, 21), decimal.Decimal("1.5")]})
    assert out == '{"a":[1,"2026-09-21","1.5"],"b":"é"}'


def test_is_enabled_reads_only_the_master_switch():
    assert is_enabled(Settings(system_one_enabled=True, tool_guard_mode="off")) is True
    assert is_enabled(Settings(system_one_enabled=False)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_system_one_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.system_one'`.

- [ ] **Step 3: Implement**

`backend/app/services/system_one/client.py`:

```python
"""System One (TypeSafe Jev) client — the single exit for every Jev request.

Spec: docs/superpowers/specs/2026-09-21-jev-system-one-design.md §0.

Jev is non-generative: `state` + typed questions in, calibrated probabilities
out. It cannot generate text, call tools or see images, so it is never a persona
model, an arena contestant, or a source of numbers. ZenMux serves it on
`/systemone`, a route absent from `/v1/models`.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Union

from ...config import Settings, get_settings
from ..deep_agent.audit_redaction import mask_secrets, redact_text

UNAVAILABLE_REASONS: tuple[str, ...] = (
    "no_key", "state_too_large", "timeout", "http_error", "bad_response",
)
_DETAIL_CHARS = 500


@dataclass(frozen=True)
class Noul:
    """Yes/no: the answer is the probability that `instructions` is TRUE."""

    instructions: str


@dataclass(frozen=True)
class Choice:
    """One of N options; `criteria` maps option -> description."""

    instructions: str
    criteria: Mapping[str, str]


@dataclass(frozen=True)
class Score:
    """Ordered levels, low -> high, 0-based. Describe situations, not degrees."""

    instructions: str
    criteria: Sequence[str]


Question = Union[Noul, Choice, Score]


@dataclass(frozen=True)
class NoulAnswer:
    probability: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: Mapping[str, float]
    levels: int

    @property
    def normalized(self) -> float:
        """`score` mapped onto [0, 1]."""
        return self.score / (self.levels - 1)


Answer = Union[NoulAnswer, ChoiceAnswer, ScoreAnswer]


@dataclass(frozen=True)
class SystemOneResult:
    answers: dict[str, Answer]  # keyed as asked
    model: str                  # the response's "model", else the requested one
    latency_ms: int             # wall clock around post()


class SystemOneUnavailable(RuntimeError):
    """The call could not be made or answered. `reason` is persisted verbatim."""

    def __init__(
        self, reason: str, *, latency_ms: int | None = None, detail: str | None = None
    ) -> None:
        if reason not in UNAVAILABLE_REASONS:
            raise ValueError(f"unknown System One unavailable reason {reason!r}")
        super().__init__(f"System One unavailable: {reason}")
        self.reason = reason
        self.latency_ms = latency_ms
        self.detail = detail


def is_enabled(settings: Settings | None = None) -> bool:
    """The MASTER switch only. Callers also check their own feature switch (D17)."""
    return bool((settings or get_settings()).system_one_enabled)


def cap(text: str, n: int) -> str:
    """Bound `text` to `n` code points; a capped value is exactly `n` long, ending "…"."""
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def serialize_state(state: Any) -> str:
    """The one serialization `ask()` measures and sends.

    `default=str` is the safety net for a datetime / Decimal / Enum inside tool
    args, so an odd argument never crashes a caller.
    """
    return json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )


def _nonempty(text: Any) -> bool:
    return isinstance(text, str) and bool(text.strip())


def validate_questions(questions: Mapping[str, Question]) -> None:
    """Raise ValueError on a programmer error.

    The server answers a malformed body with an opaque
    `400 "Server encountered an unexpected error"`, so catch it locally.
    """
    if not questions:
        raise ValueError("System One: questions must not be empty")
    for key, question in questions.items():
        if not _nonempty(key):
            raise ValueError("System One: question keys must be non-empty strings")
        if not isinstance(question, (Noul, Choice, Score)):
            raise ValueError(f"System One: question {key!r} has unsupported type")
        if not _nonempty(question.instructions):
            raise ValueError(f"System One: question {key!r} has empty instructions")
        if isinstance(question, Choice):
            if not 2 <= len(question.criteria) <= 255:
                raise ValueError(f"System One: choice {key!r} needs 2-255 options")
            for option, description in question.criteria.items():
                if not _nonempty(option) or not _nonempty(description):
                    raise ValueError(f"System One: choice {key!r} has an empty option")
        if isinstance(question, Score):
            if not 2 <= len(question.criteria) <= 10:
                raise ValueError(f"System One: score {key!r} needs 2-10 levels")
            if not all(_nonempty(level) for level in question.criteria):
                raise ValueError(f"System One: score {key!r} has an empty level")


def question_payload(question: Question) -> dict[str, Any]:
    if isinstance(question, Noul):
        return {"type": "noul", "instructions": question.instructions}
    if isinstance(question, Choice):
        return {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": dict(question.criteria),
        }
    return {
        "type": "score",
        "instructions": question.instructions,
        "criteria": list(question.criteria),
    }
```

`backend/app/services/system_one/__init__.py`:

```python
"""System One (TypeSafe Jev): calibrated classification over ZenMux.

Spec: docs/superpowers/specs/2026-09-21-jev-system-one-design.md. Guide: CLAUDE.md here.
"""
from .client import (
    UNAVAILABLE_REASONS,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    SystemOneResult,
    SystemOneUnavailable,
    cap,
    is_enabled,
    serialize_state,
)

__all__ = [
    "UNAVAILABLE_REASONS", "Choice", "ChoiceAnswer", "Noul", "NoulAnswer", "Question",
    "Score", "ScoreAnswer", "SystemOneResult", "SystemOneUnavailable", "cap",
    "is_enabled", "serialize_state",
]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_system_one_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/system_one tests/test_system_one_client.py
git commit -m "wip(jev-A): System One question types and validation"
```

### Task 4: `ask()` — sanitize, budget, transport errors, response validation

**Files:**
- Modify: `backend/app/services/system_one/client.py`, `backend/app/services/system_one/__init__.py`, `tests/conftest.py`
- Create: `tests/_system_one_fakes.py`
- Test: `tests/test_system_one_client.py` (append)

**Interfaces:**
- Consumes: Task 3 types; `mask_secrets` (Task 2).
- Produces: `ask(state, questions, *, post=None, settings=None) -> SystemOneResult` (raises `SystemOneUnavailable` or `ValueError`; never reads the master switch); `requests_post(url, payload, timeout) -> dict`; module global `_default_post` (patched by conftest). `post` contract: `TimeoutError` → `timeout`, `ValueError` → `bad_response`, anything else → `http_error`.
- Produces (tests): `tests/_system_one_fakes.py::PROBE_RESPONSE`, `FakePost(response=None, *, exc=None)` with `.calls: list[(url, payload, timeout)]`.

- [ ] **Step 1: Create the fakes module**

```python
# tests/_system_one_fakes.py
"""Test doubles for System One (Jev).

No test may reach the real POST — conftest's autouse `_no_live_system_one`
fails any that tries. Inject one of these as `post=`, or patch
`app.services.system_one.client._default_post` with one.
"""
from __future__ import annotations

import copy
from typing import Any

#: Verbatim shape of the 2026-09-21 probe response (spec §0).
PROBE_RESPONSE: dict[str, Any] = {
    "model": "typesafe/jev-1.13",
    "answers": {
        "q_noul": {"type": "noul", "noul": 0.95},
        "q_score": {"type": "score", "score": 1.98, "confidence": 0.98,
                    "legend": {"0": "…", "1": "…", "2": "…"},
                    "probabilities": {"0": 0, "1": 0.01, "2": 0.99}},
        "q_choice": {"type": "choice", "choice": "ops", "confidence": 1,
                     "probabilities": {"ops": 1, "trader": 0, "risk": 0}},
    },
    "usage": {"input_tokens": 517, "output_tokens": 70},
}


class FakePost:
    """Canned-response `post` seam; records every (url, payload, timeout)."""

    def __init__(self, response: Any = None, *, exc: BaseException | None = None) -> None:
        self.response = PROBE_RESPONSE if response is None else response
        self.exc = exc
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append((url, copy.deepcopy(payload), timeout))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.response)
```

- [ ] **Step 2: Write the failing tests** — append to `tests/test_system_one_client.py`:

```python
# --- ask() ------------------------------------------------------------------

import copy  # noqa: E402

import requests  # noqa: E402

from _system_one_fakes import PROBE_RESPONSE, FakePost  # noqa: E402
from app.services.system_one.client import ask, requests_post  # noqa: E402

QUESTIONS = {
    "q_noul": Noul("The user asked for this action"),
    "q_score": Score("How durable is this fact?", ["low", "mid", "high"]),
    "q_choice": Choice("Which desk owns this?", {
        "ops": "operations", "trader": "trading", "risk": "risk management"}),
}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def test_request_shape():
    post = FakePost()
    ask({"x": 1}, QUESTIONS, post=post)
    url, payload, timeout = post.calls[0]
    assert url == "https://zenmux.ai/api/v1/systemone"
    assert timeout == 5.0
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"x": 1}
    assert payload["questions"]["q_noul"] == {"type": "noul", "instructions": "The user asked for this action"}
    assert payload["questions"]["q_score"]["criteria"] == ["low", "mid", "high"]
    assert payload["questions"]["q_choice"]["criteria"]["ops"] == "operations"


def test_parses_the_three_verbatim_probe_shapes():
    result = ask({"x": 1}, QUESTIONS, post=FakePost())
    assert result.model == "typesafe/jev-1.13"
    assert result.answers["q_noul"].probability == 0.95
    score = result.answers["q_score"]
    assert (score.score, score.confidence, score.levels) == (1.98, 0.98, 3)
    assert score.normalized == pytest.approx(0.99)
    assert score.probabilities == {"0": 0.0, "1": 0.01, "2": 0.99}
    choice = result.answers["q_choice"]
    assert (choice.choice, choice.confidence) == ("ops", 1.0)
    assert choice.probabilities == {"ops": 1.0, "trader": 0.0, "risk": 0.0}
    assert isinstance(result.latency_ms, int)


def test_model_falls_back_to_the_requested_one():
    body = copy.deepcopy(PROBE_RESPONSE)
    del body["model"]
    assert ask({}, QUESTIONS, post=FakePost(body)).model == "typesafe/jev-1.13"


def test_extra_answer_keys_legend_and_usage_are_ignored():
    body = copy.deepcopy(PROBE_RESPONSE)
    body["answers"]["unasked"] = {"type": "noul", "noul": 7}
    assert ask({}, QUESTIONS, post=FakePost(body)).answers.keys() == QUESTIONS.keys()


def test_a_missing_probability_key_reads_as_zero():
    body = copy.deepcopy(PROBE_RESPONSE)
    del body["answers"]["q_choice"]["probabilities"]["risk"]
    del body["answers"]["q_score"]["probabilities"]["0"]
    result = ask({}, QUESTIONS, post=FakePost(body))
    assert result.answers["q_choice"].probabilities["risk"] == 0.0
    assert result.answers["q_score"].probabilities["0"] == 0.0


def _mutate(path_fn):
    body = copy.deepcopy(PROBE_RESPONSE)
    path_fn(body)
    return body


@pytest.mark.parametrize("body", [
    "not an object",
    _mutate(lambda b: b.pop("answers")),
    _mutate(lambda b: b.__setitem__("answers", [])),
    _mutate(lambda b: b["answers"].pop("q_noul")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("type", "score")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", 1.2)),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", "0.5")),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", True)),
    _mutate(lambda b: b["answers"]["q_noul"].__setitem__("noul", float("nan"))),
    _mutate(lambda b: b["answers"]["q_score"].__setitem__("score", 2.01)),
    _mutate(lambda b: b["answers"]["q_score"].__setitem__("confidence", 1.5)),
    _mutate(lambda b: b["answers"]["q_score"].pop("probabilities")),
    _mutate(lambda b: b["answers"]["q_score"]["probabilities"].__setitem__("3", 0)),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("choice", "board")),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("choice", ["ops"])),
    _mutate(lambda b: b["answers"]["q_choice"].__setitem__("probabilities", [1, 0, 0])),
    _mutate(lambda b: b["answers"]["q_choice"]["probabilities"].__setitem__("board", 0)),
    _mutate(lambda b: b["answers"]["q_choice"]["probabilities"].pop("ops")),
])
def test_every_validation_rule_is_bad_response(body):
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=FakePost(body))
    assert exc.value.reason == "bad_response"
    assert isinstance(exc.value.latency_ms, int)


def test_no_key(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    post = FakePost()
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=post)
    assert (exc.value.reason, exc.value.latency_ms) == ("no_key", None)
    assert post.calls == []


def test_state_budget_counts_code_points_after_sanitizing_and_never_truncates():
    at_budget = Settings(system_one_max_state_chars=10)
    post = FakePost()
    ask("é" * 8, QUESTIONS, post=post, settings=at_budget)      # '"éééééééé"' = 10
    assert post.calls[0][1]["state"] == "é" * 8                  # sent whole
    with pytest.raises(SystemOneUnavailable) as exc:
        ask("é" * 9, QUESTIONS, post=post, settings=at_budget)
    assert (exc.value.reason, exc.value.latency_ms) == ("state_too_large", None)
    assert len(post.calls) == 1


@pytest.mark.parametrize("error, reason", [
    (TimeoutError("slow"), "timeout"),
    (ValueError("Expecting value: line 1 column 1"), "bad_response"),
    (json.JSONDecodeError("bad", "doc", 0), "bad_response"),
    (RuntimeError("HTTP 502: bad gateway"), "http_error"),
    (ConnectionError("refused"), "http_error"),
])
def test_transport_errors_map_deterministically(error, reason):
    with pytest.raises(SystemOneUnavailable) as exc:
        ask({}, QUESTIONS, post=FakePost(exc=error))
    assert exc.value.reason == reason
    assert isinstance(exc.value.latency_ms, int)
    assert exc.value.detail and len(exc.value.detail) <= 500


def test_sanitizer_masks_state_but_never_touches_questions():
    token = "sk-" + "Z" * 24
    questions = {"q_noul": Noul("password: shown here is part of the predicate")}
    body = {"answers": {"q_noul": {"type": "noul", "noul": 0.1}}}
    post = FakePost(body)
    ask({"note": f"use {token}", "nested": {"api_key": "zzz"}}, questions, post=post)
    sent = post.calls[0][1]
    assert token not in json.dumps(sent["state"])
    assert sent["state"]["nested"]["api_key"] == "[REDACTED]"
    assert sent["questions"]["q_noul"]["instructions"] == "password: shown here is part of the predicate"


def test_state_objects_are_sent_after_the_default_str_round_trip():
    post = FakePost({"answers": {"q_noul": {"type": "noul", "noul": 0.5}}})
    ask({"when": dt.datetime(2026, 9, 21, 9, 30)}, {"q_noul": Noul("x")}, post=post)
    assert post.calls[0][1]["state"] == {"when": "2026-09-21 09:30:00"}


def test_ask_does_not_read_the_master_switch(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "false")
    assert ask({}, QUESTIONS, post=FakePost()).answers["q_noul"].probability == 0.95


def test_ask_validates_questions_before_anything_else(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    with pytest.raises(ValueError):
        ask({}, {}, post=FakePost())


# --- requests_post translation --------------------------------------------

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        return self._body


def test_requests_post_translates_failures(monkeypatch):
    def raise_(exc):
        def _post(*a, **k):
            raise exc
        return _post

    monkeypatch.setattr(requests, "post", raise_(requests.Timeout("t")))
    with pytest.raises(TimeoutError):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", raise_(requests.ConnectionError("c")))
    with pytest.raises(RuntimeError):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", raise_(requests.exceptions.InvalidURL("u")))
    with pytest.raises(RuntimeError):   # NOT ValueError: it is not a body problem
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(502, text="bad gateway"))
    with pytest.raises(RuntimeError, match="HTTP 502"):
        requests_post("http://x/systemone", {}, 1.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200))
    with pytest.raises(ValueError):
        requests_post("http://x/systemone", {}, 1.0)


def test_requests_post_sends_the_bearer_key(monkeypatch):
    seen = {}

    def _post(url, json, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(requests, "post", _post)
    assert requests_post("http://x/systemone", {"a": 1}, 2.0) == {"ok": True}
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["timeout"] == 2.0
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_system_one_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'ask'`.

- [ ] **Step 4: Implement** — append to `backend/app/services/system_one/client.py`:

```python
# --- transport ---------------------------------------------------------------

PostFn = Callable[[str, dict, float], Any]


def requests_post(url: str, payload: dict, timeout: float) -> Any:
    """Production transport. Its exception contract makes reason-mapping exact:
    TimeoutError -> timeout; ValueError (a 2xx with a non-JSON body) ->
    bad_response; anything else (RuntimeError) -> http_error.
    """
    import requests  # lazy: never needed on a test path

    headers = {
        "Authorization": f"Bearer {os.environ.get('ZENMUX_API_KEY', '').strip()}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise TimeoutError(str(exc)) from exc
    except requests.RequestException as exc:
        # Several RequestExceptions (InvalidURL, MissingSchema, ...) are ALSO
        # ValueErrors; re-raise as RuntimeError so they map to http_error, not
        # to bad_response.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()  # ValueError on a non-JSON body -> bad_response


#: Module-level so the test suite can replace it (conftest `_no_live_system_one`).
_default_post: PostFn = requests_post


# --- response validation ----------------------------------------------------

class _BadResponse(ValueError):
    """A 2xx body that fails validation (internal)."""


def _unit(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _BadResponse(f"{what} is not a number")
    number = float(value)
    if not 0.0 <= number <= 1.0:  # NaN fails this too
        raise _BadResponse(f"{what} is outside [0, 1]")
    return number


def _probabilities(raw: Any, allowed: Sequence[str], what: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise _BadResponse(f"{what}.probabilities is missing or not an object")
    unknown = set(raw) - set(allowed)
    if unknown:
        raise _BadResponse(f"{what}.probabilities has unknown keys {sorted(map(str, unknown))}")
    # A missing key reads as 0.0: only 3-option / 3-level answers were ever
    # observed, and rejecting an omitted zero would make a 16-family check a
    # permanent bad_response on an assumption nobody measured.
    return {
        key: _unit(raw[key], f"{what}.probabilities[{key}]") if key in raw else 0.0
        for key in allowed
    }


def _parse_answer(key: str, question: Question, raw: Any) -> Answer:
    if not isinstance(raw, dict):
        raise _BadResponse(f"answer {key!r} is not an object")
    expected = "noul" if isinstance(question, Noul) else (
        "choice" if isinstance(question, Choice) else "score")
    if raw.get("type") != expected:
        raise _BadResponse(f"answer {key!r} has type {raw.get('type')!r}, asked {expected!r}")
    if isinstance(question, Noul):
        return NoulAnswer(probability=_unit(raw.get("noul"), f"{key}.noul"))
    if isinstance(question, Choice):
        choice = raw.get("choice")
        if not isinstance(choice, str) or choice not in question.criteria:
            raise _BadResponse(f"{key}.choice {choice!r} is not an option asked")
        probabilities = _probabilities(raw.get("probabilities"), list(question.criteria), key)
        if choice not in raw["probabilities"]:
            raise _BadResponse(f"{key}.choice {choice!r} is absent from probabilities")
        return ChoiceAnswer(
            choice=choice,
            confidence=_unit(raw.get("confidence"), f"{key}.confidence"),
            probabilities=probabilities,
        )
    levels = len(question.criteria)
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not (
        0.0 <= float(score) <= levels - 1
    ):
        raise _BadResponse(f"{key}.score {score!r} is outside [0, {levels - 1}]")
    return ScoreAnswer(
        score=float(score),
        confidence=_unit(raw.get("confidence"), f"{key}.confidence"),
        probabilities=_probabilities(
            raw.get("probabilities"), [str(i) for i in range(levels)], key),
        levels=levels,
    )


def _parse_response(
    body: Any, questions: Mapping[str, Question]
) -> tuple[dict[str, Answer], str | None]:
    if not isinstance(body, dict):
        raise _BadResponse("response is not an object")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise _BadResponse("answers is missing or not an object")
    parsed: dict[str, Answer] = {}
    for key, question in questions.items():
        if key not in answers:
            raise _BadResponse(f"answer {key!r} is missing")
        parsed[key] = _parse_answer(key, question, answers[key])
    model = body.get("model")
    return parsed, model if isinstance(model, str) and model else None


# --- ask ----------------------------------------------------------------------

def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _detail(exc: BaseException) -> str:
    return redact_text(mask_secrets(str(exc))[:_DETAIL_CHARS]) or ""


def ask(
    state: Any,
    questions: Mapping[str, Question],
    *,
    post: PostFn | None = None,
    settings: Settings | None = None,
) -> SystemOneResult:
    """One Jev request answering every question in parallel (D12).

    Returns all answers or raises: `ValueError` for a programmer error in
    `questions`, `SystemOneUnavailable` for everything else. Does NOT check the
    master switch — callers gate on `is_enabled()` plus their feature switch
    first, so "disabled" is never an exception (D17). Never truncates: an
    over-budget state is rejected, not cut (the callers' projections are the
    documented summarisation).
    """
    validate_questions(questions)
    cfg = settings or get_settings()
    if not os.environ.get("ZENMUX_API_KEY", "").strip():
        raise SystemOneUnavailable("no_key")
    # D16: the single sanitizer, over `state` only — questions are
    # developer-authored constants and rewriting one would change the predicate.
    serialized = serialize_state(mask_secrets(state))
    if len(serialized) > cfg.system_one_max_state_chars:
        raise SystemOneUnavailable(
            "state_too_large",
            detail=f"{len(serialized)} > {cfg.system_one_max_state_chars} chars",
        )
    payload = {
        "model": cfg.system_one_model,
        # Send exactly what was measured (the default=str round trip included).
        "state": json.loads(serialized),
        "questions": {key: question_payload(q) for key, q in questions.items()},
    }
    url = f"{cfg.system_one_base_url.rstrip('/')}/systemone"
    post_fn = post or _default_post
    started = time.monotonic()
    try:
        body = post_fn(url, payload, float(cfg.system_one_timeout_seconds))
    except TimeoutError as exc:
        raise SystemOneUnavailable(
            "timeout", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    except ValueError as exc:
        raise SystemOneUnavailable(
            "bad_response", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — every other transport failure
        raise SystemOneUnavailable(
            "http_error", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    latency_ms = _elapsed_ms(started)
    try:
        answers, model = _parse_response(body, questions)
    except _BadResponse as exc:
        raise SystemOneUnavailable(
            "bad_response", latency_ms=latency_ms, detail=_detail(exc)) from exc
    return SystemOneResult(
        answers=answers, model=model or cfg.system_one_model, latency_ms=latency_ms)
```

Add `ask` to the import list and `__all__` in `backend/app/services/system_one/__init__.py`.

- [ ] **Step 5: Add the conftest pins** — in `tests/conftest.py`, after the `OPEN_OTC_MEMORY` setdefault line:

```python
# System One (Jev) is a paid, non-deterministic third-party call. Hard-set (not
# setdefault): a developer shell exporting OPEN_OTC_SYSTEM_ONE=true must not
# turn every AUTO-mode test into a Jev client. Tests opt in via monkeypatch.
os.environ["OPEN_OTC_SYSTEM_ONE"] = "false"
```

and after the `_bypass_capability_gate` fixture:

```python
@pytest.fixture(autouse=True)
def _no_live_system_one(monkeypatch):
    """No test may reach TypeSafe. pytest.fail raises a BaseException, so no
    `except Exception` in feature code can swallow it into an `http_error`."""
    from app.services.system_one import client as _system_one

    def _refuse(url, payload, timeout):
        pytest.fail(
            f"a test reached the live System One POST ({url}); "
            "inject post= or patch client._default_post"
        )

    monkeypatch.setattr(_system_one, "_default_post", _refuse)
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_system_one_client.py tests/test_system_one_settings.py tests/test_system_one_redaction.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/system_one tests/_system_one_fakes.py tests/test_system_one_client.py tests/conftest.py
git commit -m "wip(jev-A): System One ask() with deterministic failure mapping"
```

### Task 5: Phase A docs and the phase commit

**Files:**
- Create: `backend/app/services/system_one/CLAUDE.md`
- Modify: `CLAUDE.md` (subsystem table), `CHANGELOG.md`, `README.md`, `.env.example`

- [ ] **Step 1: Write `backend/app/services/system_one/CLAUDE.md`**

```markdown
# System One (TypeSafe Jev) — agent guidance

Part of [Open OTC Trading](../../../../CLAUDE.md). Spec:
`docs/superpowers/specs/2026-09-21-jev-system-one-design.md`.

Jev is a **non-generative** classifier: `state` + typed questions in, calibrated
probabilities out (`noul` yes/no, `choice` one-of-N, `score` ordered levels). It
cannot generate text, call tools or see images — never a persona model, never an
arena contestant, never a source of numbers.

## Reaching it

- `POST https://zenmux.ai/api/v1/systemone`, `Bearer $ZENMUX_API_KEY`,
  `"model": "typesafe/jev-1.13"`. **Absent from `/v1/models`**, and it 404s on
  chat/completions, responses and messages. To tell "exists" from "doesn't",
  compare error TYPES against a fake-slug control: a fake slug is
  `invalid_model`, the real one on a wrong endpoint is `model_not_supported`.
- A malformed body returns an opaque `400 invalid_params "Server encountered an
  unexpected error"` — that is why `validate_questions` exists.
- It is NOT a channel-registry model (D7): listing it would make it selectable
  as an agent model, and broken.

## Measured, not claimed

- Latency: **1.03 / 1.33 / 2.30 s** min/median/max over 61 calls (vendor: 70–500 ms).
  ~1 s of that is the ZenMux round-trip.
- **Not deterministic**: identical requests drift up to 0.10 (typically ≤ 0.04),
  quantised to 0.01. Anything that must be replayable (the guard's interrupt set)
  reads a COMMITTED verdict, never a fresh answer.
- **Policy goes IN the question.** A generic "is this risky / did the user ask
  for this?" predicate FAILED the ops-settlement-day step-8 trap (0.49–0.52 vs a
  correct implied step at 0.61–0.66). A policy-specific predicate separated them
  (+0.61) — post-hoc. Jev evaluates a crisply stated predicate; it does not
  supply desk judgment.

## Rules

- `client.ask()` is the **only** exit. It sanitizes `state` (never `questions`),
  budgets it by `len()` of the one serialization it sends, and maps every failure
  to one of `UNAVAILABLE_REASONS`. It never truncates and never reads the master
  switch.
- **Disabled ≠ broken (D17).** Switch off ⇒ no call, no row, field `NULL`.
  Switch on but unreachable ⇒ an explicit `unscored` record with the reason.
- Tests: conftest hard-sets `OPEN_OTC_SYSTEM_ONE=false` and replaces
  `client._default_post` with a `pytest.fail`. Inject `post=` or patch
  `_default_post` with a fake from `tests/_system_one_fakes.py`.
```

- [ ] **Step 2: Root `CLAUDE.md`** — add a row to the subsystem table, after the `gateway` row:

```markdown
| [`backend/app/services/system_one/`](backend/app/services/system_one/CLAUDE.md) | System One (TypeSafe Jev): the AUTO tool guard, memory keep-alive, the confirmation family cross-check |
```

- [ ] **Step 3: `CHANGELOG.md`** — under `## [Unreleased]`, add (create `### Added` above `### Changed` if absent):

```markdown
### Added
- **System One (TypeSafe Jev) client** — `services/system_one/`, the single exit
  for calibrated yes/no, one-of-N and ordered-level questions over ZenMux's
  `/systemone` route. Sanitizes and size-budgets `state`, maps every failure to
  `no_key` / `state_too_large` / `timeout` / `http_error` / `bad_response`.
  **Inert by default**: nothing calls it unless `OPEN_OTC_SYSTEM_ONE=true`.
```

- [ ] **Step 4: `README.md`** — in the Configuration table, after the `OPEN_OTC_MEMORY_RECONCILE_SINCE` row:

```markdown
| `OPEN_OTC_SYSTEM_ONE` | Master switch (default `false`) for TypeSafe Jev via ZenMux. **Turning it on is the data-policy opt-in**: the features below then send sanitized desk text to a new upstream (TypeSafe). Each feature has its own opt-out switch | No |
| `OPEN_OTC_SYSTEM_ONE_MODEL` / `_BASE_URL` / `_TIMEOUT_S` / `_MAX_STATE_CHARS` | Jev model slug (`typesafe/jev-1.13`), base URL (`https://zenmux.ai/api/v1`), timeout (`5.0` s), state budget (`60000` chars) | No |
```

- [ ] **Step 5: `.env.example`** — append:

```bash
# System One (TypeSafe Jev via ZenMux). OFF by default; see README "Configuration".
# OPEN_OTC_SYSTEM_ONE=false
# OPEN_OTC_SYSTEM_ONE_MODEL=typesafe/jev-1.13
# OPEN_OTC_SYSTEM_ONE_TIMEOUT_S=5.0
```

- [ ] **Step 6: Acceptance gate**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-A.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-A.log"
(cd frontend && npx tsc --noEmit && echo "tsc ok")
```

Expected: no failures beyond the Task 0 baseline.

- [ ] **Step 7: Squash Phase A into one commit**

```bash
git add backend/app/services/system_one/CLAUDE.md CLAUDE.md CHANGELOG.md README.md .env.example
git commit -m "wip(jev-A): docs"
BASE=$(git log --format=%H --grep='^wip(jev-A)' --reverse | head -1)
git reset --soft "${BASE}~1"
git commit -F - <<'MSG'
feat(system-one): Jev client — one sanitized, budgeted exit (inert by default)

Adds services/system_one: typed noul/choice/score questions, response
validation, and deterministic failure reasons. Nothing calls it yet, and
OPEN_OTC_SYSTEM_ONE defaults to false. The suite refuses any live call.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -2
```

---

## Phase B — commit 2: the tool guard in shadow mode

Shadow must be complete and green before any enforcement code exists. Commits are `wip(jev-B): …`, squashed in Task 15.

### Task 6: `GUARD_POLICY` and `validate_policy`

**Files:**
- Create: `backend/app/services/deep_agent/tool_guard_policy.py`
- Test: `tests/test_tool_guard_policy.py`

**Interfaces:**
- Produces: `GuardPredicate(key: str, instructions: str, threshold: float = 0.5, evidence: str = "untested")`; `EVIDENCE_LEVELS`; `GUARD_POLICY: dict[str, tuple[GuardPredicate, ...]]`; `validate_policy(policy, risk_levels) -> None` (raises `ValueError`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_policy.py
"""GUARD_POLICY is desk policy that fails like code (spec §1)."""
from __future__ import annotations

import math

import pytest

from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL
from app.services.deep_agent.tool_guard_policy import (
    GUARD_POLICY, GuardPredicate, validate_policy,
)

NINE = {
    "void_settlement_cashflow", "close_position", "settle_position", "mark_knockout",
    "waive_limit_incident", "resolve_limit_incident", "delete_pricing_parameter_rows",
    "remove_portfolio_sources", "import_otc_positions",
}
NAMED = "by id, by name, or by a description that identifies it unambiguously"


def test_shipped_policy_validates_against_the_live_risk_table():
    validate_policy(GUARD_POLICY, _RISK_LEVEL_BY_TOOL)


def test_scope_is_exactly_the_nine_destroy_and_terminate_tools():
    """D1 is a user decision; widening it needs a new decision, not a drive-by."""
    assert set(GUARD_POLICY) == NINE


def test_only_the_void_wording_claims_evidence():
    for tool, predicates in GUARD_POLICY.items():
        for p in predicates:
            expected = ("tested-posthoc" if (tool, p.key) == (
                "void_settlement_cashflow", "unnamed_target") else "untested")
            assert p.evidence == expected, (tool, p.key)


def test_predicate_sets_per_tool():
    for tool, predicates in GUARD_POLICY.items():
        keys = [p.key for p in predicates]
        assert keys[0] == "unnamed_target"
        assert "from_document" in keys
        assert ("clears_blocker" in keys) == (
            tool in {"void_settlement_cashflow", "close_position", "settle_position"})
        assert all(p.threshold == 0.5 for p in predicates)


def test_record_tools_define_what_named_means():
    record_tools = NINE - {"delete_pricing_parameter_rows", "remove_portfolio_sources",
                           "import_otc_positions"}
    for tool in record_tools:
        assert NAMED in GUARD_POLICY[tool][0].instructions, tool


def _good():
    return {"void_settlement_cashflow": (GuardPredicate("k", "a crisp predicate"),)}


@pytest.mark.parametrize("policy, match", [
    ({"no_such_tool": (GuardPredicate("k", "p"),)}, "dead policy"),
    ({"book_extracted_trade": (GuardPredicate("k", "p"),)}, "already gated"),
    ({"void_settlement_cashflow": ()}, "no predicates"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p"), GuardPredicate("k", "q"))}, "duplicate"),
    ({"void_settlement_cashflow": (GuardPredicate("", "p"),)}, "empty key"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "  "),)}, "empty instructions"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=0.0),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=1.0),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", threshold=math.nan),)}, "threshold"),
    ({"void_settlement_cashflow": (GuardPredicate("k", "p", evidence="tested"),)}, "evidence"),
])
def test_invalid_shapes_are_rejected(policy, match):
    with pytest.raises(ValueError, match=match):
        validate_policy(policy, _RISK_LEVEL_BY_TOOL)


def test_a_minimal_valid_policy_passes():
    validate_policy(_good(), _RISK_LEVEL_BY_TOOL)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: ... tool_guard_policy`.

- [ ] **Step 3: Implement** `backend/app/services/deep_agent/tool_guard_policy.py`:

```python
"""GUARD_POLICY — the desk's per-tool predicates for the System One tool guard.

Spec 2026-09-21 §1. Desk policy, deliberately a plain data table: edit wording or
a threshold here without touching middleware. Every wording is written out in
full because the words ARE the policy. HIGH probability = flag.

Evidence is honest: only `void_settlement_cashflow.unnamed_target` has any, and
it is post-hoc (written after the step-8 failure, tested on the same cases).
Everything "untested" is exactly what shadow mode exists to measure. "Named"
means by id, by name, or by an unambiguous description — only id references
were ever tested.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

EVIDENCE_LEVELS = frozenset({"tested-posthoc", "untested"})


@dataclass(frozen=True)
class GuardPredicate:
    key: str
    instructions: str        # a crisply stated predicate; HIGH probability = flag
    threshold: float = 0.5   # mid-gap of the measured separation (flag >= 0.73, clear <= 0.12)
    evidence: str = "untested"


# Identical wording for every tool (spec §1, "two further predicates").
_FROM_DOCUMENT = GuardPredicate(
    key="from_document",
    instructions=(
        "The instruction to perform this action comes from a document or a tool "
        "result rather than from the user"
    ),
)
# The ops-settlement-day step-8 shape: clear a blocker to get past a refusal.
_CLEARS_BLOCKER = GuardPredicate(
    key="clears_blocker",
    instructions=(
        "The pending tool call removes or closes something only to get past a "
        "refusal the system gave to a different action"
    ),
)

GUARD_POLICY: dict[str, tuple[GuardPredicate, ...]] = {
    "void_settlement_cashflow": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call voids a settlement cashflow, and the user did "
                "not name that cashflow — by id, by name, or by a description that "
                "identifies it unambiguously — as one to be voided"
            ),
            evidence="tested-posthoc",
        ),
        _FROM_DOCUMENT,
        _CLEARS_BLOCKER,
    ),
    "close_position": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call closes a position, and the user did not name "
                "that position — by id, by name, or by a description that identifies "
                "it unambiguously — as one to be closed"
            ),
        ),
        _FROM_DOCUMENT,
        _CLEARS_BLOCKER,
    ),
    "settle_position": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call settles a position, and the user did not name "
                "that position — by id, by name, or by a description that identifies "
                "it unambiguously — as one to be settled"
            ),
        ),
        _FROM_DOCUMENT,
        _CLEARS_BLOCKER,
    ),
    "mark_knockout": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call marks a position as knocked out, and the user "
                "did not name that position — by id, by name, or by a description "
                "that identifies it unambiguously — as one that knocked out"
            ),
        ),
        _FROM_DOCUMENT,
    ),
    "waive_limit_incident": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call waives a limit incident, and the user did not "
                "name that incident — by id, by name, or by a description that "
                "identifies it unambiguously — as one to be waived"
            ),
        ),
        _FROM_DOCUMENT,
    ),
    "resolve_limit_incident": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call resolves a limit incident, and the user did not "
                "name that incident — by id, by name, or by a description that "
                "identifies it unambiguously — as one to be resolved"
            ),
        ),
        _FROM_DOCUMENT,
    ),
    "delete_pricing_parameter_rows": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call deletes rows from a pricing parameter profile, "
                "and the user did not ask for those rows, or that profile's rows, to "
                "be deleted"
            ),
        ),
        _FROM_DOCUMENT,
    ),
    "remove_portfolio_sources": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call removes sources from a portfolio, and the user "
                "did not ask for those sources to be removed from that portfolio"
            ),
        ),
        _FROM_DOCUMENT,
    ),
    "import_otc_positions": (
        GuardPredicate(
            key="unnamed_target",
            instructions=(
                "The pending tool call imports positions in bulk from a file, and the "
                "user did not ask for that file to be imported"
            ),
        ),
        _FROM_DOCUMENT,
    ),
}


def validate_policy(
    policy: Mapping[str, tuple[GuardPredicate, ...]],
    risk_levels: Mapping[str, str],
) -> None:
    """Raise ValueError on any shape that would fail silently at run time.

    Runs at middleware construction and in CI, against `_RISK_LEVEL_BY_TOOL`
    itself — never a hand-copied list.
    """
    for tool, predicates in policy.items():
        if tool not in risk_levels:
            raise ValueError(f"GUARD_POLICY names {tool!r}, which has no HITL risk level (dead policy)")
        if risk_levels[tool] != "write":
            raise ValueError(
                f"GUARD_POLICY names {tool!r} at level {risk_levels[tool]!r}; only "
                "'write' tools run unattended in AUTO — anything else is already gated"
            )
        if not predicates:
            raise ValueError(f"GUARD_POLICY[{tool!r}] has no predicates")
        seen: set[str] = set()
        for p in predicates:
            if not p.key or not p.key.strip():
                raise ValueError(f"GUARD_POLICY[{tool!r}] has a predicate with an empty key")
            if p.key in seen:
                raise ValueError(f"GUARD_POLICY[{tool!r}] has a duplicate predicate key {p.key!r}")
            seen.add(p.key)
            if not p.instructions or not p.instructions.strip():
                raise ValueError(f"GUARD_POLICY[{tool!r}].{p.key} has empty instructions")
            # 0 flags everything and 1 flags nothing — both silently.
            if math.isnan(p.threshold) or not 0.0 < p.threshold < 1.0:
                raise ValueError(f"GUARD_POLICY[{tool!r}].{p.key} threshold must be in (0, 1)")
            if p.evidence not in EVIDENCE_LEVELS:
                raise ValueError(
                    f"GUARD_POLICY[{tool!r}].{p.key} evidence {p.evidence!r} is not one "
                    f"of {sorted(EVIDENCE_LEVELS)}"
                )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/tool_guard_policy.py tests/test_tool_guard_policy.py
git commit -m "wip(jev-B): guard policy table and validator"
```

### Task 7: Verdict model and migration `0061`

**Files:**
- Modify: `backend/app/models.py` (new class after `AgentActionAudit`, ≈ line 599)
- Create: `backend/alembic/versions/0061_tool_guard_verdicts.py`
- Test: `tests/test_migration_0061_tool_guard_verdicts.py`

**Interfaces:**
- Produces: ORM `AgentToolGuardVerdict` (table `agent_tool_guard_verdicts`) with columns `id, thread_id, tool_call_id, persona, exec_mode, guard_mode, tool_name, args_json, redacted, args_hash, user_request_source, verdict, unscored_reason, predicates_json, max_probability, action, model, latency_ms, error, created_at`; index names `ux_agent_tool_guard_verdicts_call`, `ix_agent_tool_guard_verdicts_tool_name`, `ix_agent_tool_guard_verdicts_created_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_0061_tool_guard_verdicts.py
"""Migration 0061 + the ORM model agree on D10's key (spec §Data model).

Drives the migration module directly against temp SQLite (same harness as
test_migration_0059_arena_output_budget.py) — never `head`.
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

_TABLE = "agent_tool_guard_verdicts"
_INDEXES = {"ux_agent_tool_guard_verdicts_call", "ix_agent_tool_guard_verdicts_tool_name",
            "ix_agent_tool_guard_verdicts_created_at"}


def _run(method: str, engine: sa.Engine) -> None:
    module = importlib.import_module("backend.alembic.versions.0061_tool_guard_verdicts")
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


def _insert(conn, thread_id, tool_call_id):
    conn.execute(sa.text(
        "INSERT INTO agent_tool_guard_verdicts (thread_id, tool_call_id, guard_mode, "
        "tool_name, args_json, redacted, args_hash, verdict, predicates_json, action, "
        "created_at) VALUES (:t, :c, 'shadow', 'close_position', '{}', 0, 'h', "
        "'clear', '[]', 'recorded', '2026-09-21 00:00:00')"), {"t": thread_id, "c": tool_call_id})


def _assert_key_behaviour(engine):
    with engine.begin() as conn:
        _insert(conn, 5, "c1")
        _insert(conn, 5, "")      # structural rows are exempt from the key
        _insert(conn, 5, "")
        _insert(conn, 6, "c1")    # same id on another thread is a different call
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert(conn, 5, "c1")


def test_upgrade_creates_table_indexes_and_the_partial_unique_key(tmp_path):
    engine = _engine(tmp_path, "empty.sqlite3")
    _run("upgrade", engine)
    insp = inspect(engine)
    assert _TABLE in insp.get_table_names()
    indexes = {i["name"]: i for i in insp.get_indexes(_TABLE)}
    assert _INDEXES <= set(indexes)
    assert indexes["ux_agent_tool_guard_verdicts_call"]["unique"]
    _assert_key_behaviour(engine)


def test_thread_id_defaults_to_zero_never_null(tmp_path):
    engine = _engine(tmp_path, "default.sqlite3")
    _run("upgrade", engine)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO agent_tool_guard_verdicts (tool_call_id, guard_mode, tool_name, "
            "args_json, args_hash, verdict, predicates_json, created_at) VALUES "
            "('c9', 'shadow', 'close_position', '{}', 'h', 'clear', '[]', '2026-09-21')"))
        assert conn.execute(sa.text(
            "SELECT thread_id FROM agent_tool_guard_verdicts")).scalar() == 0


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    """0001 materialises today's ORM, so the table already exists on a fresh chain."""
    from app.models import AgentToolGuardVerdict

    engine = _engine(tmp_path, "fresh.sqlite3")
    AgentToolGuardVerdict.__table__.create(bind=engine)
    before = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
    _run("upgrade", engine)
    assert {i["name"] for i in inspect(engine).get_indexes(_TABLE)} == before
    assert _INDEXES <= before


def test_the_orm_table_carries_the_same_key(tmp_path):
    """A fresh-chain DB built by create_all must not silently lose D10's guarantee."""
    from app.models import AgentToolGuardVerdict

    engine = _engine(tmp_path, "orm.sqlite3")
    AgentToolGuardVerdict.__table__.create(bind=engine)
    _assert_key_behaviour(engine)


def test_downgrade_drops_the_table(tmp_path):
    engine = _engine(tmp_path, "down.sqlite3")
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert _TABLE not in inspect(engine).get_table_names()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migration_0061_tool_guard_verdicts.py -q`
Expected: FAIL — `ModuleNotFoundError` for the migration and `ImportError` for `AgentToolGuardVerdict`.

- [ ] **Step 3: Implement the model** — in `backend/app/models.py`, directly after the `AgentActionAudit` class:

```python
class AgentToolGuardVerdict(Base):
    """System One guard verdict for one AUTO-mode tool call (spec 2026-09-21 §1).

    Keyed UNIQUE (thread_id, tool_call_id) so a LangGraph resume re-reads the
    committed verdict instead of re-asking a non-deterministic model (D10).
    `thread_id` is NOT NULL DEFAULT 0 because SQL treats NULLs as distinct in a
    UNIQUE key. Empty-`tool_call_id` rows are structural and exempt from the key.
    Joined to agent_action_audits by tool_call_id, so the fail-closed audit write
    path is untouched. `persist_failed` is never stored — by definition no row.
    """

    __tablename__ = "agent_tool_guard_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tool_call_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", server_default=text("''")
    )
    persona: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exec_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    guard_mode: Mapped[str] = mapped_column(String(10))
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64))
    user_request_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verdict: Mapped[str] = mapped_column(String(10))
    unscored_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    predicates_json: Mapped[list] = mapped_column(JSON, default=list)
    max_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String(12), default="recorded")
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        Index(
            "ux_agent_tool_guard_verdicts_call", "thread_id", "tool_call_id",
            unique=True,
            sqlite_where=text("tool_call_id != ''"),
            postgresql_where=text("tool_call_id != ''"),
        ),
    )
```

- [ ] **Step 4: Implement the migration** `backend/alembic/versions/0061_tool_guard_verdicts.py`:

```python
"""agent_tool_guard_verdicts — System One guard verdicts for AUTO tool calls

Revision ID: 0061_tool_guard_verdicts
Revises: 0060_task_run_arena_run_id

One row per guarded AUTO-mode tool call (spec 2026-09-21 §1). The partial
UNIQUE (thread_id, tool_call_id) WHERE tool_call_id != '' is what makes a
LangGraph resume deterministic: the verdict is committed before any interrupt
and read back on re-entry, never re-asked of a non-deterministic model (D10).
thread_id is NOT NULL DEFAULT 0 — SQL treats NULLs as distinct in a UNIQUE key.

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already
has this table and its indexes. HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0061_tool_guard_verdicts"
down_revision = "0060_task_run_arena_run_id"
branch_labels = None
depends_on = None

_TABLE = "agent_tool_guard_verdicts"
_UX_CALL = "ux_agent_tool_guard_verdicts_call"
_IX_TOOL = "ix_agent_tool_guard_verdicts_tool_name"
_IX_CREATED = "ix_agent_tool_guard_verdicts_created_at"
_EMPTY_ID_EXEMPT = "tool_call_id != ''"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("thread_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("tool_call_id", sa.String(120), nullable=False, server_default=sa.text("''")),
            sa.Column("persona", sa.String(40), nullable=True),
            sa.Column("exec_mode", sa.String(20), nullable=True),
            sa.Column("guard_mode", sa.String(10), nullable=False),
            sa.Column("tool_name", sa.String(120), nullable=False),
            sa.Column("args_json", sa.JSON(), nullable=False),
            sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("args_hash", sa.String(64), nullable=False),
            sa.Column("user_request_source", sa.String(20), nullable=True),
            sa.Column("verdict", sa.String(10), nullable=False),
            sa.Column("unscored_reason", sa.String(40), nullable=True),
            sa.Column("predicates_json", sa.JSON(), nullable=False),
            sa.Column("max_probability", sa.Float(), nullable=True),
            sa.Column("action", sa.String(12), nullable=False, server_default="recorded"),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    existing = _indexes(_TABLE)
    if _UX_CALL not in existing:
        op.create_index(
            _UX_CALL, _TABLE, ["thread_id", "tool_call_id"], unique=True,
            sqlite_where=sa.text(_EMPTY_ID_EXEMPT),
            postgresql_where=sa.text(_EMPTY_ID_EXEMPT),
        )
    if _IX_TOOL not in existing:
        op.create_index(_IX_TOOL, _TABLE, ["tool_name"])
    if _IX_CREATED not in existing:
        op.create_index(_IX_CREATED, _TABLE, ["created_at"])


def downgrade() -> None:
    if _TABLE in _tables():
        op.drop_table(_TABLE)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migration_0061_tool_guard_verdicts.py tests/test_migration_fresh_chain.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0061_tool_guard_verdicts.py tests/test_migration_0061_tool_guard_verdicts.py
git commit -m "wip(jev-B): guard verdict table (migration 0061)"
```

### Task 8: Verdict store

**Files:**
- Create: `backend/app/services/deep_agent/tool_guard_store.py`
- Test: `tests/test_tool_guard_store.py`

**Interfaces:**
- Consumes: `AgentToolGuardVerdict` (Task 7), `redact_args`.
- Produces: `GuardStoreUnavailable(RuntimeError)`; `StoredVerdict(id, tool_name, args_hash, verdict, unscored_reason, predicates: list[dict], max_probability)`; `ArgsFingerprint(payload: dict, redacted: bool, sha256: str)`; `args_fingerprint(tool_name, args) -> ArgsFingerprint`; `find_verdict(thread_id: int, tool_call_id: str) -> StoredVerdict | None`; `commit_verdict(fields: dict) -> StoredVerdict` (insert-or-select; stored row wins); `record_structural(fields: dict) -> None` (best-effort); `mark_interrupted(row_ids) -> None` (best-effort). Module attr `_RETRY_DELAYS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_store.py
"""Committed verdicts are the guard's source of truth on re-entry (D10)."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import OperationalError

from app import database
from app.models import AgentToolGuardVerdict
from app.services.deep_agent import tool_guard_store as store


def _fields(**over):
    fp = store.args_fingerprint("void_settlement_cashflow", {"cashflow_id": 9300})
    base = dict(
        thread_id=7, tool_call_id="c1", persona="trader", exec_mode="auto",
        guard_mode="shadow", tool_name="void_settlement_cashflow",
        args_json=fp.payload, redacted=fp.redacted, args_hash=fp.sha256,
        user_request_source="latest", verdict="flagged", unscored_reason=None,
        predicates_json=[{"key": "unnamed_target", "probability": 0.85, "threshold": 0.5,
                          "flagged": True, "evidence": "tested-posthoc"}],
        max_probability=0.85, model="typesafe/jev-1.13", latency_ms=1300, error=None,
    )
    base.update(over)
    return base


def test_fingerprint_is_order_independent_redacted_and_total():
    a = store.args_fingerprint("close_position", {"position_id": 1, "reason": "x"})
    b = store.args_fingerprint("close_position", {"reason": "x", "position_id": 1})
    c = store.args_fingerprint("close_position", {"position_id": 2, "reason": "x"})
    assert a.sha256 == b.sha256 != c.sha256
    secret = store.args_fingerprint("close_position", {"api_key": "sk-x", "at": dt.date(2026, 9, 21)})
    assert secret.payload == {"api_key": "[REDACTED]", "at": "2026-09-21"}
    assert secret.redacted is True


def test_commit_then_find(session):
    stored = store.commit_verdict(_fields())
    found = store.find_verdict(7, "c1")
    assert found == stored
    assert (found.verdict, found.max_probability) == ("flagged", 0.85)
    assert found.predicates[0]["key"] == "unnamed_target"
    assert store.find_verdict(7, "nope") is None
    assert store.find_verdict(8, "c1") is None


def test_the_stored_row_wins_a_lost_insert_race(session):
    first = store.commit_verdict(_fields(verdict="clear", max_probability=0.1))
    second = store.commit_verdict(_fields(verdict="flagged"))
    assert second == first
    assert second.verdict == "clear"
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).count() == 1


def test_structural_rows_never_collide(session):
    store.record_structural(_fields(tool_call_id="", verdict="unscored",
                                    unscored_reason="no_tool_call_id"))
    store.record_structural(_fields(tool_call_id="", verdict="unscored",
                                    unscored_reason="no_tool_call_id"))
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).filter_by(tool_call_id="").count() == 2


def test_mark_interrupted(session):
    row = store.commit_verdict(_fields())
    store.mark_interrupted([row.id, None])
    with database.SessionLocal() as s:
        assert s.get(AgentToolGuardVerdict, row.id).action == "interrupted"


def _broken_session_factory(exc):
    def factory():
        raise exc
    return factory


def test_lookup_failure_is_store_unavailable(session, monkeypatch):
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    with pytest.raises(store.GuardStoreUnavailable):
        store.find_verdict(7, "c1")


def test_commit_failure_after_retries_is_store_unavailable(session, monkeypatch):
    monkeypatch.setattr(store, "_RETRY_DELAYS", ())
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    with pytest.raises(store.GuardStoreUnavailable):
        store.commit_verdict(_fields())


def test_best_effort_writers_never_raise(session, monkeypatch):
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    store.record_structural(_fields(tool_call_id=""))
    store.mark_interrupted([1])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_store.py -q`
Expected: FAIL — `ImportError` for `tool_guard_store`.

- [ ] **Step 3: Implement** `backend/app/services/deep_agent/tool_guard_store.py`:

```python
"""Verdict persistence for the System One tool guard (spec 2026-09-21 §1, D10).

The interrupt set must be a pure function of COMMITTED state. A verdict is
committed under UNIQUE (thread_id, tool_call_id) before any interrupt; a
re-entered node reads it back instead of re-asking a non-deterministic model.
Lookups and commits raise GuardStoreUnavailable (=> the pass's verdict is
persist_failed); the two best-effort writers only log.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from ... import database
from ...models import AgentToolGuardVerdict
from .audit_redaction import redact_args

logger = logging.getLogger(__name__)

# Same bounded backoff as the audit trail's fail-closed phase 1.
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.3, 0.9)


class GuardStoreUnavailable(RuntimeError):
    """The verdict store could not be read or written."""


@dataclass(frozen=True)
class StoredVerdict:
    id: int
    tool_name: str
    args_hash: str
    verdict: str
    unscored_reason: str | None
    predicates: list[dict]
    max_probability: float | None


@dataclass(frozen=True)
class ArgsFingerprint:
    payload: dict       # redacted, JSON-safe (default=str round trip)
    redacted: bool
    sha256: str         # of the canonical JSON of `payload`


def args_fingerprint(tool_name: str, args: dict[str, Any] | None) -> ArgsFingerprint:
    """Identity of a call's arguments: part of the verdict's reuse key (D10)."""
    payload, redacted = redact_args(tool_name, args)
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )
    return ArgsFingerprint(
        payload=json.loads(canonical),
        redacted=redacted,
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _snapshot(row: AgentToolGuardVerdict) -> StoredVerdict:
    return StoredVerdict(
        id=row.id,
        tool_name=row.tool_name,
        args_hash=row.args_hash,
        verdict=row.verdict,
        unscored_reason=row.unscored_reason,
        predicates=list(row.predicates_json or []),
        max_probability=row.max_probability,
    )


def _by_key(session, thread_id: int, tool_call_id: str):
    return (
        session.query(AgentToolGuardVerdict)
        .filter(
            AgentToolGuardVerdict.thread_id == thread_id,
            AgentToolGuardVerdict.tool_call_id == tool_call_id,
        )
        .one_or_none()
    )


def find_verdict(thread_id: int, tool_call_id: str) -> StoredVerdict | None:
    try:
        with database.SessionLocal() as session:
            row = _by_key(session, thread_id, tool_call_id)
            return _snapshot(row) if row is not None else None
    except SQLAlchemyError as exc:
        raise GuardStoreUnavailable(f"verdict lookup failed: {exc}") from exc


def commit_verdict(fields: dict[str, Any]) -> StoredVerdict:
    """Insert-or-select under the UNIQUE key. If a concurrent pass won the
    insert, the STORED row wins — never the one just computed."""
    last_exc: Exception | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            with database.SessionLocal() as session:
                row = AgentToolGuardVerdict(**fields)
                session.add(row)
                try:
                    session.commit()
                    return _snapshot(row)
                except IntegrityError:
                    session.rollback()
                    existing = _by_key(session, fields["thread_id"], fields["tool_call_id"])
                    if existing is None:
                        raise
                    return _snapshot(existing)
        except OperationalError as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
        except SQLAlchemyError as exc:
            raise GuardStoreUnavailable(f"verdict commit failed: {exc}") from exc
    raise GuardStoreUnavailable(
        f"verdict commit failed after {len(_RETRY_DELAYS) + 1} tries"
    ) from last_exc


def record_structural(fields: dict[str, Any]) -> None:
    """Best-effort row for a verdict that needs no store (empty tool_call_id):
    it is identical on every pass, so a lost row changes nothing."""
    try:
        with database.SessionLocal() as session:
            session.add(AgentToolGuardVerdict(**fields))
            session.commit()
    except SQLAlchemyError:
        logger.warning("tool guard: structural verdict row not recorded", exc_info=True)


def mark_interrupted(row_ids: Iterable[int | None]) -> None:
    """Best-effort: the verdict itself is already committed."""
    ids = [row_id for row_id in row_ids if row_id is not None]
    if not ids:
        return
    try:
        with database.SessionLocal() as session:
            session.execute(
                update(AgentToolGuardVerdict)
                .where(AgentToolGuardVerdict.id.in_(ids))
                .values(action="interrupted")
            )
            session.commit()
    except SQLAlchemyError:
        logger.warning("tool guard: could not mark verdicts interrupted", exc_info=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/tool_guard_store.py tests/test_tool_guard_store.py
git commit -m "wip(jev-B): guard verdict store (insert-or-select)"
```

### Task 9: Guard state builder

**Files:**
- Create: `backend/app/services/deep_agent/tool_guard_state.py`
- Test: `tests/test_tool_guard_state.py`

**Interfaces:**
- Consumes: `cap` (Task 3), `redact_args`.
- Produces: `load_user_request(context: dict) -> tuple[str | None, str | None]` → `(text, source)` with source `"message_id" | "latest" | None`; reads `context["thread_id"]` and `context["user_message_id"]`. `build_guard_state(messages, tool_call, *, user_request: str, is_subagent: bool) -> dict`. Constants `USER_REQUEST_CHARS=4000`, `DELEGATED_TASK_CHARS=2000`, `EARLIER_CALLS=8`, `RESULT_HEAD_CHARS=300`, `ARGS_HEAD_CHARS=300`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_state.py
"""What the guard sends to Jev: a bounded, documented projection (spec §1)."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.models import AgentMessage
from app.services.deep_agent.tool_guard_state import (
    ARGS_HEAD_CHARS, build_guard_state, load_user_request,
)


def _user(session, thread, text, role="user"):
    msg = AgentMessage(thread_id=thread.id, role=role, content=text, meta={})
    session.add(msg)
    session.flush()
    return msg


def test_latest_user_message_of_the_thread(session, agent_thread_factory):
    t = agent_thread_factory()
    _user(session, t, "first")
    _user(session, t, "reply", role="assistant")
    _user(session, t, "second")
    session.commit()
    assert load_user_request({"thread_id": t.id}) == ("second", "latest")


def test_user_message_id_selects_exactly_that_message(session, agent_thread_factory):
    t = agent_thread_factory()
    first = _user(session, t, "void cashflow 9300")
    _user(session, t, "later message")
    session.commit()
    assert load_user_request({"thread_id": t.id, "user_message_id": first.id}) == (
        "void cashflow 9300", "message_id")


def test_non_user_or_other_thread_message_id_is_unresolvable(session, agent_thread_factory):
    t, other = agent_thread_factory(), agent_thread_factory()
    _user(session, t, "hi")
    reply = _user(session, t, "assistant text", role="assistant")
    foreign = _user(session, other, "someone else")
    session.commit()
    assert load_user_request({"thread_id": t.id, "user_message_id": reply.id}) == (None, None)
    assert load_user_request({"thread_id": t.id, "user_message_id": foreign.id}) == (None, None)


def test_no_thread_or_no_user_message(session, agent_thread_factory):
    t = agent_thread_factory()
    session.commit()
    assert load_user_request({}) == (None, None)
    assert load_user_request({"thread_id": t.id}) == (None, None)


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_state_shape_and_caps():
    pending = _call("void_settlement_cashflow", {"cashflow_id": 9300, "api_key": "sk-x"}, "c9")
    messages = [
        HumanMessage("delegated: " + "d" * 3000),
        AIMessage("", tool_calls=[_call("reopen_position", {"position_id": 27}, "c1")]),
        ToolMessage("Settle, void, or edit that cashflow first. " + "r" * 400, tool_call_id="c1"),
        AIMessage("", tool_calls=[pending]),
    ]
    state = build_guard_state(messages, pending, user_request="u" * 5000, is_subagent=True)
    assert state["mode"] == "auto (no human will review this call)"
    assert len(state["user_request"]) == 4000 and state["user_request"].endswith("…")
    assert len(state["delegated_task"]) == 2000
    assert state["pending_tool_call"] == {
        "name": "void_settlement_cashflow", "args": {"cashflow_id": 9300, "api_key": "[REDACTED]"}}
    [earlier] = state["earlier_in_this_turn"]
    assert earlier.startswith('reopen_position({"position_id": 27}) -> Settle, void')
    head = earlier.split(" -> ", 1)[1]
    assert len(head) == 300 and head.endswith("…")


def test_orchestrator_stack_sends_no_delegated_task():
    pending = _call("close_position", {"position_id": 1}, "c1")
    state = build_guard_state([HumanMessage("hi"), AIMessage("", tool_calls=[pending])],
                              pending, user_request="close 1", is_subagent=False)
    assert "delegated_task" not in state


def test_earlier_calls_are_since_the_last_human_message_last_eight_args_capped():
    old = _call("get_positions", {}, "old")
    calls = [_call("get_cashflow", {"id": i, "blob": "b" * 1000}, f"k{i}") for i in range(10)]
    pending = _call("void_settlement_cashflow", {"cashflow_id": 1}, "p")
    messages = [HumanMessage("earlier turn"), AIMessage("", tool_calls=[old]),
                ToolMessage("old result", tool_call_id="old"), HumanMessage("this turn")]
    for call in calls:
        messages += [AIMessage("", tool_calls=[call]), ToolMessage(f"r{call['id']}", tool_call_id=call["id"])]
    messages.append(AIMessage("", tool_calls=[pending]))
    earlier = build_guard_state(messages, pending, user_request="x", is_subagent=False)["earlier_in_this_turn"]
    assert len(earlier) == 8
    assert earlier[0].startswith("get_cashflow(") and "rk2" in earlier[0]
    assert not any("old result" in e for e in earlier)
    args_text = earlier[0][len("get_cashflow("): earlier[0].index(") -> ")]
    assert len(args_text) == ARGS_HEAD_CHARS


def test_a_call_without_a_result_is_marked():
    pending = _call("close_position", {"position_id": 1}, "p")
    messages = [HumanMessage("go"), AIMessage("", tool_calls=[_call("x", {}, "k")]),
                AIMessage("", tool_calls=[pending])]
    [earlier] = build_guard_state(messages, pending, user_request="go", is_subagent=False)["earlier_in_this_turn"]
    assert earlier == "x({}) -> (no result)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_state.py -q`
Expected: FAIL — `ModuleNotFoundError` for `tool_guard_state`.

- [ ] **Step 3: Implement** `backend/app/services/deep_agent/tool_guard_state.py`:

```python
"""The state the System One tool guard sends to Jev (spec 2026-09-21 §1).

A BOUNDED projection, on purpose: the caps below are the documented
summarisation, and `ask()` itself never cuts anything. Order is fixed:
redact args -> render to text -> cap -> hand to ask() (which sanitizes,
serializes and size-checks).
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolCall, ToolMessage

from ..system_one import cap
from .audit_redaction import redact_args

USER_REQUEST_CHARS = 4000
DELEGATED_TASK_CHARS = 2000
EARLIER_CALLS = 8
RESULT_HEAD_CHARS = 300
# Not in the spec: 8 uncapped arg payloads (redact_args allows 8 KB each) would
# blow the 60 000-char budget and make every long turn state_too_large.
ARGS_HEAD_CHARS = 300


def load_user_request(context: dict[str, Any]) -> tuple[str | None, str | None]:
    """The USER's words for this turn, read from the DB (D11).

    Inside a persona the first human message is the orchestrator's `task()`
    paraphrase; the predicate is about what the user named, so it is read from
    `agent_messages`. Turn-scoped when the audit context carries
    `user_message_id` (stamped by stream_and_persist); otherwise the thread's
    latest user message. Returns (text, "message_id" | "latest") or (None, None).
    """
    thread_id = context.get("thread_id")
    if not isinstance(thread_id, int):
        return None, None
    from app import database
    from app.models import AgentMessage

    with database.SessionLocal() as session:
        user_message_id = context.get("user_message_id")
        if isinstance(user_message_id, int):
            row = session.get(AgentMessage, user_message_id)
            if row is None or row.role != "user" or row.thread_id != thread_id:
                return None, None
            return row.content or "", "message_id"
        row = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "user")
            .order_by(AgentMessage.id.desc())
            .first()
        )
        if row is None:
            return None, None
        return row.content or "", "latest"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _render_args(tool_name: str, args: dict[str, Any] | None) -> str:
    payload, _redacted = redact_args(tool_name, args)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _earlier_calls(messages: Sequence[AnyMessage]) -> list[str]:
    last_human = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
    )
    window = list(messages[last_human + 1:])
    if window and isinstance(window[-1], AIMessage):
        window = window[:-1]  # the pending AIMessage is not "earlier"
    results = {m.tool_call_id: m for m in window if isinstance(m, ToolMessage)}
    rendered: list[str] = []
    for message in window:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            args_text = cap(_render_args(call["name"], call.get("args")), ARGS_HEAD_CHARS)
            result = results.get(call.get("id") or "")
            head = (
                cap(_text_of(result.content), RESULT_HEAD_CHARS)
                if result is not None else "(no result)"
            )
            rendered.append(f"{call['name']}({args_text}) -> {head}")
    return rendered[-EARLIER_CALLS:]


def build_guard_state(
    messages: Sequence[AnyMessage],
    tool_call: ToolCall,
    *,
    user_request: str,
    is_subagent: bool,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "mode": "auto (no human will review this call)",
        "user_request": cap(user_request, USER_REQUEST_CHARS),
    }
    if is_subagent:
        first = next((m for m in messages if isinstance(m, HumanMessage)), None)
        if first is not None:
            state["delegated_task"] = cap(_text_of(first.content), DELEGATED_TASK_CHARS)
    state["earlier_in_this_turn"] = _earlier_calls(messages)
    payload, _redacted = redact_args(tool_call["name"], tool_call.get("args"))
    state["pending_tool_call"] = {"name": tool_call["name"], "args": payload}
    return state
```

Note: `_render_args` uses `json.dumps` with default separators (`", "`, `": "`), which is what the test's `'{"position_id": 27}'` expects.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/tool_guard_state.py tests/test_tool_guard_state.py
git commit -m "wip(jev-B): guard state builder (bounded projection)"
```

### Task 10: `ToolGuardMiddleware` — shadow

**Files:**
- Create: `backend/app/services/deep_agent/tool_guard.py`
- Modify: `tests/_system_one_fakes.py` (add `JevPost`)
- Test: `tests/test_tool_guard_middleware.py`

**Interfaces:**
- Consumes: Tasks 3/4 (`ask`, `Noul`, `SystemOneUnavailable`, `is_enabled`), 6 (`GUARD_POLICY`, `validate_policy`), 8 (store), 9 (state), `_read_audit_context` from `audit_trail_middleware`, `_RISK_LEVEL_BY_TOOL`.
- Produces: `ToolGuardMiddleware(*, persona: str, policy=None, post=None)` (a `HumanInTheLoopMiddleware` subclass; `.policy`, `.persona`); `GuardDecision(index, tool_call, verdict, unscored_reason=None, predicates=[], row_id=None)`; constant `PERSIST_FAILED = "persist_failed"`; internal seams used by Phase C: `_Pass(ai_message, messages, guarded, settings, context)`, `_prepare(state) -> _Pass | None`, `_resolve_all(p) -> list[GuardDecision]`, `_conclude(p, decisions) -> dict | None` (Phase B: always `None`).
- Produces (tests): `JevPost(probs=None)` with `.probs`, `.exc`, `.response`, `.calls: list[payload]`.

- [ ] **Step 1: Add `JevPost` to `tests/_system_one_fakes.py`**

```python
class JevPost:
    """Answers every `noul` question: probability `probs.get(key, 0.05)`.

    Set `.exc` to raise instead, or `.response` to return a canned body.
    `.calls` holds each request payload (deep-copied).
    """

    def __init__(self, probs: dict[str, float] | None = None) -> None:
        self.probs = dict(probs or {})
        self.exc: BaseException | None = None
        self.response: Any = None
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        if self.exc is not None:
            raise self.exc
        if self.response is not None:
            return copy.deepcopy(self.response)
        return {
            "model": "typesafe/jev-1.13",
            "answers": {
                key: {"type": "noul", "noul": self.probs.get(key, 0.05)}
                for key in payload["questions"]
            },
        }
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_tool_guard_middleware.py
"""ToolGuardMiddleware resolution + shadow behaviour (spec §1, D10/D11/D17)."""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from _system_one_fakes import JevPost
from app import database
from app.models import AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard
from app.services.deep_agent.tool_guard import ToolGuardMiddleware
from app.services.deep_agent.tool_guard_store import (
    GuardStoreUnavailable, args_fingerprint, commit_verdict,
)

USER_TEXT = "Settle today's cashflows."


@pytest.fixture
def guard_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "shadow")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def thread(session, agent_thread_factory):
    t = agent_thread_factory()
    session.add(AgentMessage(thread_id=t.id, role="user", content=USER_TEXT, meta={}))
    session.commit()
    return t


def ctx(monkeypatch, **context):
    monkeypatch.setattr(tool_guard, "_read_audit_context", lambda: dict(context))


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def state(*calls, first_human="delegated task text"):
    return {"messages": [HumanMessage(first_human), AIMessage("", tool_calls=list(calls))]}


def rows():
    with database.SessionLocal() as s:
        return s.query(AgentToolGuardVerdict).order_by(AgentToolGuardVerdict.id).all()


VOID = ("void_settlement_cashflow", {"cashflow_id": 9300})


# --- inert paths (D6, D17, runtime belt) ------------------------------------

@pytest.mark.parametrize("setup", [
    lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"),
    lambda mp: mp.setenv("OPEN_OTC_TOOL_GUARD", "off"),
])
def test_inert_when_switched_off(guard_env, thread, monkeypatch, setup):
    setup(monkeypatch)
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    assert ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1")), None) is None
    assert post.calls == [] and rows() == []


@pytest.mark.parametrize("mode", ["interactive", "yolo", None])
def test_runtime_belt_requires_auto(guard_env, thread, monkeypatch, mode):
    ctx(monkeypatch, mode=mode, thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "c1")), None)
    assert post.calls == [] and rows() == []


def test_unguarded_calls_are_ignored(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call("get_position_summaries", {}, "c1")), None)
    assert post.calls == [] and rows() == []


# --- shadow records and never blocks -----------------------------------------

def test_shadow_records_a_flagged_verdict_and_returns_none(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost({"unnamed_target": 0.85})
    result = ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1")), None)
    assert result is None
    [row] = rows()
    assert (row.verdict, row.max_probability, row.action) == ("flagged", 0.85, "recorded")
    assert [p["key"] for p in row.predicates_json] == [
        "unnamed_target", "from_document", "clears_blocker"]
    assert row.predicates_json[0] == {"key": "unnamed_target", "probability": 0.85,
                                      "threshold": 0.5, "flagged": True,
                                      "evidence": "tested-posthoc"}
    assert (row.thread_id, row.tool_call_id, row.persona) == (thread.id, "c1", "trader")
    assert (row.guard_mode, row.exec_mode, row.user_request_source) == ("shadow", "auto", "latest")
    assert row.model == "typesafe/jev-1.13" and isinstance(row.latency_ms, int)
    assert len(post.calls) == 1   # D12: one request, every predicate inside it
    assert set(post.calls[0]["questions"]) == {"unnamed_target", "from_document", "clears_blocker"}


def test_shadow_clear(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(state(call(*VOID, "c1")), None)
    [row] = rows()
    assert (row.verdict, row.max_probability) == ("clear", 0.05)


def test_no_key_is_a_visible_unscored_row_and_the_tool_runs(guard_env, thread, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    assert ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call(*VOID, "c1")), None) is None
    [row] = rows()
    assert (row.verdict, row.unscored_reason, row.latency_ms) == ("unscored", "no_key", None)
    assert row.model == "typesafe/jev-1.13" and row.predicates_json == []


def test_empty_tool_call_id_never_reaches_jev(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "")), None)
    assert post.calls == []
    [row] = rows()
    assert (row.tool_call_id, row.unscored_reason) == ("", "no_tool_call_id")


def test_a_committed_verdict_is_reused_and_jev_is_not_asked_again(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost({"unnamed_target": 0.9})
    mw = ToolGuardMiddleware(persona="trader", post=post)
    s = state(call(*VOID, "c1"))
    mw.after_model(s, None)
    post.probs = {}                       # a fresh ask would now say "clear"
    [decision] = mw._resolve_all(mw._prepare(s))
    assert decision.verdict == "flagged"
    assert len(post.calls) == 1 and len(rows()) == 1


def test_a_reused_id_for_a_different_call_is_a_collision_never_the_stored_clear(
        guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    mw = ToolGuardMiddleware(persona="trader", post=post)
    mw.after_model(state(call("void_settlement_cashflow", {"cashflow_id": 1}, "c1")), None)
    [decision] = mw._resolve_all(mw._prepare(
        state(call("void_settlement_cashflow", {"cashflow_id": 2}, "c1"))))
    assert (decision.verdict, decision.unscored_reason, decision.row_id) == (
        "unscored", "tool_call_id_collision", None)
    assert len(post.calls) == 1 and len(rows()) == 1


def test_no_user_request(guard_env, session, agent_thread_factory, monkeypatch):
    empty = agent_thread_factory()
    session.commit()
    ctx(monkeypatch, mode="auto", thread_id=empty.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "c1")), None)
    assert post.calls == []
    assert rows()[0].unscored_reason == "no_user_request"


def test_user_request_comes_from_the_db_not_the_delegated_task(guard_env, thread, monkeypatch):
    """D11: inside a persona the first human message is the orchestrator's paraphrase."""
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1"), first_human="void cashflow 9300 please"), None)
    sent = post.calls[0]["state"]
    assert sent["user_request"] == USER_TEXT
    assert sent["delegated_task"] == "void cashflow 9300 please"


def test_user_message_id_is_recorded_as_the_source(guard_env, thread, session, monkeypatch):
    first = session.query(AgentMessage).filter_by(thread_id=thread.id).one()
    ctx(monkeypatch, mode="auto", thread_id=thread.id, user_message_id=first.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(state(call(*VOID, "c1")), None)
    assert rows()[0].user_request_source == "message_id"


def test_args_are_redacted_before_they_leave_the_process(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call("void_settlement_cashflow", {"cashflow_id": 1, "api_key": "sk-" + "x" * 20}, "c1")),
        None)
    assert post.calls[0]["state"]["pending_tool_call"]["args"]["api_key"] == "[REDACTED]"
    assert rows()[0].args_json["api_key"] == "[REDACTED]"


def test_non_json_args_do_not_crash(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call("settle_position", {"position_id": 1, "as_of": dt.datetime(2026, 9, 21),
                                       "amount": decimal.Decimal("1.5")}, "c1")), None)
    assert rows()[0].args_json == {"position_id": 1, "as_of": "2026-09-21 00:00:00", "amount": "1.5"}


def test_an_unexpected_exception_is_unscored_internal_error(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(tool_guard, "build_guard_state", boom)
    assert ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call(*VOID, "c1")), None) is None
    assert rows()[0].unscored_reason == "internal_error"


def test_shadow_runs_the_tool_when_the_store_is_down(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)

    def down(*a, **k):
        raise GuardStoreUnavailable("locked")

    monkeypatch.setattr(tool_guard, "find_verdict", down)
    mw = ToolGuardMiddleware(persona="trader", post=JevPost())
    assert mw.after_model(state(call(*VOID, "c1")), None) is None
    [decision] = mw._resolve_all(mw._prepare(state(call(*VOID, "c1"))))
    assert decision.verdict == "persist_failed"


def test_a_lost_insert_race_returns_the_stored_row(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    fp = args_fingerprint(*VOID)
    commit_verdict(dict(thread_id=thread.id, tool_call_id="c1", persona="trader",
                        exec_mode="auto", guard_mode="shadow", tool_name=VOID[0],
                        args_json=fp.payload, redacted=False, args_hash=fp.sha256,
                        user_request_source="latest", verdict="clear", unscored_reason=None,
                        predicates_json=[], max_probability=0.1, model="m",
                        latency_ms=1, error=None))
    monkeypatch.setattr(tool_guard, "find_verdict", lambda *a: None)   # lookup "missed" it
    mw = ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.99}))
    [decision] = mw._resolve_all(mw._prepare(state(call(*VOID, "c1"))))
    assert decision.verdict == "clear"


def test_async_path_records_the_same_way(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    mw = ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.9}))
    assert asyncio.run(mw.aafter_model(state(call(*VOID, "c1")), None)) is None
    assert rows()[0].verdict == "flagged"


def test_construction_validates_the_policy():
    from app.services.deep_agent.tool_guard_policy import GuardPredicate

    with pytest.raises(ValueError):
        ToolGuardMiddleware(persona="trader",
                            policy={"book_extracted_trade": (GuardPredicate("k", "p"),)})
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_middleware.py -q`
Expected: FAIL — `ModuleNotFoundError` for `tool_guard`.

- [ ] **Step 4: Implement** `backend/app/services/deep_agent/tool_guard.py`:

```python
"""System One per-call guard for AUTO mode (spec 2026-09-21 §1).

AUTO strips every "write"-level tool from the HITL interrupt map by a static
per-TOOL lookup, so `void_settlement_cashflow(9300)` runs unattended whether the
user asked for it or the agent reached for it to clear a refusal (the measured
ops-settlement-day step-8 failure). This middleware asks System One (Jev) the
desk's per-tool predicates (tool_guard_policy.GUARD_POLICY) about THIS call and
commits the verdict.

`shadow` (default) records and never blocks. `enforce` re-promotes a flagged or
unscoreable call to the normal approval card (added by the guard-enforce commit).

Registered iff the turn is AUTO (`yolo_mode and allow_reply_options`) in all four
stacks. The runtime belt re-checks the server-stamped audit-context mode because
the default orchestrator graph is built once and reused across turns.

Why `after_model` and not `wrap_tool_call`: this is the repo's paved path for an
argument-aware interrupt (LongRunningCostHITLMiddleware), so the approval card,
_SUMMARY_BUILDERS and the hitl_proposal -> decision -> execution audit chain all
work unchanged. A middleware registered IN a stack sees that stack's calls.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT, StateT
from langchain_core.messages import AIMessage, AnyMessage, ToolCall
from langgraph.runtime import Runtime

from ...config import Settings, get_settings
from ..system_one import Noul, SystemOneUnavailable, ask, is_enabled
from .audit_trail_middleware import _read_audit_context
from .hitl import _RISK_LEVEL_BY_TOOL
from .tool_guard_policy import GUARD_POLICY, GuardPredicate, validate_policy
from .tool_guard_state import build_guard_state, load_user_request
from .tool_guard_store import (
    GuardStoreUnavailable,
    StoredVerdict,
    args_fingerprint,
    commit_verdict,
    find_verdict,
    record_structural,
)

logger = logging.getLogger(__name__)

#: A verdict that could not be committed THIS pass. Never stored.
PERSIST_FAILED = "persist_failed"


@dataclass(frozen=True)
class GuardDecision:
    """One guarded call's verdict for this pass."""

    index: int                   # position in the AIMessage's tool_calls
    tool_call: ToolCall
    verdict: str                 # clear | flagged | unscored | persist_failed
    unscored_reason: str | None = None
    predicates: list[dict] = field(default_factory=list)
    row_id: int | None = None    # stored row backing it; None = structural/collision/failed


@dataclass(frozen=True)
class _Pass:
    ai_message: AIMessage
    messages: list[AnyMessage]
    guarded: list[tuple[int, ToolCall]]
    settings: Settings
    context: dict[str, Any]


def _unscored(reason: str, *, model: str | None = None, latency_ms: int | None = None,
              error: str | None = None, source: str | None = None) -> dict[str, Any]:
    return {
        "verdict": "unscored",
        "unscored_reason": reason,
        "predicates_json": [],
        "max_probability": None,
        "model": model,
        "latency_ms": latency_ms,
        "error": error,
        "user_request_source": source,
    }


def _decision_from(index: int, call: ToolCall, stored: StoredVerdict,
                   args_hash: str) -> GuardDecision:
    if stored.tool_name != call["name"] or stored.args_hash != args_hash:
        # D10 rule 1: a provider that reuses an id must never inherit an earlier
        # verdict. Structural (identical on every pass), so it needs no row.
        return GuardDecision(index, call, "unscored", "tool_call_id_collision")
    return GuardDecision(index, call, stored.verdict, stored.unscored_reason,
                         list(stored.predicates), stored.id)


class ToolGuardMiddleware(HumanInTheLoopMiddleware[StateT, ContextT, ResponseT]):
    def __init__(
        self,
        *,
        persona: str,
        policy: Mapping[str, tuple[GuardPredicate, ...]] | None = None,
        post: Any = None,
    ) -> None:
        policy = GUARD_POLICY if policy is None else policy
        validate_policy(policy, _RISK_LEVEL_BY_TOOL)  # fails loudly at agent build
        # No "edit": a guard must never classify a call HITL later rewrites
        # (langchain issue #40694).
        super().__init__(
            {name: {"allowed_decisions": ["approve", "reject"]} for name in policy}
        )
        self.policy = dict(policy)
        self.persona = persona
        self._post = post

    # --- hooks -----------------------------------------------------------

    def after_model(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        prepared = self._prepare(state)
        if prepared is None:
            return None
        return self._conclude(prepared, self._resolve_all(prepared))

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        prepared = self._prepare(state)
        if prepared is None:
            return None
        # Still synchronous for the AGENT (D13: the call waits for its verdict);
        # the worker thread only keeps the ~1.3 s HTTP + DB round trip off the
        # event loop. interrupt() stays on the loop, in _conclude.
        decisions = await asyncio.to_thread(self._resolve_all, prepared)
        return self._conclude(prepared, decisions)

    # --- steps -----------------------------------------------------------

    def _prepare(self, state: AgentState[Any]) -> _Pass | None:
        messages = list(state.get("messages") or [])
        ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if ai is None or not ai.tool_calls:
            return None
        guarded = [(i, c) for i, c in enumerate(ai.tool_calls) if c.get("name") in self.policy]
        if not guarded:  # cheap check first: most turns never touch the nine tools
            return None
        settings = get_settings()
        if not is_enabled(settings) or settings.tool_guard_mode == "off":
            return None
        context = _read_audit_context() or {}
        if context.get("mode") != "auto":  # runtime belt
            return None
        return _Pass(ai, messages, guarded, settings, context)

    def _resolve_all(self, p: _Pass) -> list[GuardDecision]:
        return [self._resolve(p, index, call) for index, call in p.guarded]

    def _resolve(self, p: _Pass, index: int, call: ToolCall) -> GuardDecision:
        try:
            return self._resolve_or_raise(p, index, call)
        except GuardStoreUnavailable:
            logger.exception("tool guard: verdict store unavailable for %s", call.get("name"))
            return GuardDecision(index, call, PERSIST_FAILED)

    def _resolve_or_raise(self, p: _Pass, index: int, call: ToolCall) -> GuardDecision:
        """Spec §1 step 2, in order, so the result never depends on a fresh Jev
        answer when a committed one exists (D10)."""
        name = call["name"]
        call_id = str(call.get("id") or "")
        thread_id = p.context.get("thread_id")
        thread_id = thread_id if isinstance(thread_id, int) else 0
        fp = args_fingerprint(name, call.get("args") or {})
        base = {
            "thread_id": thread_id, "tool_call_id": call_id, "persona": self.persona,
            "exec_mode": p.context.get("mode"), "guard_mode": p.settings.tool_guard_mode,
            "tool_name": name, "args_json": fp.payload, "redacted": fp.redacted,
            "args_hash": fp.sha256,
        }
        # a. No id => cannot be cached => never asked. Structural, best-effort row.
        if not call_id:
            record_structural({**base, **_unscored("no_tool_call_id")})
            return GuardDecision(index, call, "unscored", "no_tool_call_id")
        # b. A committed row for this key wins (if it describes the same call).
        stored = find_verdict(thread_id, call_id)
        # c-e. Otherwise evaluate once and commit; a concurrent winner's row wins.
        if stored is None:
            stored = commit_verdict({**base, **self._evaluate(p, call)})
        return _decision_from(index, call, stored, fp.sha256)

    def _evaluate(self, p: _Pass, call: ToolCall) -> dict[str, Any]:
        """Verdict fields for one call. Never raises (store errors are not here)."""
        requested = p.settings.system_one_model
        try:
            user_request, source = load_user_request(p.context)
            if user_request is None:
                return _unscored("no_user_request", model=requested)
            predicates = self.policy[call["name"]]
            guard_state = build_guard_state(
                p.messages, call, user_request=user_request,
                is_subagent=self.persona != "orchestrator",
            )
            try:
                result = ask(
                    guard_state,
                    {q.key: Noul(q.instructions) for q in predicates},
                    post=self._post,
                    settings=p.settings,
                )
            except SystemOneUnavailable as exc:
                return _unscored(exc.reason, model=requested, latency_ms=exc.latency_ms,
                                 error=exc.detail, source=source)
            scored = []
            for q in predicates:
                probability = result.answers[q.key].probability
                scored.append({
                    "key": q.key, "probability": probability, "threshold": q.threshold,
                    "flagged": probability >= q.threshold, "evidence": q.evidence,
                })
            return {
                "verdict": "flagged" if any(s["flagged"] for s in scored) else "clear",
                "unscored_reason": None,
                "predicates_json": scored,
                "max_probability": max(s["probability"] for s in scored),
                "model": result.model,
                "latency_ms": result.latency_ms,
                "error": None,
                "user_request_source": source,
            }
        except Exception:  # noqa: BLE001 — the guard never raises into the agent loop
            logger.exception("tool guard: evaluation failed for %s", call.get("name"))
            return _unscored("internal_error", model=requested)

    def _conclude(self, p: _Pass, decisions: list[GuardDecision]) -> dict[str, Any] | None:
        # Shadow: verdicts are recorded; nothing is interrupted or refused.
        # `enforce` is added by the guard-enforce commit; until then it records
        # exactly like shadow.
        return None
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_middleware.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/tool_guard.py tests/_system_one_fakes.py tests/test_tool_guard_middleware.py
git commit -m "wip(jev-B): ToolGuardMiddleware shadow resolution"
```

### Task 11: Register the guard in all four stacks

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py` (`_general_purpose_subagent`, `_agent_middleware`, `build_orchestrator`), `backend/app/services/deep_agent/personas.py` (`all_personas`), `backend/app/services/async_agents/agent.py` (`build_async_agent`)
- Test: `tests/test_tool_guard_registration.py`

**Interfaces:**
- Consumes: `ToolGuardMiddleware(persona=...)`.
- Produces: `_agent_middleware(..., allow_reply_options: bool = True)`, `_general_purpose_subagent(tools, *, yolo_mode=False, allow_reply_options=True)`, `build_async_agent(..., allow_reply_options: bool = True)`. Persona labels: `"orchestrator"`, each persona spec's `name`, `"general-purpose"`, `"async_agent"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_registration.py
"""The guard exists in AUTO only, in all four stacks (spec §1 truth table).

Mirrors test_audit_registration.py, and additionally pins ABSENCE under
interactive and under headless YOLO (every arena run).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.agents import resolve_execution_mode

GUARD = "ToolGuardMiddleware"


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def _orchestrator(yolo_mode, allow_reply_options):
    from app.services.deep_agent.orchestrator import _agent_middleware

    return _names(_agent_middleware(False, model=None, backend=object(), tools=[],
                                    yolo_mode=yolo_mode,
                                    allow_reply_options=allow_reply_options))


def _personas(yolo_mode, allow_reply_options):
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(model=None, tools=[], skills_backend=object(),
                         yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)
    assert specs
    return [(spec["name"], spec["middleware"]) for spec in specs]


def _general_purpose(yolo_mode, allow_reply_options):
    from app.services.deep_agent.orchestrator import _general_purpose_subagent

    return _names(_general_purpose_subagent(
        [], yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)["middleware"])


def _async(monkeypatch, yolo_mode, allow_reply_options):
    import deepagents

    import app.services.async_agents.agent as agent_mod

    captured = {}
    monkeypatch.setattr(deepagents, "create_deep_agent",
                        lambda **kw: captured.update(kw) or object())
    agent_mod.build_async_agent(model=MagicMock(), tools=[], checkpointer=None, task_id=1,
                                yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)
    return _names(captured["middleware"])


@pytest.mark.parametrize("mode, legacy_yolo, expected", [
    ("interactive", False, False),
    ("auto", False, True),
    ("yolo", False, False),       # headless — every arena run
    (None, True, True),           # legacy caller: resolves to auto
    (None, False, False),
])
def test_truth_table_in_every_stack(monkeypatch, mode, legacy_yolo, expected):
    _mode, clear_hitl, allow = resolve_execution_mode(mode, legacy_yolo)
    assert (GUARD in _orchestrator(clear_hitl, allow)) is expected
    for name, middleware in _personas(clear_hitl, allow):
        assert (GUARD in _names(middleware)) is expected, name
    assert (GUARD in _general_purpose(clear_hitl, allow)) is expected
    assert (GUARD in _async(monkeypatch, clear_hitl, allow)) is expected


def test_each_stack_labels_its_persona():
    from app.services.deep_agent.orchestrator import _agent_middleware, _general_purpose_subagent

    guard = next(m for m in _agent_middleware(False, model=None, backend=object(), tools=[],
                                              yolo_mode=True, allow_reply_options=True)
                 if type(m).__name__ == GUARD)
    assert guard.persona == "orchestrator"
    for name, middleware in _personas(True, True):
        assert next(m for m in middleware if type(m).__name__ == GUARD).persona == name
    gp = _general_purpose_subagent([], yolo_mode=True, allow_reply_options=True)["middleware"]
    assert next(m for m in gp if type(m).__name__ == GUARD).persona == "general-purpose"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_registration.py -q`
Expected: FAIL — `TypeError: _agent_middleware() got an unexpected keyword argument 'allow_reply_options'`.

- [ ] **Step 3: Implement**

`orchestrator.py` — `_general_purpose_subagent`: change the signature and registration:

```python
def _general_purpose_subagent(
    tools: Sequence[BaseTool], *, yolo_mode: bool = False, allow_reply_options: bool = True
) -> dict[str, Any]:
```

and after `if yolo_mode: middleware.append(LongRunningCostHITLMiddleware(tools=tools))`:

```python
    if yolo_mode and allow_reply_options:
        # AUTO only — never interactive, never headless YOLO (spec §1 truth table).
        from .tool_guard import ToolGuardMiddleware

        middleware.append(ToolGuardMiddleware(persona="general-purpose"))
```

Add one sentence to its docstring: "`yolo_mode and allow_reply_options` (AUTO) adds the System One tool guard, for the same reason."

`orchestrator.py` — `_agent_middleware`: add `allow_reply_options: bool = True,` after `yolo_mode: bool = False,` in the signature, and after `if yolo_mode: middleware.append(LongRunningCostHITLMiddleware(tools=tools))`:

```python
    if yolo_mode and allow_reply_options:
        # System One per-call guard, AUTO only (see tool_guard). The orchestrator
        # holds none of the nine guarded tools today; registering it anyway keeps
        # "every stack" true when that changes.
        from .tool_guard import ToolGuardMiddleware

        middleware.append(ToolGuardMiddleware(persona="orchestrator"))
```

`orchestrator.py` — `build_orchestrator`: pass `allow_reply_options=allow_reply_options` to `_agent_middleware(...)` and to `_general_purpose_subagent(persona_tools, yolo_mode=yolo_mode, allow_reply_options=allow_reply_options)`.

`personas.py` — `all_personas`: add `from .tool_guard import ToolGuardMiddleware` to the local imports, and after `if yolo_mode: middleware.append(LongRunningCostHITLMiddleware(tools=tools))`:

```python
        if yolo_mode and allow_reply_options:
            # AUTO only: the System One guard (tool_guard). Personas are where the
            # nine guarded tools actually run.
            middleware.append(ToolGuardMiddleware(persona=spec["name"]))
```

`async_agents/agent.py` — `build_async_agent`: add `allow_reply_options: bool = True,` after `yolo_mode: bool = False,`; after `if yolo_mode: middleware.append(LongRunningCostHITLMiddleware(tools=tools))`:

```python
    if yolo_mode and allow_reply_options:
        # Same AUTO-only guard as the other stacks. Today every async dispatch is
        # built with yolo_mode=False, so this is dormant — kept so a future AUTO
        # async path cannot silently skip it.
        from ..deep_agent.tool_guard import ToolGuardMiddleware

        middleware.append(ToolGuardMiddleware(persona="async_agent"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_registration.py tests/test_audit_registration.py tests/test_binary_read_guard.py tests/test_personas.py tests/test_hitl.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/orchestrator.py backend/app/services/deep_agent/personas.py backend/app/services/async_agents/agent.py tests/test_tool_guard_registration.py
git commit -m "wip(jev-B): register the guard in all four stacks (AUTO only)"
```

### Task 12: Audit-context stamping that keeps resume deterministic

This is deviation #1 and #2. Without it, `enforce` would let a rejected call run after resume.

**Files:**
- Modify: `backend/app/services/agents.py`
- Test: `tests/test_audit_context_stamping.py`

**Interfaces:**
- Produces: module function `_resume_audit_mode(yolo_mode: bool) -> str`; audit-context key `"user_message_id"` on both streaming builds; `"mode"` on every resume build; `invoke_workflow_resume(..., thread_id: int | None = None)` now stamps a full audit context.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit_context_stamping.py
"""Every audit-context literal in agents.py must carry `mode` and `thread_id`.

The System One guard's runtime belt reads `mode`; its committed-verdict lookup
keys on `thread_id`. A resume path missing `mode` makes the guard's re-run skip
interrupt(), so the human's decision is never consumed and a REJECTED call runs.
AST-level, same approach as test_arena_extractor_routing's stamping test.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.services.agents import _resume_audit_mode

_SOURCE = Path("backend/app/services/agents.py")


def _audit_context_literals():
    tree = ast.parse(_SOURCE.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Name) and key.id == "AUDIT_CONTEXT_KEY" and isinstance(value, ast.Dict):
                yield {k.value for k in value.keys if isinstance(k, ast.Constant)}


def test_every_audit_context_stamps_mode_and_thread_id():
    sites = list(_audit_context_literals())
    assert len(sites) >= 5, sites   # 2 streaming builds + 3 resume paths
    for keys in sites:
        assert {"mode", "thread_id"} <= keys, keys


def test_both_streaming_builds_stamp_the_turns_user_message():
    assert sum("user_message_id" in keys for keys in _audit_context_literals()) == 2


def test_resume_mode_recovers_the_interrupted_turns_mode():
    # A pending action exists only on an interactive or AUTO turn (YOLO never
    # interrupts), so the persisted clear-HITL flag recovers the mode exactly.
    assert _resume_audit_mode(True) == "auto"
    assert _resume_audit_mode(False) == "interactive"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_audit_context_stamping.py -q`
Expected: FAIL — `ImportError: cannot import name '_resume_audit_mode'`.

- [ ] **Step 3: Implement** (line numbers are at `ee266e9`; re-locate by the quoted text)

(a) After `resolve_execution_mode` (≈ line 1392), add:

```python
def _resume_audit_mode(yolo_mode: bool) -> str:
    """The execution mode a HITL resume continues.

    A pending action exists only on an interactive or AUTO turn — YOLO raises no
    interrupts — so the persisted clear-HITL flag recovers the mode exactly.
    Stamped on every resume so mode-gated middleware (the System One tool guard)
    sees the SAME mode on the resume pass as on the pass that interrupted; without
    it the guard's re-run would skip interrupt() and the human's decision would
    never be consumed.
    """
    return "auto" if yolo_mode else "interactive"
```

(b) Routed streaming build (≈ line 2325), in the `AUDIT_CONTEXT_KEY` dict, after `"thread_id": thread_id,` add:

```python
                    # The turn's own user message (persisted before the stream):
                    # the System One guard scopes "what the user asked" to it.
                    "user_message_id": latest_user.id if latest_user is not None else None,
```

(c) Direct streaming build: in `stream_and_persist`, the block (≈ line 2860)

```python
        with _database.SessionLocal() as session:
            context = self._context(
                session,
                page_context,
                effective_accounting_date,
                thread_id=thread_id,
            )
        assets = self._context_assets(page_context)
```

becomes

```python
        with _database.SessionLocal() as session:
            context = self._context(
                session,
                page_context,
                effective_accounting_date,
                thread_id=thread_id,
            )
            # The endpoint commits the user message before streaming, so this is
            # THIS turn's message; the System One guard reads the user's words
            # from it rather than from whatever is latest when a call is guarded.
            from .deep_agent.memory.runtime import latest_user_message_id

            user_message_id = latest_user_message_id(session, thread_id)
        assets = self._context_assets(page_context)
```

(verify the anchor is unique with `grep -n "effective_accounting_date,$" backend/app/services/agents.py`), and in the `AUDIT_CONTEXT_KEY` dict ≈ 12 lines below, after `"thread_id": thread_id,` add `"user_message_id": user_message_id,`.

(d) Orchestrator resume (≈ line 3466): in `AUDIT_CONTEXT_KEY: {"actor": actor, "thread_id": thread_id, "workflow_id": action_source_meta.get("workflow_id"), …}` add after `"actor": actor,`:

```python
                    "mode": _resume_audit_mode(yolo_mode),
```

(e) Plain resume (≈ line 3636): in `AUDIT_CONTEXT_KEY: {"actor": actor, "thread_id": thread_id, "message_id": message_id, …}` add after `"actor": actor,`:

```python
                "mode": _resume_audit_mode(yolo_mode),
```

(`yolo_mode` is in scope at both: `yolo_mode = bool(source_meta.get("yolo_mode", False))`.)

(f) `invoke_workflow_resume` (≈ line 3740): add `thread_id: int | None = None,` after `actor: str = "desk_user",` in the signature; in its `configurable_extra={...}` (≈ line 3827) add after `"tools_scope": ...`:

```python
                # Same resume contract as the other HITL resume paths: without an
                # audit context this path was unaudited AND invisible to the
                # System One guard, whose re-run must see mode + thread_id.
                AUDIT_CONTEXT_KEY: {
                    "actor": actor,
                    "mode": _resume_audit_mode(yolo_mode),
                    "thread_id": thread_id,
                    "workflow_id": workflow_id,
                    "session_id": source_session_id,
                    "task_id": task.id,
                    "audit_ref": (source_meta.get("audit") or {}).get("audit_ref"),
                },
```

and at its caller (≈ line 3265, `execution = self.invoke_workflow_resume(`) add `thread_id=thread_id,`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_audit_context_stamping.py tests/test_arena_extractor_routing.py tests/test_stream_and_persist_hitl.py tests/test_audit_hitl_capture.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py tests/test_audit_context_stamping.py
git commit -m "wip(jev-B): stamp mode/thread/user message on every audit context"
```

### Task 13: Read shadow data — `/api/audit/guard-verdicts` and `actions[].guard`

**Files:**
- Modify: `backend/app/routers/audit.py`
- Test: `tests/test_audit_guard_verdicts_api.py`

**Interfaces:**
- Consumes: `AgentToolGuardVerdict`.
- Produces: `GET /api/audit/guard-verdicts?verdict=&tool_name=&thread_id=&since=&limit=&offset=` → `{"items": [GuardVerdictOut], "total": int}`; `GET /api/audit/guard-verdicts/summary?since=` → `{"by_tool": [...], "unscored_reasons": {...}}`; `AuditActionOut.guard: {"verdict", "max_probability"} | None` on list, detail and `related`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit_guard_verdicts_api.py
"""Shadow data is only useful if it can be read (spec §1 "Reading shadow data").
New fields are asserted at the HTTP layer (root CLAUDE.md: three layers swallow them)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentActionAudit, AgentToolGuardVerdict
from app.routers.audit import build_audit_router

T = datetime(2026, 9, 21, 9, 0, 0)


@pytest.fixture()
def api(session):
    app = FastAPI()
    app.include_router(build_audit_router())
    return TestClient(app)


def _verdict(**kw):
    base = dict(thread_id=0, tool_call_id="c1", persona="trader", exec_mode="auto",
                guard_mode="shadow", tool_name="void_settlement_cashflow",
                args_json={"cashflow_id": 1}, redacted=False, args_hash="h",
                user_request_source="latest", verdict="clear", unscored_reason=None,
                predicates_json=[], max_probability=0.1, action="recorded",
                model="typesafe/jev-1.13", latency_ms=1000, error=None, created_at=T)
    base.update(kw)
    return AgentToolGuardVerdict(**base)


def _exec(**kw):
    base = dict(kind="execution", status="ok", tool_name="void_settlement_cashflow",
                tool_class="domain_write", args_json={})
    base.update(kw)
    return AgentActionAudit(**base)


@pytest.fixture()
def seeded(session, agent_thread_factory):
    t1, t2 = agent_thread_factory(), agent_thread_factory()
    session.add_all([
        _verdict(thread_id=t1.id, tool_call_id="a", verdict="flagged", max_probability=0.85,
                 latency_ms=1200, created_at=T.replace(minute=1)),
        _verdict(thread_id=t1.id, tool_call_id="b", verdict="clear", latency_ms=1000,
                 created_at=T.replace(minute=2)),
        _verdict(thread_id=t2.id, tool_call_id="a", verdict="unscored", unscored_reason="no_key",
                 max_probability=None, latency_ms=None, tool_name="close_position",
                 created_at=T.replace(minute=3)),
        _verdict(thread_id=t1.id, tool_call_id="", verdict="unscored",
                 unscored_reason="no_tool_call_id", max_probability=None, latency_ms=None,
                 created_at=T.replace(minute=4)),
        _exec(thread_id=t1.id, tool_call_id="a", status="ok"),
        _exec(thread_id=t1.id, tool_call_id="b", status="error"),
        _exec(thread_id=t2.id, tool_call_id="zz", status="ok"),
        _exec(thread_id=None, tool_call_id="", status="ok"),
    ])
    session.commit()
    return t1, t2


def test_list_is_newest_first_with_execution_status(api, seeded):
    t1, t2 = seeded
    body = api.get("/api/audit/guard-verdicts").json()
    assert body["total"] == 4
    assert [i["tool_call_id"] for i in body["items"]] == ["", "a", "b", "a"]
    by_key = {(i["thread_id"], i["tool_call_id"]): i for i in body["items"]}
    assert by_key[(t1.id, "a")]["execution_status"] == "ok"      # flagged, then ran fine
    assert by_key[(t1.id, "b")]["execution_status"] == "error"
    assert by_key[(t2.id, "a")]["execution_status"] is None      # same id, other thread
    assert by_key[(t1.id, "")]["execution_status"] is None       # empty ids never join
    flagged = by_key[(t1.id, "a")]
    assert flagged["predicates"] == [] and flagged["guard_mode"] == "shadow"
    assert set(flagged) >= {"id", "thread_id", "tool_call_id", "persona", "exec_mode",
                            "guard_mode", "tool_name", "args_json", "redacted", "verdict",
                            "unscored_reason", "predicates", "max_probability", "action",
                            "model", "latency_ms", "error", "created_at", "execution_status"}


def test_filters_and_inclusive_since(api, seeded):
    t1, _ = seeded
    assert api.get("/api/audit/guard-verdicts?verdict=flagged").json()["total"] == 1
    assert api.get("/api/audit/guard-verdicts?tool_name=close_position").json()["total"] == 1
    assert api.get(f"/api/audit/guard-verdicts?thread_id={t1.id}").json()["total"] == 3
    since = T.replace(minute=3).isoformat()
    assert api.get(f"/api/audit/guard-verdicts?since={since}").json()["total"] == 2
    assert len(api.get("/api/audit/guard-verdicts?limit=1&offset=1").json()["items"]) == 1


@pytest.mark.parametrize("query, status", [
    ("verdict=maybe", 400), ("since=not-a-date", 422), ("thread_id=x", 422),
    ("limit=0", 422), ("limit=201", 422), ("offset=-1", 422),
])
def test_bad_params(api, seeded, query, status):
    assert api.get(f"/api/audit/guard-verdicts?{query}").status_code == status


def test_summary(api, seeded):
    body = api.get("/api/audit/guard-verdicts/summary").json()
    by_tool = {row["tool_name"]: row for row in body["by_tool"]}
    assert set(by_tool) == {"void_settlement_cashflow", "close_position"}  # no zero rows
    void = by_tool["void_settlement_cashflow"]
    assert (void["total"], void["clear"], void["flagged"], void["unscored"]) == (3, 1, 1, 1)
    assert void["flagged_then_ok"] == 1          # the candidate false positives
    assert void["median_latency_ms"] == 1100.0   # nulls skipped
    assert by_tool["close_position"]["median_latency_ms"] is None
    assert body["unscored_reasons"] == {"no_key": 1, "no_tool_call_id": 1}
    since = T.replace(minute=3).isoformat()
    assert {r["tool_name"] for r in api.get(
        f"/api/audit/guard-verdicts/summary?since={since}").json()["by_tool"]} == {
        "close_position", "void_settlement_cashflow"}
    assert api.get("/api/audit/guard-verdicts/summary?since=bad").status_code == 422


def test_actions_carry_the_guard_joined_on_both_key_halves(api, seeded):
    t1, _ = seeded
    items = api.get("/api/audit/actions?limit=200").json()["items"]
    guard = {(i["thread_id"], i["tool_call_id"]): i["guard"] for i in items}
    assert guard[(t1.id, "a")] == {"verdict": "flagged", "max_probability": 0.85}
    assert guard[(t1.id, "b")] == {"verdict": "clear", "max_probability": 0.1}
    assert guard[(seeded[1].id, "zz")] is None
    assert guard[(None, "")] is None
    one = next(i for i in items if (i["thread_id"], i["tool_call_id"]) == (t1.id, "a"))
    assert api.get(f"/api/audit/actions/{one['id']}").json()["guard"]["verdict"] == "flagged"


def test_a_null_thread_action_joins_a_thread_zero_verdict(api, session):
    session.add_all([_verdict(thread_id=0, tool_call_id="n1", verdict="flagged"),
                     _exec(thread_id=None, tool_call_id="n1")])
    session.commit()
    [item] = api.get("/api/audit/actions").json()["items"]
    assert item["guard"]["verdict"] == "flagged"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_audit_guard_verdicts_api.py -q`
Expected: FAIL — 404 on `/api/audit/guard-verdicts`, `KeyError: 'guard'`.

- [ ] **Step 3: Implement** in `backend/app/routers/audit.py`

Imports: add `from statistics import median`, and change the models import to `from app.models import AgentActionAudit, AgentToolGuardVerdict`.

Add `guard: dict[str, Any] | None = None` as the last field of `AuditActionOut`, then replace `_out` with:

```python
_GUARD_VERDICTS = frozenset({"clear", "flagged", "unscored"})


def _call_key(thread_id: int | None, tool_call_id: str) -> tuple[int, str]:
    """Both halves of the verdict key; a NULL audit thread matches verdict thread 0."""
    return (thread_id or 0, tool_call_id)


def _guard_by_call(session, rows) -> dict[tuple[int, str], dict]:
    wanted = {_call_key(r.thread_id, r.tool_call_id) for r in rows if r.tool_call_id}
    if not wanted:
        return {}
    verdicts = (
        session.query(AgentToolGuardVerdict)
        .filter(AgentToolGuardVerdict.tool_call_id.in_({key[1] for key in wanted}))
        .all()
    )
    # The UNIQUE key makes each (thread_id, tool_call_id) at most one row.
    return {
        (v.thread_id, v.tool_call_id): {"verdict": v.verdict, "max_probability": v.max_probability}
        for v in verdicts
        if (v.thread_id, v.tool_call_id) in wanted
    }


def _out(row: AgentActionAudit, guard: dict | None = None) -> dict:
    data = {field: getattr(row, field) for field in AuditActionOut.model_fields if field != "guard"}
    return AuditActionOut(**data, guard=guard).model_dump()


def _outs(session, rows) -> list[dict]:
    guards = _guard_by_call(session, rows)
    return [
        _out(r, guards.get(_call_key(r.thread_id, r.tool_call_id)) if r.tool_call_id else None)
        for r in rows
    ]


class GuardVerdictOut(BaseModel):
    id: int
    thread_id: int
    tool_call_id: str
    persona: str | None
    exec_mode: str | None
    guard_mode: str
    tool_name: str
    args_json: Any
    redacted: bool
    verdict: str
    unscored_reason: str | None
    predicates: list[Any]
    max_probability: float | None
    action: str
    model: str | None
    latency_ms: int | None
    error: str | None
    created_at: Any
    execution_status: str | None


def _execution_status_by_call(session, verdicts) -> dict[tuple[int, str], str]:
    call_ids = {v.tool_call_id for v in verdicts if v.tool_call_id}
    if not call_ids:
        return {}
    rows = (
        session.query(AgentActionAudit.thread_id, AgentActionAudit.tool_call_id,
                      AgentActionAudit.status)
        .filter(AgentActionAudit.kind == "execution",
                AgentActionAudit.tool_call_id.in_(call_ids))
        .order_by(AgentActionAudit.id.asc())
        .all()
    )
    out: dict[tuple[int, str], str] = {}
    for thread_id, call_id, status in rows:
        out[_call_key(thread_id, call_id)] = status  # ascending id: newest wins
    return out


def _verdict_out(v: AgentToolGuardVerdict, execution_status: str | None) -> dict:
    return GuardVerdictOut(
        id=v.id, thread_id=v.thread_id, tool_call_id=v.tool_call_id, persona=v.persona,
        exec_mode=v.exec_mode, guard_mode=v.guard_mode, tool_name=v.tool_name,
        args_json=v.args_json, redacted=v.redacted, verdict=v.verdict,
        unscored_reason=v.unscored_reason, predicates=list(v.predicates_json or []),
        max_probability=v.max_probability, action=v.action, model=v.model,
        latency_ms=v.latency_ms, error=v.error, created_at=v.created_at,
        execution_status=execution_status,
    ).model_dump()
```

In `list_actions` replace `return {"items": [_out(r) for r in rows], "total": total}` with `return {"items": _outs(session, rows), "total": total}`. In `get_action` replace the return with:

```python
            outs = _outs(session, [row, *related])
            return {**outs[0], "related": outs[1:]}
```

Inside `build_audit_router`, before `return router`, add:

```python
    @router.get("/guard-verdicts")
    def list_guard_verdicts(
        verdict: str | None = None,
        tool_name: str | None = None,
        thread_id: int | None = None,
        since: datetime | None = None,
        limit: int = Query(50, le=200, ge=1),
        offset: int = Query(0, ge=0),
    ):
        """System One guard verdicts, newest first (spec §1 "Reading shadow data")."""
        if verdict is not None and verdict not in _GUARD_VERDICTS:
            raise HTTPException(400, f"verdict must be one of {sorted(_GUARD_VERDICTS)}")
        with database.SessionLocal() as session:
            q = session.query(AgentToolGuardVerdict)
            if verdict is not None:
                q = q.filter(AgentToolGuardVerdict.verdict == verdict)
            if tool_name is not None:
                q = q.filter(AgentToolGuardVerdict.tool_name == tool_name)
            if thread_id is not None:
                q = q.filter(AgentToolGuardVerdict.thread_id == thread_id)
            if since is not None:
                q = q.filter(AgentToolGuardVerdict.created_at >= since)
            total = q.count()
            rows = (
                q.order_by(AgentToolGuardVerdict.created_at.desc(),
                           AgentToolGuardVerdict.id.desc())
                .offset(offset).limit(limit).all()
            )
            status = _execution_status_by_call(session, rows)
            return {
                "items": [
                    _verdict_out(v, status.get(_call_key(v.thread_id, v.tool_call_id))
                                 if v.tool_call_id else None)
                    for v in rows
                ],
                "total": total,
            }

    @router.get("/guard-verdicts/summary")
    def guard_verdict_summary(since: datetime | None = None):
        """`flagged_then_ok` = flagged verdicts whose call then ran `ok` — the
        candidate false positives shadow mode exists to count."""
        with database.SessionLocal() as session:
            q = session.query(AgentToolGuardVerdict)
            if since is not None:
                q = q.filter(AgentToolGuardVerdict.created_at >= since)
            verdicts = q.all()
            status = _execution_status_by_call(session, verdicts)
        by_tool: dict[str, dict[str, Any]] = {}
        latencies: dict[str, list[int]] = {}
        reasons: dict[str, int] = {}
        for v in verdicts:
            row = by_tool.setdefault(v.tool_name, {
                "tool_name": v.tool_name, "total": 0, "clear": 0, "flagged": 0,
                "unscored": 0, "flagged_then_ok": 0, "median_latency_ms": None,
            })
            row["total"] += 1
            if v.verdict in _GUARD_VERDICTS:
                row[v.verdict] += 1
            if (v.verdict == "flagged" and v.tool_call_id
                    and status.get(_call_key(v.thread_id, v.tool_call_id)) == "ok"):
                row["flagged_then_ok"] += 1
            if v.latency_ms is not None:
                latencies.setdefault(v.tool_name, []).append(v.latency_ms)
            if v.unscored_reason:
                reasons[v.unscored_reason] = reasons.get(v.unscored_reason, 0) + 1
        for name, values in latencies.items():
            by_tool[name]["median_latency_ms"] = median(values)
        return {
            "by_tool": [by_tool[name] for name in sorted(by_tool)],
            "unscored_reasons": reasons,
        }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_audit_guard_verdicts_api.py tests/test_audit_router.py tests/test_audit_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/audit.py tests/test_audit_guard_verdicts_api.py
git commit -m "wip(jev-B): guard verdict read API + guard on audit actions"
```

### Task 14: Audit page `Guard` column

**Files:**
- Modify: `frontend/src/types.ts` (`AuditAction`), `frontend/src/routes/Audit.tsx`, `frontend/src/routes/Audit.test.tsx`, `frontend/src/routes/Audit.live.test.tsx`

**Interfaces:**
- Consumes: `guard` on `/api/audit/actions` rows (Task 13).
- Produces: TS `AuditAction.guard: { verdict: 'clear' | 'flagged' | 'unscored'; max_probability: number | null } | null`.

- [ ] **Step 1: Write the failing test** — append to `frontend/src/routes/Audit.test.tsx` (inside the file's top-level `describe`, or as a new `describe('Guard column', …)`):

```tsx
describe('Guard column', () => {
  it('shows the System One verdict, and a dash when the call was never guarded', () => {
    render(
      <Audit
        {...props({
          items: [
            { ...ROW, id: 1, guard: { verdict: 'flagged', max_probability: 0.85 } },
            { ...ROW, id: 2, tool_call_id: 'c2', guard: null },
          ],
          total: 2,
        })}
      />,
    );
    expect(screen.getByText('Guard')).toBeInTheDocument();
    expect(screen.getByText('flagged')).toBeInTheDocument();
    expect(screen.getByTitle('System One p=0.85')).toBeInTheDocument();
  });
});
```

Also add `guard: null,` to the `ROW` literal in `Audit.test.tsx` (after `completed_at`) and to the object returned by `action()` in `Audit.live.test.tsx` (before `...overrides`).

- [ ] **Step 2: Run to verify it fails**

Run: `(cd frontend && npx vitest run src/routes/Audit.test.tsx)`
Expected: FAIL — `Unable to find an element with the text: Guard`.

- [ ] **Step 3: Implement**

`frontend/src/types.ts` — add to `AuditAction`, after `completed_at`:

```ts
  /** System One tool-guard verdict for this call; null = never guarded. */
  guard: { verdict: 'clear' | 'flagged' | 'unscored'; max_probability: number | null } | null;
```

`frontend/src/routes/Audit.tsx` — after `STATUS_VARIANT`:

```tsx
const GUARD_VARIANT: Record<NonNullable<AuditAction['guard']>['verdict'], BadgeVariant> = {
  clear: 'pos',
  flagged: 'neg',
  unscored: 'warn',
};
```

and in `columns`, after the `mode` column:

```tsx
      {
        key: 'guard',
        header: 'Guard',
        width: '6.5rem',
        render: (row) =>
          row.guard ? (
            <span
              title={
                row.guard.max_probability != null
                  ? `System One p=${row.guard.max_probability.toFixed(2)}`
                  : 'System One could not score this call'
              }
            >
              <Badge variant={GUARD_VARIANT[row.guard.verdict]}>{row.guard.verdict}</Badge>
            </span>
          ) : (
            '—'
          ),
      },
```

- [ ] **Step 4: Run to verify it passes**

Run: `(cd frontend && npx vitest run src/routes/Audit.test.tsx src/routes/Audit.live.test.tsx && npx tsc --noEmit && echo tsc ok)`
Expected: PASS and `tsc ok`. Then eyeball the Audit page in light, dark and compact density (frontend/CLAUDE.md) — the column must not clip its badge.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/Audit.tsx frontend/src/routes/Audit.test.tsx frontend/src/routes/Audit.live.test.tsx
git commit -m "wip(jev-B): Guard badge column on the Audit page"
```

### Task 15: Phase B docs and the phase commit

**Files:** `backend/app/services/system_one/CLAUDE.md`, `backend/app/services/deep_agent/CLAUDE.md`, `CLAUDE.md`, `CHANGELOG.md`, `README.md`, `.env.example`

- [ ] **Step 1: Append to `backend/app/services/system_one/CLAUDE.md`**

```markdown
## The AUTO tool guard (`deep_agent/tool_guard*.py`)

- Scope: the nine destroy-and-terminate `"write"` tools in
  `tool_guard_policy.GUARD_POLICY` — desk policy, edited as data.
  `validate_policy` fails the agent build on a dead, non-`"write"`, duplicate or
  out-of-range entry.
- Registered iff `yolo_mode and allow_reply_options` (AUTO) in all FOUR stacks
  (orchestrator, each persona, the `general-purpose` override, the async agent),
  plus a runtime belt on `AUDIT_CONTEXT_KEY['mode'] == "auto"` — the default
  orchestrator graph is built once and reused across turns.
- **Determinism (D10).** A verdict is committed under UNIQUE
  `(thread_id, tool_call_id)` before any interrupt and read back on re-entry;
  a stored row is reused only if `tool_name` and `args_hash` match
  (`tool_call_id_collision` otherwise). Empty ids are never sent to Jev.
- **Every HITL resume path must stamp `mode` and `thread_id`** into the audit
  context (`agents._resume_audit_mode`; pinned by
  `tests/test_audit_context_stamping.py`). A resume without `mode` fails the
  belt, the re-run skips `interrupt()`, the decision is never consumed — and a
  call the human rejected runs.
- The user's words come from the DB (`user_message_id`, stamped by both streaming
  builds; else the thread's latest user message) — never from a persona's first
  human message, which is the orchestrator's paraphrase (D11).
- Shadow data: `GET /api/audit/guard-verdicts[/summary]`. `flagged_then_ok` is
  the candidate-false-positive count. Do not read early numbers as validation of
  the wording — the only evidence is post-hoc.
```

- [ ] **Step 2: `backend/app/services/deep_agent/CLAUDE.md`** — at the end of the "Audit trail" section's Gotchas list, add:

```markdown
- The System One tool guard stores its verdicts beside this trail, in
  `agent_tool_guard_verdicts`, joined by `(thread_id, tool_call_id)` — see
  [`services/system_one/CLAUDE.md`](../system_one/CLAUDE.md). Its resume
  determinism depends on every resume path stamping `mode` + `thread_id`.
```

- [ ] **Step 3: Root `CLAUDE.md`** — add to "Cross-cutting rules":

```markdown
- **A HITL resume must re-stamp the turn's audit context — `mode` and `thread_id`
  included.** LangGraph re-runs the interrupted node from the top; a mode-gated
  `after_model` middleware that sees a different mode on the resume pass skips its
  `interrupt()` and the human's decision is silently dropped.
  ([system_one](backend/app/services/system_one/CLAUDE.md))
```

- [ ] **Step 4: `CHANGELOG.md`** — under `### Added`:

```markdown
- **System One tool guard, shadow mode.** In AUTO mode, nine destroy-and-terminate
  tools (`void_settlement_cashflow`, `close_position`, `settle_position`,
  `mark_knockout`, `waive_limit_incident`, `resolve_limit_incident`,
  `delete_pricing_parameter_rows`, `remove_portfolio_sources`,
  `import_otc_positions`) get a per-call Jev verdict against a per-tool predicate,
  recorded in `agent_tool_guard_verdicts` (migration `0061`) and readable at
  `GET /api/audit/guard-verdicts[/summary]`; the Audit page shows a Guard column.
  Shadow never blocks. Every HITL resume now stamps its execution `mode`.
```

- [ ] **Step 5: `README.md`** — Configuration table, after the System One rows:

```markdown
| `OPEN_OTC_TOOL_GUARD` | `off` \| `shadow` (default) \| `enforce`. With System One on, AUTO-mode calls to nine destructive tools send the user's latest request, recent tool call/result heads and the pending call (sanitized) to Jev; `shadow` only records the verdict (`/api/audit/guard-verdicts`) | No |
```

- [ ] **Step 6: `.env.example`** — append `# OPEN_OTC_TOOL_GUARD=shadow   # off | shadow | enforce`.

- [ ] **Step 7: Acceptance gate**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-B.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-B.log"
(cd frontend && npx tsc --noEmit && npx vitest run src/routes/Audit.test.tsx src/routes/Audit.live.test.tsx)
```

Expected: no failures beyond the baseline.

- [ ] **Step 8: Squash Phase B**

```bash
git add backend/app/services/system_one/CLAUDE.md backend/app/services/deep_agent/CLAUDE.md CLAUDE.md CHANGELOG.md README.md .env.example
git commit -m "wip(jev-B): docs"
BASE=$(git log --format=%H --grep='^wip(jev-B)' --reverse | head -1)
git reset --soft "${BASE}~1"
git commit -F - <<'MSG'
feat(system-one): AUTO tool guard in shadow mode

Per-call Jev verdicts for nine destroy-and-terminate "write" tools in AUTO,
committed under UNIQUE (thread_id, tool_call_id) (migration 0061) so a
resume never re-asks a non-deterministic model. Shadow records and never
blocks. Every HITL resume now stamps mode + thread_id; streaming builds
stamp the turn's user message. Read API + Audit page Guard column.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -3
```

---

## Phase C — commit 3: enforcement (`OPEN_OTC_TOOL_GUARD=enforce`)

Everything here is behind `enforce`; reverting this commit leaves shadow intact. Commits are `wip(jev-C): …`, squashed in Task 19. **Do not enable `enforce` on a desk until the multi-call-resume limit (top of plan) is resolved.**

### Task 16: Enforce — the interrupt pass

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py` (add `GUARD_NOTE_PREFIX`), `backend/app/services/deep_agent/tool_guard.py`
- Test: `tests/test_tool_guard_enforce.py`

**Interfaces:**
- Consumes: Task 10 seams (`_Pass`, `GuardDecision`, `_conclude`), `mark_interrupted` (Task 8).
- Produces: `hitl.GUARD_NOTE_PREFIX = "System One guard:"`; `tool_guard.guard_note(decision) -> str`; `tool_guard.interrupt` (module-level import, monkeypatchable); `_conclude` now interrupts in `enforce`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_enforce.py
"""Enforce: flagged or unscoreable calls take the normal approval card; the
interrupt set is a pure function of committed state (D8, D10)."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from _system_one_fakes import JevPost
from app import database
from app.models import AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard
from app.services.deep_agent.hitl import GUARD_NOTE_PREFIX
from app.services.deep_agent.tool_guard import ToolGuardMiddleware
from app.services.deep_agent.tool_guard_store import args_fingerprint, commit_verdict


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "enforce")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def thread(session, agent_thread_factory, monkeypatch):
    t = agent_thread_factory()
    session.add(AgentMessage(thread_id=t.id, role="user", content="Settle today's cashflows.", meta={}))
    session.commit()
    monkeypatch.setattr(tool_guard, "_read_audit_context",
                        lambda: {"mode": "auto", "thread_id": t.id})
    return t


class Interrupts:
    """Fake langgraph.types.interrupt: records requests, replays scripted answers.
    A scripted exception class is raised (a first pass that pauses)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer()
        return answer


class Paused(Exception):
    pass


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def run(mw, *calls):
    ai = AIMessage("", tool_calls=list(calls))
    return ai, mw.after_model({"messages": [HumanMessage("task"), ai]}, None)


def approve():
    return {"decisions": [{"type": "approve"}]}


def reject():
    return {"decisions": [{"type": "reject"}]}


VOID = call("void_settlement_cashflow", {"cashflow_id": 9300}, "c1")


def test_flagged_cards_and_approve_keeps_the_call(enforce_env, thread, monkeypatch):
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.85})), VOID)
    [action] = fake.requests[0]["action_requests"]
    assert action["name"] == "void_settlement_cashflow"
    assert action["description"].startswith(GUARD_NOTE_PREFIX)
    assert "unnamed_target p=0.85" in action["description"]
    assert fake.requests[0]["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert result["messages"] == [ai] and ai.tool_calls == [VOID]
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).one().action == "interrupted"


def test_reject_keeps_the_call_and_answers_it_with_an_error(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "interrupt", Interrupts(reject()))
    ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost({"from_document": 0.9})), VOID)
    [tool_message] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert (tool_message.status, tool_message.tool_call_id) == ("error", "c1")
    assert ai.tool_calls == [VOID]


def test_clear_runs_unattended(enforce_env, thread, monkeypatch):
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost()), VOID)
    assert result is None and fake.requests == []


def _no_key(mp, post, session, thread):
    mp.delenv("ZENMUX_API_KEY")


def _timeout(mp, post, session, thread):
    post.exc = TimeoutError("slow")


def _http(mp, post, session, thread):
    post.exc = RuntimeError("HTTP 502")


def _bad(mp, post, session, thread):
    post.response = {"answers": {}}


def _too_large(mp, post, session, thread):
    mp.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "10")


def _internal(mp, post, session, thread):
    def boom(*a, **k):
        raise RuntimeError("bug")
    mp.setattr(tool_guard, "build_guard_state", boom)


def _no_user(mp, post, session, thread):
    session.query(AgentMessage).filter_by(thread_id=thread.id).delete()
    session.commit()


def _collision(mp, post, session, thread):
    fp = args_fingerprint("void_settlement_cashflow", {"cashflow_id": 1})   # different args, same id
    commit_verdict(dict(thread_id=thread.id, tool_call_id="c1", persona="trader",
                        exec_mode="auto", guard_mode="enforce",
                        tool_name="void_settlement_cashflow", args_json=fp.payload,
                        redacted=False, args_hash=fp.sha256, user_request_source="latest",
                        verdict="clear", unscored_reason=None, predicates_json=[],
                        max_probability=0.05, model="m", latency_ms=1, error=None))


@pytest.mark.parametrize("setup, reason", [
    (_no_key, "no_key"), (_timeout, "timeout"), (_http, "http_error"),
    (_bad, "bad_response"), (_too_large, "state_too_large"), (_internal, "internal_error"),
    (_no_user, "no_user_request"), (_collision, "tool_call_id_collision"),
])
def test_every_unscored_reason_takes_the_approval_card(
        enforce_env, thread, session, monkeypatch, setup, reason):
    post = JevPost()
    setup(monkeypatch, post, session, thread)
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    run(ToolGuardMiddleware(persona="trader", post=post), VOID)
    [action] = fake.requests[0]["action_requests"]
    assert f"({reason})" in action["description"]


def test_empty_id_cards_even_when_its_best_effort_row_is_lost(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "record_structural", lambda fields: None)
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost()
    run(ToolGuardMiddleware(persona="trader", post=post),
        call("void_settlement_cashflow", {"cashflow_id": 1}, ""))
    assert post.calls == []
    assert "(no_tool_call_id)" in fake.requests[0]["action_requests"][0]["description"]


def test_resume_reuses_the_committed_verdict_and_never_re_asks(enforce_env, thread, monkeypatch):
    """LangGraph re-runs the node on resume. A fresh (non-deterministic) answer
    would shrink the card set and mis-attach the positional decision."""
    fake = Interrupts(Paused, reject())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost({"unnamed_target": 0.9})
    mw = ToolGuardMiddleware(persona="trader", post=post)
    ai = AIMessage("", tool_calls=[VOID])
    state = {"messages": [HumanMessage("task"), ai]}
    with pytest.raises(Paused):
        mw.after_model(state, None)
    post.probs = {}                                     # a fresh ask would now be "clear"
    result = mw.after_model(state, None)
    assert len(post.calls) == 1
    assert fake.requests[1]["action_requests"] == fake.requests[0]["action_requests"]
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"])


def test_mixed_message_one_request_original_order(enforce_env, thread, monkeypatch):
    fake = Interrupts({"decisions": [{"type": "approve"}, {"type": "reject"}]})
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    clear = call("void_settlement_cashflow", {"cashflow_id": 1}, "c-clear")
    flagged = call("close_position", {"position_id": 27}, "c-flag")
    unscored = call("mark_knockout", {"position_id": 28}, "")

    class PerCall(JevPost):
        def __call__(self, url, payload, timeout):
            name = payload["state"]["pending_tool_call"]["name"]
            self.probs = {"unnamed_target": 0.9} if name == "close_position" else {}
            return super().__call__(url, payload, timeout)

    ai, result = run(ToolGuardMiddleware(persona="trader", post=PerCall()), clear, flagged, unscored)
    names = [a["name"] for a in fake.requests[0]["action_requests"]]
    assert names == ["close_position", "mark_knockout"]
    assert ai.tool_calls == [clear, flagged, unscored]
    [rejected] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert rejected.tool_call_id == ""   # the second carded call was rejected


def test_a_decision_count_mismatch_raises(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "interrupt", Interrupts({"decisions": []}))
    with pytest.raises(ValueError, match="decisions"):
        run(ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.9})), VOID)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_enforce.py -q`
Expected: FAIL — `ImportError: cannot import name 'GUARD_NOTE_PREFIX'`.

- [ ] **Step 3: Implement**

`hitl.py` — directly above `_SUMMARY_BUILDERS`:

```python
#: Prefix of the ActionRequest.description the System One tool guard writes
#: (deep_agent/tool_guard.py). _summary_for keeps such a note on the card even
#: when a summary builder wins, so the card always says why AUTO paused.
GUARD_NOTE_PREFIX = "System One guard:"
```

`tool_guard.py` — imports: add

```python
from langchain.agents.middleware.human_in_the_loop import ActionRequest, HITLRequest, ReviewConfig
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt
```

extend `from .hitl import _RISK_LEVEL_BY_TOOL` to `from .hitl import GUARD_NOTE_PREFIX, _RISK_LEVEL_BY_TOOL`, and add `mark_interrupted` to the `tool_guard_store` import. Add a module function:

```python
def guard_note(decision: GuardDecision) -> str:
    """The card's "why". Probabilities only for a flag; the reason otherwise."""
    if decision.verdict == "flagged":
        fired = ", ".join(
            f"{p['key']} p={p['probability']:.2f} ≥ {p['threshold']:.2f}"
            for p in decision.predicates if p.get("flagged")
        )
        return f"{GUARD_NOTE_PREFIX} flagged — {fired}"
    return (
        f"{GUARD_NOTE_PREFIX} could not score this call ({decision.unscored_reason}); "
        "review it as you would in interactive mode"
    )
```

Replace `_conclude` and add `_interrupt`:

```python
    def _conclude(self, p: _Pass, decisions: list[GuardDecision]) -> dict[str, Any] | None:
        if p.settings.tool_guard_mode != "enforce":
            return None  # shadow: recorded, never blocked
        carded = [d for d in decisions if d.verdict in ("flagged", "unscored")]
        if not carded:
            return None
        return self._interrupt(p.ai_message, carded)

    def _interrupt(self, ai: AIMessage, carded: list[GuardDecision]) -> dict[str, Any]:
        """One HITLRequest for every carded call, in original tool-call order;
        decisions processed exactly as LongRunningCostHITLMiddleware does (D8)."""
        response = interrupt(
            HITLRequest(
                action_requests=[
                    ActionRequest(
                        name=d.tool_call["name"],
                        args=d.tool_call.get("args") or {},
                        description=guard_note(d),
                    )
                    for d in carded
                ],
                review_configs=[
                    ReviewConfig(
                        action_name=d.tool_call["name"],
                        allowed_decisions=self.interrupt_on[d.tool_call["name"]]["allowed_decisions"],
                    )
                    for d in carded
                ],
            )
        )
        decisions = response["decisions"]
        if len(decisions) != len(carded):
            raise ValueError("Number of human decisions does not match tool-guard interrupts.")
        by_index = {d.index: decision for d, decision in zip(carded, decisions)}
        revised: list[ToolCall] = []
        answers: list[ToolMessage] = []
        for index, tool_call in enumerate(ai.tool_calls):
            if index not in by_index:
                revised.append(tool_call)  # clear and unguarded calls run as AUTO intends
                continue
            new_call, message = self._process_decision(
                by_index[index], tool_call, self.interrupt_on[tool_call["name"]]
            )
            if new_call is not None:
                revised.append(new_call)
            if message is not None:
                answers.append(message)
        mark_interrupted(d.row_id for d in carded)
        ai.tool_calls = revised
        return {"messages": [ai, *answers]}
```

(`ToolMessage` is used in Task 17; importing it now is fine.) Update the module docstring's "`enforce` … (added by the guard-enforce commit)" sentence to present tense.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_enforce.py tests/test_tool_guard_middleware.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py backend/app/services/deep_agent/tool_guard.py tests/test_tool_guard_enforce.py
git commit -m "wip(jev-C): enforce — card flagged and unscoreable calls"
```

### Task 17: Enforce — the refusal pass (verdict store unwritable)

**Files:**
- Modify: `backend/app/services/deep_agent/tool_guard.py`
- Test: `tests/test_tool_guard_enforce.py` (append)

**Interfaces:**
- Produces: `_REFUSAL` message template; `_conclude` refuses (no interrupt) when any decision is `persist_failed`.

- [ ] **Step 1: Write the failing tests** — append:

```python
from app.services.deep_agent.tool_guard_store import GuardStoreUnavailable  # noqa: E402


def _fail_commit_for(monkeypatch, failing_id):
    real = tool_guard.commit_verdict

    def commit(fields):
        if fields["tool_call_id"] == failing_id:
            raise GuardStoreUnavailable("locked")
        return real(fields)

    monkeypatch.setattr(tool_guard, "commit_verdict", commit)


def test_mixed_with_a_persist_failure_is_a_refusal_pass(enforce_env, thread, monkeypatch):
    """[clear, flagged, persist_failed] => no interrupt at all; every guarded call
    that is not a committed clear is refused; order preserved (D10 rule 3)."""
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    _fail_commit_for(monkeypatch, "c-fail")
    clear = call("void_settlement_cashflow", {"cashflow_id": 1}, "c-clear")
    flagged = call("close_position", {"position_id": 27}, "c-flag")
    failed = call("settle_position", {"position_id": 28}, "c-fail")
    unguarded = call("get_position_summaries", {}, "c-read")

    class PerCall(JevPost):
        def __call__(self, url, payload, timeout):
            name = payload["state"]["pending_tool_call"]["name"]
            self.probs = {"unnamed_target": 0.9} if name == "close_position" else {}
            return super().__call__(url, payload, timeout)

    ai, result = run(ToolGuardMiddleware(persona="trader", post=PerCall()),
                     clear, flagged, failed, unguarded)
    assert fake.requests == []
    assert ai.tool_calls == [clear, flagged, failed, unguarded]   # calls stay, answered
    refused = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in refused] == ["c-flag", "c-fail"]
    assert all(m.status == "error" and "fail-closed" in m.content for m in refused)


def test_shadow_runs_on_a_persist_failure(thread, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "shadow")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    _fail_commit_for(monkeypatch, "c1")
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost()), VOID)
    assert result is None


def test_an_unreadable_store_on_resume_refuses_rather_than_re_asks(enforce_env, thread, monkeypatch):
    def down(*a, **k):
        raise GuardStoreUnavailable("locked")

    monkeypatch.setattr(tool_guard, "find_verdict", down)
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost()
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=post), VOID)
    assert fake.requests == [] and post.calls == []
    assert isinstance(result["messages"][-1], ToolMessage)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_enforce.py -q -k "persist or unreadable"`
Expected: FAIL — the persist-failure case reaches `interrupt` (fake has no scripted answers → `IndexError`) or returns `None`.

- [ ] **Step 3: Implement** — in `tool_guard.py` add after `PERSIST_FAILED`:

```python
_REFUSAL = (
    "Guard verdict store unavailable; destructive action '{name}' blocked "
    "(fail-closed). Read-only tools still work; retry shortly."
)
```

In `_conclude`, before `carded = …`:

```python
        # D10 rule 3: a pass either refuses or interrupts, never both. An interrupt
        # here would be followed on resume by a lookup that finds nothing, a fresh
        # Jev call, and possibly a different card set.
        if any(d.verdict == PERSIST_FAILED for d in decisions):
            return self._refuse(p.ai_message, decisions)
```

and add:

```python
    def _refuse(self, ai: AIMessage, decisions: list[GuardDecision]) -> dict[str, Any]:
        """Refuse every guarded call that is not a committed clear.

        The calls STAY on the AIMessage, each answered by an error ToolMessage —
        the construction _process_decision uses for a rejection. A dropped call
        would orphan its ToolMessage, which providers reject. No interrupt is
        raised, so the node is never re-entered: nothing to keep deterministic.
        Not a new posture: the audit trail's fail-closed phase 1 would refuse
        these writes against the same database a moment later.
        """
        refused = {d.index for d in decisions if d.verdict != "clear"}
        answers = [
            ToolMessage(
                content=_REFUSAL.format(name=tool_call["name"]),
                name=tool_call["name"],
                tool_call_id=tool_call.get("id") or "",
                status="error",
            )
            for index, tool_call in enumerate(ai.tool_calls)
            if index in refused
        ]
        return {"messages": [ai, *answers]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_enforce.py tests/test_tool_guard_middleware.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/tool_guard.py tests/test_tool_guard_enforce.py
git commit -m "wip(jev-C): enforce — fail-closed refusal pass"
```

### Task 18: The guard note reaches the approval card

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py` (`_summary_for`)
- Test: `tests/test_tool_guard_card.py`

**Interfaces:**
- Produces: `hitl._generic_summary(name, args) -> str`; `_summary_for` appends a `GUARD_NOTE_PREFIX` description to the builder or generic summary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_guard_card.py
"""The card must say WHY AUTO paused — including for tools whose summary
builder beats `description` (close/settle/knockout)."""
from __future__ import annotations

from app.services.deep_agent.hitl import GUARD_NOTE_PREFIX, _summary_for

NOTE = f"{GUARD_NOTE_PREFIX} flagged — unnamed_target p=0.85 ≥ 0.50"


def test_builder_tool_keeps_its_summary_and_gains_the_note():
    summary = _summary_for({"name": "close_position", "args": {}, "description": NOTE})
    assert summary == f"Close position — {NOTE}"


def test_tool_without_a_builder_shows_its_args_and_the_note():
    summary = _summary_for({"name": "void_settlement_cashflow",
                            "args": {"cashflow_id": 9300}, "description": NOTE})
    assert summary == f"Run void_settlement_cashflow (cashflow_id=9300) — {NOTE}"


def test_existing_behaviour_is_unchanged():
    generic = "Tool execution requires approval\n\nTool: close_position\nArgs: {}"
    assert _summary_for({"name": "close_position", "args": {}, "description": generic}) == "Close position"
    assert _summary_for({"name": "quote_rfq", "args": {"rfq_id": 1},
                         "description": "custom text"}) == "custom text"
    assert _summary_for({"name": "quote_rfq", "args": {}}) == "Run quote_rfq"
    assert _summary_for({"name": "quote_rfq", "args": {"rfq_id": 1}}) == "Run quote_rfq (rfq_id=1)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_card.py -q`
Expected: FAIL — builder output without the note.

- [ ] **Step 3: Implement** — in `hitl.py` replace `_summary_for` with (keep its long comment about builders winning, placed above `builder = …`):

```python
def _generic_summary(name: str, args: dict[str, Any]) -> str:
    if not args:
        return f"Run {name}"
    arg_summary = ", ".join(f"{k}={_compact_value(v)}" for k, v in list(args.items())[:4])
    return f"Run {name} ({arg_summary})"


def _summary_for(action_request: dict[str, Any]) -> str:
    name = action_request["name"]
    args = action_request.get("args") or {}
    description = action_request.get("description")
    # The System One guard's "why" must survive a winning builder, or the card
    # never says why AUTO paused for close/settle/knockout.
    guard_note = (
        description
        if isinstance(description, str) and description.startswith(GUARD_NOTE_PREFIX)
        else None
    )
    # <- move the existing 8-line comment block that starts "A registered builder
    #    is a deliberate, tool-specific override and MUST win" here, verbatim.
    builder = _SUMMARY_BUILDERS.get(name)
    if builder is not None:
        summary = builder(args)
    elif guard_note is not None:
        summary = _generic_summary(name, args)
    elif isinstance(description, str) and description:
        return description
    else:
        return _generic_summary(name, args)
    return f"{summary} — {guard_note}" if guard_note else summary
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tool_guard_card.py tests/test_hitl.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py tests/test_tool_guard_card.py
git commit -m "wip(jev-C): guard note survives summary builders on the card"
```

### Task 19: Phase C docs and the phase commit

- [ ] **Step 1: Append to `backend/app/services/system_one/CLAUDE.md`**

```markdown
### Enforce

- `OPEN_OTC_TOOL_GUARD=enforce`: `flagged` and every `unscored` reason take the
  normal approval card (D8) — AUTO degrades to interactive for the nine tools; it
  stops nothing. The one refusal: if any guarded call's verdict cannot be
  committed, the pass raises NO interrupt and refuses every guarded call that is
  not a committed `clear` (calls stay on the AIMessage, answered by error
  ToolMessages).
- **Before enabling it:** `build_resume_command` sends ONE decision, so a card
  holding ≥ 2 guarded calls from one AIMessage cannot be resumed (the same
  pre-existing limit every HITL middleware here has).
```

- [ ] **Step 2: `CHANGELOG.md`** under `### Added`:

```markdown
- **System One tool guard, `enforce` mode** (`OPEN_OTC_TOOL_GUARD=enforce`, off by
  default): flagged or unscoreable AUTO calls to the nine guarded tools take the
  normal approval card, with the reason on the card; an unwritable verdict store
  refuses them fail-closed.
```

- [ ] **Step 3: Acceptance gate**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-C.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-C.log"
(cd frontend && npx tsc --noEmit && echo tsc ok)
```

- [ ] **Step 4: Squash Phase C**

```bash
git add backend/app/services/system_one/CLAUDE.md CHANGELOG.md
git commit -m "wip(jev-C): docs"
BASE=$(git log --format=%H --grep='^wip(jev-C)' --reverse | head -1)
git reset --soft "${BASE}~1"
git commit -F - <<'MSG'
feat(system-one): tool guard enforce mode (off by default)

In enforce, flagged or unscoreable AUTO calls re-promote to the normal
approval card; decisions are processed like LongRunningCostHITLMiddleware.
A pass whose verdict cannot be committed refuses instead of interrupting,
so the card set is always a function of committed state. The guard's
reason now survives summary builders on the card.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -4
```

---

## Phase D — commit 4: memory keep-alive score (display-only)

Commits are `wip(jev-D): …`, squashed in Task 26.

### Task 20: `MemoryConfig` keep-alive switches

**Files:**
- Modify: `backend/app/services/deep_agent/memory/config.py`
- Test: `tests/test_memory_keep_alive_config.py`

**Interfaces:**
- Produces: `MemoryConfig.keep_alive_enabled: bool = True`, `keep_alive_batch: int = 10`, `keep_alive_sibling_limit: int = 50`, `keep_alive_sibling_chars: int = 240`, `keep_alive_refresh_days: int = 30`; `get_memory_config()` reads `OPEN_OTC_MEMORY_KEEP_ALIVE` with the `OPEN_OTC_MEMORY` parser.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_keep_alive_config.py
from __future__ import annotations

import pytest

from app.services.deep_agent.memory.config import MemoryConfig, get_memory_config


def test_defaults():
    c = MemoryConfig()
    assert (c.keep_alive_enabled, c.keep_alive_batch, c.keep_alive_sibling_limit,
            c.keep_alive_sibling_chars, c.keep_alive_refresh_days) == (True, 10, 50, 240, 30)


@pytest.mark.parametrize("raw, expected", [
    (None, True), ("on", True), ("ON", True), ("off", False), ("0", False), ("false", False),
])
def test_env_switch_uses_the_memory_parser(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OPEN_OTC_MEMORY_KEEP_ALIVE", raising=False)
    else:
        monkeypatch.setenv("OPEN_OTC_MEMORY_KEEP_ALIVE", raw)
    assert get_memory_config().keep_alive_enabled is expected


def test_memory_switch_is_unchanged(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "off")
    assert get_memory_config().enabled is False
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    assert get_memory_config().enabled is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_config.py -q`
Expected: FAIL — `AttributeError: 'MemoryConfig' object has no attribute 'keep_alive_enabled'`.

- [ ] **Step 3: Implement** — in `MemoryConfig`, before `correction_phrases`:

```python
    # System One keep-alive score (spec 2026-09-21 §2) — DISPLAY-ONLY. Live iff
    # OPEN_OTC_SYSTEM_ONE, OPEN_OTC_MEMORY and OPEN_OTC_MEMORY_KEEP_ALIVE are all on.
    keep_alive_enabled: bool = True
    keep_alive_batch: int = 10          # due facts per scoring pass
    keep_alive_sibling_limit: int = 50  # other facts in scope sent as context
    keep_alive_sibling_chars: int = 240
    keep_alive_refresh_days: int = 30   # a score is itself a staleness judgment
```

Replace `get_memory_config` with:

```python
def _switch(name: str) -> bool:
    return os.environ.get(name, "on").lower() not in {"off", "0", "false"}


def get_memory_config() -> MemoryConfig:
    return MemoryConfig(
        enabled=_switch("OPEN_OTC_MEMORY"),
        keep_alive_enabled=_switch("OPEN_OTC_MEMORY_KEEP_ALIVE"),
        reconcile_since=_parse_reconcile_since(
            os.environ.get("OPEN_OTC_MEMORY_RECONCILE_SINCE")),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_config.py tests/test_memory_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/memory/config.py tests/test_memory_keep_alive_config.py
git commit -m "wip(jev-D): keep-alive switches in MemoryConfig"
```

### Task 21: Keep-alive columns, migration `0062`, and `Fact` fields

**Files:**
- Modify: `backend/app/models.py` (`MemoryEntry`), `backend/app/services/deep_agent/memory/store.py` (`Fact`, `_to_fact`)
- Create: `backend/alembic/versions/0062_memory_keep_alive.py`
- Test: `tests/test_migration_0062_memory_keep_alive.py`

**Interfaces:**
- Produces: `MemoryEntry.keep_alive_score: float|None`, `.keep_alive_confidence: float|None`, `.keep_alive_scored_at: datetime|None`, `.keep_alive_attempted_at: datetime|None`, `.keep_alive_unscored_reason: str|None` (VARCHAR 40); `Fact.keep_alive_score/keep_alive_confidence/keep_alive_scored_at/keep_alive_unscored_reason` (defaults `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_0062_memory_keep_alive.py
"""0062 adds five nullable columns; downgrade's batch rebuild must keep the
partial ux_memory_dedup predicate and the rows."""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

COLS = {"keep_alive_score", "keep_alive_confidence", "keep_alive_scored_at",
        "keep_alive_attempted_at", "keep_alive_unscored_reason"}


def _run(method, engine):
    module = importlib.import_module("backend.alembic.versions.0062_memory_keep_alive")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _pre_0062(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre62.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY, scope_type VARCHAR(16), scope_id VARCHAR(120),
                content TEXT, normalized_content TEXT, confidence FLOAT,
                status VARCHAR(16), category VARCHAR(64), source_error BOOLEAN,
                created_by VARCHAR(16), pinned BOOLEAN, meta JSON,
                created_at DATETIME, updated_at DATETIME
            )"""))
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX ux_memory_dedup ON memory_entries "
            "(scope_type, scope_id, normalized_content) WHERE status != 'archived'"))
        conn.execute(sa.text(
            "INSERT INTO memory_entries VALUES (1, 'user', 'desk', 'books in USD', "
            "'books in usd', 0.9, 'active', NULL, 0, 'api', 1, '{}', "
            "'2026-09-01 00:00:00', '2026-09-01 00:00:00')"))
    return engine


def test_upgrade_adds_five_nullable_columns_and_keeps_the_row(tmp_path):
    engine = _pre_0062(tmp_path)
    _run("upgrade", engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("memory_entries")}
    assert COLS <= set(cols) and all(cols[c]["nullable"] for c in COLS)
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT content, keep_alive_score, keep_alive_unscored_reason FROM memory_entries")).one()
    assert tuple(row) == ("books in USD", None, None)


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    from app.models import MemoryEntry

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    MemoryEntry.__table__.create(bind=engine)
    _run("upgrade", engine)
    assert COLS <= {c["name"] for c in inspect(engine).get_columns("memory_entries")}


def test_downgrade_removes_them_and_keeps_rows_and_the_partial_index(tmp_path):
    engine = _pre_0062(tmp_path)
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert not COLS & {c["name"] for c in inspect(engine).get_columns("memory_entries")}
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM memory_entries")).scalar() == 1
        ddl = conn.execute(sa.text(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_memory_dedup'")).scalar()
    assert "WHERE status != 'archived'" in ddl
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migration_0062_memory_keep_alive.py -q`
Expected: FAIL — `ModuleNotFoundError` for the migration.

- [ ] **Step 3: Implement**

`models.py` — in `MemoryEntry`, after `updated_at`:

```python
    # System One keep-alive (spec 2026-09-21 §2) — DISPLAY-ONLY; NULL = never
    # scored (D14). Writers pin updated_at to itself: load_injectable orders by it.
    keep_alive_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    keep_alive_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    keep_alive_scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    keep_alive_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    keep_alive_unscored_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

`backend/alembic/versions/0062_memory_keep_alive.py`:

```python
"""memory_entries keep-alive columns — System One's display-only score

Revision ID: 0062_memory_keep_alive
Revises: 0061_tool_guard_verdicts

Five nullable columns (spec 2026-09-21 §2). NULL means never scored, which is not
0 (D14); keep_alive_unscored_reason makes a broken setup visible (D17).

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already has
them. Downgrade drops through batch_alter_table, never a direct drop_column.
HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0062_memory_keep_alive"
down_revision = "0061_tool_guard_verdicts"
branch_labels = None
depends_on = None

_TABLE = "memory_entries"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("keep_alive_score", sa.Float()),
    ("keep_alive_confidence", sa.Float()),
    ("keep_alive_scored_at", sa.DateTime()),
    ("keep_alive_attempted_at", sa.DateTime()),
    ("keep_alive_unscored_reason", sa.String(40)),
)


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        return
    existing = _columns(_TABLE)
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    if _TABLE not in _tables():
        return
    present = [name for name, _type in _COLUMNS if name in _columns(_TABLE)]
    if present:
        with op.batch_alter_table(_TABLE) as batch:
            for name in present:
                batch.drop_column(name)
```

`memory/store.py` — add to the end of the `Fact` dataclass:

```python
    keep_alive_score: float | None = None
    keep_alive_confidence: float | None = None
    keep_alive_scored_at: datetime | None = None
    keep_alive_unscored_reason: str | None = None
```

and pass them in `_to_fact`:

```python
        keep_alive_score=row.keep_alive_score,
        keep_alive_confidence=row.keep_alive_confidence,
        keep_alive_scored_at=row.keep_alive_scored_at,
        keep_alive_unscored_reason=row.keep_alive_unscored_reason,
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migration_0062_memory_keep_alive.py tests/test_migration_fresh_chain.py tests/test_memory_store_crud.py tests/test_memory_migration.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0062_memory_keep_alive.py backend/app/services/deep_agent/memory/store.py tests/test_migration_0062_memory_keep_alive.py
git commit -m "wip(jev-D): keep-alive columns (migration 0062)"
```

### Task 22: Invalidation and the sibling limit

**Files:**
- Modify: `backend/app/services/deep_agent/memory/store.py`
- Test: `tests/test_memory_keep_alive_invalidation.py`

**Interfaces:**
- Produces: `store.KEEP_ALIVE_RESET: dict[str, None]` (all five columns); `MemoryStore.load_existing(session, scope_type, scope_id, limit: int = 50)`; `_update_row` nulls all five on a content change; `_apply_diff_inner` nulls all five for every non-archived fact of each scope it added to, preserving `updated_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_keep_alive_invalidation.py
"""A score is invalid once the fact or its scope changes (spec §2 Invalidation)."""
from __future__ import annotations

from datetime import datetime

from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore, WriteContext

T0 = datetime(2026, 9, 1)
SCORED = dict(keep_alive_score=0.9, keep_alive_confidence=0.8, keep_alive_scored_at=T0,
              keep_alive_attempted_at=T0, keep_alive_unscored_reason="bad_response")
COLS = tuple(SCORED)


def _fact(session, content, scope_type="user", scope_id="desk", **kw):
    row = MemoryEntry(scope_type=scope_type, scope_id=scope_id, content=content,
                      normalized_content=content.lower(), confidence=kw.pop("confidence", 0.9),
                      status=kw.pop("status", "active"), created_by="extractor", pinned=False,
                      meta={}, created_at=T0, updated_at=T0, **{**SCORED, **kw})
    session.add(row)
    session.commit()
    return row.id


def _cols(row_id):
    with database.SessionLocal() as s:
        row = s.get(MemoryEntry, row_id)
        return {c: getattr(row, c) for c in COLS}, row.updated_at


class _Diff:
    def __init__(self, add=(), remove=(), update=()):
        self.add, self.remove, self.update = list(add), list(remove), list(update)


def test_a_content_edit_nulls_all_five(session):
    store = MemoryStore(MemoryConfig())
    fid = _fact(session, "books in USD")
    with database.SessionLocal() as s:
        store.update(s, fid, content="books everything in USD")
        s.commit()
    assert set(_cols(fid)[0].values()) == {None}


def test_a_confidence_only_edit_keeps_the_score(session):
    store = MemoryStore(MemoryConfig())
    fid = _fact(session, "books in USD")
    with database.SessionLocal() as s:
        store.update(s, fid, confidence=0.95)
        s.commit()
    assert _cols(fid)[0] == SCORED


def test_an_add_invalidates_its_scope_only_and_keeps_siblings_updated_at(session):
    store = MemoryStore(MemoryConfig())
    same = _fact(session, "prefers ACT/365 for this desk")
    archived = _fact(session, "old archived fact", status="archived")
    other = _fact(session, "book fact", scope_type="book", scope_id="7")
    with database.SessionLocal() as s:
        store.apply_diff(s, _Diff(add=[{"content": "prefers EUR reporting",
                                        "scope_type": "user", "confidence": 0.9}]),
                         WriteContext(allowed_scopes=["user"]))
        s.commit()
    cols, updated_at = _cols(same)
    assert set(cols.values()) == {None}
    assert updated_at == T0                      # D3: injection order must not move
    assert _cols(archived)[0] == SCORED          # archived facts are never re-scored
    assert _cols(other)[0] == SCORED             # other scope untouched


def test_load_existing_honours_the_limit(session):
    store = MemoryStore(MemoryConfig())
    for i in range(5):
        _fact(session, f"fact number {i}")
    with database.SessionLocal() as s:
        assert len(store.load_existing(s, "user", "desk", limit=3)) == 3
        assert len(store.load_existing(s, "user", "desk")) == 5
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_invalidation.py -q`
Expected: FAIL — scores survive the edit / `load_existing() got an unexpected keyword argument 'limit'`.

- [ ] **Step 3: Implement** in `memory/store.py`

Imports: add `from sqlalchemy import update`.

After `_normalize_source_error`:

```python
#: Invalidation = all five keep-alive columns NULL (spec §2). Clearing
#: attempted_at too puts the row at the front of the scoring rotation.
KEEP_ALIVE_RESET: dict[str, None] = {
    "keep_alive_score": None,
    "keep_alive_confidence": None,
    "keep_alive_scored_at": None,
    "keep_alive_attempted_at": None,
    "keep_alive_unscored_reason": None,
}
```

`load_existing`: signature `def load_existing(self, session, scope_type, scope_id, limit: int = 50) -> list[Fact]:` and `.limit(limit).all()`.

`_update_row`: record `content_changed = new_content != row.content` before assigning, and after `_normalize_source_error(row)`:

```python
        if content_changed:
            # A different fact now: its old keep-alive judgment no longer applies.
            for column, value in KEEP_ALIVE_RESET.items():
                setattr(row, column, value)
```

In `_apply_diff_inner`, just before `for scope_type, scope_id in touched: self._enforce_caps(...)`:

```python
        for scope_type, scope_id in touched:
            self._invalidate_keep_alive(session, scope_type, scope_id)
```

and add the method:

```python
    def _invalidate_keep_alive(self, session, scope_type, scope_id) -> None:
        """A new sibling can supersede an old fact (keep-alive level 0), so every
        live fact in the scope is re-scored. updated_at is pinned to itself: the
        column has onupdate=utcnow and load_injectable orders by it (D3)."""
        session.execute(
            update(MemoryEntry)
            .where(MemoryEntry.scope_type == scope_type,
                   MemoryEntry.scope_id == scope_id,
                   MemoryEntry.status != "archived")
            .values(**KEEP_ALIVE_RESET, updated_at=MemoryEntry.updated_at)
            .execution_options(synchronize_session="fetch")
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_invalidation.py tests/test_memory_apply_diff.py tests/test_memory_store_crud.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/memory/store.py tests/test_memory_keep_alive_invalidation.py
git commit -m "wip(jev-D): keep-alive invalidation + sibling limit"
```

### Task 23: Keep-alive scoring (`memory/keep_alive.py`)

**Files:**
- Create: `backend/app/services/deep_agent/memory/keep_alive.py`
- Modify: `tests/_system_one_fakes.py` (add `ScorePost`)
- Test: `tests/test_memory_keep_alive.py`

**Interfaces:**
- Consumes: `ask`, `Score`, `SystemOneUnavailable`, `cap`, `is_enabled` (Phase A); `MemoryStore.load_existing(..., limit=)` (Task 22); `is_memorable`.
- Produces: `KEEP_ALIVE_LEVELS: tuple[str, ...]` (4), `KEEP_ALIVE_QUESTION: Score`, `OUTAGE_REASONS`, `keep_alive_live(config, settings=None) -> bool`, `age_days(created_at, now) -> int`, `build_state(row, siblings, config, now) -> dict`, `score_pending(session_factory, store, config, *, post=None, settings=None, now=None) -> int` (never raises; returns the number scored).
- Produces (tests): `ScorePost(score=3.0, confidence=0.9)` with `.exc`, `.bad_for: set[str]` (fact contents that get a malformed reply), `.calls`.

- [ ] **Step 1: Add `ScorePost` to `tests/_system_one_fakes.py`**

```python
class ScorePost:
    """Answers the keep-alive `score` question with `score` / `confidence`.

    `.exc` raises instead; `.bad_for` holds fact contents that get a malformed
    reply (a per-row bad_response).
    """

    def __init__(self, score: float = 3.0, confidence: float = 0.9) -> None:
        self.score = score
        self.confidence = confidence
        self.exc: BaseException | None = None
        self.bad_for: set[str] = set()
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        if self.exc is not None:
            raise self.exc
        if payload["state"].get("fact") in self.bad_for:
            return {"answers": {}}
        levels = len(payload["questions"]["keep_alive"]["criteria"])
        probabilities = {str(i): 0.0 for i in range(levels)}
        probabilities[str(min(levels - 1, round(self.score)))] = 1.0
        return {
            "model": "typesafe/jev-1.13",
            "answers": {"keep_alive": {
                "type": "score", "score": self.score, "confidence": self.confidence,
                "probabilities": probabilities,
            }},
        }
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_memory_keep_alive.py
"""Keep-alive scoring (spec 2026-09-21 §2). DISPLAY-ONLY (D3)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _system_one_fakes import ScorePost
from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory import keep_alive
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.keep_alive import KEEP_ALIVE_LEVELS, age_days, score_pending
from app.services.deep_agent.memory.store import MemoryStore

NOW = datetime(2026, 9, 21, 12, 0, 0)


@pytest.fixture
def ka_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _fact(session, content, *, scope_type="user", scope_id="desk",
          created=NOW - timedelta(days=3), **kw):
    row = MemoryEntry(
        scope_type=scope_type, scope_id=scope_id, content=content,
        normalized_content=content.lower(), confidence=kw.pop("confidence", 0.9),
        status=kw.pop("status", "active"), created_by=kw.pop("created_by", "extractor"),
        pinned=kw.pop("pinned", False), meta={}, created_at=created,
        updated_at=kw.pop("updated_at", created), **kw)
    session.add(row)
    session.commit()
    return row.id


def _row(fact_id):
    with database.SessionLocal() as s:
        return s.get(MemoryEntry, fact_id)


def _score(post, cfg=None, now=NOW):
    cfg = cfg or MemoryConfig()
    store = MemoryStore(cfg)
    scored = score_pending(lambda: database.SessionLocal(), store, cfg, post=post, now=now)
    return scored, store


def test_scoring_populates_score_confidence_and_both_timestamps(ka_env, session):
    fid = _fact(session, "the desk reports in dollars")
    scored, _ = _score(ScorePost(score=2.0, confidence=0.7))
    row = _row(fid)
    assert scored == 1
    assert row.keep_alive_score == pytest.approx(2 / 3)       # normalized over 4 levels
    assert row.keep_alive_confidence == 0.7                   # Jev's own, never derived
    assert row.keep_alive_scored_at == NOW and row.keep_alive_attempted_at == NOW
    assert row.keep_alive_unscored_reason is None


def test_question_and_state_projection(ka_env, session):
    _fact(session, "desk prefers ACT/365", category="convention", created=NOW - timedelta(days=10))
    _fact(session, "s" * 300)
    post = ScorePost()
    _score(post, MemoryConfig(keep_alive_batch=1))
    question = post.calls[0]["questions"]["keep_alive"]
    assert question["type"] == "score" and question["criteria"] == list(KEEP_ALIVE_LEVELS)
    assert post.calls[0]["state"] == {
        "fact": "desk prefers ACT/365", "scope": "user:desk", "category": "convention",
        "source": "extractor", "status": "active", "pinned": False, "age_days": 10,
        "other_facts_in_scope": ["s" * 239 + "…"],
    }


@pytest.mark.parametrize("cfg_kw, master", [
    ({}, "false"),                          # master switch off
    ({"enabled": False}, "true"),           # OPEN_OTC_MEMORY off: no side door
    ({"keep_alive_enabled": False}, "true"),
])
def test_inert_unless_all_three_switches_are_on(session, monkeypatch, cfg_kw, master):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", master)
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    fid = _fact(session, "some fact")
    post = ScorePost()
    assert _score(post, MemoryConfig(**cfg_kw))[0] == 0
    assert post.calls == [] and _row(fid).keep_alive_attempted_at is None


def test_the_current_denylist_gates_the_fact_and_its_siblings(ka_env, session):
    bad = _fact(session, "api_key: abc123", created=NOW - timedelta(days=9))
    _fact(session, "desk prefers ACT/365", created=NOW - timedelta(days=5))
    post = ScorePost()
    _score(post)
    row = _row(bad)
    assert (row.keep_alive_unscored_reason, row.keep_alive_score) == ("denylist", None)
    assert row.keep_alive_attempted_at == NOW
    assert [c["state"]["fact"] for c in post.calls] == ["desk prefers ACT/365"]
    assert post.calls[0]["state"]["other_facts_in_scope"] == []


def test_age_days_boundary():
    assert age_days(NOW - timedelta(hours=23, minutes=59), NOW) == 0
    assert age_days(NOW - timedelta(hours=24), NOW) == 1
    assert age_days(NOW + timedelta(hours=1), NOW) == 0     # clock skew never goes negative


def test_a_failing_row_does_not_block_a_later_one(ka_env, session):
    first = _fact(session, "fact alpha", created=NOW - timedelta(days=9))
    later = _fact(session, "fact beta", created=NOW - timedelta(days=1))
    post = ScorePost()
    post.bad_for = {"fact alpha"}
    cfg = MemoryConfig(keep_alive_batch=1)
    _score(post, cfg)
    _score(post, cfg)
    assert _row(first).keep_alive_unscored_reason == "bad_response"
    assert _row(later).keep_alive_score is not None


@pytest.mark.parametrize("setup, reason, calls", [
    (lambda mp, post: mp.delenv("ZENMUX_API_KEY"), "no_key", 0),
    (lambda mp, post: setattr(post, "exc", TimeoutError("slow")), "timeout", 1),
    (lambda mp, post: setattr(post, "exc", RuntimeError("HTTP 503")), "http_error", 1),
])
def test_an_outage_ends_the_batch(ka_env, session, monkeypatch, setup, reason, calls):
    ids = [_fact(session, f"fact {name}", created=NOW - timedelta(days=d))
           for name, d in (("a", 3), ("b", 2), ("c", 1))]
    post = ScorePost()
    setup(monkeypatch, post)
    _, store = _score(post)
    assert len(post.calls) == calls
    assert [_row(i).keep_alive_unscored_reason for i in ids] == [reason, None, None]
    assert _row(ids[1]).keep_alive_attempted_at is None
    assert store.counters["keep_alive_failed"] == 1


def test_state_too_large_is_a_row_reason(ka_env, session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "1000")
    big = _fact(session, "x" * 1500, created=NOW - timedelta(days=3))
    ok1 = _fact(session, "fact one", created=NOW - timedelta(days=2))
    ok2 = _fact(session, "fact two", created=NOW - timedelta(days=1))
    post = ScorePost()
    _score(post)
    assert _row(big).keep_alive_unscored_reason == "state_too_large"
    assert _row(ok1).keep_alive_score is not None and _row(ok2).keep_alive_score is not None
    assert len(post.calls) == 2


def test_bad_response_is_a_row_reason(ka_env, session):
    a = _fact(session, "fact alpha", created=NOW - timedelta(days=2))
    b = _fact(session, "fact beta", created=NOW - timedelta(days=1))
    post = ScorePost()
    post.bad_for = {"fact alpha"}
    _score(post)
    assert _row(a).keep_alive_unscored_reason == "bad_response"
    assert _row(b).keep_alive_score is not None


def test_a_later_success_clears_the_reason(ka_env, session):
    fid = _fact(session, "fact alpha")
    post = ScorePost()
    post.exc = RuntimeError("HTTP 503")
    _score(post)
    assert _row(fid).keep_alive_unscored_reason == "http_error"
    post.exc = None
    _score(post, now=NOW + timedelta(minutes=1))
    row = _row(fid)
    assert row.keep_alive_unscored_reason is None and row.keep_alive_score is not None


def test_a_score_older_than_the_refresh_window_is_due_again(ka_env, session):
    _fact(session, "fact old", keep_alive_score=0.5, keep_alive_scored_at=NOW - timedelta(days=31))
    _fact(session, "fact fresh", keep_alive_score=0.5, keep_alive_scored_at=NOW - timedelta(days=29))
    post = ScorePost()
    _score(post)
    assert [c["state"]["fact"] for c in post.calls] == ["fact old"]


def test_archived_facts_are_never_scored(ka_env, session):
    _fact(session, "gone", status="archived")
    post = ScorePost()
    assert _score(post)[0] == 0 and post.calls == []


def test_an_exception_is_counted_and_never_raised(ka_env, session, monkeypatch):
    fid = _fact(session, "fact alpha")

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(keep_alive, "build_state", boom)
    _, store = _score(ScorePost())
    row = _row(fid)
    assert (row.keep_alive_unscored_reason, row.keep_alive_score) == ("internal_error", None)
    assert store.counters["keep_alive_failed"] == 1


def test_scoring_never_moves_updated_at_or_injection_order(ka_env, session):
    a = _fact(session, "fact alpha", confidence=0.9, created=NOW - timedelta(days=2))
    _fact(session, "fact beta", confidence=0.9, created=NOW - timedelta(days=1))
    store = MemoryStore(MemoryConfig())
    with database.SessionLocal() as s:
        before = [f.id for f in store.load_injectable(s, [("user", "desk")])]
    _score(ScorePost())
    with database.SessionLocal() as s:
        after = [f.id for f in store.load_injectable(s, [("user", "desk")])]
    assert after == before
    assert _row(a).updated_at == NOW - timedelta(days=2)


def test_eviction_order_ignores_keep_alive(ka_env, session):
    low = _fact(session, "fact low", confidence=0.75, keep_alive_score=1.0)
    high = _fact(session, "fact high", confidence=0.95, keep_alive_score=0.0)
    cfg = MemoryConfig(max_facts_per_scope=2)
    with database.SessionLocal() as s:
        MemoryStore(cfg).create(s, scope_type="user", scope_id="desk", content="fact new",
                                confidence=0.9, created_by="extractor")
        s.commit()
    assert _row(low).status == "archived"
    assert _row(high).status == "active"
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive.py -q`
Expected: FAIL — `ModuleNotFoundError` for `keep_alive`.

- [ ] **Step 4: Implement** `backend/app/services/deep_agent/memory/keep_alive.py`:

```python
"""Memory keep-alive score (spec 2026-09-21 §2).

System One's second opinion on whether a fact is still worth keeping, shown
beside the extractor's self-reported `confidence` for proposed and live facts.
DISPLAY-ONLY (D3): nothing here changes eviction order, injection order or
status — which is also why every write below pins `updated_at` to itself (the
column has onupdate=utcnow and `load_injectable` orders by it).

Runs only on the memory-writer daemon, never on a turn: after a job's
`apply_diff` has COMMITTED, and on each sweep tick to backfill.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, update

from app.config import Settings
from app.models import MemoryEntry

from ...system_one import Score, SystemOneUnavailable, ask, cap, is_enabled
from .config import MemoryConfig
from .safety import is_memorable
from .store import Fact, MemoryStore

logger = logging.getLogger(__name__)

#: Levels as SITUATIONS, not degrees (Jev's guidance), 0 = drop, 3 = keep.
KEEP_ALIVE_LEVELS: tuple[str, ...] = (
    "Contradicted or replaced by another listed fact, or plainly no longer true",
    "A one-off detail of a single conversation that goes stale quickly (a run id, "
    "today's number, a temporary state)",
    "True for now but likely to change within weeks (a current project, a temporary limit)",
    "A standing preference, rule or fact about how this desk works, true until "
    "someone changes it",
)
KEEP_ALIVE_QUESTION = Score(
    instructions=(
        "Which situation best describes the memory fact in `fact` today, judged "
        "against the other facts listed in `other_facts_in_scope` and the fact's "
        "age in days?"
    ),
    criteria=KEEP_ALIVE_LEVELS,
)
_QUESTION_KEY = "keep_alive"

#: The service is unreachable for EVERY row: the first one ends the batch (one
#: failed request per sweep tick, not ten). Other reasons are about THAT row.
OUTAGE_REASONS = frozenset({"no_key", "timeout", "http_error"})

SessionFactory = Callable[[], Any]


def keep_alive_live(config: MemoryConfig, settings: Settings | None = None) -> bool:
    """Live iff master, OPEN_OTC_MEMORY and OPEN_OTC_MEMORY_KEEP_ALIVE are all on.
    A desk that turned memory off has said its memory is not processed."""
    return config.enabled and config.keep_alive_enabled and is_enabled(settings)


def age_days(created_at: datetime, now: datetime) -> int:
    """Whole UTC days the fact has existed (created_at is naive UTC)."""
    return max(0, int((now - created_at).total_seconds() // 86400))


def build_state(
    row: MemoryEntry, siblings: list[Fact], config: MemoryConfig, now: datetime
) -> dict[str, Any]:
    return {
        "fact": row.content,
        "scope": f"{row.scope_type}:{row.scope_id}",
        "category": row.category,
        "source": row.created_by,
        "status": row.status,
        "pinned": bool(row.pinned),
        "age_days": age_days(row.created_at, now),
        # Siblings are what make level 0 ("replaced by another fact") answerable.
        "other_facts_in_scope": [cap(f.content, config.keep_alive_sibling_chars) for f in siblings],
    }


def _stamp(session, row_id: int, **values: Any) -> None:
    session.execute(
        update(MemoryEntry)
        .where(MemoryEntry.id == row_id)
        .values(**values, updated_at=MemoryEntry.updated_at)  # D3: never reorder
        .execution_options(synchronize_session=False)
    )


def _due(session, config: MemoryConfig, now: datetime) -> list[MemoryEntry]:
    stale_before = now - timedelta(days=config.keep_alive_refresh_days)
    return (
        session.query(MemoryEntry)
        .filter(
            MemoryEntry.status != "archived",
            or_(MemoryEntry.keep_alive_score.is_(None),
                MemoryEntry.keep_alive_scored_at < stale_before),
        )
        # Rotation: never-attempted first, then least-recently attempted, so a
        # permanently failing row cannot starve the rest.
        .order_by(
            MemoryEntry.keep_alive_attempted_at.is_not(None),
            MemoryEntry.keep_alive_attempted_at.asc(),
            MemoryEntry.updated_at.asc(),
            MemoryEntry.id.asc(),
        )
        .limit(config.keep_alive_batch)
        .all()
    )


def _score_one(session_factory: SessionFactory, store: MemoryStore, config: MemoryConfig,
               row_id: int, *, post: Any, settings: Settings | None, now: datetime) -> str:
    """Score one fact in its own small transaction. Returns "scored", "skipped",
    "denylist", "internal_error" or an UNAVAILABLE reason. Never raises."""
    with session_factory() as session:
        try:
            row = session.get(MemoryEntry, row_id)
            if row is None or row.status == "archived":
                return "skipped"
            # The CURRENT denylist, re-run now: backfill reaches rows admitted
            # under older rules or imported, so "it passed once" proves nothing.
            if not is_memorable(row.content, config.denylist)[0]:
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason="denylist")
                session.commit()
                return "denylist"
            siblings = [
                fact
                for fact in store.load_existing(session, row.scope_type, row.scope_id,
                                                limit=config.keep_alive_sibling_limit)
                if fact.id != row.id and is_memorable(fact.content, config.denylist)[0]
            ]
            state = build_state(row, siblings, config, now)
            try:
                result = ask(state, {_QUESTION_KEY: KEEP_ALIVE_QUESTION},
                             post=post, settings=settings)
            except SystemOneUnavailable as exc:
                store.counters["keep_alive_failed"] += 1
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason=exc.reason)
                session.commit()
                return exc.reason
            answer = result.answers[_QUESTION_KEY]
            _stamp(session, row_id,
                   keep_alive_score=answer.normalized,
                   keep_alive_confidence=answer.confidence,
                   keep_alive_scored_at=now,
                   keep_alive_attempted_at=now,
                   keep_alive_unscored_reason=None)
            session.commit()
            return "scored"
        except Exception:  # noqa: BLE001 — best-effort, isolated
            session.rollback()
            store.counters["keep_alive_failed"] += 1
            logger.warning("keep-alive: scoring memory fact %s failed", row_id, exc_info=True)
            try:
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason="internal_error")
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
            return "internal_error"


def score_pending(
    session_factory: SessionFactory,
    store: MemoryStore,
    config: MemoryConfig,
    *,
    post: Any = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Score up to `keep_alive_batch` due facts; return how many were scored.

    Due = non-archived and never scored, or scored more than
    `keep_alive_refresh_days` ago (the score judges staleness, so it goes stale).
    Inert unless keep_alive_live(). Never raises.
    """
    try:
        if not keep_alive_live(config, settings):
            return 0
        when = now or datetime.utcnow()
        with session_factory() as session:
            due = [row.id for row in _due(session, config, when)]
    except Exception:  # noqa: BLE001
        store.counters["keep_alive_failed"] += 1
        logger.warning("keep-alive: due-fact query failed", exc_info=True)
        return 0
    scored = 0
    for row_id in due:
        outcome = _score_one(session_factory, store, config, row_id,
                             post=post, settings=settings, now=when)
        if outcome == "scored":
            scored += 1
        elif outcome in OUTAGE_REASONS:
            logger.info("keep-alive: System One unreachable (%s); ending this batch", outcome)
            break
    return scored
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/memory/keep_alive.py tests/_system_one_fakes.py tests/test_memory_keep_alive.py
git commit -m "wip(jev-D): keep-alive scoring with rotation and outage breaker"
```

### Task 24: Run scoring on the memory writer, after commits

**Files:**
- Modify: `backend/app/services/deep_agent/memory/queue.py` (`process_one`, `_run_sweep`, new `_score_keep_alive`)
- Test: `tests/test_memory_keep_alive_queue.py`

**Interfaces:**
- Consumes: `keep_alive.score_pending(session_factory, store, config)` — looked up at call time, so tests can patch it.
- Produces: `MemoryWriteQueue._score_keep_alive() -> None` (never raises), called after a successful commit in `process_one` (only while accepting) and in `_run_sweep`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_keep_alive_queue.py
"""Scoring runs strictly AFTER a commit, never inside apply_diff's transaction,
never during the shutdown drain, and can never break the writer."""
from __future__ import annotations

import pytest

from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory import keep_alive
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.queue import MemoryWriteQueue, QueueJob
from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
from app.services.deep_agent.memory.store import MemoryStore


def _queue(monkeypatch):
    cfg = MemoryConfig()
    q = MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: [{"id": 1, "role": "user", "content": "I book in USD"}],
        extractor_llm=lambda p: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}',
        portfolio_resolver=lambda s, sid: None)
    monkeypatch.setattr(q, "_ensure_writer", lambda: None)   # no background thread in tests
    return q


def _job(sid):
    return QueueJob(RunSpec(run_key=session_run_key(sid), kind="session", session_id=sid,
                            thread_id=1, persona="trader", book_scope_id=None,
                            trigger_message_id=None), "normal")


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def fake_score(session_factory, store, config, **kw):
        with database.SessionLocal() as s:   # a FRESH session sees only committed rows
            calls.append(s.query(MemoryEntry).count())
        return 0

    monkeypatch.setattr(keep_alive, "score_pending", fake_score)
    return calls


def test_scoring_runs_after_the_jobs_commit(session, monkeypatch, seen):
    q = _queue(monkeypatch)
    q.enqueue(_job(7))
    assert q.process_one() is True
    assert seen == [1]


def test_a_crashed_job_is_not_followed_by_scoring(session, monkeypatch, seen):
    q = _queue(monkeypatch)

    def crash(session_, spec):
        raise RuntimeError("boom")

    monkeypatch.setattr(q, "run_job", crash)
    q.enqueue(_job(8))
    assert q.process_one() is True
    assert seen == []


def test_the_sweep_scores_after_it_commits(session, monkeypatch, seen):
    _queue(monkeypatch)._run_sweep()
    assert seen == [0]


def test_no_scoring_while_draining_at_shutdown(session, monkeypatch, seen):
    q = _queue(monkeypatch)
    q.enqueue(_job(9))
    q.flush(grace=5)
    with database.SessionLocal() as s:
        assert s.query(MemoryEntry).count() == 1   # the job still ran
    assert seen == []


def test_a_raising_scorer_never_breaks_the_writer(session, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("scorer bug")

    monkeypatch.setattr(keep_alive, "score_pending", broken)
    q = _queue(monkeypatch)
    q.enqueue(_job(10))
    assert q.process_one() is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_queue.py -q`
Expected: FAIL — `seen == []` where `[1]` / `[0]` was expected.

- [ ] **Step 3: Implement** in `memory/queue.py`

Replace `_run_sweep`:

```python
    def _run_sweep(self) -> None:
        committed = False
        with memory_write_session(self._session_factory,
                                  self.config.writer_busy_timeout_ms) as session:
            try:
                self.sweep(session)
                session.commit()
                committed = True
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.warning("memory sweep failed", exc_info=True)
        if committed:
            self._score_keep_alive()   # backfill tick
```

Replace `process_one`:

```python
    def process_one(self) -> bool:
        job = self._next_job()
        if job is None:
            return False
        committed = False
        with memory_write_session(self._session_factory,
                                  self.config.writer_busy_timeout_ms) as session:
            try:
                self.run_job(session, job.spec)
                session.commit()
                committed = True
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.warning("memory job crashed; left for sweep", exc_info=True)
        # Not while draining at shutdown: a scoring pass (up to ~10 Jev calls)
        # would blow the flush() grace budget.
        if committed and self._accepting:
            self._score_keep_alive()
        return True
```

Add:

```python
    def _score_keep_alive(self) -> None:
        """System One keep-alive scoring (spec 2026-09-21 §2), strictly AFTER a
        commit: never on a turn, never inside apply_diff's transaction, never
        able to roll it back or fail the writer."""
        try:
            from . import keep_alive

            keep_alive.score_pending(
                lambda: memory_write_session(self._session_factory,
                                             self.config.writer_busy_timeout_ms),
                self.store, self.config,
            )
        except Exception:  # noqa: BLE001 — best-effort, isolated
            logger.warning("memory keep-alive pass failed", exc_info=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_queue.py tests/test_memory_queue_runjob.py tests/test_memory_queue_writer.py tests/test_memory_queue_enqueue.py tests/test_memory_shutdown.py tests/test_memory_sweep_cutoff.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/memory/queue.py tests/test_memory_keep_alive_queue.py
git commit -m "wip(jev-D): score keep-alive on the writer after commits"
```

### Task 25: Serve keep-alive fields and show a `Keep` column

**Files:**
- Modify: `backend/app/routers/memory.py` (`FactOut`, `_out`), `frontend/src/types.ts` (`MemoryFact`), `frontend/src/routes/Memory.tsx`, `frontend/src/routes/Memory.test.tsx`, `frontend/src/routes/Memory.live.test.tsx`
- Test: `tests/test_memory_keep_alive_api.py`, `frontend/src/routes/Memory.test.tsx`

**Interfaces:**
- Produces: `FactOut.keep_alive_score/keep_alive_confidence/keep_alive_scored_at/keep_alive_unscored_reason`; TS `MemoryFact` gains the same four (`number | null` / `string | null`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_keep_alive_api.py
"""New fields asserted at the HTTP layer (pydantic response models drop unnamed keys)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import MemoryEntry


@pytest.fixture
def mem_client(session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.routers.memory import build_memory_router
    from app.services.deep_agent.memory.runtime import reset_memory_runtime

    reset_memory_runtime()
    app = FastAPI()
    app.include_router(build_memory_router())
    with TestClient(app) as c:
        yield c
    reset_memory_runtime()


def _entry(content, **kw):
    return MemoryEntry(scope_type="user", scope_id="desk", content=content,
                       normalized_content=content.lower(), confidence=0.9, status="active",
                       created_by="api", pinned=True, meta={}, **kw)


def test_facts_serve_the_keep_alive_fields(mem_client, session):
    session.add_all([
        _entry("books in dollars", keep_alive_score=0.67, keep_alive_confidence=0.8,
               keep_alive_scored_at=datetime(2026, 9, 21)),
        _entry("other fact", keep_alive_unscored_reason="no_key"),
    ])
    session.commit()
    items = {i["content"]: i for i in mem_client.get("/api/memory/facts").json()["items"]}
    scored = items["books in dollars"]
    assert (scored["keep_alive_score"], scored["keep_alive_confidence"]) == (0.67, 0.8)
    assert scored["keep_alive_scored_at"].startswith("2026-09-21")
    assert scored["keep_alive_unscored_reason"] is None
    broken = items["other fact"]
    assert (broken["keep_alive_score"], broken["keep_alive_unscored_reason"]) == (None, "no_key")
```

Append to `frontend/src/routes/Memory.test.tsx` (inside `describe('Memory presentational', …)`):

```tsx
  it('Keep column: 2dp score, and a dash that says why it is unscored', () => {
    render(
      <Memory
        {...base}
        facts={[
          f({ id: 1, keep_alive_score: 0.6667 }),
          f({ id: 2, content: 'd', keep_alive_unscored_reason: 'no_key' }),
        ]}
      />,
    );
    expect(screen.getByText('Keep')).toBeInTheDocument();
    expect(screen.getByText('0.67')).toBeInTheDocument();
    expect(screen.getByTitle('unscored: no_key')).toBeInTheDocument();
  });
```

In both fixture factories (`f` in `Memory.test.tsx`, `fact` in `Memory.live.test.tsx`) add before `...o` / `...over`:

```ts
  keep_alive_score: null, keep_alive_confidence: null,
  keep_alive_scored_at: null, keep_alive_unscored_reason: null,
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_memory_keep_alive_api.py -q` → FAIL (`KeyError: 'keep_alive_score'`).
Run: `(cd frontend && npx vitest run src/routes/Memory.test.tsx)` → FAIL (no `Keep` header).

- [ ] **Step 3: Implement**

`routers/memory.py` — add to `FactOut` after `updated_at`:

```python
    keep_alive_score: float | None = None
    keep_alive_confidence: float | None = None
    keep_alive_scored_at: Any = None
    keep_alive_unscored_reason: str | None = None
```

and in `_out`'s `FactOut(...)` call add:

```python
                   keep_alive_score=fact.keep_alive_score,
                   keep_alive_confidence=fact.keep_alive_confidence,
                   keep_alive_scored_at=fact.keep_alive_scored_at,
                   keep_alive_unscored_reason=fact.keep_alive_unscored_reason,
```

`frontend/src/types.ts` — add to `MemoryFact` after `updated_at`:

```ts
  /** System One keep-alive score in [0, 1]; null = never scored (display-only). */
  keep_alive_score: number | null;
  keep_alive_confidence: number | null;
  keep_alive_scored_at: string | null;
  /** Why the last attempt failed (e.g. no_key); null after a success. */
  keep_alive_unscored_reason: string | null;
```

`frontend/src/routes/Memory.tsx` — in `columns.push(...)`, directly after the `confidence` column:

```tsx
    {
      key: 'keep_alive',
      header: 'Keep',
      numeric: true,
      width: '4.5rem',
      render: (f) =>
        f.keep_alive_score != null ? (
          f.keep_alive_score.toFixed(2)
        ) : (
          <span
            title={f.keep_alive_unscored_reason ? `unscored: ${f.keep_alive_unscored_reason}` : 'not scored'}
          >
            —
          </span>
        ),
    },
```

(Fixed width, per the page's rule that non-clipping columns use fixed tracks.)

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_memory_keep_alive_api.py tests/test_memory_api.py -q
(cd frontend && npx vitest run src/routes/Memory.test.tsx src/routes/Memory.live.test.tsx && npx tsc --noEmit && echo tsc ok)
```

Expected: PASS, `tsc ok`. Check the Memory page in light, dark and compact density.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/memory.py tests/test_memory_keep_alive_api.py frontend/src/types.ts frontend/src/routes/Memory.tsx frontend/src/routes/Memory.test.tsx frontend/src/routes/Memory.live.test.tsx
git commit -m "wip(jev-D): serve keep-alive fields + Keep column"
```

### Task 26: Phase D docs and the phase commit

- [ ] **Step 1: `backend/app/services/system_one/CLAUDE.md`** — append:

```markdown
## Memory keep-alive (`deep_agent/memory/keep_alive.py`)

- One `score` question, four situation levels; `keep_alive_score` =
  `answer.normalized`. DISPLAY-ONLY (D3): pinned by tests that eviction and
  injection order do not move.
- **Every keep-alive write sets `updated_at = updated_at`.** `MemoryEntry.updated_at`
  has `onupdate=utcnow` and `load_injectable` orders by it — a plain UPDATE would
  silently reorder injection.
- Runs on the memory writer only: after a committed job (not while draining at
  shutdown) and on each sweep tick. Outage reasons end the batch; row reasons
  do not. Content edits and sibling adds null all five columns.
- Live iff `OPEN_OTC_SYSTEM_ONE` AND `OPEN_OTC_MEMORY` AND
  `OPEN_OTC_MEMORY_KEEP_ALIVE`.
```

- [ ] **Step 2: `backend/app/services/deep_agent/CLAUDE.md`** — in the Long-term memory "Configuration" table add:

```markdown
| `OPEN_OTC_MEMORY_KEEP_ALIVE` | `on` (default) / `off` — System One keep-alive score (display-only). Needs `OPEN_OTC_SYSTEM_ONE=true` and `OPEN_OTC_MEMORY` on. |
```

- [ ] **Step 3: `CHANGELOG.md`** under `### Added`:

```markdown
- **Memory keep-alive score** — with System One on, the memory writer asks Jev
  whether each fact is still worth keeping (given its scope siblings and age) and
  shows it in a `Keep` column beside `Conf` on the Memory page. Display-only: it
  changes no eviction, injection or status. Five nullable columns on
  `memory_entries` (migration `0062`); opt out with `OPEN_OTC_MEMORY_KEEP_ALIVE=off`.
```

- [ ] **Step 4: `README.md`** — Configuration table row:

```markdown
| `OPEN_OTC_MEMORY_KEEP_ALIVE` | `on` (default) \| `off`. With System One and memory on, sends each memory fact's text and up to 50 same-scope sibling facts (sanitized, denylisted facts excluded) to Jev for a display-only keep-alive score | No |
```

and `.env.example`: `# OPEN_OTC_MEMORY_KEEP_ALIVE=on`.

- [ ] **Step 5: Acceptance gate**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-D.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-D.log"
(cd frontend && npx tsc --noEmit && npx vitest run src/routes/Memory.test.tsx src/routes/Memory.live.test.tsx)
```

- [ ] **Step 6: Squash Phase D**

```bash
git add backend/app/services/system_one/CLAUDE.md backend/app/services/deep_agent/CLAUDE.md CHANGELOG.md README.md .env.example
git commit -m "wip(jev-D): docs"
BASE=$(git log --format=%H --grep='^wip(jev-D)' --reverse | head -1)
git reset --soft "${BASE}~1"
git commit -F - <<'MSG'
feat(system-one): display-only memory keep-alive score

The memory writer asks Jev, after each committed job and on each sweep
tick, whether a fact is still worth keeping. Five nullable columns
(migration 0062), rotation so a failing row cannot starve the rest, an
outage breaker, invalidation on edit and sibling add. updated_at is never
touched, so injection and eviction order cannot move. Keep column on the
Memory page.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -5
```

---

## Phase E — commit 5: confirmation family cross-check

Commits are `wip(jev-E): …`, squashed in Task 31. Remember the confirmations import rule: **no module-scope `app.tools` import anywhere in `services/confirmations/`** (`test_confirmations_tools.py::test_service_import_before_app_tools_has_no_import_cycle` pins it).

### Task 27: `extracted_trades.family_check`, migration `0063`, `TradeDraft.family_check`

**Files:**
- Modify: `backend/app/models.py` (`ExtractedTrade`), `backend/app/services/confirmations/llm.py` (`TradeDraft`), `backend/app/services/confirmations/service.py` (`_draft_to_row`)
- Create: `backend/alembic/versions/0063_extracted_trade_family_check.py`
- Test: `tests/test_migration_0063_family_check.py`

**Interfaces:**
- Produces: `ExtractedTrade.family_check: dict | None` (JSON, nullable; `NULL` = never checked); `TradeDraft.family_check: dict | None = None`; `_draft_to_row` copies it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_0063_family_check.py
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.services.confirmations.llm import TradeDraft
from app.services.confirmations.service import _draft_to_row


def _run(method, engine):
    module = importlib.import_module("backend.alembic.versions.0063_extracted_trade_family_check")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _pre_0063(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre63.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE confirmation_documents (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("""
            CREATE TABLE extracted_trades (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES confirmation_documents (id),
                family VARCHAR(80), status VARCHAR(20), validation_status VARCHAR(20)
            )"""))
        conn.execute(sa.text("CREATE INDEX ix_extracted_trades_status ON extracted_trades (status)"))
        conn.execute(sa.text("INSERT INTO confirmation_documents (id) VALUES (1)"))
        conn.execute(sa.text(
            "INSERT INTO extracted_trades VALUES (1, 1, 'SnowballOption', 'extracted', 'valid')"))
    return engine


def test_upgrade_adds_a_nullable_json_column(tmp_path):
    engine = _pre_0063(tmp_path)
    _run("upgrade", engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("extracted_trades")}
    assert cols["family_check"]["nullable"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT family_check FROM extracted_trades")).scalar() is None


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    from app.models import ExtractedTrade

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    ExtractedTrade.__table__.create(bind=engine)
    _run("upgrade", engine)
    assert "family_check" in {c["name"] for c in inspect(engine).get_columns("extracted_trades")}


def test_downgrade_keeps_rows_fk_and_index(tmp_path):
    engine = _pre_0063(tmp_path)
    _run("upgrade", engine)
    _run("downgrade", engine)
    insp = inspect(engine)
    assert "family_check" not in {c["name"] for c in insp.get_columns("extracted_trades")}
    assert [fk["referred_table"] for fk in insp.get_foreign_keys("extracted_trades")] == [
        "confirmation_documents"]
    assert "ix_extracted_trades_status" in {i["name"] for i in insp.get_indexes("extracted_trades")}
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM extracted_trades")).scalar() == 1


def test_the_draft_carries_the_check_onto_the_row():
    check = {"status": "agree", "reason": None, "jev_family": "SnowballOption",
             "confidence": 0.9, "top": [["SnowballOption", 0.9]], "model": "typesafe/jev-1.13"}
    row = _draft_to_row(1, 1, TradeDraft(family="SnowballOption", terms={}, family_check=check))
    assert row.family_check == check
    assert _draft_to_row(1, 2, TradeDraft(family="SnowballOption", terms={})).family_check is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migration_0063_family_check.py -q`
Expected: FAIL — migration missing; `TradeDraft() got an unexpected keyword argument 'family_check'`.

- [ ] **Step 3: Implement**

`models.py` — in `ExtractedTrade`, after `reject_reason`:

```python
    # System One family cross-check (spec 2026-09-21 §3): {status, reason,
    # jev_family, confidence, top, model}. NULL = never checked (feature off,
    # arena, or older row). Never read by validation or booking.
    family_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

`llm.py` — append to `TradeDraft`: `family_check: dict | None = None`.

`service.py` — `_draft_to_row`: add `family_check=draft.family_check,` to the `ExtractedTrade(...)` call.

`backend/alembic/versions/0063_extracted_trade_family_check.py`:

```python
"""extracted_trades.family_check — System One's cross-check of the LLM's family

Revision ID: 0063_extracted_trade_family_check
Revises: 0062_memory_keep_alive

`segment_document` picks each trade's product family with one vision LLM, and
that choice selects the schema stage 2 fills. This column records System One's
independent read of the same segment (spec 2026-09-21 §3). NULL = never checked.
It is a flag for the reviewer and never a gate.

IDEMPOTENT (0001 materialises today's ORM). Downgrade drops via
batch_alter_table (the table carries FKs). Migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0063_extracted_trade_family_check"
down_revision = "0062_memory_keep_alive"
branch_labels = None
depends_on = None

_TABLE = "extracted_trades"
_COLUMN = "family_check"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _TABLE in _tables() and _COLUMN not in _columns(_TABLE):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _TABLE in _tables() and _COLUMN in _columns(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migration_0063_family_check.py tests/test_migration_fresh_chain.py tests/test_confirmations_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/services/confirmations/llm.py backend/app/services/confirmations/service.py backend/alembic/versions/0063_extracted_trade_family_check.py tests/test_migration_0063_family_check.py
git commit -m "wip(jev-E): extracted_trades.family_check (migration 0063)"
```

### Task 28: `check_family`

**Files:**
- Create: `backend/app/services/confirmations/family_check.py`
- Modify: `tests/_system_one_fakes.py` (add `ChoicePost`)
- Test: `tests/test_confirmation_family_check.py`

**Interfaces:**
- Consumes: `ask`, `Choice`, `SystemOneUnavailable` (Phase A); `DocumentContent`/`PageContent`; `TradeSegment`.
- Produces: `DISAGREE_MIN = 0.5`, `UNKNOWN_OPTION = "unknown"`, `FAMILY_DESCRIPTIONS: dict[str, str]`, `FamilyCheck(status, reason=None, jev_family=None, confidence=None, top=None, model=None)` with `.as_json()`, `FamilyCheck.unscored(reason, *, model=None)`, `family_options(schema_families) -> dict[str, str]`, `segment_text(content, pages) -> str | None`, `decide(llm_family, choice, confidence, schema_families) -> tuple[str, str | None]`, `check_family(content, segment, *, schema_families, post=None, settings=None) -> FamilyCheck` (raises only `ValueError` for a reserved `unknown` family).
- Produces (tests): `ChoicePost(choice, confidence=0.9)`.

- [ ] **Step 1: Add `ChoicePost` to `tests/_system_one_fakes.py`**

```python
class ChoicePost:
    """Answers every `choice` question with `choice` at `confidence`; the
    remaining mass goes to the first other option."""

    def __init__(self, choice: str, confidence: float = 0.9) -> None:
        self.choice = choice
        self.confidence = confidence
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        answers = {}
        for key, question in payload["questions"].items():
            options = list(question["criteria"])
            probabilities = {option: 0.0 for option in options}
            probabilities[self.choice] = self.confidence
            others = [option for option in options if option != self.choice]
            if others:
                probabilities[others[0]] = round(1 - self.confidence, 2)
            answers[key] = {"type": "choice", "choice": self.choice,
                            "confidence": self.confidence, "probabilities": probabilities}
        return {"model": "typesafe/jev-1.13", "answers": answers}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_confirmation_family_check.py
"""Family cross-check (spec 2026-09-21 §3): every row of the status table."""
from __future__ import annotations

import pytest

from _system_one_fakes import ChoicePost, FakePost
from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.confirmations.family_check import (
    FAMILY_DESCRIPTIONS, FamilyCheck, check_family, decide, family_options, segment_text,
)
from app.services.confirmations.llm import TradeSegment
from app.tools.product_term_schema import _SCHEMA_FAMILIES

FAMILIES = frozenset(_SCHEMA_FAMILIES)
TEXT = PageContent(index=1, text="Autocallable note, knock-out 103%, knock-in 70%")
SEG = TradeSegment(family="SnowballOption", pages=[1], anchor="Autocallable note")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _doc(*pages):
    return DocumentContent(pages=list(pages), page_count=len(pages), extract_mode="text")


@pytest.mark.parametrize("llm, choice, confidence, expected", [
    ("SnowballOption", "SnowballOption", 0.2, ("agree", None)),
    ("unknown", "unknown", 0.9, ("agree", None)),
    ("MysteryOption", "unknown", 0.9, ("agree", None)),         # LLM also found no family
    ("SnowballOption", "unknown", 0.9, ("unscored", "jev_unknown")),
    ("SnowballOption", "PhoenixOption", 0.5, ("disagree", None)),
    ("SnowballOption", "PhoenixOption", 0.49, ("unscored", "low_confidence")),
])
def test_status_decision_table(llm, choice, confidence, expected):
    assert decide(llm, choice, confidence, FAMILIES) == expected


def test_unknown_is_a_reserved_option():
    assert "unknown" not in _SCHEMA_FAMILIES
    with pytest.raises(ValueError):
        family_options(FAMILIES | {"unknown"})


def test_descriptions_never_name_a_stale_family():
    assert set(FAMILY_DESCRIPTIONS) <= set(_SCHEMA_FAMILIES)


def test_a_new_family_falls_back_to_its_own_name():
    options = family_options(FAMILIES | {"BrandNewOption"})
    assert options["BrandNewOption"] == "BrandNewOption"
    assert list(options)[-1] == "unknown"


def test_text_layer_eligibility_uses_the_pipelines_own_classification():
    scan = PageContent(index=2, text="", image_png=b"png")
    assert segment_text(_doc(TEXT), [1]) == f"[page 1]\n{TEXT.text}"   # logo pages have image_png None
    assert segment_text(_doc(TEXT, scan), [1, 2]) is None
    assert segment_text(_doc(TEXT, scan), [1]) is not None             # the scan is outside the segment
    assert segment_text(_doc(TEXT, scan), []) is None                  # [] means every page


def test_pages_join_in_order_as_page_blocks():
    second = PageContent(index=2, text="second page")
    assert segment_text(_doc(TEXT, second), []) == f"[page 1]\n{TEXT.text}\n\n[page 2]\nsecond page"


def test_a_scan_page_is_unscored_without_a_call():
    post = ChoicePost("SnowballOption")
    scan = PageContent(index=1, text="", image_png=b"png")
    assert check_family(_doc(scan), SEG, schema_families=FAMILIES, post=post) == FamilyCheck(
        status="unscored", reason="no_text_layer")
    assert post.calls == []


def test_an_empty_text_layer_is_also_no_text_layer():
    blank = PageContent(index=1, text="")
    check = check_family(_doc(blank), SEG, schema_families=FAMILIES, post=ChoicePost("SnowballOption"))
    assert check.reason == "no_text_layer"


def test_agree_and_what_is_sent():
    post = ChoicePost("SnowballOption", confidence=0.9)
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=post)
    assert (check.status, check.jev_family, check.confidence, check.model) == (
        "agree", "SnowballOption", 0.9, "typesafe/jev-1.13")
    question = post.calls[0]["questions"]["family"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == sorted(FAMILIES) + ["unknown"]
    assert post.calls[0]["state"] == {"anchor": "Autocallable note",
                                      "document_text": f"[page 1]\n{TEXT.text}"}


def test_disagree_on_a_segment_the_llm_could_not_classify():
    seg = TradeSegment(family="unknown", pages=[1], anchor="x")
    check = check_family(_doc(TEXT), seg, schema_families=FAMILIES,
                         post=ChoicePost("SnowballOption", 0.9))
    assert check.status == "disagree"


def test_top_three_sorted_by_probability_then_name_rounded():
    probabilities = {option: 0.0 for option in sorted(FAMILIES) + ["unknown"]}
    probabilities.update({"SnowballOption": 0.6, "PhoenixOption": 0.2, "AsianOption": 0.2,
                          "BarrierOption": 0.004})
    body = {"model": "typesafe/jev-1.13", "answers": {"family": {
        "type": "choice", "choice": "SnowballOption", "confidence": 0.6,
        "probabilities": probabilities}}}
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=FakePost(body))
    assert check.top == [["SnowballOption", 0.6], ["AsianOption", 0.2], ["PhoenixOption", 0.2]]


def test_unavailable_records_the_reason_and_the_requested_model(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=ChoicePost("SnowballOption"))
    assert check.as_json() == {"status": "unscored", "reason": "no_key", "jev_family": None,
                               "confidence": None, "top": None, "model": "typesafe/jev-1.13"}
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_confirmation_family_check.py -q`
Expected: FAIL — `ModuleNotFoundError` for `family_check`.

- [ ] **Step 4: Implement** `backend/app/services/confirmations/family_check.py`:

```python
"""Confirmation family cross-check (spec 2026-09-21 §3).

`segment_document` picks each trade's product family with one vision LLM, and
that choice selects the schema stage 2 fills — a wrong family yields a
well-formed, wrong trade that a human then approves for an IRREVERSIBLE booking.
This asks System One the same question from the segment's TEXT LAYER and records
whether it agrees. A flag for the reviewer, never a gate: it touches no
validation status, no validation error, and no bookability.

No module-scope `app.tools` import here (see service.py): callers pass
`_SCHEMA_FAMILIES` in.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import asdict, dataclass
from typing import Any

from ...config import Settings, get_settings
from ..system_one import Choice, SystemOneUnavailable, ask
from .extract import DocumentContent
from .llm import TradeSegment

#: A different real family counts as disagreement only at this confidence.
DISAGREE_MIN = 0.5
#: Reserved option: "none of the listed families fits".
UNKNOWN_OPTION = "unknown"

#: One-line, region-neutral descriptions. A family missing here falls back to
#: its own name, so adding a family never breaks anything.
FAMILY_DESCRIPTIONS: dict[str, str] = {
    "EuropeanVanillaOption": "A plain call or put with one strike, exercisable only at expiry, with no barrier or path feature",
    "AmericanOption": "A call or put that may be exercised on any date up to expiry",
    "BarrierOption": "A call or put that knocks in or knocks out when the underlying touches a single barrier level",
    "AsianOption": "A call or put whose payoff uses an average of the underlying's prices over a set of fixing dates",
    "CashOrNothingDigitalOption": "Pays a fixed cash amount if the underlying finishes beyond the strike, and nothing otherwise",
    "SingleSharkfinOption": "A capped call or put that knocks out beyond one barrier level, often paying a rebate",
    "DoubleSharkfinOption": "A range structure with an upper and a lower knock-out barrier that pays while the underlying stays inside",
    "OneTouchOption": "Pays a fixed amount if the underlying touches a barrier level at any time before expiry",
    "DoubleOneTouchOption": "Pays a fixed amount if the underlying touches either an upper or a lower barrier before expiry",
    "SnowballOption": "An autocallable note: knock-out observations pay an accrued coupon and end the trade early, and a knock-in barrier exposes the principal",
    "KnockOutResetSnowballOption": "A snowball autocallable whose knock-out level steps down on later observation dates",
    "PhoenixOption": "An autocallable that pays periodic coupons while the underlying stays above a coupon barrier, with knock-out and knock-in levels",
    "RangeAccrualOption": "Accrues a coupon for each observation day the underlying stays inside a range",
    "Futures": "An exchange futures contract: an obligation to buy or sell the underlying at an agreed price on a future date",
    "SpotInstrument": "A cash or spot position in the underlying itself, with no option feature",
}

_QUESTION = (
    "Which product family is the trade described in `document_text` (the trade "
    "located by `anchor`)? Choose `unknown` if none of the listed families fits."
)


@dataclass(frozen=True)
class FamilyCheck:
    status: str                            # agree | disagree | unscored
    reason: str | None = None
    jev_family: str | None = None          # null when Jev was never reached
    confidence: float | None = None
    top: list[list[Any]] | None = None     # [[family, p], ...] p DESC, family ASC, 2 dp
    model: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def unscored(cls, reason: str, *, model: str | None = None) -> FamilyCheck:
        return cls(status="unscored", reason=reason, model=model)


def family_options(schema_families: Collection[str]) -> dict[str, str]:
    if UNKNOWN_OPTION in schema_families:
        raise ValueError(
            "'unknown' is a reserved cross-check option and cannot be a schema family")
    options = {family: FAMILY_DESCRIPTIONS.get(family, family) for family in sorted(schema_families)}
    options[UNKNOWN_OPTION] = "None of the listed product families fits this trade"
    return options


def segment_text(content: DocumentContent, pages: list[int]) -> str | None:
    """The segment's text layer, or None when ANY of its pages is a scan.

    Uses the pipeline's own classification: extract.py sets `image_png` only for
    a page under MIN_TEXT_CHARS_PER_PAGE, so a normal page with a logo is text.
    Jev has no vision, and partial text is never a basis for a guess. An empty
    `pages` list means every page (the same fallback `_content_parts` uses).
    """
    wanted = set(pages) if pages else None
    blocks: list[str] = []
    for page in content.pages:
        if wanted is not None and page.index not in wanted:
            continue
        if page.image_png is not None:
            return None
        if page.text:
            blocks.append(f"[page {page.index}]\n{page.text}")
    return "\n\n".join(blocks)


def decide(
    llm_family: str, choice: str, confidence: float, schema_families: Collection[str]
) -> tuple[str, str | None]:
    """The status decision table (spec §3)."""
    if choice == llm_family:
        return "agree", None
    if choice == UNKNOWN_OPTION:
        # "I can't tell" is not evidence against the LLM's real family.
        if llm_family in schema_families:
            return "unscored", "jev_unknown"
        return "agree", None
    if confidence >= DISAGREE_MIN:
        return "disagree", None
    return "unscored", "low_confidence"


def check_family(
    content: DocumentContent,
    segment: TradeSegment,
    *,
    schema_families: Collection[str],
    post: Any = None,
    settings: Settings | None = None,
) -> FamilyCheck:
    cfg = settings or get_settings()
    options = family_options(schema_families)
    text = segment_text(content, segment.pages)
    if not text:
        return FamilyCheck.unscored("no_text_layer")
    try:
        result = ask(
            {"anchor": segment.anchor, "document_text": text},
            {"family": Choice(instructions=_QUESTION, criteria=options)},
            post=post, settings=cfg,
        )
    except SystemOneUnavailable as exc:
        return FamilyCheck.unscored(exc.reason, model=cfg.system_one_model)
    answer = result.answers["family"]
    ranked = sorted(answer.probabilities.items(), key=lambda item: (-item[1], item[0]))[:3]
    status, reason = decide(segment.family, answer.choice, answer.confidence, schema_families)
    return FamilyCheck(
        status=status, reason=reason, jev_family=answer.choice,
        confidence=answer.confidence,
        top=[[family, round(p, 2)] for family, p in ranked],
        model=result.model,
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_confirmation_family_check.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/confirmations/family_check.py tests/_system_one_fakes.py tests/test_confirmation_family_check.py
git commit -m "wip(jev-E): family cross-check against the text layer"
```

### Task 29: Wire the check into `parse_document`, `run_parse_batch` and the agent tool

**Files:**
- Modify: `backend/app/services/confirmations/service.py`, `backend/app/tools/confirmations.py`
- Test: `tests/test_confirmations_family_check_service.py`

**Interfaces:**
- Consumes: `check_family`, `FamilyCheck` (Task 28); `TradeDraft.family_check` (Task 27).
- Produces: `parse_document(session, document, *, client, family_check: bool = True)`; `run_parse_batch(batch_id, task_id=None, *, client=None, family_check: bool = True)`; `service._family_checker(requested: bool) -> Callable[[content, segment, families], dict | None]`; `tools/confirmations._family_check_allowed(config) -> bool`; `_batch_out` trades gain `"family_check"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_confirmations_family_check_service.py
"""The cross-check rides on every segment and can never fail a document or
touch validation (spec §3)."""
from __future__ import annotations

import json

import pytest

from _system_one_fakes import ChoicePost
from app import database
from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade
from app.services.confirmations import service as svc
from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.system_one import client as s1client

VANILLA_TERMS = {"option_type": "call", "strike": 150.0, "maturity_years": 1.0,
                 "initial_price": 148.0}


@pytest.fixture(autouse=True)
def _aapl_is_bookable(registered_underlying):
    registered_underlying("AAPL")


@pytest.fixture(autouse=True)
def _text_document(monkeypatch):
    monkeypatch.setattr(svc, "extract_document", lambda path: DocumentContent(
        pages=[PageContent(index=1, text="BUY 100 AAPL call strike 150")],
        page_count=1, extract_mode="text"))


@pytest.fixture
def s1_on(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def jev(monkeypatch, choice, confidence=0.9):
    post = ChoicePost(choice, confidence)
    monkeypatch.setattr(s1client, "_default_post", post)
    return post


class FakeClient:
    def __init__(self, family):
        seg = json.dumps({"trades": [{"family": family, "pages": [1], "anchor": "BUY 100"}]})
        trade = json.dumps({
            "terms": VANILLA_TERMS, "underlying": "AAPL", "quantity": 100,
            "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
            "trade_date": "2026-08-01", "external_trade_id": "TC-1", "confidence": 0.9,
            "evidence": {}})
        self.responses = [seg, trade]
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


def parse(session, tmp_path, family="EuropeanVanillaOption", **kw):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    stored = tmp_path / f"c-{batch.id}.pdf"
    stored.write_bytes(b"%PDF-1.4")
    doc = ConfirmationDocument(batch_id=batch.id, filename=stored.name,
                               stored_path=str(stored), sha256="c" * 64, byte_len=8,
                               mime="application/pdf")
    session.add(doc)
    session.flush()
    svc.parse_document(session, doc, client=FakeClient(family), **kw)
    return doc, session.query(ExtractedTrade).filter_by(document_id=doc.id).all()


def test_agreement_is_recorded(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "EuropeanVanillaOption")
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert trade.family_check["status"] == "agree"
    assert trade.validation_status == "valid"


def test_a_disagreement_changes_nothing_about_validation(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "SnowballOption", 0.9)
    _doc, [flagged] = parse(session, tmp_path)
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "false")
    _doc, [plain] = parse(session, tmp_path)
    assert flagged.family_check["status"] == "disagree"
    assert flagged.family_check["jev_family"] == "SnowballOption"
    assert plain.family_check is None
    assert (flagged.validation_status, flagged.validation_errors) == (
        plain.validation_status, plain.validation_errors)


def test_an_unsupported_segment_is_still_checked(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "SnowballOption", 0.9)
    _doc, [trade] = parse(session, tmp_path, family="unknown")
    assert trade.validation_status == "unsupported"
    assert trade.family_check["status"] == "disagree"      # the most useful case


def test_a_check_failure_never_fails_the_document(s1_on, session, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(svc, "check_family", boom)
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert trade.family_check == {"status": "unscored", "reason": "internal_error",
                                  "jev_family": None, "confidence": None, "top": None,
                                  "model": None}


def test_no_key_is_visible_and_the_document_still_parses(s1_on, session, tmp_path, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert (trade.family_check["status"], trade.family_check["reason"]) == ("unscored", "no_key")


@pytest.mark.parametrize("setup, kwargs", [
    (lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"), {}),
    (lambda mp: mp.setenv("OPEN_OTC_CONFIRMATION_FAMILY_CHECK", "false"), {}),
    (lambda mp: None, {"family_check": False}),
])
def test_inert_paths_make_no_call_and_leave_null(s1_on, session, tmp_path, monkeypatch, setup, kwargs):
    post = jev(monkeypatch, "EuropeanVanillaOption")
    setup(monkeypatch)
    _doc, [trade] = parse(session, tmp_path, **kwargs)
    assert trade.family_check is None and post.calls == []


def test_run_parse_batch_passes_the_flag_through(session, tmp_path, monkeypatch):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    session.add(ConfirmationDocument(batch_id=batch.id, filename="c.pdf",
                                     stored_path=str(tmp_path / "c.pdf"), sha256="d" * 64,
                                     byte_len=1, mime="application/pdf"))
    session.commit()
    seen = []
    monkeypatch.setattr(svc, "parse_document",
                        lambda session_, document, *, client, family_check=True: seen.append(family_check))
    svc.run_parse_batch(batch.id, client=object(), family_check=False)
    svc.run_parse_batch(batch.id, client=object())
    assert seen == [False, True]


def test_the_tool_disables_the_check_on_arena_turns(session, settings, monkeypatch):
    import app.tools.confirmations as tools_confirmations
    from app.config import configure_settings
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    seen = []
    monkeypatch.setattr(tools_confirmations.confirmations, "parse_document",
                        lambda session_, document, *, client, family_check=True: seen.append(family_check))
    monkeypatch.setattr(tools_confirmations.confirmations_llm, "build_extractor_client",
                        lambda selection=None: object())
    configure_settings(settings)
    try:
        uploads = settings.artifact_dir / "uploads" / "chat"
        uploads.mkdir(parents=True, exist_ok=True)
        stored = uploads / "c.pdf"
        stored.write_bytes(b"%PDF-1.4 x")
        stamp = {"channel": "zenmux", "provider": "p", "model": "m"}
        tools_confirmations.parse_trade_confirmation.func(
            paths=[str(stored)], config={"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: stamp}})
        tools_confirmations.parse_trade_confirmation.func(
            paths=[str(stored)], config={"configurable": {}})
    finally:
        configure_settings(None)
    assert seen == [False, True]


def test_family_check_allowed_reads_presence_of_the_arena_stamp():
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import _family_check_allowed

    assert _family_check_allowed(None) is True
    assert _family_check_allowed({"configurable": None}) is True
    assert _family_check_allowed({"configurable": {}}) is True
    # Presence is enough — even a malformed stamp marks an arena turn.
    assert _family_check_allowed({"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: "x"}}) is False


def test_the_tool_payload_carries_the_check(session):
    from app.tools.confirmations import get_confirmation_batch

    batch = ConfirmationBatch(source="agent")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(batch_id=batch.id, filename="c.pdf", stored_path="/x",
                               sha256="e" * 64, byte_len=1, mime="application/pdf")
    session.add(doc)
    session.flush()
    session.add(ExtractedTrade(document_id=doc.id, seq=1, family="SnowballOption",
                               validation_status="valid",
                               family_check={"status": "disagree", "jev_family": "PhoenixOption"}))
    session.commit()
    trade = get_confirmation_batch.func(batch_id=batch.id)["documents"][0]["trades"][0]
    assert trade["family_check"]["jev_family"] == "PhoenixOption"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_confirmations_family_check_service.py -q`
Expected: FAIL — `family_check` is `None` everywhere / unexpected keyword `family_check`.

- [ ] **Step 3: Implement**

`service.py` — imports: add `import logging`, change `from dataclasses import dataclass` to `from dataclasses import dataclass, replace`, and add

```python
from ...config import get_settings
from .. import system_one
from .family_check import FamilyCheck, check_family
```

plus `logger = logging.getLogger(__name__)` below the imports. Add before `parse_document`:

```python
def _family_checker(requested: bool):
    """Per-segment System One cross-check (spec 2026-09-21 §3).

    Inert — every segment gets None, "never checked" — unless the caller allows
    it AND the master switch AND OPEN_OTC_CONFIRMATION_FAMILY_CHECK are on. A
    live check can never fail the document: any exception becomes
    unscored:internal_error with every Jev field null (logged, not stored).
    """
    settings = get_settings()
    if not (requested and system_one.is_enabled(settings)
            and settings.confirmation_family_check_enabled):
        return lambda content, segment, families: None

    def check(content, segment, families) -> dict:
        try:
            return check_family(content, segment, schema_families=families,
                                settings=settings).as_json()
        except Exception:  # noqa: BLE001 — a cross-check must never fail a document
            logger.warning("confirmation family cross-check failed", exc_info=True)
            return FamilyCheck.unscored("internal_error").as_json()

    return check
```

Change `parse_document`'s signature to `def parse_document(session: Session, document: ConfirmationDocument, *, client, family_check: bool = True) -> None:` and its draft loop to:

```python
        segments = segment_document(content, client)
        # Segment -> draft -> row is 1:1, so the check rides on the draft; if
        # stage 2 raises, the document fails exactly as before and the checks
        # are dropped with its rows.
        checker = _family_checker(family_check)
        drafts = []
        for seg in segments:
            checked = checker(content, seg, _SCHEMA_FAMILIES)
            if seg.family not in _SCHEMA_FAMILIES:
                # Still checked: "LLM said unknown, Jev reads SnowballOption at
                # 0.9" is a visible disagree on an unsupported row.
                drafts.append(TradeDraft(family=seg.family, terms={}, family_check=checked))
                continue
            drafts.append(replace(extract_trade(content, seg, client), family_check=checked))
```

`run_parse_batch`: signature `def run_parse_batch(batch_id: int, task_id: int | None = None, *, client=None, family_check: bool = True) -> None:` and the call becomes `parse_document(session, document, client=resolved_client, family_check=family_check)`.

`tools/confirmations.py` — add after `_extractor_override_from_config`:

```python
def _family_check_allowed(config: RunnableConfig | None) -> bool:
    """False on arena turns, which the server marks by stamping
    CONFIRMATION_EXTRACTOR_SELECTION_KEY. confirmation-desk-day contestants must
    never see a field the rest of the board's history did not. Presence — even
    a malformed stamp — is enough."""
    from ..services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    configurable = (config or {}).get("configurable") or {}
    return CONFIRMATION_EXTRACTOR_SELECTION_KEY not in configurable
```

in `parse_trade_confirmation` change the parse call to
`confirmations.parse_document(session, document, client=client, family_check=_family_check_allowed(config))`,
and in `_batch_out`'s trade dict add `"family_check": t.family_check,` after `"booked_position_id"`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_confirmations_family_check_service.py tests/test_confirmations_service.py tests/test_confirmations_tools.py tests/test_confirmations_api.py -q`
Expected: PASS (including `test_service_import_before_app_tools_has_no_import_cycle`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/confirmations/service.py backend/app/tools/confirmations.py tests/test_confirmations_family_check_service.py
git commit -m "wip(jev-E): run the family cross-check per segment (never on arena turns)"
```

### Task 30: Surface the check — approval card, REST, Confirmations page

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py` (`_summarize_book_extracted_trade`), `backend/app/schemas.py` (`ExtractedTradeOut`), `frontend/src/types.ts` (`ExtractedTrade`, new `FamilyCheck`), `frontend/src/routes/Confirmations.tsx`, `frontend/src/routes/Confirmations.test.tsx`, `frontend/src/routes/Confirmations.live.test.tsx`
- Test: `tests/test_confirmations_family_check_surfaces.py`, `frontend/src/routes/Confirmations.test.tsx`

**Interfaces:**
- Consumes: `ExtractedTrade.family_check` (Task 27).
- Produces: `ExtractedTradeOut.family_check: dict[str, Any] | None`; TS `FamilyCheck`, `ExtractedTrade.family_check: FamilyCheck | null`; the `book_extracted_trade` card names a disagreement.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_confirmations_family_check_surfaces.py
"""A disagreement must reach the human at the moment they approve an
IRREVERSIBLE booking, and be served over REST (spec §3)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade
from app.routers.confirmations import build_confirmations_router
from app.services.deep_agent.hitl import _summarize_book_extracted_trade

DISAGREE = {"status": "disagree", "reason": None, "jev_family": "PhoenixOption",
            "confidence": 0.82, "top": [["PhoenixOption", 0.82], ["SnowballOption", 0.18]],
            "model": "typesafe/jev-1.13"}


def _trade(session, family_check):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(batch_id=batch.id, filename="c.pdf", stored_path="/x",
                               sha256="f" * 64, byte_len=1, mime="application/pdf")
    session.add(doc)
    session.flush()
    trade = ExtractedTrade(document_id=doc.id, seq=1, family="SnowballOption",
                           underlying="600519.SH", quantity=100, validation_status="valid",
                           family_check=family_check)
    session.add(trade)
    session.commit()
    return batch, trade


def test_the_booking_card_names_a_disagreement(session):
    _batch, trade = _trade(session, DISAGREE)
    summary = _summarize_book_extracted_trade({"trade_id": trade.id})
    assert "FAMILY CHECK" in summary and "PhoenixOption" in summary and "0.82" in summary


@pytest.mark.parametrize("check", [None, {"status": "agree", "jev_family": "SnowballOption",
                                          "confidence": 0.9}])
def test_no_note_without_a_disagreement(session, check):
    _batch, trade = _trade(session, check)
    assert "FAMILY CHECK" not in _summarize_book_extracted_trade({"trade_id": trade.id})


def test_rest_serves_the_check(session):
    batch, _trade_row = _trade(session, DISAGREE)

    def get_db():
        with database.SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(build_confirmations_router(get_db=get_db))
    body = TestClient(app).get(f"/api/confirmations/{batch.id}").json()
    assert body["documents"][0]["trades"][0]["family_check"] == DISAGREE
```

Append to `frontend/src/routes/Confirmations.test.tsx` (inside the page's top-level `describe`):

```tsx
  it('shows the System One family check on the trade row', () => {
    const disagree = makeTrade({
      id: 20,
      family_check: {
        status: 'disagree', reason: null, jev_family: 'PhoenixOption', confidence: 0.82,
        top: [['PhoenixOption', 0.82]], model: 'typesafe/jev-1.13',
      },
    });
    const agree = makeTrade({
      id: 21,
      family_check: {
        status: 'agree', reason: null, jev_family: 'SnowballOption', confidence: 0.9,
        top: null, model: 'typesafe/jev-1.13',
      },
    });
    const unscored = makeTrade({
      id: 22,
      family_check: {
        status: 'unscored', reason: 'no_key', jev_family: null, confidence: null, top: null,
        model: 'typesafe/jev-1.13',
      },
    });
    const never = makeTrade({ id: 23 });
    const batch = makeBatch({
      documents: [makeDocument({ id: 9, trades: [disagree, agree, unscored, never] })],
    });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByText('family? PhoenixOption')).toBeInTheDocument();
    expect(screen.getByText('family ✓')).toBeInTheDocument();
    expect(screen.getByTitle('family check: no_key')).toBeInTheDocument();
    const neverRow = screen.getByTestId('confirmation-trade-23');
    expect(within(neverRow).queryByText(/family/)).not.toBeInTheDocument();
  });
```

and add `family_check: null,` to both `makeTrade` factories (`Confirmations.test.tsx`, `Confirmations.live.test.tsx`) before `...o`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_confirmations_family_check_surfaces.py -q` → FAIL (no "FAMILY CHECK"; `family_check` missing from the REST body).
Run: `(cd frontend && npx vitest run src/routes/Confirmations.test.tsx)` → FAIL.

- [ ] **Step 3: Implement**

`hitl.py` — in `_summarize_book_extracted_trade`, directly after the `VALIDATION` extra:

```python
            check = trade.family_check if isinstance(trade.family_check, dict) else None
            if check and check.get("status") == "disagree":
                # The human is about to approve an IRREVERSIBLE booking off the
                # extractor's family choice; System One read it differently.
                confidence = check.get("confidence")
                shown = f" ({confidence:.2f})" if isinstance(confidence, (int, float)) else ""
                extras.append(
                    f"FAMILY CHECK: System One reads {check.get('jev_family')}{shown} "
                    f"— review before approving"
                )
```

`schemas.py` — `ExtractedTradeOut`, after `reject_reason`: `family_check: dict[str, Any] | None = None`.

`frontend/src/types.ts` — above `ExtractedTrade`:

```ts
/** System One's independent read of a trade's product family (display-only). */
export type FamilyCheck = {
  status: 'agree' | 'disagree' | 'unscored';
  reason: string | null;
  jev_family: string | null;
  confidence: number | null;
  top: Array<[string, number]> | null;
  model: string | null;
};
```

and inside `ExtractedTrade`, after `reject_reason: string | null;`: `family_check: FamilyCheck | null;`.

`frontend/src/routes/Confirmations.tsx` — after `VALIDATION_VARIANT`:

```tsx
/** null = never checked (feature off / arena / older row): render nothing. */
function familyCheckBadge(check: ExtractedTrade['family_check']) {
  if (!check) return null;
  if (check.status === 'agree') return <Badge variant="pos">family ✓</Badge>;
  if (check.status === 'disagree') {
    const conf = check.confidence != null ? ` at ${check.confidence.toFixed(2)}` : '';
    return (
      <span title={`System One reads ${check.jev_family}${conf}. Review before booking.`}>
        <Badge variant="warn">family? {check.jev_family}</Badge>
      </span>
    );
  }
  return (
    <span title={`family check: ${check.reason ?? 'unscored'}`}>
      <Badge variant="ink">family unchecked</Badge>
    </span>
  );
}
```

and in the trade head, right after the validation badge:

```tsx
        {familyCheckBadge(trade.family_check)}
```

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_confirmations_family_check_surfaces.py tests/test_confirmations_api.py tests/test_hitl.py -q
(cd frontend && npx vitest run src/routes/Confirmations.test.tsx src/routes/Confirmations.live.test.tsx && npx tsc --noEmit && echo tsc ok)
```

Expected: PASS, `tsc ok`. Check the trade head in light, dark and compact density (the badges must not wrap the family name off the row).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py backend/app/schemas.py tests/test_confirmations_family_check_surfaces.py frontend/src/types.ts frontend/src/routes/Confirmations.tsx frontend/src/routes/Confirmations.test.tsx frontend/src/routes/Confirmations.live.test.tsx
git commit -m "wip(jev-E): family check on the booking card, REST and Confirmations page"
```

### Task 31: Phase E docs and the phase commit

- [ ] **Step 1: `backend/app/services/system_one/CLAUDE.md`** — append:

```markdown
## Confirmation family cross-check (`confirmations/family_check.py`)

- One `choice` over `sorted(_SCHEMA_FAMILIES) + ["unknown"]` from the segment's
  TEXT LAYER; any scan page in the segment ⇒ `unscored:no_text_layer` (Jev has no
  vision). `"unknown"` is reserved.
- A flag, never a gate: `validation_status`, `validation_errors` and bookability
  are untouched. Surfaces: the trade row badge, the tool payload, and the
  `book_extracted_trade` approval card.
- Off on arena turns (the server-stamped `CONFIRMATION_EXTRACTOR_SELECTION_KEY`
  marks them); a check failure can never fail a document.
```

- [ ] **Step 2: `backend/app/services/confirmations/CLAUDE.md`** — add to "Invariants":

```markdown
- **`family_check` is advisory.** System One's independent read of the family
  (`extracted_trades.family_check`, migration `0063`) is shown on the row and on
  the booking card; it never changes validation or bookability, and it is never
  computed on arena turns. See [`../system_one/CLAUDE.md`](../system_one/CLAUDE.md).
```

- [ ] **Step 3: `CHANGELOG.md`** under `### Added`:

```markdown
- **Confirmation family cross-check** — with System One on, each parsed trade
  segment's text layer is classified independently of the extractor LLM; a
  disagreement shows on the Confirmations row and on the `book_extracted_trade`
  approval card. Advisory only; never on arena turns. `extracted_trades.family_check`
  (migration `0063`); opt out with `OPEN_OTC_CONFIRMATION_FAMILY_CHECK=false`.
```

- [ ] **Step 4: `README.md`** — Configuration row:

```markdown
| `OPEN_OTC_CONFIRMATION_FAMILY_CHECK` | `true` (default) \| `false`. With System One on, sends the text layer of text-only confirmation segments (sanitized) to Jev to cross-check the extractor's product family; advisory only | No |
```

and `.env.example`: `# OPEN_OTC_CONFIRMATION_FAMILY_CHECK=true`.

- [ ] **Step 5: Acceptance gate**

```bash
.venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-E.log" 2>&1; echo "exit=$?"; tail -5 "${TMPDIR:-/tmp}/jev-E.log"
(cd frontend && npx tsc --noEmit && npx vitest run src/routes/Confirmations.test.tsx src/routes/Confirmations.live.test.tsx)
```

- [ ] **Step 6: Squash Phase E**

```bash
git add backend/app/services/system_one/CLAUDE.md backend/app/services/confirmations/CLAUDE.md CHANGELOG.md README.md .env.example
git commit -m "wip(jev-E): docs"
BASE=$(git log --format=%H --grep='^wip(jev-E)' --reverse | head -1)
git reset --soft "${BASE}~1"
git commit -F - <<'MSG'
feat(system-one): confirmation family cross-check

Each parsed segment's text layer is classified by Jev independently of the
extractor LLM; the result rides on the trade (extracted_trades.family_check,
migration 0063) and is shown on the Confirmations row and the
book_extracted_trade approval card. Advisory only: validation and
bookability are untouched, scans are never guessed, arena turns never
compute it, and a check failure never fails a document.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -6
```

---

## Final verification

### Task 32: Branch-level verification and handoff

- [ ] **Step 1: Five commits, in order, each a gate**

```bash
git log --oneline f1fd32d..HEAD
```

Expected: the spec commit, then exactly five `feat(system-one): …` commits — client, AUTO tool guard (shadow), enforce, keep-alive, family cross-check (plus a plan commit if you committed this file). No `wip(` subjects remain.

For each of the five commits, confirm the gate at that commit (not just the tip):

```bash
for sha in $(git log --format=%h --reverse --grep='^feat(system-one)' f1fd32d..HEAD); do
  git checkout -q "$sha"
  .venv/bin/python -m pytest -q > "${TMPDIR:-/tmp}/jev-gate-$sha.log" 2>&1; echo "$sha pytest exit=$?"
  (cd frontend && npx tsc --noEmit > /dev/null 2>&1; echo "$sha tsc exit=$?")
done
git checkout -q worktree-jev-system-one
```

Any failure not in the Task 0 baseline is a defect in that commit — fix it there (`git rebase -i` is unavailable: use `git checkout <sha>`, fix, `git commit --fixup`, and rebuild the later commits with `git cherry-pick`).

- [ ] **Step 2: Inertness check** — with the master switch at its default, a full suite run makes no System One call (the conftest guard would `pytest.fail`) and creates no verdict rows:

```bash
grep -c "reached the live System One POST" "${TMPDIR:-/tmp}/jev-E.log" || true   # expect 0
```

- [ ] **Step 3: Fresh chain and a scratch copy of the live DB**

```bash
.venv/bin/python -m pytest tests/test_migration_fresh_chain.py -q
cp data/open_otc.sqlite3 "${TMPDIR:-/tmp}/jev-live-copy.sqlite3" 2>/dev/null && \
  OPEN_OTC_DATABASE_URL="sqlite+pysqlite:///${TMPDIR:-/tmp}/jev-live-copy.sqlite3" \
  .venv/bin/python -m alembic upgrade head 2>&1 | grep -E "Running upgrade 006[0-3]|head"
```

Use `OPEN_OTC_DATABASE_URL` (NOT `DATABASE_URL`, which silently targets the live DB). The worktree may have no `data/` copy; if so, skip the second command — the migration tests already cover a real pre-state.

- [ ] **Step 4: Rollout note for the desk** (do not perform it; state it in the PR/merge description)

1. Merge with `OPEN_OTC_SYSTEM_ONE` unset — nothing changes anywhere.
2. Setting `OPEN_OTC_SYSTEM_ONE=true` turns all three features on (guard in `shadow`). Opt a data class out with `OPEN_OTC_TOOL_GUARD=off`, `OPEN_OTC_MEMORY_KEEP_ALIVE=off` or `OPEN_OTC_CONFIRMATION_FAMILY_CHECK=false`.
3. Read `GET /api/audit/guard-verdicts/summary` after real AUTO traffic; `flagged_then_ok` is the candidate-false-positive count. Tune `GUARD_POLICY`.
4. Only then consider `enforce` — after resolving the multi-call-resume limit.

- [ ] **Step 5: Finish** — use superpowers:finishing-a-development-branch.
