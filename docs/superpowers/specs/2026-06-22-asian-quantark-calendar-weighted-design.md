# Asian Option: Calendar-Accurate Observations + Weighted Averaging (QuantArk)

**Date:** 2026-06-22
**Status:** Draft — awaiting user review
**Repos touched:** `quant-ark` (primary), `open-otc-trading` (integration)
**Sub-projects:** C (calendar accuracy) + D (weighted averaging), combined per decision.

> This is sub-project **C+D** of a larger Asian-option effort. The full effort is:
> - **A** — observation-frequency picker (3 surfaces) + `_build_asian` frequency→count mapping (OTC-only).
> - **B** — Asian `fixing` lifecycle event + on-demand schedule generator (OTC-only).
> - **C** — QuantArk calendar-accurate observations (this spec).
> - **D** — full weighted averaging across QuantArk + OTC (this spec).
>
> User chose to start with **C+D** (deepest quant-engine correctness first). A and B are
> deferred to their own specs and are **out of scope here**.

---

## 1. Problem

Two correctness gaps in how Asian options are scheduled and priced, both discovered by
code investigation on 2026-06-22:

### C. "DAILY" is a flat 252 and nothing is calendar-aware

- `_build_asian` (`open-otc-trading/backend/app/services/domains/product_builders.py:475`)
  computes `num_observations = round(maturity * 252)` for DAILY — a hardcoded literal.
- A configurable `bus_days_in_year` (default 252) exists on `PricingEnvironmentSnapshot`
  (`backend/app/schemas.py:244`) and on QuantArk's `PricingEnvironment`, but it is **never**
  used to derive the observation count — only for year-fraction date math.
- QuantArk's own frequency map hardcodes `daily→252`
  (`quant-ark/quantark/asset/equity/product/option/snowball_helpers.py:74`).
- Even with a real count, `AsianOption.get_observation_times` generates **uniform** times via
  `np.linspace(0, T, n+1)[1:]` (`asian_option.py:284`) — it ignores the trading calendar.
- Net effect: a 6-month DAILY Asian is always 126 evenly-spaced points end-to-end, regardless
  of the actual SSE business-day calendar or any engine config.

### D. Weighted averaging is stored but never priced

- `EquityAsianObservation` has a `weight` column (`backend/app/models.py:883`, migration 0018)
  that is read/written to the DB, but:
  - QuantArk's `AsianObservationRecord` has **no** `weight` field (`asian_option.py:33-49`).
  - Every analytical method divides by `n` (equal 1/n weights); the MC engine uses plain
    `np.mean` (`asian_option_mc_engine.py` path average).
  - Position pricing **never passes stored observations into QuantArk** — the termsheet build
    (`backend/app/services/domains/quantark.py:~742`) takes `product_kwargs` only from
    `raw_terms["terms"]`, which excludes the observations table.
  - `asian_averaging_dates` (the position-level table, `models.py:1071`) has **no** weight column.
- Net effect: weighted Asian averaging is inert — weights are stored and silently ignored.

---

## 2. Goals / Non-Goals

### Goals

1. **Calendar accuracy (C):** Asian observation *times* reflect the actual trading calendar and a
   configurable trading-days-per-year, end-to-end (OTC build → QuantArk pricing), not a flat 252
   with uniform spacing.
2. **Weighted averaging (D):** Per-observation weights are modeled in QuantArk, consumed by the MC
   engine and by the analytical methods that admit a verified weighted form, and wired through OTC
   so stored weights actually reach pricing.
3. **No silent mis-pricing:** Any analytical method that cannot price weights correctly must
   **reject** non-uniform weights with a clear, actionable error — never silently ignore them.
4. **All weighted-moment math is verified** before it ships (see §7).

### Non-Goals

- Sub-projects A (picker) and B (lifecycle/fixing) — separate specs.
- A weights *editor UI* — backend correctness first; the Asian schedule weights UI is a thin
  follow-up step listed in §6.4, not a gate for this spec's core.
