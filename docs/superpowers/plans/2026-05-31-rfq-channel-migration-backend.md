# RFQ Channel Migration (Backend Construction + Solve) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make snowball-family RFQs (Snowball / KO-Reset / Phoenix) quotable and bookable by routing RFQ product construction through the single `build_product` producer, so an RFQ-booked snowball is byte-identical to a direct agent booking of the same economics.

**Architecture:** Migration **step 2** of the unified-product-schema strangler-fig (spec: `docs/superpowers/specs/2026-05-30-unified-product-schema-design.md`; Foundation already merged). The snowball-family RFQ templates switch from nested QuantArk `product_kwargs` to the **flat term contract**; a thin adapter fills the solve target with its initial guess and calls `build_product` to synthesize a complete termsheet *before* solve/price/validation; quoting is **gated** on a filled contract; after a solve, executable terms are **regenerated through `build_product`** (not top-level-patched) so per-record schedule rates carry the solved value.

**Tech Stack:** Python 3, pydantic, pytest. QuantArk solver via `services/quantark.py::solve_rfq` → `rfq.service.quote_rfq` + `resolve_unknown_adapter`. The Foundation's `build_product` (`services/domains/product_builders.py`) is the single producer.

**Scope note (read first):** This plan is **backend only** — `services/rfq.py`, `services/quantark.py` (read-only), schemas, and the RFQ service tests. The **frontend** (`RfqIntakeCard.tsx` schedule-generating fields + a frequency selector + disabling the Quote button until the contract is filled) is a **separate follow-on plan** (`rfq-channel-migration-frontend`). The other two channels (try-solve, OTC import) are their own later plans. Do NOT touch `try_solve.py` or `position_adapter.py` here.

**Out of scope:** the non-snowball templates (vanilla/barrier/etc.) already carry complete `product_kwargs` and solve today — leave them on their existing path. Only the three snowball-family templates migrate.

---

## Key facts about the current code (verified)

