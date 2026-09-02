# Domain facades — lifecycle events & pricing curves

Position lifecycle vocabulary and the term-structure curves that feed pricing-parameter generation.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** The cash a lifecycle event implies: [`../settlement/CLAUDE.md`](../settlement/CLAUDE.md).

---

## Term-structure curves for pricing parameters

Per-underlying `r`/`q`/`vol` **term-structure curves** feed a *materialize-then-price*
step: curves are interpolated at each open trade's maturity into a normal **flat**
`PricingParameterProfile`, so the pricing path (QuantArk, `risk_engine`,
`build_assumptions_set`) is **completely unchanged** — it still consumes scalars.

**Storage.** Three nullable JSON columns on `instruments` (`Instrument` /
`UnderlyingPricingDefault`, `models.py`): `rate_curve` / `dividend_yield_curve` /
`volatility_curve`, each `list[{"tenor": <label>, "value": <float>}] | None`. `None`/`[]`
means "no curve — use the flat scalar". Migration `0050`.

**Interpolation** is a pure, DB-free module: `services/term_structure.py` — `TENOR_YEARS`
(label→year-fraction map, the single source; extend it to add 1D/4M/7Y/…), `validate_curve`
(known labels, dedup, finite, vol `>0`), and `interpolate_curve` (linear on the year axis,
flat extrapolation past the ends, single point → constant, empty/None → None).

**Generate** lives in the domain facade `services/domains/pricing_profiles.py`:
`generate_curve_param_rows` (read-only compute) walks `open_otc_positions`, skips delta-one
(`position_requires_pricing_params`), derives each trade's **tenor in years** via
`_tenor_years_for_position` — a numeric year-fraction `maturity` (the QuantArk `T` the engine
prices with) is used directly, else an absolute maturity date resolved from
`compatibility_terms_for_position(...)["product_kwargs"]` under a priority key list
(`maturity_date` / `expiry_date` / `expiry` / `exercise_date` / `settlement_date`) is turned
into **ACT/365**. It interpolates each curve (fallback to the flat Instrument scalar) and
raises `ValueError({"unfilled_trades": [...]})` when a param has neither curve nor scalar
(mirrors `build_assumptions_set`'s `unfilled_underlyings`). `generate_profile_from_curves`
writes a `PricingParameterProfile(source_type="curve")` + one flat row per trade — bound to
the position by **`position_id`** (migration `0051`), with per-row interpolation provenance —
and audits `pricing_parameter_profile.generated_from_curves`.

**Binding.** Because term-structure rows are per-trade (each maturity → its own r/q/vol),
they need a per-position key. `resolve_pricing_parameter_row_for_position` prefers a
`position_id` match before trade-id / underlying, so a curve row resolves uniquely even when
the position has no `source_trade_id` — the live-book case, where several positions share an
underlying and underlying-level resolution is otherwise `ambiguous`. Imported rows have
`position_id=None` and resolve exactly as before (fully backward-compatible). The Pricing
Parameters **POSITION** column renders the bound `#<position_id>`.

**Surfaces.** REST: curve fields on `PUT /api/underlying-pricing-defaults/{underlying}` and
`GET .../underlying-pricing-defaults` (validated server-side via `validate_curve`), plus
`POST /api/pricing-parameter-profiles/from-curves`. Agent: `set_/get_instrument_pricing_defaults`
round-trip curves, and the WRITE+HITL tool `generate_pricing_parameters_from_curves`. Frontend:
Instruments → **Assumptions** tab only — a per-underlying curve editor (tenor `<select>` +
value input, add/remove) and three token-only recharts line charts (one per param, since r/q
sit near 0–5% and vol near 15–40% — a shared y-axis would flatten r/q), plus a "Generate
pricing parameters from curves" button. Pricing Parameters stays a flat table.

### Gotchas

- **Curves feed ONLY the generate step.** `build_assumptions_set` is untouched and still uses
  the flat scalars — do not wire curves into the assumption-set build (deferred by design).
- **The generate tool needs four registrations,** not one: `QUANT_AGENT_TOOLS`
  (`tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`), and all **three**
  structures in `services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES` + `_RISK_LEVEL_BY_TOOL`
  → `"write"` + `_LABEL_BY_TOOL`). `test_hitl.py`'s exact-set guard forces you to update it.
