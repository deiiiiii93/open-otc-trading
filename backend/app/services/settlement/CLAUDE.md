# Settlement module

The cash that position lifecycle events imply, tracked and governed.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** The events that generate it: [`../domains/CLAUDE.md`](../domains/CLAUDE.md).

---

## Settlement module

The cash that position lifecycle events imply, tracked and governed. Cashflows are
auto-generated from `PositionLifecycleEvent`s; users and agents release, block, edit
and settle them, and issue settlement notices.

**Package:** `backend/app/services/settlement/` — `contracts.py` (statuses +
`CashflowDraft`), `derive.py` (the pure deriver + `CASH_LEG_RULES` +
`SINGLETON_LEG_KEYS`), `store.py` (state machine + optimistic concurrency),
`generate.py` (sweep + same-position dedup + fill), `drift.py`, `notice.py`,
`errors.py`. REST: `routers/settlement.py` (`/api/settlement`). Tools:
`tools/settlement.py` (3 reads + 10 writes). Skill:
`skills/workflows/settlement/manage-settlement-cashflows/`. Frontend:
`frontend/src/routes/Settlement.{tsx,live.tsx,types.ts,css}`. Migration `0055`.

### It governs cash; it never computes payoffs

An amount either comes with the lifecycle event or the cashflow is honestly
`needs_amount`. That is a correct state, not a bug — the same `empty` vs
`unavailable` discipline the report module fought for. A settlement calculator here
would be a second, unvalidated pricing surface beside the pinned `quantark==0.3.0`.
The one exception is the `premium` leg, which multiplies the position's own recorded
`entry_price × quantity` — two stored trade fields, not a model.

### `derived_amount` beside `amount` is the load-bearing choice

`derived_*` is the deriver's snapshot for the row's OWN event; `amount`/`value_date`
are effective values an edit changes. **Without the frozen snapshot, drift cannot be
detected on an edited row** — the recompute would be compared against the human's
number and every edited row would read as drifted forever. `amount != derived_amount`
therefore means "overridden", which is exactly how `resync_cashflow` must treat it.

### Two dedup layers, because they catch different duplicates

- `UNIQUE(lifecycle_event_id, leg_key)` stops one event emitting a leg twice.
- `SINGLETON_LEG_KEYS` (`{"settlement", "premium"}`) + `generate._open_singleton` stop
  **different** events emitting the same once-per-position leg twice. The DB cannot
  see that a `knock_out` and its follow-up `settle` are one economic settlement —
  they are separate `position_lifecycle_events` rows. Dedup ignores **terminal**
  cashflows (`settled` / `void`) so a settle → reopen → settle cycle earns a second
  row **only once the first settlement is terminal**. If the first row is still
  `pending` — the ordinary desk state — the second `settle` would be absorbed by the
  singleton guard and its amount **silently dropped**: the lifecycle log would say 650
  while the blotter still said 500. Measured, not theorised; `reopen` was unreachable
  until the lifecycle-vocabulary work made it recordable, so this path is new.
  **`record_lifecycle_event` therefore REFUSES a `reopen` while a non-terminal
  `settlement` row exists** (`generate.live_settlement_row`, checked in
  `positions.record_lifecycle_event` so REST, UI and agent all get it). Fail-closed
  and mutates nothing — the desk settles, voids or edits that cashflow first, and
  then the reopen legitimately earns a second row. The always-`pending` `premium`
  row is deliberately not consulted: only the `settlement` leg can be stranded.
  **`coupon` is deliberately NOT singleton** — coupons recur, and deduping them would
  collapse a snowball's whole schedule into one row.

### Gotchas

- **`generate._fill_if_empty` is the single sanctioned exception to INSERT-only.**
  Terminating events create a `needs_amount` row; the later `settle` fills it.
  Narrow by design: null → value only, never over a released or edited row,
  idempotent, logged as `filled_from_event`. **It leaves `derived_*` untouched** —
  that snapshot describes the row's own event, which really carried no amount, so
  rewriting it would make drift compare the filled value against a re-derived `None`
  forever.
- **Drift flags, never applies, and never bumps `row_version`.** Flagging is not a
  user mutation; a UI holding a version must stay able to act on the row it sees.
- **Amounts are non-negative magnitudes; `direction` carries the sign.** A negative
  derived amount flips `pay` ↔ `receive`, so a loss-side settlement cannot read as
  "pay 1,250" when the cash goes the other way.
- **The inline hook in `create_lifecycle_event` is best-effort on purpose.** Lifecycle
  is the source of truth for position status and must never be held hostage to
  cashflow derivation, which is why the deriver is *total* (never raises) and
  `generate_missing` is the safety net.
- **`settle_settlement_cashflow` is `irreversible`; every other settlement write is
  `"write"`.** Deliberate desk decision: release is recallable (`unrelease` exists and
  `block` is reachable from `released`), so only the unrecallable assertion that money
  moved is hard-gated. Remember `"write"` means AUTO/headless executes it unattended.
  `settle` carries a `_SUMMARY_BUILDERS` entry so the card states the amount rather
  than two bare integers.
- **A test derives the mutating REST routes from the real router** and asserts each has
  a tool counterpart, so the HTTP and agent surfaces cannot drift apart.
- **Eight exact-set pins broke when this landed**, seven backend and one frontend:
  `test_hitl.py` (interrupt set), `test_capability_assignments.py` (121 → 134),
  `test_skills_catalog_v2.py` (×2), `test_routing_table.py`, `test_persona_domains.py`,
  and `frontend/src/lib/routing.test.ts` (26 → 27 routes).
- **The skill lint caps a SKILL.md body at 500 tokens** and requires `may_escalate_to`
  plus an `## Example` section. The first draft came in at 749 and failed CI lint.
- **`--radius-1` and `--ink-3` are referenced by some page CSS but defined nowhere.**
  Do not copy them; verify every token against `frontend/src/tokens/` before use.