- `RFQRequestDraft` (`schemas.py:254`): `product_type`, `product_kwargs` (today: nested QuantArk kwargs), `market` (`PricingEnvironmentSnapshot`), `engine_spec`, `unknown` (`RFQUnknownSpecIn`: `field_path`, `lower_bound`, `upper_bound`, `initial_guess`, `display_label`), `target`, `quote_mode`, `underlying`, `side`, `quantity`.
- `COMMON_TEMPLATES` (`rfq.py:40`): the `snowball` (line 139), `ko_reset_snowball` (160), `phoenix` (179) templates carry nested `product_kwargs` with a `barrier_config` that has **no observation schedule** — the shape the Foundation's `build_product` now rejects as malformed.
- `quote_rfq` (`rfq.py:486`): `_draft_with_quote_overrides` → `validate_rfq_terms` → (price: `price_product`; solve: `quantark_solve_rfq(draft)`) → `_executable_terms_for_quote` → `_quantark_build_validation`.
- `validate_rfq_terms` (`rfq.py:361`): for `solve`, requires `unknown.field_path` and `target.value > 0`; then `_quantark_build_validation` runs `validate_quantark_build(draft.product_type, draft.product_kwargs, ...)` — this is where a bare snowball fails today.
- `quantark_solve_rfq` (`quantark.py:541`): builds a real QuantArk product from `normalize_quantark_kwargs(rfq.product_kwargs)` and bisects `unknown.field_path` via `resolve_unknown_adapter`. **The product_kwargs must already build a complete product.**
- `_executable_terms_for_quote` (`rfq.py:1018`): on solve, top-level-patches the solved value into `product_kwargs` at `field_path` via `_set_quantark_unknown_path` — it does **not** regenerate derived structure (decision 7's gap).
- `book_rfq_to_position` (`rfq.py:726`): `_booking_terms` → `product_spec_from_executable_terms` → `book_position` (which, post-Foundation, routes snowball terms through `build_product`). Already HITL-gated and wraps `book_position`.

---

## Task 1: Spike — characterize how a complete snowball solves for `ko_rate` (throwaway)

**This is exploration, not TDD.** Its deliverable is a recorded finding + a decision that the later tasks depend on. Throw the script away after.

**The question:** When `quantark_solve_rfq` bisects `unknown.field_path = "barrier_config.ko_rate"` on a snowball whose `product_kwargs` is a complete `build_product` output (a `ko_observation_schedule` whose every record's `return_rate` equals the initial-guess `ko_rate`), does the model price actually vary with the bisection candidate — or is it inert because the engine prices off the per-record `return_rate`s rather than the top-level `barrier_config.ko_rate`?

**Files:**
- Create (throwaway): `backend/scratch_snowball_solve_spike.py`

- [ ] **Step 1: Write the spike script**

```python
# backend/scratch_snowball_solve_spike.py  (THROWAWAY)
from app.services.domains.product_builders import build_product
from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQTargetIn, RFQEngineSpecIn
from app.services.quantark import solve_rfq

flat = {
    "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
    "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
    "lockup_months": 3, "trade_start_date": "2026-01-05",
    "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
}
built = build_product("SnowballOption", flat, underlying="000905.SH", currency="CNY")
assert built.ok, built.validation
kwargs = dict(built.product_kwargs)

draft = RFQRequestDraft(
    underlying="000905.SH", quantity=1.0, quote_mode="solve",
    product_type="SnowballOption", product_kwargs=kwargs,
    engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
    unknown=RFQUnknownSpecIn(
        field_path="barrier_config.ko_rate",
        lower_bound=0.05, upper_bound=0.40, initial_guess=0.15,
    ),
    target=RFQTargetIn(label="price", value=0.0),
)
res = solve_rfq(draft)
print("ok:", res.ok)
print("error:", res.error)
print("data:", {k: res.data.get(k) for k in ("solved_value", "achieved_price", "residual", "status")})
```

- [ ] **Step 2: Run it and record the finding**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python scratch_snowball_solve_spike.py`

Record in the plan's "Spike findings" section below ONE of:
- **Outcome A — solve converges** (`ok: True`, a sensible `solved_value`): the engine honors top-level `barrier_config.ko_rate`. The default solve target `barrier_config.ko_rate` works; later tasks use it as-is.
- **Outcome B — solve fails "not bracketed" / price is flat across the bound**: the engine prices off per-record `return_rate`s, so top-level `ko_rate` is inert. Decision: the adapter must, for the solve target `barrier_config.ko_rate`, ALSO designate the per-record rate path so the QuantArk adapter rebinds the records — OR (simpler, preferred) keep `build_product` synthesis but set the solve `field_path` to the schedule's record-rate path that `resolve_unknown_adapter` supports. Probe `resolve_unknown_adapter` field-path support with a second script run before deciding.

- [ ] **Step 3: Probe the unknown-adapter field paths (only if Outcome B)**

Add to the script and re-run:
```python
from rfq.registry import PRODUCT_BUILDERS  # noqa
# Enumerate which field_paths resolve_unknown_adapter accepts for a snowball:
from rfq.registry import resolve_unknown_adapter
# (inspect rfq.registry source / try field_path candidates:
#  "barrier_config.ko_rate", "barrier_config.ko_observation_schedule.return_rate",
#  "accrual_config.coupon_rate", "ko_rate")
```
Record which `field_path` makes the price vary monotonically across `[lower, upper]`.

- [ ] **Step 4: Record the decision and delete the script**

Append the chosen solve target (the `unknown.field_path` value the snowball template will declare) + the field→flat-key map to the "Spike findings" section. Then:
```bash
rm /Users/fuxinyao/open-otc-trading/backend/scratch_snowball_solve_spike.py
```

### Spike findings (recorded from Task 1 run on 2026-05-31)
- **Outcome: A** — the QuantArk solver honors top-level `barrier_config.ko_rate`. Bisecting it re-prices monotonically: at bounds `[0.05, 0.40]` the model price spans `[0.501507, 24.453]`. A solve with `target.value = 5.0` converged: `solved_value ≈ 0.115736`, `achieved_price = 5.0`, residual ~0, `status: success`.
- **Snowball solve target `unknown.field_path`: `barrier_config.ko_rate`** (used as-is; no Outcome-B substitution).
- **field_path → flat-contract-key map:** `barrier_config.ko_rate` → `ko_rate`; `coupon_config.coupon_rate` → `coupon_rate`; `barrier_config.ki_barrier` → `ki_barrier_pct`.
- **KO-reset / Phoenix solve targets:** KO-reset → `barrier_config.ko_rate`; Phoenix → `coupon_config.coupon_rate` (per templates in Task 2).
- **Working solve test target: `target.value = 5.0`** (inside the `[0.50, 24.45]` price band) → bake into Task 5/7 solve tests.
- **PLAN CORRECTION (dates):** a frozen `trade_start_date` in the past relative to the market `valuation_date` (`utcnow`) makes QuantArk reject the schedule with `end_date (...) must be after start_date (...)`. The plan's literal `"2026-01-05"` fails. **Decision:** templates and tests use a **near-future business-day** `trade_start_date` computed relative to today (helper `_default_trade_start_date()` in Task 2), not a frozen literal. Tests that assert RFQ-vs-direct byte-identity must pass the **same** date to both sides.

> Tasks 2, 5, 6 below assume **Outcome A** (`field_path = "barrier_config.ko_rate"`, flat key `ko_rate`). If Task 1 records Outcome B, substitute the recorded `field_path`/flat-key everywhere those tasks use `barrier_config.ko_rate`/`ko_rate`; the structure of the tasks is unchanged.

---

## Task 2: Flatten the snowball-family templates to the term contract

Change the three snowball-family `COMMON_TEMPLATES` entries from nested QuantArk `product_kwargs` to **flat contract terms**, and add a marker so the rest of the code knows these go through `build_product`.

**Files:**
- Modify: `backend/app/services/rfq.py` (templates `rfq.py:139-199`; add `_BUILD_PRODUCT_FAMILIES`)
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_services_domains_rfq.py`:
```python
def test_snowball_template_carries_flat_contract_terms():
    from app.services.rfq import COMMON_TEMPLATES, _BUILD_PRODUCT_FAMILIES

    snowball = next(t for t in COMMON_TEMPLATES if t["key"] == "snowball")
    assert snowball["product_type"] in _BUILD_PRODUCT_FAMILIES
    pk = snowball["product_kwargs"]
    # FLAT contract inputs, not a nested QuantArk barrier_config
    assert "barrier_config" not in pk
    for key in ("ko_barrier_pct", "ki_barrier_pct", "ko_rate",
                "lockup_months", "trade_start_date", "observation_frequency"):
        assert key in pk, key
```

- [ ] **Step 2: Run it to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_snowball_template_carries_flat_contract_terms -v`
Expected: FAIL — `_BUILD_PRODUCT_FAMILIES` doesn't exist / `barrier_config` still present.

- [ ] **Step 3: Implement**

In `backend/app/services/rfq.py`, add near the top after the imports:
```python
# Families whose RFQ templates carry the FLAT term contract and are synthesized
# into a complete QuantArk termsheet by build_product (the single producer).
_BUILD_PRODUCT_FAMILIES = {
    "SnowballOption",
    "KnockOutResetSnowballOption",
    "PhoenixOption",
}
```

Replace the `snowball` template entry (`rfq.py:139-158`) with:
```python
    {
        "key": "snowball",
        "label": "Snowball",
        "product_type": "SnowballOption",
        "engine_spec": {"engine_name": "SnowballQuadEngine"},
        "unknown_fields": ["barrier_config.ko_rate"],
        # FLAT term contract (build_product input), NOT nested QuantArk kwargs.
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.15,
            "lockup_months": 3,
            "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
```

Replace the `ko_reset_snowball` template (`rfq.py:160-178`) with:
```python
    {
        "key": "ko_reset_snowball",
        "label": "KO Reset Snowball",
        "product_type": "KnockOutResetSnowballOption",
        "engine_spec": {"engine_name": "KOResetSnowballQuadEngine"},
        "unknown_fields": ["barrier_config.ko_rate"],
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.15,
            "post_ko_barrier_pct": 100.0,
            "post_ko_rate": 0.10,
            "lockup_months": 3,
            "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
```

Replace the `phoenix` template (`rfq.py:179-199`) with:
```python
    {
        "key": "phoenix",
        "label": "Phoenix",
        "product_type": "PhoenixOption",
        "engine_spec": {"engine_name": "PhoenixQuadEngine"},
        "unknown_fields": ["coupon_config.coupon_rate"],
        "product_kwargs": {
            "initial_price": 100.0,
            "strike": 100.0,
            "maturity_years": 1.0,
            "ko_barrier_pct": 103.0,
            "ki_barrier_pct": 75.0,
            "ko_rate": 0.0,
            "coupon_barrier_pct": 85.0,
            "coupon_rate": 0.01,
            "lockup_months": 3,
            "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY",
            "contract_multiplier": 1.0,
        },
    },
```

(If Task 1 recorded a different solve target, set each `unknown_fields` accordingly.)

- [ ] **Step 4: Run it to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_snowball_template_carries_flat_contract_terms -v`
Expected: PASS.

- [ ] **Step 5: Check for templates-shape regressions**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py ../tests/test_tools_rfq.py ../tests/test_cli_rfq.py -q`
Some existing snowball-template assertions may now fail because they asserted the old nested shape. For each failure: if the test pins the OLD nested template shape (a bare snowball that was already non-quotable), update it to assert the flat contract; if it pins genuine cross-cutting behavior, STOP and report it. Do NOT weaken assertions about non-snowball templates.

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/rfq.py tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(rfq): snowball-family templates carry the flat term contract"
```

---

## Task 3: Adapter — synthesize a complete termsheet from the flat contract

A thin function that maps a snowball-family draft's flat `product_kwargs` → a complete QuantArk termsheet via `build_product`, filling the solve target with its initial guess so synthesis succeeds. Returns `(product_kwargs, missing)`.

**Files:**
- Modify: `backend/app/services/rfq.py`
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the failing test**

```python
def test_executable_kwargs_synthesizes_complete_snowball_termsheet():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert missing == []
    assert kwargs["barrier_config"]["ko_observation_schedule"]["records"]  # synthesized


def test_executable_kwargs_reports_missing_contract_inputs():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={"initial_price": 100.0, "ko_barrier_pct": 103.0},  # incomplete
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert kwargs == {}
    assert "observation_frequency" in missing  # contract gap surfaced precisely


def test_executable_kwargs_passthrough_for_non_build_product_family():
    from app.schemas import RFQRequestDraft
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(product_type="EuropeanVanillaOption",
                            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0})
    kwargs, missing = _executable_product_kwargs(draft, quote_mode="solve")
    assert missing == []
    assert kwargs == draft.product_kwargs  # unchanged for non-snowball families
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_executable_kwargs_synthesizes_complete_snowball_termsheet -v`
Expected: FAIL — `_executable_product_kwargs` not defined.

- [ ] **Step 3: Implement**

In `backend/app/services/rfq.py`, add:
```python
# Maps a snowball-family solve target (a path into the BUILT termsheet) to the
# FLAT contract key the placeholder initial-guess must be written to so
# build_product can synthesize a complete, priceable termsheet.
_SOLVE_TARGET_FLAT_KEY = {
    "barrier_config.ko_rate": "ko_rate",
    "coupon_config.coupon_rate": "coupon_rate",
    "barrier_config.ki_barrier": "ki_barrier_pct",
}


def _executable_product_kwargs(
    draft: RFQRequestDraft, *, quote_mode: str
) -> tuple[dict[str, Any], list[str]]:
    """For build_product families, synthesize a complete QuantArk termsheet from
    the draft's FLAT contract terms. In solve mode the designated solve target is
    filled with its initial guess (a placeholder) so synthesis produces a complete
    termsheet the QuantArk solver can start from (decision 6). Returns
    (product_kwargs, missing); missing is non-empty iff the contract is unfilled."""
    if draft.product_type not in _BUILD_PRODUCT_FAMILIES:
        return dict(draft.product_kwargs), []
    from .domains.product_builders import build_product

    contract = dict(draft.product_kwargs)
    solve_target = draft.unknown.field_path if quote_mode == "solve" else None
    if solve_target:
        flat_key = _SOLVE_TARGET_FLAT_KEY.get(solve_target)
        if flat_key and contract.get(flat_key) is None and draft.unknown.initial_guess is not None:
            contract[flat_key] = draft.unknown.initial_guess
    built = build_product(
        draft.product_type,
        contract,
        underlying=draft.underlying,
        currency=draft.market.currency,
        solve_target=solve_target,
    )
    if built.missing:
        return {}, built.missing
    return dict(built.product_kwargs), []
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py -k executable_kwargs -v`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/rfq.py tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(rfq): adapter synthesizes complete termsheet from flat contract via build_product"
```

---

## Task 4: Gate quoting on a filled contract

`validate_rfq_terms` must reject a snowball-family quote whose contract is unfilled, with the precise `missing` list from `build_product` — instead of letting the bare shape reach the opaque quad validator. A bare draft stays a legitimate *draft*; only quoting is gated.

**Files:**
- Modify: `backend/app/services/rfq.py` (`validate_rfq_terms`, `rfq.py:361`)
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the failing test**

```python
def test_validate_rfq_terms_gates_unfilled_snowball_contract():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn, RFQTargetIn
    from app.services.rfq import validate_rfq_terms

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={"initial_price": 100.0, "ko_barrier_pct": 103.0},  # unfilled
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),
    )
    result = validate_rfq_terms(draft, "solve")
    assert result["valid"] is False
    # precise contract gap, not the opaque "KO observation … required"
    assert any("observation_frequency" in m for m in result["missing_fields"])
    assert not any("KO observation" in e for e in result["errors"])


def test_validate_rfq_terms_accepts_filled_snowball_contract():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn, RFQTargetIn
    from app.services.rfq import validate_rfq_terms

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=5.0),
    )
    result = validate_rfq_terms(draft, "solve")
    assert result["valid"] is True, result
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_validate_rfq_terms_gates_unfilled_snowball_contract -v`
Expected: FAIL — today the unfilled snowball reaches `_quantark_build_validation` and the error is the opaque `KO observation … required` (or it validates differently), so the precise `observation_frequency` assertion fails.

- [ ] **Step 3: Implement**

In `validate_rfq_terms` (`rfq.py:361`), replace the final build-validation block (currently):
```python
    if not errors and not missing:
        build_validation = _quantark_build_validation(draft)
        if not build_validation["valid"]:
            errors.extend(build_validation["errors"])
    return {"valid": not errors and not missing, "errors": errors, "missing_fields": missing}