- **`source_type="curve"` is load-bearing** — the generate path does NOT reuse `create_profile`
  (which hardcodes `source_type="agent"` and a generic audit event); it has its own write.
- **`validate_curve` is the single validation seam,** shared by the REST PUT
  (`upsert_underlying_default`) and the agent setter (`set_instrument_defaults`). Volatility
  curves require `> 0`; rate/dividend allow any finite value (rates can be negative).

---

## Position lifecycle events

Two layers, one chokepoint. `services/domains/lifecycle_vocabulary.py` owns
`LIFECYCLE_EVENT_TARGETS` (17 event types → the status each drives; `None` =
non-transitioning), `PRODUCT_LIFECYCLE_EVENTS` (per-family allowlist, all 15 bookable
families explicit), `RETIRED_EVENT_TYPES`, and `EVENT_FIELD_SPECS`. It is pure data —
no DB, no service imports — and `positions.py` re-exports it for the historical import
site. Served to the UI by `GET /api/lifecycle-vocabulary`; the frontend keeps **no**
copy, because the copy it used to keep drifted and lost `settle` and `fixing`.

- **The allowlist is TOTAL.** `create_lifecycle_event` is the only constructor of
  `PositionLifecycleEvent`, so a family missing from the map does not degrade
  gracefully — it loses the event outright, and the only symptom is a `ValueError`
  when someone tries to record something real. `tests/test_lifecycle_vocabulary.py`
  asserts every declared type is reachable (or explicitly retired) and every family in
  `product_builders._REGISTRY` is covered. **Both guards failed before this landed:**
  `open`/`reopen` were reachable by no product, so `CASH_LEG_RULES["open"]` — the
  premium leg — had never fired for any position. Its two tests passed by building
  `PositionLifecycleEvent(...)` directly. **A test that constructs its own subject
  bypasses whatever validates the real one**, so it proves satisfiability, never
  reachability — the same lesson as the arena golden replay and the trader-rfq fix.
