# Position Lifecycle Event Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every bookable product family a complete, reachable lifecycle event vocabulary — including the missing `exercise`/`expire`/`barrier_reset` types — and make the frontend read that vocabulary from the server instead of a hand-maintained copy.

**Architecture:** Extract the vocabulary out of `services/domains/positions.py` into a pure, DB-free data module, complete it for all 15 families, and guard it with two structural tests (every declared type reachable; every family covered) that fail on `main` today. `book_position` then emits the `open` event that makes the already-written `premium` cash rule fire, and one new read-only endpoint serves the vocabulary to the UI.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend, `.venv/bin/python -m pytest`), React 19 / Vite / TypeScript + vitest (frontend). No new dependencies. **No migration** — `event_type` is `String(80)`, not a DB enum.

**Spec:** `docs/superpowers/specs/2026-08-12-lifecycle-event-coverage-design.md`

## Global Constraints

- **No new dependency, no Alembic migration.** The vocabulary is enforced in Python only.
- **`quantark==0.3.0` stays pinned.** Never `pip install -e` a QuantArk working tree.
- **Never run the suite against the live DB.** `tests/conftest.py` pins `OPEN_OTC_ENV_FILE` empty; do not add a `.env` reader that bypasses `app.config.dotenv_path()`.
- **Never pipe pytest through `tail`** — it truncates the failure list.
- Backend tests run from the repo root: `.venv/bin/python -m pytest`.
- Frontend: `cd frontend && npm test`, type-check `npx tsc --noEmit`. The frontend vitest suite is **flaky under load**; compare a failing-file set against a same-machine `main` run before blaming this branch.
- Frontend styling is **token-only**. `--radius-1` and `--ink-3` are referenced by some page CSS but defined nowhere — verify every token against `frontend/src/tokens/`.
- `CHANGELOG.md` under `[Unreleased]` is required by the `pre-push` hook.

## File Structure

| File | Responsibility |
|---|---|
| **Create** `backend/app/services/domains/lifecycle_vocabulary.py` | The whole vocabulary: event types → target status, per-family allowlist, retired set, per-event field specs, and the two lookups. Pure data, no DB, no imports from the rest of `domains/`. Precedent: `services/term_structure.py`. |
| **Modify** `backend/app/services/domains/positions.py` | Drops the two dicts, re-exports from the new module for backward compatibility, and gains a non-committing `record_lifecycle_event` seam that `create_lifecycle_event` and `book_position` both use. |
| **Modify** `backend/app/services/domains/booking.py` | `book_position` emits the `open` event. Function-scope import to avoid a real cycle. |
| **Modify** `backend/app/services/settlement/derive.py` | `exercise` gains a settlement leg; the module comment's "14 legal event types" becomes 17. |
| **Modify** `backend/app/schemas.py` | `LifecycleFieldSpecOut` + `LifecycleVocabularyOut`. |
| **Modify** `backend/app/main.py` | `GET /api/lifecycle-vocabulary`, beside the existing lifecycle routes. |
| **Create** `tests/test_lifecycle_vocabulary.py` | The two structural guards plus the retirement behaviour. |
| **Modify** `frontend/src/types.ts` | `LifecycleVocabulary` type. |
| **Modify** `frontend/src/routes/Positions.live.tsx` | Fetches the vocabulary and passes it down. |
| **Modify** `frontend/src/routes/Positions.tsx` | Threads the prop through to the timeline. |
| **Modify** `frontend/src/components/PositionLifecycleTimeline.tsx` | Deletes `PRODUCT_EVENTS` and `EVENT_FIELDS`; drives off the prop. |

---

### Task 1: Extract the vocabulary module (pure refactor, no behaviour change)

Moving the maps first means every later task edits one small, focused file instead of a 1298-line service. This task must not change any behaviour — the existing suite is the proof.

**Files:**
- Create: `backend/app/services/domains/lifecycle_vocabulary.py`
- Modify: `backend/app/services/domains/positions.py:37-109`
- Test: `tests/test_lifecycle_vocabulary.py` (create)

**Interfaces:**
- Produces: `LIFECYCLE_EVENT_TARGETS: dict[str, str | None]`, `PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]]`, `valid_lifecycle_event_types(product_type: str) -> set[str]` — all importable from **both** `app.services.domains.lifecycle_vocabulary` and (re-exported) `app.services.domains.positions`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lifecycle_vocabulary.py`:

```python
"""The lifecycle event vocabulary: its shape, its guards, and its retirements."""
from __future__ import annotations

from app.services.domains import lifecycle_vocabulary as vocab
from app.services.domains import positions as positions_svc


def test_positions_still_re_exports_the_vocabulary():
    """positions.py is the historical import site; tests and the settlement
    deriver read the maps from there, so the move must not break them."""
    assert positions_svc.LIFECYCLE_EVENT_TARGETS is vocab.LIFECYCLE_EVENT_TARGETS
    assert positions_svc.PRODUCT_LIFECYCLE_EVENTS is vocab.PRODUCT_LIFECYCLE_EVENTS
    assert positions_svc.valid_lifecycle_event_types is vocab.valid_lifecycle_event_types


def test_vocabulary_module_imports_nothing_from_the_position_service():
    """The vocabulary is pure data. Keeping it free of service imports is what
    lets the router, the deriver and the tests read it without dragging in the
    DB layer."""
    import inspect

    source = inspect.getsource(vocab)
    assert "from .positions" not in source
    assert "import database" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lifecycle_vocabulary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.domains.lifecycle_vocabulary'`

- [ ] **Step 3: Create the module with the CURRENT vocabulary, moved verbatim**

Create `backend/app/services/domains/lifecycle_vocabulary.py`:

```python
"""The position lifecycle event vocabulary.

Deliberately DB-free: this module is pure data plus two lookups, so the REST
layer, the settlement deriver and the tests can all read the same tables
without importing the position service.

Two layers:

* ``LIFECYCLE_EVENT_TARGETS`` — every legal event type and the position status
  it drives. ``None`` means the event records something without moving status.
* ``PRODUCT_LIFECYCLE_EVENTS`` — which of those a given product family may
  actually record.

``create_lifecycle_event`` is the ONLY constructor of ``PositionLifecycleEvent``
and it rejects anything outside the family's allowlist, so the allowlist is
TOTAL: a family missing from this map does not degrade gracefully, it loses the
event outright, and the only symptom is a ValueError when a desk user tries to
record something real. ``tests/test_lifecycle_vocabulary.py`` guards that.
"""
from __future__ import annotations

LIFECYCLE_EVENT_TARGETS: dict[str, str | None] = {
    "open": "open",
    "close": "closed",
    "settle": "closed",
    "reopen": "open",
    "knock_in": "knocked_in",
    "knock_out": "closed",
    "coupon_observation": None,
    "coupon_paid": None,
    "maturity": "closed",
    "autocall": "closed",
    "coupon_lock": None,
    "memory_coupon": None,
    "fixing": None,
    "custom": None,
}

PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    "SnowballOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "coupon_observation",
        "coupon_paid",
        "maturity",
        "custom",
    },
    "PhoenixOption": {
        "close",
        "settle",
        "autocall",
        "coupon_lock",
        "coupon_paid",
        "memory_coupon",
        "maturity",
        "custom",
    },
    "BarrierOption": {"close", "settle", "knock_in", "knock_out", "maturity", "custom"},
    "SingleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "DoubleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "AsianOption": {"close", "settle", "fixing", "custom"},
}


def valid_lifecycle_event_types(product_type: str) -> set[str]:
    """Return lifecycle events allowed for a product type."""
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, {"close", "settle", "custom"})


__all__ = [
    "LIFECYCLE_EVENT_TARGETS",
    "PRODUCT_LIFECYCLE_EVENTS",
    "valid_lifecycle_event_types",
]
```

