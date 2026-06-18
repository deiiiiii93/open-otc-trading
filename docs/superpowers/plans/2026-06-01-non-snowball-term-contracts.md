# Non-Snowball Term Contracts Implementation Plan (parity gap b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every non-snowball family a declarative `FamilyContract` (the data source of truth for its required inputs) and make the builder↔contract consistency net cover **all 15** families — closing parity gap (b) and the latent KnockOutReset/Phoenix drift the snowball-only net never caught.

**Background:** `product_contracts.py` declares per-family term contracts as DATA (`required_bound` / `defaulted` / `solvable`). Today only the snowball family has one, and the consistency test (`tests/test_product_contracts.py::test_builder_missing_keys_are_declared_in_the_contract`) checks **only** `SnowballOption`. Two consequences: (1) the 12 non-snowball families have no declared contract; (2) `KnockOutResetSnowballOption` and `PhoenixOption` *share* `_SNOWBALL_CONTRACT` but their builders report keys it doesn't declare (`post_barrier_config.ko_barrier/ko_rate`; `coupon_config.coupon_barrier/coupon_rate`) — undetected because the net never checks them. This plan adds the 12 contracts, gives KO-reset/Phoenix their own accurate contracts, and parametrizes the net over the whole registry.

**Architecture:** Contracts stay **declarative + drift-guarded, not the source of `missing`** — builders keep computing `missing` imperatively; the contract declares the *union* of keys a family can report, and the test asserts `build_product(family, {}).missing ⊆ contract.required_bound` for every family plus completeness (every `_REGISTRY` family has a contract). No change to `build_product`, the builders, or how `missing` is computed → zero behavior risk. `required_bound` values below are **empirically derived** from `build_product(family, {}).missing` on `main` @ `9c9f171`; `defaulted` from the builder source; `solvable` is left `()` for the non-snowball families (advisory only — not test-enforced, and `filter_solved` uses the runtime `solve_target`, not `contract.solvable`).

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`). No frontend changes.

**Prerequisite:** None — everything is on `main` (`9c9f171`). **Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns `main`. Execute in an isolated worktree (superpowers:using-git-worktrees) — no git remote, so branch from local `main` into an external path, e.g. `git worktree add /Users/fuxinyao/ots-wt-contracts -b feat/non-snowball-term-contracts 9c9f171`, and run pytest with the main venv: `cd <worktree> && /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest …`. NOTE: `python -c` spikes import `app` from the MAIN checkout (venv `.pth`), not the worktree — only pytest (rootdir=worktree) runs worktree code; to spike worktree code set `PYTHONPATH=<worktree>/backend`.

**Out of scope:** wiring `build_product` to *consume* contracts for `missing` (a separate, larger refactor — builders stay the imperative source); populating `solvable` for non-snowball families (advisory; deferred until a channel designates a solver target); parity gap (d) cross-channel equivalence-net extension.

---

## Empirical required-bound table (verified on `main` @ 9c9f171)

`build_product(family, {}).missing` — the exact maximal required set per family (no conditional keys for the non-snowball families, so this equals `required_bound`):

| family | required_bound (empirical) |
|---|---|
| EuropeanVanillaOption | initial_price, maturity_years, strike |
| AmericanOption | initial_price, maturity_years, strike |
| AsianOption | initial_price, maturity_years, strike |
| CashOrNothingDigitalOption | initial_price, maturity_years, strike, cash_payoff |
| BarrierOption | initial_price, maturity_years, strike, barrier |
| SingleSharkfinOption | initial_price, maturity_years, strike, barrier |
| DoubleSharkfinOption | initial_price, maturity_years, strike, lower_barrier, upper_barrier |
| OneTouchOption | initial_price, maturity_years, barrier, cash_payoff |
| DoubleOneTouchOption | initial_price, maturity_years, upper_barrier, lower_barrier, cash_payoff |
| RangeAccrualOption | initial_price, maturity_years, range_config.lower_barrier, range_config.upper_barrier, range_config.accrual_rate |
| Futures | initial_price, underlying |
| SpotInstrument | initial_price, underlying |
| KnockOutResetSnowballOption | snowball base **+ post_barrier_config.ko_barrier, post_barrier_config.ko_rate** |
| PhoenixOption | snowball base **+ coupon_config.coupon_barrier, coupon_config.coupon_rate** |

(snowball base = the existing `_SNOWBALL_CONTRACT.required_bound`.)

---

## File Structure

- `backend/app/services/domains/product_contracts.py` — add 12 non-snowball `FamilyContract`s to `_CONTRACTS`; replace the shared KO-reset/Phoenix entries with their own (base + extra) contracts. One block.
- `tests/test_product_contracts.py` — replace the snowball-only subset test with a registry-parametrized net (completeness + subset over all families); keep the existing snowball-declaration + `filter_solved` tests.
- No production builder/`build_product` change.

---

### Task 1: Parametrized consistency net (the drift guard)

**Files:**
- Test: `tests/test_product_contracts.py`

**Context:** The existing `test_builder_missing_keys_are_declared_in_the_contract` checks only `SnowballOption`. Replace it with a net parametrized over `_REGISTRY` that asserts (a) every buildable family has a contract and (b) its empty-terms missing set is a subset of its declared `required_bound`. Written first, it goes RED for the 12 contract-less families and for KO-reset/Phoenix (whose shared contract under-declares).

- [ ] **Step 1: Write the failing test.** In `tests/test_product_contracts.py`, add the `_REGISTRY` import and replace `test_builder_missing_keys_are_declared_in_the_contract` with the two parametrized tests below. Keep `test_snowball_contract_declares_full_required_bound_set`, `test_filter_solved_exempts_the_designated_target_only`, and `test_filter_solved_noop_without_target` unchanged.

```python
import pytest

