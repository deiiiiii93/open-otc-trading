# Design: Unified Product Schema Across Business Workflows

Status: Draft for review · Author: desk eng · Date: 2026-05-30

> **Status update (2026-06-01): COMPLETE.** All four channels (RFQ, try-solve, OTC
> import, direct/agent) reach the single producer `build_product`. Cross-channel
> equivalence is pinned by `tests/test_cross_channel_equivalence.py` (RFQ ≡ agent
> byte-identical; try-solve & OTC import structural). OTC import is validate-and-wrap
> (`build_product(prebuilt=True)`), not re-synthesized, because it carries
> heterogeneous per-date schedules — a deliberate deviation from "retire
> position_adapter synthesis". The "remove dead builder code" cleanup was a verified
> no-op: each channel retired its own dead code during migration, and family
> derivation was already consolidated onto the canonical product.

## Background

Every product-shaped defect we have chased recently is the same root cause in
different clothes: **the codebase has no single way to turn workflow input into a
validated product.** Each workflow grew its own "raw input → QuantArk
`product_kwargs`" builder, and those builders drift apart. The drift is the bug.

Concrete evidence from the last few sessions:

- **"Booking test 4" loop.** Direct snowball booking looped on `book_position`
  because `build_product`'s output shape, the booking layer's expectations, and
  the family allowlist disagreed. Fixed (commit `10a555f`) by deriving the family
  from `quantark_class`, carrying accrual dates, and routing booking through the
  one rich builder.
- **Two snowball synthesizers.** `product_builders._build_snowball` (raw terms →
  schedules) and `booking._normalize_snowball_terms` (tidy already-built kwargs)
  diverged; the second could not synthesize schedules. Now consolidated into
  `build_product`.
- **RFQ snowballs cannot book at all.** The RFQ path has its *own* product builder
  (`COMMON_TEMPLATES` + the NL drafter), which emits a levels-only `barrier_config`
  with no observation schedules and never collects the inputs needed to synthesize
  them. A bare-template snowball fails at the **quote** step
  (`validate_rfq_terms` → "KO observation dates or schedule required"), long before
  booking.

These were patched locally. This document proposes the structural fix: **one
producer of canonical products, fed by thin per-channel adapters.**

## Current state — four independent builders

| Builder | Module | Input | Synthesizes schedules? | Used by |
|---|---|---|---|---|
| `build_product` (registry) | `services/domains/product_builders.py` | structured economic terms | ✅ (the complete one) | direct agent booking; booking normalize/repair (post-`10a555f`) |
| RFQ templates + NL drafter | `services/rfq.py` (`COMMON_TEMPLATES`, `draft_from_natural_language`) | template + solved unknown | ❌ levels only | RFQ desk UI, client form, client chat, agent RFQ tools |
| try-solve row builder | `services/try_solve.py` (`_product_kwargs_for_row`) | Excel/workbook row | partial | try-solve sandbox (no booking) |
| import adapter | `services/position_adapter.py` | OTC import row | ✅ | `import_otc_positions` |

Four builders, three different behaviours for the *same* product family. There is
already **one** thing they must all satisfy — `validate_quantark_build`
(`services/quantark.py`), which builds the quad-engine termsheet. That validator
is the de-facto canonical schema; nothing else is shared.

The **storage** layer is independently converging via the in-flight
[Position Product Refactor](./2026-05-28-position-product-refactor-design.md):
a normalized `products` root table + family detail tables, with
`products.raw_terms` holding "the complete normalized QuantArk constructor
snapshot." That work unifies *persistence*. It does **not** unify *construction* —
which is this document's scope.

## Goals

1. Exactly one canonical in-memory product representation, produced by exactly one
   builder, validated by exactly one gate, persisted by exactly one storage path.
2. Every business workflow (RFQ, direct booking, OTC import, try-solve) reaches
   that canonical product through a **thin adapter**, never its own builder.
3. Retire the bespoke builders (`COMMON_TEMPLATES` implicit shape,
   `try_solve._product_kwargs_for_row`, `position_adapter` synthesis) as each
   channel migrates.
