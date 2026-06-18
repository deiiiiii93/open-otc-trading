# Cleanup Channel — Unified-Product-Schema Capstone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the strangler-fig migration by **locking in** the unification with a cross-channel equivalence regression net (a golden product per family that every channel must round-trip to the same canonical `build_product` output), plus an honest minimal tidy.

**Architecture:** All four channels now reach the single producer `build_product`: RFQ (`rfq._executable_product_kwargs`), try-solve (`try_solve._build_row_termsheet`), OTC import (`position_adapter` → `booking.normalize` → `build_product(prebuilt=True)`), and direct/agent (`build_product` directly). The migration's *whole point* — "divergence is the bug" — is only durably protected by a test that asserts the channels converge. This plan adds that net. It is deliberately **mostly additive (tests)**: a code survey found the "dead builder code" already retired per-channel and family-derivation already consolidated onto the canonical product (`create_or_get_product` trusts `spec.product_family`; the remaining `product_family_for_quantark_class` calls are legitimate spec *factories*). So the tidy task is small and honest.

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`). QuantArk derivatives library (vendored, path-injected via settings). No frontend changes.

---

## ⚠ Prerequisite — both queued channel branches must be merged to `main` first

This is the **final** strangler-fig step; it builds on the two channels not yet merged:
- `feat/double-one-touch-builder` — retires `try_solve._product_kwargs_for_row` (the cross-channel test must not reference it).
- `feat/otc-import-migration` — adds `build_product(..., prebuilt=True)`, which the OTC-import arm of the equivalence net **requires**.

Do NOT start this plan until both are merged to `main` and the full suite is green (modulo the known pre-existing `test_quant_agent_tools_count_unchanged` 63→64 drift, which should be fixed at merge time). Verify the prerequisites are present before Task 1:
- `grep -n "prebuilt" backend/app/services/domains/product_builders.py` → the `prebuilt` parameter exists.
- `grep -c "_product_kwargs_for_row" backend/app/services/try_solve.py` → `0`.

**Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns `HEAD`/branches. Execute in an isolated worktree (superpowers:using-git-worktrees) — this repo has no git remote, so branch from local `main` into an external path, e.g. `git worktree add /Users/fuxinyao/ots-wt-cleanup -b feat/cleanup-channel main`, and run pytest with the main venv: `cd <worktree> && /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest …`.

**Out of scope:** No frontend changes. No storage-refactor work (`raw_terms` normalization is a separate in-flight effort). No new builder behavior — this plan only *pins* existing behavior and removes genuinely-dead code (expected: little to none).

---

## Decisions baked in (stakeholder Q&A, 2026-06-01)

- **Equivalence net + honest tidy** (not net-only, not tidy-only).
- **Rigor: uniform golden + structural import.** RFQ vs agent → assert **byte-identical** canonical `product_kwargs` (both call `build_product` on the same flat contract, so identity is structurally guaranteed and the assertion is robust). OTC import → assert **structural** equivalence (same `quantark_class`/family, same KO-schedule record count, same barrier/rate economics where they coincide) — *not* byte-identical, because import carries a complete heterogeneous termsheet that the uniform synthesizer cannot reproduce key-for-key. try-solve → structural (its row adapter applies moneyness scaling + field defaults, so byte-identity is fragile; structural is the honest assertion).

---

## File Structure

- `tests/test_cross_channel_equivalence.py` — **new**. The golden-product regression net. One golden fixture per family; asserts the channels that emit that family converge. This is the deliverable that makes divergence fail a test.
- `backend/app/services/domains/products.py` — **tidy only, if warranted.** `product_spec_from_position_payload` and `product_spec_from_executable_terms` are near-duplicate factories; consolidate their shared body only if it can be done without behavior change (Task 3 decides based on a real diff).
- No other production files are expected to change. Task 3's orphan sweep is grep-driven and may legitimately remove nothing.

---

## Channel adapter entry points (for the test author)

| Channel | Call that yields canonical `product_kwargs` |
|---|---|
| direct/agent | `build_product(family, flat_terms).product_kwargs` |
| RFQ | `from app.services.rfq import _executable_product_kwargs`; build an `RFQRequestDraft(product_type=…, product_kwargs=flat_terms)`; `_executable_product_kwargs(draft, quote_mode="price")[0]` (price mode → no solve-target fill) |
| try-solve | `from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years`; build a `TrySolveRowIn`; `_build_row_termsheet(row, product, market, maturity, quote_field)[0]` |
| OTC import | `from app.services.position_adapter import map_trade_row`; `build_product(family, map_trade_row(row).product_kwargs, prebuilt=True).product_kwargs` (snowball-family may also use auto-detect, prebuilt unnecessary) |

`RFQRequestDraft` / `RFQUnknownSpecIn` / `RFQTargetIn` live in `app.schemas` (see `tests/test_services_domains_rfq.py` for construction examples: `RFQRequestDraft(product_type="SnowballOption", product_kwargs={…})`). `TrySolveRowIn` / `TrySolveMarketIn` / `TrySolveQuoteRequestIn` and `registry_by_key` are imported by `tests/test_try_solve.py`. The OTC row builders (`snowball_row`, `vanilla_row`, etc.) live in `tests/test_position_import_pricing.py` — import them or replicate the minimal row dicts.

---

### Task 1: Golden snowball — RFQ ≡ agent (byte-identical) + try-solve & import (structural)

**Files:**
- Create: `tests/test_cross_channel_equivalence.py`

**Context:** The snowball is the hardest, highest-value family (it synthesizes KO/KI schedules). A uniform golden snowball expressed as agent flat terms and as an RFQ draft must produce **byte-identical** `product_kwargs` (both go through `build_product` on the same flat contract). try-solve and import must match **structurally**. These are characterization/lock-in tests: they should pass on first run (proving convergence); a FAILURE reveals a real divergence bug to fix, not a test to weaken.

- [ ] **Step 1: Write the golden snowball equivalence test**

```python
"""Cross-channel equivalence net: every workflow channel must reach the same
canonical build_product output for a golden product per family. This is the
regression net that makes builder divergence (the root cause the unified-product-
schema migration removed) fail a test. RFQ vs agent: byte-identical. try-solve and
OTC import: structural (same family + KO-schedule record count + barrier/rate
economics) — import carries a complete heterogeneous termsheet the uniform
synthesizer cannot reproduce key-for-key."""
from app.services.domains.product_builders import build_product