- [ ] **Step 4: Replace the definitions in `positions.py` with a re-export**

In `backend/app/services/domains/positions.py`, delete lines 37–52 (`LIFECYCLE_EVENT_TARGETS`), lines 54–91 (`PRODUCT_LIFECYCLE_EVENTS`) and the `valid_lifecycle_event_types` function at lines 107–109. Add this import beside the other `from .` imports near the top of the file (after `from app.services.settlement.generate import generate_for_event`):

```python
from .lifecycle_vocabulary import (
    LIFECYCLE_EVENT_TARGETS,
    PRODUCT_LIFECYCLE_EVENTS,
    valid_lifecycle_event_types,
)
```

Leave every other line of `positions.py` untouched — `_project_status_from_lifecycle` and `create_lifecycle_event` keep referring to the same names.

- [ ] **Step 5: Run the new test plus every existing consumer**

Run:
```bash
.venv/bin/python -m pytest tests/test_lifecycle_vocabulary.py tests/test_lifecycle_events.py \
  tests/test_asian_fixing_lifecycle.py tests/test_services_domains_positions.py \
  tests/test_settlement_derive.py tests/test_settlement_generate.py -v
```
Expected: PASS, all of them. A failure here means the move was not verbatim.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/lifecycle_vocabulary.py \
        backend/app/services/domains/positions.py \
        tests/test_lifecycle_vocabulary.py
git commit -m "refactor(lifecycle): extract the event vocabulary into its own module"
```

---

### Task 2: The structural guards, then the complete vocabulary

The guards come first and must fail — F1 survived for the whole life of the settlement module precisely because no test asked whether a declared event was reachable.

**Files:**
- Modify: `tests/test_lifecycle_vocabulary.py`
- Modify: `backend/app/services/domains/lifecycle_vocabulary.py`

**Interfaces:**
- Consumes: Task 1's module.
- Produces: `RETIRED_EVENT_TYPES: frozenset[str]`, `BASE_EVENTS: frozenset[str]`, and a `PRODUCT_LIFECYCLE_EVENTS` covering all 15 families with 17 declared types.

- [ ] **Step 1: Write the failing guards**

Append to `tests/test_lifecycle_vocabulary.py`:

```python
def test_every_declared_event_type_is_reachable_or_explicitly_retired():
    """A type nobody can record is a silent capability deletion, not a warning.

    This is the test that would have caught the dead `open`/`reopen` types and
    the `premium` cash leg that could never fire. The RETIRED escape hatch is
    what keeps a deliberate retirement distinguishable from an accidental
    orphan: it costs one line, and that line is the decision record.
    """
    reachable = set().union(*vocab.PRODUCT_LIFECYCLE_EVENTS.values())
    orphans = set(vocab.LIFECYCLE_EVENT_TARGETS) - reachable - vocab.RETIRED_EVENT_TYPES
    assert orphans == set(), f"declared but unreachable: {sorted(orphans)}"


def test_every_cash_leg_rule_is_reachable_or_explicitly_retired():
    """A cash rule keyed on an unreachable event books money nobody can trigger."""
    from app.services.settlement.derive import CASH_LEG_RULES

    reachable = set().union(*vocab.PRODUCT_LIFECYCLE_EVENTS.values())
    orphans = set(CASH_LEG_RULES) - reachable - vocab.RETIRED_EVENT_TYPES
    assert orphans == set(), f"cash rules that can never fire: {sorted(orphans)}"


def test_every_bookable_family_has_an_explicit_event_map():
    """Falling through to a default is how KnockOutResetSnowballOption ended up
    a snowball with no knock-in, knock-out or coupon events."""
    from app.services.domains.product_builders import _REGISTRY

    missing = set(_REGISTRY) - set(vocab.PRODUCT_LIFECYCLE_EVENTS)
    assert missing == set(), f"families with no event map: {sorted(missing)}"


def test_retired_types_keep_their_target_status_and_cash_rule():
    """Retired means no NEW events, not that the system forgets what the type
    meant. `_project_status_from_lifecycle` resolves targets with `.get()`, so
    deleting `autocall` would make a historical phoenix autocall read as
    non-transitioning and silently stop projecting the position to `closed`.
    """
    from app.services.settlement.derive import CASH_LEG_RULES

    assert vocab.RETIRED_EVENT_TYPES == frozenset({"autocall", "coupon_lock"})
    assert vocab.LIFECYCLE_EVENT_TARGETS["autocall"] == "closed"
    assert vocab.LIFECYCLE_EVENT_TARGETS["coupon_lock"] is None
    assert "autocall" in CASH_LEG_RULES
    for retired in vocab.RETIRED_EVENT_TYPES:
        for family, allowed in vocab.PRODUCT_LIFECYCLE_EVENTS.items():
            assert retired not in allowed, f"{family} may still record {retired}"


def test_every_family_can_record_the_universal_base_events():
    assert vocab.BASE_EVENTS == frozenset(
        {"open", "reopen", "close", "settle", "maturity", "custom"}
    )
    for family, allowed in vocab.PRODUCT_LIFECYCLE_EVENTS.items():
        assert vocab.BASE_EVENTS <= allowed, f"{family} is missing base events"


def test_one_touch_models_the_touch_as_a_terminating_knock_out():
    """The touch both ends the option and creates the obligation. `knock_in`
    targets `knocked_in` and has no cash rule at all, which would leave the
    money invisible until maturity."""
    for family in ("OneTouchOption", "DoubleOneTouchOption"):
        allowed = vocab.PRODUCT_LIFECYCLE_EVENTS[family]
        assert "knock_out" in allowed
        assert "knock_in" not in allowed


def test_ko_reset_snowball_is_a_snowball_plus_its_stepping_barrier():
    snowball = vocab.PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    ko_reset = vocab.PRODUCT_LIFECYCLE_EVENTS["KnockOutResetSnowballOption"]
    assert ko_reset == snowball | {"barrier_reset"}


