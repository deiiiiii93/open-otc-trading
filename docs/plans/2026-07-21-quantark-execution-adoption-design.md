# quantark.execution adoption — phase 1 design

Date: 2026-07-21. Status: validated design (auto mode, single pass).

## Goal

Adopt quantark 0.3.0's `quantark.execution` framework in open-otc-trading,
starting with the most self-contained parallel-pricing hot spot:
`risk_engine.run_portfolio_scenarios` (the spot×vol reprice grid). Behavior
and numbers must stay identical; the hand-rolled `ThreadPoolExecutor` is
replaced by `PricingSession.run_scenario_plans` (one bounded pool, per-cell
error boundary, reproducibility manifests).

## Why this slice first (YAGNI)

- `price_many` is serial-only upstream, so it cannot replace the thread pools.
- The risk batch (`calculate_portfolio_risk`, price+greeks per position) maps
  to the greek-bump cells, but those mirror `calculate_numerical_greeks`
  bitwise — the app currently uses `method="auto"` (analytical where
  available), so migrating it would change numbers. Deferred to phase 2.
- The scenario grid is reprice-only, already isolates per-cell failures as
  0.0 contributions, and its unit of work (one position, one shift) maps
  1:1 onto scenario cells.

## Architecture

New module `backend/app/services/quantark_execution.py` — owns every
framework touchpoint. Registered callables are module-level named functions
(spawn-safety contract); registration happens at module import (the module
is always imported by its canonical name, never as `__main__`).

- `OtcPricingInputs` (frozen dataclass): `product_type`, `product_kwargs`,
  `market` (a `PricingEnvironmentSnapshot.model_dump()` dict),
  `engine_name`, `engine_kwargs`. This is the scenario base.
- Factory `open-otc-pricing-inputs/v1`: `payload dict -> OtcPricingInputs`.
  Deterministic and pure, so worker-side rebuild fingerprints match.
- Transformer `open-otc-market-shift/v1`: applies
  `spot * (1 + pct/100)` and `max(vol + shift, 1e-6)` to a copy of the
  market dict (exactly today's `_portfolio_value` math), returns
  `dataclasses.replace(base, market=shifted)`. `allowed_tags={"spot","vol"}`,
  component extractors read `market["spot"]` / `market["volatility"]`,
  `covered_fields=("market",)`.
- Runner `open-otc-unit-price/v1` (`value_kind="float"`, processes-eligible):
  rebuilds the market snapshot, then reuses the existing build chain
  verbatim (`_build_termsheet` → `build_product_from_termsheet` →
  `build_pricing_env_from_market_kwargs` → `build_engine_from_termsheet` →
  `engine.price`) and returns the float unit PV. Engine factory is `None`;
  the runner builds the engine from the payload, so one packed run may mix
  engine types.
- `run_market_shift_grid(items, spot_shifts_pct, vol_shifts_abs, *, backend,
  workers)`: packs one `(BaseInputsRef, specs)` plan per position — specs =
  base cell `(0.0, 0.0)` + every grid cell — and executes all plans through
  one `PricingSession.run_scenario_plans(..., collect_errors=True)` call.
  Returns the per-plan outcome lists (`ScenarioOutcome.value` = unit PV, or
  `PricingFailure`).

`run_portfolio_scenarios` keeps its current contract: exclusion gate,
`price * quantity`, `usable_model_value` gate against the shifted snapshot
(re-derived caller-side, cheap — no pricing), `PricingFailure` → 0.0
(matching today's `price_product` exception path, which also yields 0.0).

## Settings

`config.py` gains `quantark_execution_backend` (env
`OPEN_OTC_QUANTARK_EXECUTION_BACKEND`), default `"processes"`; worker count
reuses `risk_parallel_workers`. `"serial"` is supported for tests and
single-core hosts. No silent fallback — the framework forbids it and the
setting is explicit.

## Error handling

- Per-cell pricing exceptions → `PricingFailure` (collect_errors) → 0.0
  contribution, same as today's `ok=False` path.
- One plan's base/planning failure → aligned `PricingFailure` list for that
  position only; other positions still execute (upstream guarantee).
- `processes` requires a re-importable `__main__` (uvicorn and pytest both
  qualify); stdin/heredoc hosts fail closed with `CapabilityError`.

## Testing

New `tests/test_quantark_execution.py`:

1. Unit-price parity: runner PV == `price_product` PV for a snowball spec.
2. Grid parity: `run_portfolio_scenarios` (serial backend) matches the
   legacy inline reprice math computed with `price_product`.
3. processes backend == serial backend on a small grid.
4. Failure isolation: one broken position → its cells 0.0, others priced.

Regression: `test_risk_engine.py`, `test_batch_pricing.py`,
`test_position_pricer_*.py`, plus the earlier 881-test quantark batch.

## Phase 2 (not in this change)

- Migrate `calculate_portfolio_risk` price+greeks to greek-bump cells
  (`TradeState`, `greek-bump/v1`, `greek-value/v1`) once the analytical-vs-
  numerical greeks policy is decided.
- Route `price_product` through a shared `PricingSession` for
  prepared-artifact/draw cache reuse.
- `greeks_landscape` curves via scenario plans.
