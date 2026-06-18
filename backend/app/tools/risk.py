"""@tool wrappers for the risk domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/risk,
shape JSON. The wire shapes preserve the legacy langchain_tools.py payloads so
existing agent tests continue to exercise this layer untouched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import database
from app.schemas import PortfolioPositionSpec, PricingEnvironmentSnapshot
from app.services import fx as fx_svc
from app.services.currency_codes import ISO_4217_CODES, normalize_currency
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import risk as risk_svc
from app.services.fx import fx_rate_as_of
from ._product_inputs import ToolPositionSnapshotSpec


class PortfolioSnapshotInput(BaseModel):
    positions: list[ToolPositionSnapshotSpec] = Field(default_factory=list)
    market: PricingEnvironmentSnapshot = Field(
        default_factory=PricingEnvironmentSnapshot
    )


class RunBatchPricingInput(BaseModel):
    # Forbid extras so market overrides (spot/rate/dividend_yield/volatility)
    # or valuation_date fail LOUDLY instead of being silently dropped from an
    # approved action. Batch pricing is profile-driven; overrides belong to
    # the sync position-detail dialog endpoint or price_product what-ifs.
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    method: str = "summary"
    position_ids: list[int] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional position id subset to scope the batch-pricing run to. "
            "Omit to run the full resolved portfolio."
        ),
    )
    pricing_parameter_profile_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "pricing_parameter_profile_id", "pricing_profile_id"
        ),
        description=(
            "Pricing parameter profile id that supplies r/q/vol and the "
            "valuation date for the run. Use the page context "
            "pricing_profile_id when present."
        ),
    )


class LatestRiskRunInput(BaseModel):
    portfolio_id: int


class HedgeInput(BaseModel):
    risk: dict[str, Any] = Field(default_factory=dict)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("calculate_risk", args_schema=PortfolioSnapshotInput)
def calculate_risk_tool(
    positions: list[ToolPositionSnapshotSpec],
    market: PricingEnvironmentSnapshot = PricingEnvironmentSnapshot(),
) -> dict[str, Any]:
    """Calculate deterministic risk metrics for a supplied position snapshot."""
    legacy_positions = [
        PortfolioPositionSpec.model_validate(position.to_legacy_payload())
        for position in positions
    ]
    return risk_svc.calculate_risk(positions=legacy_positions, market=market)


def _estimate_run_batch_pricing_cost(tool_input: Any) -> float:
    """Estimate seconds for run_batch_pricing based on portfolio size."""
    if not isinstance(tool_input, dict):
        return 0.0
    portfolio_id = tool_input.get("portfolio_id")
    if portfolio_id is None:
        return 0.0
    position_ids = tool_input.get("position_ids")
    return float(
        risk_svc.estimate_run_seconds(
            portfolio_id=int(portfolio_id),
            position_ids=position_ids if isinstance(position_ids, list) else None,
        )
    )


@capability_gated(
    group=ToolGroup.DOMAIN_WRITE, cost_estimator=_estimate_run_batch_pricing_cost
)
@tool("run_batch_pricing", args_schema=RunBatchPricingInput)
def run_batch_pricing_tool(
    portfolio_id: int,
    method: str = "summary",
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
) -> dict[str, Any]:
    """Queue the audited async batch-pricing run for a portfolio or position
    subset. ONE pass reprices the scoped positions against the pricing
    parameter profile AND computes portfolio risk metrics, persisting both a
    position valuation run and a risk run. Use for any persisted portfolio
    repricing or risk refresh; do not queue it twice for pricing-then-risk.
    Market overrides (spot/r/q/vol) and valuation_date are NOT accepted:
    create or select a pricing parameter profile instead, or use
    price_product for one-off non-persisted what-ifs."""
    return risk_svc.run(
        portfolio_id=portfolio_id,
        method=method,
        position_ids=position_ids,
        pricing_profile_id=pricing_parameter_profile_id,
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_latest_risk_run", args_schema=LatestRiskRunInput)
def get_latest_risk_run_tool(portfolio_id: int) -> dict[str, Any]:
    """Read the latest completed stored portfolio risk run and metrics."""
    run = risk_svc.get_latest_run(portfolio_id=portfolio_id)
    if run is None:
        return {
            "portfolio_id": portfolio_id,
            "found": False,
            "message": "No completed stored risk run exists for this portfolio.",
        }
    metrics = run.metrics or {}
    return {
        "portfolio_id": portfolio_id,
        "found": True,
        "risk_run_id": run.id,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        # Pricing as-of of the run (profile valuation date for profile-bound
        # runs, queue time otherwise). When this is older than created_at the
        # run is a historical repricing, NOT current risk — do not hedge on it.
        "valuation_as_of": metrics.get("valuation_as_of"),
        "metrics": metrics,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("recommend_hedge", args_schema=HedgeInput)
def recommend_hedge_tool(risk: dict[str, Any]) -> dict[str, Any]:
    """Recommend a hedge from calculated risk metrics."""
    return risk_svc.recommend_hedge(risk=risk)


class ConvertCurrencyInput(BaseModel):
    by_currency: dict[str, dict[str, float]] = Field(
        description="The by_currency block from a risk run (money metrics per currency)."
    )
    target_currency: str = Field(description="ISO code to convert into, e.g. 'USD'.")
    valuation_date: str = Field(
        description="ISO date (YYYY-MM-DD) used to resolve FX rates reproducibly."
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("convert_currency", args_schema=ConvertCurrencyInput)
def convert_currency_tool(
    by_currency: dict[str, dict[str, float]],
    target_currency: str,
    valuation_date: str,
) -> dict[str, Any]:
    """Deterministically FX-convert a risk run's per-currency money metrics into a
    single target currency, using valuation-snapshot rates (latest <= valuation_date).
    Never fabricates a rate: unconvertible currencies are returned in `missing`."""
    target = normalize_currency(target_currency)
    if target not in ISO_4217_CODES:
        return {
            "error": f"Invalid target currency: {target_currency!r}",
            "totals": {},
            "fx_rates_used": {},
            "missing": [],
        }
    target_currency = target
    as_of = datetime.fromisoformat(valuation_date)
    with database.SessionLocal() as session:
        def _lookup(base: str, quote: str) -> float | None:
            return fx_rate_as_of(session, base, quote, as_of)

        result = fx_svc.convert_risk_currency(by_currency, target_currency, _lookup)
    result["as_of"] = valuation_date
    return result
