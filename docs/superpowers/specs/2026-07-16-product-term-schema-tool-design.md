# `get_product_term_schema` — fillable legal term-sheet template (design)

**Date:** 2026-07-16
**Status:** draft → spec gate

## Problem

Arena Runs #22/#23 exposed a dominant tool-call loop when DeepSeek models book a
product through `build_product`: the model **guesses** the term schema and
trial-and-errors until `build_product` accepts it. In the Run #23 `deepseek-v4-pro`
transcript this was **5 `build_product` failures in one match**, all the same class:

- `barrier_type: "DOWN_AND_IN"` → quant-ark rejects `Invalid barrier type` (the legal
  value is `DOWN_IN`), ×4;
- `missing: [initial_price, maturity_years, barrier]` — required fields the model forgot.

The model already has these tools but **none surfaces the legal enum values**:

- `get_rfq_catalog` — product-type names + quote modes only.
- `get_product_reference_doc` — prose description, no fillable field list.
- `check_term_completeness(class, terms)` — reports missing/provided/defaulted field
  **names** (presence check only). It never lists a field's legal *values*, so it
  cannot catch `DOWN_AND_IN` (a present-but-invalid enum) and can only be called
  *after* the model has already guessed.

The quant-ark enum types (`BarrierType = {UP_IN, UP_OUT, DOWN_IN, DOWN_OUT}`, etc.)
exist but are surfaced to the model **nowhere**. The fix is to give the model the exact
legal schema — field names, types, required/optional, defaults, and enum values — *before*
it builds, and prompt it to fill from the RFQ/context instead of guessing.

This is the schema-discoverability half of the friction identified in the Run #22 EFF
analysis (the other half — the `book_position`/`build_product` term-contract asymmetry —
was fixed separately on 2026-07-16; see `CHANGELOG.md`). This design does **not** address
the async batch-pricing polling loop (a distinct follow-up).

## Goals / non-goals

**Goals**
- A dedicated read-only tool `get_product_term_schema(quantark_class)` returning a
  structured, fillable schema per family, including **live** enum values.
- Enum values that can never drift from quant-ark (introspected at call time).
- One source of truth for required-ness (the existing `FamilyContract` tuples), shared
  with `check_term_completeness`.
- Registration + a prompt nudge so the model actually calls it before `build_product`.

**Non-goals (explicit YAGNI)**
- **No `build_product` enum aliasing.** `build_product` keeps rejecting bad enums with
  honest errors — a model that still fumbles after being handed the schema is a real
  capability signal, not something to hide. (User decision: schema-only.)
- No DB migration — the schema is pure derivation from code.
- No change to `build_product`/`book_position` behavior, **except** the `_common_option`
  maturity both-present guard required by finding 3 (§3).
- No async-polling fix.
- **V1 family scope:** the tool covers the **flat option families** — `BarrierOption`,
  `EuropeanVanillaOption`, `AmericanOption`, `AsianOption`, `CashOrNothingDigitalOption`,
  `SingleSharkfinOption`, `DoubleSharkfinOption`, `OneTouchOption`, `DoubleOneTouchOption`
  (all consume flat `input_name`s). The nested-config families (`SnowballOption`,
  `PhoenixOption`, `KnockOutResetSnowballOption` with `barrier_config.*`;
  `RangeAccrualOption` with `range_config.*`) and the DeltaOne pair (`Futures`,
  `SpotInstrument` — underlying-threading + the `deltaone_type=FUTURES` non-round-trip)
  are **deferred**: the tool returns `{"schema_available": false, "reason": ...,
  "use_instead": "check_term_completeness + get_product_reference_doc"}` for them. The
  `FieldSpec.contract_path` field is retained precisely so a later PR can add them without
  a schema redesign. This is the spec's exclusion mechanism, made concrete.

## Approach (decisions locked in brainstorming)