```
with:
```python
    if not errors and not missing:
        if draft.product_type in _BUILD_PRODUCT_FAMILIES:
            # Build through the single producer: surface precise contract gaps,
            # never the opaque quad "KO observation … required".
            _kwargs, contract_missing = _executable_product_kwargs(
                draft, quote_mode=quote_mode
            )
            if contract_missing:
                missing.extend(contract_missing)
            else:
                build_validation = _quantark_build_validation(
                    draft.model_copy(update={"product_kwargs": _kwargs})
                )
                if not build_validation["valid"]:
                    errors.extend(build_validation["errors"])
        else:
            build_validation = _quantark_build_validation(draft)
            if not build_validation["valid"]:
                errors.extend(build_validation["errors"])
    return {"valid": not errors and not missing, "errors": errors, "missing_fields": missing}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py -k "validate_rfq_terms" -v`
Expected: PASS (both new + existing validate tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/rfq.py tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(rfq): gate snowball quoting on a filled contract with precise missing fields"
```

---

## Task 5: Route `quote_rfq` construction through the adapter (price + solve)

Before pricing/solving a snowball-family RFQ, replace the draft's flat `product_kwargs` with the complete synthesized termsheet, so `quantark_solve_rfq` / `price_product` operate on a buildable product.

**Files:**
- Modify: `backend/app/services/rfq.py` (`quote_rfq`, `rfq.py:486`)
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the failing test**

