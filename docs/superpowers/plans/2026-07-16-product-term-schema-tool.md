# `get_product_term_schema` Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desk agent a `get_product_term_schema(family)` tool that returns the exact legal term-sheet schema (builder-facing field names, types, required/optional, defaults, and legal enum values) so it fills `build_product` correctly on the first call instead of guessing enum values and looping.

**Architecture:** Extend the declarative `FamilyContract` with per-field `FieldSpec`s carrying builder-facing `input_name`s and either a live-introspected quant-ark `enum_ref` (only where every enum member round-trips) or builder-faithful `enum_values` literals. A new read-only tool serializes the schema; a round-trip fidelity test proves every advertised field/value produces a correct `build_product`. Schema-only — no `build_product` enum aliasing.

**Tech Stack:** Python 3.11, LangChain `@tool`, pydantic, pytest, quant-ark (`quantark.util.enum`).

## Global Constraints

- Numbers/validation come from quant-ark, never an LLM (this tool surfaces *schema*, not prices).
- Tests: `.venv/bin/python -m pytest` from repo root; `pythonpath=["backend"]`.
- No DB migration — schema is pure derivation from code.
- **V1 family scope (flat option families only):** `BarrierOption`, `EuropeanVanillaOption`, `AmericanOption`, `AsianOption`, `CashOrNothingDigitalOption`, `SingleSharkfinOption`, `DoubleSharkfinOption`, `OneTouchOption`, `DoubleOneTouchOption`. Nested-config families (`SnowballOption`, `PhoenixOption`, `KnockOutResetSnowballOption`, `RangeAccrualOption`) and DeltaOne (`Futures`, `SpotInstrument`) return `schema_available: false`.
- `enum_ref` (live introspection) is permitted ONLY where the round-trip test proves the builder consumes every member: `OptionType`, `BarrierType`, `BarrierDirection`, `TouchType`. Everything else uses `enum_values` literals.
- Frequency literals are builder-specific: Asian `averaging_frequency` = `("DAILY","WEEKLY","MONTHLY","QUARTERLY","SEMI_ANNUAL")`; `deltaone_type` (deferred) would be `("STOCK","INDEX","ETF")` (FUTURES does not round-trip).
- CHANGELOG.md updated under `[Unreleased]` before the feature is considered done.

---

## Task 1: `FieldSpec` dataclass + `FamilyContract.fields` + enum resolver

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (add `FieldSpec`, extend `FamilyContract`, add `resolve_enum_values`)
- Test: `tests/test_product_term_schema.py` (new)

**Interfaces:**
- Produces: `FieldSpec` dataclass; `FamilyContract.fields: tuple[FieldSpec, ...] = ()`; `resolve_enum_values(spec: FieldSpec) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_term_schema.py
from app.services.domains.product_contracts import FieldSpec, resolve_enum_values


def test_resolve_enum_values_from_literal():
    spec = FieldSpec(input_name="ki_convention", kind="enum",
                     description="x", enum_values=("DAILY", "EUROPEAN", "NONE"))
    assert resolve_enum_values(spec) == ("DAILY", "EUROPEAN", "NONE")


def test_resolve_enum_values_from_quantark_enum_ref():
    spec = FieldSpec(input_name="barrier_type", kind="enum",
                     description="x", enum_ref="BarrierType")
    assert resolve_enum_values(spec) == ("UP_IN", "UP_OUT", "DOWN_IN", "DOWN_OUT")


def test_resolve_enum_values_non_enum_is_empty():
    spec = FieldSpec(input_name="strike", kind="number", description="x")
    assert resolve_enum_values(spec) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Expected: FAIL with `ImportError: cannot import name 'FieldSpec'`.

- [ ] **Step 3: Add `FieldSpec`, `fields`, and `resolve_enum_values`**

In `product_contracts.py`, after the imports add `from typing import Any` if absent, and:

```python
@dataclass(frozen=True)
class FieldSpec:
    input_name: str                 # builder-facing key the model fills (flat)
    kind: str                       # "number" | "date" | "enum" | "string" | "bool"
    description: str
    contract_path: str | None = None  # dotted required_bound key, if != input_name
    default: Any | None = None
    enum_ref: str | None = None     # quant-ark enum class name (round-trip-proven only)
    enum_values: tuple[str, ...] | None = None  # builder-faithful literals otherwise
    one_of: str | None = None       # alternative-group id; exactly one member required


