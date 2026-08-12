# Position lifecycle event coverage — design

**Date:** 2026-08-12
**Status:** approved (brainstorming complete, pending implementation plan)

---

## 1. Problem

The desk cannot record that a vanilla option was exercised. There is no `exercise` event type
anywhere in the system, and `EuropeanVanillaOption` / `AmericanOption` are allowed only
`{close, settle, custom}`.

Auditing outward from that observation found the gap is structural, not a missing entry: the
per-product event allowlist covers **6 of 15 bookable families**, two declared event types are
reachable by no product at all, and the frontend maintains a second copy of the vocabulary that
has already drifted from the backend.

### 1.1 How the vocabulary is structured

| Layer | Location | Role |
|---|---|---|
| `LIFECYCLE_EVENT_TARGETS` | `services/domains/positions.py:37` | The 14 globally legal event types → the status each drives (`None` = non-transitioning) |
| `PRODUCT_LIFECYCLE_EVENTS` | `services/domains/positions.py:54` | Per-family allowlist — only 6 entries |
| `valid_lifecycle_event_types()` | `services/domains/positions.py:107` | Fallback for every other family: `{close, settle, custom}` |
| `CASH_LEG_RULES` | `services/settlement/derive.py:230` | Downstream: which events imply cash |
| `PRODUCT_EVENTS` / `EVENT_FIELDS` | `frontend/src/components/PositionLifecycleTimeline.tsx:9,19` | A second, hand-maintained copy |

`create_lifecycle_event` (`positions.py:583`) is the **only** place `PositionLifecycleEvent(...)`
is constructed (`positions.py:618`), and it rejects any type outside the family's allowlist. The
allowlist is therefore total: a family omitted from `PRODUCT_LIFECYCLE_EVENTS` does not degrade
gracefully, it loses the event outright. `event_type` is `String(80)`, not a DB enum, so nothing
in the schema objects when the maps drift.

### 1.2 Coverage as it stands (verified by executing the maps)

```
family                        map       allowed events
AmericanOption                default   close, custom, settle
AsianOption                   EXPLICIT  close, custom, fixing, settle
BarrierOption                 EXPLICIT  close, custom, knock_in, knock_out, maturity, settle
CashOrNothingDigitalOption    default   close, custom, settle
DoubleOneTouchOption          default   close, custom, settle
DoubleSharkfinOption          EXPLICIT  close, custom, knock_in, knock_out, maturity, settle
EuropeanVanillaOption         default   close, custom, settle
Futures                       default   close, custom, settle
KnockOutResetSnowballOption   default   close, custom, settle
OneTouchOption                default   close, custom, settle
PhoenixOption                 EXPLICIT  autocall, close, coupon_lock, coupon_paid, custom,
                                        maturity, memory_coupon, settle
RangeAccrualOption            default   close, custom, settle
SingleSharkfinOption          EXPLICIT  close, custom, knock_in, knock_out, maturity, settle
SnowballOption                EXPLICIT  close, coupon_observation, coupon_paid, custom,
                                        knock_in, knock_out, maturity, settle
SpotInstrument                default   close, custom, settle
```

### 1.3 Findings

**F1 — `open` and `reopen` are unreachable, so the `premium` cash leg is dead.** Both are declared
in `LIFECYCLE_EVENT_TARGETS` but appear in no family's allowlist *and* not in the default, so the
sole constructor rejects them for every product. `CASH_LEG_RULES["open"]` can therefore never fire,
and the settlement module's own documented desk decision — *"`open` emits a `premium` leg … so the
cash lifecycle is covered from inception"* (`derive.py:212`) — does not hold. No position has ever
had a premium cashflow; every book's cash record begins at termination.

The two tests covering it (`tests/test_settlement_generate.py:295`,
`tests/test_settlement_derive.py:135`) construct `PositionLifecycleEvent(...)` directly via the
`_event` helper (`test_settlement_generate.py:36`), bypassing the gate. They prove
**satisfiability, not reachability** — the same defect class `CLAUDE.md` records for the arena
golden replay ("it proves satisfiability, never reachability") and the trader-rfq
live-reachability fix.

**F2 — no `exercise`, and no `expire`.** Vanillas get `{close, settle, custom}`, and `maturity` is
not even allowed for them, so an exercised or expiring option can only be recorded as a generic
`close` or a `settle`. American early exercise — which has an economically meaningful date and an
early-vs-final distinction — has nowhere to live.

