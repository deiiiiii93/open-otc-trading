# Non-Snowball Cross-Channel Equivalence Net Implementation Plan (parity gap d)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the cross-channel equivalence net (`tests/test_cross_channel_equivalence.py`) from snowball-only + weak family-only import checks to real **agent ↔ try-solve ↔ import** economic convergence for the non-snowball families — so a future builder/adapter divergence on vanilla/digital/sharkfin/asian/range/touch fails a test, exactly as it already does for snowball.

**Background:** The equivalence net pins that every channel reaches the same `build_product` output. Today it covers snowball fully (RFQ≡agent byte-identical; import/try-solve structural) but for non-snowball it only has **import↔agent family-only checks** (assert the family validates) and **zero try-solve cross-channel coverage**. The biggest gap is try-solve: it routes 8 non-snowball option families through `build_product` (`_build_row_termsheet`) and nothing pins that its output matches the agent's. RFQ is correctly out of scope here — `_BUILD_PRODUCT_FAMILIES` is snowball-only, so RFQ does not build_product-route non-snowball families (their templates pass through unchanged).

**Architecture:** Pure **test additions** to one file — no production change. Two new comparisons, both spike-verified on `main` @ `cd6b195`:
1. **agent ↔ try-solve.** A helper builds the try-solve row termsheet via `_build_row_termsheet` (solving the price-like `premium_rate`, which leaves the product kwargs fully specified). For **vanilla, single_sf, double_sf, asian** the canonical product_kwargs are **byte-identical** to the agent's `build_product` output (the non-snowball parallel of snowball's RFQ≡agent byte-identity). For **digital, one_touch, double_one_touch** they are identical **except the notional-scaled payoff** (`payout`/`rebate`) — try-solve scales by notional, an input-adapter convention, so the assertion pins "everything but the payoff matches, and both carry the payoff". **range_accrual** (no `premium_rate` field) is structural on its `range_config` + `num_observations`.
2. **agent ↔ import.** Strengthen the existing import round-trip test from family-only to asserting the **shared economic fields** (strike / barrier(s) / option_type / payout) agree with an agent build of the same economics. Import stays structural (it carries explicit dates, `contract_multiplier`, and observation schedules the uniform synthesizer doesn't).

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`; `tests/` is importable, so `from test_position_import_pricing import …` works). No frontend changes.

**Prerequisite:** None — everything is on `main` (`cd6b195`). **Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns `main`. Execute in an isolated worktree (superpowers:using-git-worktrees) — no git remote, so branch from local `main`: `git worktree add /Users/fuxinyao/ots-wt-equiv -b feat/non-snowball-equivalence-net cd6b195`, and run pytest with the main venv: `cd <worktree> && /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest …`. NOTE: `python -c` spikes import `app` from the MAIN checkout, not the worktree — use pytest, or set `PYTHONPATH=<worktree>/backend:<worktree>/tests`.

**Out of scope:** RFQ cross-channel for non-snowball (RFQ doesn't build_product-route them — intentional); `forward`→`Futures` try-solve comparison (DeltaOne, already covered by the parity-(a) gate/pricing tests); any production change (this plan only pins existing behavior — if a comparison reveals a real divergence, STOP and surface it).

---

## Spike results baked into this plan (verified on `main` @ cd6b195)

`_build_row_termsheet` output vs `build_product` (agent), solving `premium_rate` unless noted:

| try-solve key | class | agent vs try-solve |
|---|---|---|
| vanilla | EuropeanVanillaOption | **byte-identical** |
| single_sf | SingleSharkfinOption | **byte-identical** |
| double_sf | DoubleSharkfinOption | **byte-identical** |
| asian | AsianOption | **byte-identical** |
| digital | CashOrNothingDigitalOption | identical except `payout` (try-solve notional-scales it) |
| one_touch | OneTouchOption | identical except `rebate` (notional-scaled) |
| double_one_touch | DoubleOneTouchOption | identical except `rebate` (notional-scaled) |
| range_accrual | RangeAccrualOption | structural: same `range_config` + `num_observations` (solve `range_accrual_rate`; no `premium_rate` field) |

Import (`map_trade_row` → `build_product(prebuilt=True)`) shared economic fields vs agent: vanilla `{strike, option_type}`; digital `+{payout}`; barrier `+{barrier}`; single_sf `+{barrier, participation_rate}`; double_sf `+{lower_barrier, upper_barrier, participation_rate}`. (Import also carries `exercise_date`/`settlement_date`/`contract_multiplier=10000`/observation schedules → structural, not byte-identical.)

---

## File Structure

- `tests/test_cross_channel_equivalence.py` — add the agent↔try-solve helper + tests (Task 1); strengthen the import↔agent tests to economic (Task 2). No other file changes.

---

### Task 1: agent ↔ try-solve equivalence for the 8 non-snowball option families

**Files:**
- Test: `tests/test_cross_channel_equivalence.py`

**Context:** try-solve's `_build_row_termsheet(row, product, market, maturity, quote_field)` returns `(product_kwargs, missing)` — the same `build_product`-produced canonical kwargs the agent yields. Solving `premium_rate` (a price-like unknown, not a product kwarg) leaves the product kwargs fully specified, giving a clean comparison. The existing `test_trysolve_snowball_is_structurally_equivalent_to_agent` shows the row/registry setup to mirror; `_ko_records` already exists in this file.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_cross_channel_equivalence.py` (it already imports `pytest` and `build_product`):

```python
def _trysolve_kwargs(product_key, fields, *, quote_field="premium_rate"):
    """Canonical product_kwargs the try-solve channel feeds build_product for a
    fully-specified row. Solving the price-like `premium_rate` leaves the product
    kwargs complete (the solve target is not a product field)."""
    from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key
    from app.schemas import TrySolveRowIn, TrySolveMarketIn, TrySolveQuoteRequestIn

    row = TrySolveRowIn(
        row_id="x", product_key=product_key, fields=fields,
        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
        quote_request=TrySolveQuoteRequestIn(
            quote_field_key=quote_field, initial_guess=0.05,
            target_label="price", target_value=5.0,
        ),
    )
    product = registry_by_key()[product_key]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(
        row, product, market, _maturity_years(row), product.quote_fields[quote_field],
    )
    assert missing == [], (product_key, missing)
    return kwargs


_TS_ROW_BASE = {"underlying": "000905.SH", "notional": 1_000_000, "tenor_months": 12}

# try-solve canonical kwargs are BYTE-IDENTICAL to the agent's for these families.
_TS_BYTE_IDENTICAL = [
    ("vanilla", {"strike": 100.0},
     "EuropeanVanillaOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0}),
    ("single_sf", {"strike": 100.0, "barrier": 120.0},
     "SingleSharkfinOption",
     {"initial_price": 100.0, "strike": 100.0, "barrier": 120.0, "option_type": "CALL",
      "maturity_years": 1.0}),
    ("double_sf", {"strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0},
     "DoubleSharkfinOption",
     {"initial_price": 100.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
      "option_type": "CALL", "maturity_years": 1.0}),
    ("asian", {"strike": 100.0},
     "AsianOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
      "averaging_frequency": "MONTHLY"}),
]


@pytest.mark.parametrize("key, extra, agent_class, agent_terms", _TS_BYTE_IDENTICAL)
def test_trysolve_is_byte_identical_to_agent(key, extra, agent_class, agent_terms):
    ts = _trysolve_kwargs(key, {**_TS_ROW_BASE, **extra})
    agent = build_product(agent_class, dict(agent_terms))
    assert agent.ok, agent.validation
    assert ts == agent.product_kwargs


# Identical EXCEPT the notional-scaled payoff (try-solve scales payout/rebate by
# notional — an input-adapter convention, not a builder divergence).
_TS_STRUCTURAL_PAYOFF = [
    ("digital", {"strike": 100.0, "payout": 10.0}, "payout",
     "CashOrNothingDigitalOption",
     {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
      "maturity_years": 1.0, "cash_payoff": 10.0}),
    ("one_touch", {"barrier": 120.0, "rebate": 10.0}, "rebate",
     "OneTouchOption",
     {"initial_price": 100.0, "barrier": 120.0, "cash_payoff": 10.0, "maturity_years": 1.0}),
    ("double_one_touch", {"upper_barrier": 120.0, "lower_barrier": 80.0, "rebate": 10.0}, "rebate",
     "DoubleOneTouchOption",
     {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
      "cash_payoff": 10.0, "maturity_years": 1.0}),
]


@pytest.mark.parametrize("key, extra, payoff, agent_class, agent_terms", _TS_STRUCTURAL_PAYOFF)
def test_trysolve_matches_agent_except_scaled_payoff(key, extra, payoff, agent_class, agent_terms):
    ts = _trysolve_kwargs(key, {**_TS_ROW_BASE, **extra})
    agent = build_product(agent_class, dict(agent_terms))
    assert agent.ok, agent.validation
    # both carry the payoff; only its (notional-scaled) value differs
    assert payoff in ts and payoff in agent.product_kwargs
    ts_rest = {k: v for k, v in ts.items() if k != payoff}
    agent_rest = {k: v for k, v in agent.product_kwargs.items() if k != payoff}
    assert ts_rest == agent_rest


def test_trysolve_range_accrual_economics_match_agent():
    # range_accrual has no premium_rate field; solve the accrual rate (the row also
    # supplies it, so the built termsheet is fully specified). Structural: the
    # range_config economics + observation count agree with the agent.
    ts = _trysolve_kwargs(
        "range_accrual",
        {**_TS_ROW_BASE, "lower_barrier": 90.0, "upper_barrier": 110.0, "accrual_rate": 0.1},
        quote_field="range_accrual_rate",
    )
    agent = build_product(
        "RangeAccrualOption",
        {"initial_price": 100.0, "maturity_years": 1.0, "lower_barrier_pct": 90.0,
         "upper_barrier_pct": 110.0, "accrual_rate": 0.1},
    )
    assert agent.ok, agent.validation
    assert ts["range_config"] == agent.product_kwargs["range_config"]
    assert ts["num_observations"] == agent.product_kwargs["num_observations"]
```

- [ ] **Step 2: Run.**
Run: `python -m pytest tests/test_cross_channel_equivalence.py -k "trysolve" -v`
Expected: PASS — the 4 byte-identical families equal exactly; the 3 payoff families equal except `payout`/`rebate`; range_accrual structural matches. These are characterization tests grounded in the spike, so they pass first run. If `test_trysolve_is_byte_identical_to_agent` FAILS for a family, the try-solve and agent channels have diverged for it — STOP and surface the diff (do not weaken to structural without confirming it is an intentional input-adapter difference like the payoff scaling).

- [ ] **Step 3: Commit.**
```bash
git add tests/test_cross_channel_equivalence.py
git commit -m "test(equivalence): pin agent<->try-solve for the 8 non-snowball option families"
```

---

### Task 2: Strengthen agent ↔ import from family-only to economic

**Files:**
- Test: `tests/test_cross_channel_equivalence.py`

**Context:** `test_import_vanilla_validates_and_matches_agent_family` and `test_import_family_round_trips_through_prebuilt_gate` currently assert only the family/engine. Strengthen them so they also assert the **shared economic fields** match an agent build of the same economics. Import stays structural (it adds `exercise_date`/`settlement_date`/`contract_multiplier=10000`/observation schedules), so compare only the shared keys. The import rows come from `test_position_import_pricing` helpers (`vanilla_row`, `shark_row`, `double_shark_row`), already imported in this file's tests.

- [ ] **Step 1: Add a shared-fields helper + replace the parametrized import test.** In `tests/test_cross_channel_equivalence.py`, add the helper and REPLACE `test_import_family_round_trips_through_prebuilt_gate` (keep its row-construction logic) with a version that also asserts economics. Leave `test_import_vanilla_validates_and_matches_agent_family`, `test_import_phoenix_validates_through_prebuilt_gate`, and the snowball/try-solve tests unchanged.

```python
def _shared(kwargs, keys):
    return {k: kwargs[k] for k in keys if k in kwargs}


@pytest.mark.parametrize(
    "structure, quantark_class, family, agent_terms, shared_keys",
    [
        ("欧式二元", "CashOrNothingDigitalOption", "option",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL",
          "maturity_years": 1.0, "cash_payoff": 5.0},
         ("strike", "option_type", "payout")),
        ("基础障碍敲入期权", "BarrierOption", "barrier",
         {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
          "barrier": 80.0, "barrier_type": "DOWN_IN"},
         ("strike", "option_type", "barrier")),
        ("单鲨", "SingleSharkfinOption", "sharkfin",
         {"initial_price": 100.0, "strike": 100.0, "barrier": 120.0, "option_type": "CALL",
          "maturity_years": 1.0},
         ("strike", "option_type", "barrier", "participation_rate")),
        ("双鲨", "DoubleSharkfinOption", "sharkfin",
         {"initial_price": 100.0, "strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
          "option_type": "CALL", "maturity_years": 1.0},
         ("strike", "option_type", "lower_barrier", "upper_barrier", "participation_rate")),
    ],
)
def test_import_economics_match_agent(structure, quantark_class, family, agent_terms, shared_keys):
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import (  # type: ignore
        vanilla_row, shark_row, double_shark_row,
    )
    if structure == "单鲨":
        row = shark_row()
    elif structure == "双鲨":
        row = double_shark_row()
    else:
        row = vanilla_row(f"T-{quantark_class}")
        row["结构类型"] = structure
        if structure == "欧式二元":
            row["收益率"] = "5%"
        if structure == "基础障碍敲入期权":
            row["敲入价格"] = 80.0
            row["未敲入收益率"] = "1%"
    mapping = map_trade_row(row)
    imported = build_product(quantark_class, dict(mapping.product_kwargs), prebuilt=True)
    assert imported.ok, imported.validation
    assert imported.product_spec.quantark_class == quantark_class
    assert imported.product_spec.product_family == family

    agent = build_product(quantark_class, dict(agent_terms))
    assert agent.ok, agent.validation
    # structural: import carries richer dates/multiplier/schedules; the shared
    # economic fields must agree across the two channels.
    assert _shared(imported.product_kwargs, shared_keys) == _shared(agent.product_kwargs, shared_keys)
```

- [ ] **Step 2: Strengthen the vanilla import test.** Replace the body of `test_import_vanilla_validates_and_matches_agent_family` so it also asserts the shared economics (`strike`, `option_type`) match — not just the family/engine:

```python
def test_import_vanilla_validates_and_matches_agent_family():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import vanilla_row  # type: ignore

    mapping = map_trade_row(vanilla_row("T-GV"))
    imported = build_product("EuropeanVanillaOption", dict(mapping.product_kwargs), prebuilt=True)
    assert imported.ok, imported.validation
    assert imported.product_spec.product_family == "option"
    agent = build_product(
        "EuropeanVanillaOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert agent.ok
    assert agent.engine_name == imported.engine_name == "BlackScholesEngine"
    assert agent.product_spec.product_family == imported.product_spec.product_family
    # shared economics agree (import additionally carries dates + contract_multiplier)
    for key in ("strike", "option_type"):
        assert imported.product_kwargs[key] == agent.product_kwargs[key]
```

- [ ] **Step 3: Run.**
Run: `python -m pytest tests/test_cross_channel_equivalence.py -k "import" -v`
Expected: PASS — every import family's shared economic fields equal the agent's. If a `_shared(...)` assertion FAILS, the import adapter and the agent disagree on an economic value the desk would expect to match (e.g., a strike mismatch) — STOP and surface it; do not drop the key from `shared_keys` to make it pass.

- [ ] **Step 4: Commit.**
```bash
git add tests/test_cross_channel_equivalence.py
git commit -m "test(equivalence): strengthen agent<->import non-snowball checks from family-only to shared economics"
```

---

### Task 3: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite.**
Run: `python -m pytest -q`
Expected: PASS (no regressions). This plan is test-only; the snowball equivalence tests and all other suites must stay green.

- [ ] **Step 2: Finish the development branch.**
Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present options (no git remote → merge-locally / keep / discard; PR unavailable), execute the choice. Given the concurrent agent on `main`, check `main` is still FF-able before merging.

---

## Notes / risks

- **Test-only, zero production risk.** Every assertion is grounded in a spike on `cd6b195`. A first-run failure means a genuine cross-channel divergence — investigate, don't loosen.
- **The byte-identity for 4 families is the high-value pin.** vanilla/single_sf/double_sf/asian produce byte-identical canonical kwargs via agent and try-solve — the non-snowball parallel of snowball's RFQ≡agent. If a future builder change makes try-solve and agent diverge for these, the test fails.
- **The payoff scaling is intentional, documented, not a bug.** try-solve notional-scales `payout`/`rebate` (input-adapter behavior). The structural-except-payoff test pins that the scaling is the ONLY difference — so a divergence in any other field still fails.
- **RFQ intentionally excluded.** `_BUILD_PRODUCT_FAMILIES` is snowball-only; non-snowball RFQ templates don't build_product-route, so there's no RFQ canonical output to compare. Re-including RFQ for non-snowball is a separate decision (would require flattening those templates through build_product).
- **`forward`→`Futures` left out.** DeltaOne is already covered by the parity-(a) gate/pricing/`_otc_` tests; adding a try-solve Futures comparison is a small optional follow-up, not part of this net.