def resolve_enum_values(spec: FieldSpec) -> tuple[str, ...]:
    """Legal values for an enum field: declared literals, or live-introspected from a
    quant-ark enum class. Non-enum -> empty."""
    if spec.enum_values is not None:
        return tuple(spec.enum_values)
    if spec.enum_ref is not None:
        from quantark.util import enum as qenum
        enum_cls = getattr(qenum, spec.enum_ref)
        return tuple(member.name for member in enum_cls)
    return ()
```

Add `fields` to `FamilyContract` (keep existing fields; default empty so unchanged families are unaffected):

```python
@dataclass(frozen=True)
class FamilyContract:
    quantark_class: str
    required_bound: tuple[str, ...]
    defaulted: tuple[str, ...]
    solvable: tuple[str, ...]
    fields: tuple[FieldSpec, ...] = ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_term_schema.py
git commit -m "feat(products): add FieldSpec + enum resolver to FamilyContract"
```

---

## Task 2: Maturity `one_of` — shared required helper, completeness agreement, builder guard

Addresses finding 3: `build_product` accepts `maturity_date` as an alternative to `maturity_years`, but `check_term_completeness` reports `maturity_years` missing, and the synthesize path silently drops a `maturity_date` supplied alongside `maturity_years`.

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (add `required_fields` helper)
- Modify: `backend/app/tools/term_completeness.py` (use the helper + honor `one_of`)
- Modify: `backend/app/services/domains/product_builders.py` (`_common_option` both-present guard)
- Test: `tests/test_product_term_schema.py`, `tests/test_product_booking.py`

**Interfaces:**
- Produces: `required_fields(contract: FamilyContract, terms: dict) -> list[str]` in `product_contracts.py` — the required contract paths given the collected terms, resolving `one_of` groups (a group is satisfied when any member is present) and the `ko_observation_dates`-only-when-CUSTOM rule.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_product_term_schema.py  (append)
from app.services.domains.product_builders import build_product


def test_barrier_builds_with_maturity_date_only():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN", "maturity_date": "2027-07-15"})
    assert r.ok, r.validation


def test_completeness_accepts_maturity_date_only():
    from app.tools.term_completeness import check_term_completeness
    out = check_term_completeness.func(  # .func unwraps the @tool
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
         "maturity_date": "2027-07-15"})
    assert "maturity_years" not in out["missing_required"]
    assert out["complete"] is True


def test_barrier_rejects_both_maturity_representations():
    r = build_product("BarrierOption", {
        "initial_price": 100.0, "strike": 100.0, "barrier": 80.0,
        "option_type": "PUT", "barrier_type": "DOWN_IN",
        "maturity_years": 1.0, "maturity_date": "2027-07-15"})
    assert not r.ok
    assert "maturity" in ((r.validation or {}).get("error") or "").lower()


def test_completeness_maturity_date_does_not_satisfy_deferred_family():
    # finding 1: a deferred structured family (no one_of FieldSpecs) must still require
    # maturity_years — maturity_date must NOT be treated as a valid alternative for it.
    from app.tools.term_completeness import check_term_completeness
    out = check_term_completeness.func(
        "SnowballOption",
        {"initial_price": 100.0, "strike": 100.0, "maturity_date": "2027-07-15",
         "trade_start_date": "2026-07-15", "observation_frequency": "MONTHLY",
         "barrier_config": {"ko_barrier": 103.0, "ki_barrier": 75.0, "ko_rate": 0.15,
                            "lockup_months": 3}})
    assert "maturity_years" in out["missing_required"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q -k maturity`
Expected: `test_barrier_builds_with_maturity_date_only` PASSES already (book_position fix), the other two FAIL (`maturity_years` still reported missing; both-present not rejected).

- [ ] **Step 3: Add the `one_of_groups` + `required_fields` helpers**

**Per-family, not global (finding 1).** A maturity alternative applies ONLY to a family
whose FieldSpecs declare `one_of` — the deferred structured families (Snowball, Phoenix,
KO-reset, Range Accrual) have empty `fields`, so `maturity_years` stays required and a
`maturity_date`-only term set is NOT reported complete for them.

In `product_contracts.py`:

```python
def one_of_groups(contract: "FamilyContract") -> dict[str, tuple[str, ...]]:
    """Alternative groups this family declares, group_id -> member contract paths.
    Derived from the family's own FieldSpecs, so a global 'maturity' alternative can
    never leak into a family that doesn't opt in."""
    groups: dict[str, list[str]] = {}
    for spec in contract.fields:
        if spec.one_of is not None:
            groups.setdefault(spec.one_of, []).append(spec.contract_path or spec.input_name)
    return {g: tuple(members) for g, members in groups.items()}


def _present(terms: dict, key: str) -> bool:
    if key in terms and terms[key] not in (None, ""):
        return True
    node: object = terms
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return node not in (None, "")


def required_fields(contract: "FamilyContract", terms: dict) -> list[str]:
    """Required contract paths given collected terms: required_bound minus the
    conditional ko_observation_dates rule, with THIS family's declared one_of groups
    collapsed to satisfied/unsatisfied."""
    required = list(contract.required_bound)
    freq = terms.get("observation_frequency")
    if ("ko_observation_dates" in required and freq not in (None, "")
            and str(freq).strip().upper() != "CUSTOM"):
        required.remove("ko_observation_dates")
    groups = one_of_groups(contract)                       # per-family, may be empty
    member_to_group = {m: g for g, members in groups.items() for m in members}
    out: list[str] = []
    seen_groups: set[str] = set()
    for key in required:
        group = member_to_group.get(key)
        if group is None:
            out.append(key)                                # plain required field
            continue
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if not any(_present(terms, m) for m in groups[group]):
            out.append(groups[group][0])                   # representative for missing group
    return out
```

- [ ] **Step 4: Route `check_term_completeness` through the helper**

In `term_completeness.py`, replace the inline required/missing computation (the block computing `required`, `missing`, `provided`) with:

```python
from app.services.domains.product_contracts import _CONTRACTS, contract_for, required_fields
# ...
    required = required_fields(contract, terms)
    missing = [key for key in required if not _is_provided(_lookup(terms, key))]
    provided = [key for key in required if key not in missing]
```

Note: `required_fields` already handles the `ko_observation_dates`/CUSTOM and `maturity` one_of rules, so the family-specific `freq` block previously in this file is removed.

- [ ] **Step 5: Add the both-present guard to `_common_option`**

In `product_builders.py`, `_common_option` currently prefers `maturity_years` then falls back to `_explicit_maturity_date`. Add an up-front conflict check:

```python
def _common_option(terms: dict, out: _Out) -> dict:
    pk: dict[str, Any] = {"contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0}
    m = _num(terms.get("maturity_years"))
    explicit_date = _explicit_maturity_date(terms)
    if m is not None and explicit_date is not None:
        out.warnings.append("maturity_conflict")
        out.missing.append("__maturity_conflict__")  # forces ok=False with a clear error
        return pk
    if m is not None:
        pk["maturity"] = m
    elif explicit_date is not None:
        pk["exercise_date"] = explicit_date
    else:
        out.missing.append("maturity_years")
    return pk
```

Then in `build_product`, the synthesize branch already short-circuits on non-empty `missing`. To surface a readable error rather than a raw missing-key, add — right after `missing = product_contracts.filter_solved(...)` in the synthesize branch — a translation:

```python
        if "__maturity_conflict__" in missing:
            return BuildResult(
                ok=False, quantark_class=family, engine_name=engine_name,
                missing=[], warnings=out.warnings,
                validation={"ok": False, "error":
                    "maturity_years must not be supplied together with maturity_date; "
                    "use either explicit dates or tenor maturity, not both"},
                product_spec=None,
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py tests/test_product_booking.py -q`
Expected: PASS (maturity tests green; existing booking suite unbroken).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/product_contracts.py backend/app/tools/term_completeness.py backend/app/services/domains/product_builders.py tests/test_product_term_schema.py
git commit -m "feat(products): maturity one_of — completeness + builder agree on tenor|date"
```

---

## Task 3: Declare V1 field-specs + round-trip fidelity gate

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (attach `fields` to the 9 V1 contracts)
- Test: `tests/test_product_term_schema.py`

**Interfaces:**
- Consumes: `FieldSpec`, `resolve_enum_values`, `required_fields` (Tasks 1-2).
- Produces: the 9 V1 `FamilyContract`s now carry `fields`.

- [ ] **Step 1: Write the failing round-trip fidelity test**

```python
# tests/test_product_term_schema.py  (append)
import pytest
from app.services.domains.product_contracts import (
    _CONTRACTS, contract_for, required_fields, resolve_enum_values)
from app.services.domains.product_builders import build_product

V1_FAMILIES = [
    "BarrierOption", "EuropeanVanillaOption", "AmericanOption", "AsianOption",
    "CashOrNothingDigitalOption", "SingleSharkfinOption", "DoubleSharkfinOption",
    "OneTouchOption", "DoubleOneTouchOption",
]