**F3 — 9 of 15 families run on the 3-event default**, several implausibly.
`KnockOutResetSnowballOption` is the starkest: a snowball with no knock-in, knock-out or coupon
events, though it is grouped *with* `SnowballOption` in `_SNOWBALL_BOOKING_TYPES`
(`booking.py:50`), `_SCHEDULE_FAMILIES` (`product_lifecycle.py:28`) and `_PREBUILT_TIDY_CLASSES`
(`product_builders.py:699`) everywhere else. Also `OneTouchOption` / `DoubleOneTouchOption` (touch
products with no touch event) and `RangeAccrualOption` (an accrual product with no coupon or
observation event, despite being in `_SCHEDULE_FAMILIES`).

**F4 — the frontend copy has drifted.** `PositionLifecycleTimeline.tsx` drops `settle` from all
five families it lists, omits `AsianOption`/`fixing` entirely, and its fallback is
`['close', 'custom']` — missing the `settle` the backend allows.

**F5 — zero cash cannot be expressed.** `generate.py:197` sets
`status = "needs_amount" if draft.amount is None else "pending"`, and a zero resolves to `None`
(`test_zero_premium_is_needs_amount_not_a_zero_cashflow`). Any terminating event that emits a
`settlement` leg with nothing owed leaves a row permanently *awaiting an amount* — a phantom
obligation on the blotter. This already bites `maturity`: a barrier or sharkfin maturing worthless
emits a `needs_amount` row with no follow-up `settle` to fill it, and the desk's only recourse is
to `block` it by hand.

**F6 — the UI cannot record a `settle` event at all.** `settle` is absent from every
`PRODUCT_EVENTS` list *and* from `EVENT_FIELDS`, so neither the option nor its
`settlement_amount` / `settlement_date` fields exist in the timeline form. `settle` is the only
event the backend enriches with a number (`_enrich_snowball_ko_settlement`, `positions.py:485`)
and the only one that fills a `needs_amount` settlement row. Agents can record it
(`tools/positions.py:843`); humans cannot. The Settlement page's direct cashflow edit is the only
human path to the money.

## 2. Scope

Whole event layer: F1–F6 together. Four decisions were taken during brainstorming and are binding
on the implementation.

| # | Decision |
|---|---|
| D1 | Fix the whole event layer, not just the vanilla gap |
| D2 | `exercise` and `expire` are **two distinct terminating event types**; `expire` emits no cash leg |
| D3 | `book_position` emits an `open` event, so premium cashflows generate from inception. **No backfill** of existing positions |
| D4 | A single server-owned vocabulary endpoint; the frontend deletes both hardcoded tables |

Plus a fifth, taken as a correction during review:

| # | Decision |
|---|---|
| D5 | `maturity` means *the position is no longer alive*, and is a **final state available to all products** — not partitioned by product style, and not redefined as non-terminating |

## 3. Vocabulary — 14 → 16 event types

Two additions to `LIFECYCLE_EVENT_TARGETS`:

| Event | Target status | Cash leg | Meaning |
|---|---|---|---|
| `exercise` | `closed` | `settlement` (singleton) | The holder exercised. `event_data`: `{exercise_date, early: bool, settlement_amount?}` |
| `expire` | `closed` | **none** | Ended with nothing owed, and that was verified |

`early: bool` is what distinguishes American early exercise from exercise at expiry — one event
type, two economics, rather than two near-synonym types.

`expire` emitting **no** cash leg is load-bearing, and follows directly from F5: it is the only way
this system can say *zero* rather than *unknown*. This is the same `empty` vs `unavailable`
discipline the reporting module already established — a report that renders them alike claims a
clean book nobody verified.

`maturity`, `exercise` and `expire` are all final states (D5). The desk records the one that
describes what actually happened:

- `maturity` — reached its scheduled end; cash follows via a later `settle` (the snowball/phoenix
  pattern, where the payoff is computed after the final observation)
- `exercise` — exercised; cash owed
- `expire` — ended worthless; nothing owed

## 4. Per-family matrix

Universal base for **all 15 families**: `open`, `reopen`, `close`, `settle`, `maturity`, `custom`.

