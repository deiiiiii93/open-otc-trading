# Product Term-Schema V2 (nested-config + DeltaOne) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `get_product_term_schema` to the 6 deferred families (Snowball, KOReset, Phoenix, RangeAccrual, Futures, SpotInstrument) so the agent fills `build_product` first-try instead of guessing the nested term-sheet shape.

**Architecture:** Add an `input_aliases` set + `requires_when` conditional to `FieldSpec`; author `fields=` for the 6 families (flat input → dotted `contract_path`); teach `required_fields`/`_present`/`_lookup` that any flat alias satisfies its dotted path; add a both-spellings barrier guard at the builder + completeness; publish the families from the schema tool. Synthesis is otherwise untouched (`_build_*` already read flat keys).

**Tech Stack:** Python 3.11, dataclasses, langchain tool, pytest (`.venv/bin/python -m pytest`).

## Global Constraints

- **Numbers never come from an LLM** — enum values are builder-faithful literals or live-introspected only where every member round-trips.
- **build-ok ≠ economically-correct** — every round-trip test asserts the *built structure/classification*, never `.ok` alone.
- **Builder input vocab ≠ completeness vocab re-creates the loop** — a flat `input_name` MUST satisfy its dotted `contract_path` across schema + completeness + builder.
- Run tests from repo root with `.venv/bin/python -m pytest` (pythonpath=["backend"]).
- Files touched live under `backend/app/services/domains/`, `backend/app/tools/`, `tests/`.

---

### Task 1: FieldSpec extension — `input_aliases` + `requires_when` + helpers

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py`
- Test: `tests/test_product_contracts.py`

**Interfaces:**
- Consumes: existing `FieldSpec`, `FamilyContract`, `_present`, `one_of_groups`.
- Produces: `FieldSpec.input_aliases: tuple[str, ...]`, `FieldSpec.requires_when: tuple[str, str] | None`; `flat_aliases(contract) -> dict[str, tuple[str, ...]]`; `_alias_present(terms, contract_path, aliases) -> bool`; `_requires_when_active(spec, terms) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_contracts.py
def test_flat_aliases_maps_contract_path_to_all_input_spellings():
    from app.services.domains.product_contracts import FieldSpec, FamilyContract, flat_aliases
    spec = FieldSpec("ko_barrier", "number", "KO barrier.",
                     contract_path="barrier_config.ko_barrier",
                     input_aliases=("ko_barrier", "ko_barrier_pct"))
    c = FamilyContract("X", ("barrier_config.ko_barrier",), (), (), fields=(spec,))
    assert flat_aliases(c) == {"barrier_config.ko_barrier": ("ko_barrier", "ko_barrier_pct")}