# Uniform snowball, fully specified (no solve target). trade_start_date is PINNED
# (not the RFQ template default) so synthesis is deterministic across channels.
GOLDEN_SNOWBALL_FLAT = {
    "initial_price": 100.0,
    "strike": 100.0,
    "maturity_years": 1.0,
    "ko_barrier_pct": 103.0,
    "ki_barrier_pct": 75.0,
    "ko_rate": 0.10,
    "lockup_months": 3,
    "trade_start_date": "2026-07-01",
    "observation_frequency": "MONTHLY",
    "contract_multiplier": 1.0,
}


def _agent_snowball_kwargs():
    built = build_product("SnowballOption", dict(GOLDEN_SNOWBALL_FLAT))
    assert built.ok, built.validation
    return built.product_kwargs


def test_rfq_snowball_is_byte_identical_to_agent():
    from app.schemas import RFQRequestDraft
    from app.services.rfq import _executable_product_kwargs

    draft = RFQRequestDraft(
        product_type="SnowballOption",
        product_kwargs=dict(GOLDEN_SNOWBALL_FLAT),
    )
    rfq_kwargs, missing = _executable_product_kwargs(draft, quote_mode="price")
    assert missing == []
    # Same flat contract through the same producer -> identical canonical kwargs.
    assert rfq_kwargs == _agent_snowball_kwargs()


def _ko_records(product_kwargs):
    return product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]


def test_import_snowball_is_structurally_equivalent_to_agent():
    from app.services.position_adapter import map_trade_row

    # A UNIFORM imported snowball (single ko level/rate, explicit observation dates).
    # Reuse the import row-builder pattern; collapse the step-down to one level.
    from test_position_import_pricing import snowball_row  # type: ignore
    row = snowball_row("T-GOLDEN")
    row["敲出价格"] = "103"          # uniform ko level
    row["敲出收益率"] = "10%"        # uniform ko rate
    row["敲入价格"] = 75.0
    row["敲出观察日"] = "2026/10/01,2027/07/01"

    mapping = map_trade_row(row)
    built = build_product("SnowballOption", dict(mapping.product_kwargs), prebuilt=True)
    assert built.ok, built.validation

    agent = _agent_snowball_kwargs()
    # Structural: same family + a real KO schedule + matching ko economics.
    assert built.product_spec.quantark_class == "SnowballOption"
    assert built.product_spec.product_family == "autocallable"
    import_records = _ko_records(built.product_kwargs)
    assert len(import_records) >= 1
    assert built.product_kwargs["barrier_config"]["ko_barrier"] in (103.0, [103.0])
    # the imported KO coupon matches the golden 10% on the first observation
    assert abs(import_records[0]["return_rate"] - 0.10) < 1e-9


