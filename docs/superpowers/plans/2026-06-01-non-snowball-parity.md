# Non-Snowball Parity Implementation Plan (Gate + Builder Edge Tests)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the non-snowball families up to parity on the two dimensions where snowball got special treatment: **(a)** every `build_product`-supported family the desk can book is validated by the booking gate, and **(c)** the high-volume non-snowball builders (digital, barrier, single/double sharkfin) have real edge-case test depth, not just happy-path smoke tests.

**Background:** The unified-product-schema migration is complete (`main` @ `cbc53c8`) — all 15 families build through the single `build_product` producer. But an audit found two snowball-vs-rest asymmetries: the booking gate (`_GATED_BOOKING_TYPES`) validates **9 of 15** families — 6 are un-gated (Futures, SpotInstrument, AsianOption, OneTouchOption, RangeAccrualOption, DoubleOneTouchOption) — and the non-snowball builders have ~8 test functions total vs snowball's 14 (mostly happy-path). This plan closes (a) and (c). (Two other gaps — data-driven term contracts for non-snowball families, and extending the cross-channel equivalence net — are explicitly **out of scope**, separate follow-ups.)

**Architecture:** Part (a) is a one-line set extension plus tests — the booking gate already routes non-snowball families through `build_product(prebuilt=True)` (validate-and-wrap); a de-risk spike confirmed **all 6 un-gated families round-trip cleanly** (build via synthesis, then re-validate through the gate's prebuilt path: `gate.ok=True`), and the booking input allowlist (`_ALLOWED_PRODUCT_FAMILIES`) already permits all 6. Part (c) is pure test additions against the existing builders — no production change — pinning real behavior captured by spikes.

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`). QuantArk (vendored). No frontend changes.

**Prerequisite:** None — everything is on `main` (`cbc53c8`). **Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns `main`. Execute in an isolated worktree (superpowers:using-git-worktrees) — no git remote, so branch from local `main` into an external path, e.g. `git worktree add /Users/fuxinyao/ots-wt-parity -b feat/non-snowball-parity main`, and run pytest with the main venv: `cd <worktree> && /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest …`.

**Out of scope:** (b) term contracts for non-snowball families; (d) cross-channel equivalence-net extension; any builder *behavior* change (this plan pins existing behavior — if an edge test reveals a real bug, STOP and surface it, don't silently fix).

---

## Spike results baked into this plan (verified on `main` @ cbc53c8)

| Probe | Result (assert these) |
|---|---|
| 6 un-gated families: build then `build_product(family, kwargs, prebuilt=True)` | all `ok=True`; families: futures, spot, asian, touch, range_accrual, touch |
| `BarrierOption` × `{DOWN_OUT, UP_OUT, DOWN_IN, UP_IN}` | all `ok=True` |
| `BarrierOption` missing `barrier` | `ok=False`, `missing=['barrier']` |
| `CashOrNothingDigitalOption` missing `cash_payoff` | `ok=False`, `missing=['cash_payoff']` |
| `CashOrNothingDigitalOption` `cash_payoff=7.5`, PUT | `ok=True`, `payout==7.5`, `cash_payoff` not in kwargs |
| `DoubleSharkfinOption` inverted `lower=120 > upper=80` | `ok=False`, error contains `"Lower barrier (120.0) must be less than upper barrier (80.0)"` |
| `DoubleSharkfinOption` normal `lower<upper` | `ok=True` |
| `SingleSharkfinOption` missing `barrier` | `ok=False`, `missing=['barrier']` |
| `SingleSharkfinOption` default participation | `ok=True`, `participation_rate==1.0` |
| `_ALLOWED_PRODUCT_FAMILIES` (booking input gate) | already contains asian, futures, range_accrual, spot, touch |

---

## File Structure

- `backend/app/services/domains/booking.py` — Part (a): add the 6 families to `_GATED_BOOKING_TYPES` (one block). `normalize`/`validate_booking_product_spec` already read that set, so no other change.
- `tests/test_product_booking.py` — Part (a) tests: the newly-gated families validate-and-wrap; an invalid one is rejected with a precise error.
- `tests/test_product_builders.py` — Part (c) tests: digital, barrier, single/double sharkfin edge cases.
- No other production files change.

---

### Task 1 (Part a): Gate the 6 remaining families

**Files:**
- Modify: `backend/app/services/domains/booking.py` (`_GATED_BOOKING_TYPES` block)
- Test: `tests/test_product_booking.py`

**Context:** `_GATED_BOOKING_TYPES` currently = the 9 OTC/snowball families. `normalize_booking_product_spec` routes every member through `build_product` (snowball-family `prebuilt=False` auto-detect; others `prebuilt=True` validate-and-wrap), and `validate_booking_product_spec` guards on the same set. Adding the 6 means a booking of those families is now validated, not trusted as-is. The spike proved all 6 round-trip; the input allowlist already permits them. `ProductBookingSpec` fields: `asset_class, product_family, quantark_class, underlying, currency, terms, components, display_name, source_payload`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_product_booking.py` (reuse its existing imports; add `import pytest` only if absent):

```python
from app.services.domains.product_builders import build_product
from app.services.domains.booking import (
    ProductBookingSpec,
    normalize_booking_product_spec,
)


_NEWLY_GATED = [
    ("Futures", {"initial_price": 100.0, "underlying": "000905.SH", "maturity_years": 1.0}),
    ("SpotInstrument", {"initial_price": 100.0, "underlying": "000905.SH"}),
    ("AsianOption", {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
                     "maturity_years": 1.0, "averaging_frequency": "MONTHLY"}),
    ("OneTouchOption", {"initial_price": 100.0, "barrier": 120.0, "cash_payoff": 10.0,
                        "barrier_direction": "UP", "maturity_years": 1.0}),
    ("RangeAccrualOption", {"initial_price": 100.0, "maturity_years": 1.0,
                            "lower_barrier_pct": 90.0, "upper_barrier_pct": 110.0, "accrual_rate": 0.1}),
    ("DoubleOneTouchOption", {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
                              "cash_payoff": 10.0, "maturity_years": 1.0}),
]


@pytest.mark.parametrize("family, flat", _NEWLY_GATED)
def test_booking_gate_validates_newly_gated_family(family, flat):
    built = build_product(family, flat)
    assert built.ok, built.validation
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=built.product_spec.product_family,
        quantark_class=family,
        underlying="000905.SH",
        currency="CNY",
        terms=dict(built.product_kwargs),
        components=[],
        display_name=None,
        source_payload={},
    )
    # complete + valid -> validate-and-wrap returns the same terms (no synthesis)
    normalized = normalize_booking_product_spec(spec)
    assert normalized.terms == spec.terms


def test_booking_gate_rejects_invalid_one_touch():
    # OneTouchOption is now gated: an incomplete termsheet (no barrier) must be
    # rejected by the gate, not persisted unvalidated.
    spec = ProductBookingSpec(
        asset_class="equity", product_family="touch", quantark_class="OneTouchOption",
        underlying="000905.SH", currency="CNY",
        terms={"barrier_direction": "UP", "touch_type": "ONE_TOUCH"},  # no barrier/maturity
        components=[], display_name=None, source_payload={},
    )
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    assert "OneTouchOption" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure.**
Run: `python -m pytest "tests/test_product_booking.py::test_booking_gate_rejects_invalid_one_touch" "tests/test_product_booking.py::test_booking_gate_validates_newly_gated_family" -v`
Expected: the parametrized `validates_newly_gated_family` cases PASS today (non-gated → `normalize` is a no-op → returns spec unchanged → `terms == terms` trivially holds — a characterization guard). `test_booking_gate_rejects_invalid_one_touch` FAILS today (OneTouchOption not gated → no-op returns the spec, no ValueError).

- [ ] **Step 3: Extend `_GATED_BOOKING_TYPES`.** In `backend/app/services/domains/booking.py`, add the 6 families to the `_GATED_BOOKING_TYPES` set. The current block is:

```python
_GATED_BOOKING_TYPES = _SNOWBALL_BOOKING_TYPES | {
    "PhoenixOption",
    "EuropeanVanillaOption",
    "AmericanOption",
    "CashOrNothingDigitalOption",
    "BarrierOption",
    "SingleSharkfinOption",
    "DoubleSharkfinOption",
}
```

Replace with:

```python
_GATED_BOOKING_TYPES = _SNOWBALL_BOOKING_TYPES | {
    "PhoenixOption",
    "EuropeanVanillaOption",
    "AmericanOption",
    "CashOrNothingDigitalOption",
    "BarrierOption",
    "SingleSharkfinOption",
    "DoubleSharkfinOption",
    # parity pass: every build_product-supported family the desk can book is now
    # validated at booking, not just the OTC-emitted set. All six validate-and-wrap
    # (prebuilt=True); a de-risk spike confirmed each round-trips cleanly.
    "Futures",
    "SpotInstrument",
    "AsianOption",
    "OneTouchOption",
    "RangeAccrualOption",
    "DoubleOneTouchOption",
}
```

- [ ] **Step 4: Run the new tests + the booking/positions suites.**
Run: `python -m pytest tests/test_product_booking.py tests/test_services_domains_positions.py tests/test_position_import_pricing.py tests/test_tools_positions.py -q`
Expected: PASS, including both new tests. If an EXISTING booking/positions test breaks, it is pinning a previously-unvalidated booking of one of the 6 families with terms that don't validate — read it: incomplete fixture (fix the fixture to a complete termsheet) vs a real latent invalid-terms bug (STOP and report BLOCKED with the family + validator error). Do NOT weaken the gate.

- [ ] **Step 5: Commit.**
```bash
git add backend/app/services/domains/booking.py tests/test_product_booking.py
git commit -m "feat(booking): gate the remaining 6 families (futures/spot/asian/touch/range/double-touch)"
```

---

### Task 2 (Part c): Digital + barrier builder edge cases

**Files:**
- Test only: `tests/test_product_builders.py`

**Context:** `_build_digital` inherits vanilla and requires `cash_payoff` → `payout` (a QuantArk rename). `_build_barrier` inherits vanilla, requires `barrier`, defaults `barrier_type="DOWN_OUT"` and `rebate=0.0`. Today each has ~1 happy-path test. Add edge coverage pinning the spike-verified behavior. These are characterization tests — they should pass on first run; a failure is a real behavior change to investigate.

- [ ] **Step 1: Write the tests.** Append to `tests/test_product_builders.py` (reuse the file's existing `build_product` import and `pytest`):

```python
def test_digital_missing_cash_payoff_reported():
    result = build_product(
        "CashOrNothingDigitalOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "cash_payoff" in result.missing


def test_digital_maps_cash_payoff_to_payout_for_put():
    result = build_product(
        "CashOrNothingDigitalOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "PUT",
         "maturity_years": 1.0, "cash_payoff": 7.5},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["payout"] == 7.5         # cash_payoff -> payout rename
    assert "cash_payoff" not in result.product_kwargs
    assert result.product_kwargs["option_type"] == "PUT"


@pytest.mark.parametrize(
    "barrier_type, barrier",
    [("DOWN_OUT", 80.0), ("UP_OUT", 120.0), ("DOWN_IN", 80.0), ("UP_IN", 120.0)],
)
def test_barrier_all_four_types_build_and_validate(barrier_type, barrier):
    result = build_product(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "barrier": barrier, "barrier_type": barrier_type},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["barrier_type"] == barrier_type
    assert result.product_kwargs["barrier"] == barrier


def test_barrier_missing_barrier_reported():
    result = build_product(
        "BarrierOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "barrier" in result.missing


def test_barrier_rebate_defaults_zero_and_honors_explicit():
    base = {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
            "maturity_years": 1.0, "barrier": 80.0}
    assert build_product("BarrierOption", base).product_kwargs["rebate"] == 0.0
    assert build_product("BarrierOption", {**base, "rebate": 2.5}).product_kwargs["rebate"] == 2.5
```

- [ ] **Step 2: Run.**
Run: `python -m pytest tests/test_product_builders.py -k "digital or barrier" -v`
Expected: PASS — all cases match the spike results. If `test_barrier_all_four_types_build_and_validate` fails for a variant, the validator's behavior differs from the spike — print `result.validation` and investigate (do not loosen the assertion).

- [ ] **Step 3: Commit.**
```bash
git add tests/test_product_builders.py
git commit -m "test(builders): digital + barrier edge cases (payout rename, 4 barrier types, missing/rebate)"
```

---

### Task 3 (Part c): Single + double sharkfin builder edge cases

**Files:**
- Test only: `tests/test_product_builders.py`

**Context:** `_build_single_sharkfin` requires `barrier`, defaults `participation_rate=1.0`. `_build_double_sharkfin` requires `strike`, `lower_barrier`, `upper_barrier`, defaults `participation_rate=1.0`, and does **not** itself check barrier ordering — QuantArk catches an inverted `lower>upper` at validation (verified). The inverted-rejection test is the most valuable here: it pins that a nonsensical double-sharkfin is caught, not silently built.

- [ ] **Step 1: Write the tests.** Append to `tests/test_product_builders.py`:

```python
def test_single_sharkfin_missing_barrier_reported():
    result = build_product(
        "SingleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "barrier" in result.missing


def test_single_sharkfin_participation_defaults_one():
    result = build_product(
        "SingleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
         "maturity_years": 1.0, "barrier": 120.0},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["participation_rate"] == 1.0


def test_double_sharkfin_missing_barriers_reported():
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is False
    assert "lower_barrier" in result.missing
    assert "upper_barrier" in result.missing


def test_double_sharkfin_inverted_barriers_are_rejected():
    # _build_double_sharkfin does not order-check; QuantArk must catch lower>upper
    # so a nonsensical double sharkfin is rejected, not silently built.
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "lower_barrier": 120.0, "upper_barrier": 80.0},
    )
    assert result.ok is False
    assert "lower barrier" in (result.validation or {}).get("error", "").lower()
    assert result.product_spec is None


def test_double_sharkfin_normal_barriers_build_and_validate():
    result = build_product(
        "DoubleSharkfinOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
         "lower_barrier": 80.0, "upper_barrier": 120.0},
    )
    assert result.ok is True, result.validation
    assert result.product_kwargs["lower_barrier"] == 80.0
    assert result.product_kwargs["upper_barrier"] == 120.0
```

- [ ] **Step 2: Run.**
Run: `python -m pytest tests/test_product_builders.py -k "sharkfin" -v`
Expected: PASS — matches the spike. If `test_double_sharkfin_inverted_barriers_are_rejected` fails (i.e. the inverted product builds `ok=True`), that is a REAL robustness gap — STOP and report it (a nonsensical product is being accepted); do not delete the test.

- [ ] **Step 3: Commit.**
```bash
git add tests/test_product_builders.py
git commit -m "test(builders): single + double sharkfin edge cases (missing barriers, inverted rejection, participation default)"
```

---

### Task 4: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite.**
Run: `python -m pytest -q`
Expected: PASS (no regressions). The gate extension (Task 1) is the only production change; the rest are test additions. The cross-channel equivalence net and the OTC import suites must stay green.

- [ ] **Step 2: Finish the development branch.**
Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present options, execute the choice. Given the concurrent agent on `main`, the worktree-isolated keep-or-merge flow applies.

---

## Notes / risks

- **Part (a) is low-risk and spike-proven.** All 6 families build and re-validate through the gate; the input allowlist already permits them; the change is one set extension. The only way it breaks an existing test is if some test books one of these families with incomplete terms that currently persist unvalidated — that is a latent bug being surfaced, not a regression to paper over.
- **Part (c) adds no production code.** It pins existing builder behavior captured by spikes. Every assertion has a verified expected value, so first-run failures mean a real behavior change — investigate, don't loosen.
- **Scoped deliberately.** Term contracts for non-snowball families (data-driven `missing` + a consistency test) and extending the equivalence net are NOT in this plan — they are the natural next parity steps but are independent and larger.
- **`validate_booking_product_spec` also widens automatically.** It guards on `_GATED_BOOKING_TYPES`, so `book_position` of the 6 families now runs both the `normalize` (build_product) gate and the engine-accurate `validate_booking_product_spec`. The adapter/agent engine names match `_ENGINE_BY_CLASS` for these families (DeltaOne / Asian / OneTouch / RangeAccrual), so the two gates agree.
- **One genuinely valuable assertion:** the double-sharkfin inverted-barrier rejection pins a real robustness property (a nonsensical product is caught). If a future builder change starts silently accepting it, this test fails.
```