- Changing the payoff definitions or floating-strike symmetry logic beyond what weights require.
- Stochastic-vol or non-lognormal Asian models.

---

## 3. Design Overview

```
                 OTC (open-otc-trading)                      QuantArk (quant-ark)
  ┌──────────────────────────────────────────┐    ┌────────────────────────────────────┐
  │ _build_asian                              │    │ AsianObservationRecord(+weight)     │
  │   start/maturity/frequency  ─────────────────► │ AsianOption(observation_records)    │
  │   schedules.asian_observation_records()   │    │   get_average (weighted)            │
  │   (SSE-calendar dated + weighted)         │    │   resolve_observations              │
  │                                           │    │     → (past_prices, past_weights,   │
  │ quantark.py termsheet build               │    │        future_times, future_weights)│
  │   wires asian_averaging_dates(+weight) ───────►│                                     │
  │   into observation_records                │    │ Analytical engine                   │
  │                                           │    │   TW / geometric: weighted moments  │
  │ migration: asian_averaging_dates.weight   │    │   Levy / Curran: reject non-uniform │
  │                                           │    │ MC engine: weighted path average    │
  └──────────────────────────────────────────┘    └────────────────────────────────────┘
```

The lever that makes C cheap: the analytical engine **already** prices off a `future_times`
list extracted from `resolve_observations` (`asian_option_analytical_engine.py:195-217`). Non-
uniform / calendar-accurate times are therefore already consumable — the only hardcoding is in
*schedule generation*. C replaces uniform generation with calendar-accurate dated records; D
threads weights alongside those same times.

---

## 4. C — Calendar-Accurate Observations

### 4.1 Configurable trading-days-per-year (QuantArk)

- Add a module-level constant `DEFAULT_TRADING_DAYS_PER_YEAR = 252` in
  `snowball_helpers.py` and reference it in the frequency map (replace the literal `252`).
- Where QuantArk derives a *count* from a frequency for uniform fallback, source the daily count
  from `pricing_env.bus_days_in_year` when a pricing env is available, else the default constant.
- The frequency→periods map becomes the single source of truth:
  `{"daily": <bus_days>, "weekly": 52, "monthly": 12, "quarterly": 4, "semi_annual": 2}`.

### 4.2 Calendar-accurate schedule generation (OTC is the source of dates)

Rationale: OTC already owns the SSE business-day calendar (`schedules.py`:
`china_sse_business_days`, `roll_to_business_day`, `periodic_observation_dates`). QuantArk
should **consume** dated observations, not reinvent a calendar.

- New pure helper in `open-otc-trading/backend/app/services/domains/schedules.py`:
  `asian_observation_records(*, start, maturity_years, frequency, weights=None) -> list[dict]`
  returning records `{observation_date, sequence, weight}` where dates are:
  - DAILY → every SSE business day in `(start, maturity]`.
  - WEEKLY → every 7 calendar days from `start`, each rolled forward to the next SSE business day.
  - MONTHLY/QUARTERLY/SEMI_ANNUAL → `periodic_observation_dates` with `months_step` 1/3/6.
  - `weights` defaults to uniform (`None` → equal); if provided, length must match the date count.
- `_build_asian` passes these as QuantArk `observation_records` (dated) **instead of** a bare
  `num_observations`, so the analytical engine prices off real spacing. When `start`/maturity are
  unavailable (e.g. a stub agent build), fall back to `num_observations` from the §4.1 map.

### 4.3 QuantArk consumption

- `AsianObservationRecord.resolve_time` already converts `observation_date` → year fraction via
  `calculate_year_fraction` honoring `day_count_convention`, `bus_days_in_year`, and `calendar`
  (`asian_option.py:78-94`). No change needed beyond §5 (adding `weight`).
- `get_observation_times` keeps `linspace` only as the **uniform fallback** when no dated records
  are supplied. Document it as approximate.