- **`book_position` emits `open`,** via the non-committing
  `positions.record_lifecycle_event` seam (`create_lifecycle_event` wraps it and
  commits; booking calls it inside the caller's transaction). `booking.py` imports it
  **function-scope**: positions.py → position_adapter.py → booking.py is a real cycle.
  Consequence: every booking now writes a `premium` cashflow **and** a second audit row
  (`position.lifecycle_event` beside `position.created`) — three exact-set assertions
  had to be updated for that.
- **`open` moves NO status** (target `None`). Making it reachable exposed that its old
  `"open"` target silently overwrote the booked status of a position booked
  `knocked_in` or `closed` — a historical trade imported mid-life. An inception
  *record* must not rewrite what it records. `reopen` is the transition.
- **`expire` emits no cash leg, deliberately.** A terminating event with no amount in
  `event_data` yields `amount=None` → a `needs_amount` row (`generate.py:197`), i.e. a
  permanent phantom obligation for a trade where nothing is owed. `expire` records the
  termination without opening a cash row at all.
  **Precision, measured after an over-broad first draft:** an *explicit*
  `settlement_amount: 0.0` IS expressible and yields a `0.0` / `pending` row — the
  zero→`None` collapse belongs to `_premium_from_position` (the position resolver),
  not the `amount_keys` path. So `expire` is not "the only way to say zero"; it is the
  way to say *nothing is owed* without creating a zero cashflow somebody must still
  release and settle.
- **`barrier_reset` RECORDS a barrier step; it does not apply one.** Lifecycle events
  write `event_data`; pricing reads `Position.product_kwargs`. A reset position still
  prices off its original barrier.
- **One-touch models the touch as `knock_out`, not `knock_in`.** The touch both ends
  the option and creates the obligation; `knock_in` leaves the position alive and has
  no cash rule, which would hide the money until maturity.
- **`autocall`/`coupon_lock` are retired, not deleted** — no family may record them
  (phoenix now uses `knock_out`/`coupon_observation`), but they keep their
  `LIFECYCLE_EVENT_TARGETS` entry and `autocall` keeps its cash rule.
  `_project_status_from_lifecycle` resolves targets with `.get()`, so an unknown type
  reads as *non-transitioning* rather than *unrecognised* — deleting them would make
  historical phoenix rows silently stop closing their positions.

### The agent surface: one generic tool, not one per event type

`record_lifecycle_event` (`tools/positions.py`) records **any** event its position's
family allows, validating against `valid_lifecycle_event_types` rather than keeping a
list of its own — so it can never drift from the vocabulary and never needs extending
when a type is added. It returns `{ok: false, error: ...}` (the message names the legal
types for that family) instead of raising, so a model can recover without guessing.
HITL `"write"`, like its three hardcoded siblings — a lifecycle event is recallable via
`cancel_lifecycle_event`, unlike a booking.

- **`close_position` / `settle_position` / `mark_knockout` each hardcode one event
  type.** They predate the generic tool and stay for their friendlier signatures, but
  they covered only 3 of 17 types — `exercise`, `expire` and `barrier_reset` were
  reachable from REST and the UI and **not from the agent**, which only a live smoke of
  the agent path revealed. **After adding an event type, check the agent can record
  it**; the vocabulary, the REST layer and the tool surface are three different gates.
- **Availability is the third gate, not the last one — discoverability is the fourth,
  and it fails at a DIFFERENT layer than you fix.** With the generic tool available,
  allowlisted and correct, a live 3-probe smoke measured the desk agent recording both
  "expired worthless" and "exercised early" through `settle_position`: the `early` flag
  and the event type were lost, and the worthless expiry booked a `0.0`/`pending` cash
  row somebody must still release. Two distinct causes, each needing its own fix:
  - **The persona chooses by tool description.** Redirecting from the single-event
    tools ("records `settle` and nothing else; if the trade ended some OTHER way, call
    `record_lifecycle_event`") fixed the exercise probe on its own. Every terminating
    event closes the position, so "it is closed now" never discriminates — the
    descriptions have to say so.
  - **The orchestrator never sees a tool description.** It delegates via `task()`, and
    it had written *"Record an OTM expiry **settlement**"* into the delegation before
    any persona existed — because **no skill claimed lifecycle recording**, so there
    was nothing to route to and it improvised from the nearest word it knew. Only the
    `record-lifecycle-event` skill's `routing:` line fixed that probe. Same lesson the
    arena measured (routed skills 64–88%, unrouted 0–23%): **when a model never reaches
    for a working tool, look one level UP from where you think the choice is made.**
  The event menu in `record_lifecycle_event`'s description is **rendered from
  `LIFECYCLE_EVENT_TARGETS` + `EVENT_FIELD_SPECS`** (`_event_menu()`), and the
  redirects from `_redirect_notice()`. Hand-writing them would recreate exactly the
  copy that already drifted in the frontend. `.description` is assigned after the
  decorator stack — it is a declared `BaseTool` field, so plain assignment works,
  unlike `invoke` which `capability_gated` must patch via `object.__setattr__`.
- **`_SETTLEMENT_LEG.amount_keys` is `("settlement_amount", "payoff")`.** `payoff` is a
  fallback and must stay second. Both `mark_knockout` and the UI's knock_out/autocall
  forms collect a "Payoff", and before that key existed the number was written to
  `event_data` and read by nothing — so a desk user or agent who correctly reported the
  KO payoff still got a `needs_amount` row and silently lost the figure.
- **`PositionLifecycleReferenceInput` sets `extra="forbid"`.** Pydantic's default
  `ignore` silently DROPPED a plausible-but-wrong argument: `mark_knockout(
  settlement_amount=900)` returned success with no amount recorded. A loud rejection
  lets a model retry; a silent drop loses the number.
- **All four lifecycle tools carry `_SUMMARY_BUILDERS` entries** sharing one
  `_lifecycle_subject` helper. The interrupt fires before the tool body, so each could
  only see `position_id` and every card read `Run close_position (position_id=27)` —
  a gate in name only. They now read `Close 1000.0 BarrierOption / 000905.SH (position
  #1, now open, reason: client unwind)`. `_lifecycle_subject` **never raises** (a card
  that throws breaks the gate it serves): an unknown position degrades to
  `position #999999 (not found)`, a `source_trade_id` to `trade SB-2026-014`.
  **Test them through `_summary_for` WITH a `description` present** —
  HumanInTheLoopMiddleware stamps boilerplate on every action request, and a builder
  that loses to it is unreachable in the live path however well its unit test passes.