| Family | Plus |
|---|---|
| EuropeanVanillaOption | `exercise`, `expire` |
| AmericanOption | `exercise` *(early)*, `expire` |
| CashOrNothingDigitalOption | `exercise`, `expire` |
| BarrierOption | `knock_in`, `knock_out`, `exercise`, `expire` |
| SingleSharkfinOption | `knock_in`, `knock_out`, `exercise`, `expire` |
| DoubleSharkfinOption | `knock_in`, `knock_out`, `exercise`, `expire` |
| OneTouchOption | `knock_in` *(the touch)*, `expire` *(no-touch)* |
| DoubleOneTouchOption | `knock_in`, `expire` |
| AsianOption | `fixing`, `exercise`, `expire` |
| SnowballOption | `knock_in`, `knock_out`, `coupon_observation`, `coupon_paid` |
| KnockOutResetSnowballOption | `knock_in`, `knock_out`, `coupon_observation`, `coupon_paid` |
| PhoenixOption | `autocall`, `coupon_lock`, `coupon_paid`, `memory_coupon`, `knock_in` |
| RangeAccrualOption | `fixing`, `coupon_observation`, `coupon_paid` |
| Futures | — |
| SpotInstrument | — |

Rationale on the non-obvious cells:

- **`KnockOutResetSnowballOption` mirrors `SnowballOption` exactly.** It is a snowball; its absence
  from the map is an oversight, and every other subsystem already pairs the two.
- **Phoenix gains `knock_in`.** Phoenix structures standardly carry a KI barrier; the omission
  looks like the same class of oversight. Additive, so low risk.
- **One-touch models the touch as `knock_in`**, which targets `knocked_in` and leaves the position
  alive until `maturity`/`settle` — correct for a pay-at-maturity one-touch, and it needs no change
  to the target map. A no-touch expiry is `expire`.
- **Futures gets no `expire`**, because futures always settle something; `maturity` + `settle`
  covers final settlement.
- **`reopen` is universal.** It reverses an erroneous close, and settlement already supports the
  cycle: dedup ignores terminal cashflows, so a settle → reopen → settle sequence legitimately
  earns a second row (an established invariant in `CLAUDE.md`).
- **`SpotInstrument` keeps `maturity`** for consistency with D5, though spot will never use it.

## 5. Reachability — `book_position` emits `open`

`book_position` (`booking.py:259`) currently sets `Position.status` directly and emits no lifecycle
event, so a position's timeline begins empty and `_project_status_from_lifecycle`
(`positions.py:567`) has no true origin point to seed from. That is why `open` was never added to
any allowlist.

The fix records an `open` event after the position is created, in the same transaction, with the
actor taken from the booking context. Every booking path — confirmations, RFQ, hedge booking, xlsx
import — funnels through this one function, so a single edit covers all of them.