def test_barrier_reset_and_expire_do_not_move_status_or_book_cash():
    from app.services.settlement.derive import CASH_LEG_RULES

    assert vocab.LIFECYCLE_EVENT_TARGETS["barrier_reset"] is None
    assert vocab.LIFECYCLE_EVENT_TARGETS["expire"] == "closed"
    assert "barrier_reset" not in CASH_LEG_RULES
    assert "expire" not in CASH_LEG_RULES
```

- [ ] **Step 2: Run the guards to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lifecycle_vocabulary.py -v`
Expected: FAIL. `test_every_declared_event_type_is_reachable_or_explicitly_retired` fails first with `AttributeError: module ... has no attribute 'RETIRED_EVENT_TYPES'`; once that exists it reports `declared but unreachable: ['open', 'reopen']`, and the cash guard reports `['open']`.

- [ ] **Step 3: Replace the vocabulary with the complete one**

In `backend/app/services/domains/lifecycle_vocabulary.py`, replace `LIFECYCLE_EVENT_TARGETS`, `PRODUCT_LIFECYCLE_EVENTS` and `valid_lifecycle_event_types` with:

```python
LIFECYCLE_EVENT_TARGETS: dict[str, str | None] = {
    # Inception and correction.
    "open": "open",
    "reopen": "open",
    # Terminations. `maturity`, `exercise` and `expire` are all FINAL states;
    # the desk records the one that describes what actually happened.
    "close": "closed",
    "settle": "closed",
    "maturity": "closed",
    "exercise": "closed",
    "expire": "closed",
    "knock_out": "closed",
    # Alive-but-changed.
    "knock_in": "knocked_in",
    # Observations and records — these move no status.
    "coupon_observation": None,
    "coupon_paid": None,
    "memory_coupon": None,
    "barrier_reset": None,
    "fixing": None,
    "custom": None,
    # Retired (see RETIRED_EVENT_TYPES). Kept so historical rows still resolve.
    "autocall": "closed",
    "coupon_lock": None,
}

RETIRED_EVENT_TYPES: frozenset[str] = frozenset({"autocall", "coupon_lock"})
"""Types no family may record any more, kept so history still resolves.

A phoenix autocall IS a knock-out and a coupon-lock IS a coupon observation, so
phoenix and snowball now share one vocabulary. Deleting the old spellings would
damage history twice over: `_project_status_from_lifecycle` resolves targets
with `.get()`, so an unknown type reads as *non-transitioning* rather than
*unrecognised* and a historical autocall would stop closing its position; and
dropping `CASH_LEG_RULES["autocall"]` would stop `generate_missing` ever
sweeping those events for cash.
"""

BASE_EVENTS: frozenset[str] = frozenset(
    {"open", "reopen", "close", "settle", "maturity", "custom"}
)
"""Every family can be opened, corrected, closed, settled and matured."""

_FAMILY_EXTRA_EVENTS: dict[str, frozenset[str]] = {
    "EuropeanVanillaOption": frozenset({"exercise", "expire"}),
    "AmericanOption": frozenset({"exercise", "expire"}),
    "CashOrNothingDigitalOption": frozenset({"exercise", "expire"}),
    "BarrierOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    "SingleSharkfinOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    "DoubleSharkfinOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    # The touch terminates the option AND creates the obligation, so it is a
    # knock_out (terminating, carries the settlement leg), never a knock_in
    # (leaves the position alive, has no cash rule). A no-touch is `expire`.
    "OneTouchOption": frozenset({"knock_out", "expire"}),
    "DoubleOneTouchOption": frozenset({"knock_out", "expire"}),
    "AsianOption": frozenset({"fixing", "exercise", "expire"}),
    "SnowballOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid"}
    ),
    # A snowball plus the stepping barrier that distinguishes it. NOTE:
    # `barrier_reset` RECORDS the step; it does not move the barrier the
    # position is priced against (that lives in Position.product_kwargs).
    "KnockOutResetSnowballOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid", "barrier_reset"}
    ),
    "PhoenixOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid", "memory_coupon"}
    ),
    # Assumes range accruals pay once at maturity; `fixing` records the
    # in/out-of-range observations. A periodic payer would also need
    # `coupon_paid`.
    "RangeAccrualOption": frozenset({"fixing"}),
    # Futures always settle something, so no `expire`: maturity + settle.
    "Futures": frozenset(),
    "SpotInstrument": frozenset(),
}

PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    family: set(BASE_EVENTS | extra) for family, extra in _FAMILY_EXTRA_EVENTS.items()
}


def valid_lifecycle_event_types(product_type: str) -> set[str]:
    """Return lifecycle events allowed for a product type.

    Every bookable family has an explicit entry (guarded by
    ``test_every_bookable_family_has_an_explicit_event_map``); the fallback
    only ever applies to an unrecognised product type.
    """
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, set(BASE_EVENTS))
```

Extend `__all__` with `"RETIRED_EVENT_TYPES"` and `"BASE_EVENTS"`.

- [ ] **Step 4: Run the guards to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lifecycle_vocabulary.py -v`
Expected: PASS — 11 tests. `test_every_cash_leg_rule_is_reachable_or_explicitly_retired` passes because `open` is now reachable and `autocall` is retired.

- [ ] **Step 5: Run every existing lifecycle consumer**

Run:
```bash
.venv/bin/python -m pytest tests/test_lifecycle_events.py tests/test_asian_fixing_lifecycle.py \
  tests/test_services_domains_positions.py tests/test_settlement_derive.py \
  tests/test_settlement_generate.py -v
```
Expected: PASS. `test_non_asian_does_not_get_fixing` still passes (vanillas get no `fixing`). If `tests/test_settlement_derive.py:90` fails, it is asserting `set(CASH_LEG_RULES) - set(LIFECYCLE_EVENT_TARGETS)` is empty — still true, since no cash rule was added yet.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/lifecycle_vocabulary.py tests/test_lifecycle_vocabulary.py
git commit -m "feat(lifecycle): complete the event vocabulary for all 15 families

Adds exercise/expire/barrier_reset, gives every family an explicit map, and
retires autocall/coupon_lock in favour of knock_out/coupon_observation.
Guarded by reachability and family-coverage tests that fail on main."
```

---

### Task 3: Settlement — `exercise` books cash, `expire` and `barrier_reset` do not

**Files:**
- Modify: `backend/app/services/settlement/derive.py:182-190` (comment), `:230-245` (`CASH_LEG_RULES`)
- Test: `tests/test_settlement_derive.py`

**Interfaces:**
- Consumes: Task 2's `RETIRED_EVENT_TYPES`.
- Produces: `CASH_LEG_RULES["exercise"] == (_SETTLEMENT_LEG,)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settlement_derive.py`:

```python
def test_exercise_books_the_settlement_leg():
    drafts = derive_cashflows(
        _position(),
        _event("exercise", {"settlement_amount": 1250.0, "settlement_date": "2026-09-01"}),
    )
    assert [d.leg_key for d in drafts] == ["settlement"]
    assert drafts[0].amount == 1250.0


def test_expire_books_nothing_because_nothing_is_owed():
    """The whole point of a distinct `expire`: a terminating event that emitted
    a settlement leg with no amount would leave a row permanently
    `needs_amount` — a phantom obligation nobody can ever clear."""
    assert derive_cashflows(_position(), _event("expire", {"expiry_date": "2026-09-01"})) == []


def test_barrier_reset_books_nothing():
    """A barrier stepping is a change of terms, not a cash movement."""
    drafts = derive_cashflows(
        _position(),
        _event("barrier_reset", {"reset_date": "2026-09-01", "new_barrier_level": 98.0}),
    )
    assert drafts == []


def test_a_one_touch_knock_out_uses_the_same_singleton_settlement_leg():
    """So a touch followed by a settle fills one row rather than booking twice."""
    drafts = derive_cashflows(
        _position(), _event("knock_out", {"settlement_amount": 500.0})
    )
    assert [d.leg_key for d in drafts] == ["settlement"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_derive.py -k "exercise or expire or barrier_reset" -v`
Expected: FAIL — `test_exercise_books_the_settlement_leg` gets `[]` because `exercise` has no rule.

- [ ] **Step 3: Add the rule and correct the module comment**

In `backend/app/services/settlement/derive.py`, add one entry to `CASH_LEG_RULES` in the "Terminations" block, immediately after `"close": (_SETTLEMENT_LEG,),`:

```python
    "exercise": (_SETTLEMENT_LEG,),
```

Then update the comment block. Change line 182 from `# The 14 legal event types live in ``LIFECYCLE_EVENT_TARGETS``` to:

```python
# The 17 legal event types live in ``LIFECYCLE_EVENT_TARGETS``
```

and replace the closing "Non-cash types" paragraph (the two lines beginning `# Non-cash types (reopen, knock_in, ...`) with:

```python
# Non-cash types (reopen, knock_in, coupon_observation, fixing, barrier_reset,
# expire, custom) are absent on purpose and therefore emit nothing. `expire`
# because nothing is owed — a settlement leg with no amount would sit
# `needs_amount` forever; `barrier_reset` because a stepping barrier is a
# change of terms, not cash.
#
# `autocall` and `coupon_lock` are RETIRED (see
# lifecycle_vocabulary.RETIRED_EVENT_TYPES): no family may record them any
# more, but `autocall` keeps its rule below so ``generate_missing`` can still
# sweep historical phoenix autocalls for cash.
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_settlement_derive.py tests/test_lifecycle_vocabulary.py -v`
Expected: PASS. The reachability guard still passes — `exercise` is reachable via six families.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/settlement/derive.py tests/test_settlement_derive.py
git commit -m "feat(settlement): exercise books a settlement leg; expire and barrier_reset book none"
```

---

### Task 4: `book_position` emits `open`, so the premium leg finally fires

**Files:**
- Modify: `backend/app/services/domains/positions.py:583-655` (extract a non-committing seam)
- Modify: `backend/app/services/domains/booking.py:296-312`
- Test: `tests/test_settlement_generate.py`

**Interfaces:**
- Consumes: `generate_for_event(sess, event=..., actor=...)` from `app.services.settlement.generate`.
- Produces: `positions.record_lifecycle_event(sess, *, portfolio, position, event_type, event_data, actor) -> PositionLifecycleEvent` — creates the event, applies the status transition, generates cashflows and records the audit row, **without committing**. `create_lifecycle_event` and `book_position` both call it.

**Why a new seam:** `create_lifecycle_event` ends in `sess.commit()`. Calling it from inside `book_position` would commit the caller's in-flight booking transaction. Extracting the non-committing core is the only way both paths can share one implementation.

**Import direction matters:** `positions.py` → `position_adapter.py` → `booking.py`, so `booking.py` importing `positions.py` at module scope is a real cycle. Use a **function-scope import**, as `services/confirmations/` already does for the same reason.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settlement_generate.py`:

```python
def test_open_is_reachable_through_the_real_constructor(session, book):
    """The premium leg was dead for the life of this module because no product
    could record `open` — and the unit tests missed it by building the ORM
    object directly, proving satisfiability rather than reachability. This test
    goes through the gate."""
    from app.services.domains import positions as positions_svc

    _, position = book
    position.entry_price = 2.5
    position.quantity = 100.0
    session.flush()

    update = positions_svc.create_lifecycle_event(
        position_id=position.id,
        event_type="open",
        event_data={"source": "test"},
        session=session,
    )

    assert update.event.event_type == "open"
    row = session.query(SettlementCashflow).filter_by(leg_key="premium").one()
    assert row.amount == 250.0
    assert row.status == "pending"


def _vanilla_booking(portfolio_id: int, *, entry_price: float):
    """A minimal bookable vanilla. `ProductBookingSpec` is a `ProductSpec`:
    asset_class / product_family / quantark_class / underlying / currency /
    terms — there is no `family=` shorthand."""
    from app.services.domains.booking import BookingRequest, ProductBookingSpec

    return BookingRequest(
        portfolio_id=portfolio_id,
        product=ProductBookingSpec(
            asset_class="equity",
            product_family="vanilla",
            quantark_class="EuropeanVanillaOption",
            underlying="AAPL",
            currency="USD",
            terms={
                "strike": 100.0,
                "maturity_years": 1.0,
                "option_type": "CALL",
                "initial_price": 100.0,
            },
        ),
        quantity=10.0,
        entry_price=entry_price,
        engine_name="BlackScholesEngine",
    )


def test_booking_a_position_emits_open_and_its_premium(session, registered_underlying):
    """Every booking path funnels through book_position, so the cash lifecycle
    is covered from inception rather than from termination."""
    from app.services.domains.booking import book_position

    registered_underlying("AAPL")
    portfolio = Portfolio(name="Booking Emits Open")
    session.add(portfolio)
    session.flush()

    position = book_position(session, _vanilla_booking(portfolio.id, entry_price=3.0))
    session.flush()

    events = (
        session.query(PositionLifecycleEvent)
        .filter_by(position_id=position.id)
        .all()
    )
    assert [e.event_type for e in events] == ["open"]

    row = session.query(SettlementCashflow).filter_by(position_id=position.id).one()
    assert row.leg_key == "premium"
    assert row.amount == 30.0
    assert row.direction == "pay"


def test_booking_at_zero_entry_price_yields_needs_amount_not_a_zero_row(
    session, registered_underlying
):
    """`BookingRequest.entry_price` defaults to 0.0, so this is the common case,
    not an edge one. `needs_amount` honestly says 'premium owed, amount not
    recorded'; a zero row would claim the trade was free."""
    from app.services.domains.booking import book_position

    registered_underlying("AAPL")
    portfolio = Portfolio(name="No Entry Price")
    session.add(portfolio)
    session.flush()

    position = book_position(session, _vanilla_booking(portfolio.id, entry_price=0.0))
    session.flush()

    row = session.query(SettlementCashflow).filter_by(position_id=position.id).one()
    assert row.amount is None
    assert row.status == "needs_amount"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement_generate.py -k "open_is_reachable or booking" -v`
