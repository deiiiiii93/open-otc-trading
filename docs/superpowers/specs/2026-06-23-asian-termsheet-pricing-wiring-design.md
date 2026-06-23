# Asian Option: Termsheet Pricing-Wiring (booked schedule → position pricing)

**Date:** 2026-06-23
**Status:** Draft — awaiting user review
**Repo touched:** `open-otc-trading` (integration only; QuantArk already supports weighted/dated Asian pricing)
**Sub-project:** **D-OTC / task #13** — the deferred half of the Asian effort.

> This is the final sub-project of the Asian-option effort begun 2026-06-22. Prior, merged work:
> - **A** — observation-frequency picker (3 surfaces) + `_build_asian` frequency→count mapping.
> - **B** — Asian `fixing` lifecycle event + on-demand schedule generator.
> - **C+D (QuantArk)** — calendar-accurate + weighted averaging in the pricing engine.
> - **C+D (OTC, partial)** — `schedules.asian_observation_records` helper; `asian_averaging_dates.weight`
>   column (migration 0031); booking round-trip of explicit dates.
>
> What was **deferred** (this spec): making the stored, weighted, dated schedule actually reach
> **position pricing**. Today a booked weighted Asian silently prices as a uniform average over
> `num_observations` evenly-spaced points — the booked dates and weights are ignored.

---

## 1. Problem

The position pricing chokepoint — used by **all** valuation surfaces (risk, greeks, scenario, backtest) —
is:

```
build_product_for_position(position, market)
  → _build_termsheet_for_position
    → compatibility_terms_for_position(position)   # product_kwargs from product.raw_terms["terms"]
    → _build_termsheet(...)                         # _drop_past_observations + _add_observation_times + normalize
  → build_product_from_termsheet(termsheet)         # QuantArk registry → AsianOption
```

Three gaps make the booked Asian schedule invisible to this path:

1. **Booking doesn't materialize a schedule from the frequency picker.**
   `_replace_asian_schedule` only persists *explicit* `averaging_dates`/`observation_dates`. A position
   booked with just `averaging_frequency` (sub-project A) writes **zero** dated rows — there is nothing
   to price from.

2. **The booked schedule never enters `product_kwargs`.**
   `compatibility_terms_for_position` emits only `averaging_frequency` / `num_observations`. The
   `asian_averaging_dates` table (dates + weights) is read back **only** by `get_asian_schedule`
   (UI + the sub-project B lifecycle generator). It never reaches the termsheet → never reaches QuantArk.

3. **In-progress positions would crash if naively wired.**
   QuantArk (`asian_option.py:377`) **raises `ValidationError`** for a *past* observation date with no
   `observed_price`, and there is no stored realized price anywhere.

**Consequence:** every booked weighted/dated Asian is silently mispriced as a uniform average — exactly
the error this whole effort exists to prevent.

QuantArk itself is **ready**: the registry coerces `product_kwargs["observation_records"]` (a list of
dicts) into `AsianObservationRecord(observation_time/observation_date, observed_price, weight)` and prices
the weighted, dated average (with realized fixings) correctly. The entire remaining work is **OTC-side**.

---

## 2. Decisions (settled during brainstorming)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | In-progress (mid-life) positions | **Exact now** | Price the realized + unrealized legs correctly, not an approximation. |
| 2 | Source of a realized fixing's price | **Stored snapshot at fixing** | A fix is captured once its date passes (from `MarketQuote` close) and is **immutable** thereafter — matches how contractual Asian fixings work; immune to later market-data revisions. |
| 3 | Where the priced schedule lives | **In `product_kwargs.observation_records`** | Matches every other product (autocallable `ko/ki_observation_schedule`): pricing stays **session-free** and consistent; the valuation-relative past/future split happens in the existing `_build_termsheet` path. |

A consequence of Decision 3: the realized price is stored **inside the `product_kwargs` records**, not as a
new table column. The `asian_averaging_dates` table is kept as the **UI/edit projection**, synced from the
same source.

---

## 3. Architecture & data flow

