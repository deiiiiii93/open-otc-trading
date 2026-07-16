# Product Term-Schema V2 — nested-config + DeltaOne families

**Date:** 2026-07-16
**Status:** scoped (design approved; awaiting plan)
**Builds on:** `2026-07-16-product-term-schema-tool-design.md` (V1)

## Problem

`get_product_term_schema` (V1) publishes the legal term-sheet for 9 **flat** option
families so the agent fills `build_product` right on the first call instead of guessing
enum spellings. The 6 remaining families return `schema_available: false`:

- **Nested-config:** `SnowballOption`, `KnockOutResetSnowballOption`, `PhoenixOption`,
  `RangeAccrualOption` (their required inputs live under dotted paths —
  `barrier_config.ko_barrier`, `coupon_config.coupon_barrier`, `range_config.lower_barrier`, …).
- **DeltaOne:** `Futures`, `SpotInstrument` (flat, but were deferred with the nested set).

**Evidence this matters (Arena Run #24, trader-rfq-booking-day).** `deepseek-v4-pro`
looped **6 times** building a PhoenixOption. It *did* call `get_product_term_schema`,
which returned `schema_available: false`, so it fell back to guessing the term-sheet
shape: `barrier_config` → `barrier_config.ko_barrier` → flat `ko_barrier` (attempt 6
succeeded). The build-retry loop the tool was designed to drain survives **exactly where
V1 punted.** Meanwhile every V1-covered BarrierOption in the same run built first-try
(clean 1 schema : 1 build). The V1 scope boundary and the residual-loop boundary are the
same line.

**Root inconsistency (why these were deferred, and the crux of V2).** The synthesize path
already speaks flat — `_build_snowball`/`_build_phoenix`/`_build_range_accrual`/
`_build_futures`/`_build_spot` read flat keys (`ko_barrier`, `coupon_barrier`, `underlying`,
…) and nest them into `barrier_config`/`coupon_config`/`range_config` themselves. But
`check_term_completeness` (which the model also calls) computes required **dotted** paths
via `required_fields`, and its `_lookup(terms, "barrier_config.ko_barrier")` only walks the
nested dict or a literal dotted key — it **never checks the flat `ko_barrier` alias**. So a
model that fills flat (as the schema would advise) is told by completeness that
`barrier_config.ko_barrier` is still missing → it loops. Advertising flat names without
fixing this would make the schema tool *actively feed* the loop.

## Decisions

1. **Cover all 6 deferred families** — 4 nested-config + 2 DeltaOne (clears the whole
   `schema_available: false` backlog).
2. **Barriers advertised as an alias set (absolute *or* percentage), NOT a `one_of`.**
   `ko_barrier` and `ko_barrier_pct` are two *spellings of the same value* (both feed
   `barrier_config.ko_barrier` via `_abs_barrier`), not two distinct economic concepts —
   so they are an **input-alias set** on one required field (either spelling satisfies it),
   distinct from the maturity `one_of` (two different contract paths, pick one). This
   distinction is load-bearing: a `{contract_path: input_name}` scalar map cannot hold both
   aliases, and `one_of_groups` (which keys on contract_path) would emit two identical
   `barrier_config.ko_barrier` members and still report the field missing — re-creating the
   loop. Every barrier the builders accept as `*_pct` gets both spellings:
   `ko_barrier|ko_barrier_pct`, `ki_barrier|ki_barrier_pct`,
   `post_ko_barrier|post_ko_barrier_pct` (KOReset),
   `coupon_barrier|coupon_barrier_pct` (Phoenix),
   `lower_barrier|lower_barrier_pct`, `upper_barrier|upper_barrier_pct` (RangeAccrual).
   **Both-spellings-present is an ambiguity** (the builder silently prefers absolute — a
   verified Snowball with `ko_barrier=103` + `ko_barrier_pct=120` books at 103): the builder
   rejects it and `check_term_completeness` flags it, mirroring the V1 maturity conflict
   guard, so schema/completeness/builder never disagree.
3. **Conditional-required expressed structurally.** Add `requires_when: (field, equals)` to
   `FieldSpec`; the schema emits it and `required_fields` honors it, so the model can reason
   about "required only under a condition" instead of parsing prose.
4. **Enums: live `enum_ref` only where every member round-trips; builder-faithful literals
   otherwise** (the V1 rule).
   - `observation_frequency` → **literals** `("MONTHLY","QUARTERLY","SEMI_ANNUAL","CUSTOM")`.
     Quantark *has* an `ObservationFrequency` enum, but the builder gates on these exact
     strings; the enum's members may differ (the `SEMI_ANNUAL`/`SEMI_ANNUALLY` drift class).
   - `ki_convention` → **literals** `("DAILY","EUROPEAN","NONE")`.
   - `deltaone_type` → **live `DeltaOneType`** (`STOCK/INDEX/ETF/FUTURES`) **iff** all four
     round-trip; else literals.
5. **Coherence fix (the enabler): any flat input alias satisfies its dotted `contract_path`.**
   Derive a `{contract_path: tuple[input_name, ...]}` **multi-alias** map from the family's
   own `fields`, and make `required_fields` / `_present` (contracts) / `_lookup` (completeness)
   treat the dotted path as satisfied when the nested path **or any** of its flat aliases is
   present. The map is list-valued precisely so both barrier spellings coexist.

## Architecture

### FieldSpec / contracts (`product_contracts.py`)

- **Extend `FieldSpec`** with two fields:
  - `requires_when: tuple[str, str] | None = None` — `(field, value)`; the spec is required
    only when `terms[field]` equals `value`. Negation (e.g. "ki_barrier required unless
    `ki_convention == NONE`") is encoded with a `!`-prefixed value: `("ki_convention","!NONE")`
    (documented convention, evaluated by a single helper). Schema output shape is fixed:
    `"requires_when": {"field": "...", "equals": "..."}` (or `"not_equals"` for the negated form).
  - `input_aliases: tuple[str, ...] = ()` — additional flat spellings that all satisfy this
    spec's one `contract_path` (default: just `input_name`). This is the abs/pct alias set —
    NOT a `one_of` (they are one value, two spellings). A barrier FieldSpec declares
    `input_name="ko_barrier"`, `input_aliases=("ko_barrier","ko_barrier_pct")`,
    `contract_path="barrier_config.ko_barrier"`.
- **Author `fields=` for the 6 families** (flat `input_name`/aliases → dotted `contract_path`):
  - **SnowballOption:** `initial_price`, `maturity_years`, `trade_start_date`,
    `observation_frequency` (enum-literals), **`ko_barrier|ko_barrier_pct`**
    (→`barrier_config.ko_barrier`), **`ki_barrier|ki_barrier_pct`** (→`barrier_config.ki_barrier`,
    `requires_when` ki_convention≠NONE), `ko_rate` (→`barrier_config.ko_rate`),
    `lockup_months` (→`barrier_config.lockup_months`), `ko_observation_dates`
    (`requires_when` observation_frequency==CUSTOM), `ki_convention` (enum-literals, default DAILY).
  - **KnockOutResetSnowballOption:** snowball fields + **`post_ko_barrier|post_ko_barrier_pct`**
    (→`post_barrier_config.ko_barrier`), `post_ko_rate` (→`post_barrier_config.ko_rate`).
  - **PhoenixOption:** snowball fields + **`coupon_barrier|coupon_barrier_pct`**
    (→`coupon_config.coupon_barrier`), `coupon_rate` (→`coupon_config.coupon_rate`),
    `memory_coupon` (bool, defaulted).
  - **RangeAccrualOption:** `initial_price`, `maturity_years`, **`lower_barrier|lower_barrier_pct`**
    (→`range_config.lower_barrier`), **`upper_barrier|upper_barrier_pct`**
    (→`range_config.upper_barrier`), `accrual_rate` (→`range_config.accrual_rate`),
    `observation_frequency` (default DAILY), `contract_multiplier` (defaulted).
    *(Plan MUST confirm the exact flat/pct keys `_build_range_accrual` reads before authoring.)*
  - **Futures:** `initial_price`, `underlying`; defaulted `contract_multiplier`,
    `maturity_years`, `basis`, `basis_decay_rate`, `market_price`, `contract_code`.
  - **SpotInstrument:** `initial_price`, `underlying`; `deltaone_type` (enum, default INDEX),
    defaulted `instrument_code`, `exchange`, `contract_multiplier`.
- **`flat_aliases(contract) -> dict[str, tuple[str, ...]]`** — `{contract_path: (input names)}`,
  list-valued so both barrier spellings coexist. Used by the coherence fix.
- **Both-spellings guard.** `check_term_completeness` reports a conflict, and the builder
  rejects, when >1 alias of the same barrier is supplied (mirrors the V1 maturity guard). This
  is the ONE narrow synthesis-vocab change (see Out of scope).

### Coherence fix (required_fields / _present / _lookup)

- `required_fields`: a required dotted path is met if the nested path **or any** of its flat
  aliases (`flat_aliases`) is present. `requires_when` fields are only added to `required`
  when their condition holds against the collected terms.
- `_present` (contracts) and `_lookup` (completeness): accept the flat aliases. Single shared
  alias-aware presence helper so the two consumers cannot drift.

### Schema tool (`product_term_schema.py`)

- Add the 6 families to `_SCHEMA_FAMILIES`.
- Emit per-field `requires_when` (when set) and the **flat input aliases** (e.g.
  `"input_names": ["ko_barrier", "ko_barrier_pct"]` with a note "absolute level or % of
  initial") — NOT as `one_of` alternatives. The `schema_available: false` fallback stays for
  any future unlisted family.

### Tests (fidelity is the gate)

- **Round-trip fidelity** per family and **per barrier spelling**: build from the advertised
  schema — supplying the absolute barrier in one case and the `_pct` alias in another — and
  assert **faithful structure**, not just `.ok`: Phoenix has a populated `coupon_config`;
  KOReset has `post_barrier_config`; RangeAccrual has `range_config`; the pct spelling lands at
  `pct × initial`; Snowball KI drops when `ki_convention=NONE`.
- **Both-spellings conflict:** supplying `ko_barrier` AND `ko_barrier_pct` (any barrier pair)
  makes `check_term_completeness` report a conflict AND `build_product` reject — never a silent
  absolute-wins build. One test per barrier pair.
- **Anti-loop regression:** flat-supplied terms (either barrier spelling) make
  `check_term_completeness` report `complete: true` for each nested family (the bug that caused
  the Run #24 Phoenix loop).
- **Conditional-required both branches:** `observation_frequency=CUSTOM` ⇒ `ko_observation_dates`
  required; non-CUSTOM ⇒ not. `ki_convention=NONE` ⇒ `ki_barrier` not required; else required.
- **Enum round-trip:** assert every advertised `deltaone_type` member builds a faithful
  SpotInstrument; if any fails, downgrade to literals (test enforces the choice).
- **Count/coupling:** `_SCHEMA_FAMILIES` size + any pinned tool/enum-count assertions updated.

## Failure handling

- **Economically-wrong enum members** (the `SEMI_ANNUAL` class) — caught by the fidelity
  test asserting the *built structure/classification*, never `.ok` alone; such enums stay
  literals.
- **Contradictory economics (both barrier spellings)** — `_abs_barrier` silently prefers the
  absolute value, so completeness rejecting while the builder books it would desync. The
  both-spellings guard rejects at BOTH layers so they agree (see conflict test).
- **Coherence regression** — the anti-loop test asserts flat terms read complete; if a future
  edit re-breaks the dotted/flat equivalence it fails loudly.
- **prebuilt/verbatim `build_product` path** is untouched — the schema advises the flat
  synthesize vocab, which is the path these families already use.

## Out of scope

- **No synthesize-path changes EXCEPT two narrow correctness guards** — `_build_*` already
  read flat keys. The only builder edits are: (a) rejecting >1 representation of the same
  barrier (flat abs/pct **or** the nested/dotted path — mirrors V1's maturity conflict), and
  (b) making `_build_phoenix` unconditionally default the KO-leg `ko_rate` to 0 when omitted
  (the current guarded default never fires, so a Phoenix without `ko_rate` wrongly fails). Both
  are correctness fixes, not synthesis rewrites.
- **RangeAccrual frequency is restricted to `DAILY`/`MONTHLY`** — the builder prices every
  non-DAILY value as monthly, so QUARTERLY/SEMI_ANNUAL are deliberately NOT published (they'd
  be economically wrong); widening them requires a builder period-map (a later change).
- **No families beyond the 6.**
- **par recalibration** — the other Run #24 EFF finding; a separate scoring change.
- **No enum-spelling aliasing** — schema-only safety net, same as V1 (the model must use a
  listed value; the tool does not silently translate `DOWN_AND_IN`→`DOWN_IN`).
- **Arena verification** — a Run #25 to confirm the Phoenix loop is drained is a follow-up,
  not part of this feature.