from app.services.domains.product_builders import _REGISTRY, build_product


@pytest.mark.parametrize("family", sorted(_REGISTRY))
def test_every_buildable_family_has_a_contract(family):
    """Completeness: no family may be built without a declared term contract."""
    assert pc.contract_for(family) is not None, family


@pytest.mark.parametrize("family", sorted(_REGISTRY))
def test_builder_missing_keys_are_declared_in_the_contract(family):
    """Consistency net (all families): every key a builder can report missing must
    be a declared required-bound contract key — so builder and contract cannot
    drift. Empty terms surface a family's full required set (no non-snowball family
    has conditional requirements; snowball's CUSTOM-only key is declared anyway)."""
    contract = pc.contract_for(family)
    assert contract is not None, family
    result = build_product(family, {})
    undeclared = [m for m in result.missing if m not in contract.required_bound]
    assert undeclared == [], (family, undeclared)
```

- [ ] **Step 2: Run to verify failure.**
Run: `python -m pytest tests/test_product_contracts.py -v`
Expected: `test_every_buildable_family_has_a_contract` FAILS for the 12 contract-less families (AmericanOption, AsianOption, BarrierOption, CashOrNothingDigitalOption, DoubleOneTouchOption, DoubleSharkfinOption, EuropeanVanillaOption, Futures, OneTouchOption, RangeAccrualOption, SingleSharkfinOption, SpotInstrument). `test_builder_missing_keys_are_declared_in_the_contract` FAILS for those 12 (contract None) AND for `KnockOutResetSnowballOption` (undeclared `post_barrier_config.ko_barrier/ko_rate`) and `PhoenixOption` (undeclared `coupon_config.coupon_barrier/coupon_rate`). SnowballOption passes both.

- [ ] **Step 3: Commit the failing test.**
```bash
git add tests/test_product_contracts.py
git commit -m "test(contracts): parametrize the builder<->contract net over all families (RED)"
```

---

### Task 2: Add the 12 non-snowball contracts + fix KO-reset/Phoenix

**Files:**
- Modify: `backend/app/services/domains/product_contracts.py` (`_CONTRACTS` block)

**Context:** Add a `FamilyContract` per non-snowball family with the empirically-verified `required_bound`, builder-derived `defaulted`, and `solvable=()` (advisory, deferred). Replace the shared KO-reset/Phoenix entries with their own contracts = snowball base + their extra required keys. `FamilyContract` fields: `quantark_class, required_bound, defaulted, solvable` (all tuples).

- [ ] **Step 1: Add the contracts.** In `backend/app/services/domains/product_contracts.py`, replace the existing `_CONTRACTS` dict (currently mapping the three snowball-family classes to `_SNOWBALL_CONTRACT`) with the block below. Keep `_SNOWBALL_CONTRACT`, `contract_for`, and `filter_solved` as-is.

```python
# KO-reset and Phoenix build ON the snowball synthesizer and add their own
# required keys imperatively (post-KI reset leg / coupon leg). They get their own
# contracts = the snowball base + those extra required keys, so the consistency
# net does not under-declare them.
_KO_RESET_CONTRACT = FamilyContract(
    quantark_class="KnockOutResetSnowballOption",
    required_bound=_SNOWBALL_CONTRACT.required_bound
    + ("post_barrier_config.ko_barrier", "post_barrier_config.ko_rate"),
    defaulted=_SNOWBALL_CONTRACT.defaulted,
    solvable=_SNOWBALL_CONTRACT.solvable,
)
_PHOENIX_CONTRACT = FamilyContract(
    quantark_class="PhoenixOption",
    required_bound=_SNOWBALL_CONTRACT.required_bound
    + ("coupon_config.coupon_barrier", "coupon_config.coupon_rate"),
    defaulted=_SNOWBALL_CONTRACT.defaulted + ("memory_coupon",),
    solvable=_SNOWBALL_CONTRACT.solvable,
)

# Vanilla family: _build_vanilla descendants require S0 + maturity + strike and
# default option_type/contract_multiplier. `solvable` is advisory and left empty
# for the non-snowball families (no consumer reads contract.solvable for them yet;
# filter_solved uses the runtime solve_target).
_VANILLA_REQUIRED = ("initial_price", "maturity_years", "strike")
_VANILLA_DEFAULTED = ("option_type", "contract_multiplier")