1. **Delivery:** a new dedicated tool `get_product_term_schema(family)` (not an
   enrichment of an existing tool).
2. **Source (hybrid):** enum legal values **live-introspected** from quant-ark enum
   classes; field metadata (type, default, description, enum link) declared once per
   family by extending `FamilyContract`.
3. **Scope:** schema-only, no aliasing safety net.

## Component design

### 1. Data layer — extend `FamilyContract` with per-field specs

File: `backend/app/services/domains/product_contracts.py`.

**Design invariant (from spec review): the schema advertises exactly what the
synthesize builder READS, and every advertised value must round-trip to a successful,
economically-correct `build_product`.** A schema that names a field the builder ignores,
or an enum value it silently mis-defaults, is worse than none — it cements the retry
loop. Two review findings drive the model below:

1. **Builder-facing input names, not contract paths.** The synthesize builders consume
   **flat** keys (`ko_barrier`, `lockup_months`, `lower_barrier`, `accrual_rate`) even
   though `required_bound` / `check_term_completeness` use **dotted** semantic paths
   (`barrier_config.ko_barrier`). Advertising the dotted names would send the model into
   the very loop we're killing. So `FieldSpec` carries a builder-facing `input_name`
   (what the model fills) distinct from an optional `contract_path` (the dotted key used
   to derive required-ness from `required_bound`). For flat families the two are equal.
2. **Builder-faithful enum values, not blind live introspection.** Live-introspecting a
   quant-ark enum is safe ONLY when the builder consumes every member correctly. Verified
   in review: `barrier_type` (all 4 `BarrierType`), `option_type`, `barrier_direction`
   round-trip fully → `enum_ref` (live). The **frequency** fields do NOT: the
   snowball/asian builders accept `{MONTHLY, QUARTERLY, SEMI_ANNUAL, CUSTOM}` (note
   `SEMI_ANNUAL`; no `DAILY/WEEKLY/ANNUALLY`) and **silently fall back to monthly** for
   anything else — a 1-yr Asian with `SEMI_ANNUALLY` builds a wrong 2→12-observation
   product. Frequency fields therefore declare **literal `enum_values`** matching what
   the builder implements, never `enum_ref`.

```python
@dataclass(frozen=True)
class FieldSpec:
    input_name: str                 # builder-facing key the MODEL fills (flat), e.g. "ko_barrier"
    kind: str                       # "number" | "date" | "enum" | "string" | "bool"
    description: str
    contract_path: str | None = None  # dotted required_bound key, if != input_name
    default: Any | None = None      # advisory; the builder owns the real default
    enum_ref: str | None = None     # quant-ark enum class name — ONLY when round-trip-proven
    enum_values: tuple[str, ...] | None = None  # builder-faithful literals otherwise
    one_of: str | None = None       # alternative-group id; exactly one member required (see §3)
```

- `required` is **NOT** stored on `FieldSpec` — derived at tool time from
  `required_bound`/`defaulted` (via `contract_path or input_name`), one source of truth;
  the builder↔contract consistency test still governs it.
- `kind == "enum"` ⇒ exactly one of `enum_ref`/`enum_values` (validation test).
- **A family is exposed by the tool only if its advertised schema round-trips** to a
  correct build (§Testing). Any family that can't be faithfully described (should be none
  after the `input_name`/literal fixes) is excluded with a pointer to
  `check_term_completeness`, never shipped as a misleading schema.