# Known-good base terms per family (probe values that build ok).
PROBE = {
    "EuropeanVanillaOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0},
    "AmericanOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0},
    "CashOrNothingDigitalOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "cash_payoff": 10.0},
    "BarrierOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "barrier": 80.0},
    "SingleSharkfinOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "barrier": 120.0},
    "DoubleSharkfinOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0},
    "AsianOption": {"initial_price": 100.0, "maturity_years": 1.0, "strike": 100.0, "averaging_frequency": "MONTHLY"},
    "OneTouchOption": {"initial_price": 100.0, "maturity_years": 1.0, "barrier": 120.0, "cash_payoff": 10.0, "barrier_direction": "UP", "touch_type": "ONE_TOUCH"},
    "DoubleOneTouchOption": {"initial_price": 100.0, "maturity_years": 1.0, "upper_barrier": 120.0, "lower_barrier": 80.0, "cash_payoff": 10.0, "touch_type": "DOUBLE_ONE_TOUCH"},
}


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_v1_family_has_fields_and_base_probe_builds(family):
    contract = contract_for(family)
    assert contract.fields, f"{family} must declare FieldSpecs"
    assert build_product(family, dict(PROBE[family])).ok


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_every_advertised_required_field_is_in_probe(family):
    # The schema must not advertise a required field the builder ignores (finding 1):
    # every required input_name maps to a builder-consumed key present in the base probe.
    contract = contract_for(family)
    req_paths = set(required_fields(contract, {}))
    input_by_path = {(f.contract_path or f.input_name): f.input_name for f in contract.fields}
    for path in req_paths:
        name = input_by_path.get(path, path)
        # one_of members: at least one alternative present in probe
        assert name in PROBE[family] or path in PROBE[family], f"{family}: {path} not fillable"


@pytest.mark.parametrize("family", V1_FAMILIES)
def test_every_enum_value_round_trips_faithfully(family):
    # Build ok is NOT enough (finding 3): an accepted-but-mis-priced enum (e.g. OneTouch
    # + DOUBLE_ONE_TOUCH) must never be advertised. Assert the built product carries back
    # exactly the enum value we sent — faithful classification, not just .ok.
    contract = contract_for(family)
    for spec in contract.fields:
        if spec.kind != "enum":
            continue
        for value in resolve_enum_values(spec):
            r = build_product(family, {**PROBE[family], spec.input_name: value})
            assert r.ok, f"{family}.{spec.input_name}={value} did not build: {r.validation}"
            # enum values the builder normalizes into product_kwargs must round-trip
            if spec.input_name in r.product_kwargs:
                assert str(r.product_kwargs[spec.input_name]).upper() == value.upper(), \
                    f"{family}.{spec.input_name}={value} classified as {r.product_kwargs[spec.input_name]}"


# The _common_option families advertise the maturity one_of; verify date-only, tenor-only,
# and both-present for each — proving the advertised alternative is real (finding 2).
_MATURITY_ALT_FAMILIES = ["EuropeanVanillaOption", "AmericanOption",
                          "CashOrNothingDigitalOption", "BarrierOption",
                          "SingleSharkfinOption", "DoubleSharkfinOption"]


@pytest.mark.parametrize("family", _MATURITY_ALT_FAMILIES)
def test_maturity_alternative_is_faithful(family):
    base = {k: v for k, v in PROBE[family].items() if k != "maturity_years"}
    assert build_product(family, {**base, "maturity_years": 1.0}).ok        # tenor-only
    assert build_product(family, {**base, "maturity_date": "2027-07-15"}).ok  # date-only
    both = build_product(family, {**base, "maturity_years": 1.0, "maturity_date": "2027-07-15"})
    assert not both.ok  # both-present rejected


@pytest.mark.parametrize("family", ["AsianOption", "OneTouchOption", "DoubleOneTouchOption"])
def test_tenor_only_families_do_not_advertise_maturity_date(family):
    names = {f.input_name for f in contract_for(family).fields}
    assert "maturity_date" not in names