Expected: FAIL — the booking tests find `[]` lifecycle events. (`test_open_is_reachable_through_the_real_constructor` should already PASS after Task 2; if it fails, Task 2 is incomplete.)

- [ ] **Step 3: Extract the non-committing seam in `positions.py`**

In `backend/app/services/domains/positions.py`, add this function immediately **above** `create_lifecycle_event` (before line 583):

```python
def record_lifecycle_event(
    sess: Session,
    *,
    portfolio: Portfolio,
    position: Position,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    actor: str = "agent",
) -> PositionLifecycleEvent:
    """Create one lifecycle event and apply its consequences, WITHOUT committing.

    The caller owns the transaction. ``create_lifecycle_event`` wraps this and
    commits; ``book_position`` calls it mid-booking, where committing here would
    end the caller's transaction early.
    """
    clean_event_type = (event_type or "").strip()
    if clean_event_type not in LIFECYCLE_EVENT_TARGETS:
        raise ValueError(f"Invalid event type '{event_type}'")
    valid_types = valid_lifecycle_event_types(position.product_type or "")
    if clean_event_type not in valid_types:
        raise ValueError(
            f"Invalid event type '{clean_event_type}'. Valid types: {sorted(valid_types)}"
        )
    clean_event_data = _enrich_lifecycle_event_data(
        sess, position, clean_event_type, dict(event_data or {})
    )
    target_status = LIFECYCLE_EVENT_TARGETS.get(clean_event_type)
    old_status = position.status
    new_status = old_status
    if target_status is not None and old_status != target_status:
        position.status = target_status
        new_status = target_status
    event = PositionLifecycleEvent(
        position_id=position.id,
        event_type=clean_event_type,
        event_data=clean_event_data,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
    )
    sess.add(event)
    sess.flush()  # event.id is required by cashflow generation
    try:
        generate_for_event(sess, event=event, actor=actor)
    except Exception:  # noqa: BLE001
        # Best-effort by design. Lifecycle is the source of truth for position
        # status and must never be held hostage to cashflow derivation;
        # generate_missing() is the safety net that fills any gap this leaves.
        logger.exception(
            "settlement cashflow generation failed for lifecycle event on position %s",
            position.id,
        )
    portfolio.updated_at = datetime.utcnow()
    record_audit(
        sess,
        event_type="position.lifecycle_event",
        actor=actor,
        subject_type="position",
        subject_id=position.id,
        payload={
            "event_type": clean_event_type,
            "event_data": clean_event_data,
            "old_status": old_status,
            "new_status": new_status,
        },
    )
    return event
```

Then replace the body of `create_lifecycle_event` (everything from `clean_event_type = ...` through `return PositionLifecycleUpdate(...)`) with:

```python
    with _session_scope(session) as sess:
        portfolio, position = _resolve_lifecycle_position(
            sess,
            position_id=position_id,
            source_trade_id=source_trade_id,
            portfolio_id=portfolio_id,
        )
        event = record_lifecycle_event(
            sess,
            portfolio=portfolio,
            position=position,
            event_type=event_type,
            event_data=event_data,
            actor=actor,
        )
        sess.commit()
        sess.refresh(event)
        sess.refresh(position)
        return PositionLifecycleUpdate(position=position, event=event)
```

Keep the `clean_event_type` validation *inside* `record_lifecycle_event` only — do not duplicate it in the wrapper.

- [ ] **Step 4: Emit the event from `book_position`**

In `backend/app/services/domains/booking.py`, inside `book_position`, insert immediately **after** the `record_audit(...)` call and **before** `return position`:

```python
    # The cash lifecycle starts at inception, not at termination: this `open`
    # event is what makes the settlement deriver emit the premium leg.
    # Function-scope import on purpose — positions.py -> position_adapter.py ->
    # booking.py, so a module-scope import here is a real cycle.
    from .positions import record_lifecycle_event

    open_event_data: dict[str, Any] = {"source": request.source}
    if request.trade_effective_date is not None:
        open_event_data["trade_date"] = str(request.trade_effective_date)
    record_lifecycle_event(
        session,
        portfolio=portfolio,
        position=position,
        event_type="open",
        event_data=open_event_data,
        actor=request.actor,
    )
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_settlement_generate.py tests/test_lifecycle_events.py tests/test_services_domains_positions.py -v`
Expected: PASS.

- [ ] **Step 6: Run every booking path, since all of them now emit an event**

Run:
```bash
.venv/bin/python -m pytest tests/test_confirmations_service.py tests/test_confirmations_tools.py \
  tests/test_rfq.py tests/test_hedging_strategy.py tests/test_position_import_pricing.py \
  tests/test_booking.py tests/test_product_builders.py -v
```
Expected: PASS. If a test asserts an exact cashflow or lifecycle-event count for a freshly booked position, it needs updating to include the new `open` event and `premium` row — that is the intended behaviour change, not a regression. If a file in that list does not exist, drop it and run `grep -rln "book_position" tests/` to get the real list.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/positions.py backend/app/services/domains/booking.py \
        tests/test_settlement_generate.py
git commit -m "feat(booking): emit an open lifecycle event so premium cashflows generate