def test_requires_when_negation_and_equality():
    from app.services.domains.product_contracts import FieldSpec, _requires_when_active
    unless_none = FieldSpec("ki_barrier", "number", "KI.", requires_when=("ki_convention", "!NONE"))
    only_custom = FieldSpec("ko_observation_dates", "date", "dates",
                            requires_when=("observation_frequency", "CUSTOM"))
    assert _requires_when_active(unless_none, {"ki_convention": "DAILY"}) is True
    assert _requires_when_active(unless_none, {"ki_convention": "NONE"}) is False
    assert _requires_when_active(only_custom, {"observation_frequency": "CUSTOM"}) is True
    assert _requires_when_active(only_custom, {"observation_frequency": "MONTHLY"}) is False
    # requires_when=None → unconditional (always active)
    assert _requires_when_active(FieldSpec("x", "number", ""), {}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_contracts.py -k "flat_aliases or requires_when" -v`
Expected: FAIL (`FieldSpec` has no `input_aliases`/`requires_when`; `flat_aliases`/`_requires_when_active` undefined).

- [ ] **Step 3: Implement the extension**

```python
# product_contracts.py — in FieldSpec dataclass, add after `one_of`:
    input_aliases: tuple[str, ...] = ()   # flat spellings that all satisfy contract_path
    requires_when: tuple[str, str] | None = None  # (field, value); "!X" = required unless ==X


def _aliases_for(spec: FieldSpec) -> tuple[str, ...]:
    """Flat input spellings that satisfy this spec's contract_path (>= its input_name)."""
    return spec.input_aliases or (spec.input_name,)


def flat_aliases(contract: FamilyContract) -> dict[str, tuple[str, ...]]:
    """{dotted contract_path: (flat input spellings)} for specs whose path != input vocab."""
    out: dict[str, tuple[str, ...]] = {}
    for spec in contract.fields:
        path = spec.contract_path or spec.input_name
        out[path] = _aliases_for(spec)
    return out


def _requires_when_active(spec: FieldSpec, terms: dict) -> bool:
    """Is this spec's requirement active given collected terms? Unconditional when
    requires_when is None. '!X' means active UNLESS the field equals X."""
    if spec.requires_when is None:
        return True
    field, value = spec.requires_when
    actual = str(terms.get(field, "") or "").strip().upper()
    if value.startswith("!"):
        return actual != value[1:].strip().upper()
    return actual == value.strip().upper()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_product_contracts.py -k "flat_aliases or requires_when" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_contracts.py
git commit -m "feat(products): FieldSpec input_aliases + requires_when + flat_aliases helper"
```

---

### Task 2: Alias-aware coherence in `required_fields` + `_present` + conditional-required

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (`_present`, `required_fields`)
- Test: `tests/test_product_contracts.py`

**Interfaces:**
- Consumes: `flat_aliases`, `_aliases_for`, `_requires_when_active` (Task 1).
- Produces: `required_fields` that (a) treats a dotted path satisfied by any flat alias, (b) adds a `requires_when` field only when active. Shared `_path_present(terms, path, aliases)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_contracts.py
def _snowball_like():
    from app.services.domains.product_contracts import FieldSpec, FamilyContract
    return FamilyContract(
        "S", ("barrier_config.ko_barrier", "barrier_config.ki_barrier"), (), (),
        fields=(
            FieldSpec("ko_barrier", "number", "KO", contract_path="barrier_config.ko_barrier",
                      input_aliases=("ko_barrier", "ko_barrier_pct")),
            FieldSpec("ki_barrier", "number", "KI", contract_path="barrier_config.ki_barrier",
                      input_aliases=("ki_barrier", "ki_barrier_pct"),
                      requires_when=("ki_convention", "!NONE")),
        ),
    )


def test_flat_alias_satisfies_dotted_required_path():
    from app.services.domains.product_contracts import required_fields
    c = _snowball_like()
    # flat ko_barrier supplied → barrier_config.ko_barrier NOT reported missing
    missing = required_fields(c, {"ko_barrier": 90.0, "ki_convention": "NONE"})
    assert "barrier_config.ko_barrier" not in missing
    # pct spelling also satisfies
    missing_pct = required_fields(c, {"ko_barrier_pct": 90.0, "ki_convention": "NONE"})
    assert "barrier_config.ko_barrier" not in missing_pct


def test_requires_when_drops_ki_when_convention_none():
    from app.services.domains.product_contracts import required_fields
    c = _snowball_like()
    # ki_convention=NONE → ki_barrier not required
    assert "barrier_config.ki_barrier" not in required_fields(c, {"ko_barrier": 90, "ki_convention": "NONE"})
    # ki_convention=DAILY → ki_barrier required (absent)
    assert "barrier_config.ki_barrier" in required_fields(c, {"ko_barrier": 90, "ki_convention": "DAILY"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_contracts.py -k "flat_alias_satisfies or requires_when_drops" -v`
Expected: FAIL (current `_present` only checks the dotted path; `required_fields` has no requires_when).

- [ ] **Step 3: Implement**

```python
# product_contracts.py — replace _present usage in required_fields with alias-aware presence.

def _path_present(terms: dict, path: str, aliases: tuple[str, ...] = ()) -> bool:
    """True if the dotted `path` is present as a nested value/literal key, OR any flat
    alias key is present."""
    if _present(terms, path):
        return True
    return any(_present(terms, a) for a in aliases)


def required_fields(contract: FamilyContract, terms: dict) -> list[str]:
    required = list(contract.required_bound)
    freq = terms.get("observation_frequency")
    if (
        "ko_observation_dates" in required
        and freq not in (None, "")
        and str(freq).strip().upper() != "CUSTOM"
    ):
        required.remove("ko_observation_dates")

    aliases = flat_aliases(contract)
    # requires_when: drop paths whose FieldSpec condition is inactive.
    path_to_spec = {(s.contract_path or s.input_name): s for s in contract.fields}
    conditional_inactive = {
        path for path, s in path_to_spec.items()
        if s.requires_when is not None and not _requires_when_active(s, terms)
    }
    required = [p for p in required if p not in conditional_inactive]

    groups = one_of_groups(contract)
    member_to_group = {m: g for g, members in groups.items() for m in members}
    out: list[str] = []
    seen_groups: set[str] = set()
    for key in required:
        group = member_to_group.get(key)
        if group is None:
            if not _path_present(terms, key, aliases.get(key, ())):
                out.append(key)
            continue
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if not any(_path_present(terms, m, aliases.get(m, ())) for m in groups[group]):
            out.append(groups[group][0])
    return out
```

Note: the current `required_fields` returns ALL group-collapsed keys (it defers the present-check to the caller). This task moves the present-check INTO `required_fields` via `_path_present` so a flat alias counts. **Update the docstring** to say it now returns genuinely-missing paths. Verify `check_term_completeness` (Task 4) and the schema tool still read it correctly (both already filter by presence — the schema tool calls `required_fields(contract, {})` with empty terms, so every required path stays; completeness re-checks — see Task 4 for the completeness alignment).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_product_contracts.py -k "flat_alias_satisfies or requires_when_drops" -v`
Then full contracts suite: `.venv/bin/python -m pytest tests/test_product_contracts.py -q`
Expected: PASS (and no regression — the V1 flat families have empty `aliases` so behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_contracts.py
git commit -m "feat(products): required_fields treats flat aliases + requires_when conditional"
```

---

### Task 3: Author FieldSpecs for the 4 nested-config families

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (attach `fields=` to Snowball/KOReset/Phoenix/RangeAccrual contracts)
- Test: `tests/test_product_term_schema.py`

**Interfaces:**
- Consumes: `FieldSpec` (Task 1). The barrier keys are confirmed from the builders: `_abs_barrier(terms, "ko_barrier", "ko_barrier_pct", …)`, `ki_barrier|ki_barrier_pct`, `post_ko_barrier|post_ko_barrier_pct`, `coupon_barrier|coupon_barrier_pct`, `lower_barrier|lower_barrier_pct`, `upper_barrier|upper_barrier_pct`; flat `ko_rate`, `lockup_months`, `trade_start_date`, `coupon_rate`, `accrual_rate`, `memory_coupon`; `observation_frequency` literals `{MONTHLY,QUARTERLY,SEMI_ANNUAL,CUSTOM}` (Snowball) / `{DAILY,MONTHLY,QUARTERLY,SEMI_ANNUAL}` (RangeAccrual); `ki_convention` literals `{DAILY,EUROPEAN,NONE}`.
- Produces: `_CONTRACTS[...]` for the 4 families now carry non-empty `fields`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_term_schema.py
NESTED = ["SnowballOption", "KnockOutResetSnowballOption", "PhoenixOption", "RangeAccrualOption"]

def test_nested_contracts_declare_fields_with_dotted_paths():
    from app.services.domains.product_contracts import contract_for, flat_aliases
    for fam in NESTED:
        c = contract_for(fam)
        assert c.fields, f"{fam} must declare FieldSpecs"
        al = flat_aliases(c)
        # every dotted required_bound path is reachable via a declared flat alias
        for path in c.required_bound:
            if "." in path:
                assert path in al, f"{fam}: {path} has no FieldSpec/alias"


def test_barrier_specs_advertise_abs_and_pct():
    from app.services.domains.product_contracts import contract_for
    specs = {s.contract_path or s.input_name: s for s in contract_for("SnowballOption").fields}
    ko = specs["barrier_config.ko_barrier"]
    assert set(ko.input_aliases) == {"ko_barrier", "ko_barrier_pct"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -k "nested_contracts_declare or barrier_specs_advertise" -v`
Expected: FAIL (contracts have empty `fields`).

- [ ] **Step 3: Implement — author the FieldSpecs**

```python
# product_contracts.py — shared nested FieldSpec building blocks (place near _VANILLA_FIELDS):

def _barrier(name: str, path: str, desc: str, **kw) -> FieldSpec:
    return FieldSpec(name, "number", desc, contract_path=path,
                     input_aliases=(name, f"{name}_pct"), **kw)

_OBS_FREQ_SNOWBALL = FieldSpec(
    "observation_frequency", "enum", "KO observation frequency.",
    enum_values=("MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "CUSTOM"))
_KI_CONVENTION = FieldSpec(
    "ki_convention", "enum", "KI monitoring convention.", default="DAILY",
    enum_values=("DAILY", "EUROPEAN", "NONE"))

_SNOWBALL_FIELDS = (
    _S0, _TENOR,
    FieldSpec("trade_start_date", "date", "Trade/observation start date (ISO)."),
    _OBS_FREQ_SNOWBALL,
    _barrier("ko_barrier", "barrier_config.ko_barrier", "Knock-out barrier (abs or % of initial)."),
    _barrier("ki_barrier", "barrier_config.ki_barrier", "Knock-in barrier (abs or %).",
             requires_when=("ki_convention", "!NONE")),
    FieldSpec("ko_rate", "number", "KO coupon rate.", contract_path="barrier_config.ko_rate"),
    FieldSpec("lockup_months", "number", "Lock-up months before first KO.",
              contract_path="barrier_config.lockup_months"),
    FieldSpec("ko_observation_dates", "date", "Explicit KO dates (list); CUSTOM freq only.",
              requires_when=("observation_frequency", "CUSTOM")),
    _KI_CONVENTION,
)
```

Then attach `fields=` in `_CONTRACTS` / the base contracts:
- `_SNOWBALL_CONTRACT` → `fields=_SNOWBALL_FIELDS`.
- `_KO_RESET_CONTRACT` → `fields=_SNOWBALL_FIELDS + (_barrier("post_ko_barrier", "post_barrier_config.ko_barrier", "Post-KI reset KO barrier."), FieldSpec("post_ko_rate","number","Post-reset KO rate.", contract_path="post_barrier_config.ko_rate"))`.
- `_PHOENIX_CONTRACT` → **also drop `barrier_config.ko_rate` from `required_bound`** (Phoenix
  defaults the KO-leg rate to 0 — see the builder fix below), and
  `fields=_SNOWBALL_FIELDS_NO_KO_RATE + (FieldSpec("ko_rate","number","KO-leg coupon rate (defaults 0 for Phoenix).", contract_path="barrier_config.ko_rate", default=0.0), _barrier("coupon_barrier","coupon_config.coupon_barrier","Coupon (memory) barrier."), FieldSpec("coupon_rate","number","Coupon rate.", contract_path="coupon_config.coupon_rate"), FieldSpec("memory_coupon","bool","Memory coupon (default false).", default=False))`
  where `_SNOWBALL_FIELDS_NO_KO_RATE` is `_SNOWBALL_FIELDS` with the plain `ko_rate` spec removed. This makes the schema advertise `ko_rate` as **defaulted, not required**, so the model does not invent it (finding 3).
  - **Builder fix (finding 3):** `_build_phoenix` must **unconditionally** default the KO-leg rate to 0 when `ko_rate` is omitted and drop `barrier_config.ko_rate` from `out.missing` — the current guard (`"ko_rate" in bc`) never fires because `_build_snowball` omits the key entirely when absent. Amend `_build_phoenix` (see Task 5) to set `out.product_kwargs.setdefault("barrier_config", {})["ko_rate"] = 0.0` and remove the marker whenever `terms.get("ko_rate") is None`.
- `RangeAccrualOption` contract → add
  `fields=(_S0, _TENOR, _barrier("lower_barrier","range_config.lower_barrier","Lower accrual barrier."), _barrier("upper_barrier","range_config.upper_barrier","Upper accrual barrier."), FieldSpec("accrual_rate","number","Accrual rate.", contract_path="range_config.accrual_rate"), FieldSpec("observation_frequency","enum","Accrual obs frequency.", default="DAILY", enum_values=("DAILY","MONTHLY")), _MULT)`.
  - **Finding 1:** `_build_range_accrual` prices EVERY non-DAILY value as `round(maturity×12)` obs (a live 2Y probe gave 24 obs for MONTHLY/QUARTERLY/SEMI_ANNUAL alike). Advertising QUARTERLY/SEMI_ANNUAL would publish schedules that silently price as monthly. Restrict the enum to **`("DAILY","MONTHLY")`** — the only two the builder distinguishes — and assert the exact observation count per value in Task 6.

Since `_SNOWBALL_CONTRACT`/`_KO_RESET_CONTRACT`/`_PHOENIX_CONTRACT` are `frozen` dataclasses built before `_SNOWBALL_FIELDS`, define `_SNOWBALL_FIELDS` ABOVE them (move the field blocks above line 74) OR rebuild the contracts with `dataclasses.replace(_SNOWBALL_CONTRACT, fields=_SNOWBALL_FIELDS)`. Prefer defining the field blocks first, then the contracts, to keep one definition site.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -k "nested_contracts or barrier_specs" tests/test_product_contracts.py -q`
Expected: PASS (contract↔builder consistency net in `test_product_contracts.py` still green — `fields` is additive).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_term_schema.py
git commit -m "feat(products): FieldSpecs for nested-config families (Snowball/KOReset/Phoenix/RangeAccrual)"
```

---

### Task 4: Author DeltaOne FieldSpecs + both-spellings barrier guard (completeness side)

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (Futures/SpotInstrument `fields=`)
- Modify: `backend/app/tools/term_completeness.py` (`_lookup` alias-aware; both-spellings conflict)
- Test: `tests/test_product_term_schema.py`

**Interfaces:**
- Consumes: `flat_aliases`, `_requires_when_active`, `required_fields` (Tasks 1–2). `DeltaOneType` enum members `{STOCK,INDEX,ETF,FUTURES}` — verify round-trip in Step 1.
- Produces: DeltaOne contracts carry `fields`; `check_term_completeness` recognizes flat aliases and reports a `conflicts` entry when >1 spelling of a barrier is supplied.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_term_schema.py
def test_deltaone_type_enum_round_trips():
    from app.services.domains.product_builders import build_product
    from app.services.pricing_parameters import default_market  # or the existing test market helper
    for dt in ("STOCK", "INDEX", "ETF", "FUTURES"):
        r = build_product("SpotInstrument", {"initial_price": 100.0, "underlying": "AAPL",
                                             "deltaone_type": dt}, prebuilt=False)
        assert r.ok, f"deltaone_type {dt} failed: {r}"
        # faithful: the built product carries the requested deltaone_type
        assert str(getattr(r.product, "deltaone_type", "")).upper().endswith(dt)


def test_completeness_accepts_flat_alias_for_nested_family():
    from app.tools.term_completeness import check_term_completeness
    res = check_term_completeness.func(  # .func to bypass the tool wrapper in tests
        "SnowballOption",
        {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
         "observation_frequency": "MONTHLY", "ko_barrier": 103, "ki_barrier": 70,
         "ko_rate": 0.08, "lockup_months": 3, "ki_convention": "DAILY"},
    )
    assert "barrier_config.ko_barrier" not in res["missing_required"]
    assert "barrier_config.ki_barrier" not in res["missing_required"]


def test_completeness_flags_both_barrier_spellings_conflict():
    from app.tools.term_completeness import check_term_completeness
    # flat abs + flat pct
    r1 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "ko_barrier": 103, "ko_barrier_pct": 120})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r1["conflicts"])
    assert r1["complete"] is False
    # finding 2: nested/dotted representation + a flat alias is ALSO a conflict
    r2 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "barrier_config": {"ko_barrier": 103}, "ko_barrier_pct": 120})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r2["conflicts"])
    r3 = check_term_completeness.func("SnowballOption",
        {"initial_price": 100, "barrier_config.ko_barrier": 103, "ko_barrier": 90})
    assert any(c.get("alias_conflict") == "barrier_config.ko_barrier" for c in r3["conflicts"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -k "deltaone_type_enum or completeness_accepts_flat or both_barrier_spellings" -v`
Expected: FAIL. **If `test_deltaone_type_enum_round_trips` fails for any member**, drop the live enum and declare `deltaone_type` with `enum_values` limited to the members that build (record which in a comment) — this is the "live only where it round-trips" rule.

- [ ] **Step 3: Implement**

DeltaOne `fields` in `_CONTRACTS`:
```python
    "Futures": FamilyContract("Futures", ("initial_price", "underlying"),
        ("contract_multiplier", "maturity_years", "basis", "basis_decay_rate", "market_price", "contract_code"), (),
        fields=(_S0, FieldSpec("underlying", "string", "Underlying symbol/name."), _MULT,
                _TENOR, FieldSpec("basis", "number", "Futures basis.", default=0.0))),
    "SpotInstrument": FamilyContract("SpotInstrument", ("initial_price", "underlying"),
        ("deltaone_type", "instrument_code", "exchange", "contract_multiplier"), (),
        fields=(_S0, FieldSpec("underlying", "string", "Underlying symbol/name."),
                FieldSpec("deltaone_type", "enum", "Delta-one instrument kind.", default="INDEX",
                          enum_ref="DeltaOneType"), _MULT)),
```

`term_completeness.py` — make `_lookup` alias-aware and add the both-spellings conflict:
```python
from app.services.domains.product_contracts import flat_aliases  # add import

def _is_provided_path(terms, path, aliases):
    if _is_provided(_lookup(terms, path)):
        return True
    return any(_is_provided(_lookup(terms, a)) for a in aliases)

# in check_term_completeness, after computing `required`:
    aliases = flat_aliases(contract)
    missing = [k for k in required if not _is_provided_path(terms, k, aliases.get(k, ()))]
    provided = [k for k in required if k not in missing]
    ...
    # Representation-complete barrier conflict (finding 2): the distinct ways to express one
    # barrier are {the canonical dotted/nested path} ∪ {flat aliases}. >1 provided → conflict.
    alias_conflicts = []
    for path, al in aliases.items():
        if len(al) <= 1:
            continue
        reps = 0
        # canonical dotted/nested path (only when it is NOT one of the flat alias keys)
        if path not in al and _is_provided(_lookup(terms, path)):
            reps += 1
        reps += sum(1 for a in al if _is_provided(_lookup(terms, a)))
        if reps > 1:
            provided = ([path] if (path not in al and _is_provided(_lookup(terms, path))) else []) \
                       + [a for a in al if _is_provided(_lookup(terms, a))]
            alias_conflicts.append({"alias_conflict": path, "provided": provided})
    conflicts = conflicts + alias_conflicts   # keep existing one_of conflicts too
```
`complete` stays `not missing and not conflicts`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -k "deltaone or completeness_accepts or both_barrier" tests/test_product_contracts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py backend/app/tools/term_completeness.py tests/test_product_term_schema.py
git commit -m "feat(products): DeltaOne FieldSpecs + completeness alias/both-spellings handling"
```

---

### Task 5: Both-spellings barrier guard (builder side)

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (`_abs_barrier` or its callers, + `build_product` error surfacing)
- Test: `tests/test_product_booking.py` (or `tests/test_product_builders.py` — use whichever holds the build_product tests)

**Interfaces:**
- Consumes: `_abs_barrier(terms, abs_key, pct_key, initial)`.
- Produces: supplying BOTH `abs_key` and `pct_key` makes the build fail with an ambiguity error (not silently prefer absolute).

- [ ] **Step 1: Write the failing test**

```python
def _snowball_base():
    return {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "ki_barrier": 70, "ko_rate": 0.08,
            "lockup_months": 3, "ki_convention": "DAILY"}

def test_build_rejects_both_barrier_spellings_flat():
    from app.services.domains.product_builders import build_product
    r = build_product("SnowballOption", {**_snowball_base(), "ko_barrier": 103, "ko_barrier_pct": 120},
                      prebuilt=False)
    assert not r.ok
    assert any("ko_barrier" in str(m) for m in (r.missing or []))

def test_build_rejects_nested_plus_flat_barrier():  # finding 2 at the builder boundary
    from app.services.domains.product_builders import build_product
    r = build_product("SnowballOption",
                      {**_snowball_base(), "barrier_config": {"ko_barrier": 103}, "ko_barrier_pct": 120},
                      prebuilt=False)
    assert not r.ok

def test_phoenix_builds_without_ko_rate():  # finding 3
    from app.services.domains.product_builders import build_product
    terms = {k: v for k, v in _snowball_base().items() if k != "ko_rate"}
    terms.update({"ko_barrier": 103, "coupon_barrier": 80, "coupon_rate": 0.02})
    r = build_product("PhoenixOption", terms, prebuilt=False)
    assert r.ok, r
    assert r.product_kwargs["barrier_config"]["ko_rate"] == 0.0  # KO leg defaulted, not missing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_product_booking.py -k "rejects_both_barrier or nested_plus_flat or phoenix_builds_without_ko_rate" -v`
Expected: FAIL (both-spellings builds ok / absolute silently wins; Phoenix without ko_rate reports it missing).

- [ ] **Step 3: Implement — representation-complete guard via a `_resolve_barrier` helper**

```python
# product_builders.py
def _resolve_barrier(terms: dict, abs_key: str, pct_key: str, path: str,
                     initial: float, out: "_Out") -> float | None:
    """Resolve one barrier from its flat abs/pct spellings AND its canonical nested/dotted
    path. >1 distinct representation supplied → ambiguity: record in out.missing, return None."""
    def _present(key):  # nested-or-literal-dotted lookup, mirrors completeness._lookup
        if key in terms:
            return terms[key]
        node = terms
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node
    direct = _num(terms.get(abs_key))
    pct = _num(terms.get(pct_key))
    nested = _num(_present(path)) if path not in (abs_key, pct_key) else None
    supplied = [v for v in (direct, (round(initial * pct / 100.0, 6) if pct is not None else None),
                            nested) if v is not None]
    if len(supplied) > 1:
        out.missing.append(f"{path} (supply ONE of {abs_key}/{pct_key})")
        return None
    return supplied[0] if supplied else None
```

Replace each `_abs_barrier(...)` call with `_resolve_barrier(terms, abs_key, pct_key, path, initial, out)` in `_build_snowball` (ko/ki), `_build_koreset` (post_ko), `_build_phoenix` (coupon), `_build_range_accrual` (lower/upper), passing the family's dotted `path`. `_abs_barrier` may remain for any non-conflict-checked callers, or be removed if unused.

**Phoenix `ko_rate` builder fix (finding 3) — same task:** in `_build_phoenix`, replace the guarded default with an unconditional one:
```python
    if terms.get("ko_rate") is None:
        out.product_kwargs.setdefault("barrier_config", {})["ko_rate"] = 0.0
        if "barrier_config.ko_rate" in out.missing:
            out.missing.remove("barrier_config.ko_rate")
```
so a Phoenix omitting `ko_rate` builds (KO leg pays 0), rather than reporting `barrier_config.ko_rate` missing.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_product_booking.py -q && .venv/bin/python -m pytest tests/test_product_contracts.py -q`
Expected: PASS (single-spelling builds unaffected; both-spellings now rejected).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_booking.py
git commit -m "feat(products): builder rejects both abs+pct spellings of a barrier (ambiguity guard)"
```

---

### Task 6: Publish families from the schema tool + round-trip fidelity tests

**Files:**
- Modify: `backend/app/tools/product_term_schema.py` (`_SCHEMA_FAMILIES` + emit `input_names`/`requires_when`)
- Test: `tests/test_product_term_schema.py`

**Interfaces:**
- Consumes: `contract.fields`, `flat_aliases`, `resolve_enum_values`, `required_fields`, `one_of_groups`.
- Produces: `get_product_term_schema` returns a real schema for all 6 families; each barrier field lists both `input_names`; conditional fields carry `requires_when`.

- [ ] **Step 1: Write the failing tests (fidelity is the gate)**

```python
# tests/test_product_term_schema.py
def test_schema_available_for_all_v2_families():
    from app.tools.product_term_schema import get_product_term_schema
    for fam in ["SnowballOption","KnockOutResetSnowballOption","PhoenixOption",
                "RangeAccrualOption","Futures","SpotInstrument"]:
        out = get_product_term_schema.func(fam)
        assert "fields" in out and out.get("schema_available") is not False, f"{fam} not published"


def test_schema_barrier_lists_both_input_names():
    from app.tools.product_term_schema import get_product_term_schema
    out = get_product_term_schema.func("SnowballOption")
    ko = next(f for f in out["fields"] if f["name"] == "ko_barrier")
    assert set(ko["input_names"]) == {"ko_barrier", "ko_barrier_pct"}


def test_schema_marks_conditional_fields():
    from app.tools.product_term_schema import get_product_term_schema
    out = get_product_term_schema.func("SnowballOption")
    kod = next(f for f in out["fields"] if f["name"] == "ko_observation_dates")
    assert kod["requires_when"] == {"field": "observation_frequency", "equals": "CUSTOM"}


# --- ROUND-TRIP FIDELITY: build from the advertised schema, assert faithful structure ---
def _snowball_terms(pct=False):
    b = {"ko_barrier_pct": 103, "ki_barrier_pct": 70} if pct else {"ko_barrier": 103, "ki_barrier": 70}
    return {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "ko_rate": 0.08, "lockup_months": 3,
            "ki_convention": "DAILY", **b}

def test_phoenix_builds_faithful_coupon_config_both_spellings():
    from app.services.domains.product_builders import build_product
    for pct in (False, True):
        terms = {**_snowball_terms(pct),
                 **({"coupon_barrier_pct": 80} if pct else {"coupon_barrier": 80}),
                 "coupon_rate": 0.02}
        r = build_product("PhoenixOption", terms, prebuilt=False)
        assert r.ok, r
        cc = r.product_kwargs["coupon_config"] if hasattr(r, "product_kwargs") else None
        # faithful: coupon_config present with the coupon economics (not just .ok)
        assert cc and cc["coupon_rate"] == 0.02
        # pct spelling resolved to abs = pct% of initial
        if pct:
            assert cc["coupon_barrier"] == 80.0  # 80% of 100

def test_koreset_builds_post_barrier_config():
    from app.services.domains.product_builders import build_product
    terms = {**_snowball_terms(pct=False), "post_ko_barrier": 105, "post_ko_rate": 0.09}
    r = build_product("KnockOutResetSnowballOption", terms, prebuilt=False)
    assert r.ok, r
    assert r.product_kwargs["post_barrier_config"]["ko_barrier"] == 105

def test_range_accrual_builds_range_config_and_exact_obs_count():  # finding 1
    from app.services.domains.product_builders import build_product
    base = {"initial_price": 100, "maturity_years": 2, "lower_barrier": 90,
            "upper_barrier": 110, "accrual_rate": 0.05}
    r = build_product("RangeAccrualOption", {**base, "observation_frequency": "DAILY"}, prebuilt=False)
    assert r.ok and r.product_kwargs["range_config"]["lower_barrier"] == 90
    assert r.product_kwargs["num_observations"] == round(2 * 252)   # DAILY
    r2 = build_product("RangeAccrualOption", {**base, "observation_frequency": "MONTHLY"}, prebuilt=False)
    assert r2.product_kwargs["num_observations"] == round(2 * 12)   # MONTHLY
    # pct spelling resolves to abs (% of initial)
    r3 = build_product("RangeAccrualOption",
                       {**{k: v for k, v in base.items() if k not in ("lower_barrier", "upper_barrier")},
                        "lower_barrier_pct": 90, "upper_barrier_pct": 110,
                        "observation_frequency": "DAILY"}, prebuilt=False)
    assert r3.product_kwargs["range_config"]["lower_barrier"] == 90.0  # 90% of 100

def test_snowball_ki_dropped_when_convention_none():
    from app.services.domains.product_builders import build_product
    terms = {k: v for k, v in _snowball_terms(pct=False).items() if k != "ki_barrier"}
    terms["ki_convention"] = "NONE"
    r = build_product("SnowballOption", terms, prebuilt=False)
    assert r.ok, r
    assert "ki_barrier" not in r.product_kwargs.get("barrier_config", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -k "schema_available_for_all_v2 or barrier_lists_both or marks_conditional or builds_faithful or post_barrier or range_config or ki_dropped" -v`
Expected: FAIL (families not in `_SCHEMA_FAMILIES`; output lacks `input_names`/`requires_when`).

- [ ] **Step 3: Implement the schema tool changes**

```python
# product_term_schema.py
_SCHEMA_FAMILIES = frozenset({
    "BarrierOption", "EuropeanVanillaOption", "AmericanOption", "AsianOption",
    "CashOrNothingDigitalOption", "SingleSharkfinOption", "DoubleSharkfinOption",
    "OneTouchOption", "DoubleOneTouchOption",
    # V2 nested-config + DeltaOne
    "SnowballOption", "KnockOutResetSnowballOption", "PhoenixOption",
    "RangeAccrualOption", "Futures", "SpotInstrument",
})

# in the fields loop, extend each entry:
    entry = {
        "name": spec.input_name,
        "kind": spec.kind,
        "required": spec.one_of is None and spec.requires_when is None and path in required_paths,
        "description": spec.description,
    }
    if spec.input_aliases and len(spec.input_aliases) > 1:
        entry["input_names"] = list(spec.input_aliases)  # abs + pct spellings
    if spec.requires_when is not None:
        field, value = spec.requires_when
        entry["requires_when"] = ({"field": field, "not_equals": value[1:]}
                                  if value.startswith("!")
                                  else {"field": field, "equals": value})
    ...
```
Keep the existing `enum_values`/`one_of`/`default` handling. The `required_groups` stay for TRUE one_of only (maturity); barrier aliases surface via `input_names`, not groups.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Expected: PASS (all V1 tests still green; V2 families published; fidelity asserted for both spellings).

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/product_term_schema.py tests/test_product_term_schema.py
git commit -m "feat(tools): publish nested-config + DeltaOne schemas (input aliases + requires_when + fidelity)"
```

---

### Task 7: Anti-loop regression + coupling/count updates + docs

**Files:**
- Test: `tests/test_product_term_schema.py` (anti-loop), plus any pinned-count test that references `_SCHEMA_FAMILIES` size or tool count.
- Modify: `CHANGELOG.md`; `backend/app/skills/workflows/products/build-product/SKILL.md` (only if a nudge tweak is needed and stays under the 500-token body cap).

**Interfaces:**
- Consumes: everything above.
- Produces: a regression proving the Run #24 Phoenix loop can't recur; green full suite.

- [ ] **Step 1: Write the anti-loop regression test**

```python
# tests/test_product_term_schema.py
def test_schema_then_completeness_agree_for_flat_phoenix():
    """The Run #24 loop: schema advises flat keys; completeness must then read them
    complete (not still-missing) — schema/completeness/builder in agreement."""
    from app.tools.product_term_schema import get_product_term_schema
    from app.tools.term_completeness import check_term_completeness
    from app.services.domains.product_builders import build_product
    get_product_term_schema.func("PhoenixOption")   # advises ko_barrier, coupon_barrier, ...
    terms = {"initial_price": 100, "maturity_years": 2, "trade_start_date": "2026-01-05",
             "observation_frequency": "MONTHLY", "ko_barrier": 103, "ki_barrier": 70,
             "ko_rate": 0.08, "lockup_months": 3, "ki_convention": "DAILY",
             "coupon_barrier": 80, "coupon_rate": 0.02}
    assert check_term_completeness.func("PhoenixOption", terms)["complete"] is True
    assert build_product("PhoenixOption", terms, prebuilt=False).ok is True
```

- [ ] **Step 2: Run + find pinned-count breakage**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py -q`
Then the broader gate: `.venv/bin/python -m pytest tests/test_product_contracts.py tests/test_product_booking.py tests/test_capability_assignments.py -q`
Fix any pinned count (e.g. a `_SCHEMA_FAMILIES` size assertion) to the new value (15). No new tool is registered, so `QUANT_AGENT_TOOLS` count is unchanged (still 99) — confirm.

- [ ] **Step 3: Update docs**

- `CHANGELOG.md` under `[Unreleased]` → Added: "`get_product_term_schema` now covers the nested-config (Snowball/KOReset/Phoenix/RangeAccrual) + DeltaOne (Futures/SpotInstrument) families — flat input aliases (abs|pct barriers) resolve to their dotted contract paths across schema/completeness/builder; both-spellings ambiguity is rejected."
- SKILL.md: only if the existing nudge implies flat-only families; if it already says "call get_product_term_schema before build_product," no change needed (keep under the 500-token body cap — re-count if edited).

- [ ] **Step 4: Full regression**

Run: `.venv/bin/python -m pytest tests/test_product_term_schema.py tests/test_product_contracts.py tests/test_product_booking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ CHANGELOG.md backend/app/skills/workflows/products/build-product/SKILL.md
git commit -m "test(products): anti-loop regression + count/docs for term-schema V2"
```

---

## Self-Review

- **Spec coverage:** Decisions 1–5 all map to tasks — families (T3/T4), alias-set + both-spellings (T1/T4/T5), requires_when (T1/T2/T6), coherence fix (T2/T4), enum round-trip (T4/T6). Failure-handling items (economically-wrong enums, contradictory economics, coherence regression) → fidelity tests (T6), conflict tests (T4/T5), anti-loop (T7).
- **Placeholder scan:** the two `...` test bodies in T6 Step 1 are explicitly flagged to be filled with concrete analogues before commit — not shippable as-is.
- **Type consistency:** `flat_aliases` is list-valued (`dict[str, tuple[str,...]]`) everywhere (T1/T2/T4/T6); `requires_when` output shape `{field, equals|not_equals}` consistent (T1 spec, T6 emit); `_BARRIER_CONFLICT` sentinel handled at every `_abs_barrier` call site (T5).
- **Ordering:** T2 depends on T1; T3/T4 add data T2's logic consumes; T5 (builder) and T4 (completeness) together close the both-spellings loop; T6 publishes; T7 regresses. Each task ends green.

## Execution Handoff

This plan is executed inline by the feature-flow pipeline (Stage 5) via `superpowers:executing-plans` in an isolated worktree.