def test_asian_frequency_observation_count_is_correct():
    # finding 2: an advertised frequency must produce the right schedule, not silently monthly.
    expected = {"DAILY": 252, "WEEKLY": 52, "MONTHLY": 12, "QUARTERLY": 4, "SEMI_ANNUAL": 2}
    spec = next(f for f in contract_for("AsianOption").fields if f.input_name == "averaging_frequency")
    for value in resolve_enum_values(spec):
        r = build_product("AsianOption", {**PROBE["AsianOption"], "averaging_frequency": value})
        assert r.ok
        assert r.product_kwargs["num_observations"] == expected[value], value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q -k "v1_family or advertised or round_trips or observation_count"`
Expected: FAIL — `contract.fields` is empty for the V1 families.

- [ ] **Step 3: Declare `fields` on the 9 V1 contracts**

In `product_contracts.py`, define reusable FieldSpec groups and attach `fields=` to each V1 contract. Add near the vanilla constants:

**Maturity is scoped to `_common_option` families only (finding 2).** Only builders that
route through `_common_option` — `_build_vanilla` and descendants (vanilla, american,
digital, barrier) and sharkfin — accept `maturity_date`. `_build_one_touch`,
`_build_double_one_touch`, and `_build_asian` read `maturity_years` directly, so those
families advertise **only** `_TENOR` (plain required `maturity_years`, no `maturity_date`,
no `one_of`). Advertising `maturity_date` for them would name a field the builder ignores.

```python
_S0 = FieldSpec("initial_price", "number", "Initial fixing S0 / valuation spot.")
_STRIKE = FieldSpec("strike", "number", "Strike price.")
_MULT = FieldSpec("contract_multiplier", "number", "Contract multiplier (default 1.0).", default=1.0)
_OPTION_TYPE = FieldSpec("option_type", "enum", "CALL or PUT.", default="CALL", enum_ref="OptionType")
# _common_option families: tenor OR explicit date (one_of group).
_MATURITY_YEARS = FieldSpec("maturity_years", "number", "Tenor in years. Supply exactly one of the 'maturity' group.", one_of="maturity")
_MATURITY_DATE = FieldSpec("maturity_date", "date", "Explicit expiry date (ISO). Supply exactly one of the 'maturity' group.", one_of="maturity")
# Non-_common_option families (one-touch, double-one-touch, asian): tenor only.
_TENOR = FieldSpec("maturity_years", "number", "Tenor in years.")
_VANILLA_FIELDS = (_S0, _MATURITY_YEARS, _MATURITY_DATE, _STRIKE, _OPTION_TYPE, _MULT)   # _common_option families
_VANILLA_TENOR_FIELDS = (_S0, _TENOR, _STRIKE, _OPTION_TYPE, _MULT)                       # asian (reads maturity_years directly)
```

Then set `fields=` on each contract, e.g.:

```python
"EuropeanVanillaOption": FamilyContract(
    "EuropeanVanillaOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, (),
    fields=_VANILLA_FIELDS),
"AmericanOption": FamilyContract(
    "AmericanOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, (),
    fields=_VANILLA_FIELDS),