**Enum coverage** (from the synthesize builders' `terms.get("<field>", …)` calls):

| input_name | kind | value source (round-trip-proven) | families |
|---|---|---|---|
| `option_type` | enum | `enum_ref="OptionType"` (live) | vanilla/american/asian/digital/barrier/sharkfin |
| `barrier_type` | enum | `enum_ref="BarrierType"` (live) | barrier |
| `barrier_direction` | enum | `enum_ref="BarrierDirection"` (live) | one-touch |
| `touch_type` | enum | **family-specific literals** — OneTouch `("ONE_TOUCH","NO_TOUCH")`, Double `("DOUBLE_ONE_TOUCH","DOUBLE_NO_TOUCH")`. NOT `enum_ref`: OneTouch *builds ok* with `DOUBLE_ONE_TOUCH` but prices it as no-touch (silent corruption) | one-touch / double |
| `deltaone_type` | enum | literals `("STOCK","INDEX","ETF")` — `FUTURES` does not round-trip (deferred family) | spot |
| `observation_frequency` | enum | **literal** `("MONTHLY","QUARTERLY","SEMI_ANNUAL","CUSTOM")` | snowball / range-accrual |
| `averaging_frequency` | enum | **literal** (builder-implemented values only — verify names) | asian |
| `ki_convention` | enum | **literal** `("DAILY","EUROPEAN","NONE")` | snowball |

Non-enum fields (`initial_price`, `maturity_years`, `maturity_date`, `strike`, `barrier`,
`cash_payoff`, `contract_multiplier`, `rebate`, `participation_rate`, …) get `kind`
`number`/`date`/`bool`. Nested-config families (snowball/phoenix/range) declare flat
`input_name` (`ko_barrier`) with the dotted `contract_path` (`barrier_config.ko_barrier`)
so required-ness still maps to `required_bound`.

### 2. Enum introspection — the hybrid resolver

A small pure helper (co-located in `product_contracts.py` or a new
`term_schema.py` service):

```python
def resolve_enum_values(spec: FieldSpec) -> tuple[str, ...]:
    if spec.enum_values is not None:
        return spec.enum_values
    if spec.enum_ref is not None:
        enum_cls = getattr(quantark.util.enum, spec.enum_ref)
        return tuple(e.name for e in enum_cls)
    return ()
```

Resolved **at call time**, so adding a `BarrierType` member in quant-ark immediately
appears in the schema with zero repo change. Unknown `enum_ref` → the validation test
fails at test time (not at runtime for the agent). **`enum_ref` is permitted only for
enums the round-trip test proves the builder consumes in full** (§Testing); every other
enum field uses builder-faithful `enum_values` literals. This is the guard against
finding 2 (a live enum member the builder silently mis-defaults).

### 3. Shared required-ness helper + the maturity `one_of` alternative

`check_term_completeness` encodes one conditional required rule (`ko_observation_dates`
is required only when `observation_frequency == CUSTOM`). Factor the "which
`required_bound` keys are actually required given these terms" logic into a shared
helper (`required_fields(contract, terms)` in `product_contracts.py`) that BOTH
`check_term_completeness` and the new schema tool call, so the two never disagree.
For the schema tool (no terms), `required` = `required_bound`, with the conditional key
marked `required_if: "observation_frequency == CUSTOM"` in its FieldSpec description.

**Maturity alternative (finding 3).** `required_bound` lists only `maturity_years`, but
the synthesize builder now accepts `maturity_date`/`exercise_date` as an alternative
(the 2026-07-16 book_position fix — `_common_option`). So the schema must present them as
an alternative group, not mark `maturity_years` unconditionally required. Model this with
`one_of="maturity"` on both the `maturity_years` and `maturity_date` FieldSpecs: the
schema emits them as *"supply exactly one of: maturity_years, maturity_date"* (neither is
individually `required: true`; the group is required). This requires a small companion
change so the two consumers agree and both-present is rejected rather than silently
resolved:

- **`check_term_completeness`**: treat a satisfied `one_of` group as satisfying its
  requirement — a `maturity_date`-only term set must NOT report `missing_required:
  [maturity_years]` (today it does, contradicting the builder). The shared
  `required_fields` helper resolves `one_of` groups against the provided terms.
- **Builder both-present guard**: the synthesize path currently prefers `maturity_years`
  and silently discards an explicit `maturity_date` when both are present.
  `_common_option` gains a both-present rejection (mirroring the prebuilt path's existing
  `_mixed_maturity_representation_error`) so a contradictory pair fails loudly instead of
  dropping the contractual date. Test: tenor-only ✓, date-only ✓, both-present → error.

This keeps the schema, completeness, and builder in agreement on maturity — the same
"one source of truth" discipline as required-ness.

### 4. The tool — `backend/app/tools/product_term_schema.py`

Mirror `term_completeness.py` structure exactly:

```python
class GetProductTermSchemaInput(BaseModel):
    quantark_class: str = Field(description="QuantArk family class, e.g. BarrierOption.")

@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_term_schema", args_schema=GetProductTermSchemaInput)
def get_product_term_schema(quantark_class: str) -> dict:
    """Return the legal term-sheet schema for a product family — field names,
    types, required/optional, defaults, and legal enum values. Call this BEFORE
    build_product and fill from the RFQ/context; do NOT guess enum values."""
```

Return shape:

Return shape (field key is `name` = the builder-facing `input_name`):

```json
{
  "quantark_class": "BarrierOption",
  "fields": [
    {"name": "initial_price", "kind": "number", "required": true,
     "description": "Initial fixing S0 / valuation spot."},
    {"name": "strike", "kind": "number", "required": true, "description": "..."},
    {"name": "barrier", "kind": "number", "required": true, "description": "..."},
    {"name": "maturity_years", "kind": "number", "required": false, "one_of": "maturity",
     "description": "Tenor in years. Supply exactly one of the 'maturity' group."},
    {"name": "maturity_date", "kind": "date", "required": false, "one_of": "maturity",
     "description": "Explicit expiry date. Supply exactly one of the 'maturity' group."},
    {"name": "barrier_type", "kind": "enum", "required": false, "default": "DOWN_OUT",
     "enum_values": ["UP_IN","UP_OUT","DOWN_IN","DOWN_OUT"],
     "description": "Barrier direction + gating."},
    {"name": "option_type", "kind": "enum", "required": false, "default": "CALL",
     "enum_values": ["CALL","PUT"], "description": "..."}
  ],
  "required_groups": [{"one_of": "maturity", "members": ["maturity_years", "maturity_date"]}],
  "notes": "Fill from the RFQ/context. Required fields + one member of each required_groups must be supplied; defaulted fields fall back to desk defaults."
}
```

Unknown class → `{"error": "Unknown QuantArk class 'X'", "known_classes": [...]}` (same
shape as `check_term_completeness`).

### 5. Wiring — registration + fetch-before-build nudge

- **Register** `get_product_term_schema` in `QUANT_AGENT_TOOLS`
  (`backend/app/tools/__init__.py`) **and** the `DEEP_AGENT_TOOL_NAMES` allowlist that
  `select_deep_agent_tools()` filters by — the documented "registered ≠ available"
  gotcha (a tool absent from the allowlist is silently dropped from every persona).
- **Prompt nudge:** add a routing line to
  `backend/app/skills/workflows/products/build-product/SKILL.md` (the build-product
  skill): *"Before calling `build_product`, call `get_product_term_schema(family)` and
  fill terms from the RFQ/context — never guess enum values or omit required fields."*
  Consider the same line in `product-term-interpretation/SKILL.md`. (The orchestrator
  prompt `prompts/trader.md` need not change — routing lives in the skill.)

## Data flow

```
model (needs to book a barrier)
  → get_rfq_catalog                     # which family? -> BarrierOption
  → get_product_term_schema(BarrierOption)
        contract_for("BarrierOption") -> FamilyContract(fields=...)
        required_fields(contract, {})  -> required-ness
        resolve_enum_values(spec)      -> live [UP_IN,UP_OUT,DOWN_IN,DOWN_OUT]
     ← {fields:[...], enum_values:[...DOWN_IN...]}
  → build_product(BarrierOption, {barrier_type:"DOWN_IN", ...})   # correct first try
```

## Testing

New `tests/test_product_term_schema.py`:

- **★ Round-trip fidelity (the central correctness gate — addresses findings 1 & 2).**
  For **every** family the tool exposes: build a term dict by filling each advertised
  required field (and one `one_of` member) with a valid probe value, then assert
  `build_product(family, terms).ok is True`. Then, for **every enum field**, iterate
  **every advertised `enum_values`/`enum_ref` value**, build with it, and assert (a) the
  build succeeds and (b) the produced structure is economically correct — specifically
  the **observation count matches the frequency** for schedule-bearing families (a 1-yr
  Asian/snowball with `SEMI_ANNUAL` yields 2 observations, `QUARTERLY` → 4, `MONTHLY` →
  12). This test is what proves the schema never names a field the builder ignores
  (finding 1) nor advertises a value the builder mis-defaults (finding 2); a family that
  can't pass it must be excluded from the tool, not shipped.
- **Shape:** `get_product_term_schema("BarrierOption")` returns every required field with
  `required: true`, `barrier_type.enum_values` contains `DOWN_IN` and **not**
  `DOWN_AND_IN`, and `maturity_years`/`maturity_date` appear in a `maturity` `one_of`
  group (neither individually required).
- **Live enum:** resolved `barrier_type` values equal `[e.name for e in BarrierType]`.
- **Maturity `one_of` agreement:** a `maturity_date`-only term set passes
  `check_term_completeness` (no `missing_required: [maturity_years]`) AND builds; a
  tenor-only set builds; a both-present set is **rejected** by the builder.
- **Required-ness parity:** the tool's required set (groups resolved) equals what
  `check_term_completeness` treats as required for the same family (shared-helper).
- **Unknown class:** returns `error` + `known_classes`.
- **Coverage guard:** every enum field the synthesize builders read (`barrier_type`,
  `option_type`, `barrier_direction`, `touch_type`, `observation_frequency`,
  `averaging_frequency`, `deltaone_type`, `ki_convention`) appears in some family's
  field-spec — a new enum field can't ship without a schema entry.
- **FieldSpec invariant:** `kind == "enum"` ⇒ exactly one of `enum_ref`/`enum_values`;
  `input_name` present; `contract_path` (if set) exists in `required_bound`/`defaulted`.

Extend `tests/test_product_contracts.py` (builder↔contract consistency net) so a family
with a `fields` tuple covers at least its `required_bound` + `defaulted` keys (mapped via
`contract_path or input_name`).

Regression: `get_product_term_schema` registered ⇒ update the skills-catalog/tool-count
assertions if the tool set is size-pinned (per the documented catalog coupling); the
golden `trader-rfq-booking-day` replay must still earn full marks.

## Risks / notes

- **Tool proliferation:** this is a 4th product-schema-ish tool. Mitigated by a crisp
  docstring boundary (schema = *before* build; `check_term_completeness` = verify a
  *collected* set; `get_product_reference_doc` = prose economics).
- **Frequency enum values** — resolved during review: the frequency fields
  (`observation_frequency`, `averaging_frequency`) use **builder-faithful literals**, NOT
  a live enum — the builders implement only `{MONTHLY, QUARTERLY, SEMI_ANNUAL, CUSTOM}`
  and silently monthly-default the rest. `ki_convention` → literal
  `("DAILY","EUROPEAN","NONE")`. Exact `averaging_frequency` literals to confirm against
  `_FREQUENCY_PER_YEAR` / the asian builder during implementation.
- **Skill-catalog test coupling:** adding a tool may trip exact-set/count assertions in
  the skills catalog tests (documented six-file coupling) — update as needed.
- **Does the model actually call it?** The prompt nudge + allowlist registration are
  load-bearing; a follow-up live arena run (#24) confirms the `build_product` failure
  count drops. Not part of this spec's acceptance (which is unit + golden-replay green).