```
BOOKING ──► product_kwargs.observation_records = [{observation_date, weight, observed_price?}, …]
              • generated from averaging_frequency + maturity + optional weights
                (schedules.asian_observation_records), OR from explicit dates when given
              • mirrored to asian_averaging_dates (UI projection)
              • eager-capture already-past dates (a session exists at booking)
                                   │
CAPTURE  ──► capture_due_asian_fixings(session, position_id, as_of = today)
              • for each record with observation_date ≤ today AND observed_price is null:
                  observed_price = MarketQuote close as-of observation_date (for the position's instrument)
              • immutable once written; idempotent; same fn over all positions == backfill
                                   │
PRICING  ──► _build_termsheet (has market.valuation_date + calendar/day-count context)
              • observation_time = year_fraction(valuation_date, observation_date)
              • observation_time ≤ 0 (past-relative):  keep stored observed_price;
                  if missing → DROP record + renormalize remaining weights (never crash)
              • observation_time > 0 (future-relative): force observed_price = None
              • drop num_observations when records are present
                                   │
              ──► build_product_from_termsheet ──► AsianOption weighted/dated pricing
```

Capture keys off **wall-clock today**, never the pricing `valuation_date`, so historical/as-of pricing is
unaffected by capture state.

---

## 4. Components

### Component A — Schedule materialization at booking

**Where:** `backend/app/services/domains/position_terms.py` (the AsianOption branch that currently calls
`_replace_asian_schedule`) and the product-terms write path that produces `product_kwargs`.

**What:**
- When an Asian is booked/updated, build the dated schedule:
  - **explicit dates given** (`averaging_dates`/`observation_dates`) → use them (with any supplied weights);
  - **else** → generate from `averaging_frequency` + `maturity_years` (+ optional `weights`) via
    `schedules.asian_observation_records(start=trade_start, maturity_years=…, frequency=…, weights=…)`.
- Write the result into **`product_kwargs.observation_records`** (the priced source of truth) as a list of
  `{observation_date: ISO, weight: float|None, observed_price: float|None}`.
- Mirror the same dates/weights to `asian_averaging_dates` (existing `_replace_asian_schedule`) so the UI
  schedule view/edit is unchanged.
- **Eager-capture** any already-past observation dates immediately (booking has a session) by calling
  Component C's capture function — so seasoned/backdated imports never carry uncaptured fixings.

**Notes:**
- `start` (trade start) and `maturity_years` already exist on the position/terms.
- Weights default to `None` (uniform) when the user supplies none — preserving today's prices (see §6).

### Component B — Pricing wiring (core)

**Where:** `backend/app/services/quantark.py`, a new Asian branch in `_build_termsheet` (alongside the
existing `_add_observation_times` / `_drop_past_observations` logic, which already holds
`market.valuation_date` and the `_observation_context` calendar/day-count helpers).

**What:** transform `product_kwargs["observation_records"]` into QuantArk-ready records:
1. `observation_time = year_fraction(valuation_date → observation_date)` using the existing
   `_observation_time(context, observation_date)` helper (calendar-aware, same as schedules).
2. **Past-relative** (`observation_time ≤ 0`): keep the stored `observed_price`. If it is `null`, **drop the
   record** and renormalize the remaining weights — never emit a past record without a price (that is the
   `ValidationError` crash). Log dropped-uncaptured at debug.
3. **Future-relative** (`observation_time > 0`): force `observed_price = None` (a stored real-world fixing
   that is still future relative to a historical valuation must not be treated as realized).
4. Emit each record as `{observation_time, observed_price?, weight?}`; **remove `num_observations`** from the
   kwargs when records are present (records take precedence in QuantArk; dropping avoids ambiguity).
5. **Empty/absent** `observation_records` → leave kwargs untouched (today's `num_observations` behavior;
   full back-compat for non-Asian and un-migrated Asian positions).

**Why `observation_time`, not `observation_date`:** `AsianObservationRecord` is a plain `@dataclass` with no
date coercion, and the existing autocallable path likewise pre-resolves year-fractions via the calendar
context. Passing `observation_time` reuses the day-count/calendar logic and sidesteps date-string parsing.

### Component C — Observed-price capture + backfill

**Where:** `backend/app/services/domains/positions.py` (near the sub-project B fixing helpers), a new
`capture_due_asian_fixings(session, position_id, *, as_of=None)`.

**What (idempotent):**
- `as_of` defaults to wall-clock today.
- Load the position's `observation_records` (from `product_kwargs`).
- For each record with `observation_date ≤ as_of` **and** `observed_price is null`: resolve the position's
  instrument, query `MarketQuote` for the close as-of `observation_date` (resolution rule: latest
  `as_of ≤ date`, id tie-break — the documented `MarketQuote` semantics), and write it **once**.
- Persist back to `product_kwargs.observation_records` (and mirror `observed_price` to
  `asian_averaging_dates` if we surface it in the UI).