Extracts a non-committing record_lifecycle_event seam so book_position can
record the event inside the caller's transaction."
```

---

### Task 5: Field specs and the server-owned vocabulary endpoint

**Files:**
- Modify: `backend/app/services/domains/lifecycle_vocabulary.py`
- Modify: `backend/app/schemas.py:1882` (after `PositionLifecycleEventOut`)
- Modify: `backend/app/main.py:2896` (before `list_portfolio_lifecycle_events`)
- Test: `tests/test_lifecycle_events.py`

**Interfaces:**
- Consumes: Task 2's vocabulary.
- Produces: `EVENT_FIELD_SPECS: dict[str, tuple[dict[str, str], ...]]`; `GET /api/lifecycle-vocabulary` → `{event_types, by_family, retired, event_fields}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lifecycle_events.py`:

```python
def test_lifecycle_vocabulary_endpoint_serves_the_server_owned_maps(tmp_path: Path):
    """The UI used to keep its own copy and it drifted — losing `settle` and
    `fixing` entirely. One source of truth, served."""
    client = make_client(tmp_path)

    resp = client.get("/api/lifecycle-vocabulary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["event_types"]["exercise"] == "closed"
    assert body["event_types"]["barrier_reset"] is None
    assert sorted(body["retired"]) == ["autocall", "coupon_lock"]

    vanilla = body["by_family"]["EuropeanVanillaOption"]
    assert "exercise" in vanilla and "expire" in vanilla
    assert "settle" in vanilla, "the UI's missing-settle bug must not survive"

    assert "settle" in body["event_fields"], "settle had no form fields at all"
    settle_keys = [f["key"] for f in body["event_fields"]["settle"]]
    assert settle_keys == ["settlement_amount", "settlement_date"]

    # Retired types keep their field specs so historical rows still render.
    assert "autocall" in body["event_fields"]


def test_lifecycle_vocabulary_by_family_matches_the_service(tmp_path: Path):
    from app.services.domains import positions as positions_svc

    client = make_client(tmp_path)
    body = client.get("/api/lifecycle-vocabulary").json()

    for family, allowed in body["by_family"].items():
        assert set(allowed) == positions_svc.valid_lifecycle_event_types(family)
        assert allowed == sorted(allowed), "sorted output keeps the picker stable"


def test_every_selectable_event_has_field_specs(tmp_path: Path):
    """A selectable event with no fields renders an empty form — which is how
    `settle` became unusable from the UI."""
    client = make_client(tmp_path)
    body = client.get("/api/lifecycle-vocabulary").json()

    selectable = {e for events in body["by_family"].values() for e in events}
    missing = selectable - set(body["event_fields"])
    assert missing == set(), f"selectable events with no form fields: {sorted(missing)}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lifecycle_events.py -k vocabulary -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Add the field specs**

Append to `backend/app/services/domains/lifecycle_vocabulary.py`:

```python
EVENT_FIELD_SPECS: dict[str, tuple[dict[str, str], ...]] = {
    "open": (
        {"key": "trade_date", "label": "Trade Date", "type": "date"},
        {"key": "premium_amount", "label": "Premium Amount", "type": "number"},
    ),
    "reopen": ({"key": "reason", "label": "Reason", "type": "text"},),
    "close": ({"key": "reason", "label": "Reason", "type": "text"},),
    "settle": (
        {"key": "settlement_amount", "label": "Settlement Amount", "type": "number"},
        {"key": "settlement_date", "label": "Settlement Date", "type": "date"},
    ),
    "maturity": (
        {"key": "maturity_date", "label": "Maturity Date", "type": "date"},
        {"key": "final_payoff", "label": "Final Payoff", "type": "number"},
    ),
    "exercise": (
        {"key": "exercise_date", "label": "Exercise Date", "type": "date"},
        {"key": "early", "label": "Early Exercise", "type": "bool"},
        {"key": "settlement_amount", "label": "Settlement Amount", "type": "number"},
    ),
    "expire": (
        {"key": "expiry_date", "label": "Expiry Date", "type": "date"},
        {"key": "reason", "label": "Reason", "type": "text"},
    ),
    "knock_in": (
        {"key": "barrier_level", "label": "Barrier Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
    ),
    "knock_out": (
        {"key": "barrier_level", "label": "Barrier Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "payoff", "label": "Payoff", "type": "number"},
    ),
    "barrier_reset": (
        {"key": "reset_date", "label": "Reset Date", "type": "date"},
        {"key": "new_barrier_level", "label": "New Barrier Level", "type": "number"},
        {"key": "previous_barrier_level", "label": "Previous Barrier", "type": "number"},
    ),
    "coupon_observation": (
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "observed_price", "label": "Observed Price", "type": "number"},
    ),
    "coupon_paid": (
        {"key": "coupon_amount", "label": "Coupon Amount", "type": "number"},
        {"key": "coupon_date", "label": "Coupon Date", "type": "date"},
    ),
    "memory_coupon": (
        {"key": "coupon_amount", "label": "Coupon Amount", "type": "number"},
        {"key": "memory_periods", "label": "Memory Periods", "type": "number"},
    ),
    "fixing": (
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "observed_price", "label": "Observed Price", "type": "number"},
    ),
    "custom": ({"key": "reason", "label": "Reason", "type": "text"},),
    # Retained for RETIRED types so historical rows still render with labels.
    # `by_family` is what gates the picker; this map is what renders. Keeping
    # the two concerns separate is what lets a retired type stay readable.
    "autocall": (
        {"key": "autocall_level", "label": "Autocall Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "payoff", "label": "Payoff", "type": "number"},
    ),
    "coupon_lock": (
        {"key": "lock_date", "label": "Lock Date", "type": "date"},
        {"key": "locked_coupon_rate", "label": "Locked Coupon Rate", "type": "number"},
    ),
}
```

Add `"EVENT_FIELD_SPECS"` to `__all__`.

- [ ] **Step 4: Add the response schemas**

In `backend/app/schemas.py`, immediately after `PositionLifecycleEventOut` (line 1882):

```python
class LifecycleFieldSpecOut(BaseModel):
    key: str
    label: str
    type: str


class LifecycleVocabularyOut(BaseModel):
    """The server-owned lifecycle event vocabulary.

    The frontend used to keep its own copy of these tables and they drifted —
    it lost `settle` and `fixing` entirely. This endpoint is the single source.
    """

    event_types: dict[str, str | None]
    by_family: dict[str, list[str]]
    retired: list[str]
    event_fields: dict[str, list[LifecycleFieldSpecOut]]
```

- [ ] **Step 5: Add the endpoint**

In `backend/app/main.py`, immediately **before** the `@app.get("/api/portfolios/{portfolio_id}/lifecycle-events")` decorator (line 2896):

```python
    @app.get("/api/lifecycle-vocabulary", response_model=LifecycleVocabularyOut)
    def get_lifecycle_vocabulary():
        """Serve the lifecycle event vocabulary so the UI never keeps a copy."""
        from app.services.domains import lifecycle_vocabulary as vocab

        return LifecycleVocabularyOut(
            event_types=dict(vocab.LIFECYCLE_EVENT_TARGETS),
            by_family={
                family: sorted(events)
                for family, events in vocab.PRODUCT_LIFECYCLE_EVENTS.items()
            },
            retired=sorted(vocab.RETIRED_EVENT_TYPES),
            event_fields={
                event: [LifecycleFieldSpecOut(**spec) for spec in specs]
                for event, specs in vocab.EVENT_FIELD_SPECS.items()
            },
        )
```

Add `LifecycleFieldSpecOut` and `LifecycleVocabularyOut` to the existing `from .schemas import (...)` block at `main.py:57`. That block is only roughly alphabetical (`QuoteRefreshResultOut` already sits out of order at line 92) — place the two new names immediately before `MarketQuoteCreate` (line 88).

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_lifecycle_events.py tests/test_lifecycle_vocabulary.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/lifecycle_vocabulary.py backend/app/schemas.py \
        backend/app/main.py tests/test_lifecycle_events.py
git commit -m "feat(api): serve the lifecycle event vocabulary from the server"
```

---

### Task 6: Frontend reads the vocabulary instead of hardcoding it

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/PositionLifecycleTimeline.tsx:9-58,77`
- Modify: `frontend/src/routes/Positions.tsx:970`
- Modify: `frontend/src/routes/Positions.live.tsx:68,130,480,570`
- Test: `frontend/src/components/PositionLifecycleTimeline.test.tsx`

**Interfaces:**
- Consumes: `GET /api/lifecycle-vocabulary` from Task 5.
- Produces: `LifecycleVocabulary` type; `PositionLifecycleTimeline` gains a **required** `vocabulary: LifecycleVocabulary | null` prop (`null` while loading).

**Why a required prop rather than an internal fetch:** this codebase splits `Route.live.tsx` (fetches) from `Route.tsx` (renders), and the timeline is a pure presentational component. A fallback default would reintroduce exactly the hardcoded list this task deletes, so `null` renders a disabled picker instead.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/PositionLifecycleTimeline.test.tsx`, and add `vocabulary={vocabulary}` to the two existing `render(<PositionLifecycleTimeline ... />)` calls:

```tsx
import type { LifecycleVocabulary } from '../types';

const vocabulary: LifecycleVocabulary = {
  event_types: { knock_out: 'closed', settle: 'closed', custom: null },
  by_family: {
    SnowballOption: ['close', 'custom', 'knock_in', 'knock_out', 'settle'],
  },
  retired: ['autocall', 'coupon_lock'],
  event_fields: {
    settle: [
      { key: 'settlement_amount', label: 'Settlement Amount', type: 'number' },
      { key: 'settlement_date', label: 'Settlement Date', type: 'date' },
    ],
    knock_out: [{ key: 'barrier_level', label: 'Barrier Level', type: 'number' }],
    close: [{ key: 'reason', label: 'Reason', type: 'text' }],
    custom: [{ key: 'reason', label: 'Reason', type: 'text' }],
    knock_in: [{ key: 'barrier_level', label: 'Barrier Level', type: 'number' }],
  },
};

it('offers the server-supplied events, including settle', async () => {
  render(
    <PositionLifecycleTimeline
      row={row}
      events={[]}
      onAddEvent={vi.fn()}
      onCancelEvent={vi.fn()}
      adding={false}
      vocabulary={vocabulary}
    />,
  );

  await userEvent.click(screen.getByRole('button', { name: /add event/i }));
  expect(screen.getByRole('button', { name: 'settle' })).toBeInTheDocument();
});

it('disables the picker until the vocabulary arrives', () => {
  render(
    <PositionLifecycleTimeline
      row={row}
      events={[]}
      onAddEvent={vi.fn()}
      onCancelEvent={vi.fn()}
      adding={false}
      vocabulary={null}
    />,
  );

  expect(screen.getByRole('button', { name: /add event/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/components/PositionLifecycleTimeline.test.tsx`
Expected: FAIL — TypeScript rejects the unknown `vocabulary` prop and `LifecycleVocabulary` is not exported.

- [ ] **Step 3: Add the type**

Append to `frontend/src/types.ts`:

```ts
export type LifecycleFieldSpec = {
  key: string;
  label: string;
  type: 'number' | 'date' | 'text' | 'bool';
};

/** Server-owned lifecycle event vocabulary. The UI must not keep its own copy:
 *  the previous hardcoded tables drifted and lost `settle` and `fixing`. */
export type LifecycleVocabulary = {
  event_types: Record<string, string | null>;
  by_family: Record<string, string[]>;
  retired: string[];
  event_fields: Record<string, LifecycleFieldSpec[]>;
};
```

- [ ] **Step 4: Rewrite the component to read the prop**

In `frontend/src/components/PositionLifecycleTimeline.tsx`:

1. Delete `PRODUCT_EVENTS` (lines 9–15) and `EVENT_FIELDS` (lines 19–58) entirely, along with the now-unused local `FieldSpec` type.
2. Import the shared types: add `LifecycleFieldSpec` and `LifecycleVocabulary` to the existing `import type { PositionLifecycleEvent } from '../types';`.
3. Add `vocabulary: LifecycleVocabulary | null;` to the `Props` type and destructure it in the component signature.
4. Replace line 77 with:

```tsx
  const availableEvents = useMemo(
    () => vocabulary?.by_family[row.product_type] ?? [],
    [vocabulary, row.product_type],
  );
```

5. Wherever the component reads `EVENT_FIELDS[eventType]`, read `vocabulary?.event_fields[eventType] ?? []` instead, typed as `LifecycleFieldSpec[]`.
6. Add `disabled={!vocabulary}` to the "Add Event" button.
7. For a field whose `type` is `'bool'`, render a checkbox and store `'true'`/`''` in the existing `eventData` string map; the submit handler must convert `'true'` to boolean `true` before calling `onAddEvent`. Everything else keeps its current `number`/`date`/`text` handling.

- [ ] **Step 5: Thread the prop through the route**

In `frontend/src/routes/Positions.tsx`: add `lifecycleVocabulary: LifecycleVocabulary | null;` to the props type of the component that owns the lifecycle tab, and pass `vocabulary={lifecycleVocabulary}` at line 970.

In `frontend/src/routes/Positions.live.tsx`:

```tsx
// with the other useState declarations, near line 68
const [lifecycleVocabulary, setLifecycleVocabulary] = useState<LifecycleVocabulary | null>(null);

// in the same effect that calls fetchLifecycleEvents, near line 130
setLifecycleVocabulary(await fetchLifecycleVocabulary());

// beside fetchLifecycleEvents, near line 570
async function fetchLifecycleVocabulary(): Promise<LifecycleVocabulary> {
  return await api<LifecycleVocabulary>('/api/lifecycle-vocabulary');
}
```

and pass `lifecycleVocabulary={lifecycleVocabulary}` to `<Positions ... />` near line 480. Add `LifecycleVocabulary` to the `import type { ... } from '../types';` block at line 3.

- [ ] **Step 6: Run the tests and the type-check**

Run:
```bash
cd frontend && npx vitest run src/components/PositionLifecycleTimeline.test.tsx && npx tsc --noEmit
```
Expected: PASS, and `tsc` clean.

- [ ] **Step 7: Confirm no hardcoded vocabulary survives**

Run: `cd frontend && grep -rn "knock_out'" src/components/PositionLifecycleTimeline.tsx`
Expected: no output. Any hit means a table survived the deletion.

- [ ] **Step 8: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS. This suite is flaky under load — if files fail, re-run those files alone and compare against a same-machine `main` run before treating it as a regression.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/PositionLifecycleTimeline.tsx \
        frontend/src/components/PositionLifecycleTimeline.test.tsx \
        frontend/src/routes/Positions.tsx frontend/src/routes/Positions.live.tsx
git commit -m "feat(frontend): drive the lifecycle picker off the server vocabulary"
```

---

### Task 7: Full suite, docs, and the pre-merge checks

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the whole backend suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS. Do **not** pipe through `tail`. Any failure mentioning an exact count of lifecycle events or cashflows for a booked position is Task 4's intended behaviour change — update the assertion.

- [ ] **Step 2: Check for exact-set assertions this work breaks**

Run: `grep -rln "lifecycle" tests/ && grep -rln "book_position" tests/`
Expected: review each hit for a hardcoded event-type list or a booked-position cashflow count. This repo has been bitten repeatedly by exact-set pins (the settlement module broke eight).

- [ ] **Step 3: Count the historical rows the phoenix retirement affects**

Run:
```bash
.venv/bin/python -c "
import sqlite3
db = sqlite3.connect('data/open_otc.sqlite3')
for t in ('autocall', 'coupon_lock'):
    n = db.execute(
        'SELECT COUNT(*) FROM position_lifecycle_events WHERE event_type = ?', (t,)
    ).fetchone()[0]
    print(f'{t}: {n} existing rows')
"
```
This is a **read-only** query against the live DB, and the only step in this plan that touches it. Record the numbers in the CHANGELOG entry: they are the size of the vocabulary discontinuity a phoenix book will show either side of this change.

- [ ] **Step 4: Add the `CLAUDE.md` subsection**

Insert a new section in `CLAUDE.md` after the "Settlement module" section:

```markdown
## Position lifecycle events

Two layers, one chokepoint. `services/domains/lifecycle_vocabulary.py` owns
`LIFECYCLE_EVENT_TARGETS` (17 event types → the status each drives; `None` =
non-transitioning), `PRODUCT_LIFECYCLE_EVENTS` (per-family allowlist, all 15
bookable families explicit), `RETIRED_EVENT_TYPES`, and `EVENT_FIELD_SPECS`.
Served to the UI by `GET /api/lifecycle-vocabulary` — the frontend keeps **no**
copy, because the copy it used to keep drifted and lost `settle` and `fixing`.

- **The allowlist is TOTAL.** `create_lifecycle_event` is the only constructor of
  `PositionLifecycleEvent`, so a family missing from the map does not degrade
  gracefully — it loses the event outright. `tests/test_lifecycle_vocabulary.py`
  guards that every declared type is reachable and every family is covered.
  **Both guards failed on `main`:** `open`/`reopen` were reachable by no product,
  so `CASH_LEG_RULES["open"]` — the premium leg — had never fired for any
  position. Its two tests passed by building `PositionLifecycleEvent(...)`
  directly, proving satisfiability rather than reachability. **A test that
  constructs its own subject bypasses whatever validates the real one.**
- **`book_position` emits `open`,** via the non-committing
  `positions.record_lifecycle_event` seam (`create_lifecycle_event` wraps it and
  commits). `booking.py` imports it **function-scope**: positions.py →
  position_adapter.py → booking.py is a real cycle.
- **`expire` emits no cash leg, deliberately.** `generate.py` sets
  `status = "needs_amount" if amount is None else "pending"` and a zero resolves
  to `None`, so a terminating event with nothing owed would leave a permanent
  phantom obligation. `expire` is how the system says *zero* rather than
  *unknown*.
- **`barrier_reset` RECORDS a barrier step; it does not apply one.** Lifecycle
  events write `event_data`; pricing reads `Position.product_kwargs`. A reset
  position still prices off its original barrier.
- **`autocall`/`coupon_lock` are retired, not deleted** — no family may record
  them, but they keep their `LIFECYCLE_EVENT_TARGETS` entry and `autocall` keeps
  its cash rule. `_project_status_from_lifecycle` resolves targets with `.get()`,
  so deleting them would make historical phoenix rows read as non-transitioning
  and silently stop closing their positions.
```

- [ ] **Step 5: Add the CHANGELOG entry**

Under `[Unreleased]` in `CHANGELOG.md`:

```markdown
### Added
- Lifecycle events for every bookable product family: `exercise` (with an
  `early` flag for American early exercise), `expire` (terminal, books no cash),
  and `barrier_reset` for KO-reset snowballs. All 15 families now have an
  explicit event map instead of falling through to `close`/`settle`/`custom`.
- `GET /api/lifecycle-vocabulary` serves the event vocabulary and per-event form
  fields; the Positions lifecycle picker reads it instead of a hardcoded copy,
  which restores the missing `settle` and `fixing` options.

### Fixed
- `open` and `reopen` were declared but allowed for no product, so the `premium`
  settlement leg could never fire and no position had ever recorded its
  inception cash. `book_position` now emits `open`.

### Changed
- Phoenix records `knock_out`/`coupon_observation` instead of
  `autocall`/`coupon_lock`, which are retired: existing rows keep working, but
  no new ones can be recorded. (<N> autocall / <M> coupon_lock existing rows —
  fill in from Step 3.)
```

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md CHANGELOG.md
git commit -m "docs: record the lifecycle event vocabulary and its reachability guard"
```

---

## Self-Review

**Spec coverage:** §3 vocabulary → Task 2; §3.1 `barrier_reset` scope boundary → Task 2 (code comment) + Task 7 (CLAUDE.md); §3.2 retirement → Task 2 + Task 3; §4 matrix → Task 2; §5 booking emits `open` → Task 4; §6 settlement → Task 3; §7 endpoint + field specs → Task 5; §8 frontend → Task 6; §9 tests → Tasks 2–6; §10 blast radius → Task 4 Step 6 + Task 7 Steps 2–3; §11 out of scope → nothing implements it, correctly.

**Deviation from the spec, flagged:** §9 says "settlement tests go through `create_lifecycle_event`, not raw `PositionLifecycleEvent(...)`". This plan keeps the existing `_event` helper for the generation *unit* tests and adds a dedicated reachability test through the real constructor (Task 4, Step 1). Rewriting every generation unit test into an integration test would slow them and couple them to portfolio/product setup for no extra signal; one test that proves the production path reaches `open` is what actually closes F1. If the reviewer prefers the literal reading, converting `_event` is mechanical — the `book` fixture's `SnowballOption` accepts every event type those tests use, and `Portfolio.kind` already defaults to `CONTAINER`.

**Type consistency:** `record_lifecycle_event` has the same signature in Task 4 Step 3 and its callers in Steps 3–4. `LifecycleVocabulary` field names (`event_types`, `by_family`, `retired`, `event_fields`) match `LifecycleVocabularyOut` in Task 5 exactly. `LifecycleFieldSpec.type` includes `'bool'` in TS to match the `early` flag's `"bool"` in `EVENT_FIELD_SPECS`.
