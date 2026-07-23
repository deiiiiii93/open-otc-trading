"""quantark.execution adoption (phase 1, spec 2026-07-21).

Owns every touchpoint with quantark 0.3.0's execution framework. Registered
scenario callables are module-level named functions and register at import
time — the spawn-safety contract requires workers to rebuild them by
importing this module under its canonical name.

Phase 1 migrates the portfolio scenario grid
(:func:`app.services.risk_engine.run_portfolio_scenarios`) from a hand-rolled
``ThreadPoolExecutor`` to ``PricingSession.run_scenario_plans``: one bounded
pool across all position x shift cells, a per-cell error boundary, and
reproducibility manifests. Numbers and error semantics are unchanged — the
runner reuses the exact termsheet build chain of
:func:`app.services.quantark.price_product`, and a failed cell maps to a
0.0 contribution just like a failed ``price_product`` call did.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

FACTORY_ID = "open-otc-pricing-inputs/v1"
TRANSFORMER_ID = "open-otc-market-shift/v1"
RUNNER_ID = "open-otc-unit-price/v1"

BASE_SCENARIO_ID = "base"


@dataclass(frozen=True)
class OtcPricingInputs:
    """Scenario base: everything needed to price one position's unit PV.

    ``market`` is a ``PricingEnvironmentSnapshot.model_dump(mode="json")``
    dict so the whole base stays JSON-representable across process
    boundaries. The transformer replaces only ``market``.
    """

    product_type: str
    product_kwargs: dict[str, Any]
    market: dict[str, Any]
    engine_name: str
    engine_kwargs: dict[str, Any]


def build_pricing_inputs(payload: dict[str, Any]) -> OtcPricingInputs:
    """Registered factory: rebuild the base from its JSON payload."""
    return OtcPricingInputs(
        product_type=payload["product_type"],
        product_kwargs=dict(payload["product_kwargs"] or {}),
        market=dict(payload["market"]),
        engine_name=payload["engine_name"],
        engine_kwargs=dict(payload.get("engine_kwargs") or {}),
    )


def market_shift_transform(
    base: OtcPricingInputs, parameters: dict[str, Any]
) -> OtcPricingInputs:
    """Registered transformer: relative spot pct + absolute vol shift.

    Mirrors the shift math of the legacy ``_portfolio_value`` exactly and
    never mutates ``base`` in place (planner purity gate).
    """
    market = dict(base.market)
    market["spot"] = float(market["spot"]) * (
        1.0 + float(parameters["spot_shift_pct"]) / 100.0
    )
    market["volatility"] = max(
        float(market["volatility"]) + float(parameters["vol_shift_abs"]), 1e-6
    )
    return dataclasses.replace(base, market=market)


def unit_price_runner(cell: Any, resolved: Any, child_context: Any) -> tuple:
    """Registered runner (value_kind="float"): unit PV of one position.

    Reuses the ``price_product`` build chain verbatim. Exceptions propagate
    so the framework records the native error type in ``PricingFailure``.
    """
    from ..schemas import PricingEnvironmentSnapshot
    from .quantark import _build_termsheet, ensure_quantark_path

    ensure_quantark_path()
    from quantark.rfq.builders import (
        build_engine_from_termsheet,
        build_pricing_env_from_market_kwargs,
        build_product_from_termsheet,
    )

    inputs: OtcPricingInputs = resolved.transformed
    market = PricingEnvironmentSnapshot.model_validate(inputs.market)
    termsheet, otc_attrs = _build_termsheet(
        product_type=inputs.product_type,
        product_kwargs=inputs.product_kwargs,
        market=market,
        engine_name=inputs.engine_name,
        engine_kwargs=inputs.engine_kwargs,
    )
    product = build_product_from_termsheet(termsheet)
    for key, value in otc_attrs.items():
        setattr(product, key, value)
    pricing_env = build_pricing_env_from_market_kwargs(termsheet.market_kwargs)
    engine = build_engine_from_termsheet(termsheet)
    pv = float(engine.price(product, pricing_env))
    return pv, (("pv", pv),), None


def _register_callables() -> None:
    from .quantark import ensure_quantark_path

    ensure_quantark_path()
    from quantark.execution.scenario import registries

    registries.register_factory(FACTORY_ID, build_pricing_inputs)
    registries.register_transformer(
        TRANSFORMER_ID,
        market_shift_transform,
        allowed_tags=frozenset({"spot", "vol"}),
        components=(
            ("spot", lambda base: base.market["spot"]),
            ("vol", lambda base: base.market["volatility"]),
        ),
        covered_fields=("market",),
    )
    registries.register_runner(RUNNER_ID, unit_price_runner, value_kind="float")


_register_callables()


def _scenario_specs(
    spot_shifts_pct: list[float], vol_shifts_abs: list[float]
) -> list:
    """Base cell first, then grid cells in legacy (vol row, spot col) order."""
    from quantark.execution import ScenarioSpec

    def _spec(scenario_id: str, spot_shift_pct: float, vol_shift_abs: float):
        return ScenarioSpec(
            scenario_id=scenario_id,
            transformer_id=TRANSFORMER_ID,
            parameters=(
                ("spot_shift_pct", float(spot_shift_pct)),
                ("vol_shift_abs", float(vol_shift_abs)),
            ),
            mutation_tags=frozenset({"spot", "vol"}),
            required_capabilities=frozenset({f"runner:{RUNNER_ID}"}),
        )

    specs = [_spec(BASE_SCENARIO_ID, 0.0, 0.0)]
    for vol_shift in vol_shifts_abs:
        for spot_shift_pct in spot_shifts_pct:
            specs.append(
                _spec(f"spot{spot_shift_pct:+g}pct/vol{vol_shift:+g}", spot_shift_pct, vol_shift)
            )
    return specs


def run_market_shift_grid(
    items: list[dict[str, Any]],
    spot_shifts_pct: list[float],
    vol_shifts_abs: list[float],
    *,
    backend: str | None = None,
    workers: int | None = None,
) -> list[list]:
    """Price every item under every (spot, vol) shift via run_scenario_plans.

    ``items`` are payload dicts with keys ``product_type``, ``product_kwargs``,
    ``market`` (JSON-mode snapshot dump), ``engine_name``, ``engine_kwargs``.
    Returns one outcome list per item, aligned with ``_scenario_specs`` order:
    index 0 is the base cell, then grid cells row-major over
    ``vol_shifts_abs`` x ``spot_shifts_pct``. Entries are ``ScenarioOutcome``
    (``.value`` = unit PV float) or ``PricingFailure``.
    """
    from quantark.execution import (
        ExecutionPolicy,
        ExecutorSelection,
        PricingSession,
        default_context,
    )
    from quantark.execution.scenario.contracts import BaseInputsRef

    from ..config import get_settings

    settings = get_settings()
    backend = backend or settings.quantark_execution_backend
    workers = max(1, int(workers or settings.risk_parallel_workers))
    if backend == "processes" and workers <= 1:
        # Explicit call-layer policy: a one-worker process pool is pure
        # spawn overhead, so a single worker runs serial.
        backend = "serial"

    specs = _scenario_specs(spot_shifts_pct, vol_shifts_abs)
    plans = [
        (
            BaseInputsRef(
                factory_id=FACTORY_ID,
                payload=tuple(sorted(payload.items())),
            ),
            specs,
        )
        for payload in items
    ]
    context = dataclasses.replace(
        default_context(),
        execution_policy=ExecutionPolicy(
            scenario=ExecutorSelection(backend=backend, workers=workers)
        ),
    )
    with PricingSession(context) as session:
        return session.run_scenario_plans(
            plans, engine_factory=None, collect_errors=True
        )