4. Make the per-family **term contract** (the minimal economic inputs a family
   needs) explicit and shared, so a channel either collects it or fails with a
   precise, fillable list — never with a downstream "schedule required" mystery.
5. Fix the RFQ snowball gap as a *consequence* of unification, not a side quest.

## Non-goals

- **Do not unify the inputs.** A workbook row, an NL sentence, a desk form, and a
  structured agent payload are legitimately different. Unify the *output* and the
  *builder*; keep per-channel input formats and adapters distinct.
- **Do not invent a new schema object.** The canonical schema already exists
  implicitly as "what `validate_quantark_build` accepts." Promote it; don't
  replace it.
- **Do not block on or duplicate the storage refactor.** Construction unification
  lands first and independently; storage normalization keeps its own timeline.
- No big-bang cutover. No removal of `positions.product_kwargs` /
  `Product.raw_terms` compatibility fields here (owned by the storage refactor).

## Key design decisions (proposed, for stakeholder lock)

1. **Canonical product = a build result that carries BOTH identity and
   diagnostics.** Neither existing type suffices: `ProductSpec`
   (`products.py:33`) has identity (`product_family`, `underlying`, `currency`,
   `components`) + persistence metadata but **no** `ok`/`missing`/`validation`/
   `engine_name`; `BuildResult` (`product_builders.py:44`) has diagnostics but
   **no** identity. The carrier is defined explicitly in
   [Canonical carrier API](#canonical-carrier-api) below — **locked** as `BuildResult`
   extended with a `product_spec: ProductSpec` field (identity + `terms`), keeping
   `ok`/`missing`/`warnings`/`validation`/`engine_name` on `BuildResult`. The
   `build_product(...) -> ProductSpec` shorthand used earlier in diagrams is
   incorrect; it returns the carrier, and `.product_spec` is valid only when `ok`.
2. **`build_product` is the single producer.** It already dispatches per family. It
   becomes the only path that emits a canonical product — but see decision 8: its
   "already-built" detection must be hardened first.
3. **`product_family` is derived from `(quantark_class, components)`, never
   supplied.** `quantark_class` is the authoritative taxonomy, but a non-empty
   `components` list makes the family `package` regardless of class
   (`product_family_for_quantark_class`, `products.py:399`). Derivation therefore
   takes class **and** components (and any deltaone subtype that proves to matter),
   runs once inside the canonical product, not per tool. Component packages
   (vertical spread, call-put portfolio, ladder binary, …) are first-class in the
   storage refactor, so dropping components from the derivation would misfile every
   package as a single-leg option.
4. **Per-channel adapters are thin, dumb, and emit the FLAT contract shape.** An
   adapter maps channel input → the family term contract dict — a **flat** dict of
   raw economic inputs (`ko_barrier_pct`, `ko_rate`, `lockup_months`,
   `trade_start_date`, frequency, …), **never** a nested QuantArk `product_kwargs`
   with a pre-formed `barrier_config`. It fills the solve placeholder, then calls
   `build_product`. No schedule logic, no validation, no family guessing. This is a
   hard requirement, not a style note — see decision 8 for why.
5. **The term contract is data, not prose.** Each family declares its required and
   optional economic inputs (e.g. snowball: initial fixing S0, KO/KI barriers,
   coupon, lockup, observation frequency, trade start). `build_product` reports
   anything absent in `missing`; channels surface it as a fillable form/clarify.
6. **The solve target is a designated free variable, not a missing field.** RFQ
   *solve* mode and try-solve deliberately omit one field (`unknown.field_path` —
   e.g. `barrier_config.ko_rate`) which the bootstrapping solver
   (bisection/Newton, run *inside* the QuantArk library via `solve_rfq` →
   `rfq.service.quote_rfq` + `register_unknown_adapter`) resolves against a target.
   The contract treats that one field as **bound-by-initial-guess**, never as a
   blocking `missing`. The channel adapter fills it with `unknown.initial_guess`
   as a placeholder so `build_product` can synthesize a *complete* termsheet
   (schedules included); the solver then re-binds that field — and any structure
   derived from it, like per-record schedule rates — each iteration. `build_product`
   itself stays solve-unaware: it is called **once per quote** to produce the
   complete placeholder termsheet, not inside the solver hot loop.
7. **Resolution closes the free variable; RFQ→book converges on `book_position`.**
   On a successful solve, the resolved value is bound back into the terms, making the
   product fully complete (no free variable left). The executable terms are then
   **regenerated through `build_product` with the resolved value bound** — not merely
   top-level-patched — so derived structure (e.g. every schedule record's rate)
   reflects the solved coupon rather than the stale initial-guess placeholder.
   Booking then goes through the **same `book_position`** the agent uses for direct
   bookings (the existing `book_rfq_to_position` already wraps it, adding RFQ
   provenance + the `BOOKED` transition). Booking is a persisted, irreversible write,
   so it is **HITL-gated**: it happens only when the user explicitly asks or approves
   the confirmation card.
8. **`build_product`'s "already-built" detection must be hardened — `barrier_config`
   presence is NOT a safe signal.** Today (`product_builders.py:524`)
   `family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict)`
   routes to the tidy/pass-through branch. The current RFQ snowball template
   (`rfq.py:140`) is *nested QuantArk kwargs with a `barrier_config` but no
   observation schedule* — so it hits that branch, **skips synthesis, reports
   `missing=[]`, and fails with the opaque quad error** `KO observation dates or
   schedule required` instead of clean contract `missing` fields. Verified
   empirically. The "already-built" branch must therefore require **evidence of a
   synthesized schedule** (e.g. a non-empty `barrier_config.ko_observation_schedule`),
   not merely a `barrier_config`. A nested-but-schedule-less shape is *neither* the
   flat contract *nor* a complete product: it must be **rejected as malformed**, never
   silently tidied. Combined with decision 4 (adapters emit flat contract only), a
   raw contract always takes the synthesis path and the opaque-error bypass is closed.

## Architecture

```
  channel input            adapter (thin)         single producer        gate                storage
  ─────────────            ──────────────         ───────────────        ────                ───────
  RFQ intake / NL    ─┐    map input → FLAT      build_product(class,   validate_      create_or_get_product
  desk position form ─┤    contract dict        flat terms) → carrier  quantark_      (products root +
  OTC import row     ─┼──▶ (raw economic     ──▶ {ok, missing,      ──▶ build      ─▶ family tables;
  agent terms        ─┤    inputs; no nested    validation,            (inside           raw_terms snapshot)
  try-solve row      ─┘    barrier_config)      product_spec, …}       build_product)
```

Everything left of `build_product` is channel-specific and stays that way.
Everything from `build_product` rightward is shared and singular.

**Quoting & bootstrapping (solve mode).** A quoted RFQ / try-solve row omits one
field by design — the solve target. The flow is:

```
  bound inputs + unknown(field_path, bounds, initial_guess)
        │  adapter fills unknown := initial_guess (placeholder)
        ▼
  build_product(class, complete-terms)  ──▶  canonical product (schedules synthesized)
        │                                          │  pass termsheet + field_path + bounds + target
        ▼                                          ▼
  (price mode: price once)                 QuantArk solver loop  ── bisection/Newton ──┐
                                            re-binds field_path each iteration via      │
                                            register_unknown_adapter, reprices ◀────────┘
                                                       │ converged
                                                       ▼
                                            solved value + quote
```

The product terms are **complete throughout** bootstrapping; only the value of the
designated free variable changes between iterations. `build_product` runs once (to
materialize the complete placeholder termsheet); the iterative regression lives in
the QuantArk library, not in our builder.

**RFQ → book lifecycle.** Draft (contract may be incomplete) → quote/solve (gated on
a filled contract; solver resolves the free variable) → **resolved value bound back,
canonical terms regenerated via `build_product`** (now fully complete) →
approve/release/client-accept → **`book_position`** on user ask/approval (HITL-gated).
The product that gets booked is byte-identical in shape to a direct agent booking of
the same economics — same builder, same validator, same `create_or_get_product`.

## Canonical carrier API

Define the carrier before implementation; do not assume `ProductSpec` or
`BuildResult` is sufficient (neither is — see decision 1).

**LOCKED (2026-05-30): extend `BuildResult` to carry identity.**

```python
@dataclass(frozen=True)
class BuildResult:
    ok: bool                              # validate_quantark_build passed
    missing: list[str]                    # required-bound contract inputs absent
    warnings: list[str]
    validation: dict[str, Any] | None     # {"ok", "error"} from the quad build
    engine_name: str
    product_spec: ProductSpec | None      # NEW: identity + terms; non-None iff ok
```

- `product_spec` is the single carrier of identity: `asset_class`, `product_family`
  (derived from `quantark_class` + `components` per decision 3), `quantark_class`,
  `underlying`, `currency`, `terms` (the validated `product_kwargs`), `components`,
  `display_name`. The standalone `product_kwargs` field on today's `BuildResult` is
  replaced by `product_spec.terms`.
- `product_spec` is `None` when `ok is False` (missing inputs or failed validation);
  callers branch on `ok`/`missing` before reading it. This removes today's ambiguity
  where a `BuildResult` could carry partial `product_kwargs` with `ok=False`.
- Persistence-only fields on `ProductSpec` (`source_payload`) are populated by the
  caller at book time, not by `build_product`.

Alternative considered: a separate `CanonicalProductBuild(ok, missing, validation,
spec, engine_name)` wrapper. Rejected for now as more churn (every `build_product`
caller already consumes `BuildResult`); revisit if `BuildResult` accretes
build-only state that should not travel with the product.

## Components

### New
- **`product_contracts.py`** (or a table in `product_builders.py`): per-family term
  contract — required/optional input keys + types. Drives both `missing` reporting
  and channel form generation.
- **Channel adapters** (thin functions, one per workflow): `rfq_terms_to_contract`,
  `import_row_to_contract`, `trysolve_row_to_contract`. Agent/direct booking already
  passes contract-shaped terms.

### Changed
- **`services/domains/product_builders.py` + `schedules.py`** — (a) extend
  `BuildResult` with `product_spec` (decision 1); (b) harden the already-built
  detection to require a synthesized schedule, else reject (decision 8); (c)
  **add multi-frequency KO/KI schedule synthesis** (`MONTHLY|QUARTERLY|SEMI_ANNUAL|
  CUSTOM`) — `schedules.py` currently exposes only `monthly_observation_dates`, so
  this is net-new per the locked frequency policy (P2b/B).
- **`services/domains/products.py`** — `product_family_for_quantark_class` is the
  single derivation point; ensure every caller passes `components` (decision 3).
- **`services/rfq.py` + `RfqIntakeCard.tsx`** — `COMMON_TEMPLATES` snowball/phoenix/
  ko-reset entries reference the term contract; intake gains the
  schedule-generating fields (lockup, trade start) **and a frequency selector**
  (P2b/B); quoting routes product construction through `build_product` via the
  adapter. This is where the RFQ snowball gap closes.
- **`services/try_solve.py`** — `_product_kwargs_for_row` becomes an adapter to the
  contract + `build_product`; bespoke kwargs assembly retired.
- **`services/position_adapter.py`** — import schedule synthesis replaced by the
  shared builder; adapter maps import rows → contract.
- **`services/domains/booking.py`** — already routes through `build_product`
  (`10a555f`); no further change beyond moving family derivation onto the product.

### Retired (as each channel migrates)
- `try_solve._product_kwargs_for_row` bespoke assembly.
- `position_adapter` schedule synthesis.
- The implicit "RFQ template product shape" as a construction format.

## The term contract (worked example: snowball)

```
SnowballOption requires:
  initial_price (S0)        # never invented — user-clarified
  ko_barrier_pct | ko_barrier
  ki_barrier_pct | ki_barrier   (unless ki_convention = NONE)
  ko_rate (coupon)          # may be the RFQ solve target
  maturity_years | tenor
  lockup_months
  trade_start_date
  observation frequency     # MONTHLY | QUARTERLY | SEMI_ANNUAL | CUSTOM (required)
optional / defaulted:
  ki_convention (DAILY|EUROPEAN|NONE, default DAILY)
  ko_rate_annualized (default false)
  initial_date / settlement_date (else derived: start, start+tenor)
```

**Frequency policy (P2b — LOCKED 2026-05-30: multi-frequency / option B).**
Observation frequency is a **required-bound** input, not a default. The builder must
gain explicit multi-frequency schedule synthesis — today it hardcodes monthly
(`product_builders.py:171`, `monthly_observation_dates` + `frequency="MONTHLY"`), so
this is net-new builder work (see Components → Changed) plus an intake frequency
selector. `CUSTOM` carries an explicit observation-date list. This unblocks
quarterly/semi-annual snowballs the monthly-only builder cannot express today.

The RFQ snowball is broken precisely because its intake collects **none** of
`lockup_months`, `trade_start_date`, or observation frequency — the genuinely-required
schedule-generating inputs. Once the contract is explicit, the RFQ intake either
collects them (and quote/book work) or `build_product` returns them in `missing` and
the UI/agent asks — instead of failing opaquely at the quad engine.

**Bound inputs vs. the solve target.** Exactly one field may be designated the
solve target (`unknown.field_path`, e.g. `barrier_config.ko_rate`); it is supplied
as `(lower_bound, upper_bound, initial_guess)` rather than a value, and is **exempt**
from the contract-complete check. Everything else — including all schedule-generating
inputs — must be bound before a quote is allowed. So the contract has two tiers:

```
required-bound:   initial_price, ko/ki barriers (or the unknown), maturity,
                  lockup_months, trade_start_date, observation frequency
defaulted:        ki_convention (DAILY), ko_rate_annualized (false), …
free-variable:    at most one of the solvable fields (price mode: none; the
                  target value is then a fixed input)
```

Note the snowball's *missing schedule inputs* (lockup, trade start, frequency) are a
**different problem** from its *unknown coupon* (`ko_rate`). The coupon being unbound
is by design and fine; the schedule inputs being uncollected is the bug. Conflating
the two is what hid this for so long.

## Migration — strangler-fig, one channel at a time

Each step is independently shippable and guarded by characterization tests first
(the pattern proven in `10a555f`: write a test pinning current behaviour, refactor
to the shared builder, keep it green).

1. **Foundation** (must land before any channel migration) — (a) define the
   canonical carrier (decision 1); (b) harden `build_product`'s already-built
   detection so a nested-but-schedule-less shape is rejected, not tidied
   (decision 8); (c) derive `product_family` from `(quantark_class, components)`
   (decision 3); (d) extract the per-family term contract and drive `missing` from
   it; (e) add multi-frequency KO/KI schedule synthesis to `schedules.py` +
   `_build_snowball` (locked P2b/B). No channel/caller changes yet, but the P1a
   regression test lands here.
2. **RFQ** (highest value — it is the broken one). Add the term contract to the
   snowball/phoenix/ko-reset templates + intake; route `quote_rfq` product
   construction through `build_product` (adapter fills the solve target with its
   initial guess first). **Gate quoting on a filled contract** (decision below):
   the draft can be saved incomplete, but `quote_rfq` / the Quote action stays
   disabled until every required-bound input is present and a valid solve target is
   designated. Delete reliance on the bare template shape. Net: a bare-template
   snowball is a legitimate *draft*, and becomes quotable+bookable only once the
   contract is satisfied.
3. **try-solve** — adapter + `build_product`; retire `_product_kwargs_for_row`.
4. **OTC import** — adapter + `build_product`; retire `position_adapter` synthesis.
5. **Cleanup** — move family derivation onto the canonical product; remove now-dead
   builder code.

Order rationale: RFQ first delivers a user-visible fix and exercises the contract
on the hardest family; import last because it currently *works* (lowest risk, no
regression pressure).

## Testing

- **Characterization first** per channel: pin current working behaviour (e.g. the
  RFQ already-scheduled booking test added in `10a555f`) before refactoring.
- **One golden product per family**: a single fixture of valid economic terms that
  every adapter must round-trip to the same canonical `product_kwargs`. This is the
  regression net that makes divergence impossible to reintroduce.
- **Contract coverage**: each family's required-bound keys produce a precise
  `missing` entry when absent (no opaque quad-engine errors), and the designated
  solve target is *not* reported missing.
- **No silent nested-shape bypass (P1a regression)**: feeding the *current* RFQ
  snowball template shape (nested `barrier_config`, no `ko_observation_schedule`)
  into `build_product` must **not** return `missing=[]` + the opaque
  `KO observation … required` error. It must either surface the flat contract's
  `missing` fields or be rejected as malformed. (This is the exact bypass verified
  at spec-review time: `missing=[]`, `ok=False`, opaque quad error.)
- **Family derivation with components**: a packaged product (non-empty `components`)
  derives `product_family == "package"`, not the single-leg class family.
- **Multi-frequency synthesis (P2b/B)**: each frequency builds the right KO
  observation count over a tenor (e.g. 1Y QUARTERLY → 4, SEMI_ANNUAL → 2, CUSTOM →
  the supplied dates), and omitting frequency yields a `missing` entry (it is
  required-bound, not defaulted).
- **Solve-mode placeholder builds**: for every solvable field, the adapter +
  `build_product` produce a termsheet that builds and prices at the initial guess
  (so the bootstrapping solver can start), and an end-to-end solve converges and is
  then bookable.
- **Resolved-value propagation**: after a solve, the booked product's *derived*
  structure (e.g. every schedule record's rate) carries the **resolved** value, not
  the initial-guess placeholder — i.e. an RFQ that solved `ko_rate` and a direct
  booking with that same `ko_rate` produce identical canonical `product_kwargs`.
- **Cross-channel equivalence**: the same snowball expressed as an RFQ, an import
  row, and agent terms produces byte-identical canonical `product_kwargs`.

## Risks / open considerations

- **Storage refactor is mid-flight.** `product_kwargs` is now a compatibility
  projection; `raw_terms` is the snapshot. Unify construction *first*; let storage
  normalization converge on its own. Sequencing avoids refactoring twice. See
  `migrations-no-live-orm-services` caution — adapters must target the canonical
  builder, not future ORM shapes.
- **Legacy persisted data.** Old positions carry pre-contract shapes
  (`repair_invalid_snowball_booking_terms` exists for this). The unified schema
  needs a defined repair/migration path, not just new-write coverage.
- **Lowest-common-denominator risk.** Keep per-family builders under the
  `build_product` dispatcher; do **not** collapse into one wide optional-everything
  product object.
- **RFQ template semantics.** Templates are intentionally *incomplete* starting
  points with `unknown_fields` to solve. The contract does not force templates to be
  complete products — it requires the template's *bound* economic inputs to be
  sufficient to build a valid product once the solve target is placeholder-filled.
- **Resolved (2026-05-30): bare template = draft-only; quote gated on a filled
  contract.** A snowball RFQ may be *saved* with an incomplete template, but the
  Quote action / `quote_rfq` is disabled until the contract's required-bound inputs
  are present and a valid solve target is designated. This keeps draft creation
  frictionless while making "quote" mean "this is actually buildable." Intake
  (`RfqIntakeCard` + the template definitions) must therefore grow fields for the
  schedule-generating inputs (lockup, trade start, observation frequency) it does
  not collect today. The solve target stays absent by design — it is supplied as
  bounds + initial guess, not a value.
- **Solver ownership.** Bootstrapping (bisection/Newton) lives inside the QuantArk
  library, not our code; our responsibility ends at handing it a complete placeholder
  termsheet + `field_path` + bounds + target. `build_product` must therefore produce
  a termsheet that is valid *at the initial guess* for any solvable field — i.e. the
  placeholder must build, even if it is economically off — or the solver cannot
  start. The contract's solvable-field initial guesses must respect that.

## Relationship to existing work

- Builds on [Position Product Refactor (2026-05-28)](./2026-05-28-position-product-refactor-design.md)
  — that unifies storage; this unifies construction. They meet at the canonical
  product.
- Extends [Direct-Booking Routing + Product Builders (2026-05-29)](./2026-05-29-agent-booking-and-product-builders-design.md)
  — `build_product` and its registry are the seed of the single producer; this
  generalizes them from "the agent's direct-booking builder" to "the desk's only
  builder."