### 4.4 Acceptance (C)

- A DAILY 0.5y Asian priced through OTC uses the *actual* count of SSE business days in the
  window (not a flat 126) and non-uniform times across holidays.
- `bus_days_in_year` set to e.g. 244 changes the daily uniform-fallback count accordingly.
- Existing uniform-schedule tests (count-only) still pass via the fallback path.

---

## 5. D — Weighted Averaging

### 5.1 Data model (QuantArk)

- Add `weight: Optional[float] = None` to `AsianObservationRecord` (`asian_option.py:33`).
  `None` ⇒ uniform. `validate()` rejects `weight <= 0`.
- `resolve_observations` returns weights alongside prices/times. New return contract
  (back-compatible via an added field, not a reshuffle):
  `(past_prices, past_weights, future_times, future_weights, total_observations)`.
  - Update the single in-repo caller (`_extract_params`) accordingly.
  - Normalization rule: weights are normalized to sum to 1 across **all** observations
    (past + future) at resolve time; uniform ⇒ each `1/n`. Document this invariant centrally.

### 5.2 Weighted averaging primitives

- `AsianOption.get_average(prices, weights=None)`:
  - Arithmetic: `Σ wᵢ pᵢ` (weights normalized; uniform ⇒ `mean`).
  - Geometric: `exp(Σ wᵢ ln pᵢ)` (uniform ⇒ existing log-mean).
- `get_past_average` and `_handle_near_expiry`'s blended estimate use the weighted forms.

### 5.3 MC engine (reference implementation)

- Path average becomes `Σ wᵢ S_tᵢ` (arithmetic) / `exp(Σ wᵢ ln S_tᵢ)` (geometric), using the
  resolved per-observation weights aligned to the simulated observation grid.
- This is the **ground-truth reference** all analytical weighted forms are validated against.

### 5.4 Analytical methods — weighted moments

Average `A = Σ wᵢ S_tᵢ` (arithmetic) with `Σ wᵢ = 1`. Under lognormal `S_tᵢ`:

- **M1** = `Σ wᵢ E[S_tᵢ]`, with `E[S_tᵢ] = S·e^{b tᵢ}` (plus the realized past contribution
  `Σ_past wⱼ S_Aⱼ`).
- **M2** = `Σᵢ Σⱼ wᵢ wⱼ E[S_tᵢ S_tⱼ]`, with
  `E[S_tᵢ S_tⱼ] = S²·e^{b(tᵢ+tⱼ)}·e^{σ² min(tᵢ,tⱼ)}`.
- Turnbull-Wakeman then matches a lognormal to `(M1, M2)` exactly as today, but with the weighted
  moments above replacing the `/n`, `/n²` forms (`asian_option_analytical_engine.py:628-669`).