_CONTRACTS: dict[str, FamilyContract] = {
    "SnowballOption": _SNOWBALL_CONTRACT,
    "KnockOutResetSnowballOption": _KO_RESET_CONTRACT,
    "PhoenixOption": _PHOENIX_CONTRACT,
    "EuropeanVanillaOption": FamilyContract(
        "EuropeanVanillaOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, ()
    ),
    "AmericanOption": FamilyContract(
        "AmericanOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED, ()
    ),
    "AsianOption": FamilyContract(
        "AsianOption", _VANILLA_REQUIRED, _VANILLA_DEFAULTED + ("averaging_frequency",), ()
    ),
    "CashOrNothingDigitalOption": FamilyContract(
        "CashOrNothingDigitalOption",
        _VANILLA_REQUIRED + ("cash_payoff",),
        _VANILLA_DEFAULTED,
        (),
    ),
    "BarrierOption": FamilyContract(
        "BarrierOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("barrier_type", "rebate"),
        (),
    ),
    "SingleSharkfinOption": FamilyContract(
        "SingleSharkfinOption",
        _VANILLA_REQUIRED + ("barrier",),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
    ),
    "DoubleSharkfinOption": FamilyContract(
        "DoubleSharkfinOption",
        _VANILLA_REQUIRED + ("lower_barrier", "upper_barrier"),
        _VANILLA_DEFAULTED + ("participation_rate",),
        (),
    ),
    "OneTouchOption": FamilyContract(
        "OneTouchOption",
        ("initial_price", "maturity_years", "barrier", "cash_payoff"),
        ("barrier_direction", "touch_type"),
        (),
    ),
    "DoubleOneTouchOption": FamilyContract(
        "DoubleOneTouchOption",
        ("initial_price", "maturity_years", "upper_barrier", "lower_barrier", "cash_payoff"),
        ("touch_type",),
        (),
    ),
    "RangeAccrualOption": FamilyContract(
        "RangeAccrualOption",
        (
            "initial_price",
            "maturity_years",
            "range_config.lower_barrier",
            "range_config.upper_barrier",
            "range_config.accrual_rate",
        ),
        ("observation_frequency", "contract_multiplier"),
        (),
    ),
    "Futures": FamilyContract(
        "Futures",
        ("initial_price", "underlying"),
        ("contract_multiplier", "maturity_years", "basis", "basis_decay_rate",
         "market_price", "contract_code"),
        (),
    ),
    "SpotInstrument": FamilyContract(
        "SpotInstrument",
        ("initial_price", "underlying"),
        ("deltaone_type", "instrument_code", "exchange", "contract_multiplier"),
        (),
    ),
}
```

- [ ] **Step 2: Update the module docstring.** Change the line `Only the snowball family is data-driven today; other families keep imperative\n`missing` until their channel migration.` to reflect full coverage:

```python
Every buildable family now has a declared contract; the builder<->contract
consistency test (`tests/test_product_contracts.py`) parametrizes over the whole
registry so builder and contract cannot drift. Contracts remain declarative —
builders still compute `missing` imperatively; the contract declares the union of
keys a family can report.
```

- [ ] **Step 3: Run the consistency net.**
Run: `python -m pytest tests/test_product_contracts.py -v`
Expected: PASS — all families have a contract; every empty-terms missing set is a subset of its declared `required_bound` (KO-reset/Phoenix now declare their extra keys).

- [ ] **Step 4: Commit.**
```bash
git add backend/app/services/domains/product_contracts.py
git commit -m "feat(contracts): declare term contracts for all 12 non-snowball families + fix KO-reset/Phoenix"
```

---

### Task 3: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite.**
Run: `python -m pytest -q`
Expected: PASS (no regressions). This change is data-only (contracts + a test); no builder or `build_product` behavior changed, so the gate/booking/pricing/cross-channel suites stay green.

- [ ] **Step 2: Finish the development branch.**
Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present options (no git remote → merge-locally / keep / discard; PR unavailable), execute the choice. Given the concurrent agent on `main`, check `main` is still FF-able before merging.

---

## Notes / risks

- **Zero behavior risk.** No builder, `build_product`, or `missing`-computation change — contracts are declarative data and a test. The only way the full suite breaks is if a builder already reports a key this plan failed to declare; the empirical table was derived from `build_product(family, {})` so that set is covered, and Task 1's net would catch any miss.
- **The KO-reset/Phoenix fix is the real find.** They shared the snowball contract while their builders report `post_barrier_config.*` / `coupon_config.*`; the snowball-only net never checked them. Parametrizing the net exposes it (Task 1 RED) and the own-contract split closes it (Task 2).
- **`solvable` deliberately empty for non-snowball.** It is advisory (not test-enforced; `filter_solved` uses the runtime `solve_target`). Populating it per family from the try-solve registry `quote_fields` is a separate, optional refinement — left out to avoid asserting canonical paths this plan can't verify cheaply.
- **Empty-terms probe is sufficient.** No non-snowball family has conditional requirements, so `build_product(family, {}).missing` is its maximal required set. Snowball's one conditional key (`ko_observation_dates`, CUSTOM-only) is declared in the base contract anyway, so the subset assertion holds.