def test_trysolve_snowball_is_structurally_equivalent_to_agent():
    from app.services.try_solve import (
        _build_row_termsheet, _pricing_market, _maturity_years, registry_by_key,
    )
    from app.schemas import TrySolveRowIn, TrySolveMarketIn, TrySolveQuoteRequestIn

    row = TrySolveRowIn(
        row_id="g1", product_key="autocall",
        fields={"underlying": "000905.SH", "notional": 1_000_000,
                "start_date": "2026-07-01", "tenor_months": 12,
                "ko_barrier": 1.03, "ki_barrier": 0.75,
                "observation_frequency": "MONTHLY", "lockup_months": 3},
        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
        quote_request=TrySolveQuoteRequestIn(quote_field_key="annualized_coupon",
                                             initial_guess=0.10, target_label="price", target_value=5.0),
    )
    product = registry_by_key()["autocall"]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(
        row, product, market, _maturity_years(row), product.quote_fields["annualized_coupon"],
    )
    assert missing == []
    # Structural: same family + a KO schedule present (try-solve scales moneyness +
    # defaults, so byte-identity is intentionally not asserted).
    assert "barrier_config" in kwargs
    assert len(_ko_records(kwargs)) >= 1
```

NOTE: confirm `TrySolveRowIn`/`TrySolveMarketIn`/`TrySolveQuoteRequestIn` import paths against `tests/test_try_solve.py` (they may be re-exported from a different module). If `snowball_row` cannot be imported cross-module cleanly, inline the minimal row dict using `TRADE_HEADERS` from `tests/test_position_import_pricing.py`.

- [ ] **Step 2: Run the snowball equivalence tests**

Run: `python -m pytest tests/test_cross_channel_equivalence.py -v`
Expected: PASS. The RFQ≡agent test is the load-bearing lock-in. If `test_rfq_snowball_is_byte_identical_to_agent` FAILS, the channels diverge on snowball construction — STOP and report the diff (this is a real bug the net just caught, not a test to relax). If a structural test fails on a field name (e.g. `return_rate` vs `ko_rate`), read the actual synthesized record shape and fix the assertion to the real key (do not loosen it to vacuous).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cross_channel_equivalence.py
git commit -m "test(equivalence): golden snowball cross-channel regression net"
```

---

### Task 2: Golden scalar/coupon families — agent ≡ import (structural), + RFQ/try-solve where they emit it

**Files:**
- Modify: `tests/test_cross_channel_equivalence.py`

**Context:** Beyond snowball, the channels share several families. Pin a golden product for each and assert the channels that *emit* it converge. Scope per family to the channels that actually produce it: OTC import emits vanilla/american/digital/barrier/single_sf/double_sf/snowball/phoenix; the agent emits all 14; try-solve emits the solver families; RFQ templates cover snowball/ko_reset/phoenix + the vanilla template. Do not assert a channel that doesn't emit a family.

- [ ] **Step 1: Write the scalar/phoenix equivalence tests**

Add to `tests/test_cross_channel_equivalence.py`:

```python
def test_import_vanilla_validates_and_matches_agent_family():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import vanilla_row  # type: ignore

    mapping = map_trade_row(vanilla_row("T-GV"))
    imported = build_product("EuropeanVanillaOption", dict(mapping.product_kwargs), prebuilt=True)
    assert imported.ok, imported.validation
    assert imported.product_spec.product_family == "option"
    # the agent build of the same economics is the same family + engine
    agent = build_product(
        "EuropeanVanillaOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert agent.ok
    assert agent.engine_name == imported.engine_name == "BlackScholesEngine"
    assert agent.product_spec.product_family == imported.product_spec.product_family


def test_import_phoenix_validates_through_prebuilt_gate():
    from app.services.position_adapter import map_trade_row
    from test_position_import_pricing import phoenix_row  # type: ignore

    mapping = map_trade_row(phoenix_row("T-GP"))
    built = build_product("PhoenixOption", dict(mapping.product_kwargs), prebuilt=True)
    assert built.ok, built.validation
    assert built.product_spec.quantark_class == "PhoenixOption"
    assert built.product_spec.product_family == "autocallable"
    assert "coupon_config" in built.product_kwargs


import pytest


@pytest.mark.parametrize(
    "structure, quantark_class, family",
    [
        ("欧式二元", "CashOrNothingDigitalOption", "option"),
        ("基础障碍敲入期权", "BarrierOption", "barrier"),
        ("单鲨", "SingleSharkfinOption", "sharkfin"),
        ("双鲨", "DoubleSharkfinOption", "sharkfin"),
    ],
)
def test_import_family_round_trips_through_prebuilt_gate(structure, quantark_class, family):
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
    built = build_product(quantark_class, dict(mapping.product_kwargs), prebuilt=True)
    assert built.ok, built.validation
    assert built.product_spec.quantark_class == quantark_class
    assert built.product_spec.product_family == family
```

NOTE: cross-check each `(structure, quantark_class, family)` triple against `position_adapter.map_trade_row`'s `mapping_by_structure` and `products.product_family_for_quantark_class`. Adjust the family strings to whatever `product_family_for_quantark_class` actually returns (e.g. digital → "option", barrier → "barrier", sharkfin → "sharkfin", touch → "touch"). If a row builder needs extra columns to map cleanly (the spike in the OTC plan showed which), add them.

- [ ] **Step 2: Run the scalar/phoenix tests**

