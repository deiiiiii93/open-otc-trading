"""Greeks-based P&L attribution against a risk-run snapshot diff.

Decomposes the market-value move between two runs into delta / gamma / vega /
theta / rho / rho_q buckets plus an unexplained residual. Unit multipliers are
read from the run's own ``metric_contract`` rather than hardcoded, so a future
contract revision is either followed or rejected loudly.

Convention: **start-of-period Greeks.** The ``before`` run's sensitivities are
applied to the market move. Using the ``after`` run's Greeks instead would
silently change every number in every report, so the choice is explicit here.
"""
from __future__ import annotations

from typing import Any

# Above this |residual| / |actual| ratio the decomposition must not be
# presented as an explanation. Consumed by the pnl.explain report block and
# quoted in the trader template's narrative brief.
RESIDUAL_WARN_RATIO = 0.10

_SUPPORTED_CONTRACT_IDS = frozenset({"limits-risk_run-metrics/v1"})

# Multiplier applied to each raw market change to match the Greek's declared
# unit in `limits-risk_run-metrics/v1`:
#   delta  underlying_units                  -> per 1.0 of spot
#   gamma  underlying_units_per_spot_unit    -> per 1.0 of spot (squared term)
#   vega   {currency}/1volpct                -> per 1 vol POINT, sigma is absolute
#   theta  {currency}/1day                   -> per 1 day
#   rho    {currency}/1pct                   -> per 1 PERCENT, rate is absolute
#   rho_q  {currency}/1pct                   -> per 1 PERCENT, yield is absolute
_VOL_POINTS_PER_UNIT = 100.0
_RATE_PERCENT_PER_UNIT = 100.0

_BUCKETS: tuple[str, ...] = ("delta", "gamma", "vega", "theta", "rho", "rho_q")


class UnsupportedMetricContract(RuntimeError):
    """Raised when a run declares a metric contract whose units are unknown."""


def _contract_id(metrics: dict[str, Any]) -> str | None:
    contract = (metrics.get("source_metadata") or {}).get("metric_contract") or {}
    value = contract.get("contract_id")
    return value if isinstance(value, str) else None


def _assert_supported(before: dict[str, Any], after: dict[str, Any]) -> None:
    for metrics, label in ((before, "before"), (after, "after")):
        contract_id = _contract_id(metrics)
        if contract_id is None:
            raise UnsupportedMetricContract(
                f"{label} run declares no metric_contract.contract_id; "
                "attribution units cannot be resolved"
            )
        if contract_id not in _SUPPORTED_CONTRACT_IDS:
            raise UnsupportedMetricContract(
                f"{label} run declares unsupported metric contract {contract_id!r}; "
                f"known: {sorted(_SUPPORTED_CONTRACT_IDS)}"
            )


def _float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _positions_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["position_id"]): row
        for row in metrics.get("positions") or []
        if isinstance(row, dict) and row.get("position_id") is not None
    }


def _exclusion_reason(
    before_row: dict[str, Any], after_row: dict[str, Any]
) -> str | None:
    for row, label in ((before_row, "before"), (after_row, "after")):
        if not row.get("pricing_ok", True):
            return f"pricing_ok is false in the {label} run"
        if not row.get("greeks_ok", True):
            return f"greeks_ok is false in the {label} run"
    return None


def _changed(market: dict[str, Any], key: str) -> float:
    entry = market.get(key) or {}
    return _float(entry.get("change"))


def explain_diff(
    before: dict[str, Any], after: dict[str, Any], diff: dict[str, Any]
) -> dict[str, Any]:
    """Decompose the market-value move described by ``diff`` into Greek buckets.

    ``diff`` must be the output of ``snapshot_diff.diff_metrics(before, after)``.
    """
    _assert_supported(before, after)

    before_positions = _positions_by_id(before)
    after_positions = _positions_by_id(after)
    elapsed_days = _float((diff.get("as_of") or {}).get("elapsed_days"))
    market_moves = diff.get("market") or {}

    buckets = {name: 0.0 for name in _BUCKETS}
    by_position: dict[int, dict[str, float]] = {}
    excluded: list[dict[str, Any]] = []
    actual = 0.0

    for position_id in (diff.get("membership") or {}).get("held", []):
        before_row = before_positions.get(position_id, {})
        after_row = after_positions.get(position_id, {})

        reason = _exclusion_reason(before_row, after_row)
        if reason is not None:
            excluded.append({"position_id": position_id, "reason": reason})
            continue

        market = market_moves.get(position_id) or {}
        d_spot = _changed(market, "spot")
        d_vol = _changed(market, "volatility")
        d_rate = _changed(market, "rate")
        d_div = _changed(market, "dividend_yield")

        # Start-of-period Greeks.
        contribution = {
            "delta": _float(before_row.get("delta")) * d_spot,
            "gamma": 0.5 * _float(before_row.get("gamma")) * d_spot * d_spot,
            "vega": _float(before_row.get("vega")) * d_vol * _VOL_POINTS_PER_UNIT,
            "theta": _float(before_row.get("theta")) * elapsed_days,
            "rho": _float(before_row.get("rho")) * d_rate * _RATE_PERCENT_PER_UNIT,
            "rho_q": _float(before_row.get("rho_q")) * d_div * _RATE_PERCENT_PER_UNIT,
        }

        position_actual = _float(after_row.get("market_value")) - _float(
            before_row.get("market_value")
        )
        position_explained = sum(contribution.values())

        for name, value in contribution.items():
            buckets[name] += value
        actual += position_actual

        by_position[position_id] = {
            **contribution,
            "explained": position_explained,
            "actual": position_actual,
            "residual": position_actual - position_explained,
        }

    explained = sum(buckets.values())
    residual = actual - explained
    residual_ratio = None if actual == 0.0 else abs(residual) / abs(actual)

    return {
        "buckets": buckets,
        "explained": explained,
        "actual": actual,
        "residual": residual,
        "residual_ratio": residual_ratio,
        "residual_warn_ratio": RESIDUAL_WARN_RATIO,
        "residual_exceeds_threshold": (
            residual_ratio is not None and residual_ratio > RESIDUAL_WARN_RATIO
        ),
        "by_position": by_position,
        "excluded": excluded,
        "convention": "start_of_period_greeks",
    }


__all__ = [
    "RESIDUAL_WARN_RATIO",
    "UnsupportedMetricContract",
    "explain_diff",
]