```python
def test_quote_rfq_snowball_solve_produces_pending_approval(session):
    from app.schemas import (RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn,
                             RFQTargetIn, RFQQuoteRequest)
    from app.services.rfq import create_rfq_draft, quote_rfq
    from app.models import RfqStatus

    draft = RFQRequestDraft(
        underlying="000905.SH", quantity=1_000_000.0, quote_mode="solve",
        product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=0.0),
    )
    rfq = create_rfq_draft(session, draft, channel="desk")
    quoted = quote_rfq(session, rfq.id, RFQQuoteRequest(quote_mode="solve"))
    # The snowball now reaches the solver instead of failing the build gate.
    assert quoted.status in {RfqStatus.PENDING_APPROVAL.value, RfqStatus.PRICING_FAILED.value}
    # If the solver could not bracket, the error must be a bracketing message,
    # NOT the opaque build error.
    if quoted.status == RfqStatus.PRICING_FAILED.value:
        assert "KO observation" not in (quoted.quote_payload.get("quantark_error") or "")
```

(Set `target.value` from the Task 1 spike's observed price band so the solve brackets and yields PENDING_APPROVAL; if the spike recorded a workable target, assert `== PENDING_APPROVAL`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_quote_rfq_snowball_solve_produces_pending_approval -v`
Expected: FAIL — today the bare flat `product_kwargs` reaches `quantark_solve_rfq` and the solver can't build the product.

- [ ] **Step 3: Implement**

In `quote_rfq` (`rfq.py:486`), after the `validate_rfq_terms` success branch and BEFORE the `if quote_mode == "price":` block, materialize the executable kwargs for build_product families:
```python
    # For build_product families, the draft carries the FLAT contract; synthesize
    # the complete termsheet that pricing/solving operates on. Non-snowball
    # families pass through unchanged.
    if draft.product_type in _BUILD_PRODUCT_FAMILIES:
        exec_kwargs, exec_missing = _executable_product_kwargs(draft, quote_mode=quote_mode)
        if exec_missing:  # defense-in-depth; validate_rfq_terms already gated this
            raise ValueError(
                f"Incomplete {draft.product_type} contract; missing: "
                + ", ".join(exec_missing)
            )
        draft = draft.model_copy(update={"product_kwargs": exec_kwargs})
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py -k "quote_rfq_snowball" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/rfq.py tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(rfq): build snowball termsheet via build_product before price/solve"
```

---

## Task 6: Regenerate executable terms through `build_product` after solve (decision 7)

After a solve, the resolved value must be bound back into the FLAT contract and the termsheet **regenerated** via `build_product`, so every synthesized schedule record's rate carries the solved value — not the initial-guess placeholder. This makes an RFQ-solved snowball byte-identical to a direct booking of the same final economics.

**Files:**
- Modify: `backend/app/services/rfq.py` (`_executable_terms_for_quote`, `rfq.py:1018`)
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the failing test**

```python
def test_executable_terms_regenerates_schedule_rates_from_solved_value():
    from app.schemas import RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn
    from app.services.rfq import _executable_terms_for_quote, _executable_product_kwargs

    draft = RFQRequestDraft(
        underlying="000905.SH", quote_mode="solve", product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs={
            "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
            "lockup_months": 3, "trade_start_date": "2026-01-05",
            "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
        },
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
    )
    quote_payload = {"solved_value": 0.2233, "field_path": "barrier_config.ko_rate"}
    terms = _executable_terms_for_quote(draft, "solve", quote_payload)

    records = terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"]["records"]
    # every record's return_rate reflects the SOLVED coupon, not the 0.15 placeholder
    assert all(abs(r["return_rate"] - 0.2233) < 1e-9 for r in records)
    # and a direct build with the same solved coupon yields identical product_kwargs
    direct, _ = _executable_product_kwargs(
        draft.model_copy(update={"product_kwargs": {**draft.product_kwargs, "ko_rate": 0.2233},
                                 "quote_mode": "price"}),
        quote_mode="price",
    )
    assert terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"] == \
        direct["barrier_config"]["ko_observation_schedule"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_executable_terms_regenerates_schedule_rates_from_solved_value -v`
Expected: FAIL — today `_executable_terms_for_quote` top-level-patches `barrier_config.ko_rate` only; the per-record `return_rate`s stay at the 0.15 placeholder.

- [ ] **Step 3: Implement**

In `_executable_terms_for_quote` (`rfq.py:1018`), after computing `field_path` and `solved`, branch for build_product families to regenerate instead of top-level-patching. Replace the tail (from `field_path = str(...)` through `return terms`) with:
```python
    field_path = str(quote_payload.get("field_path") or draft.unknown.field_path)
    if not field_path or field_path == "fixed_terms":
        return terms
    if draft.product_type in _BUILD_PRODUCT_FAMILIES:
        # Bind the solved value back into the FLAT contract and REGENERATE the
        # termsheet so derived structure (per-record schedule rates) reflects the
        # solved value — not the initial-guess placeholder (decision 7).
        flat_key = _SOLVE_TARGET_FLAT_KEY.get(field_path)
        flat_contract = dict(draft.product_kwargs)
        if flat_key is not None:
            flat_contract[flat_key] = solved
        regenerated, missing = _executable_product_kwargs(
            draft.model_copy(update={"product_kwargs": flat_contract, "quote_mode": "price"}),
            quote_mode="price",
        )
        if not missing:
            terms["product_kwargs"] = regenerated
        return terms
    _set_quantark_unknown_path(terms, field_path, solved)
    return terms
```

Note: `terms` was built from `model_to_dict(draft)`, whose `product_kwargs` is the FLAT contract; for build_product families we overwrite it with the regenerated complete termsheet, which is what `_booking_terms` → `book_position` consumes.

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py -k "regenerates_schedule_rates" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/rfq.py tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "fix(rfq): regenerate solved snowball terms via build_product (decision 7)"
```

---

## Task 7: End-to-end — RFQ snowball quotes, books, and equals a direct booking

The integration test that proves the migration: a snowball RFQ drives quote → approve → release → client-accept → book, and the booked product's canonical kwargs equal a direct `build_product` of the same final economics.

**Files:**
- Test: `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Write the test**

```python
def test_rfq_snowball_books_identical_to_direct_build(session):
    from app.schemas import (RFQRequestDraft, RFQUnknownSpecIn, RFQEngineSpecIn,
                             RFQTargetIn, RFQQuoteRequest, RFQApprovalDecision,
                             RFQReleaseRequest, RFQClientAcceptRequest, RFQBookRequest)
    from app.services.rfq import (create_rfq_draft, quote_rfq, approve_rfq, release_rfq,
                                  mark_client_accepted, book_rfq_to_position)
    from app.services.domains.product_builders import build_product
    from app.models import Portfolio, PortfolioKind, RfqStatus

    portfolio = Portfolio(name="RFQ Book", base_currency="CNY",
                          kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio); session.flush()

    flat = {
        "initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
        "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15,
        "lockup_months": 3, "trade_start_date": "2026-01-05",
        "observation_frequency": "MONTHLY", "contract_multiplier": 1.0,
    }
    draft = RFQRequestDraft(
        underlying="000905.SH", quantity=1_000_000.0, quote_mode="solve",
        product_type="SnowballOption",
        engine_spec=RFQEngineSpecIn(engine_name="SnowballQuadEngine"),
        product_kwargs=dict(flat),
        unknown=RFQUnknownSpecIn(field_path="barrier_config.ko_rate",
                                 lower_bound=0.05, upper_bound=0.40, initial_guess=0.15),
        target=RFQTargetIn(label="price", value=0.0),  # set from spike's price band
    )
    rfq = create_rfq_draft(session, draft, channel="desk")
    rfq = quote_rfq(session, rfq.id, RFQQuoteRequest(quote_mode="solve"))
    assert rfq.status == RfqStatus.PENDING_APPROVAL.value, rfq.quote_payload
    solved = rfq.quote_payload["solved_value"]

    approve_rfq(session, rfq.id, RFQApprovalDecision(approver="trader"))
    release_rfq(session, rfq.id, RFQReleaseRequest(actor="trader"))
    mark_client_accepted(session, rfq.id, RFQClientAcceptRequest(actor="client"))
    position = book_rfq_to_position(session, rfq.id, RFQBookRequest(portfolio_id=portfolio.id))
    session.flush()

    assert position.product_type == "SnowballOption"
    booked_sched = position.product_kwargs["barrier_config"]["ko_observation_schedule"]
    direct = build_product("SnowballOption", {**flat, "ko_rate": solved},
                           underlying="000905.SH", currency="CNY")
    assert direct.ok
    assert booked_sched == direct.product_kwargs["barrier_config"]["ko_observation_schedule"]
```

- [ ] **Step 2: Run it**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py::test_rfq_snowball_books_identical_to_direct_build -v`
Expected: PASS. If the solve does not bracket (target.value not in the price band), set `target.value` to a price inside the band the Task 1 spike recorded. If `book_rfq_to_position` rejects on status, confirm the quote reached `PENDING_APPROVAL` first.

- [ ] **Step 3: Full RFQ + Foundation regression**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_services_domains_rfq.py ../tests/test_tools_rfq.py ../tests/test_cli_rfq.py ../tests/test_product_booking.py ../tests/test_product_builders.py -q`
Expected: all PASS. Investigate any failure; do not weaken tests to pass.

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add tests/test_services_domains_rfq.py
git -C /Users/fuxinyao/open-otc-trading commit -m "test(rfq): end-to-end snowball quote->book equals direct build"
```

---

## Self-Review

**Spec coverage (migration step 2 / RFQ):**
- Templates carry the term contract → **Task 2** ✓
- Route `quote_rfq` construction through `build_product` via a thin adapter → **Tasks 3, 5** ✓
- Adapter fills the solve target with its initial guess → **Task 3** (`_SOLVE_TARGET_FLAT_KEY` + initial_guess) ✓
- Gate quoting on a filled contract (precise missing, not opaque) → **Task 4** ✓
- Resolution regenerates via `build_product`, not top-level patch (decision 7) → **Task 6** ✓
- RFQ→book via `book_position`, byte-identical to direct booking → **Task 7** (`book_rfq_to_position` already wraps `book_position`; cross-channel equivalence asserted) ✓
- Solve target is a free variable, not missing → relies on Foundation's `solve_target` exemption, exercised in **Tasks 3-5** ✓

**Deferred (separate plans, explicitly):** frontend `RfqIntakeCard` schedule-generating fields + frequency selector + Quote-button gating; try-solve and OTC-import channels. HITL gating already exists (`book_rfq_to_position`/`book_position` in `INTERRUPT_TOOL_NAMES`).

**The one genuine risk** is isolated in **Task 1 (spike)**: whether the QuantArk solver re-prices when bisecting `barrier_config.ko_rate` on a complete snowball, or prices off per-record rates. Tasks 2/5/6 are written for Outcome A and tell the implementer exactly what to substitute for Outcome B (the recorded `field_path`/flat-key). This is a real investigative task with a recorded decision, not a placeholder.

**Placeholder scan:** the only deliberately-unfilled values are `target.value` for solve tests (depends on the spike's observed price band — the steps say how to set it) and the Spike findings section (filled during Task 1). No "TBD"/"add error handling"/"similar to" placeholders.

**Type/name consistency:** `_BUILD_PRODUCT_FAMILIES` (Task 2) used in Tasks 3-6; `_executable_product_kwargs` (Task 3) used in Tasks 4-6; `_SOLVE_TARGET_FLAT_KEY` (Task 3) used in Task 6; all return `(dict, list[str])` consistently.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-31-rfq-channel-migration-backend.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks. NOTE: do **Task 1 (spike) first and report its findings** before dispatching Task 2, because the recorded solve target may change the exact field paths in Tasks 2/5/6.

**2. Inline Execution** — execute tasks in this session using executing-plans, with the Task 1 spike as a hard checkpoint.

**Which approach?**