"AsianOption": FamilyContract(
    "AsianOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED + ("averaging_frequency",), (),
    fields=_VANILLA_TENOR_FIELDS + (
        FieldSpec("averaging_frequency", "enum", "Averaging observation frequency.",
                  default="MONTHLY",
                  enum_values=("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "SEMI_ANNUAL")),)),
"CashOrNothingDigitalOption": FamilyContract(
    "CashOrNothingDigitalOption", _VANILLA_REQUIRED + ("cash_payoff",), _VANILLA_DEFAULTED, (),
    fields=_VANILLA_FIELDS + (FieldSpec("cash_payoff", "number", "Cash payout if in-the-money."),)),
"BarrierOption": FamilyContract(
    "BarrierOption", _VANILLA_REQUIRED + ("barrier",), _VANILLA_DEFAULTED + ("barrier_type", "rebate"), (),
    fields=_VANILLA_FIELDS + (
        FieldSpec("barrier", "number", "Barrier price level."),
        FieldSpec("barrier_type", "enum", "Barrier direction + gating.", default="DOWN_OUT", enum_ref="BarrierType"),
        FieldSpec("rebate", "number", "Rebate paid on knock (default 0).", default=0.0))),
"SingleSharkfinOption": FamilyContract(
    "SingleSharkfinOption", _VANILLA_REQUIRED + ("barrier",), _VANILLA_DEFAULTED + ("participation_rate",), (),
    fields=_VANILLA_FIELDS + (
        FieldSpec("barrier", "number", "Sharkfin barrier level."),
        FieldSpec("participation_rate", "number", "Upside participation (default 1.0).", default=1.0))),
"DoubleSharkfinOption": FamilyContract(
    "DoubleSharkfinOption", _VANILLA_REQUIRED + ("lower_barrier", "upper_barrier"),
    _VANILLA_DEFAULTED + ("participation_rate",), (),
    fields=_VANILLA_FIELDS + (
        FieldSpec("lower_barrier", "number", "Lower barrier level."),
        FieldSpec("upper_barrier", "number", "Upper barrier level."),
        FieldSpec("participation_rate", "number", "Upside participation (default 1.0).", default=1.0))),
"OneTouchOption": FamilyContract(
    "OneTouchOption", ("initial_price", "maturity_years", "barrier", "cash_payoff"),
    ("barrier_direction", "touch_type"), (),
    fields=(_S0, _TENOR,
            FieldSpec("barrier", "number", "Barrier level."),
            FieldSpec("cash_payoff", "number", "Cash paid on touch."),
            FieldSpec("barrier_direction", "enum", "UP or DOWN.", default="UP", enum_ref="BarrierDirection"),
            FieldSpec("touch_type", "enum", "Single touch/no-touch.", default="ONE_TOUCH",
                      enum_values=("ONE_TOUCH", "NO_TOUCH")))),
"DoubleOneTouchOption": FamilyContract(
    "DoubleOneTouchOption",
    ("initial_price", "maturity_years", "upper_barrier", "lower_barrier", "cash_payoff"),
    ("touch_type",), (),
    fields=(_S0, _TENOR,
            FieldSpec("upper_barrier", "number", "Upper barrier level."),
            FieldSpec("lower_barrier", "number", "Lower barrier level."),
            FieldSpec("cash_payoff", "number", "Cash paid on touch."),
            FieldSpec("touch_type", "enum", "Double touch/no-touch.",
                      default="DOUBLE_ONE_TOUCH",
                      enum_values=("DOUBLE_ONE_TOUCH", "DOUBLE_NO_TOUCH")))),
```

**Finding 3 — `touch_type` uses family-specific literals, NOT live `TouchType`.** A live
probe shows OneTouch *builds ok* with `DOUBLE_ONE_TOUCH` but prices it on the no-touch
branch (silent economic corruption), so a build-ok-only gate would certify it. OneTouch
advertises only `("ONE_TOUCH","NO_TOUCH")`; DoubleOneTouch only the `DOUBLE_*` pair. The
round-trip test additionally asserts the built `product_kwargs["touch_type"]` equals the
input (faithful classification), not merely `.ok`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Expected: PASS. `test_every_enum_value_round_trips_faithfully` proves each advertised enum
value builds AND classifies faithfully; `test_maturity_alternative_is_faithful` proves the
one_of families accept date-only and reject both-present.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_term_schema.py
git commit -m "feat(products): declare V1 term-schema FieldSpecs + round-trip fidelity gate"
```

---

## Task 4: The `get_product_term_schema` tool

**Files:**
- Create: `backend/app/tools/product_term_schema.py`
- Test: `tests/test_product_term_schema.py`

**Interfaces:**
- Consumes: `contract_for`, `required_fields`, `resolve_enum_values`, `_CONTRACTS`, and the V1 family set.
- Produces: `get_product_term_schema` (`@tool`) returning `{quantark_class, fields, required_groups, notes}` or `{quantark_class, schema_available: false, reason, use_instead}` / `{error, known_classes}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_term_schema.py  (append)
from app.tools.product_term_schema import get_product_term_schema


def _call(cls):
    return get_product_term_schema.func(cls)  # unwrap @tool


def test_barrier_schema_shape():
    out = _call("BarrierOption")
    names = {f["name"]: f for f in out["fields"]}
    assert names["barrier_type"]["enum_values"] == ["UP_IN", "UP_OUT", "DOWN_IN", "DOWN_OUT"]
    assert "DOWN_AND_IN" not in names["barrier_type"]["enum_values"]
    assert names["initial_price"]["required"] is True
    # maturity is a one_of group, neither individually required
    assert names["maturity_years"]["required"] is False
    assert names["maturity_years"]["one_of"] == "maturity"
    assert {"one_of": "maturity", "members": ["maturity_years", "maturity_date"]} in out["required_groups"]


def test_deferred_family_returns_schema_unavailable():
    out = _call("SnowballOption")
    assert out["schema_available"] is False
    assert "check_term_completeness" in out["use_instead"]


def test_unknown_class_errors():
    out = _call("NotARealOption")
    assert "error" in out and "known_classes" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q -k "schema_shape or deferred or unknown_class"`
Expected: FAIL with `ModuleNotFoundError: app.tools.product_term_schema`.

- [ ] **Step 3: Write the tool**

```python
# backend/app/tools/product_term_schema.py
"""Read-only legal term-sheet schema for a product family.

Surfaces the builder-facing input names, types, required/optional, defaults, and legal
enum values so the agent fills build_product correctly on the first call instead of
guessing. Values are derived from FamilyContract FieldSpecs (enum values live from
quant-ark where round-trip-proven); numbers never come from an LLM."""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains.product_contracts import (
    _CONTRACTS, contract_for, one_of_groups, required_fields, resolve_enum_values)

# V1 covers the flat option families; nested-config + DeltaOne are deferred.
_SCHEMA_FAMILIES = frozenset({
    "BarrierOption", "EuropeanVanillaOption", "AmericanOption", "AsianOption",
    "CashOrNothingDigitalOption", "SingleSharkfinOption", "DoubleSharkfinOption",
    "OneTouchOption", "DoubleOneTouchOption",
})


class GetProductTermSchemaInput(BaseModel):
    quantark_class: str = Field(description="QuantArk family class, e.g. BarrierOption.")


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_term_schema", args_schema=GetProductTermSchemaInput)
def get_product_term_schema(quantark_class: str) -> dict:
    """Return the legal term-sheet schema for a product family — builder-facing field
    names, types, required/optional, defaults, and legal enum values. Call this BEFORE
    build_product and fill from the RFQ/context; do NOT guess enum values or omit
    required fields."""
    contract = contract_for(quantark_class)
    if contract is None:
        return {"error": f"Unknown QuantArk class {quantark_class!r}",
                "known_classes": sorted(_CONTRACTS)}
    if quantark_class not in _SCHEMA_FAMILIES or not contract.fields:
        return {"quantark_class": quantark_class, "schema_available": False,
                "reason": "structured schema not yet published for this family",
                "use_instead": "check_term_completeness + get_product_reference_doc"}

    required_paths = set(required_fields(contract, {}))
    groups = one_of_groups(contract)                       # per-family, may be empty
    fields = []
    for spec in contract.fields:
        path = spec.contract_path or spec.input_name
        entry = {
            "name": spec.input_name,
            "kind": spec.kind,
            "required": spec.one_of is None and path in required_paths,
            "description": spec.description,
        }
        if spec.default is not None:
            entry["default"] = spec.default
        if spec.kind == "enum":
            entry["enum_values"] = list(resolve_enum_values(spec))
        if spec.one_of is not None:
            entry["one_of"] = spec.one_of
        fields.append(entry)

    required_groups = [{"one_of": g, "members": list(members)}
                       for g, members in sorted(groups.items())]
    return {
        "quantark_class": quantark_class,
        "fields": fields,
        "required_groups": required_groups,
        "notes": ("Fill from the RFQ/context. Required fields and one member of each "
                  "required_groups must be supplied; defaulted fields fall back to desk "
                  "defaults. Do not guess enum values — use the listed enum_values."),
    }


__all__ = ["get_product_term_schema"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/product_term_schema.py tests/test_product_term_schema.py
git commit -m "feat(tools): add get_product_term_schema tool"
```

---

## Task 5: Register the tool + fetch-before-build prompt nudge

**Files:**
- Modify: `backend/app/tools/__init__.py` (import + `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py:372` (`DEEP_AGENT_TOOL_NAMES`)
- Modify: `backend/app/skills/workflows/products/build-product/SKILL.md` (procedure nudge)
- Test: `tests/test_product_term_schema.py`, `tests/test_personas.py`

**Interfaces:**
- Consumes: `get_product_term_schema` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_term_schema.py  (append)
def test_tool_is_registered_and_available():
    from app.tools import QUANT_AGENT_TOOLS
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools
    assert any(t.name == "get_product_term_schema" for t in QUANT_AGENT_TOOLS)
    assert "get_product_term_schema" in DEEP_AGENT_TOOL_NAMES
    # not silently dropped by the allowlist filter
    assert any(getattr(t, "name", None) == "get_product_term_schema"
               for t in select_deep_agent_tools())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q -k registered`
Expected: FAIL (tool not in `QUANT_AGENT_TOOLS`).

- [ ] **Step 3: Register in `QUANT_AGENT_TOOLS`**

In `backend/app/tools/__init__.py`, near the other imports (`from .term_completeness import check_term_completeness`):

```python
from .product_term_schema import get_product_term_schema
```

Add `get_product_term_schema,` to the `QUANT_AGENT_TOOLS = [ ... ]` list, next to `check_term_completeness`.

- [ ] **Step 4: Register in the allowlist**

In `backend/app/services/agents.py`, in the `DEEP_AGENT_TOOL_NAMES` frozenset (line ~372), add near `"check_term_completeness"`:

```python
        "get_product_term_schema",
```

- [ ] **Step 5: Add the prompt nudge to the build-product skill**

In `backend/app/skills/workflows/products/build-product/SKILL.md`, in the `## Procedure` list, insert a step between "Identify the family" and "Extract structured terms":

```markdown
2. Call `get_product_term_schema(family)` to get the legal fields, types, required/
   optional, and **enum values**. Fill terms from the RFQ/context using exactly those
   names and enum values — never guess an enum spelling or omit a required field.
```

Renumber the subsequent steps.

- [ ] **Step 6: Add a membership assertion to `test_personas.py`**

In `tests/test_personas.py`, alongside the existing `assert "book_position" in DEEP_AGENT_TOOL_NAMES` group, add:

```python
    assert "get_product_term_schema" in DEEP_AGENT_TOOL_NAMES
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py tests/test_personas.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/tools/__init__.py backend/app/services/agents.py backend/app/skills/workflows/products/build-product/SKILL.md tests/test_personas.py tests/test_product_term_schema.py
git commit -m "feat(agents): register get_product_term_schema + fetch-before-build nudge"
```

---

## Task 6: Regression sweep + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`
- Test: full suite

**Interfaces:** none (verification + docs).

- [ ] **Step 1: Run the product/agent/golden suites**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py tests/test_product_booking.py tests/test_product_builders.py tests/test_product_contracts.py tests/test_personas.py tests/test_agent_product_tool_contract.py -q -p no:warnings`
Expected: all PASS.

- [ ] **Step 2: Run the golden-replay + catalog gates**

Run: `.venv/bin/python -m pytest tests/ -q -k "golden or regression or flagship or catalog or skills" -p no:warnings`
Expected: all PASS. If a tool-set/count assertion trips, update it to include `get_product_term_schema` (membership, not exact count — mirrors `check_term_completeness`).

- [ ] **Step 3: Update CHANGELOG**

Under `## [Unreleased] / ### Added` in `CHANGELOG.md`, add:

```markdown
- **Products: `get_product_term_schema(family)` tool — a fillable legal term-sheet
  template.** Returns builder-facing field names, types, required/optional, defaults, and
  **legal enum values** per product family so the agent fills `build_product` correctly on
  the first call instead of guessing enum spellings (`DOWN_AND_IN` → the loop). Enum values
  are live-introspected from quant-ark where every member round-trips (`barrier_type`,
  `option_type`, `barrier_direction`, `touch_type`) and builder-faithful literals otherwise
  (frequencies). A round-trip fidelity test proves every advertised field/value produces a
  correct build. Also adds a `maturity_years | maturity_date` one_of alternative shared by
  the schema, `check_term_completeness`, and the synthesize builder (both-present now
  rejected). Schema-only — no `build_product` enum aliasing. V1 covers the flat option
  families; nested-config + DeltaOne families return `schema_available: false`.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): get_product_term_schema tool"
```

---

## Self-review notes (author)

- **Spec coverage:** FieldSpec/resolver (T1) · maturity one_of + builder guard (T2) · V1 field-specs + round-trip gate (T3) · tool + shapes (T4) · registration + nudge (T5) · regression + CHANGELOG (T6). All spec sections mapped.
- **Plan-review findings applied (Codex, plan gate):** (1) one_of is **per-family** — `one_of_groups(contract)` derives it from the family's own FieldSpecs, so `maturity_date` never satisfies a deferred family's completeness; negative test on `SnowballOption`. (2) `maturity_date` advertised ONLY by `_common_option` families (vanilla/american/digital/barrier/sharkfin); OneTouch/DoubleOneTouch/Asian use `_TENOR` (verified their builders read `maturity_years` directly); date/tenor/both tests per alt-family. (3) `touch_type` uses **family-specific literals** (not live `TouchType`) and the round-trip test asserts faithful `product_kwargs` classification, not just `.ok`.
- **Type consistency:** `FieldSpec(input_name, kind, description, contract_path, default, enum_ref, enum_values, one_of)` identical across T1/T3/T4; `resolve_enum_values`/`required_fields`/`one_of_groups` names consistent T1→T4; no global `_ONE_OF_GROUPS` (removed — per-family only); `.func` unwrap for every `@tool` call in tests.
- **Deferred-family guard:** `_SCHEMA_FAMILIES` in the tool AND the V1 list in T3 tests must stay in sync — if a nested family is added later, add it to both plus a probe-terms entry.