- If no `MarketQuote` exists for a date, leave `observed_price` null (→ dropped at pricing) and log.

**Backfill = the same function.** A one-shot maintenance pass calls `capture_due_asian_fixings` for every
existing Asian position — no separate backfill code path.

**Surfaces:**
- `POST /api/portfolios/{pid}/positions/{id}/asian-fixings/capture` (mirrors the sub-project B endpoint).
- An agent tool ("record due Asian fixings").
- Optional: annotate the sub-project B `fixing` lifecycle event payload with the captured price for
  provenance (nice-to-have; does not gate the realized-pricing path).

**Out of scope:** automatic/cron-scheduled capture — capture stays an explicit action.

---

## 5. Instrument & price resolution

- Position → underlying → instrument: reuse the existing resolver (`_instrument_id_for_row` in
  `pricing_profiles.py`, or the equivalent instrument lookup) so capture finds the right `MarketQuote` series.
- "Close as-of date": `MarketQuote` filtered by `instrument_id`, `as_of ≤ observation_date`, ordered by
  `as_of` desc then `id` desc, take first `.price` (price_type `close`). A thin helper, unit-tested.

---

## 6. Edge cases & error handling

| Case | Behavior |
|------|----------|
| Unweighted, frequency-only Asian (the common booking) | Prices **byte-identical** to today: uniform weights + the same observation count. **Pinned by a regression test.** |
| Captured (real-world-past) date that is *future* relative to a historical valuation | Priced as future (`observed_price` nulled). Explicit test. |
| Past-relative date with no captured price (uncaptured / no MarketQuote) | Record dropped, weights renormalized, **no crash**. Explicit test. |
| No `MarketQuote` for a date at capture | `observed_price` stays null; logged; resolved later if data arrives. |
| Non-Asian position / Asian with no `observation_records` | Untouched — existing `num_observations` path. |
| `cross_channel_equivalence` suite | Must stay green (agent↔try-solve↔import parity unaffected for the unweighted default). |

---

## 7. Testing strategy

TDD throughout (red → green → refactor). New/updated suites:

- `tests/test_asian_pricing_wiring.py` — Component B: weighted records → weighted price (vs QuantArk
  reference); future-nulling; uncaptured-past drop+renormalize; `num_observations` removed when records
  present; empty-records back-compat.
- `tests/test_asian_fixing_capture.py` — Component C: idempotent capture; immutability of a captured price;
  MarketQuote close-as-of resolution; missing-quote → null; backfill over many positions.
- `tests/test_asian_schedule_materialization.py` — Component A: frequency-only booking writes
  `observation_records`; explicit-dates path preserved; eager-capture of already-past dates; UI projection
  mirrored.
- **Regression anchor**: unweighted frequency-only Asian prices byte-identical to pre-change; full suite
  + `tests/test_cross_channel_equivalence.py` green.

**Review gate:** `zenmux-codex-review-loop` (GPT-5.5 xhigh, **max 3 loops**) at each component boundary, per
the standing `/goal` (independent reviewer; implement straight through).

---

## 8. Out of scope

- Auto-scheduled/cron capture (capture is explicit).
- Migrating the UI off `asian_averaging_dates` (kept as a synced projection).
- RFQ / try-solve **pricing** wiring (position-pricing only — the universal chokepoint).
- Any QuantArk change (engine already supports weighted/dated/realized Asian pricing).

---

## 9. Files (anticipated)

| File | Change |
|------|--------|
| `backend/app/services/domains/position_terms.py` | Component A: materialize `observation_records` from frequency/explicit dates; eager-capture |
| `backend/app/services/domains/schedules.py` | reuse `asian_observation_records` (already present) |
| `backend/app/services/quantark.py` | Component B: Asian branch in `_build_termsheet` (records → observation_time, past/future split, drop num_observations) |
| `backend/app/services/domains/positions.py` | Component C: `capture_due_asian_fixings` + (optional) fixing-event annotation |
| `backend/app/main.py` | capture endpoint |
| agent tool registry + SKILL (if a new tool) | "record due Asian fixings" tool wiring + orchestrator routing line |
| `backend/tests/test_asian_*` | new suites above |

> No new migration is required for pricing (realized price lives in `product_kwargs`). If we choose to
> surface `observed_price` in the UI projection, that is an additive nullable column on
> `asian_averaging_dates` — decided during implementation, not a blocker.