**Blast radius, accepted:** every new booking generates a `premium` cashflow of
`|entry_price × quantity|` (`_premium_from_position`, `derive.py:103`). A position with no
`entry_price` produces a `needs_amount` premium row — the honest answer ("premium owed, amount not
recorded"), consistent with the existing desk decision that a trade must never move cash silently.
The Settlement page will become non-empty on trade date rather than at termination.

**No backfill (D3).** Existing positions gain no `open` event and no premium row. A backfill would
have to synthesize events against the live DB and guard against double-counting anything already
recorded by hand; it is deliberately out of scope.

## 6. Settlement consequences

`CASH_LEG_RULES` gains exactly one entry:

```python
"exercise": (_SETTLEMENT_LEG,),
```

`expire` is deliberately **absent**, joining `reopen` / `knock_in` / `coupon_observation` /
`coupon_lock` / `fixing` / `custom` as events that emit nothing.

No new dedup logic is required. `_SETTLEMENT_LEG` has `leg_key="settlement"`, which is already in
`SINGLETON_LEG_KEYS`, so `generate._open_singleton` handles an `exercise` followed by a `settle`
exactly as it already handles `knock_out` → `settle`: the later event fills the row rather than
creating a second one.

The comment at `derive.py:182` ("The 14 legal event types live in `LIFECYCLE_EVENT_TARGETS`") must
be updated to 16, and its list of non-cash types extended with `expire`.

## 7. Server-owned vocabulary endpoint

`GET /api/lifecycle-vocabulary` returns:

```json
{
  "event_types":  { "<name>": "<target_status|null>" },
  "by_family":    { "<family>": ["<name>", ...] },
  "event_fields": { "<name>": [{"key": "...", "label": "...", "type": "number|date|text"}] }
}
```

`EVENT_FIELDS` moves from TSX into Python beside the allowlist, so the per-event form fields become
server-owned too — they encode domain rules (`knock_out` needs a barrier level and an observation
date), which is why they drifted alongside the event list.

New field specs required for the added events, and for the two the UI is missing today (F6):

| Event | Fields |
|---|---|
| `exercise` | `exercise_date` (date), `early` (bool), `settlement_amount` (number) |
| `expire` | `expiry_date` (date), `reason` (text) |
| `settle` | `settlement_amount` (number), `settlement_date` (date) |
| `fixing` | `observation_date` (date), `observed_price` (number) |
| `open` | `trade_date` (date), `premium_amount` (number) — display only; see note |
| `reopen` | `reason` (text) |

Note on `open`: `derive.py`'s `open` rule declares no `amount_keys`, so the position resolver is
its sole source and a recorded `premium_amount` does **not** override it — pinned deliberately by
`test_premium_ignores_unrelated_event_data_keys`. The field is informational only; changing that is
out of scope.

The `type` union gains `bool` for the `early` flag.

## 8. Frontend

`PositionLifecycleTimeline.tsx` deletes `PRODUCT_EVENTS` and `EVENT_FIELDS` and drives off the
fetched vocabulary. This restores the `settle` and `fixing` options the UI is currently missing
(F4, F6) as a consequence rather than as a separate fix.

Styling is token-only per `frontend/CLAUDE.md`. Note that `--radius-1` and `--ink-3` are referenced
by some page CSS but defined nowhere — verify every token against `frontend/src/tokens/`.

## 9. Testing

The decisive tests are the two structural guards, because F1 survived precisely by having tests
that bypassed the gate.

- **Reachability guard.** Every key in `LIFECYCLE_EVENT_TARGETS` and every key in `CASH_LEG_RULES`
  must appear in at least one family's allowlist. *This fails on `main` today* — it is the test
  that would have caught F1.
- **Family coverage guard.** `PRODUCT_LIFECYCLE_EVENTS` must cover every family in
  `product_builders._REGISTRY`, so a newly added family cannot silently fall through to the
  3-event default. This is what hid F3.
- **Settlement tests go through `create_lifecycle_event`**, not raw `PositionLifecycleEvent(...)`.
  The `_event` helper in `test_settlement_generate.py:36` must be routed through the real
  constructor, or these tests keep proving satisfiability instead of reachability.
- `expire` produces no cashflow row.
- `exercise` → `settle` produces exactly one `settlement` leg (existing singleton dedup).
- `book_position` emits exactly one `open` event and one `premium` cashflow; a position with no
  `entry_price` yields `needs_amount` rather than a zero row.
- The endpoint's `by_family` matches `valid_lifecycle_event_types` for all 15 families.
- Frontend: the timeline renders its options from the fetched vocabulary, and no hardcoded event
  list remains in the component.

Run backend as `.venv/bin/python -m pytest` from the repo root; frontend as
`cd frontend && npm test` plus `npx tsc --noEmit`. Never pipe pytest through `tail`.

## 10. Compatibility and blast radius

- **Purely additive to recorded history.** The allowlist gates *creation* only; existing rows of
  any type still render and still project status. No migration is needed — `event_type` is
  `String(80)`, not an enum.
- **No event type is removed**, so nothing recordable today becomes unrecordable.
- **New cashflow rows appear on every new booking** (§5). This is the intended behaviour, but it
  changes what the Settlement page shows from the day it ships.
- **Golden workflows / arena:** no manifest grades a lifecycle event type today, but confirm before
  merge — an exact-set assertion over tool or route surfaces would break. Enumerate with
  `grep -rln "lifecycle" tests/`.
- **`CLAUDE.md`** needs a subsection recording the two-layer vocabulary, the chokepoint property,
  and the reachability guard. **`CHANGELOG.md`** under `[Unreleased]` is required by the
  `pre-push` hook.

## 11. Out of scope

- Backfilling `open` events or premium cashflows for existing positions (D3).
- Any payoff computation for `exercise` or `expire`. Settlement governs cash and never computes it;
  an amount either arrives with the event or the row is honestly `needs_amount`.
- Letting a recorded `premium_amount` override the position-derived premium (§7).
- Physical settlement / delivery legs. Exercise here is cash-settled; a delivery leg that mints a
  stock position is a separate design.
- Automatic event generation from schedules (beyond the existing
  `generate_asian_fixing_schedule`) — e.g. auto-`expire` on the maturity date.

## 12. Open items

Neither blocks implementation; both are cheap to revise.

1. **`reopen` on every family** — offered universally on the reasoning that it corrects an
   erroneous close and settlement already supports the cycle. Could be narrowed to a
   correction-only tool.
2. **`expire` for Futures** — dropped on the reasoning that futures always settle something.
   Trivially additive if the futures book needs it.