Run: `python -m pytest tests/test_cross_channel_equivalence.py -v`
Expected: PASS (all snowball + scalar + phoenix cases). A failure on a family-string assertion means the expected family was wrong — fix it to `product_family_for_quantark_class`'s real output (that *is* the single derivation point this test also pins). A `build_product` `ok=False` means the import termsheet doesn't validate-and-wrap — STOP and report (it contradicts the OTC migration's spike, which found all 8 families validate).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cross_channel_equivalence.py
git commit -m "test(equivalence): golden scalar + phoenix cross-channel coverage"
```

---

### Task 3: Honest tidy — verify no dead builder code; consolidate the duplicate spec factory if safe

**Files:**
- Modify (only if warranted): `backend/app/services/domains/products.py`
- Test: existing suites (no new tests unless a consolidation is made)

**Context:** A pre-plan survey found **no orphaned helpers** in `product_builders.py`/`rfq.py`, and family-derivation already consolidated onto the canonical product (`create_or_get_product` trusts `spec.product_family`; `repair_invalid_snowball_booking_terms` is a live startup repair, not dead). So this task is a *verification* that the migration left the tree clean, plus one optional DRY consolidation. Expect to remove little or nothing — that is a successful outcome, not a gap.

- [ ] **Step 1: Orphan sweep (grep-driven, decision-ruled)**

Run, from the repo root:
```bash
for f in backend/app/services/domains/product_builders.py backend/app/services/rfq.py backend/app/services/try_solve.py backend/app/services/position_adapter.py backend/app/services/domains/booking.py; do
  echo "## $f"
  grep -oE "^def [a-zA-Z_]+|^    def [a-zA-Z_]+" "$f" | sed -E 's/^ *def //' | while read fn; do
    n=$(grep -rc "\\b$fn\\b" backend/app | grep -v __pycache__ | awk -F: '{s+=$2} END{print s}')
    [ "${n:-0}" -le 1 ] && echo "  ORPHAN? $fn (refs=$n)"
  done
done
```
**Decision rule:** for each reported `ORPHAN?`, confirm it is truly unreferenced (check tests too: `grep -rn "\b<fn>\b" tests/`). A private helper (`_name`) with refs ≤ 1 and no test reference is genuinely dead → remove it. A public function may be part of an API surface (exported, used by `main.py`/tools) → keep. Remove only what is provably dead. If the sweep reports nothing (the expected result), record "no orphans" and proceed — do not invent removals.

- [ ] **Step 2: Evaluate the duplicate ProductSpec factory**

Read `product_spec_from_position_payload` and `product_spec_from_executable_terms` in `backend/app/services/domains/products.py`. They are near-duplicate (both derive `product_family_for_quantark_class`, build a `ProductSpec`). **Only if** their bodies are identical modulo input-shape extraction, extract the shared `ProductSpec` construction into a small private helper (e.g. `_spec_from_parts(...)`) that both call — pure DRY, no behavior change. If they differ materially (different defaults, currency fallback "USD" vs payload-driven, source_payload handling), **leave them** and record why (consolidating divergent factories risks a subtle regression — not worth it).

- [ ] **Step 3: Run the affected suites**

Run: `python -m pytest tests/test_product_builders.py tests/test_product_booking.py tests/test_services_domains_positions.py tests/test_services_domains_rfq.py tests/test_tools_products.py -q`
Expected: PASS. If you removed an "orphan" and a test fails, it was not dead — revert that removal.

- [ ] **Step 4: Commit (only if something changed)**

```bash
# If orphans removed and/or factory consolidated:
git add backend/app/services/domains/products.py  # and any file an orphan was removed from
git commit -m "refactor(cleanup): remove dead helpers; dedupe ProductSpec factory"
# If nothing was genuinely dead, skip the commit and note "tidy: verified clean, no changes" in the Task 5 report.
```

---

### Task 4: Document the closed migration

**Files:**
- Modify: `docs/superpowers/specs/2026-05-30-unified-product-schema-design.md` (or a short note in `build-contract.md`)

**Context:** The strangler-fig is complete. Leave a durable marker so the next reader knows the four channels converge and how the net protects it.

- [ ] **Step 1: Add a short status note**

At the top of `docs/superpowers/specs/2026-05-30-unified-product-schema-design.md` (just under the `Status:` line), add:

```markdown
> **Status update (2026-06-01): COMPLETE.** All four channels (RFQ, try-solve, OTC
> import, direct/agent) reach the single producer `build_product`. Cross-channel
> equivalence is pinned by `tests/test_cross_channel_equivalence.py` (RFQ ≡ agent
> byte-identical; try-solve & OTC import structural). OTC import is validate-and-wrap
> (`prebuilt=True`), not re-synthesized, because it carries heterogeneous per-date
> schedules — a deliberate deviation from "retire position_adapter synthesis".
```

Ensure the wording trips none of `tests/test_reference_docs.py`'s archaeology-marker regex if you instead edit `build-contract.md`. (Editing the spec doc under `docs/` is not covered by that test — the simplest place.)

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-30-unified-product-schema-design.md
git commit -m "docs(schema): mark unified-product-schema migration complete"
```

---

### Task 5: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite from the repo root**

Run: `python -m pytest -q`
Expected: PASS apart from pre-existing, unrelated failures (confirm each fails identically on the base commit before dismissing — notably the known `test_quant_agent_tools_count_unchanged` 63→64 drift, which should already be fixed if the two prerequisite branches were merged correctly; and optional-dep `langchain_quickjs`/`deepseek`). The new `tests/test_cross_channel_equivalence.py` must be green.

- [ ] **Step 2: Finish the development branch**

Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present options, execute the choice.

---

## Notes / risks

- **The RFQ≡agent byte-identity is the load-bearing assertion.** It is robust because both channels call `build_product` on the *same* flat contract; identity is structural, not coincidental. If it ever fails, a channel reintroduced its own construction — exactly the divergence the migration removed.
- **Import/try-solve are structural by necessity.** OTC import carries a complete heterogeneous termsheet (step-downs, explicit dates); try-solve scales moneyness and defaults schedule inputs. Asserting byte-identity there would be brittle and would not reflect a real invariant. Structural equivalence (same family + schedule presence + matching economics) is the honest, durable assertion.
- **The tidy is expected to be near-empty.** The survey found no orphans and consolidated family derivation. Removing nothing is a valid outcome; do not manufacture cleanup. The value of this plan is the regression net, not deletions.
- **Prerequisite ordering is load-bearing.** Without the OTC branch's `prebuilt`, the import arm of the net cannot build; without the double-one-touch branch's deletion, a stray `_product_kwargs_for_row` reference could mislead the orphan sweep. Merge both first.
- **`trade_start_date` must be pinned in the golden fixture**, not left to the RFQ template's `_default_trade_start_date()` (today+7d) — otherwise agent and RFQ synthesize different observation dates and the byte-identity assertion fails for a spurious reason.