Coverage decision (per user — *target full coverage, verified; reject what can't be verified*):

| Method | Weighted plan |
|--------|---------------|
| **TURNBULL_WAKEMAN** | Implement weighted M1/M2 (above). Primary arithmetic method. |
| **DISCRETE_HHM** | Implement weighted `E[A]`, `E[A²]` (same moment structure, HHM matching). |
| **KEMNA_VORST** | Continuous geometric — weights only meaningful for discrete; reject non-uniform (continuous has no per-fixing weight). |
| **GEOMETRIC_DISCRETE** | Implement weighted geometric: `ln A = Σ wᵢ ln S_tᵢ`, variance `Σᵢ Σⱼ wᵢ wⱼ σ² min(tᵢ,tⱼ)`. |
| **LEVY** | Attempt weighted moment-matching; **gate on §7 verification**. If the zenmux review cannot validate the weighted Levy form against MC within tolerance, **reject** non-uniform weights ("use TURNBULL_WAKEMAN or MC"). |
| **CURRAN** | Same as Levy — attempt, gate on verification, else reject non-uniform weights. |

- Rejection is a `ValidationError` raised in `_check_method_compatibility` when the product carries
  non-uniform weights and the selected method is in the reject set.
- Method **auto-selection** (`_select_method`) routes weighted arithmetic to TURNBULL_WAKEMAN
  (already the default), so the default path is always a supported weighted method.

### 5.5 OTC integration (the silent-gap fix)

1. **Migration:** add nullable `weight` (Float) to `asian_averaging_dates`
   (new alembic revision off the current head; migration-local Core table only, per the
   "no live ORM in migrations" rule).
2. **Persist weights:** `position_terms._replace_asian_schedule` writes the `weight` column;
   `get_asian_schedule` / product-details readers surface it.
3. **Wire into pricing:** the termsheet build for a position
   (`quantark.py` `_build_termsheet_for_position` / `compatibility_terms_for_position`) must
   reconstruct `observation_records` (dated + weighted) from `asian_averaging_dates` and pass them
   into the QuantArk `AsianOption`, so weights and real dates reach the engine. This closes the gap
   where observations were stored but never priced.
4. `_build_asian` (agent/booking build path) passes weighted dated records from §4.2 when weights
   are supplied; uniform otherwise (byte-identical to today's behavior for the uniform case).

### 5.6 Acceptance (D)

- Weighted MC and weighted TW agree within MC standard error on a battery of weight vectors
  (front-loaded, back-loaded, single-fixing-dominant).
- Uniform weights reproduce **byte-identical** prices to the pre-change code (regression guard).
- A non-uniform-weight product priced via LEVY/CURRAN (if unverified) raises the documented
  rejection error; via TW/MC it prices.
- A position with stored non-uniform weights now produces a different PV than the equal-weight
  case (proving weights reach pricing) — using **non-default** weight values per the
  characterization-test lesson.

---

## 6. Work Breakdown

### 6.1 QuantArk
- `AsianObservationRecord.weight` + `validate`.
- `resolve_observations` / `_resolve_from_records` / `_resolve_from_legacy` weight plumbing +
  normalization invariant.
- `get_average` / `get_past_average` weighted.
- MC engine weighted path average.
- Analytical: weighted TW, HHM, geometric-discrete; KEMNA_VORST/LEVY/CURRAN reject (LEVY/CURRAN
  conditionally, post-verification).
- `_extract_params`, `_handle_near_expiry`, `_check_method_compatibility` updates.
- `snowball_helpers` trading-days constant + `bus_days_in_year` sourcing.

### 6.2 open-otc-trading backend
- `schedules.asian_observation_records` (dated + weighted, DAILY/WEEKLY/MONTHLY/QUARTERLY/SEMI_ANNUAL).
- `_build_asian` passes dated/weighted records (fallback to count map).
- Migration: `asian_averaging_dates.weight`.
- `position_terms` persist/read weight.
- `quantark.py` termsheet build wires observations (dated + weighted) into the product.

### 6.3 Tests
- QuantArk: weighted-moment unit tests vs MC; uniform-weight byte-identical regression; calendar
  spacing tests; rejection-path tests.
- OTC: `test_product_builders` calendar/weighted assertions with **non-default** values; migration
  up/down; position-pricing-uses-weights integration test.

### 6.4 Follow-up (not gating)
- Weights editor UI in the Asian schedule component (sub-project D-UI, after backend lands).
- Sub-projects A and B (separate specs).

---

## 7. Math Verification Gate (required)

The weighted-moment derivations are the highest-risk part of this work. Before any weighted
analytical method is considered done:

1. **Reference check:** each weighted analytical price is validated numerically against the
   weighted **MC** engine across the weight battery (§5.6), within MC standard error.
2. **`zenmux-codex-review-loop` (1 loop):** run the skill over the diff containing the weighted
   moment math (GPT-5.5 xhigh) to cross-check the derivations and catch algebra/edge-case errors.
   Apply its findings. (User-directed verification method.)
3. **`model-researcher` skill:** cross-check the weighted Turnbull-Wakeman / geometric moment
   formulas against authoritative sources (Haug, *The Complete Guide to Option Pricing Formulas*;
   Turnbull-Wakeman 1991; Curran 1994) to confirm the weighted generalization is standard.
4. Any method failing (1)–(3) moves to the **reject non-uniform weights** set rather than shipping.

---

## 8. Isolation, Risk, Coupling

- **Worktrees (both repos):** implement in a `quant-ark` worktree and an `open-otc-trading`
  worktree — the OTC repo HEAD/branches are shared with concurrent sessions (memory:
  `feedback_subagent_exec_worktree`, `git_stash_shared_repo_gotcha`). Wire QuantArk via
  `QUANTARK_PATH`/PYTHONPATH to the quant-ark worktree (memory:
  `project_backtest_module`, `project_quantark_packaging`).
- **`python -c` import trap:** the venv `.pth` imports QuantArk from the MAIN checkout, not a
  worktree — use `PYTHONPATH=<wt>/backend` or pytest for any cross-repo check.
- **Migration vs live DB:** apply the new revision to `data/open_otc.sqlite3`; verify boot
  incremental-schema repair still passes (memory: instrument/scenario projects).
- **Back-compat:** uniform-weight + count-only path must stay byte-identical — it's the default
  for every existing Asian and the regression anchor.
- **Pre-existing test failures** (env-dependent) should be baselined before/after so they aren't
  attributed to this change.

---

## 9. Open Questions (resolve during planning)

- Exact tolerance for MC-vs-analytical agreement (proposed: 3× MC standard error).
- Whether weighted geometric should also gate on verification or is considered low-risk (the
  log-moment generalization is exact, so proposed: low-risk, MC-checked only).
- Whether to expose `averaging weights` as a first-class booking term now or keep them
  product/position-level until the D-UI follow-up.

---

## 10. Implementation status (2026-06-22, branch feat/asian-cd, NOT merged)

Implemented across two worktrees (quant-ark `qa-asian-cd`, open-otc-trading `oot-asian-cd`),
each change TDD'd and gated by `zenmux-codex-review-loop` (GPT-5.5 xhigh, ≤3 loops) as the
independent reviewer.

**Done**
- **QuantArk C+D** (7 commits): weight model + weighted resolve/average; weighted MC reference;
  weighted Turnbull-Wakeman + geometric-discrete (validated vs MC); Levy/Curran/HHM/Kemna-Vorst/
  floating-strike reject non-uniform weights; configurable trading-days. Math gate: 3 loops
  (3 real fixes + 1 false positive + 2 validation fixes). 92 Asian tests green.
- **OTC C+D integration** (task 9, contained part): `schedules.asian_observation_records`
  (SSE-calendar, dedupes rolled collisions); `asian_averaging_dates.weight` (model + migration
  0031 + persistence round-trip). OTC gate clean.
- **Sub-project A**: `_build_asian` full DAILY/WEEKLY/MONTHLY/QUARTERLY/SEMI_ANNUAL→count map;
  Observation Frequency picker on Booking + Client RFQ + Try-to-Solve (key `averaging_frequency`).
  Gate clean first pass; tsc + vitest green.
- **Sub-project B**: `fixing` lifecycle event + `generate_asian_fixing_schedule` service +
  `POST .../asian-fixing-schedule` endpoint; idempotent + per-position row lock. Gate: 3 loops clean.

**Deferred** (own spec): the §5.5 termsheet pricing-wiring — making stored position-level
weighted/dated observations reach *position pricing*. `build_product_from_termsheet` ignores
observation data (position Asian pricing still uses default num_observations=12), `build_product_for_position`
lacks a session, and in-progress fixings need observed_price (not stored). High hot-path/equivalence
risk; warrants its own brainstorm/spec/review cycle.
